"""Formal-film allowances survive source changes, races and uncertain work."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import sqlite3
import threading

import pytest

from agent_service.contracts import ServiceError
from video_workbench.production_sequence_store import ProductionSequenceStore


OWNER, PROJECT = 'alice', 'project-1'
SOURCE = dict(run_id='original-run', version_id='original-source', sha256='a' * 64)
INPUT, RESULT, PROOF = 'b' * 64, 'c' * 64, 'd' * 64
ITEMS = [dict(scope=dict(kind='time_range', start_seconds=1.5, end_seconds=3),
    problem='两个对象重叠，身份不清晰。', expected='保留对象身份，改成错开路径。')]
BUDGET = dict(max_agent_tasks=3, max_renders=2)


@pytest.fixture
def work(tmp_path):
    store = ProductionSequenceStore(tmp_path / 'sequence.sqlite3')
    sequence = store.create(OWNER, PROJECT, SOURCE, 'create-1')
    return store, sequence['id']


def error(code):
    return pytest.raises(ServiceError, match='^' + code + '$')


def reserve(store, sequence_id, kind='automatic', *, key='automatic-1', source=None,
            instruction='请再打磨一次整片。', request_id=None, expected_revision=None):
    source = source or SOURCE
    if expected_revision is None:
        expected_revision = store.get(OWNER, PROJECT, sequence_id)['revision']
    request = None if kind == 'automatic' else dict(request_id=request_id or key,
        viewed_version_id=source['version_id'], instruction=instruction)
    return store.reserve_round(OWNER, PROJECT, sequence_id, kind, source['version_id'], INPUT, key,
        expected_revision=expected_revision, requested_by_user=request)


def cancel_reserved(store, operation, status='cancelled'):
    return store.finish(OWNER, PROJECT, operation['id'], status, RESULT,
        expected_revision=operation['revision'])


def complete(store, operation):
    operation = store.claim_submission(OWNER, PROJECT, operation['id'], expected_revision=operation['revision'])
    assert operation['submission_claimed'] is True
    operation = store.submitted(OWNER, PROJECT, operation['id'], 'coordinator:' + operation['id'],
        expected_revision=operation['revision'])
    return store.finish(OWNER, PROJECT, operation['id'], 'completed', RESULT,
        stop_proof_sha256=PROOF, expected_revision=operation['revision'])


def bind(store, sequence_id, source, key='bind-1', expected_revision=None):
    return store.bind_source(OWNER, PROJECT, sequence_id, source, key,
        expected_revision=store.get(OWNER, PROJECT, sequence_id)['revision']
            if expected_revision is None else expected_revision)


def requirements(store, sequence_id, key='requirements-1', *, items=None, budget=None, source=None,
                 expected_revision=None):
    return store.reserve_requirements(OWNER, PROJECT, sequence_id,
        (source or SOURCE)['version_id'], ITEMS if items is None else items, BUDGET if budget is None else budget, key,
        expected_revision=store.get(OWNER, PROJECT, sequence_id)['revision']
            if expected_revision is None else expected_revision)


def test_origin_run_is_stable_identity_while_identical_hashes_are_not_identity(work):
    store, sequence_id = work
    original = store.get(OWNER, PROJECT, sequence_id)
    assert original['limits'] == dict(automatic=1, user_requested=2)
    assert store.create(OWNER, PROJECT, SOURCE, 'create-alias') == original
    different = dict(SOURCE, run_id='independent-origin', version_id='independent-source')
    assert store.create(OWNER, PROJECT, different, 'create-independent')['id'] != sequence_id
    other_owner = store.create('bob', PROJECT, SOURCE, 'create-1')
    assert other_owner['id'] != sequence_id
    assert len(store.events(OWNER, PROJECT, sequence_id)) == 1
    with error('production_sequence_origin_conflict'):
        store.create(OWNER, PROJECT, dict(SOURCE, sha256='e' * 64), 'changed-origin')
    with error('production_sequence_origin_conflict'):
        store.create(OWNER, 'different-project', SOURCE, 'moved-origin')


def test_source_for_run_reads_origin_and_explicitly_bound_derivative(work):
    store, sequence_id = work
    derived = dict(run_id='derived-run', version_id='derived-source', sha256=SOURCE['sha256'])
    bind(store, sequence_id, derived)
    sequence = store.get(OWNER, PROJECT, sequence_id)
    for source in (SOURCE, derived):
        assert store.source_for_run(OWNER, PROJECT, source['run_id']) == dict(sequence=sequence, source=source)
        assert store.source_for_run(OWNER, PROJECT, source['run_id'], source['version_id']) == dict(
            sequence=sequence, source=source)
    assert store.source_for_run(OWNER, PROJECT, 'unbound-run') is None
    assert store.source_for_run(OWNER, PROJECT, 'unbound-run', SOURCE['version_id']) is None


def test_source_for_run_keeps_same_run_and_version_ids_isolated_by_owner(work):
    store, sequence_id = work
    other_sequence = store.create('bob', PROJECT, SOURCE, 'create-1')
    assert store.source_for_run('bob', PROJECT, SOURCE['run_id']) == dict(
        sequence=other_sequence, source=SOURCE)
    assert store.source_for_run(OWNER, PROJECT, SOURCE['run_id'])['sequence']['id'] == sequence_id
    assert store.source_for_run('carol', PROJECT, SOURCE['run_id']) is None
    assert store.source_for_run('carol', PROJECT, SOURCE['run_id'], SOURCE['version_id']) is None


@pytest.mark.parametrize('version_id', [None, SOURCE['version_id'], 'unbound-version'])
def test_source_for_run_rejects_owned_run_from_another_project(work, version_id):
    store, _ = work
    with error('production_sequence_not_found') as exc:
        store.source_for_run(OWNER, 'different-project', SOURCE['run_id'], version_id)
    assert exc.value.status == 404


def test_source_for_run_requires_exact_version_when_one_run_has_multiple_sources(work):
    store, sequence_id = work
    second = dict(SOURCE, version_id='second-source', sha256='e' * 64)
    derived = dict(run_id='derived-run', version_id='derived-source', sha256=SOURCE['sha256'])
    bind(store, sequence_id, second)
    bind(store, sequence_id, derived, key='bind-derived')
    with error('production_sequence_source_ambiguous') as exc:
        store.source_for_run(OWNER, PROJECT, SOURCE['run_id'])
    assert exc.value.status == 409
    sequence = store.get(OWNER, PROJECT, sequence_id)
    for source in (SOURCE, second):
        assert store.source_for_run(OWNER, PROJECT, SOURCE['run_id'], source['version_id']) == dict(
            sequence=sequence, source=source)
    for version_id in ('unbound-version', derived['version_id']):
        with error('production_sequence_source_not_found') as exc:
            store.source_for_run(OWNER, PROJECT, SOURCE['run_id'], version_id)
        assert exc.value.status == 404
    with error('production_sequence_source_not_found'):
        store.source_for_run(OWNER, PROJECT, derived['run_id'], SOURCE['version_id'])


def test_source_for_run_uses_read_only_select_without_changing_ledger(work, monkeypatch):
    store, sequence_id = work
    reserve(store, sequence_id)
    sequence = store.get(OWNER, PROJECT, sequence_id)
    operations = store.operations(OWNER, PROJECT, sequence_id)
    events = store.events(OWNER, PROJECT, sequence_id)
    connect = sqlite3.connect
    with connect(store.path) as db:
        before = list(db.iterdump())
    calls, statements = [], []

    def read_connection(path, *args, **kwargs):
        calls.append((path, kwargs))
        db = connect(path, *args, **kwargs)
        db.set_trace_callback(statements.append)
        return db

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', read_connection)
        found = store.source_for_run(OWNER, PROJECT, SOURCE['run_id'])
        assert store.source_for_run(OWNER, PROJECT, 'unbound-run') is None
    assert found == dict(sequence=sequence, source=SOURCE)
    assert len(calls) == 2 and all(path.endswith('?mode=ro') and options['uri'] is True
        for path, options in calls)
    assert len(statements) == 2 and all(statement.startswith('SELECT ') for statement in statements)
    found['sequence']['limits']['automatic'] = 100
    found['source']['sha256'] = 'e' * 64
    assert store.get(OWNER, PROJECT, sequence_id) == sequence
    assert store.operations(OWNER, PROJECT, sequence_id) == operations
    assert store.events(OWNER, PROJECT, sequence_id) == events
    with connect(store.path) as db:
        assert list(db.iterdump()) == before


def test_mutating_returned_documents_does_not_change_the_ledger(work):
    store, sequence_id = work
    original = store.get(OWNER, PROJECT, sequence_id)
    original['origin_source']['sha256'] = 'e' * 64
    original['limits']['automatic'] = 100
    operation = reserve(store, sequence_id)
    operation['source']['sha256'] = 'e' * 64
    assert store.get(OWNER, PROJECT, sequence_id)['limits']['automatic'] == 1
    assert store.operation(OWNER, PROJECT, operation['id'])['source'] == SOURCE


def test_exact_creation_idempotency_is_scoped_by_owner_and_api_action(work):
    store, sequence_id = work
    with error('production_sequence_idempotency_conflict'):
        store.create(OWNER, PROJECT, dict(SOURCE, run_id='other'), 'create-1')
    with error('production_sequence_idempotency_conflict'):
        store.create(OWNER, 'other', SOURCE, 'create-1')
    with error('production_sequence_idempotency_conflict'):
        reserve(store, sequence_id, key='create-1')
    assert store.get(OWNER, PROJECT, sequence_id)['revision'] == 1


@pytest.mark.parametrize('read', ['get', 'events', 'operations', 'operation'])
@pytest.mark.parametrize('owner,project', [('bob', PROJECT), (OWNER, 'different-project')])
def test_another_owner_or_project_cannot_read_ledger_objects(work, read, owner, project):
    store, sequence_id = work
    operation = reserve(store, sequence_id)
    target = operation['id'] if read == 'operation' else sequence_id
    with pytest.raises(ServiceError) as exc:
        getattr(store, read)(owner, project, target)
    assert exc.value.status == 404


def test_bind_source_persists_lineage_and_prevents_bound_run_becoming_a_new_origin(work):
    store, sequence_id = work
    initial = complete(store, reserve(store, sequence_id))
    source = dict(run_id='derived-run', version_id='revised-source', sha256='e' * 64)
    rev = store.get(OWNER, PROJECT, sequence_id)['revision']
    assert bind(store, sequence_id, source, expected_revision=rev) == source
    restarted = ProductionSequenceStore(store.path)
    assert bind(restarted, sequence_id, source, expected_revision=rev) == source
    assert restarted.get(OWNER, PROJECT, sequence_id)['id'] == sequence_id
    with error('production_sequence_run_conflict'):
        restarted.create(OWNER, PROJECT, source, 'derivative-cannot-renew')
    with error('production_sequence_allowance_consumed'):
        reserve(restarted, sequence_id, source=source, key='automatic-after-new-source')
    user = reserve(restarted, sequence_id, 'user_requested', source=source, key='user-1')
    assert user['round_index'] == 1 and user['source'] == source
    assert restarted.operation(OWNER, PROJECT, initial['id'])['source'] == SOURCE


@pytest.mark.parametrize('change', ['sha', 'run', 'project', 'sequence'])
def test_registered_source_version_cannot_change_content_or_ownership_binding(work, change):
    store, sequence_id = work
    source = dict(SOURCE)
    project = PROJECT
    if change == 'sha':
        source['sha256'] = 'e' * 64
    elif change == 'run':
        source['run_id'] = 'new-run'
    elif change == 'project':
        project = 'other-project'
        other = store.create(OWNER, project,
            dict(run_id='other-origin', version_id='other-source', sha256='e' * 64), 'other-create')
        sequence_id = other['id']
    else:
        other = store.create(OWNER, project,
            dict(run_id='other-origin', version_id='other-source', sha256='e' * 64), 'other-create')
        sequence_id = other['id']
    with error('production_sequence_source_conflict'):
        store.bind_source(OWNER, project, sequence_id, source, 'bind-conflict',
            expected_revision=store.get(OWNER, project, sequence_id)['revision'])


def test_one_plus_two_is_consumed_by_reservation_even_after_failure_cancel_and_restart(work):
    store, sequence_id = work
    auto = reserve(store, sequence_id)
    cancel_reserved(store, auto, 'failed')
    with error('production_sequence_allowance_consumed'):
        reserve(store, sequence_id, key='automatic-again')
    first = reserve(store, sequence_id, 'user_requested', key='user-1')
    cancel_reserved(store, first)
    store = ProductionSequenceStore(store.path)
    second = reserve(store, sequence_id, 'user_requested', key='user-2')
    complete(store, second)
    with error('production_sequence_allowance_consumed'):
        reserve(store, sequence_id, 'user_requested', key='user-3')
    assert [(op['kind'], op['round_index']) for op in store.operations(OWNER, PROJECT, sequence_id)] == [
        ('automatic', 0), ('user_requested', 1), ('user_requested', 2)]


def test_user_round_requires_explicit_request_and_exact_viewed_source(work):
    store, sequence_id = work
    with error('production_sequence_initial_round_required'):
        reserve(store, sequence_id, 'user_requested', key='too-early')
    with error('invalid_production_sequence_request'):
        store.reserve_round(OWNER, PROJECT, sequence_id, 'user_requested', SOURCE['version_id'], INPUT,
            'not-user-requested', expected_revision=1)
    with error('production_sequence_viewed_version_mismatch'):
        store.reserve_round(OWNER, PROJECT, sequence_id, 'user_requested', SOURCE['version_id'], INPUT,
            'wrong-watched-version', expected_revision=1, requested_by_user=dict(request_id='r',
                viewed_version_id='different-source', instruction='再优化'))
    assert store.operations(OWNER, PROJECT, sequence_id) == []


def test_one_user_request_cannot_consume_two_rounds_with_different_keys(work):
    store, sequence_id = work
    complete(store, reserve(store, sequence_id))
    complete(store, reserve(store, sequence_id, 'user_requested', key='user-1', request_id='human-request-1'))
    with error('production_sequence_user_request_conflict'):
        reserve(store, sequence_id, 'user_requested', key='alias-key', request_id='human-request-1')
    assert len(store.operations(OWNER, PROJECT, sequence_id)) == 2


def test_unknown_submission_retains_original_identity_and_blocks_new_work(work):
    store, sequence_id = work
    operation = reserve(store, sequence_id, expected_revision=1)
    claimed = store.claim_submission(OWNER, PROJECT, operation['id'], expected_revision=1)
    store.submission_unknown(OWNER, PROJECT, operation['id'], 'response lost', expected_revision=claimed['revision'])
    restarted = ProductionSequenceStore(store.path)
    original = reserve(restarted, sequence_id, expected_revision=1)
    assert original['id'] == operation['id'] and original['status'] == 'submission_unknown'
    assert restarted.claim_submission(OWNER, PROJECT, original['id'],
        expected_revision=original['revision'])['submission_claimed'] is False
    with error('production_sequence_stop_proof_required'):
        restarted.finish(OWNER, PROJECT, original['id'], 'cancelled', RESULT,
            expected_revision=original['revision'])
    with error('production_sequence_work_in_progress'):
        requirements(restarted, sequence_id)
    recovered = restarted.submitted(OWNER, PROJECT, original['id'], 'original-coordinator',
        expected_revision=original['revision'])
    with error('production_sequence_execution_conflict'):
        restarted.submitted(OWNER, PROJECT, original['id'], 'replacement-coordinator',
            expected_revision=recovered['revision'])
    result = restarted.finish(OWNER, PROJECT, original['id'], 'failed', RESULT,
        stop_proof_sha256=PROOF, expected_revision=recovered['revision'])
    assert result['execution_ref'] == 'original-coordinator'
    assert reserve(restarted, sequence_id, expected_revision=1)['id'] == original['id']
    history = restarted.events(OWNER, PROJECT, sequence_id)
    assert next(row for row in history if row['event'] == 'submission_unknown')['error'] == 'response lost'
    assert history[-1]['execution_ref'] == 'original-coordinator'
    assert history[-1]['stop_proof_sha256'] == PROOF
    with error('production_sequence_allowance_consumed'):
        reserve(restarted, sequence_id, key='replacement-auto')


def test_completed_round_requires_submitted_work_and_preserves_terminal_evidence(work):
    store, sequence_id = work
    operation = reserve(store, sequence_id)
    with error('production_sequence_transition_conflict'):
        store.finish(OWNER, PROJECT, operation['id'], 'completed', RESULT,
            expected_revision=operation['revision'], stop_proof_sha256=PROOF)
    final = complete(store, operation)
    before = store.events(OWNER, PROJECT, sequence_id)
    assert store.finish(OWNER, PROJECT, operation['id'], 'completed', RESULT,
        expected_revision=final['revision'], stop_proof_sha256=PROOF) == final
    with error('production_sequence_result_conflict'):
        store.finish(OWNER, PROJECT, operation['id'], 'failed', RESULT,
            expected_revision=final['revision'], stop_proof_sha256=PROOF)
    assert store.events(OWNER, PROJECT, sequence_id) == before


def test_round_idempotency_freezes_all_input_including_original_cas(work):
    store, sequence_id = work
    original = reserve(store, sequence_id, expected_revision=1)
    assert reserve(store, sequence_id, expected_revision=1) == original
    with error('production_sequence_idempotency_conflict'):
        reserve(store, sequence_id, expected_revision=2)
    with error('production_sequence_idempotency_conflict'):
        store.reserve_round(OWNER, PROJECT, sequence_id, 'automatic', SOURCE['version_id'], 'e' * 64,
            'automatic-1', expected_revision=1)


def test_requirements_freeze_specific_scope_budget_and_do_not_reopen_one_plus_two(work):
    store, sequence_id = work
    cancel_reserved(store, reserve(store, sequence_id), 'failed')
    cancel_reserved(store, reserve(store, sequence_id, 'user_requested', key='user-1'))
    cancel_reserved(store, reserve(store, sequence_id, 'user_requested', key='user-2'))
    rev = store.get(OWNER, PROJECT, sequence_id)['revision']
    batch = requirements(store, sequence_id, expected_revision=rev)
    assert batch['kind'] == 'requirements' and batch['source'] == SOURCE
    assert batch['items'] == ITEMS and batch['budget'] == BUDGET
    batch['items'][0]['problem'] = 'changed after return'
    assert requirements(store, sequence_id, expected_revision=rev)['items'] == ITEMS
    with error('production_sequence_idempotency_conflict'):
        requirements(store, sequence_id, expected_revision=rev, budget=dict(BUDGET, max_renders=3))
    batch = complete(store, store.operation(OWNER, PROJECT, batch['id']))
    new = requirements(store, sequence_id, key='requirements-2')
    cancel_reserved(store, new)
    with error('production_sequence_allowance_consumed'):
        reserve(store, sequence_id, key='auto-after-explicit-work')
    with error('production_sequence_allowance_consumed'):
        reserve(store, sequence_id, 'user_requested', key='user-after-explicit-work')
    assert len(store.operations(OWNER, PROJECT, sequence_id)) == 5
    assert store.get(OWNER, PROJECT, sequence_id)['origin_source'] == SOURCE


@pytest.mark.parametrize('change', ['no_items', 'no_problem', 'no_expected', 'unbounded_scope',
    'reverse_range', 'nan_range', 'extra_scope_field', 'unbounded_budget', 'boolean_budget',
    'unknown_budget', 'blank_problem'])
def test_requirement_batches_need_concrete_issues_ranges_and_finite_budget(work, change):
    store, sequence_id = work
    items, budget = deepcopy(ITEMS), dict(BUDGET)
    if change == 'no_items':
        items = []
    elif change == 'no_problem':
        del items[0]['problem']
    elif change == 'no_expected':
        del items[0]['expected']
    elif change == 'unbounded_scope':
        items[0]['scope'] = dict(kind='whatever')
    elif change == 'reverse_range':
        items[0]['scope']['end_seconds'] = 0
    elif change == 'nan_range':
        items[0]['scope']['start_seconds'] = float('nan')
    elif change == 'extra_scope_field':
        items[0]['scope']['anything'] = True
    elif change == 'unbounded_budget':
        budget['max_agent_tasks'] = 0
    elif change == 'boolean_budget':
        budget['max_renders'] = True
    elif change == 'unknown_budget':
        budget['unlimited'] = True
    else:
        items[0]['problem'] = ' \n '
    before = store.events(OWNER, PROJECT, sequence_id)
    with error('invalid_production_sequence_request'):
        requirements(store, sequence_id, items=items, budget=budget)
    assert store.events(OWNER, PROJECT, sequence_id) == before
    assert store.get(OWNER, PROJECT, sequence_id)['revision'] == 1


def test_multiline_feedback_and_whole_film_requirement_are_supported(work):
    store, sequence_id = work
    complete(store, reserve(store, sequence_id))
    user = reserve(store, sequence_id, 'user_requested', key='user-1', instruction='第一段请加速。\n第二段请保留。')
    complete(store, user)
    items = [dict(scope=dict(kind='whole_film'), problem='画面与讲解不同步。\n请重点看后半段。', expected='保持原声，修正动作。')]
    assert requirements(store, sequence_id, items=items)['items'] == items


def test_stale_cas_and_unknown_sources_do_not_consume_allowances(work):
    store, sequence_id = work
    bind(store, sequence_id, dict(run_id='next-run', version_id='next-source', sha256='e' * 64))
    before = store.events(OWNER, PROJECT, sequence_id)
    with error('production_sequence_revision_conflict'):
        reserve(store, sequence_id, expected_revision=1)
    with error('production_sequence_source_not_found'):
        reserve(store, sequence_id, source=dict(SOURCE, version_id='unbound'))
    assert store.operations(OWNER, PROJECT, sequence_id) == []
    assert store.events(OWNER, PROJECT, sequence_id) == before
    assert reserve(store, sequence_id)['round_index'] == 0


def test_source_belongs_to_same_sequence_before_round_or_requirement_admission(work):
    store, sequence_id = work
    other_source = dict(run_id='different-run', version_id='different-source', sha256='e' * 64)
    other = store.create(OWNER, PROJECT, other_source, 'different-origin')
    with error('production_sequence_source_not_found'):
        reserve(store, sequence_id, source=other_source)
    with error('production_sequence_source_not_found'):
        requirements(store, sequence_id, source=other_source)
    with error('production_sequence_run_conflict'):
        bind(store, sequence_id, dict(other_source, version_id='additional-version'))
    assert store.get(OWNER, PROJECT, other['id'])['revision'] == 1


@pytest.mark.parametrize('owner,project', [('bob', PROJECT), (OWNER, 'different-project')])
def test_another_owner_or_project_cannot_mutate_or_reserve_work(work, owner, project):
    store, sequence_id = work
    operation = reserve(store, sequence_id)
    before = store.events(OWNER, PROJECT, sequence_id)
    actions = [
        lambda: store.bind_source(owner, project, sequence_id,
            dict(run_id='derived', version_id='derived', sha256='e' * 64), 'unauthorized-source', expected_revision=2),
        lambda: store.reserve_round(owner, project, sequence_id, 'automatic', SOURCE['version_id'], INPUT,
            'unauthorized-round', expected_revision=2),
        lambda: store.reserve_requirements(owner, project, sequence_id, SOURCE['version_id'], ITEMS, BUDGET,
            'unauthorized-requirements', expected_revision=2),
        lambda: store.claim_submission(owner, project, operation['id'], expected_revision=1),
        lambda: store.finish(owner, project, operation['id'], 'cancelled', RESULT, expected_revision=1),
    ]
    for action in actions:
        with pytest.raises(ServiceError) as exc:
            action()
        assert exc.value.status == 404
    assert store.events(OWNER, PROJECT, sequence_id) == before
    assert store.operation(OWNER, PROJECT, operation['id'])['status'] == 'reserved'


def race(count, action):
    barrier = threading.Barrier(count)

    def call(index):
        barrier.wait(timeout=10)
        try:
            return action(index)
        except ServiceError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=count) as executor:
        return list(executor.map(call, range(count)))


def test_independent_connections_concurrently_create_only_one_origin_sequence(work):
    store, _ = work
    stores = [ProductionSequenceStore(store.path) for _ in range(4)]
    source = dict(run_id='racing-origin', version_id='racing-source', sha256='e' * 64)
    results = race(4, lambda i: stores[i].create(OWNER, PROJECT, source, f'create-race-{i}'))
    assert len({result['id'] for result in results}) == 1
    assert len(store.events(OWNER, PROJECT, results[0]['id'])) == 1


def test_independent_connections_cannot_double_reserve_the_automatic_round(work):
    store, sequence_id = work
    stores = [ProductionSequenceStore(store.path) for _ in range(4)]
    results = race(4, lambda i: reserve(stores[i], sequence_id, key=f'auto-race-{i}', expected_revision=1))
    assert sum(type(result) is dict for result in results) == 1
    assert results.count('production_sequence_revision_conflict') == 3
    assert len(store.operations(OWNER, PROJECT, sequence_id)) == 1


def test_concurrent_same_idempotency_recovers_original_reservation(work):
    store, sequence_id = work
    stores = [ProductionSequenceStore(store.path) for _ in range(4)]
    results = race(4, lambda i: reserve(stores[i], sequence_id, expected_revision=1))
    assert len({result['id'] for result in results}) == 1
    assert len(store.operations(OWNER, PROJECT, sequence_id)) == 1


def test_submission_cas_allows_exactly_one_external_admission(work):
    store, sequence_id = work
    operation = reserve(store, sequence_id)
    stores = [ProductionSequenceStore(store.path) for _ in range(4)]
    results = race(4, lambda i: stores[i].claim_submission(OWNER, PROJECT, operation['id'], expected_revision=1))
    claimed = [result for result in results if type(result) is dict]
    assert len(claimed) == 1 and claimed[0]['submission_claimed'] is True
    assert results.count('production_sequence_revision_conflict') == 3
    restarted = ProductionSequenceStore(store.path)
    assert restarted.claim_submission(OWNER, PROJECT, operation['id'],
        expected_revision=claimed[0]['revision'])['submission_claimed'] is False


def test_round_and_requirement_admissions_cannot_race_into_simultaneous_work(work):
    store, sequence_id = work
    stores = [ProductionSequenceStore(store.path) for _ in range(2)]
    results = race(2, lambda i: reserve(stores[i], sequence_id, expected_revision=1) if i == 0
        else requirements(stores[i], sequence_id, expected_revision=1))
    assert sum(type(result) is dict for result in results) == 1
    assert results.count('production_sequence_revision_conflict') == 1
    assert len(store.operations(OWNER, PROJECT, sequence_id)) == 1


def test_concurrent_requirement_replays_freeze_one_batch_and_budget(work):
    store, sequence_id = work
    stores = [ProductionSequenceStore(store.path) for _ in range(4)]
    results = race(4, lambda i: requirements(stores[i], sequence_id, expected_revision=1))
    assert len({result['id'] for result in results}) == 1
    assert all(result['budget'] == BUDGET for result in results)
    assert len(store.operations(OWNER, PROJECT, sequence_id)) == 1


def test_same_source_binding_cas_replays_only_original_input(work):
    store, sequence_id = work
    source = dict(run_id='derived', version_id='new-source', sha256='e' * 64)
    bind(store, sequence_id, source, expected_revision=1)
    assert bind(store, sequence_id, source, expected_revision=1) == source
    with error('production_sequence_idempotency_conflict'):
        bind(store, sequence_id, source, expected_revision=2)
    assert store.get(OWNER, PROJECT, sequence_id)['revision'] == 2


def test_failed_creation_with_an_existing_source_rolls_back_new_sequence_and_origin(work):
    store, sequence_id = work
    attempted = dict(SOURCE, run_id='new-origin-with-existing-version')
    with error('production_sequence_source_conflict'):
        store.create(OWNER, PROJECT, attempted, 'failed-create')
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT count(*) FROM production_sequences').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM production_sequence_runs').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM production_sequence_requests').fetchone()[0] == 1
    assert len(store.events(OWNER, PROJECT, sequence_id)) == 1


def test_business_database_is_rejected_without_modifying_existing_tables(tmp_path):
    path = tmp_path / 'business.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
    with error('production_sequence_database_not_isolated'):
        ProductionSequenceStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [('projects',)]
