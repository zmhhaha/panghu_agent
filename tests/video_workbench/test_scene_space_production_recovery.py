"""Crash boundaries use durable journals, with no real model or render work.

The imported transport fixtures provide synthetic artifacts and measured usage;
these tests establish recovery/accounting behavior, not visual acceptance.
"""
from copy import deepcopy

import pytest

from video_workbench.production_run_store import ProductionRunStore
from video_workbench.scene_space_production import SceneSpaceProductionAdapter
from video_workbench.scene_space_store import SceneSpaceStore
from test_scene_space_production import (
    OWNER, begin, finish, gated_scene_env, legacy_env, scene_env,
)


class SimulatedProcessCrash(BaseException):
    """Bypass the production driver's ordinary recoverable-error handling."""


def tick_to_crash(env):
    with pytest.raises(SimulatedProcessCrash):
        for _ in range(64):
            env.runs.tick()


def restart(env):
    """Reopen both journals; retain only the remote fixture's actual jobs."""
    env.spaces=SceneSpaceStore(env.spaces.root)
    env.adapter=SceneSpaceProductionAdapter(env.adapter.root,space_store=env.spaces,
        author=env.author,reviewer=env.reviewer,renderer=env.renderer,enabled=True)
    env.runs=ProductionRunStore(env.store,env.joint,env.motion,env.production,
        scene_space_adapter=env.adapter,sequence_store=env.sequence)


@pytest.mark.parametrize('actual_render_seconds',[None,3601])
def test_clip_settlement_crash_recovers_paid_samples_without_new_work(
        gated_scene_env,monkeypatch,actual_render_seconds):
    env=gated_scene_env
    submit=env.renderer.start
    def measured(owner,request,key):
        child=submit(owner,request,key)
        if key.startswith('scene-clip-'):
            env.renderer.jobs[(owner,key)]['usage']['render_seconds']=actual_render_seconds
        return child
    monkeypatch.setattr(env.renderer,'start',measured)
    charge=env.spaces.record_usage
    crashed=[]
    def settle_then_crash(owner,task_id,**event):
        result=charge(owner,task_id,**event)
        if event['id'].startswith('scene-clip-'):
            crashed.append(event['id'])
            raise SimulatedProcessCrash()
        return result
    monkeypatch.setattr(env.spaces,'record_usage',settle_then_crash)
    run=begin(env)
    tick_to_crash(env)
    before=env.adapter._read(OWNER,run['id'])
    clip=before['clips'][0]
    assert len(crashed)==1 and clip['key']==crashed[0]
    assert clip['attempted'] and clip['render_id'] and clip['status']=='pending'
    assert not clip['samples'] and len(env.renderer.submissions)==len(before['previews'])+1
    rendered_count=len(env.renderer.submissions)
    saved=env.spaces.get(OWNER,run['id'])
    assert saved['usage_status']['status']==('usage_unknown' if actual_render_seconds is None else 'over_budget')
    event=next(event for event in saved['usage']['events'] if event['id']==clip['key'])
    assert event['render_seconds']==actual_render_seconds
    assert next(r for r in saved['reservations'] if r['id']==clip['key'])['status']=='settled'

    restart(env)
    finish(env)
    public=env.runs.get(OWNER,run['id'])
    recovered=env.adapter._read(OWNER,run['id'])
    assert public['status']=='failed' and not public['draining']
    restored=recovered['clips'][0]
    assert restored['render_id']==clip['render_id'] and restored['status']=='completed'
    assert len(restored['samples'])==2
    for sample in restored['samples']:
        assert (env.adapter.root/'artifacts'/(sample['artifact_sha256']+'.png')).is_file()
    assert env.adapter.preview(OWNER,run['id'],restored['render_id'])[1]=='video/mp4'
    # Recovering already-paid outputs must not authorize another clip, review,
    # source repair, or whole-film export under unknown/exhausted usage.
    assert len(env.renderer.submissions)==rendered_count
    assert len(env.author.submissions)==1 and not env.reviewer.submissions
    after=env.spaces.get(OWNER,run['id'])
    assert [e for e in after['usage']['events'] if e['id']==clip['key']]==[event]
    assert not any(r['status']=='active' for r in after['reservations'])


