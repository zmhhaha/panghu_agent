"""Offline motion wire admission, manifests and stop proof; no Agent calls."""
from copy import deepcopy
import hashlib
import json
from uuid import UUID

import httpx
import pytest

from agent_service.artifacts import frozen_manifest
from agent_service.client import AgentClient
from agent_service.contracts import ServiceError, canonical_bytes
from video_workbench.motion import make_beats, example_plan
from video_workbench.motion_gateway import MotionAgentGateway, PROFILE
from video_workbench.motion_store import MotionStore
from video_workbench.motion_workflow import MotionWorkflow
from video_workbench.store import Store


JOB, ATTEMPT, INPUT, ARTIFACT = [str(UUID(int=i)) for i in (40, 41, 42, 43)]
TOKEN = 'offline-motion-service-token-' + 'a' * 32


def document():
    return dict(schema='video.motion-input.v1', title='请求如何完成', style='清晰图解',
        visual_description='请求进入队列，执行节点处理，返回结果。', instruction='', base_plan=None,
        beats=make_beats('先把请求交给队列。执行节点取出任务。最后把结果返回。'))


class Peer:
    def __init__(self):
        self.calls, self.job, self.request = [], None, None
        self.profiles = [dict(profile=PROFILE, profile_revision='1', configured=True,
            ready=True, supports_inputs=True, max_timeout_seconds=600)]
        self.lose_response = False
        self.upload_change = None
        self.http_errors = {}
        self.malformed_submission = False

    def __call__(self, request):
        self.calls.append(request)
        method, path = request.method, request.url.path
        if (method, path) in self.http_errors:
            return httpx.Response(self.http_errors[method, path], json={'detail': {'code': 'temporary_outage'}})
        if path == '/v1/capabilities':
            return httpx.Response(200, json={'profiles': self.profiles})
        if path == '/v1/inputs' and method == 'POST':
            assert request.url.params['name'] == 'motion-input.json'
            receipt = dict(input_id=INPUT, name='motion-input.json', size=len(request.content),
                           sha256=hashlib.sha256(request.content).hexdigest())
            if self.upload_change:
                receipt.update(self.upload_change)
            return httpx.Response(201, json=receipt)
        if path == '/v1/jobs' and method == 'POST':
            self.request = json.loads(request.content)
            self.job = dict(job_id=JOB, state='queued', business_ref=self.request['business_ref'],
                            publication_authorized=False)
            if self.lose_response:
                raise httpx.ReadTimeout('Offline lost response', request=request)
            return httpx.Response(202, json={} if self.malformed_submission else self.job)
        if path.startswith('/v1/jobs/by-idempotency/') or path == '/v1/jobs/' + JOB:
            return httpx.Response(200, json=self.job) if self.job else httpx.Response(
                404, json={'detail': {'code': 'job_not_found'}})
        if path == '/v1/jobs/' + JOB + '/cancel':
            self.job['state'] = 'cancelling'
            return httpx.Response(200, json=self.job)
        if path == '/v1/jobs/' + JOB + '/artifact-manifest':
            return httpx.Response(200, json=self.manifest)
        if path == '/v1/artifacts/' + ARTIFACT + '/content':
            return httpx.Response(200, content=self.content)
        raise AssertionError((method, path))

    def complete(self, operation_id, input_document=None):
        doc = document() if input_document is None else input_document
        result = dict(schema='video.motion-result.v1', input_sha256=hashlib.sha256(canonical_bytes(doc)).hexdigest(),
                      plan=example_plan(doc['beats']))
        self.content = canonical_bytes(result)
        self.manifest = frozen_manifest(JOB, ATTEMPT, [dict(artifact_id=ARTIFACT,
            name='motion-result.json', size=len(self.content), sha256=hashlib.sha256(self.content).hexdigest())])
        self.job = dict(job_id=JOB, state='completed', business_ref='video-motion:' + operation_id,
            publication_authorized=False, artifact_manifest_sha256=self.manifest['sha256'],
            pins=dict(profile=PROFILE, profile_revision='1'), attempt=dict(attempt_id=ATTEMPT, fence=1),
            stop_proven=True, stop_proof=dict(stop_proven=True, attempt_id=ATTEMPT, fence=1,
                request_sha256='a' * 64, result_sha256='b' * 64, resource_ids=['offline-writer'], observed_at=1000.0))
        return result

    def rehash_manifest(self):
        self.manifest['sha256'] = hashlib.sha256(canonical_bytes({k: v for k, v in self.manifest.items()
                                                               if k != 'sha256'})).hexdigest()
        self.job['artifact_manifest_sha256'] = self.manifest['sha256']

    def count(self, method, path):
        return sum(r.method == method and r.url.path == path for r in self.calls)


