"""Stopped Manim checkpoints are reused without changing the creative brief."""
from copy import deepcopy
import json
import sqlite3

import pytest

from agent_service.contracts import ServiceError
from video_workbench.lab_resource_stage import LabManimProducer, LabResourceStage
from video_workbench.manim_method_coordinator import ManimMethodCoordinator
from test_lab_resource_stage import stage, finish
from test_manim_method_integration import OWNER, RecipeGateway, ResourceExecutor


class FailingResourceExecutor(ResourceExecutor):
    def __init__(self):
        super().__init__()
        self.fail = True

    def execute(self, plan):
        result = super().execute(plan)
        if self.fail:
            result.update(status='failed', generated_assets=None, error='fixture_resource_failure')
            self.result = deepcopy(result)
        return result


class FailingRecipeGateway(RecipeGateway):
    def __init__(self):
        super().__init__()
        self.fail = True

    def prepare_submission(self, operation, document, deadline, refs):
        parent = super().prepare_submission(operation, document, deadline, refs)

        def admit():
            result = parent()
            if self.fail:
                self.job['state'] = 'failed'
            return result

        return admit


def retried(run, name='retry-commission'):
    previous = deepcopy(run)
    previous['state'] = 'failed'
    current = deepcopy(run)
    current.update(id=name, state='queued', deadline_at=run['deadline_at'] + 60)
    current['frozen']['retry_of_commission_id'] = run['id']
    return current, previous


def source_record(shared, coordinator, run):
    operation_id = shared.operation_id(run['id'], run.get('resource_round'))
    plan = shared._get(OWNER, operation_id)
    task = plan['nodes']['manim']['task']
    return task, deepcopy(coordinator._get(OWNER, task['task_id']))


def test_completed_pngs_are_adopted_with_original_identity_and_no_recipe_or_cairo(tmp_path, monkeypatch):
    shared, coordinator, gateway, executor, original = stage(tmp_path)
    original['resource_round'] = 0
    assert finish(shared, original) == 'completed'
    descriptor = shared.context(OWNER, original['id'], resource_round=0)
    before_task, before = source_record(shared, coordinator, original)
    current, previous = retried(original)
    monkeypatch.setattr(coordinator.skill_adapter, 'freeze', lambda *args, **kwargs: pytest.fail('Do not refresh Skills'))
    plan = shared.restore_retry(OWNER, current, previous)
    assert plan['status'] == 'completed'
    assert plan['usage']['attempts'] == 0
    assert plan['nodes']['manim']['task'] is None
    assert shared.context(OWNER, current['id'], resource_round=0) == descriptor
    assert shared.placement(OWNER, current)['global_start_frame'] == 24
    assert finish(shared, current) == 'completed'
    assert gateway.submissions == executor.calls == 1
    assert coordinator._get(OWNER, before_task['task_id']) == before
    assert shared.restore_retry(OWNER, current, previous) == shared._get(OWNER, plan['operation_id'])


def test_failed_cairo_reuses_exact_completed_recipe_and_only_executes_resource_again(tmp_path, monkeypatch):
    shared, coordinator, gateway, _, original = stage(tmp_path)
    executor = FailingResourceExecutor()
    coordinator.executor = executor
    assert finish(shared, original) == 'failed'
    before_task, before = source_record(shared, coordinator, original)
    assert before['state'] == 'failed' and 'recipe' in before
    current, previous = retried(original)
    monkeypatch.setattr(coordinator.skill_adapter, 'freeze', lambda *args, **kwargs: pytest.fail('Do not refresh Skills'))
    executor.fail = False
    plan = shared.restore_retry(OWNER, current, previous)
    new_task = plan['nodes']['manim']['task']
    new_value = coordinator._get(OWNER, new_task['task_id'])
    assert new_value['state'] == 'recipe_completed'
    assert new_value['input'] == before['input']
    assert new_value['recipe'] == before['recipe']
    assert new_value['job_id'] == before['job_id']
    assert new_value['recipe_stop_proof_sha256'] == before['recipe_stop_proof_sha256']
    assert finish(shared, current) == 'completed'
    assert gateway.submissions == 1 and executor.calls == 2
    assert coordinator._get(OWNER, before_task['task_id']) == before
    completed = coordinator._get(OWNER, new_task['task_id'])
    assert completed['plan']['request'] == before['plan']['request']
    assert completed['plan']['scope'] != before['plan']['scope']
    assert completed['input']['skill_catalog'] == before['input']['skill_catalog']
    assert coordinator.view(OWNER, new_task['task_id'])['usage'] == dict(recipe_jobs=0, resource_executions=1)


