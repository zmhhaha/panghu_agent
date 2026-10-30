"""Offline wire checks for admission recovery and untrusted video candidates."""
import copy
import hashlib
import json
from uuid import UUID

import httpx
import pytest

from agent_service.artifacts import frozen_manifest
from agent_service.client import AgentClient
from agent_service.contracts import ServiceError, canonical_bytes
from video_workbench.agent import VideoAgentGateway
from video_workbench.service import Workflow
from video_workbench.store import Store


JOB, ATTEMPT, INPUT, ARTIFACT = (str(UUID(int=index)) for index in (10, 11, 12, 13))
INPUT_DOCUMENT = {'schema_version': '1', 'text': '用图形讲清楚流程。'}
INPUT_SHA256 = hashlib.sha256(canonical_bytes(INPUT_DOCUMENT)).hexdigest()
TOKEN = 'offline-video-service-token-0000000000000000'


def result_document(kind='storyboard'):
    if kind == 'storyboard':
        output = dict(schema_version='1', scenes=[dict(title='开场', narration='用图形讲清楚流程。',
            visual_description='三个圆角矩形和连接箭头', duration_seconds=10, format='svg')])
    else:
        output = dict(schema_version='1', format='svg', summary='深色背景与标题',
            source='<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720">'
                   '<rect width="1280" height="720" fill="#101827"/>'
                   '<text x="100" y="100" fill="white">示例</text></svg>')
    return dict(schema_version='1', kind=kind, input_sha256=INPUT_SHA256, output=output)


class Peer:
    """A local HTTP peer: no engine, model, subprocess, or external requests."""

    def __init__(self):
        self.calls = []
        self.request = None
        self.job = None
        self.upload_mutation = None
        self.lose_submit_response = False
        self.submit_failure = None
        self.http_errors = {}
        self.preflight_failure = None
        self.profiles = [dict(profile='video.' + kind + '.v1', profile_revision='1',
            configured=True, ready=True, supports_inputs=True, max_timeout_seconds=600)
            for kind in ('storyboard', 'scene')]

    def __call__(self, request):
        self.calls.append(request)
        path, method = request.url.path, request.method
        if (method, path) in self.http_errors:
            status, code = self.http_errors[method, path]
            return httpx.Response(status, json={'detail': {'code': code}})
        if path == '/v1/capabilities':
            if self.preflight_failure == 'timeout':
                raise httpx.ReadTimeout('Capabilities response lost', request=request)
            if self.preflight_failure == 'http':
                return httpx.Response(503, json={'detail': {'code': 'service_unavailable'}})
            return httpx.Response(200, json={'api_version': '1', 'profiles': self.profiles})
        if path == '/v1/inputs' and method == 'POST':
            assert request.url.params['name'] == 'video-input.json'
            metadata = dict(input_id=INPUT, name='video-input.json', size=len(request.content),
                            sha256=hashlib.sha256(request.content).hexdigest(), created_at=1000)
            if self.upload_mutation is not None:
                self.upload_mutation(metadata)
            return httpx.Response(201, json=metadata)
        if path == '/v1/jobs' and method == 'POST':
            self.request = json.loads(request.content)
            self.job = dict(job_id=JOB, state='queued', business_ref=self.request['business_ref'],
                            publication_authorized=False, private_future_field={'preserved': True})
            if self.lose_submit_response:
                raise httpx.ReadTimeout('Response lost after admission', request=request)
            if self.submit_failure is not None:
                status, code = self.submit_failure
                return httpx.Response(status, json={'detail': {'code': code}})
            return httpx.Response(202, json=self.job)
        if path.startswith('/v1/jobs/by-idempotency/') or path == '/v1/jobs/' + JOB:
            if self.job is None:
                return httpx.Response(404, json={'detail': {'code': 'job_not_found'}})
            return httpx.Response(200, json=self.job)
        if path == '/v1/jobs/' + JOB + '/cancel':
            self.job['state'] = 'cancelling'
            return httpx.Response(200, json=self.job)
        if path == '/v1/jobs/' + JOB + '/artifact-manifest':
            return httpx.Response(200, json=self.manifest)
        if path == '/v1/artifacts/' + ARTIFACT + '/content':
            return httpx.Response(200, content=self.content)
        raise AssertionError((method, path))

    def complete(self, kind='storyboard', *, raw=None):
        document = result_document(kind)
        self.content = canonical_bytes(document) if raw is None else raw
        self.manifest = frozen_manifest(JOB, ATTEMPT, [dict(artifact_id=ARTIFACT,
            name='video-result.json', size=len(self.content), sha256=hashlib.sha256(self.content).hexdigest())])
        self.job = dict(job_id=JOB, state='completed', business_ref='video:operation-1',
            publication_authorized=False, artifact_manifest_sha256=self.manifest['sha256'],
            pins={'profile': 'video.' + kind + '.v1', 'profile_revision': '1'},
            attempt={'attempt_id': ATTEMPT, 'fence': 1}, stop_proven=True,
            stop_proof=dict(stop_proven=True, attempt_id=ATTEMPT, fence=1,
                            request_sha256='a' * 64, result_sha256='b' * 64,
                            resource_ids=['offline-container'], observed_at=1000.0))
        return document

    def rehash_manifest(self):
        self.manifest['sha256'] = hashlib.sha256(canonical_bytes({
            key: value for key, value in self.manifest.items() if key != 'sha256'})).hexdigest()
        self.job['artifact_manifest_sha256'] = self.manifest['sha256']

    def count(self, method, path):
        return sum(request.method == method and request.url.path == path for request in self.calls)


