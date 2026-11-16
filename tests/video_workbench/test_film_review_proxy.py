"""Real review-v4 gateway over offline HTTP, durable facade, no paid model."""
import base64
from contextlib import contextmanager
from copy import deepcopy
from http.client import HTTPConnection
import json
import threading

import httpx
import pytest

from agent_service.client import AgentClient
from agent_service.contracts import ServiceError, canonical_bytes
from video_workbench.animation_creation_http import CreationHTTPServer
from video_workbench.animation_review_gateway import AnimationInputReviewGateway
from video_workbench.film_review_proxy import FilmReviewProxy, MAX_BODY, validate_request
from test_animation_review_gateway import Peer, PNG, JOB, visual_document


TOKEN = 'film-review-private-test-' + 'x' * 40
OWNER = 'casdoor:formal-film-owner'


def request_document():
    document = visual_document()
    files = {AnimationInputReviewGateway.input_name: canonical_bytes(document),
        **{row['file_name']: PNG for row in document['observation']['frames']}}
    return dict(schema='video.film-review-request.v1', operation_id='formal-review-1',
        project_id='project-1', source=dict(run_id='film-run-1', version_id='render-1', sha256='a' * 64),
        source_sha256='b' * 64, deadline_at=1200, document=document,
        files={key: base64.b64encode(raw).decode('ascii') for key, raw in files.items()})


def binding(request):
    return {key: deepcopy(request[key]) for key in ('project_id', 'source', 'source_sha256')}


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setattr('video_workbench.animation_review_gateway.time.time', lambda: 1000.0)
    peer = Peer(AnimationInputReviewGateway)
    with AgentClient('http://agent.invalid', 'offline-review-agent-token-' + 'z' * 40,
            transport=httpx.MockTransport(peer)) as client:
        gateway = AnimationInputReviewGateway(client)
        service = FilmReviewProxy(tmp_path, gateway, clock=lambda: 1000.0)
        yield service, gateway, peer, request_document()


def complete(service, peer, request):
    operation = request['operation_id']
    service.start(OWNER, request)
    service.advance(OWNER, operation)
    remote = peer.request['business_ref'][len(AnimationInputReviewGateway.business_prefix):]
    expected = peer.complete(remote, request['document'])
    service.advance(OWNER, operation)
    return expected


def test_start_is_durable_unpaid_and_exact_request_is_idempotent(work):
    service, _, peer, request = work
    first = service.start(OWNER, request)
    assert first['job_id'] == request['operation_id'] and first['state'] == 'queued'
    assert service.start(OWNER, request) == first
    assert peer.calls == [] and service.pending() == [dict(owner=OWNER, id=request['operation_id'])]
    changed = deepcopy(request)
    changed['deadline_at'] += 1
    with pytest.raises(ServiceError, match='film_review_operation_conflict'):
        service.start(OWNER, changed)
    assert peer.calls == []


def test_submission_uses_the_existing_v4_gateway_and_owns_remote_identity(work):
    service, gateway, peer, request = work
    service.start(OWNER, request)
    service.advance(OWNER, request['operation_id'])
    assert service.gateway is gateway
    assert peer.request['profile'] == 'video.animation-review.v4'
    assert peer.request['execution_deadline_at'] == request['deadline_at']
    assert peer.request['business_ref'].startswith('animation-review-input:film-review-')
    assert len(peer.uploads) == 3 and peer.count('POST', '/v1/jobs') == 1
    job = service.lookup(OWNER, request['operation_id'], binding(request))
    assert job['job_id'] == request['operation_id'] and job['state'] == 'queued'
    assert 'inputs' not in job and 'pins' not in job


def test_same_operation_text_under_different_owners_cannot_collide_in_agent_namespace(work):
    service, _, peer, request = work
    service.start(OWNER, request)
    service.advance(OWNER, request['operation_id'])
    first = peer.request['business_ref']
    peer.uploads.clear()  # The offline peer allocates test input IDs by count.
    service.start('casdoor:other-owner', request)
    service.advance('casdoor:other-owner', request['operation_id'])
    assert peer.request['business_ref'] != first and peer.count('POST', '/v1/jobs') == 2


