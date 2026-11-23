"""Task cost history survives immutable-plan retries and controlled revisions."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.scene_space_budget import (
    MAX_EVENTS, bind_revision, create_usage, prepare_retry, record_usage,
    require_export_budget, usage_status, validate_usage,
)
from video_workbench.scene_space_plan import freeze_plan, revise_plan


@pytest.fixture
def frozen():
    path=Path(__file__).resolve().parents[1]/'fixtures/scene_space/plan.json'
    return freeze_plan(json.loads(path.read_text(encoding='utf-8')))


def test_failed_costs_and_duplicate_events_survive_technical_retry(frozen):
    original=deepcopy(frozen)
    ledger=create_usage(frozen,lineage_id='task-1')
    ledger=record_usage(ledger,id='failed-author',phase='generation',outcome='failed',
        model_calls=1,model_tokens=120,render_seconds=2)
    ledger=record_usage(ledger,id='review',phase='review',outcome='completed',
        model_calls=1,model_tokens=80)
    repeated=record_usage(ledger,id='failed-author',phase='generation',outcome='failed',
        model_calls=1,model_tokens=120,render_seconds=2)
    assert repeated==ledger
    retry=prepare_retry(ledger,frozen,frozen['plan_sha256'])
    assert retry['frozen']==frozen==original
    assert retry['usage']==ledger
    assert retry['status']['used']['model_calls']==2
    assert retry['status']['used']['model_tokens']==200
    assert retry['status']['used']['render_seconds']==2
    with pytest.raises(ServiceError,match='event_conflict'):
        record_usage(ledger,id='failed-author',phase='generation',outcome='failed',model_calls=0)


def test_cost_history_survives_revision_without_rewriting_frozen_plan(frozen):
    ledger=create_usage(frozen,lineage_id='task-1')
    ledger=record_usage(ledger,id='repair-cost',phase='repair',outcome='completed',
        model_calls=1,model_tokens=100,render_seconds=4)
    replacement=deepcopy(frozen['plan'])
    replacement['revision']+=1
    replacement['viewport']['width']-=1
    replacement['budget']['revisions']['used']+=1
    revised=revise_plan(frozen,replacement,reason='Fit the target viewport')
    carried=bind_revision(ledger,frozen,revised['frozen'],reason='Fit the target viewport')
    before=usage_status(ledger);after=usage_status(carried)
    assert after['used']['model_calls']==before['used']['model_calls']
    assert after['used']['model_tokens']==before['used']['model_tokens']
    assert after['used']['render_seconds']==before['used']['render_seconds']
    assert after['used']['revisions']==before['used']['revisions']+1
    assert carried['root_plan_sha256']==ledger['root_plan_sha256']
    assert carried['plan_sha256']==revised['frozen']['plan_sha256']
    assert carried['events']==ledger['events']
    assert prepare_retry(carried,revised['frozen'],carried['plan_sha256'])['usage']==carried
    with pytest.raises(ServiceError,match='usage_plan_changed'):
        prepare_retry(carried,frozen,frozen['plan_sha256'])


def test_actual_overrun_remains_recorded_and_blocks_export_and_retry(frozen):
    ledger=create_usage(frozen,lineage_id='task-1')
    ledger=record_usage(ledger,id='actual-overrun',phase='render',outcome='failed',
        render_seconds=ledger['limits']['render_seconds']+1)
    status=usage_status(ledger)
    assert status['status']=='over_budget'
    assert 'render_seconds' in status['exceeded']
    assert status['used']['render_seconds']>ledger['limits']['render_seconds']
    assert not status['can_start_export']
    with pytest.raises(ServiceError,match='export_budget_unavailable'):
        require_export_budget(ledger)
    with pytest.raises(ServiceError,match='retry_budget_unavailable'):
        prepare_retry(ledger,frozen,frozen['plan_sha256'])


def test_unknown_tokens_are_never_converted_to_zero(frozen):
    ledger=create_usage(frozen,lineage_id='task-1')
    ledger=record_usage(ledger,id='unknown-measurement',phase='collaboration',outcome='failed',
        model_calls=1,model_tokens=None)
    ledger=record_usage(ledger,id='known-measurement',phase='review',outcome='completed',
        model_calls=1,model_tokens=30)
    status=usage_status(ledger)
    assert status['status']=='usage_unknown'
    assert status['used']['model_tokens'] is None
    assert status['used']['known_tokens_lower_bound']>=30
    assert status['remaining']['model_tokens'] is None
    with pytest.raises(ServiceError,match='export_budget_unavailable'):
        require_export_budget(ledger)
    omitted=record_usage(create_usage(frozen,lineage_id='task-2'),id='unmeasured',
        phase='generation',outcome='completed',model_calls=1)
    assert usage_status(omitted)['used']['model_tokens'] is None


def test_unknown_render_cost_and_missing_call_count_are_not_silently_zero(frozen):
    ledger=create_usage(frozen,lineage_id='task-1')
    ledger=record_usage(ledger,id='unmeasured-render',phase='render',outcome='failed')
    status=usage_status(ledger)
    assert status['status']=='usage_unknown'
    assert status['used']['render_seconds'] is None
    assert status['used']['known_seconds_lower_bound']==0
    with pytest.raises(ServiceError,match='export_budget_unavailable'):
        require_export_budget(ledger)
    with pytest.raises(ServiceError,match='usage_measurement_required'):
        record_usage(ledger,id='missing-count',phase='review',outcome='failed')


def test_export_admission_reserves_time_and_an_accounting_slot(frozen):
    ledger=create_usage(frozen,lineage_id='task-1')
    assert require_export_budget(ledger,reserve_seconds=1)['can_start_export']
    with pytest.raises(ServiceError,match='export_budget_unavailable'):
        require_export_budget(ledger,reserve_seconds=ledger['limits']['render_seconds']+1)
    # Build a full fixture directly, then validate its content identity.
    ledger['events']=[dict(id='event-'+str(i),phase='preview',outcome='completed',
        model_calls=0,model_tokens=0,render_seconds=0) for i in range(MAX_EVENTS)]
    ledger['sha256']=digest({k:v for k,v in ledger.items() if k!='sha256'})
    assert usage_status(ledger)['available_event_slots']==0
    with pytest.raises(ServiceError,match='export_budget_unavailable'):
        require_export_budget(ledger)
    with pytest.raises(ServiceError,match='event_limit'):
        record_usage(ledger,id='one-too-many',phase='preview',outcome='completed')


def test_changed_content_cannot_reuse_a_ledger_identity(frozen):
    ledger=create_usage(frozen,lineage_id='task-1')
    ledger['baseline']['model_calls']+=1
    with pytest.raises(ServiceError,match='identity_changed'):
        validate_usage(ledger)


@pytest.mark.parametrize('value',[False,-1,10**400,float('inf'),{},[]])
def test_malformed_measurements_fail_with_service_errors(frozen,value):
    ledger=create_usage(frozen,lineage_id='task-1')
    with pytest.raises(ServiceError):
        record_usage(ledger,id='bad',phase='preview',outcome='completed',render_seconds=value)


def test_revision_binding_rejects_unrecorded_spending(frozen):
    ledger=create_usage(frozen,lineage_id='task-1')
    replacement=deepcopy(frozen['plan'])
    replacement['revision']+=1
    replacement['budget']['revisions']['used']+=1
    replacement['budget']['model']['used_calls']+=1
    revised=revise_plan(frozen,replacement,reason='Tracked correction required')
    with pytest.raises(ServiceError,match='unrecorded_spending'):
        bind_revision(ledger,frozen,revised['frozen'],reason='Tracked correction required')