@pytest.fixture
def wire(monkeypatch):
    monkeypatch.setattr('video_workbench.agent.time.time', lambda: 1000.0)
    peer = Peer()
    with AgentClient('http://agent.invalid', TOKEN, transport=httpx.MockTransport(peer)) as client:
        yield VideoAgentGateway(client), peer


def ref():
    return dict(input_id=INPUT, sha256=INPUT_SHA256, path='video-input.json', role='video_input')


@pytest.fixture
def wire_operation(wire, tmp_path):
    gateway, peer = wire
    store = Store(tmp_path / 'state.db')
    project = store.create_project('alice', dict(title='真实协议回归', script='输入文稿', style='蓝色信息图'))
    project = store.save_scene('alice', project['id'], result_document()['output']['scenes'][0])
    operation = store.start('alice', project['id'], 'generate',
        dict(expected_revision=project['revision']), 'wire-scene', scene_id=project['scenes'][0]['id'])
    return gateway, peer, store, Workflow(store, gateway), project, operation


def complete_operation(peer, operation):
    document = result_document('scene')
    document['input_sha256'] = operation['input_sha256']
    peer.complete('scene', raw=canonical_bytes(document))
    peer.job['business_ref'] = 'video:' + operation['id']


def test_upload_uses_exact_canonical_bytes_and_returns_persistable_ref(wire):
    gateway, peer = wire
    uploaded = gateway.upload(INPUT_DOCUMENT)
    assert uploaded == ref()
    assert peer.calls[0].content == canonical_bytes(INPUT_DOCUMENT)
    assert peer.count('POST', '/v1/jobs') == 0


@pytest.mark.parametrize('field,value', [
    ('sha256', '0' * 64), ('size', 1), ('size', True),
    ('name', '../video-input.json'), ('input_id', 'untrusted-input-id'),
])
def test_upload_rejects_wrong_server_receipt(wire, field, value):
    gateway, peer = wire
    peer.upload_mutation = lambda item: item.update({field: value})
    with pytest.raises(ServiceError) as failure:
        gateway.upload(INPUT_DOCUMENT)
    assert failure.value.code == 'video_agent_protocol_invalid'


def test_submit_requires_preuploaded_ref_and_sends_only_video_contract(wire):
    gateway, peer = wire
    remote = gateway.submit('operation-1', 'scene', INPUT_DOCUMENT, 1200.2, ref())
    assert remote == peer.job
    assert remote['private_future_field'] == {'preserved': True}
    assert peer.count('POST', '/v1/inputs') == 0
    assert peer.request == dict(api_version='1', profile='video.scene.v1', profile_revision='1',
        business_ref='video:operation-1', task={'input_sha256': INPUT_SHA256},
        inputs=[ref()], timeout_seconds=201, execution_deadline_at=1200.2, checkpoint_ref=None)
    assert peer.calls[-1].headers['idempotency-key'] == 'video-operation-1'


