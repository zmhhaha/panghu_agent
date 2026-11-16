"""Real SQLite concurrency limits; synthetic source only, no external jobs."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.animation_collaboration import operation_input
from video_workbench.animation_method_commissions import AnimationMethodCommissionStore
from test_animation_collaboration import CRITERIA, base_document, plan_document


OWNER = 'developer'


@pytest.fixture
def ledger(tmp_path):
    now = [100.0]
    store = AnimationMethodCommissionStore(tmp_path / 'commissions.sqlite3', clock=lambda: now[0])
    commission = store.create(OWNER, dict(goal='Synthetic source-only cooperative composition',
        collaboration={'max_components': 3},
        budget=dict(creator=5, render=1, review=1, seconds=300)), 'collaboration-request')
    plan = plan_document()
    # Three independent responsibilities make the two-slot cap observable.
    plan['components'][2]['depends_on'] = []
    document = operation_input(base_document(), 'plan', config={'max_components': 3})
    op = store.reserve(OWNER, commission['id'], 'creator', document, 'collaboration-plan')
    assert store.claim(OWNER, op['id'])['submission_claimed'] is True
    store.complete(OWNER, op['id'], dict(schema='video.animation-collaboration-result.v1',
        phase='plan', input_sha256=digest(document), criteria=deepcopy(CRITERIA), methods=[],
        source=None, sound_events=[], review=None, knowledge_proposals=[], plan=plan, component=None))
    return store, commission['id'], now, plan


def component_input(plan, identity):
    return operation_input(base_document(), 'component', config={'max_components': 3},
        brief=plan, component_id=identity, dependencies=[], criteria=CRITERIA)


def integration_input(plan):
    dependencies = [dict(component_id=part['id'], engine='canvas', interface=part['interface'],
                         source='export function draw() {} // synthetic inert source')
                    for part in plan['components']]
    return operation_input(base_document(), 'integrate', config={'max_components': 3},
                           brief=plan, dependencies=dependencies, criteria=CRITERIA)


def reserve(store, commission_id, plan, identity, key=None):
    return store.reserve(OWNER, commission_id, 'creator', component_input(plan, identity),
                         key or 'component-' + identity)


def test_two_distinct_parts_with_same_plan_reserve_and_claim_independently(ledger):
    store, commission_id, _, plan = ledger
    first = reserve(store, commission_id, plan, 'mountains')
    second = reserve(store, commission_id, plan, 'foreground')
    assert first['id'] != second['id']
    assert store.claim(OWNER, first['id'])['submission_claimed'] is True
    assert store.claim(OWNER, second['id'])['submission_claimed'] is True
    run = store.get(OWNER, commission_id)
    assert run['usage']['creator'] == 3 and run['remaining']['creator'] == 2
    assert run['deadline_at'] == 400 and len(run['candidates']) == 0


def test_three_concurrent_connections_cannot_exceed_two_component_slots(ledger):
    store, commission_id, now, plan = ledger
    barrier = threading.Barrier(3)

    def attempt(identity):
        connection = AnimationMethodCommissionStore(store.path, clock=lambda: now[0])
        barrier.wait()
        try:
            return ('reserved', reserve(connection, commission_id, plan, identity)['id'])
        except ServiceError as error:
            return ('rejected', error.code)

    with ThreadPoolExecutor(max_workers=3) as pool:
        outcomes = list(pool.map(attempt, ['mountains', 'foreground', 'atmosphere']))
    assert sum(row[0] == 'reserved' for row in outcomes) == 2
    assert [row[1] for row in outcomes if row[0] == 'rejected'] == ['animation_method_operation_in_progress']
    assert store.get(OWNER, commission_id)['usage']['creator'] == 3


def test_same_component_cannot_take_a_second_parallel_slot_with_different_key(ledger):
    store, commission_id, _, plan = ledger
    original = reserve(store, commission_id, plan, 'mountains')
    with pytest.raises(ServiceError, match='operation_in_progress'):
        reserve(store, commission_id, plan, 'mountains', key='duplicate-identity')
    assert reserve(store, commission_id, plan, 'mountains')['id'] == original['id']
    assert store.get(OWNER, commission_id)['usage']['creator'] == 2


def test_different_plan_cannot_share_existing_component_slot(ledger):
    store, commission_id, _, plan = ledger
    reserve(store, commission_id, plan, 'mountains')
    changed = deepcopy(plan)
    changed['material'] = 'A different frozen material agreement.'
    with pytest.raises(ServiceError, match='operation_in_progress'):
        reserve(store, commission_id, changed, 'foreground')
    assert store.get(OWNER, commission_id)['usage']['creator'] == 2


@pytest.mark.parametrize('other_stage,other_phase', [
    ('creator', 'plan'), ('creator', 'integrate'), ('review', 'plan'), ('render', 'plan'),
])
def test_component_does_not_overlap_plan_integration_review_or_render(ledger, other_stage, other_phase):
    store, commission_id, _, plan = ledger
    reserve(store, commission_id, plan, 'mountains')
    document = (integration_input(plan) if other_phase == 'integrate' else
                operation_input(base_document(), 'plan', config={'max_components': 3}))
    with pytest.raises(ServiceError, match='operation_in_progress'):
        store.reserve(OWNER, commission_id, other_stage, document, 'forbidden-' + other_stage + other_phase)


@pytest.mark.parametrize('phase', ['plan', 'integrate'])
def test_active_plan_or_integration_cannot_overlap_another_creator(ledger, phase):
    store, commission_id, _, plan = ledger
    document = (integration_input(plan) if phase == 'integrate' else
                operation_input(base_document(), 'plan', config={'max_components': 3}))
    store.reserve(OWNER, commission_id, 'creator', document, 'active-' + phase)
    with pytest.raises(ServiceError, match='operation_in_progress'):
        reserve(store, commission_id, plan, 'mountains')


def test_plain_commission_cannot_borrow_cooperative_parallelism(ledger):
    store, _, _, plan = ledger
    ordinary = store.create(OWNER, dict(goal='Ordinary experiment without collaboration opt-in',
        budget=dict(creator=5, render=1, review=1, seconds=300)), 'ordinary-request')
    reserve(store, ordinary['id'], plan, 'mountains')
    with pytest.raises(ServiceError, match='operation_in_progress'):
        reserve(store, ordinary['id'], plan, 'foreground')
    assert store.get(OWNER, ordinary['id'])['usage']['creator'] == 1


def test_two_slots_keep_same_idempotent_operations_when_reopened(ledger):
    store, commission_id, now, plan = ledger
    first = reserve(store, commission_id, plan, 'mountains')
    second = reserve(store, commission_id, plan, 'foreground')
    reopened = AnimationMethodCommissionStore(store.path, clock=lambda: now[0])
    assert reserve(reopened, commission_id, plan, 'mountains')['id'] == first['id']
    assert reserve(reopened, commission_id, plan, 'foreground')['id'] == second['id']
    changed = component_input(plan, 'mountains')
    changed['feedback'] = 'Changed input under original key'
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        reopened.reserve(OWNER, commission_id, 'creator', changed, 'component-mountains')
    assert reopened.get(OWNER, commission_id)['usage']['creator'] == 3


def test_cancel_keeps_both_original_claims_until_stop_proven(ledger):
    store, commission_id, _, plan = ledger
    first = reserve(store, commission_id, plan, 'mountains')
    second = reserve(store, commission_id, plan, 'foreground')
    for op in [first, second]:
        store.claim(OWNER, op['id'])
        store.mark_unknown(OWNER, op['id'])
    assert store.cancel(OWNER, commission_id)['state'] == 'cancelling'
    with pytest.raises(ServiceError, match='commission_stopped'):
        reserve(store, commission_id, plan, 'atmosphere')
    with pytest.raises(ServiceError, match='operation_in_progress'):
        store.stop(OWNER, commission_id, 'cancelled', 'No stop proof yet')
    for index, op in enumerate([first, second]):
        assert store.claim(OWNER, op['id'])['submission_claimed'] is False
        store.mark_submitted(OWNER, op['id'], 'original-job-' + str(index))
        store.fail(OWNER, op['id'], 'original_job_stopped', cancelled=True)
    stopped = store.stop(OWNER, commission_id, 'cancelled', 'Both original tasks actually stopped')
    assert stopped['usage']['creator'] == 3 and stopped['deadline_at'] == 400
    assert [row['job_id'] for row in stopped['operations'][1:]] == ['original-job-0', 'original-job-1']


def test_deadline_blocks_new_claim_but_unknown_claim_still_reconciles_original_id(ledger):
    store, commission_id, now, plan = ledger
    unknown = reserve(store, commission_id, plan, 'mountains')
    unclaimed = reserve(store, commission_id, plan, 'foreground')
    store.claim(OWNER, unknown['id'])
    store.mark_unknown(OWNER, unknown['id'])
    now[0] = 400
    with pytest.raises(ServiceError, match='deadline_exceeded'):
        store.claim(OWNER, unclaimed['id'])
    with pytest.raises(ServiceError, match='deadline_exceeded'):
        reserve(store, commission_id, plan, 'atmosphere')
    assert store.claim(OWNER, unknown['id'])['submission_claimed'] is False
    store.mark_submitted(OWNER, unknown['id'], 'original-timed-out-job')
    store.fail(OWNER, unknown['id'], 'timeout_stop_verified')
    store.release_unsubmitted(OWNER, unclaimed['id'], {'reason': 'Deadline before original claim'})
    stopped = store.stop(OWNER, commission_id, 'budget_exhausted', 'Deadline reached, both tasks stopped')
    assert stopped['usage']['creator'] == 2 and stopped['deadline_at'] == 400


def test_each_component_upload_manifest_is_bound_to_its_original_input(ledger):
    store, commission_id, _, plan = ledger
    first = reserve(store, commission_id, plan, 'mountains')
    second = reserve(store, commission_id, plan, 'foreground')
    manifest = [dict(path='collaboration-input.json', sha256=first['input_sha256'])]
    recorded = store.record_input_manifest(OWNER, first['id'], manifest)
    assert recorded['input_resource_manifest_sha256'] == digest(manifest)
    assert store.record_input_manifest(OWNER, first['id'], manifest) == recorded
    with pytest.raises(ServiceError, match='input_manifest_invalid'):
        store.record_input_manifest(OWNER, second['id'], manifest)
