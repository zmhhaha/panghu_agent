"""Independent transport tests for collaborative job identity and stop lookup."""
import hashlib
import json
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest

from agent_service.client import AgentClient
from agent_service.contracts import ServiceError, canonical_bytes
from agent_service.profiles import video_animation_collaboration_contract as contract
from video_workbench.animation_collaboration_gateway import AnimationCollaborationGateway
from video_workbench.animation_method_trash import AnimationMethodTrash


JOB = str(UUID(int=770))
INPUT = str(UUID(int=771))
OPERATION = 'source-operation-identity'


def document():
    return contract.build_input(phase='plan', goal='A synthetic coherent collaboration transport fixture.',
        criteria=[], constraints=dict(width=1280, height=720, fps=24,
            duration_seconds=8, allowed_engines=['canvas']), assets={})


class Peer:
    def __init__(self):
        self.calls = []
        self.job = None
        self.lose_response = False
        self.changed_business = False
        self.profiles = [dict(profile=contract.PROFILE, profile_revision='1', configured=True,
            ready=True, supports_inputs=True, max_timeout_seconds=600,
            required_model_capabilities=['text', 'tool_calls'])]

    def __call__(self, request):
        self.calls.append(request)
        path = request.url.path
        if path == '/v1/capabilities':
            return httpx.Response(200, json=dict(profiles=self.profiles))
        if path == '/v1/inputs':
            return httpx.Response(201, json=dict(input_id=INPUT, name=request.url.params['name'],
                size=len(request.content), sha256=hashlib.sha256(request.content).hexdigest()))
        if path == '/v1/jobs':
            value = json.loads(request.content)
            self.job = dict(job_id=JOB, state='queued', business_ref=value['business_ref'], publication_authorized=False)
            if self.lose_response:
                raise httpx.ReadTimeout('lost admission response', request=request)
            return httpx.Response(202, json=self.job)
        if path == '/v1/jobs/by-idempotency/animation-collaboration-' + OPERATION:
            value = dict(self.job)
            if self.changed_business:
                value['business_ref'] = 'animation-method:' + OPERATION
            return httpx.Response(200, json=value)
        if path == '/v1/jobs/' + JOB + '/cancel':
            self.job.update(state='cancelled', stop_proven=True)
            return httpx.Response(200, json=self.job)
        raise AssertionError((request.method, path))


@pytest.fixture
def wire(monkeypatch):
    monkeypatch.setattr('video_workbench.animation_review_gateway.time.time', lambda: 1000.0)
    peer = Peer()
    with AgentClient('http://agent.invalid', 'offline-collaboration-token-' + 'a' * 32,
                     transport=httpx.MockTransport(peer)) as client:
        yield AnimationCollaborationGateway(client), peer


def test_gateway_uploads_exact_collaboration_file_and_prepares_before_one_admission(wire):
    gateway, peer = wire
    value = document()
    refs = gateway.upload(value)
    assert refs == [dict(input_id=INPUT, sha256=hashlib.sha256(canonical_bytes(value)).hexdigest(),
                         path='collaboration-input.json', role='animation_collaboration_input')]
    admit = gateway.prepare_submission(OPERATION, value, 1600, refs)
    assert not any(row.url.path == '/v1/jobs' for row in peer.calls)
    assert admit()['job_id'] == JOB
    posts = [row for row in peer.calls if row.url.path == '/v1/jobs']
    assert len(posts) == 1
    request = json.loads(posts[0].content)
    assert request['profile'] == 'video.animation-collaboration.v1'
    assert request['business_ref'] == 'animation-collaboration:' + OPERATION
    assert posts[0].headers['Idempotency-Key'] == 'animation-collaboration-' + OPERATION
    assert request['execution_deadline_at'] == 1600
    assert request['timeout_seconds'] == 600


def test_lost_submit_response_recovers_original_identity_and_cancel_never_resubmits(wire):
    gateway, peer = wire
    value = document(); refs = gateway.upload(value); peer.lose_response = True
    with pytest.raises(ServiceError):
        gateway.submit(OPERATION, value, 1600, refs)
    assert gateway.lookup(OPERATION)['job_id'] == JOB
    assert gateway.cancel(JOB)['state'] == 'cancelled'
    assert sum(row.url.path == '/v1/jobs' for row in peer.calls) == 1
    lookup = [row for row in peer.calls if '/by-idempotency/' in row.url.path]
    assert len(lookup) == 1 and lookup[0].method == 'GET'
    assert lookup[0].url.path.endswith('animation-collaboration-' + OPERATION)


def test_unadmitted_profile_does_not_submit_or_fall_back_to_ordinary_author(wire):
    gateway, peer = wire
    value = document(); refs = gateway.upload(value)
    peer.profiles = [dict(peer.profiles[0], profile='video.animation-method-author.v2')]
    with pytest.raises(ServiceError, match='animation_profile_unavailable'):
        gateway.prepare_submission(OPERATION, value, 1600, refs)
    assert not any(row.url.path == '/v1/jobs' for row in peer.calls)


def test_trash_stop_probe_uses_collaboration_client_and_original_namespace_only(wire):
    gateway, peer = wire
    peer.job = dict(job_id=JOB, state='failed', stop_proven=True,
        business_ref='animation-collaboration:' + OPERATION, publication_authorized=False)
    trash = AnimationMethodTrash.__new__(AnimationMethodTrash)
    trash.service = SimpleNamespace(collaboration_author=gateway, author=object(), reviewer=object())
    operation = dict(id=OPERATION, stage='creator', status='failed', job_id=JOB,
        input=dict(schema=contract.INPUT_SCHEMA))
    trash.stopped('developer', [dict(frozen={}, operations=[operation])])
    assert [(row.method, row.url.path) for row in peer.calls] == [
        ('GET', '/v1/jobs/by-idempotency/animation-collaboration-' + OPERATION)]
    peer.changed_business = True
    with pytest.raises(ServiceError, match='animation_method_trash_stop_unproven'):
        trash.stopped('developer', [dict(frozen={}, operations=[operation])])
    trash.service.collaboration_author = None
    with pytest.raises(ServiceError, match='animation_method_trash_stop_unproven'):
        trash._stop_gateway(operation)
    assert all(row.method == 'GET' for row in peer.calls)
