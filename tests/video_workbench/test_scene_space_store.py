from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path

import pytest

from agent_service.contracts import ServiceError
from video_workbench.scene_space_store import SceneSpaceStore


@pytest.fixture
def plan():
    return json.loads((Path(__file__).resolve().parents[1]/'fixtures/scene_space/plan.json').read_text(encoding='utf-8'))


def test_restart_replay_and_owner_isolation(tmp_path,plan):
    store=SceneSpaceStore(tmp_path)
    original=store.freeze('owner-a','film',plan)
    store.record_usage('owner-a','film',id='failed-author',phase='generation',outcome='failed',
        model_calls=1,model_tokens=40,render_seconds=0)
    store=SceneSpaceStore(tmp_path)
    replay=store.freeze('owner-a','film',plan)
    assert replay['usage_status']['used']['model_calls']==1
    assert store.retry('owner-a','film',original['frozen']['plan_sha256'])['usage']==replay['usage']
    with pytest.raises(ServiceError,match='task_not_found'):store.get('owner-b','film')
    changed=deepcopy(plan);changed['viewport']['width']-=1
    with pytest.raises(ServiceError,match='task_plan_conflict'):store.freeze('owner-a','film',changed)


def test_concurrent_duplicate_settlement_is_exactly_once(tmp_path,plan):
    store=SceneSpaceStore(tmp_path);store.freeze('owner','film',plan)
    def charge(_):
        return store.record_usage('owner','film',id='same-event',phase='review',
            outcome='failed',model_calls=1,model_tokens=20,render_seconds=0)
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(charge,range(12)))
    current=store.get('owner','film')
    assert current['usage_status']['used']['model_calls']==1
    assert len(current['usage']['events'])==1
    with pytest.raises(ServiceError,match='event_conflict'):
        store.record_usage('owner','film',id='same-event',phase='review',outcome='failed',model_calls=2,model_tokens=20)


def test_concurrent_reservations_cannot_double_spend(tmp_path,plan):
    plan['budget']['model']['limit_calls']=1
    store=SceneSpaceStore(tmp_path);store.freeze('owner','film',plan)
    def reserve(number):
        try:store.reserve('owner','film','operation-'+str(number),phase='generation',model_calls=1,model_tokens=10);return True
        except ServiceError as error:
            assert error.code=='scene_space_budget_reservation_exceeded';return False
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(reserve,range(4)))
    assert sum(results)==1
    row=store.get('owner','film');operation=row['reservations'][0]
    store.record_usage('owner','film',id=operation['id'],phase='generation',outcome='failed',
        model_calls=1,model_tokens=9,render_seconds=0)
    replay=store.reserve('owner','film',operation['id'],phase='generation',model_calls=1,model_tokens=10)
    assert replay['reservations'][0]['status']=='settled'


def test_overrun_is_preserved_and_blocks_later_export(tmp_path,plan):
    store=SceneSpaceStore(tmp_path);store.freeze('owner','film',plan)
    row=store.record_usage('owner','film',id='actual-overrun',phase='render',outcome='failed',
        render_seconds=plan['budget']['render']['limit_seconds']+1)
    assert row['usage_status']['status']=='over_budget'
    with pytest.raises(ServiceError,match='export_budget_unavailable'):store.require_export('owner','film')
    assert SceneSpaceStore(tmp_path).get('owner','film')['usage']==row['usage']


def test_revision_preserves_costs_old_plan_and_preview_history(tmp_path,plan):
    store=SceneSpaceStore(tmp_path);row=store.freeze('owner','film',plan)
    original=row['frozen']
    store.record_usage('owner','film',id='repair',phase='repair',outcome='completed',model_calls=1,model_tokens=50)
    store.record_preview('owner','film','layout',dict(status='valid',plan_sha256=original['plan_sha256']))
    replacement=deepcopy(plan);replacement['revision']+=1
    replacement['budget']['revisions']['used']+=1;replacement['viewport']['width']-=1
    revised=store.revise('owner','film',replacement,reason='Fit composition',expected_plan_sha256=original['plan_sha256'])
    assert revised['usage_status']['used']['model_tokens']==50
    assert revised['previews'][0]['current'] is False
    assert store.plan('owner','film',original['plan_sha256'])==original
    assert set(revised['change']['affected_shot_ids'])=={shot['id'] for shot in plan['shots']}
    with pytest.raises(ServiceError,match='revision_conflict'):
        store.revise('owner','film',replacement,reason='stale',expected_plan_sha256=original['plan_sha256'])


def test_active_work_blocks_plan_revision_and_export_respects_reservations(tmp_path,plan):
    store=SceneSpaceStore(tmp_path);row=store.freeze('owner','film',plan)
    store.reserve('owner','film','inflight',phase='render',render_seconds=plan['budget']['render']['limit_seconds'])
    with pytest.raises(ServiceError,match='reservation_exceeded'):store.require_export('owner','film',reserve_seconds=1)
    with pytest.raises(ServiceError,match='still_active'):
        store.revise('owner','film',plan,reason='not yet',expected_plan_sha256=row['frozen']['plan_sha256'])
    with pytest.raises(ServiceError,match='still_active'):
        store.retry('owner','film',row['frozen']['plan_sha256'])


def test_unknown_model_tokens_allow_a_bounded_diagnostic_but_not_final_export(tmp_path,plan):
    store=SceneSpaceStore(tmp_path);store.freeze('owner','film',plan)
    store.record_usage('owner','film',id='author',phase='generation',outcome='completed',model_calls=1)
    preview=store.reserve('owner','film','diagnostic',phase='preview',render_seconds=5)
    assert preview['usage_status']['used']['model_tokens'] is None
    with pytest.raises(ServiceError,match='export_budget_unavailable'):store.require_export('owner','film')
    with pytest.raises(ServiceError,match='budget_unavailable'):
        store.reserve('owner','film','review',phase='review',model_calls=1,model_tokens=1)