@pytest.fixture
def wire(monkeypatch):
    monkeypatch.setattr('video_workbench.motion_gateway.time.time', lambda: 1000.0)
    peer = Peer()
    with AgentClient('http://agent.invalid', TOKEN, transport=httpx.MockTransport(peer)) as client:
        yield MotionAgentGateway(client), peer


def test_motion_upload_and_admission_are_distinct_from_static_contract(wire):
    gateway, peer = wire
    doc = document()
    ref = gateway.upload(doc)
    assert ref['path'] == 'motion-input.json' and ref['role'] == 'motion_input'
    assert peer.calls[0].content == canonical_bytes(doc)
    gateway.submit('operation-1', doc, 1250, ref)
    assert peer.request['profile'] == PROFILE and peer.request['profile_revision'] == '1'
    assert peer.request['business_ref'] == 'video-motion:operation-1'
    assert peer.request['inputs'] == [ref]
    assert peer.request['task'] == {'input_sha256': ref['sha256']}
    assert next(r for r in peer.calls if r.url.path == '/v1/jobs').headers['idempotency-key'] == 'motion-operation-1'
    gateway.lookup('operation-1')
    assert peer.count('POST', '/v1/jobs') == 1


@pytest.mark.parametrize('change', [{'input_id': 'untrusted'}, {'name': 'video-input.json'},
    {'size': True}, {'sha256': '0' * 64}])
def test_upload_refuses_wrong_server_receipt(wire, change):
    gateway, peer = wire
    peer.upload_change = change
    with pytest.raises(ServiceError, match='motion_agent_protocol_invalid'):
        gateway.upload(document())
    assert peer.count('POST', '/v1/jobs') == 0


@pytest.mark.parametrize('change', ['disabled', 'not_ready', 'wrong_profile', 'duplicate', 'preflight_outage'])
def test_preflight_failure_never_posts_a_paid_job(wire, change):
    gateway, peer = wire
    if change == 'disabled':
        peer.profiles[0]['configured'] = False
    elif change == 'not_ready':
        peer.profiles[0]['ready'] = False
    elif change == 'wrong_profile':
        peer.profiles[0]['profile'] = 'video.scene.v1'
    elif change == 'duplicate':
        peer.profiles *= 2
    else:
        peer.http_errors['GET', '/v1/capabilities'] = 503
    with pytest.raises(ServiceError):
        gateway.submit('operation-1', document(), 1200, gateway.upload(document()))
    assert peer.count('POST', '/v1/jobs') == 0


@pytest.mark.parametrize('change', ['timeout', 'malformed'])
def test_ambiguous_submission_is_queryable_without_second_post(wire, change):
    gateway, peer = wire
    peer.lose_response = change == 'timeout'
    peer.malformed_submission = change == 'malformed'
    with pytest.raises(ServiceError) as failure:
        gateway.submit('operation-1', document(), 1200, gateway.upload(document()))
    assert failure.value.code.startswith('service_')
    assert gateway.lookup('operation-1')['job_id'] == JOB
    assert peer.count('POST', '/v1/jobs') == 1


def test_collect_independently_returns_only_hash_bound_plan(wire):
    gateway, peer = wire
    result = peer.complete('operation-1')
    assert gateway.collect(JOB, result['input_sha256'], document()['beats']) == result


