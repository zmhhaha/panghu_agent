"""Explicit new execution budgets preserve failed work and effect identity."""
from copy import deepcopy
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, digest
from agent_service.profiles import video_animation_method as protocol
from test_animation_method_service import CRITERIA, work, start, finish, runtime_capabilities
from test_animation_method_effect_groups import effect_for
from test_animation_method_commissions import document, state, OWNER


def fail_run(service, run_id, *, with_sample=False):
    if with_sample:
        for _ in range(30):
            if service.get('developer', run_id)['candidates']:
                break
            service.advance('developer', run_id)
        assert service.get('developer', run_id)['candidates']
    else:
        operation = service.commissions.reserve('developer', run_id, 'creator', {'mode': 'plan'}, 'failed-plan')
        service.commissions.claim('developer', operation['id'])
        service.commissions.fail('developer', operation['id'], 'fixture_plan_failed')
    service.commissions.stop('developer', run_id, 'failed', 'Fixture execution failed')
    return service.get('developer', run_id)


def retry(service, run_id, **options):
    return start(service, retry_of_commission_id=run_id, **options)


def test_failed_plan_retries_with_fresh_budget_and_deadline_without_reopening_old_ledger(work):
    service, creator, reviewer, renderer = work
    now = [1000]
    service.clock = service.commissions.clock = lambda: now[0]
    original = start(service, title='Canvas effect', reference={'url': 'https://example.com/reference'},
                     duration_seconds=14, budget={'max_rounds': 2, 'max_seconds': 120})
    failed = fail_run(service, original['id'])
    before = service.commissions.get('developer', original['id'])
    events = service.commissions.events('developer', original['id'])
    now[0] += 150
    body = dict(request_id=str(uuid4()), retry_of_commission_id=original['id'],
                goal=original['goal'], budget={'max_rounds': 2, 'max_seconds': 120})
    queued = service.start('developer', body)
    fresh = service.commissions.get('developer', queued['id'])
    assert queued['id'] != original['id'] and queued['state'] == 'queued'
    assert queued['can_retry'] is False and failed['can_retry'] is True
    assert queued['reference'] == original['reference'] and queued['duration_seconds'] == 14
    assert queued['retry_budget'] == failed['retry_budget'] == body['budget']
    assert fresh['deadline_at'] == now[0] + 120
    assert fresh['usage'] == {'creator': 0, 'review': 0, 'render': 0} and fresh['operations'] == []
    assert 'base_commission_id' not in fresh['frozen']
    assert fresh['frozen']['retry_of_commission_id'] == original['id']
    assert service.start('developer', body)['id'] == queued['id']
    assert len(service.commissions.list_all('developer')) == 2
    assert service.commissions.get('developer', original['id']) == before
    assert service.commissions.events('developer', original['id']) == events
    assert creator.submissions == reviewer.submissions == renderer.calls == []
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        service.start('developer', dict(body, goal='Changed idempotent payload'))
    completed = finish(service, queued['id'])
    assert completed['state'] == 'completed'
    effect = effect_for(service, queued['id'])
    assert effect['id'] == original['id'] and effect['open_commission_id'] == queued['id']
    assert effect['best_candidate']['id'] == completed['best_candidate_id']
    assert [row['kind'] for row in effect['commissions']] == ['original', 'retry']
    assert effect['commissions'][-1]['retry_of_commission_id'] == original['id']
    assert service.start('developer', body)['id'] == queued['id']
    assert service.commissions.get('developer', original['id']) == before


@pytest.mark.parametrize('terminal', ['failed', 'cancelled'])
def test_only_same_owner_stopped_failures_or_cancellations_can_retry(work, terminal):
    service, _, _, _ = work
    original = start(service)
    with pytest.raises(ServiceError, match='retry_not_available'):
        retry(service, original['id'])
    if terminal == 'failed':
        fail_run(service, original['id'])
    else:
        service.cancel('developer', original['id'], {})
    with pytest.raises(ServiceError, match='not_found'):
        service.start('other-owner', dict(request_id=str(uuid4()), retry_of_commission_id=original['id'],
            goal='Private retry', budget={'max_rounds': 1, 'max_seconds': 300}))
    queued = retry(service, original['id'])
    completed = finish(service, queued['id'])
    with pytest.raises(ServiceError, match='retry_not_available'):
        retry(service, completed['id'])
    assert service.get('developer', original['id'])['state'] == terminal