def test_failed_recipe_restarts_from_original_input_and_original_catalog(tmp_path, monkeypatch):
    shared, coordinator, _, executor, original = stage(tmp_path)
    gateway = FailingRecipeGateway()
    coordinator.gateway = gateway
    assert finish(shared, original) == 'failed'
    _, before = source_record(shared, coordinator, original)
    assert before['state'] == 'failed' and 'recipe' not in before
    current, previous = retried(original)
    gateway.fail = False
    monkeypatch.setattr(coordinator.skill_adapter, 'freeze', lambda *args, **kwargs: pytest.fail('Do not refresh Skills'))
    plan = shared.restore_retry(OWNER, current, previous)
    task_id = plan['nodes']['manim']['task']['task_id']
    assert coordinator._get(OWNER, task_id)['input'] == before['input']
    assert finish(shared, current) == 'completed'
    assert gateway.submissions == 2 and executor.calls == 1
    assert coordinator._get(OWNER, task_id)['input'] == before['input']


def test_restart_between_claim_and_coordinator_restore_resumes_the_saved_source(tmp_path, monkeypatch):
    shared, coordinator, gateway, _, original = stage(tmp_path)
    executor = FailingResourceExecutor()
    coordinator.executor = executor
    assert finish(shared, original) == 'failed'
    current, previous = retried(original)
    actual_restore = coordinator.restore_retry

    def interrupted(*args, **kwargs):
        raise RuntimeError('fixture process interrupted after resource claim')

    monkeypatch.setattr(coordinator, 'restore_retry', interrupted)
    with pytest.raises(RuntimeError, match='fixture process interrupted'):
        shared.restore_retry(OWNER, current, previous)
    plan = shared._get(OWNER, current['id'])
    task_id = plan['nodes']['manim']['task']['task_id']
    assert plan['nodes']['manim']['status'] == 'submitting'
    assert coordinator._get(OWNER, task_id) is None
    monkeypatch.setattr(coordinator, 'restore_retry', actual_restore)
    executor.fail = False
    resumed = LabResourceStage(shared.path, bindings_path=shared.bindings_path, coordinator=lambda: coordinator)
    assert finish(resumed, current) == 'completed'
    assert resumed._get(OWNER, current['id'])['nodes']['manim']['task']['task_id'] == task_id
    assert gateway.submissions == 1 and executor.calls == 2


@pytest.mark.parametrize('change', ['goal', 'frames', 'placement', 'policy', 'binding'])
def test_retry_does_not_change_creative_intent_timing_or_skill_choice(tmp_path, change):
    shared, _, gateway, executor, original = stage(tmp_path)
    assert finish(shared, original) == 'completed'
    current, previous = retried(original)
    if change == 'goal':
        current['frozen']['goal'] += ' New direction.'
    elif change == 'frames':
        current['frozen']['resource_authoring']['frame_count'] = 4
    elif change == 'placement':
        current['frozen']['resource_authoring']['global_start_frame'] = 25
    elif change == 'policy':
        current['frozen']['use_wikiskill'] = False
    else:
        current['library_binding'] = {'different': True}
    with pytest.raises(ServiceError, match='lab_resource_retry_input_changed'):
        shared.restore_retry(OWNER, current, previous)
    assert shared._binding(OWNER, current['id']) is None
    assert gateway.submissions == executor.calls == 1