def test_submit_caps_timeout_without_moving_absolute_deadline(wire):
    gateway, peer = wire
    peer.profiles[0]['max_timeout_seconds'] = 30
    gateway.submit('operation-1', 'storyboard', INPUT_DOCUMENT, 5000, ref())
    assert peer.request['timeout_seconds'] == 30
    assert peer.request['execution_deadline_at'] == 5000


@pytest.mark.parametrize('change', [
    {'configured': False}, {'ready': False}, {'supports_inputs': False},
    {'max_timeout_seconds': True}, {'profile_revision': 'unaccepted'},
    {'profile': 'research.generate.v1'},
])
def test_unavailable_route_never_falls_back_or_submits(wire, change):
    gateway, peer = wire
    peer.profiles[0].update(change)
    with pytest.raises(ServiceError) as failure:
        gateway.submit('operation-1', 'storyboard', INPUT_DOCUMENT, 1200, ref())
    assert (failure.value.code, failure.value.status) == ('video_profile_unavailable', 503)
    assert peer.count('POST', '/v1/jobs') == 0


def test_duplicate_routes_are_not_accepted_as_an_explicit_binding(wire):
    gateway, peer = wire
    peer.profiles.append(copy.deepcopy(peer.profiles[0]))
    with pytest.raises(ServiceError, match='video_profile_unavailable'):
        gateway.submit('operation-1', 'storyboard', INPUT_DOCUMENT, 1200, ref())
    assert peer.count('POST', '/v1/jobs') == 0


@pytest.mark.parametrize('failure', ['timeout', 'http'])
def test_capabilities_failure_is_definite_preflight_failure_without_post(wire, failure):
    gateway, peer = wire
    peer.preflight_failure = failure
    with pytest.raises(ServiceError) as error:
        gateway.submit('operation-1', 'storyboard', INPUT_DOCUMENT, 1200, ref())
    assert (error.value.code, error.value.status) == ('video_preflight_unavailable', 503)
    assert len(peer.calls) == 1
    assert peer.count('GET', '/v1/capabilities') == 1
    assert peer.count('POST', '/v1/jobs') == 0


@pytest.mark.parametrize('input_ref', [None, {}, {**ref(), 'sha256': '0' * 64},
    {**ref(), 'path': 'other.json'}, {**ref(), 'role': 'research'},
    {**ref(), 'input_id': 'untrusted-input-id'}, {**ref(), 'engine': 'pi'}])
def test_missing_or_mismatched_persisted_input_rejected_before_network(wire, input_ref):
    gateway, peer = wire
    with pytest.raises(ServiceError, match='invalid_video_input_ref'):
        gateway.submit('operation-1', 'storyboard', INPUT_DOCUMENT, 1200, input_ref)
    assert not peer.calls


@pytest.mark.parametrize('deadline,code', [(1000, 'video_deadline_expired'),
    (999, 'video_deadline_expired'), (float('inf'), 'invalid_video_deadline'),
    (float('nan'), 'invalid_video_deadline'), (True, 'invalid_video_deadline'),
    (10 ** 1000, 'invalid_video_deadline')])
def test_invalid_or_expired_deadline_rejected_before_network(wire, deadline, code):
    gateway, peer = wire
    with pytest.raises(ServiceError, match=code):
        gateway.submit('operation-1', 'storyboard', INPUT_DOCUMENT, deadline, ref())
    assert not peer.calls


def test_lost_submit_response_recovers_with_get_and_never_reposts(wire):
    gateway, peer = wire
    peer.lose_submit_response = True
    with pytest.raises(ServiceError, match='service_unavailable'):
        gateway.submit('operation-1', 'storyboard', INPUT_DOCUMENT, 1200, ref())
    recovered = gateway.lookup('operation-1')
    assert recovered == peer.job
    assert peer.count('POST', '/v1/jobs') == 1
    assert peer.count('GET', '/v1/jobs/by-idempotency/video-operation-1') == 1


