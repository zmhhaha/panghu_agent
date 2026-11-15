"""Read-only stop checks through real Agent gateways and the public proof contract."""
from copy import deepcopy
from pathlib import Path
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from agent_service.api import _public_stop_proof
from agent_service.client import AgentClient
from agent_service.contracts import ServiceError
from video_workbench.animation_method_gateway import (
    AnimationMethodAuthorGateway, AnimationMethodReviewGateway,
    AnimationSkillMethodAuthorGateway, AnimationSkillMethodReviewGateway,
)
from video_workbench.animation_method_service import AnimationMethodService


OWNER = 'stop-contract-owner'
GENERATIONS = [
    (AnimationMethodAuthorGateway, AnimationMethodReviewGateway),
    (AnimationSkillMethodAuthorGateway, AnimationSkillMethodReviewGateway),
]
STOPPED_STATES = ['completed', 'failed', 'cancelled', 'needs_input', 'timed_out']


def public_job(gateway_class, operation_id, job_id, state='completed', *, proven=True):
    attempt_id = str(uuid4())
    stored = dict(job_id=job_id, state=state, attempt_id=attempt_id, fence=1,
                  pins=dict(engine='pi'))
    receipt = dict(job_id=job_id, attempt_id=attempt_id, fence=1, engine='pi',
        request_sha256='a' * 64, result_sha256='b' * 64,
        resource_ids=['container:original-attempt'], observed_at=1000.0)
    proof = _public_stop_proof(stored, dict(_service_stop_proven=proven, stop_receipt=receipt))
    return dict(job_id=job_id, state=state, publication_authorized=False,
        business_ref=gateway_class.business_prefix + operation_id,
        pins=dict(profile=gateway_class.profile, profile_revision='1'),
        attempt=dict(attempt_id=attempt_id, fence=1), stop_proven=proof['stop_proven'],
        stop_proof=proof)


class ReadOnlyPeer:
    def __init__(self):
        self.jobs, self.calls = {}, []

    def __call__(self, request):
        self.calls.append((request.method, request.url.path))
        assert request.method == 'GET', 'A stop check must never submit or cancel a job'
        if request.url.path not in self.jobs:
            return httpx.Response(404, json=dict(detail=dict(code='job_not_found')))
        return httpx.Response(200, json=self.jobs[request.url.path])


@pytest.fixture
def setup(tmp_path):
    peer = ReadOnlyPeer()
    with AgentClient('http://agent.invalid', 'offline-stop-contract-' + 'x' * 32,
                     transport=httpx.MockTransport(peer)) as client:
        def create(current, original, stage, state='completed', *, proven=True, bound_job=True):
            service = AnimationMethodService(tmp_path / str(uuid4()),
                author_gateway=current[0](client), review_gateway=current[1](client),
                renderer=SimpleNamespace())
            gateway_class = original[stage == 'review']
            run = service.commissions.create(OWNER, dict(title='Stopped contract fixture',
                goal='Verify original task custody', budget=dict(creator=1, review=1, render=0, seconds=300),
                protocol_schema=original[0].protocol.INPUT_SCHEMA), str(uuid4()))
            op = service.commissions.reserve(OWNER, run['id'], stage,
                dict(schema=original[0].protocol.INPUT_SCHEMA, mode='review' if stage == 'review' else 'plan'),
                str(uuid4()))
            service.commissions.claim(OWNER, op['id'])
            job_id = str(uuid4())
            if bound_job:
                service.commissions.mark_submitted(OWNER, op['id'], job_id)
            service.commissions.fail(OWNER, op['id'], 'fixture_stopped')
            service.commissions.stop(OWNER, run['id'], 'failed', 'Fixture terminal without generation')
            path = '/v1/jobs/by-idempotency/' + gateway_class.idempotency_prefix + op['id']
            peer.jobs[path] = public_job(gateway_class, op['id'], job_id, state, proven=proven)
            return service, run['id'], op['id'], path
        yield create, peer


def delete_body(service, run_id):
    with service.trash.database() as db:
        eligibility = service.trash.eligibility(db, service.trash.state(db, OWNER), 'commission', run_id)[0]
    return dict(request_id=str(uuid4()), expected_snapshot_sha256=eligibility['snapshot_sha256'])


def original_evidence(service):
    return {path: Path(path).read_bytes() for path in (service.commissions.path, service.methods.path)}


@pytest.mark.parametrize('current,original', [(GENERATIONS[1], GENERATIONS[0]),
                                            (GENERATIONS[0], GENERATIONS[1])], ids=['legacy-history', 'skill-history'])