def test_real_gateway_and_durable_workflow_recover_a_lost_http_response(wire, tmp_path):
    gateway, peer = wire
    base = Store(tmp_path / 'motion-wire.db')
    project = base.create_project('alice', dict(title='端到端协议', script='总稿', style='清晰图解'))
    project = base.save_scene('alice', project['id'], dict(title='请求如何完成',
        narration=document()['beats']['source_text'], visual_description=document()['visual_description'],
        duration_seconds=15, format='svg'))
    motion = MotionStore(base)
    op = motion.start('alice', project['scenes'][0]['id'], dict(expected_revision=project['revision']), 'wire-1')
    peer.lose_response = True
    MotionWorkflow(motion, gateway, enabled=True).tick()
    assert motion.operation(op['id'])['error'] == 'submission_uncertain'
    restored = MotionStore(Store(base.path))
    workflow = MotionWorkflow(restored, gateway, enabled=True)
    workflow.tick()
    assert restored.operation(op['id'])['job_id'] == JOB
    peer.complete(op['id'], restored.operation(op['id'])['document'])
    workflow.tick()
    scene = restored.project('alice', project['id'])['scenes'][0]
    assert len(scene['motion']['versions']) == 1 and scene['motion']['head_version_id'] is None
    assert peer.count('POST', '/v1/jobs') == peer.count('POST', '/v1/inputs') == 1
    assert restored.operation(op['id'])['status'] == 'completed'


@pytest.mark.parametrize('change', ['stop_false', 'wrong_attempt', 'wrong_fence', 'empty_writers',
    'duplicate_writers', 'nan_time', 'wrong_profile', 'wrong_revision', 'publish_true',
    'manifest_hash', 'wrong_name', 'extra_file', 'wrong_size', 'wrong_bytes', 'wrong_input',
    'duplicate_json', 'script_field', 'dangling_beat'])
def test_untrusted_proof_manifest_or_plan_cannot_publish_a_candidate(wire, change):
    gateway, peer = wire
    result = peer.complete('operation-1')
    proof = peer.job['stop_proof']
    if change == 'stop_false':
        peer.job['stop_proven'] = False
    elif change == 'wrong_attempt':
        proof['attempt_id'] = INPUT
    elif change == 'wrong_fence':
        proof['fence'] += 1
    elif change == 'empty_writers':
        proof['resource_ids'] = []
    elif change == 'duplicate_writers':
        proof['resource_ids'] *= 2
    elif change == 'nan_time':
        # Nonfinite values cannot pass the real JSON client; null represents
        # the same invalid public field without serializing NaN over HTTP.
        proof['observed_at'] = None
    elif change == 'wrong_profile':
        peer.job['pins']['profile'] = 'video.scene.v1'
    elif change == 'wrong_revision':
        peer.job['pins']['profile_revision'] = 'v1'
    elif change == 'publish_true':
        peer.job['publication_authorized'] = True
    elif change == 'manifest_hash':
        peer.job['artifact_manifest_sha256'] = '0' * 64
    elif change == 'wrong_name':
        peer.manifest['files'][0]['name'] = 'video-result.json'
        peer.rehash_manifest()
    elif change == 'extra_file':
        peer.manifest['files'].append(deepcopy(peer.manifest['files'][0]))
        peer.rehash_manifest()
    elif change == 'wrong_size':
        peer.manifest['files'][0]['size'] += 1
        peer.rehash_manifest()
    elif change == 'wrong_bytes':
        peer.content += b' '
    else:
        if change == 'wrong_input':
            result['input_sha256'] = '0' * 64
        elif change == 'script_field':
            result['plan']['javascript'] = 'alert(1)'
        elif change == 'dangling_beat':
            result['plan']['actions'][0]['beat_id'] = 'beat_0000000000000000'
        raw = canonical_bytes(result)
        if change == 'duplicate_json':
            raw = b'{"schema":"bad",' + raw[1:]
        peer.content = raw
        peer.manifest['files'][0].update(size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        peer.manifest['total_bytes'] = len(raw)
        peer.rehash_manifest()
    with pytest.raises(ServiceError):
        gateway.collect(JOB, result['input_sha256'] if change != 'wrong_input' else 'a' * 64, document()['beats'])