def test_active_or_foreign_resource_is_not_a_reusable_checkpoint(tmp_path):
    shared, _, gateway, executor, original = stage(tmp_path)
    shared.advance(OWNER, original)
    current, previous = retried(original)
    with pytest.raises(ServiceError, match='lab_resource_retry_source_not_stopped'):
        shared.restore_retry(OWNER, current, previous)
    assert shared._binding(OWNER, current['id']) is None
    assert gateway.submissions == executor.calls == 0
    with pytest.raises(ServiceError, match='lab_resource_retry_relationship_invalid'):
        shared.restore_retry(OWNER, current, dict(previous, state='running'))


def test_source_with_no_resource_work_does_not_reserve_new_paid_task(tmp_path):
    shared, _, gateway, executor, original = stage(tmp_path)
    current, previous = retried(original)
    assert shared.restore_retry(OWNER, current, previous) is None
    assert shared._binding(OWNER, current['id']) is None
    assert gateway.submissions == executor.calls == 0


def test_missing_recipe_checkpoint_cannot_silently_start_a_new_direction(tmp_path):
    shared, coordinator, gateway, _, original = stage(tmp_path)
    executor = FailingResourceExecutor()
    coordinator.executor = executor
    assert finish(shared, original) == 'failed'
    task, _ = source_record(shared, coordinator, original)
    with sqlite3.connect(coordinator.path) as db:
        db.execute('DELETE FROM resources WHERE owner=? AND commission_id=?', (OWNER, task['task_id']))
    current, previous = retried(original)
    with pytest.raises(ServiceError, match='lab_resource_retry_checkpoint_missing'):
        shared.restore_retry(OWNER, current, previous)
    assert shared._get(OWNER, current['id'])['usage']['attempts'] == 0
    assert gateway.submissions == executor.calls == 1


def test_completed_producer_pngs_can_be_reused_without_rewriting_cancelled_plan(tmp_path):
    shared, coordinator, gateway, executor, original = stage(tmp_path)
    assert finish(shared, original) == 'completed'
    descriptor = shared.context(OWNER, original['id'])
    # Synthetic custody fixture: producer completed, but the caller expired
    # before adopting it. The original stop proof and PNG envelope stay real.
    with shared._store()._mutation(OWNER, original['id']) as plan:
        plan['cancel_requested'] = True
        plan['nodes']['manim'].update(status='cancelled', output=None)
    before = shared._get(OWNER, original['id'])
    current, previous = retried(original)
    restored = shared.restore_retry(OWNER, current, previous)
    assert restored['status'] == 'completed' and restored['usage']['attempts'] == 0
    assert shared.context(OWNER, current['id']) == descriptor
    assert shared._get(OWNER, original['id']) == before
    assert gateway.submissions == executor.calls == 1


def test_manim_fresh_recipe_receives_policy_and_reliable_issues_without_old_artifacts(tmp_path):
    shared, coordinator, _, _, run = stage(tmp_path)
    problem = dict(text='The brushwork looks like gray polygons.', source='developer')
    run['frozen'].update(creation_mode='fresh', use_wikiskill=False, problem_summary=[problem])
    assert finish(shared, run) == 'completed'
    _, value = source_record(shared, coordinator, run)
    instruction, raw = value['input']['guidance'].split('\n', 1)
    context = json.loads(raw)
    assert context['authoring_policy']['creation_mode'] == 'fresh'
    assert context['authoring_policy']['use_wikiskill'] is False
    assert context['authoring_policy']['problem_summary'] == [problem]
    assert 'without waiting for human confirmation' in instruction
    assert 'baseline' not in context and not context.get('methods')


def test_fresh_with_disabled_library_can_use_its_own_new_plan_and_feedback(tmp_path):
    shared, coordinator, gateway, executor, run = stage(tmp_path)
    current_context = dict(methods=[dict(guidance='A new method proposed in this experiment')],
        review=dict(findings=['Current experiment result needs a clearer cause']),
        developer_feedback=[dict(comment='Feedback on this experiment only')])
    run['frozen'].update(creation_mode='fresh', use_wikiskill=False, manim_iteration=current_context)
    assert finish(shared, run) == 'completed'
    _, value = source_record(shared, coordinator, run)
    context = json.loads(value['input']['guidance'].split('\n', 1)[1])
    assert all(context[key] == content for key, content in current_context.items())
    assert context['authoring_policy']['creation_mode'] == 'fresh'
    assert context['authoring_policy']['use_wikiskill'] is False
    assert gateway.submissions == executor.calls == 1


