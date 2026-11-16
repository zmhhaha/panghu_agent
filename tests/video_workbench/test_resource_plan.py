"""Common-resource orchestration tests use synthetic peers, never media approval."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading

import pytest

from agent_service.contracts import ServiceError
from video_workbench.resource_plan import validate_request
from video_workbench.resource_plan_store import ResourcePlanStore
from video_workbench.resource_plan_workflow import ResourcePlanWorkflow


OWNER, PROJECT, OPERATION = 'owner:test', 'project', 'operation'


def node(key='picture', *, dependencies=(), **changes):
    return dict(id=key, kind='animation', producer='fake', inputs={'instruction': key},
        dependencies=list(dependencies), objective='Explain the changing relationship',
        required_capabilities=[], key_effects=[], local_timing={}, placement={'start_frame': 0}, **changes)


def request(nodes=None, *, attempts=8, per_node=3):
    return dict(nodes=nodes or [node()], budget=dict(max_attempts=attempts, max_attempts_per_node=per_node),
        deadline_at=2000)


def output(task, **changes):
    value = dict(content=dict(source=task['inputs'], dependencies={key: value['resource_version_id']
        for key, value in task['dependency_outputs'].items()}), local_timing=deepcopy(task['local_timing']),
        capabilities=list(task['required_capabilities']), key_effects=list(task['key_effects']))
    value.update(changes)
    return value


class Producer:
    def __init__(self, *, status='completed', capabilities=()):
        self.status, self.supported = status, list(capabilities)
        self.tasks, self.submissions, self.lookups, self.cancellations = {}, [], [], []
        self.lose_ack = False
        self.valid = True

    def capabilities(self):
        return dict(admitted=True, kind='animation', revision='r1', capabilities=self.supported, key_effects=[])

    def receipt(self, task, status=None):
        status = status or self.status
        return dict(task_id=task['task_id'], request_sha256=task['request_sha256'], status=status,
            stop_proven=status in {'completed', 'failed', 'cancelled'},
            **({'output': output(task)} if status == 'completed' else {}))

    def submit(self, task):
        self.submissions.append(task['task_id'])
        self.tasks[task['task_id']] = deepcopy(task)
        if self.lose_ack:
            raise TimeoutError('ack lost after the real synthetic submission')
        return self.receipt(task)

    def lookup(self, task):
        self.lookups.append(task['task_id'])
        return self.receipt(task) if task['task_id'] in self.tasks else None

    def cancel(self, task):
        self.cancellations.append(task['task_id'])
        return self.receipt(task, 'cancelled')

    def validate_output(self, task, result):
        return self.valid


@pytest.fixture
def setup(tmp_path):
    clock = [1000.0]
    store = ResourcePlanStore(tmp_path / 'resources.sqlite3', clock=lambda: clock[0])
    return store, clock


def test_reads_and_replays_never_create_work_and_are_owner_scoped(setup):
    store, _ = setup
    first = store.create(OWNER, PROJECT, OPERATION, request())
    assert first == store.create(OWNER, PROJECT, OPERATION, request()) == store.get(OWNER, OPERATION)
    assert first['usage']['attempts'] == 0 and first['stop_proven']
    with pytest.raises(ServiceError, match='not_found'):
        store.get('another-owner', OPERATION)
    altered = request()
    altered['nodes'][0]['inputs'] = {'changed': True}
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        store.create(OWNER, PROJECT, OPERATION, altered)


@pytest.mark.parametrize('nodes,code', [
    ([node('a', dependencies=['missing'])], 'dependency_missing'),
    ([node('a', dependencies=['b']), node('b', dependencies=['a'])], 'dependency_cycle'),
    ([node('a'), node('a')], 'duplicate_node'),
])
def test_dependency_graph_is_validated_before_any_work(nodes, code):
    with pytest.raises(ServiceError, match=code):
        validate_request(request(nodes))


def test_independent_nodes_advance_while_a_dependent_waits(setup):
    store, _ = setup
    producer = Producer(status='running')
    store.create(OWNER, PROJECT, OPERATION, request([
        node('first'), node('dependent', dependencies=['first']), node('independent')]))
    result = ResourcePlanWorkflow(store, {'fake': producer}).advance(OWNER, OPERATION)
    assert len(producer.submissions) == 2
    assert result['nodes']['dependent']['status'] == 'queued'
    assert result['status'] == 'running' and not result['stop_proven']


def test_recovery_without_admission_polls_original_tasks_and_never_claims_pending_nodes(setup):
    store, _ = setup
    producer = Producer(status='running')
    store.create(OWNER, PROJECT, OPERATION, request([node('first'), node('later', dependencies=['first'])]))
    workflow = ResourcePlanWorkflow(store, {'fake': producer})
    workflow.advance(OWNER, OPERATION)
    assert len(producer.submissions) == 1
    original = producer.submissions[0]
    producer.status = 'completed'
    result = workflow.advance(OWNER, OPERATION, admit=False)
    assert result['nodes']['first']['status'] == 'completed'
    assert result['nodes']['later']['status'] == 'queued'
    assert producer.lookups == [original] and producer.submissions == [original]


def test_completed_dependencies_are_frozen_in_consumer_task(setup):
    store, _ = setup
    producer = Producer()
    store.create(OWNER, PROJECT, OPERATION, request([node('a'), node('b', dependencies=['a'])]))
    result = ResourcePlanWorkflow(store, {'fake': producer}).advance(OWNER, OPERATION)
    assert result['status'] == 'completed' and len(producer.submissions) == 2
    task = result['nodes']['b']['task']
    assert task['dependency_outputs']['a'] == result['nodes']['a']['output']
    assert 'placement' not in task
    assert result['nodes']['a']['output']['validation_scope'].endswith('not_expression_acceptance')


def test_unknown_ack_recovers_original_without_paid_resubmission(setup):
    store, clock = setup
    producer = Producer()
    producer.lose_ack = True
    store.create(OWNER, PROJECT, OPERATION, request())
    workflow = ResourcePlanWorkflow(store, {'fake': producer})
    first = workflow.advance(OWNER, OPERATION)
    assert first['nodes']['picture']['status'] == 'submission_unknown'
    resumed = ResourcePlanStore(store.path, clock=lambda: clock[0])
    result = ResourcePlanWorkflow(resumed, {'fake': producer}).advance(OWNER, OPERATION)
    assert result['status'] == 'completed'
    assert len(producer.submissions) == len(producer.lookups) == 1
    assert result['usage']['attempts'] == 1


def test_claim_then_crash_without_submission_stays_unknown_not_retried(setup):
    store, clock = setup
    producer = Producer()
    store.create(OWNER, PROJECT, OPERATION, request())
    original = store.claim(OWNER, OPERATION, 'picture', producer.capabilities())
    clock[0] += 31
    workflow = ResourcePlanWorkflow(store, {'fake': producer})
    for _ in range(3):
        result = workflow.advance(OWNER, OPERATION)
    assert producer.submissions == []
    assert result['nodes']['picture']['task'] == original
    assert result['nodes']['picture']['status'] == 'submission_unknown'
    with pytest.raises(ServiceError, match='not_stopped'):
        store.retry(OWNER, OPERATION, 'picture', 'retry')


def test_cancel_before_claim_and_after_claim_never_starts_new_work(setup):
    store, _ = setup
    producer = Producer()
    store.create(OWNER, PROJECT, OPERATION, request([node('a'), node('b')]))
    task = store.claim(OWNER, OPERATION, 'a', producer.capabilities())
    result = store.cancel(OWNER, OPERATION)
    assert result['status'] == 'cancelling'
    result = ResourcePlanWorkflow(store, {'fake': producer}).advance(OWNER, OPERATION)
    assert result['status'] == 'cancelled' and result['stop_proven']
    assert producer.cancellations == [task['task_id']] and not producer.submissions
    assert result['nodes']['b']['status'] == 'cancelled'


def test_completed_result_after_cancel_is_not_adopted(setup):
    store, _ = setup
    producer = Producer()
    store.create(OWNER, PROJECT, OPERATION, request())
    task = store.claim(OWNER, OPERATION, 'picture', producer.capabilities())
    store.cancel(OWNER, OPERATION)
    store.record(OWNER, OPERATION, 'picture', task['task_id'], producer.receipt(task), validated=True)
    result = store.get(OWNER, OPERATION)
    assert result['nodes']['picture']['output'] is None
    assert result['status'] == 'cancelled'


def test_position_change_reuses_content_and_consumers_without_new_attempts(setup):
    store, _ = setup
    producer = Producer()
    store.create(OWNER, PROJECT, OPERATION, request([node('a'), node('b', dependencies=['a'])]))
    before = ResourcePlanWorkflow(store, {'fake': producer}).advance(OWNER, OPERATION)
    after = store.revise(OWNER, OPERATION, {'a': {'placement': {'start_frame': 120}}}, 'move', 'move later')
    assert after['nodes']['a']['output'] == before['nodes']['a']['output']
    assert after['nodes']['b']['output'] == before['nodes']['b']['output']
    assert after['nodes']['a']['task'] == before['nodes']['a']['task']
    assert after['usage'] == before['usage'] and after['status'] == 'completed'


def test_changed_content_invalidates_real_dependents_but_retains_history(setup):
    store, _ = setup
    producer = Producer()
    store.create(OWNER, PROJECT, OPERATION, request([
        node('a'), node('b', dependencies=['a']), node('unrelated')]))
    before = ResourcePlanWorkflow(store, {'fake': producer}).advance(OWNER, OPERATION)
    old_task = before['nodes']['a']['task']
    after = store.revise(OWNER, OPERATION, {'a': {'inputs': {'instruction': 'new'}}}, 'change', 'new drawing')
    assert after['nodes']['a']['output'] is None and after['nodes']['b']['output'] is None
    assert after['nodes']['unrelated']['output'] == before['nodes']['unrelated']['output']
    assert after['nodes']['a']['history'][0]['output'] == before['nodes']['a']['output']
    with pytest.raises(ServiceError, match='stale_result'):
        store.record(OWNER, OPERATION, 'a', old_task['task_id'], producer.receipt(old_task), validated=True)


def test_existing_recording_is_adopted_without_paid_attempts(setup):
    store, _ = setup
    store.create(OWNER, PROJECT, OPERATION, request([node('audio')]))
    existing = dict(content=dict(audio_version_id='original', sha256='a' * 64),
        local_timing=dict(duration_seconds=3.25), capabilities=[], key_effects=[])
    first = store.adopt(OWNER, OPERATION, 'audio', existing, 'adopt-original')
    replay = store.adopt(OWNER, OPERATION, 'audio', existing, 'adopt-original')
    assert first == replay and first['usage']['attempts'] == 0
    assert first['nodes']['audio']['output']['source_task_id'] is None


def test_budget_remains_spent_through_failure_retry_and_revisions(setup):
    store, _ = setup
    producer = Producer(status='failed')
    store.create(OWNER, PROJECT, OPERATION, request(attempts=2, per_node=2))
    workflow = ResourcePlanWorkflow(store, {'fake': producer})
    assert workflow.advance(OWNER, OPERATION)['status'] == 'blocked'
    store.retry(OWNER, OPERATION, 'picture', 'again')
    assert workflow.advance(OWNER, OPERATION)['usage']['attempts'] == 2
    with pytest.raises(ServiceError, match='budget_exhausted'):
        store.retry(OWNER, OPERATION, 'picture', 'third')
    changed = store.revise(OWNER, OPERATION, {'picture': {'inputs': {'instruction': 'alternate'}}}, 'change', 'try alternate')
    assert changed['usage']['attempts'] == 2
    result = workflow.advance(OWNER, OPERATION)
    assert result['nodes']['picture']['error'] == 'resource_plan_budget_exhausted'
    assert len(producer.submissions) == 2


def test_retry_unblocks_dependent_work_without_renewing_the_budget(setup):
    store, _ = setup
    producer = Producer(status='failed')
    store.create(OWNER, PROJECT, OPERATION, request([node('a'), node('b', dependencies=['a'])]))
    workflow = ResourcePlanWorkflow(store, {'fake': producer})
    failed = workflow.advance(OWNER, OPERATION)
    assert failed['nodes']['b']['status'] == 'blocked'
    producer.status = 'completed'
    retried = store.retry(OWNER, OPERATION, 'a', 'retry-source')
    assert retried['nodes']['b']['status'] == 'queued'
    result = workflow.advance(OWNER, OPERATION)
    assert result['status'] == 'completed' and result['usage']['attempts'] == 3


def test_placement_edit_cannot_invalidate_or_replace_inflight_source(setup):
    store, _ = setup
    producer = Producer(status='running')
    store.create(OWNER, PROJECT, OPERATION, request())
    before = ResourcePlanWorkflow(store, {'fake': producer}).advance(OWNER, OPERATION)
    moved = store.revise(OWNER, OPERATION, {'picture': {'placement': {'start_frame': 30}}}, 'move', 'later')
    assert moved['nodes']['picture']['task'] == before['nodes']['picture']['task']
    with pytest.raises(ServiceError, match='active_dependency'):
        store.revise(OWNER, OPERATION, {'picture': {'inputs': {'change': True}}}, 'edit', 'change')


def test_changed_mutation_replay_does_not_change_prior_resource_or_budget(setup):
    store, _ = setup
    store.create(OWNER, PROJECT, OPERATION, request())
    first = store.revise(OWNER, OPERATION, {'picture': {'placement': {'start_frame': 30}}}, 'move', 'later')
    assert first == store.revise(OWNER, OPERATION, {'picture': {'placement': {'start_frame': 30}}}, 'move', 'later')
    with pytest.raises(ServiceError, match='mutation_conflict'):
        store.revise(OWNER, OPERATION, {'picture': {'placement': {'start_frame': 40}}}, 'move', 'later')


def test_substitution_cannot_drop_explicit_capability_or_effect(setup):
    store, _ = setup
    original = node()
    original.update(required_capabilities=['manim'], key_effects=['preserve-trace'])
    store.create(OWNER, PROJECT, OPERATION, request([original]))
    for change in ({'required_capabilities': []}, {'key_effects': []}, {'objective': 'easier goal'}):
        with pytest.raises(ServiceError, match='substitution_forbidden'):
            store.revise(OWNER, OPERATION, {'picture': change}, 'erase-' + str(len(change)), 'make it easy')
    result = ResourcePlanWorkflow(store, {'fake': Producer()}).advance(OWNER, OPERATION)
    assert result['nodes']['picture']['error'] == 'resource_plan_substitution_forbidden'
    assert result['usage']['attempts'] == 0


def test_effect_metadata_is_not_technical_or_visual_acceptance(setup):
    store, _ = setup
    original = node()
    original['key_effects'] = ['follow-identity']
    store.create(OWNER, PROJECT, OPERATION, request([original]))
    result = ResourcePlanWorkflow(store, {'fake': Producer()}).advance(OWNER, OPERATION)
    assert result['status'] == 'completed'
    assert 'not_expression_acceptance' in result['nodes']['picture']['output']['validation_scope']


def test_media_validation_failure_is_stopped_failure_not_unknown_success(setup):
    store, _ = setup
    producer = Producer()
    producer.valid = False
    store.create(OWNER, PROJECT, OPERATION, request())
    result = ResourcePlanWorkflow(store, {'fake': producer}).advance(OWNER, OPERATION)
    assert result['status'] == 'blocked' and result['stop_proven']
    assert result['nodes']['picture']['status'] == 'failed'
    assert result['nodes']['picture']['output'] is None


def test_wrong_receipt_identity_cannot_be_adopted(setup):
    store, _ = setup
    producer = Producer()
    store.create(OWNER, PROJECT, OPERATION, request())
    task = store.claim(OWNER, OPERATION, 'picture', producer.capabilities())
    receipt = producer.receipt(task)
    receipt['request_sha256'] = 'wrong'
    with pytest.raises(ServiceError, match='binding_mismatch'):
        store.record(OWNER, OPERATION, 'picture', task['task_id'], receipt, validated=True)
    assert store.get(OWNER, OPERATION)['nodes']['picture']['output'] is None


def test_atomic_claim_reserves_only_one_attempt_between_coordinators(setup):
    store, _ = setup
    producer = Producer()
    store.create(OWNER, PROJECT, OPERATION, request())
    barrier = threading.Barrier(2)
    def claim():
        barrier.wait()
        return store.claim(OWNER, OPERATION, 'picture', producer.capabilities())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    assert sum(value is not None for value in results) == 1
    assert store.get(OWNER, OPERATION)['usage']['attempts'] == 1


def test_expiration_cancels_original_without_changing_user_intent(setup):
    store, clock = setup
    producer = Producer(status='running')
    store.create(OWNER, PROJECT, OPERATION, request())
    workflow = ResourcePlanWorkflow(store, {'fake': producer})
    workflow.advance(OWNER, OPERATION)
    clock[0] = 2001
    result = workflow.advance(OWNER, OPERATION)
    assert result['status'] == 'timed_out' and result['stop_proven']
    assert result['cancel_requested'] is False and len(producer.cancellations) == 1


def test_completion_is_idempotent_and_later_cancel_does_not_rewrite_it(setup):
    store, _ = setup
    producer = Producer()
    store.create(OWNER, PROJECT, OPERATION, request())
    first = ResourcePlanWorkflow(store, {'fake': producer}).advance(OWNER, OPERATION)
    task = first['nodes']['picture']['task']
    store.record(OWNER, OPERATION, 'picture', task['task_id'], producer.receipt(task), validated=True)
    assert store.cancel(OWNER, OPERATION) == first
    assert len(producer.submissions) == 1


def test_cancel_failure_retains_original_unproven_attempt(setup):
    store, _ = setup
    producer = Producer(status='running')
    store.create(OWNER, PROJECT, OPERATION, request())
    workflow = ResourcePlanWorkflow(store, {'fake': producer})
    running = workflow.advance(OWNER, OPERATION)
    store.cancel(OWNER, OPERATION)
    producer.cancel = lambda task: (_ for _ in ()).throw(TimeoutError('unavailable'))
    after = workflow.advance(OWNER, OPERATION)
    assert after['status'] == 'cancelling' and not after['stop_proven']
    assert after['nodes']['picture']['task'] == running['nodes']['picture']['task']
    assert after['usage']['attempts'] == 1


def test_producer_revision_is_part_of_immutable_output_identity(setup):
    store, _ = setup
    producer = Producer()
    store.create(OWNER, PROJECT, OPERATION, request())
    first = ResourcePlanWorkflow(store, {'fake': producer}).advance(OWNER, OPERATION)
    assert first['nodes']['picture']['output']['producer_revision'] == 'r1'
    assert first['nodes']['picture']['output']['source_task_id'] == first['nodes']['picture']['task']['task_id']