@pytest.mark.parametrize('stage', ['creator', 'review'])
@pytest.mark.parametrize('state', STOPPED_STATES)
def test_real_historical_lookup_accepts_public_stop_proof_without_rewriting_evidence(setup, current, original, stage, state):
    create, peer = setup
    service, run_id, _, path = create(current, original, stage, state)
    job = peer.jobs[path]
    assert job['stop_proof']['stop_proven'] is True
    assert job['stop_proof']['attempt_id'] == job['attempt']['attempt_id']
    before = original_evidence(service)
    configured = [(gateway.protocol, gateway.profile, gateway.business_prefix,
                   gateway.idempotency_prefix, gateway.client) for gateway in (service.author, service.reviewer)]
    result = service.trash.delete(OWNER, 'commission', run_id, delete_body(service, run_id))
    assert result['action'] == 'trash'
    assert peer.calls == [('GET', path)]
    assert original_evidence(service) == before
    assert configured == [(gateway.protocol, gateway.profile, gateway.business_prefix,
        gateway.idempotency_prefix, gateway.client) for gateway in (service.author, service.reviewer)]


@pytest.mark.parametrize('change', ['running', 'quarantined', 'unproven', 'wrong-job',
                                   'wrong-business', 'missing-job', 'unknown-schema'])
def test_unproven_missing_or_mismatched_original_task_is_rejected_without_writes(setup, change):
    create, peer = setup
    state = change if change in {'running', 'quarantined'} else 'timed_out'
    service, run_id, _, path = create(GENERATIONS[1], GENERATIONS[0], 'creator', state,
                                      proven=change != 'unproven')
    if change == 'wrong-job':
        peer.jobs[path]['job_id'] = str(uuid4())
    elif change == 'wrong-business':
        peer.jobs[path]['business_ref'] = 'unrelated:' + str(uuid4())
    elif change == 'missing-job':
        peer.jobs.clear()
    elif change == 'unknown-schema':
        operation = service.commissions.get(OWNER, run_id)['operations'][0]
        operation = deepcopy(operation)
        operation['input']['schema'] = 'video.animation-method-input.future'
        before = original_evidence(service)
        with pytest.raises(ServiceError, match='animation_method_trash_stop_unproven'):
            service.trash.stopped(OWNER, [dict(operations=[operation], frozen={})])
        assert peer.calls == [] and original_evidence(service) == before
        return
    before = original_evidence(service)
    metadata = Path(service.metadata_path).read_bytes()
    with pytest.raises(ServiceError, match='animation_method_trash_stop_unproven'):
        service.trash.delete(OWNER, 'commission', run_id, delete_body(service, run_id))
    assert peer.calls == [('GET', path)]
    assert original_evidence(service) == before
    assert Path(service.metadata_path).read_bytes() == metadata
    assert service.trash_catalogue(OWNER)['items'] == []


@pytest.mark.parametrize('state', STOPPED_STATES)
def test_manim_recipe_uses_agent_stopped_states_with_the_original_job_binding(setup, state):
    create, peer = setup
    service, _, operation_id, path = create(GENERATIONS[0], GENERATIONS[0], 'creator', state)
    job = peer.jobs[path]
    coordinator = SimpleNamespace(gateway=service.author)
    old = dict(state='failed', recipe_claimed=True, scope=dict(operation_id=operation_id), job_id=job['job_id'])
    before = original_evidence(service)
    service.trash.manim_stopped(coordinator, old, time.monotonic() + 20)
    assert peer.calls == [('GET', path)] and original_evidence(service) == before


def test_lost_submission_ack_still_requires_lookup_proof_for_the_frozen_namespace(setup):
    create, peer = setup
    service, run_id, _, path = create(GENERATIONS[1], GENERATIONS[0], 'creator', 'needs_input', bound_job=False)
    service.trash.delete(OWNER, 'commission', run_id, delete_body(service, run_id))
    assert peer.calls == [('GET', path)]


@pytest.mark.parametrize('stage', ['creator', 'review'])
@pytest.mark.parametrize('found', [True, False], ids=['original-caller', 'missing-original-job'])
def test_explicit_historical_caller_is_used_without_falling_back_to_current_identity(setup, stage, found):
    create, current_peer = setup
    service, run_id, _, path = create(GENERATIONS[1], GENERATIONS[0], stage, 'completed')
    historical_peer = ReadOnlyPeer()
    if found:
        historical_peer.jobs[path] = deepcopy(current_peer.jobs[path])
    # The current caller can even expose a different job under the same key;
    # it must never replace the explicitly configured historical caller.
    current_peer.jobs[path]['job_id'] = str(uuid4())
    with AgentClient('http://historical-agent.invalid', 'offline-historical-caller-' + 'y' * 32,
                     transport=httpx.MockTransport(historical_peer)) as client:
        old_gateway = GENERATIONS[0][stage == 'review'](client)
        service.historical_method_gateways = {(GENERATIONS[0][0].protocol.INPUT_SCHEMA, stage): old_gateway}
        before = original_evidence(service)
        metadata = Path(service.metadata_path).read_bytes()
        if found:
            service.trash.delete(OWNER, 'commission', run_id, delete_body(service, run_id))
        else:
            with pytest.raises(ServiceError, match='animation_method_trash_stop_unproven'):
                service.trash.delete(OWNER, 'commission', run_id, delete_body(service, run_id))
            assert Path(service.metadata_path).read_bytes() == metadata
        assert historical_peer.calls == [('GET', path)]
        assert current_peer.calls == []
        assert original_evidence(service) == before
        assert service.author.client is service.reviewer.client
        assert service.author.client is not client