@pytest.mark.parametrize('stage,method,path', [
    ('capabilities', 'GET', '/v1/capabilities'),
    ('upload', 'POST', '/v1/inputs'),
    ('submit', 'POST', '/v1/jobs'),
    ('lookup', 'GET', '/v1/jobs/by-idempotency/video-operation-1'),
    ('poll', 'GET', '/v1/jobs/' + JOB),
    ('cancel', 'POST', '/v1/jobs/' + JOB + '/cancel'),
    ('manifest', 'GET', '/v1/jobs/' + JOB + '/artifact-manifest'),
    ('download', 'GET', '/v1/artifacts/' + ARTIFACT + '/content'),
])
@pytest.mark.parametrize('status,code', [
    (500, 'upstream_internal_failure'), (503, 'relay_restart_gate_closed'),
])
def test_custom_upstream_5xx_is_recoverable_without_implicit_retry(wire, stage, method, path, status, code):
    gateway, peer = wire
    peer.complete('scene')
    peer.http_errors[method, path] = status, code
    calls = dict(capabilities=gateway.capabilities, upload=lambda: gateway.upload(INPUT_DOCUMENT),
        submit=lambda: gateway.submit('operation-1', 'scene', INPUT_DOCUMENT, 1200, ref()),
        lookup=lambda: gateway.lookup('operation-1'), poll=lambda: gateway.poll(JOB),
        cancel=lambda: gateway.cancel(JOB), manifest=lambda: gateway.collect(JOB, 'scene', INPUT_SHA256),
        download=lambda: gateway.collect(JOB, 'scene', INPUT_SHA256))
    with pytest.raises(ServiceError) as failure:
        calls[stage]()
    assert (failure.value.code, failure.value.status) == ('service_unavailable', status)
    assert peer.count(method, path) == 1


def test_custom_capability_outage_is_definite_preflight_failure(wire):
    gateway, peer = wire
    peer.http_errors['GET', '/v1/capabilities'] = 503, 'relay_restart_gate_closed'
    with pytest.raises(ServiceError) as failure:
        gateway.submit('operation-1', 'scene', INPUT_DOCUMENT, 1200, ref())
    assert (failure.value.code, failure.value.status) == ('video_preflight_unavailable', 503)
    assert peer.count('POST', '/v1/jobs') == 0


@pytest.mark.parametrize('lost_response', ['timeout', 'custom_503'])
def test_custom_lookup_outage_survives_restart_with_one_admission_and_candidate(wire_operation, lost_response):
    gateway, peer, store, workflow, project, operation = wire_operation
    if lost_response == 'timeout':
        peer.lose_submit_response = True
    else:
        # The backend accepted the paid Job before returning its own 503 code.
        peer.submit_failure = 503, 'admission_receipt_unavailable'
    workflow.tick()
    pending = store.operation(operation['id'])
    assert pending['status'] == 'submitting' and pending['error'] == 'submission_uncertain'
    assert pending['submit_attempted'] is True and pending['job_id'] is None
    assert peer.job['job_id'] == JOB
    path = '/v1/jobs/by-idempotency/video-' + operation['id']
    peer.http_errors['GET', path] = 503, 'relay_restart_gate_closed'
    workflow.tick()
    workflow.tick()
    pending = Store(store.path).operation(operation['id'])
    assert pending['status'] == 'submitting' and pending['error'] == 'service_unavailable'
    assert pending['job_id'] is None
    assert store.project('alice', project['id'])['scenes'][0]['versions'] == []

    # Recreate the HTTP client and durable reconciler, just as on service restart.
    with AgentClient('http://agent.invalid', TOKEN, transport=httpx.MockTransport(peer)) as client:
        recovered = Workflow(Store(store.path), VideoAgentGateway(client))
        del peer.http_errors['GET', path]
        complete_operation(peer, pending)
        recovered.tick()
        recovered.tick()
    finished = Store(store.path).operation(operation['id'])
    assert finished['status'] == 'completed' and finished['error'] is None
    assert finished['job_id'] == JOB and finished['input_ref'] == pending['input_ref']
    assert finished['deadline_at'] == pending['deadline_at']
    scene = store.project('alice', project['id'])['scenes'][0]
    assert len(scene['versions']) == 1 and scene['head_version_id'] is None
    assert peer.count('POST', '/v1/jobs') == 1
    assert peer.count('POST', '/v1/inputs') == 1
    assert peer.count('GET', path) == 3


