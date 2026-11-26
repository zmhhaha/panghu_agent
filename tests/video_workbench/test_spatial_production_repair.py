from copy import deepcopy

from agent_service.contracts import digest
from video_workbench.scene_space_production import SceneSpaceProductionAdapter
from test_scene_space_production import scene_env, gated_scene_env, legacy_env, OWNER, begin, finish


def structural_author(env, *, replace_identity=False):
    collect=env.author.collect
    def revised(job_id,input_sha256,document):
        result=collect(job_id,input_sha256,document)
        repair=document['constraints'].get('scene_space_same_content_repair')
        if repair:
            plan=deepcopy(repair['plan'])
            plan['revision']+=1;plan['budget']['revisions']['used']+=1
            for key in plan['shots'][0]['camera']['keyframes']:
                key['x']+=2
            if replace_identity:
                plan['worlds'][0]['seed']+=1
                plan['worlds'][0]['revision']+=1
                for shot in plan['shots']:
                    if shot['world_id']==plan['worlds'][0]['id']:
                        shot['world_revision']+=1
            result['method']['scene_space']['plan']=plan
            result['method']['scene_space']['world']=deepcopy(plan['worlds'][0])
        return result
    env.author.collect=revised


def test_reviewed_camera_revision_preserves_source_history_usage_and_invalidates_previews(gated_scene_env):
    env=gated_scene_env;env.reviewer.scores=[65,90];structural_author(env)
    run=begin(env,key='spatial-camera-repair');finish(env)
    public=env.runs.get(OWNER,run['id']);row=env.adapter._read(OWNER,run['id'])
    assert public['status']=='completed' and row['attempt']==1
    original=row['attempts'][0]
    assert original['plan_sha256']!=row['plan_sha256']
    assert original['plan_change']['affected_shot_ids']==['shot-1']
    assert original['plan_change']['budget_history_preserved'] is True
    assert original['plan_change']['invalidated_cache_ids']
    saved=env.spaces.get(OWNER,run['id'])
    assert saved['frozen']['plan']['revision']==2
    assert saved['frozen']['plan']['budget']['revisions']['used']==1
    assert saved['usage_status']['used']['model_calls']==4
    assert saved['usage_status']['used']['model_tokens']==3600
    assert row['source_sha256']!=original['source_sha256']
    assert row['gate']['layout_report']['plan_sha256']==row['plan_sha256']
    assert all(p['request']['scene_space']['plan_sha256']==row['plan_sha256'] for p in row['previews'])
    assert env.adapter.preview(OWNER,run['id'],original['previews'][0]['render_id'])[1]=='image/png'


def test_changing_seed_cannot_pass_as_same_content_repair(gated_scene_env):
    env=gated_scene_env;env.reviewer.scores=[65,90];structural_author(env,replace_identity=True)
    run=begin(env,key='spatial-content-protection');finish(env)
    public=env.runs.get(OWNER,run['id']);row=env.adapter._read(OWNER,run['id'])
    assert public['status']=='failed' and public['error']=='scene_space_same_content_seed_changed'
    assert row['attempt']==0 and env.spaces.get(OWNER,run['id'])['frozen']['plan']['revision']==1
    assert not any('frame_range' not in r for r in env.renderer.submissions)


def test_committed_identical_revision_wins_a_competing_compare_and_swap(gated_scene_env):
    env=gated_scene_env;env.reviewer.scores=[65,90];structural_author(env)
    revise=env.spaces.revise
    def raced(*args,**kwargs):
        revise(*args,**kwargs)
        from agent_service.contracts import ServiceError
        raise ServiceError('scene_space_plan_revision_conflict',409)
    env.spaces.revise=raced
    run=begin(env,key='spatial-identical-cas');finish(env)
    assert env.runs.get(OWNER,run['id'])['status']=='completed'
    saved=env.spaces.get(OWNER,run['id'])
    assert saved['frozen']['plan']['revision']==2
    assert saved['usage_status']['used']['model_calls']==4


def test_restart_after_ledger_revision_does_not_pay_or_revise_twice(gated_scene_env):
    env=gated_scene_env;env.reviewer.scores=[65,90];structural_author(env)
    class Crash(BaseException):pass
    revise=env.spaces.revise
    def interrupted(*args,**kwargs):
        revise(*args,**kwargs)
        raise Crash()
    env.spaces.revise=interrupted
    run=begin(env,key='spatial-repair-restart')
    import pytest
    with pytest.raises(Crash):finish(env)
    assert env.spaces.get(OWNER,run['id'])['frozen']['plan']['revision']==2
    assert env.adapter._read(OWNER,run['id'])['attempt']==0
    env.spaces.revise=revise
    env.adapter=SceneSpaceProductionAdapter(env.adapter.root,space_store=env.spaces,
        author=env.author,reviewer=env.reviewer,renderer=env.renderer,enabled=True)
    env.runs.scene_space_adapter=env.adapter
    finish(env)
    row=env.adapter._read(OWNER,run['id'])
    assert env.runs.get(OWNER,run['id'])['status']=='completed' and row['attempt']==1
    assert len(row['attempts'])==1 and len(env.author.submissions)==2
    assert env.spaces.get(OWNER,run['id'])['usage_status']['used']['model_calls']==4