def test_failed_render_with_sample_stays_playable_while_retries_run_or_fail(work):
    service, _, _, _ = work
    failed = fail_run(service, start(service)['id'], with_sample=True)
    original = service.commissions.get('developer', failed['id'])
    queued = retry(service, failed['id'])
    effect = effect_for(service, queued['id'])
    assert effect['state'] == 'queued' and effect['open_commission_id'] == failed['id']
    assert effect['best_candidate']['id'] == failed['best_candidate_id']
    assert service.artifact('developer', failed['id'], failed['best_candidate_id'], 'media')
    failed_retry = fail_run(service, queued['id'], with_sample=True)
    effect = effect_for(service, failed_retry['id'])
    assert effect['open_commission_id'] == failed['id']
    assert effect['best_candidate']['id'] == failed['best_candidate_id']
    assert service.artifact('developer', failed_retry['id'], failed_retry['best_candidate_id'], 'media')
    completed = finish(service, retry(service, failed_retry['id'])['id'])
    effect = effect_for(service, completed['id'])
    assert effect['open_commission_id'] == completed['id'] and len(effect['commissions']) == 3
    assert service.commissions.get('developer', failed['id']) == original


def test_retry_inherits_continuation_baseline_server_side_and_allows_new_goal_criteria(work):
    service, _, _, _ = work
    original = finish(service, start(service)['id'])
    failed = fail_run(service, start(service, base_commission_id=original['id'])['id'])
    before = service.commissions.get('developer', failed['id'])['frozen']
    criteria = [dict(id='new_goal', description='A distinct observable requirement')]
    queued = retry(service, failed['id'], goal='Revised expression', duration_seconds=16, criteria=criteria)
    frozen = service.commissions.get('developer', queued['id'])['frozen']
    for key in ('base_commission_id', 'base_candidate_id', 'method_versions', 'base_candidate'):
        assert frozen[key] == before[key]
    assert frozen['criteria'] == criteria and queued['duration_seconds'] == 16
    with pytest.raises(ServiceError, match='retry_relationship_invalid'):
        retry(service, failed['id'], base_commission_id=failed['id'])
    completed = finish(service, queued['id'])
    effect = effect_for(service, completed['id'])
    assert effect['id'] == original['id'] and effect['open_commission_id'] == completed['id']
    assert [row['kind'] for row in effect['commissions']] == ['original', 'continuation', 'retry']


def test_retry_of_transfer_keeps_frozen_method_and_never_replaces_reference(work):
    service, _, _, _ = work
    original = finish(service, start(service)['id'])
    transfer = service.transfer('developer', original['id'], dict(request_id=str(uuid4()),
        goal='Explain a fork', structural_changes={'relations': {'before': 'chain', 'after': 'fork'}},
        budget={'max_rounds': 1, 'max_seconds': 300}))
    failed = fail_run(service, transfer['id'])
    before = service.commissions.get('developer', failed['id'])['frozen']
    queued = retry(service, failed['id'], goal='Explain a fork clearly')
    frozen = service.commissions.get('developer', queued['id'])['frozen']
    for key in ('role', 'baseline_demonstration_id', 'structural_changes', 'method_versions'):
        assert frozen[key] == before[key]
    assert 'base_commission_id' not in frozen
    completed = finish(service, queued['id'])
    assert completed['state'] == 'completed'
    effect = effect_for(service, completed['id'])
    assert effect['id'] == original['id'] and effect['open_commission_id'] == original['id']
    assert effect['commissions'][-1]['kind'] == 'retry' and effect['commissions'][-1]['role'] == 'transfer'


def test_competing_successful_retries_do_not_infer_global_best(work):
    service, _, _, _ = work
    original = fail_run(service, start(service)['id'], with_sample=True)
    first = finish(service, retry(service, original['id'])['id'])
    second = finish(service, retry(service, original['id'])['id'])
    effect = effect_for(service, second['id'])
    assert len(service.catalogue('developer')['effects']) == 1
    assert effect['open_commission_id'] == original['id']
    assert {row['id'] for row in effect['commissions']} == {original['id'], first['id'], second['id']}