@pytest.mark.parametrize('stage,status,code', [
    ('submit', 422, 'invalid_request'), ('lookup', 403, 'caller_forbidden'),
    ('poll', 401, 'caller_unauthorized'),
])
def test_upstream_4xx_remains_terminal_without_reposting(wire_operation, stage, status, code):
    gateway, peer, store, workflow, project, operation = wire_operation
    if stage == 'lookup':
        peer.lose_submit_response = True
        workflow.tick()
        method, path = 'GET', '/v1/jobs/by-idempotency/video-' + operation['id']
    else:
        method, path = ('POST', '/v1/jobs') if stage == 'submit' else ('GET', '/v1/jobs/' + JOB)
    peer.http_errors[method, path] = status, code
    workflow.tick()
    workflow.tick()
    failed = store.operation(operation['id'])
    assert failed['status'] == 'failed' and failed['error'] == code
    assert peer.count('POST', '/v1/jobs') == 1
    assert store.project('alice', project['id'])['scenes'][0]['versions'] == []


def test_lookup_404_remains_pending_and_never_reposts(wire_operation):
    gateway, peer, store, workflow, project, operation = wire_operation
    peer.lose_submit_response = True
    workflow.tick()
    path = '/v1/jobs/by-idempotency/video-' + operation['id']
    peer.http_errors['GET', path] = 404, 'job_not_found'
    recovered = Workflow(Store(store.path), gateway)
    recovered.tick()
    pending = store.operation(operation['id'])
    assert pending['status'] == 'submitting' and pending['job_id'] is None
    del peer.http_errors['GET', path]
    complete_operation(peer, pending)
    recovered.tick()
    recovered.tick()
    assert store.operation(operation['id'])['status'] == 'completed'
    assert peer.count('POST', '/v1/jobs') == 1
    assert len(store.project('alice', project['id'])['scenes'][0]['versions']) == 1


@pytest.mark.parametrize('failure,code', [
    ('profile', 'video_profile_unavailable'), ('artifact', 'video_artifact_digest_mismatch'),
])
def test_local_video_validation_is_terminal_and_never_publishes(wire_operation, failure, code):
    gateway, peer, store, workflow, project, operation = wire_operation
    if failure == 'profile':
        peer.profiles[1]['ready'] = False
    else:
        workflow.tick()
        complete_operation(peer, store.operation(operation['id']))
        peer.content += b' '
    workflow.tick()
    workflow.tick()
    failed = store.operation(operation['id'])
    assert failed['status'] == 'failed' and failed['error'] == code
    assert peer.count('POST', '/v1/jobs') == (0 if failure == 'profile' else 1)
    assert store.project('alice', project['id'])['scenes'][0]['versions'] == []


def test_capabilities_poll_and_cancel_preserve_service_states(wire):
    gateway, peer = wire
    assert gateway.capabilities() == {'api_version': '1', 'profiles': peer.profiles}
    gateway.submit('operation-1', 'storyboard', INPUT_DOCUMENT, 1200, ref())
    assert gateway.poll(JOB) == peer.job
    assert gateway.cancel(JOB)['state'] == 'cancelling'
    assert peer.count('POST', '/v1/jobs') == 1


@pytest.mark.parametrize('kind', ['storyboard', 'scene'])
def test_collect_validates_real_manifest_and_domain_result(wire, kind):
    gateway, peer = wire
    expected = peer.complete(kind)
    assert gateway.collect(JOB, kind, INPUT_SHA256) == expected
    assert peer.count('GET', '/v1/artifacts/' + ARTIFACT + '/content') == 1


@pytest.mark.parametrize('drift', ['not_stopped', 'wrong_attempt', 'wrong_fence', 'wrong_profile',
    'wrong_revision', 'publication_authorized', 'missing_stop_digest'])