def test_remote_cancel_only_receives_the_job_owned_by_the_bound_operation(work, monkeypatch):
    service, gateway, peer, request = work
    service.start(OWNER, request)
    service.advance(OWNER, request['operation_id'])
    cancelled = []
    monkeypatch.setattr(gateway.client, 'cancel', lambda job: cancelled.append(job))
    service.cancel(OWNER, request['operation_id'], binding(request))
    assert cancelled == [JOB] and peer.count('POST', '/v1/jobs') == 1
    with pytest.raises(ServiceError, match='film_review_not_found'):
        service.cancel(OWNER, JOB, binding(request))
    assert cancelled == [JOB]


def test_proxy_rejects_a_different_reviewer_profile_before_storage(tmp_path):
    class Unadmitted:
        profile = 'video.animation-review.v3'
    with pytest.raises(ServiceError, match='film_review_v4_required'):
        FilmReviewProxy(tmp_path / 'not-created', Unadmitted())
    assert not (tmp_path / 'not-created').exists()


def test_uncertain_paid_admission_recovers_same_identity_after_restart_without_new_post(work):
    service, gateway, peer, request = work
    peer.lose_response = True
    service.start(OWNER, request)
    service.advance(OWNER, request['operation_id'])
    assert service.get(OWNER, request['operation_id'], binding(request))['state'] == 'starting'
    reopened = FilmReviewProxy(service.root, gateway, clock=lambda: 1000.0)
    assert reopened.start(OWNER, request)['state'] == 'starting'
    reopened.advance(OWNER, request['operation_id'])
    assert reopened.get(OWNER, request['operation_id'], binding(request))['state'] == 'queued'
    assert peer.count('POST', '/v1/jobs') == 1
    assert len(peer.uploads) == 3