def test_continuing_with_disabled_library_can_still_use_exact_previous_owned_recipe(tmp_path):
    shared, coordinator, gateway, executor, run = stage(tmp_path)
    assert finish(shared, run) == 'completed'
    original = shared.context(OWNER, run['id'])
    continued = deepcopy(run)
    continued['id'] = 'continue-without-library'
    continued['frozen'].update(creation_mode='continue', use_wikiskill=False,
        manim_iteration=dict(baseline_descriptor=original, methods=[]))
    assert finish(shared, continued) == 'completed'
    _, value = source_record(shared, coordinator, continued)
    context = json.loads(value['input']['guidance'].split('\n', 1)[1])
    assert context['baseline'] == coordinator.source_context(OWNER, original)
    assert context['authoring_policy']['use_wikiskill'] is False
    assert context['methods'] == []
    assert gateway.submissions == executor.calls == 2


def test_published_library_binding_reaches_recipe_run_without_affecting_old_requests(tmp_path):
    shared, _, _, _, run = stage(tmp_path)
    original = shared.request(run)
    binding = dict(batch_id='frozen-batch', use_wikiskill=False, release_id=None,
        release_sha256=None, publisher_owner=None, release_sequence=None)
    run['library_binding'] = binding
    request = shared.request(run)
    assert 'library_binding' not in original['nodes'][0]['inputs']
    assert request['nodes'][0]['inputs']['library_binding'] == binding
    task = dict(task_id='task-1', inputs=request['nodes'][0]['inputs'], deadline_at=run['deadline_at'])
    child = LabManimProducer._run(task)
    assert child['library_binding'] == binding
    binding['batch_id'] = 'caller mutation'
    assert child['library_binding']['batch_id'] == 'frozen-batch'


def test_disabled_library_stage_freezes_an_empty_recipe_catalog_without_reading_releases(tmp_path):
    from video_workbench.animation_wikiskill_production import LibrarySkillProductionAdapter

    shared, coordinator, gateway, executor, run = stage(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail('Disabled Manim creation must not read active or archived library knowledge')

    adapter = LibrarySkillProductionAdapter(forbidden, forbidden, forbidden,
        tmp_path/'disabled-recipe-skills.sqlite3')
    coordinator.skill_adapter = adapter
    binding = adapter.pin(OWNER, 'laboratory-no-knowledge', use_wikiskill=False)
    run['library_binding'] = binding
    run['frozen'].update(creation_mode='fresh', use_wikiskill=False)
    assert finish(shared, run) == 'completed'
    _, value = source_record(shared, coordinator, run)
    catalog = value['input']['skill_catalog']
    assert catalog['skills'] == []
    delivery = adapter.get_bundle(OWNER, value['scope']['operation_id'], catalog['sha256'])
    assert delivery['catalog'] == catalog and delivery['packages'] == {}
    assert delivery['retrieval_receipts'][0]['library_binding'] == binding
    context = json.loads(value['input']['guidance'].split('\n', 1)[1])
    assert context['authoring_policy']['use_wikiskill'] is False
    assert gateway.submissions == executor.calls == 1


def test_oversized_policy_guidance_is_rejected_before_reserving_resource_work(tmp_path):
    shared, _, gateway, executor, run = stage(tmp_path)
    run['frozen'].update(creation_mode='fresh', problem_summary=[
        dict(text='x' * 4000, source='developer') for _ in range(5)])
    with pytest.raises(ServiceError, match='manim_method_iteration_context_too_large'):
        shared.advance(OWNER, run)
    assert not shared.path.exists()
    assert shared._binding(OWNER, run['id']) is None
    assert gateway.submissions == executor.calls == 0