def test_completed_label_without_consistent_service_proof_is_rejected(wire, drift):
    gateway, peer = wire
    peer.complete()
    if drift == 'not_stopped':
        peer.job['stop_proven'] = False
    elif drift == 'wrong_attempt':
        peer.job['stop_proof']['attempt_id'] = INPUT
    elif drift == 'wrong_fence':
        peer.job['stop_proof']['fence'] = 2
    elif drift == 'wrong_profile':
        peer.job['pins']['profile'] = 'workspace-task.v1'
    elif drift == 'wrong_revision':
        peer.job['pins']['profile_revision'] = 'v1'
    elif drift == 'publication_authorized':
        peer.job['publication_authorized'] = True
    else:
        peer.job['stop_proof'].pop('result_sha256')
    with pytest.raises(ServiceError, match='video_agent_protocol_invalid'):
        gateway.collect(JOB, 'storyboard', INPUT_SHA256)
    assert peer.count('GET', '/v1/jobs/' + JOB + '/artifact-manifest') == 0


@pytest.mark.parametrize('drift', ['manifest_digest', 'job_digest', 'attempt', 'job', 'extra_file',
    'wrong_name', 'path_alias', 'role_alias', 'bool_size', 'oversize', 'total_size'])
def test_manifest_tampering_rejected_before_artifact_download(wire, drift):
    gateway, peer = wire
    peer.complete()
    item = peer.manifest['files'][0]
    if drift == 'manifest_digest':
        peer.manifest['sha256'] = '0' * 64
    elif drift == 'job_digest':
        peer.job['artifact_manifest_sha256'] = '0' * 64
    elif drift == 'attempt':
        peer.manifest['attempt_id'] = INPUT
    elif drift == 'job':
        peer.manifest['job_id'] = INPUT
    elif drift == 'extra_file':
        peer.manifest['files'].append(copy.deepcopy(item))
    elif drift == 'wrong_name':
        item['name'] = '../video-result.json'
    elif drift == 'path_alias':
        item['path'] = 'video-result.json'
    elif drift == 'role_alias':
        item['role'] = 'video_result'
    elif drift == 'bool_size':
        item['size'] = True
    elif drift == 'oversize':
        item['size'] = 2 * 1024 * 1024 + 1
    else:
        peer.manifest['total_bytes'] += 1
    if drift not in {'manifest_digest', 'job_digest'}:
        peer.rehash_manifest()
    with pytest.raises(ServiceError, match='video_agent_protocol_invalid'):
        gateway.collect(JOB, 'storyboard', INPUT_SHA256)
    assert peer.count('GET', '/v1/artifacts/' + ARTIFACT + '/content') == 0


def test_modified_download_rejected_even_when_manifest_itself_is_valid(wire):
    gateway, peer = wire
    peer.complete()
    peer.content = peer.content.replace(b'storyboard', b'STORYBOARD')
    with pytest.raises(ServiceError, match='video_artifact_digest_mismatch'):
        gateway.collect(JOB, 'storyboard', INPUT_SHA256)


@pytest.mark.parametrize('raw', [b'{"kind":"storyboard","kind":"scene"}',
    b'{"value":NaN}', b'{"value":Infinity}', b'[]', b'\xff'])
def test_invalid_json_rejected_even_when_bytes_have_valid_service_hash(wire, raw):
    gateway, peer = wire
    peer.complete(raw=raw)
    with pytest.raises(ServiceError, match='video_agent_output_invalid'):
        gateway.collect(JOB, 'storyboard', INPUT_SHA256)


def test_old_input_result_and_unsafe_source_are_independently_rejected(wire):
    gateway, peer = wire
    peer.complete('scene')
    with pytest.raises(ServiceError):
        gateway.collect(JOB, 'scene', '0' * 64)
    document = result_document('scene')
    document['output']['source'] = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    peer.complete('scene', raw=canonical_bytes(document))
    with pytest.raises(ServiceError):
        gateway.collect(JOB, 'scene', INPUT_SHA256)


def test_collect_does_not_treat_needs_input_as_a_completed_candidate(wire):
    gateway, peer = wire
    peer.complete()
    peer.job['state'] = 'needs_input'
    with pytest.raises(ServiceError, match='video_result_not_ready'):
        gateway.collect(JOB, 'storyboard', INPUT_SHA256)
    assert peer.count('GET', '/v1/jobs/' + JOB + '/artifact-manifest') == 0