def test_empty_failed_retry_branch_cannot_hide_the_only_successful_sample(work):
    service, _, _, _ = work
    original = fail_run(service, start(service)['id'])
    empty = fail_run(service, retry(service, original['id'])['id'])
    completed = finish(service, retry(service, original['id'])['id'])
    effect = effect_for(service, completed['id'])
    assert effect['open_commission_id'] == completed['id']
    assert effect['best_candidate']['id'] == completed['best_candidate_id']
    assert len(effect['commissions']) == 3
    # An empty intermediate failure becomes relevant when it has a successful
    # descendant. Then there are two successful branches and no global winner.
    descendant = finish(service, retry(service, empty['id'])['id'])
    effect = effect_for(service, descendant['id'])
    assert len(effect['commissions']) == 4
    assert effect['open_commission_id'] == original['id'] and effect['best_candidate'] is None


def test_retry_uses_new_trusted_runtime_and_replay_keeps_its_snapshot(work, monkeypatch):
    service, _, _, renderer = work
    admitted = runtime_capabilities(['html-svg'])
    monkeypatch.setattr(renderer, 'capabilities', lambda: deepcopy(admitted), raising=False)
    original = fail_run(service, start(service)['id'])
    admitted.update(runtime_capabilities(['html-svg', 'canvas']))
    body = dict(request_id=str(uuid4()), goal=original['goal'], retry_of_commission_id=original['id'],
                budget={'max_rounds': 1, 'max_seconds': 300})
    queued = service.start('developer', body)
    frozen = service.commissions.get('developer', queued['id'])['frozen']
    assert frozen['constraints']['allowed_engines'] == ['html-svg', 'canvas']
    admitted.update(runtime_capabilities(['html-svg', 'canvas', 'three']))
    assert service.start('developer', body)['id'] == queued['id']
    assert service.commissions.get('developer', queued['id'])['frozen'] == frozen


def test_store_enforces_owner_terminal_and_relationship_constraints_and_replay(state):
    store, run_id, _ = state
    body = document(retry_of_commission_id=run_id)
    with pytest.raises(ServiceError, match='retry_not_available'):
        store.create(OWNER, body, 'retry-1')
    store.stop(OWNER, run_id, 'failed', 'Fixture failed')
    before = store.get(OWNER, run_id)
    with pytest.raises(ServiceError, match='not_found'):
        store.create('foreign', body, 'retry-foreign')
    with pytest.raises(ServiceError, match='retry_relationship_invalid'):
        store.create(OWNER, dict(body, method_versions=['unrelated']), 'retry-forged')
    created = store.create(OWNER, body, 'retry-1')
    assert store.create(OWNER, body, 'retry-1')['id'] == created['id']
    assert created['id'] != run_id and store.get(OWNER, run_id) == before
    assert created['usage'] == {'creator': 0, 'review': 0, 'render': 0}


def test_plan_requirements_prevent_observed_uppercase_and_method_shape_errors(work):
    service, creator, _, _ = work
    queued = start(service)
    run = service.commissions.get('developer', queued['id'])
    document = service._document('developer', run, 'plan')
    requirements = document['constraints']['result_requirements']
    assert requirements['source'] is None and requirements['review'] is None
    assert requirements['sound_events'] == []
    assert all(term in requirements['instruction'] for term in (
        '[a-z][a-z0-9_.-]{0,99}', 'lowercase', 'applicability', 'nonempty string',
        'nonempty exact subset', 'complete criterion objects'))
    assert protocol.validate_input(document) == document
    # The hint preserves strict shared validation; it does not silently repair
    # malformed results or claim a previously failed execution succeeded.
    valid = dict(schema=protocol.RESULT_SCHEMA, input_sha256=digest(document), mode='plan',
        criteria=deepcopy(CRITERIA), methods=[], source=None, sound_events=[], review=None)
    assert protocol.validate_result(valid, digest(document), document) == valid
    invalid = deepcopy(valid)
    invalid['criteria'][0]['id'] = 'C1'
    with pytest.raises(ServiceError):
        protocol.validate_result(invalid, digest(document), document)
