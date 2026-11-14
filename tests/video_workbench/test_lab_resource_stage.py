"""Actual recipe coordinator with synthetic peers exercises the common lab path."""
import sqlite3

import pytest

from agent_service.contracts import ServiceError
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