@pytest.mark.parametrize('stage',['preview','clip','review','repair','export'])
def test_cancel_after_hold_before_dispatch_intent_releases_only_undispatched_costs(
        gated_scene_env,monkeypatch,stage):
    env=gated_scene_env
    if stage=='repair':env.reviewer.scores=[65]
    prefixes=dict(preview='scene-preview-',clip='scene-clip-',review='scene-review-',
        repair='scene-repair-',export='scene-render-')
    reserve=env.spaces.reserve
    holds=[]
    def hold_then_crash(owner,task_id,operation,**amounts):
        result=reserve(owner,task_id,operation,**amounts)
        if operation.startswith(prefixes[stage]):
            holds.append(dict(id=operation,**amounts))
            raise SimulatedProcessCrash()
        return result
    monkeypatch.setattr(env.spaces,'reserve',hold_then_crash)
    run=begin(env)
    tick_to_crash(env)
    assert len(holds)==1
    operation=holds[0]['id']
    row=env.adapter._read(OWNER,run['id'])
    if stage in {'preview','clip'}:
        item=next(item for item in row['previews']+row['clips'] if item['key']==operation)
        assert not item['attempted'] and item['render_id'] is None
    elif stage in {'review','repair'}:
        assert not row[stage]['attempted'] and row[stage]['job_id'] is None
    else:
        assert not row['render_submit_attempted'] and row['render_id'] is None
    before=env.spaces.get(OWNER,run['id'])
    assert not any(event['id']==operation for event in before['usage']['events'])
    assert next(hold for hold in before['reservations'] if hold['id']==operation)['status']=='active'
    submissions=(len(env.author.submissions),len(env.reviewer.submissions),len(env.renderer.submissions))

    restart(env)
    env.runs.cancel(OWNER,run['id'])
    finish(env)
    stopped=env.runs.get(OWNER,run['id'])
    assert stopped['status']=='cancelled' and not stopped['draining']
    assert env.adapter._read(OWNER,run['id'])['stop_proven'] is True
    after=env.spaces.get(OWNER,run['id'])
    event=next(event for event in after['usage']['events'] if event['id']==operation)
    assert event==dict(id=operation,phase=holds[0]['phase'],outcome='cancelled',
        model_calls=0,model_tokens=0,render_seconds=0)
    assert not any(hold['status']=='active' for hold in after['reservations'])
    assert after['usage_status']['used']==before['usage_status']['used']
    assert submissions==(len(env.author.submissions),len(env.reviewer.submissions),len(env.renderer.submissions))
    # A repeated cancellation neither recharges nor dispatches the reserved ID.
    usage=deepcopy(after['usage'])
    env.runs.cancel(OWNER,run['id'])
    assert env.spaces.get(OWNER,run['id'])['usage']==usage


@pytest.mark.parametrize('changed_field',['camera','world_seed'])
def test_same_content_repair_rejects_a_changed_plan_and_retains_original_samples(
        gated_scene_env,monkeypatch,changed_field):
    env=gated_scene_env;env.reviewer.scores=[65,90]
    collect=env.author.collect
    changed=[]
    def tampered(job_id,input_sha256,document):
        result=collect(job_id,input_sha256,document)
        if document['constraints'].get('scene_space_same_content_repair'):
            spatial=result['method']['scene_space']
            if changed_field=='camera':
                spatial['plan']['shots'][0]['camera']['keyframes'][0]['x']+=1
            else:
                spatial['plan']['worlds'][0]['seed']+=1
                spatial['world']=deepcopy(spatial['plan']['worlds'][0])
            changed.append(deepcopy(result))
        return result
    monkeypatch.setattr(env.author,'collect',tampered)
    run=begin(env);finish(env)
    public=env.runs.get(OWNER,run['id'])
    row=env.adapter._read(OWNER,run['id'])
    assert public['status']=='failed' and public['error']=='scene_space_repair_plan_changed'
    assert not public['draining'] and len(changed)==1
    assert row['attempt']==0 and not row['attempts']
    original=row['repair']['document']['constraints']['scene_space_same_content_repair']
    assert row['source']==original['source']
    assert row['source_sha256']==original['source_sha256']
    assert row['plan_sha256']==original['plan_sha256']
    record=env.spaces.get(OWNER,run['id'])
    assert record['frozen']['plan']==original['plan']
    assert row['repair']['result'] is None
    assert all(item['status']=='completed' for item in row['previews']+row['clips'])
    for item in row['previews']+row['clips']:
        assert env.adapter.preview(OWNER,run['id'],item['render_id'])[0]
    assert len(env.author.submissions)==2 and len(env.reviewer.submissions)==1
    assert len(env.renderer.submissions)==len(row['previews'])+len(row['clips'])
    assert all('frame_range' in r for r in env.renderer.submissions)
    # Rejected output still consumed the paid repair call and tokens exactly once.
    repairs=[event for event in record['usage']['events'] if event['phase']=='repair']
    assert len(repairs)==1 and repairs[0]['model_calls']==1 and repairs[0]['model_tokens']==1000
    assert not any(hold['status']=='active' for hold in record['reservations'])