def test_two_executor_instances_share_one_paid_admission_claim(tmp_path):
    """Concurrency fixture exercises the ledger fence, not model quality."""
    class ConcurrentGateway:
        profile = 'video.animation-review.v4'

        def __init__(self):
            self.barrier, self.guard, self.posts = threading.Barrier(2), threading.Lock(), 0

        def preflight(self, _):
            return {}

        def upload(self, *_):
            return []

        def prepare_submission(self, *_):
            self.barrier.wait(timeout=5)
            def admit():
                with self.guard:
                    self.posts += 1
                return dict(job_id=JOB, state='queued', stop_proven=False)
            return admit

    gateway = ConcurrentGateway()
    first = FilmReviewProxy(tmp_path, gateway, clock=lambda: 1000.0)
    second = FilmReviewProxy(tmp_path, gateway, clock=lambda: 1000.0)
    request = request_document()
    first.start(OWNER, request)
    threads = [threading.Thread(target=service.advance, args=(OWNER, request['operation_id']))
        for service in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
        assert not thread.is_alive()
    assert gateway.posts == 1
    assert first.get(OWNER, request['operation_id'], binding(request))['state'] == 'queued'


def test_unknown_remote_not_found_is_never_permission_to_repeat_paid_post(work):
    service, gateway, peer, request = work
    peer.lose_response = True
    service.start(OWNER, request)
    service.advance(OWNER, request['operation_id'])
    peer.job = None
    reopened = FilmReviewProxy(service.root, gateway, clock=lambda: 1400.0)
    for _ in range(3):
        assert reopened.lookup(OWNER, request['operation_id'], binding(request))['state'] == 'starting'
    assert peer.count('POST', '/v1/jobs') == 1


@pytest.mark.parametrize('change', ['owner', 'project', 'media', 'run', 'source'])
def test_all_status_result_and_cancel_reads_require_exact_owner_and_source_binding(work, change):
    service, _, peer, request = work
    service.start(OWNER, request)
    owner, expected = OWNER, binding(request)
    if change == 'owner':
        owner = 'casdoor:other-owner'
    elif change == 'project':
        expected['project_id'] = 'other-project'
    elif change == 'media':
        expected['source']['sha256'] = 'c' * 64
    elif change == 'run':
        expected['source']['run_id'] = 'other-run'
    else:
        expected['source_sha256'] = 'c' * 64
    for method in (service.lookup, service.result, service.cancel):
        with pytest.raises(ServiceError, match='film_review_not_found'):
            method(owner, request['operation_id'], expected)
    assert peer.calls == []


def test_completed_review_returns_validated_result_and_true_control_plane_frame_receipt(work):
    service, _, peer, request = work
    expected = complete(service, peer, request)
    result = service.result(OWNER, request['operation_id'], binding(request))
    assert result['result'] == expected
    assert result['model_frame_receipt']['job_id'] == JOB
    assert result['result']['coverage'] == 'sampled_frames'
    assert result['result']['evidence_scope'] == 'visual_only'
    count = len(peer.calls)
    assert service.result(OWNER, request['operation_id'], binding(request)) == result
    assert len(peer.calls) == count and peer.count('POST', '/v1/jobs') == 1


def test_model_claimed_frame_list_cannot_replace_real_frame_receipt(work):
    service, _, peer, request = work
    complete(service, peer, request)
    peer.receipt_change['images'] = []
    with pytest.raises(ServiceError, match='animation_model_frame_evidence_incomplete'):
        service.result(OWNER, request['operation_id'], binding(request))


@pytest.mark.parametrize('change', ['frame', 'document', 'extra_file', 'bad_image', 'media', 'source'])
def test_frozen_document_and_each_original_frame_are_validated_before_any_network(work, change):
    service, _, peer, request = work
    if change == 'frame':
        request['files']['frame-1.png'] = base64.b64encode(PNG + b'changed').decode()
    elif change == 'document':
        request['files'][AnimationInputReviewGateway.input_name] = base64.b64encode(b'{}').decode()
    elif change == 'extra_file':
        request['files']['other.txt'] = 'YQ=='
    elif change == 'bad_image':
        request['files']['frame-1.png'] = '!!!'
    elif change == 'media':
        request['source']['sha256'] = 'c' * 64
    else:
        request['source_sha256'] = 'c' * 64
    with pytest.raises(ServiceError):
        service.start(OWNER, request)
    assert service.pending() == [] and peer.calls == []


def test_deadline_is_bounded_and_pre_admission_cancel_is_proven_without_paid_post(work):
    service, _, peer, request = work
    too_late = deepcopy(request)
    too_late['deadline_at'] = 1601
    with pytest.raises(ServiceError, match='film_review_deadline_invalid'):
        service.start(OWNER, too_late)
    service.start(OWNER, request)
    result = service.cancel(OWNER, request['operation_id'], binding(request))
    assert result['state'] == 'cancelled' and result['stop_proven'] is True
    assert peer.calls == [] and service.pending() == []


@contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def http(server, method, path, body=None, *, owner=OWNER, token=TOKEN, headers=None):
    supplied = {'Authorization': 'Bearer ' + token, 'X-Animation-Creation-Owner': owner,
        'Content-Type': 'application/json'}
    supplied.update(headers or {})
    connection = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
    try:
        connection.request(method, path, body=None if body is None else json.dumps(body), headers=supplied)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_private_transport_reuses_existing_service_token_and_rejects_arbitrary_job_access(work):
    service, _, peer, request = work
    with serving(CreationHTTPServer(object(), TOKEN, pump=False, film_review_service=service)) as server:
        prefix = '/api/film-reviews'
        assert http(server, 'GET', prefix + '/capabilities', token='wrong' * 10)[0] == 403
        assert peer.calls == []
        assert http(server, 'POST', prefix + '/operations', request)[0] == 202
        path = prefix + '/operations/' + request['operation_id']
        assert http(server, 'POST', path + '/lookup', binding(request), owner='other-owner')[0] == 404
        assert peer.calls == []
        assert http(server, 'POST', prefix + '/operations/' + JOB + '/lookup', binding(request))[0] == 404
        assert http(server, 'POST', path + '/cancel', binding(request))[1]['state'] == 'cancelled'
        assert peer.calls == []
        assert http(server, 'GET', prefix + '/jobs/' + JOB)[0] == 404
        assert http(server, 'GET', prefix + '/capabilities?job_id=' + JOB)[0] == 422


def test_private_body_limit_is_applied_before_reading_the_claimed_input(work):
    service, _, peer, _ = work
    with serving(CreationHTTPServer(object(), TOKEN, pump=False, film_review_service=service)) as server:
        status, value = http(server, 'POST', '/api/film-reviews/operations', {},
            headers={'Content-Length': str(MAX_BODY + 1)})
        assert status == 413 and value['error'] == 'animation_creation_body_size'
        assert peer.calls == []


def test_workbench_bound_client_crosses_private_http_and_returns_real_review_evidence(work):
    from video_workbench.animation_creation_http import CreationAPIClient
    from video_workbench.film_review_client import FilmReviewGateway
    from agent_service.contracts import digest

    service, _, peer, request = work
    request['operation_id'] = 'formal:review-1'
    with serving(CreationHTTPServer(object(), TOKEN, pump=False, film_review_service=service)) as server:
        transport = CreationAPIClient('http://127.0.0.1:' + str(server.server_port), TOKEN)
        bound = FilmReviewGateway(transport).for_context(OWNER, request['project_id'], request['source'],
            source_sha256=request['source_sha256'])
        _, files = validate_request(request)
        refs = bound.upload(request['document'], files)
        admit = bound.prepare_submission(request['operation_id'], request['document'], request['deadline_at'], refs)
        assert peer.count('POST', '/v1/jobs') == 0
        assert admit()['job_id'] == request['operation_id']
        assert bound.lookup(request['operation_id'])['state'] == 'queued'
        remote = peer.request['business_ref'][len(AnimationInputReviewGateway.business_prefix):]
        expected = peer.complete(remote, request['document'])
        assert bound.poll(request['operation_id'])['state'] == 'completed'
        assert bound.collect(request['operation_id'], digest(request['document']), request['document']) == expected
        assert bound.model_frame_receipt(request['operation_id'])['job_id'] == JOB
        assert peer.count('POST', '/v1/jobs') == 1


def test_executor_wires_existing_v4_reviewer_without_another_credential_or_startup_io(tmp_path, monkeypatch):
    from video_workbench import animation_creation_executor as executor
    from test_animation_creation_executor import NoNetworkAgentClient, IMAGE

    NoNetworkAgentClient.instances = []
    monkeypatch.setattr(executor, 'AgentClient', NoNetworkAgentClient)
    agent, private = tmp_path / 'agent-token', tmp_path / 'private-token'
    agent.write_text('existing-agent-token-' + 'a' * 40, encoding='ascii')
    private.write_text(TOKEN, encoding='ascii')
    server, client = executor.create_server(root=tmp_path / 'executor', agent_url='http://127.0.0.1:1',
        agent_token_file=agent, token_file=private, renderer_image=IMAGE, enable_isolated_rendering=True,
        port=0)
    try:
        assert server.film_review_service.gateway is server.service.review_gateway
        assert server.film_review_service.gateway.client is client
        assert len(NoNetworkAgentClient.instances) == 1
        assert client.calls == [] and server.film_review_service.pending() == []
        assert server.film_review_pump.thread.is_alive()
    finally:
        server.server_close()
        client.close()
    assert not server.film_review_pump.thread.is_alive()
