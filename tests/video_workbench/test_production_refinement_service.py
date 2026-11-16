"""Formal rounds execute once, preserve lineage and stop at explicit budgets."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.production_refinement_service import ProductionRefinementService
from video_workbench.production_refinement_driver import ProductionRefinementDriver
from video_workbench.production_sequence_store import ProductionSequenceStore


OWNER, PROJECT = 'alice', 'project-1'
SOURCE = dict(run_id='run-1', version_id='render-1', sha256='a' * 64)
ISSUE = dict(start_seconds=3, end_seconds=4, problem='The labels overlap.', change_target='Separate labels.')
ITEMS = [dict(scope=dict(kind='time_range', start_seconds=3, end_seconds=4),
    problem='The labels overlap.', expected='Keep each label legible.')]


class Driver:
    """Explicit synthetic peer; it does not execute or evaluate animation."""
    def __init__(self):
        self.calls, self.children, self.issues = [], {}, [ISSUE]
        self.unknown_once, self.fail_once = False, None
        self.preparation_failure = None
        self.next_output_source = None

    def authorize(self, owner, project):
        if (owner, project) != (OWNER, PROJECT):
            raise ServiceError('not_found', 404)

    def freeze(self, owner, project, source):
        return dict(owner=owner, project_id=project, source=deepcopy(source),
            module_version_ids=['m0', 'm1', 'm2'],
            film=dict(scenes=[dict(start_seconds=i * 3, end_seconds=(i + 1) * 3) for i in range(3)]))

    validate_request = staticmethod(ProductionRefinementDriver.validate_request)

    def prepare(self, stage, key, context, state):
        if self.preparation_failure:
            raise ServiceError(self.preparation_failure, 422)
        index = next((row['index'] for row in state['children'] if row['key'] == key), None)
        fingerprint = digest(dict(stage=stage, index=index, context=context,
            preceding=state['module_version_ids'][:index] if index is not None else state['module_version_ids']))
        def submit():
            self.calls.append((stage, key, deepcopy(state['module_version_ids'])))
            output = (dict(issues=deepcopy(self.issues), summary='Synthetic review.',
                limitations=['Sampled frames only; no audio.'], evidence_scope='sampled_frames') if stage == 'review'
                else dict(version_id='revised-' + str(index)) if stage == 'module'
                else dict(source=self.next_output_source or dict(SOURCE, version_id='render-' + state['id'], sha256='b' * 64)))
            self.children[key] = dict(id=key, state='completed', stop_proven=True, output=output, error=None)
            if self.fail_once == stage:
                self.fail_once = None
                self.children[key].update(state='failed', error='synthetic_failure')
            if self.unknown_once:
                self.unknown_once = False
                raise OSError('The accepted response was lost.')
        return fingerprint, submit

    def lookup(self, stage, key, context, state):
        return deepcopy(self.children.get(key))

    def cancel(self, stage, key, context, state):
        if key in self.children:
            self.children[key].update(state='cancelled', stop_proven=True)


@pytest.fixture
def work(tmp_path):
    sequences = ProductionSequenceStore(tmp_path / 'sequences.sqlite3')
    sequence = sequences.create(OWNER, PROJECT, SOURCE, 'origin')
    driver = Driver()
    service = ProductionRefinementService(tmp_path / 'refinements.sqlite3', sequences, driver)
    return service, sequences, driver, sequence['id']


def start(service, kind='automatic', key='automatic-1', source=None, **kw):
    return service.start(OWNER, PROJECT, source or SOURCE, kind=kind, request_id=key,
        **(dict(instruction='Improve the whole film.') if kind == 'user_requested' else {}), **kw)


def finish(service, operation):
    for _ in range(50):
        service.tick()
        current = service.get(OWNER, operation['id'])
        if current['status'] in {'completed', 'failed', 'cancelled'}:
            return current
    raise AssertionError('did not reach a terminal result')


def test_actual_sequence_budget_drives_one_review_suffix_rewrite_and_render(work):
    service, sequences, driver, sequence_id = work
    operation = start(service)
    result = finish(service, operation)
    assert result['status'] == 'completed'
    assert [stage for stage, _, _ in driver.calls] == ['review', 'module', 'module', 'render']
    assert result['result']['module_version_ids'] == ['m0', 'revised-1', 'revised-2']
    assert driver.calls[2][2] == ['m0', 'revised-1', 'm2']
    assert result['result']['aesthetic_status'] == 'not_accepted'
    assert result['result']['post_revision_agent_review_performed'] is False
    assert sequences.source_for_run(OWNER, PROJECT, SOURCE['run_id'],
        result['result']['source']['version_id'])['sequence']['id'] == sequence_id
    assert sequences.operations(OWNER, PROJECT, sequence_id)[0]['status'] == 'completed'
    for _ in range(5):
        service.tick()
    assert len(driver.calls) == 4


def test_no_issues_is_not_permission_to_invent_changes_or_acceptance(work):
    service, sequences, driver, _ = work
    driver.issues = []
    result = finish(service, start(service))
    assert result['status'] == 'completed'
    assert [stage for stage, _, _ in driver.calls] == ['review']
    assert result['result']['source'] == SOURCE
    assert result['result']['revision_performed'] is False
    assert result['review']['limitations'] == ['Sampled frames only; no audio.']
    assert result['aesthetic_status'] == 'not_accepted'


def test_auto_then_two_user_rounds_then_explicit_requirements(work):
    service, sequences, driver, sequence_id = work
    result = finish(service, start(service))
    for number in (1, 2):
        result = finish(service, start(service, 'user_requested', 'user-' + str(number),
            source=result['result']['source']))
        assert result['status'] == 'completed'
    with pytest.raises(ServiceError, match='production_sequence_allowance_consumed'):
        start(service, 'user_requested', 'user-3', source=result['result']['source'])
    service.tick()  # Rejected outbox becomes a retained failed request.
    # Explicit requirements remain available, on the SAME sequence and budget.
    explicit = start(service, 'requirements', 'requirements-1', source=result['result']['source'],
        items=ITEMS, budget=dict(max_agent_tasks=3, max_renders=1))
    explicit = finish(service, explicit)
    assert explicit['status'] == 'completed'
    operations = sequences.operations(OWNER, PROJECT, sequence_id)
    assert [row['kind'] for row in operations] == ['automatic', 'user_requested', 'user_requested', 'requirements']
    assert all(row['sequence_id'] == sequence_id for row in operations)


def test_user_round_requires_prior_auto_and_viewed_exact_source(work):
    service, sequences, driver, sequence_id = work
    with pytest.raises(ServiceError, match='production_sequence_initial_round_required'):
        start(service, 'user_requested', 'premature')
    with pytest.raises(ServiceError, match='production_sequence_source_not_found'):
        start(service, source=dict(SOURCE, version_id='unknown'))
    with pytest.raises(ServiceError, match='production_refinement_source_not_registered'):
        start(service, source=dict(SOURCE, sha256='c' * 64))
    assert driver.calls == []
    assert sequences.operations(OWNER, PROJECT, sequence_id) == []


def test_restart_reconciles_lost_accepted_response_without_resubmit(work):
    service, sequences, driver, _ = work
    driver.unknown_once = True
    operation = start(service)
    service.tick()
    assert service.get(OWNER, operation['id'])['children'][0]['status'] == 'submission_unknown'
    service = ProductionRefinementService(service.path, sequences, driver)
    assert finish(service, operation)['status'] == 'completed'
    assert len([stage for stage, _, _ in driver.calls if stage == 'review']) == 1


def test_absent_unknown_submission_never_resubmits_or_refunds(work):
    service, sequences, driver, sequence_id = work
    driver.unknown_once = True
    operation = start(service)
    service.tick()
    driver.children.clear()
    for _ in range(4):
        service = ProductionRefinementService(service.path, sequences, driver)
        service.tick()
    assert len(driver.calls) == 1
    assert service.get(OWNER, operation['id'])['children'][0]['status'] == 'submission_unknown'
    assert sequences.operations(OWNER, PROJECT, sequence_id)[0]['status'] == 'submitted'
    with pytest.raises(ServiceError, match='production_sequence_allowance_consumed'):
        start(service, key='another-auto')


def test_failures_consume_round_and_do_not_trigger_automatic_second_review(work):
    service, sequences, driver, sequence_id = work
    driver.fail_once = 'module'
    result = finish(service, start(service))
    assert result['status'] == 'failed'
    assert [stage for stage, _, _ in driver.calls] == ['review', 'module']
    assert sequences.operations(OWNER, PROJECT, sequence_id)[0]['status'] == 'failed'
    with pytest.raises(ServiceError, match='production_sequence_allowance_consumed'):
        start(service, key='retry')


def test_budget_enforced_before_each_task_no_unbounded_requirements_loop(work):
    service, sequences, driver, _ = work
    operation = start(service, 'requirements', 'explicit-budget', items=ITEMS,
        budget=dict(max_agent_tasks=1, max_renders=1))
    result = finish(service, operation)
    assert result['status'] == 'failed'
    assert result['error'] == 'production_refinement_budget_exhausted'
    assert result['usage'] == dict(agent_tasks=1, renders=0)
    assert [stage for stage, _, _ in driver.calls] == ['review']


def test_cancel_before_submission_stops_without_any_paid_child(work):
    service, sequences, driver, sequence_id = work
    operation = start(service)
    service.cancel(OWNER, operation['id'])
    result = finish(service, operation)
    assert result['status'] == 'cancelled'
    assert driver.calls == []
    assert sequences.operations(OWNER, PROJECT, sequence_id)[0]['status'] == 'cancelled'


def test_idempotency_replay_returns_same_task_and_rejects_changed_body(work):
    service, _, driver, _ = work
    original = start(service)
    finish(service, original)
    assert start(service)['id'] == original['id']
    with pytest.raises(ServiceError, match='production_refinement_idempotency_conflict'):
        start(service, source=dict(SOURCE, sha256='e' * 64))
    with pytest.raises(ServiceError, match='not_found'):
        service.get('bob', original['id'])


def test_preparation_rejection_is_unsubmitted_and_definitive(work):
    service, _, driver, _ = work
    driver.preparation_failure = 'film_creation_project_binding_mismatch'
    result = finish(service, start(service))
    assert result['status'] == 'failed'
    assert driver.calls == []
    assert result['children'][0]['stop_proven'] is True


def test_changed_run_id_cannot_launder_refinement_as_new_origin(work):
    service, sequences, driver, sequence_id = work
    driver.next_output_source = dict(SOURCE, run_id='invented-new-origin', version_id='new-render')
    operation = start(service)
    for _ in range(20):
        service.tick()
    result = service.get(OWNER, operation['id'])
    assert result['status'] == 'running'
    assert result['error'] == 'production_refinement_result_lineage_changed'
    assert sequences.source_for_run(OWNER, PROJECT, 'invented-new-origin') is None


def test_point_issue_at_boundary_targets_following_scene(work):
    service, _, driver, _ = work
    driver.issues = [dict(ISSUE, start_seconds=3, end_seconds=3)]
    result = finish(service, start(service))
    assert result['status'] == 'completed'
    assert result['result']['module_version_ids'] == ['m0', 'revised-1', 'revised-2']


def test_explicit_requirements_do_not_expand_to_unrequested_review_issues(work):
    service, _, driver, _ = work
    driver.issues = [dict(ISSUE, start_seconds=0, end_seconds=1)]
    result = finish(service, start(service, 'requirements', 'scoped-work', items=ITEMS,
        budget=dict(max_agent_tasks=3, max_renders=1)))
    assert result['status'] == 'completed'
    assert result['result']['module_version_ids'] == ['m0', 'revised-1', 'revised-2']
    assert result['review']['issues'] == driver.issues


def test_requirements_outside_actual_film_rejected_before_allowance(work):
    service, sequences, driver, sequence_id = work
    items = [dict(ITEMS[0], scope=dict(kind='time_range', start_seconds=8, end_seconds=10))]
    with pytest.raises(ServiceError, match='production_refinement_requirement_outside_film'):
        start(service, 'requirements', 'outside-film', items=items,
            budget=dict(max_agent_tasks=3, max_renders=1))
    assert sequences.operations(OWNER, PROJECT, sequence_id) == []
    assert driver.calls == []


def test_unproven_stopped_child_never_unlocks_next_paid_stage(work):
    service, sequences, driver, _ = work
    operation = start(service)
    original = driver.prepare
    def prepare(*args):
        checksum, submit = original(*args)
        def unproven():
            submit()
            driver.children[args[1]]['stop_proven'] = False
        return checksum, unproven
    driver.prepare = prepare
    for _ in range(6):
        service.tick()
    result = service.get(OWNER, operation['id'])
    assert result['status'] == 'running'
    assert result['error'] == 'production_refinement_stop_not_proven'
    assert [stage for stage, _, _ in driver.calls] == ['review']
    assert result['children'][0]['status'] != 'completed'


def test_competing_workflows_only_one_claimant_submits_same_child(work):
    service, sequences, driver, _ = work
    operation = start(service)
    value = service._read(operation['id'])
    service._allowance(value)
    service._child(value, 'review')
    second = ProductionRefinementService(service.path, sequences, driver)
    barrier = threading.Barrier(2)
    original = driver.prepare
    def prepare(*args):
        ready = original(*args)
        barrier.wait(timeout=10)
        return ready
    driver.prepare = prepare
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda item: item.tick(), [service, second]))
    assert [stage for stage, _, _ in driver.calls] == ['review']
    driver.prepare = original
    assert finish(service, operation)['status'] == 'completed'


@pytest.mark.parametrize('kind', ['user_requested', 'requirements'])
def test_legal_individual_fields_that_exceed_combined_author_budget_fail_before_allowance(work, kind):
    service, sequences, driver, sequence_id = work
    changes = (dict(instruction='x' * 16000) if kind == 'user_requested' else dict(
        items=[dict(scope=dict(kind='whole_film'), problem='x' * 8000, expected='y' * 8000)],
        budget=dict(max_agent_tasks=4, max_renders=1)))
    with pytest.raises(ServiceError, match='production_refinement_user_input_too_large'):
        service.start(OWNER, PROJECT, SOURCE, kind=kind, request_id='oversized-' + kind, **changes)
    assert driver.calls == []
    assert sequences.operations(OWNER, PROJECT, sequence_id) == []
    assert service.list(OWNER, PROJECT) == []


def test_preflight_preserves_user_text_and_reserves_room_for_observed_review(work):
    service, sequences, driver, sequence_id = work
    instruction = '让对象移动自然，原文保留。' * 100
    # Use an explicit requirement before automatic rounds to isolate the input
    # preflight from the independently tested user-round eligibility rule.
    items = [dict(scope=dict(kind='whole_film'), problem=instruction, expected='保留原文中的每个要求。')]
    operation = start(service, 'requirements', 'precise-user-text', items=items,
        budget=dict(max_agent_tasks=4, max_renders=1))
    frozen = service._read(operation['id'])
    assert frozen['request']['items'][0]['problem'] == instruction
    assert sequences.operations(OWNER, PROJECT, sequence_id)[0]['items'][0]['problem'] == instruction
