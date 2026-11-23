"""Actual recipe coordinator with synthetic peers exercises the common lab path."""
from copy import deepcopy
import sqlite3

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.lab_resource_stage import LabResourceStage
from test_manim_method_integration import OWNER, setup


def stage(tmp_path):
    coordinator, gateway, executor, run = setup(tmp_path)
    bindings = tmp_path / 'commissions.sqlite3'
    with sqlite3.connect(bindings) as db:
        db.execute('CREATE TABLE commissions(id TEXT)')
    shared = LabResourceStage(tmp_path / 'resource-plans.sqlite3', bindings_path=bindings,
        coordinator=lambda: coordinator)
    return shared, coordinator, gateway, executor, run


def finish(shared, run):
    for _ in range(20):
        state = shared.advance(OWNER, run)
        if state in {'completed', 'failed', 'cancelled'}:
            return state
    raise AssertionError('resource stage did not complete')


def test_reading_pending_lab_resource_does_not_create_or_submit(tmp_path):
    shared, _, gateway, executor, run = stage(tmp_path)
    assert shared.view(OWNER, run['id'])['status'] == 'pending'
    assert shared.allocated_round(OWNER, run['id']) is None
    assert not shared.path.exists()
    with sqlite3.connect(shared.bindings_path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='lab_resource_bindings'").fetchone() is None
    assert gateway.submissions == executor.calls == 0


def test_lab_uses_common_plan_and_keeps_content_local_to_zero(tmp_path):
    shared, coordinator, gateway, executor, run = stage(tmp_path)
    assert finish(shared, run) == 'completed'
    assert gateway.submissions == executor.calls == 1
    descriptor = shared.context(OWNER, run['id'])
    assert descriptor['manifest']['global_start_frame'] == 0
    assert shared.placement(OWNER, run)['global_start_frame'] == 24
    assert run['frozen']['resource_authoring']['global_start_frame'] == 24
    assert descriptor['scope']['experiment_id'] != run['id']
    assert shared._store().get(OWNER, run['id'])['usage']['attempts'] == 1
    assert coordinator.resolve(descriptor)
    for _ in range(3):
        assert shared.view(OWNER, run['id'])['status'] == 'completed'
    assert gateway.submissions == executor.calls == 1


def test_lab_restart_reconciles_uncertain_recipe_without_new_paid_key(tmp_path):
    shared, coordinator, gateway, executor, run = stage(tmp_path)
    gateway.unknown = True
    for _ in range(3):
        shared.advance(OWNER, run)
    original = shared._store().get(OWNER, run['id'])['nodes']['manim']['task']['task_id']
    resumed = LabResourceStage(shared.path, bindings_path=shared.bindings_path, coordinator=lambda: coordinator)
    assert finish(resumed, run) == 'completed'
    assert resumed._store().get(OWNER, run['id'])['nodes']['manim']['task']['task_id'] == original
    assert gateway.submissions == executor.calls == 1


def test_lab_cancel_before_recipe_does_not_schedule_media(tmp_path):
    shared, _, gateway, executor, run = stage(tmp_path)
    shared.advance(OWNER, run)
    assert shared.advance(OWNER, run, cancelled=True) == 'cancelled'
    assert gateway.submissions == executor.calls == 0


def test_missing_shared_ledger_does_not_reissue_original_commission(tmp_path):
    shared, coordinator, gateway, executor, run = stage(tmp_path)
    assert finish(shared, run) == 'completed'
    shared.path.unlink()
    resumed = LabResourceStage(shared.path, bindings_path=shared.bindings_path, coordinator=lambda: coordinator)
    with pytest.raises(ServiceError, match='ledger_missing'):
        resumed.advance(OWNER, run)
    assert gateway.submissions == executor.calls == 1


def test_lab_position_only_revision_reuses_the_exact_resource(tmp_path):
    shared, _, gateway, executor, run = stage(tmp_path)
    assert finish(shared, run) == 'completed'
    original = shared.context(OWNER, run['id'])
    shared._store().revise(OWNER, run['id'], {'manim': {'placement': {'global_start_frame': 48}}},
        'move-resource', 'Move the unchanged expression later in the same passage')
    assert finish(shared, run) == 'completed'
    assert shared.context(OWNER, run['id']) == original
    assert shared.placement(OWNER, run)['global_start_frame'] == 48
    assert gateway.submissions == executor.calls == 1


def test_each_explicit_resource_round_regenerates_manim_with_previous_scene_and_review(tmp_path):
    shared, coordinator, gateway, executor, run = stage(tmp_path)
    run['resource_round'] = 0
    assert finish(shared, run) == 'completed'
    assert shared.allocated_round(OWNER, run['id']) == 0
    original = shared.context(OWNER, run['id'], resource_round=0)
    second = deepcopy(run)
    second['resource_round'] = 1
    second['frozen'].update(feedback='Make the corresponding vertices easier to track.',
        manim_iteration=dict(baseline_descriptor=original,
            review=dict(outcome='failed', findings=['The vertices cannot be followed.'])))
    assert finish(shared, second) == 'completed'
    assert shared.allocated_round(OWNER, run['id']) == 1
    assert shared.allocated_round('another-owner', run['id']) is None
    revised = shared.context(OWNER, run['id'], resource_round=1)
    assert revised['scope'] != original['scope']
    assert shared.context(OWNER, run['id'], resource_round=0) == original
    assert shared.placement(OWNER, second)['global_start_frame'] == 24
    first_id = shared.operation_id(run['id'], 0)
    second_id = shared.operation_id(run['id'], 1)
    assert first_id != second_id and shared.operation_id(run['id']) == run['id']
    first_task = shared._store().get(OWNER, first_id)['nodes']['manim']['task']
    second_task = shared._store().get(OWNER, second_id)['nodes']['manim']['task']
    assert first_task['task_id'] != second_task['task_id']
    assert second_task['inputs']['manim_iteration'] == second['frozen']['manim_iteration']
    assert coordinator._get(OWNER, second_task['task_id'])['input']['guidance'].count('vertices') >= 2
    assert gateway.submissions == executor.calls == 2
    # Polls and reconstruction retain this round's original producer identity.
    resumed = LabResourceStage(shared.path, bindings_path=shared.bindings_path, coordinator=lambda: coordinator)
    assert finish(resumed, second) == 'completed'
    assert gateway.submissions == executor.calls == 2


def test_cancel_new_round_preserves_prior_manim_and_spends_no_new_recipe(tmp_path):
    shared, _, gateway, executor, run = stage(tmp_path)
    run['resource_round'] = 0
    assert finish(shared, run) == 'completed'
    original = shared.context(OWNER, run['id'], resource_round=0)
    second = deepcopy(run)
    second['resource_round'] = 1
    assert shared.advance(OWNER, second, cancelled=True) == 'cancelled'
    assert shared.context(OWNER, run['id'], resource_round=0) == original
    assert gateway.submissions == executor.calls == 1


def test_allocated_round_reads_an_intent_even_before_shared_ledger_creation(tmp_path):
    shared, _, gateway, executor, run = stage(tmp_path)
    operation_id = shared.operation_id(run['id'], 1)
    with shared._connection(create=True) as db:
        db.execute('INSERT INTO lab_resource_bindings VALUES(?,?,?,?)',
            (OWNER, operation_id, 'f' * 64, 'intent'))
    assert shared.allocated_round(OWNER, run['id']) == 1
    assert not shared.path.exists()
    assert gateway.submissions == executor.calls == 0


@pytest.mark.parametrize('cancelled', [False, True])
@pytest.mark.parametrize('existing_ledger', [False, True])
def test_expired_intent_before_ledger_creation_stops_after_restart_without_recipe(tmp_path, cancelled, existing_ledger):
    shared, coordinator, gateway, executor, run = stage(tmp_path)
    if existing_ledger:
        shared._store().create(OWNER, run['id'], 'another-round', shared.request(run))
    run['resource_round'] = 0
    operation_id = shared.operation_id(run['id'], 0)
    checksum = digest(shared.request(run))
    with shared._connection(create=True) as db:
        db.execute('INSERT INTO lab_resource_bindings VALUES(?,?,?,?)',
            (OWNER, operation_id, checksum, 'intent'))
    assert shared.path.exists() is existing_ledger
    resumed = LabResourceStage(shared.path, bindings_path=shared.bindings_path,
        coordinator=lambda: coordinator, clock=lambda: run['deadline_at'] + 1)
    expected = 'cancelled' if cancelled else 'failed'
    assert resumed.advance(OWNER, run, cancelled=cancelled) == expected
    assert resumed.view(OWNER, run['id'], resource_round=0)['status'] == (
        'cancelled' if cancelled else 'timed_out')
    assert shared.path.exists() is existing_ledger
    assert not resumed._has_plan(OWNER, operation_id)
    assert gateway.submissions == executor.calls == 0
    # Even a later worker with an earlier clock cannot turn the stopped intent
    # into a new resource plan or spend a fresh paid identity.
    assert shared.advance(OWNER, run) == expected
    assert gateway.submissions == executor.calls == 0
    assert shared.path.exists() is existing_ledger
    changed = deepcopy(run)
    changed['frozen']['goal'] += ' Changed replay.'
    with pytest.raises(ServiceError, match='request_changed'):
        shared.advance(OWNER, changed)


def test_shared_producer_preserves_first_experiment_drawing_and_browser_motion_selection(tmp_path):
    shared, coordinator, gateway, executor, run = stage(tmp_path)
    selection = dict(creation=['manim'], motion=['animejs'])
    run['frozen'].update(manim_iteration={}, capability_selection=deepcopy(selection))
    assert finish(shared, run) == 'completed'
    task = shared._store().get(OWNER, run['id'])['nodes']['manim']['task']
    assert task['inputs']['capability_selection'] == selection
    guidance = coordinator._get(OWNER, task['task_id'])['input']['guidance']
    assert 'not to Python' in guidance and '"creation":["manim"]' in guidance
    assert run['frozen']['capability_selection'] == selection
    assert gateway.submissions == executor.calls == 1


def test_oversized_continuation_is_rejected_before_resource_attempt_reservation(tmp_path):
    shared, _, gateway, executor, run = stage(tmp_path)
    run['frozen'].update(feedback='x' * 16000,
        manim_iteration=dict(criteria=[dict(id='shape', description='Preserve the intended expression.')]))
    with pytest.raises(ServiceError, match='iteration_context_too_large'):
        shared.advance(OWNER, run)
    assert not shared.path.exists()
    assert shared._binding(OWNER, run['id']) is None
    assert gateway.submissions == executor.calls == 0


@pytest.mark.parametrize('round_index', [-1, 8, True, '1'])
def test_invalid_resource_round_does_not_allocate_a_new_plan(tmp_path, round_index):
    shared, _, gateway, executor, run = stage(tmp_path)
    run['resource_round'] = round_index
    with pytest.raises(ServiceError, match='round_invalid'):
        shared.advance(OWNER, run)
    assert not shared.path.exists()
    assert gateway.submissions == executor.calls == 0
