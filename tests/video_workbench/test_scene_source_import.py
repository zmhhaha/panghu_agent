"""Same source/plan with a fresh PNG/v2 execution; synthetic custody, not art."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import time

import pytest
from fastapi.testclient import TestClient

from agent_service.contracts import ServiceError,digest
from video_workbench.app import create_app
from video_workbench.production_run_store import ProductionRunStore
from video_workbench.scene_source_import import MODEL_LIMITS,task_document
from test_scene_space_production import scene_env,gated_scene_env,legacy_env,finish,WorldReview,OWNER
from test_scene_source_recovery import recovery_env,raw_run,original_rows
from test_scene_review_continuation import continuation_env,request_for as continuation_request,control_evidence
from test_scene_review_reliability import LosslessRenderer,ClassifiedReview

class BoundedClassifiedReview(ClassifiedReview):
    def preflight_model_limits(self,deadline,limits):
        assert deadline>time.time() and limits==MODEL_LIMITS
        return {'ready':True}

    def prepare_submission(self,operation_id,document,deadline_at,refs,*,model_limits=None):
        assert model_limits==MODEL_LIMITS
        original=WorldReview.prepare_submission(self,operation_id,document,deadline_at,refs)
        def call():
            original();job=self.jobs[operation_id]
            job['model_limits']=deepcopy(model_limits)
            job['usage_evidence']=control_evidence(job,bounded=True)
            job['pins']['profile']=self.profile
            return deepcopy(job)
        return call

@pytest.fixture
def import_env(continuation_env,monkeypatch):
    env=continuation_env
    original=env.reviewer.collect
    def old_quality_failure(*args,**kwargs):
        result=original(*args,**kwargs);result['review'].update(score=83,issues=['Historical v1 issue.'])
        return result
    monkeypatch.setattr(env.reviewer,'collect',old_quality_failure)
    old=env.runs.continue_scene_review(OWNER,env.parent['id'],continuation_request(env),'old-once-continuation')
    finish(env);assert env.runs.get(OWNER,old['id'])['error']=='scene_space_preview_quality_not_met'
    env.import_parent=raw_run(env,old['id']);env.import_parent_scene=env.adapter._read(OWNER,old['id'])
    env.import_parent_rows=original_rows(env,old['id'])
    assert env.import_parent_scene['review']['result']['review']['score']==83
    renderer=LosslessRenderer();renderer.__dict__.update(env.renderer.__dict__)
    env.renderer=renderer;env.adapter.renderer=renderer
    env.classified=BoundedClassifiedReview();env.adapter.structured_reviewer=env.classified
    env.adapter.freeze_configuration=env.current_scene_configuration
    env.before_renders=len(renderer.submissions)
    return env

def request_for(env):
    draft=env.runs.source_import_preflight(OWNER,env.import_parent['id'])
    assert draft['model_requests']==draft['jobs_created']==0 and draft['grant_created'] is False
    return dict(draft['request'],authorization_sha256='a'*64,execution_deadline_at=time.time()+1800,
        accept_parent_cumulative_usage_unknown=True,accept_original_token_cap_unproven=True,
        allow_export_with_parent_unknown=True)

def test_new_run_preserves_old83_unknown_and_full_plan_reuses_author_then_fresh_png_v2_export(import_env):
    env=import_env;body=request_for(env)
    created=env.runs.import_scene_source(OWNER,env.import_parent['id'],body,'fresh-same-source')
    assert not env.classified.submissions
    finish(env);public=env.runs.get(OWNER,created['id']);assert public['status']=='completed',public.get('error')
    scene=env.adapter._read(OWNER,created['id']);task=env.spaces.get(OWNER,created['id'])
    parent=env.spaces.get(OWNER,env.import_parent['id'])
    assert scene['source']==env.import_parent_scene['source'] and task['frozen']==parent['frozen']
    assert scene['author_document']==env.import_parent_scene['author_document']
    assert scene['author_result']==env.import_parent_scene['author_result']
    assert scene['configuration']!=env.import_parent_scene['configuration']
    assert scene['configuration']['review_profile']==env.classified.profile
    assert scene['configuration']['max_pre_export_repairs']==0 and scene['repair'] is None
    assert len(env.author.submissions)==1 and len(env.classified.submissions)==1
    assert scene['review']['job_id'] not in scene['source_import']['lineage_job_ids']
    assert len(set(scene['source_import']['lineage_job_ids'])|{scene['review']['job_id']})==7
    assert task['usage_status']['status']=='usage_unknown' and task['usage_status']['used']['model_tokens'] is None
    assert task['source_import_execution_status']['status']=='within_budget'
    assert task['source_import_execution_status']['used']['model_tokens']==800
    assert task['usage']['limits']['render_seconds']==600
    assert not task['usage']['history'] and all(e['phase']!='generation' for e in task['usage']['events'])
    renders=env.renderer.submissions[env.before_renders:]
    assert len([r for r in renders if 'frame_range' not in r])==1
    assert all(r['source']==scene['source'] and r['scene_space']['plan_sha256']==scene['plan_sha256'] for r in renders)
    binding=renders[-1]['document']['constraints']['scene_review_binding']
    assert binding['review_job_id']==scene['review']['job_id'] and binding['decision_sha256']==digest(scene['review']['decision'])
    document,files=env.classified.uploads[0]
    assert all(files[f['file_name']].startswith(b'\x89PNG') for f in document['observation']['frames'])
    assert original_rows(env,env.import_parent['id'])==env.import_parent_rows
    assert env.runs.import_scene_source(OWNER,env.import_parent['id'],body,'fresh-same-source')['id']==created['id']
    with pytest.raises(ServiceError,match='already_exists'):
        env.runs.import_scene_source(OWNER,env.import_parent['id'],body,'another-import-key')

@pytest.mark.parametrize('finding',['violation','unresolved','low_score','stop','new_unknown','unverified_limits','over_budget'])
def test_new_gate_or_new_unknown_stops_without_repair_author_or_export(import_env,monkeypatch,finding):
    env=import_env
    if finding in {'violation','unresolved'}:env.classified.finding=finding
    elif finding=='low_score':env.classified.score=79
    elif finding=='stop':env.classified.recommend_stop=True
    else:
        original=env.classified.prepare_submission
        def changed(*args,**kwargs):
            submit=original(*args,**kwargs)
            def call():
                job=submit();actual=env.classified._job(job['job_id']);evidence=actual['usage_evidence']
                if finding=='new_unknown':evidence['completeness']='partial'
                elif finding=='unverified_limits':evidence['model_limits_verified']=False
                else:evidence.update(input_tokens=300000,output_tokens=300,total_tokens=300300)
                evidence['sha256']=digest({k:v for k,v in evidence.items() if k!='sha256'})
                return deepcopy(actual)
            return call
        monkeypatch.setattr(env.classified,'prepare_submission',changed)
    created=env.runs.import_scene_source(OWNER,env.import_parent['id'],request_for(env),'one-failing-v2')
    finish(env);public=env.runs.get(OWNER,created['id'])
    assert public['status']=='failed' and not public.get('draining')
    assert not any('frame_range' not in r for r in env.renderer.submissions[env.before_renders:])
    assert len(env.author.submissions)==len(env.classified.submissions)==1
    row=env.adapter._read(OWNER,created['id']);assert row['repair'] is None and row['stop_proven'] is True
    if finding in {'new_unknown','unverified_limits'}:
        assert env.spaces.get(OWNER,created['id'])['source_import_execution_status']['status']=='usage_unknown'
    assert original_rows(env,env.import_parent['id'])==env.import_parent_rows

@pytest.mark.parametrize('bad',['owner','ack','parent_run','parent_scene','parent_task','parent_usage',
    'source','plan','old_configuration','configuration','author_usage','lineage','expired','extended'])
def test_invalid_or_unaccepted_import_rejected_before_new_intent_or_paid_call(import_env,bad):
    env=import_env;body=request_for(env);owner=OWNER
    mapping=dict(parent_run='expected_source_run_sha256',parent_scene='expected_scene_sha256',
        parent_task='expected_task_sha256',parent_usage='expected_parent_usage_sha256',source='expected_source_sha256',
        plan='expected_plan_sha256',old_configuration='expected_parent_configuration_sha256',
        configuration='expected_configuration_sha256',author_usage='expected_usage_evidence_sha256')
    if bad in mapping:body[mapping[bad]]='f'*64
    elif bad=='owner':owner='other-owner'
    elif bad=='ack':body['allow_export_with_parent_unknown']=False
    elif bad=='lineage':body['expected_lineage_job_ids']=body['expected_lineage_job_ids'][:-1]
    elif bad=='expired':body['execution_deadline_at']=time.time()-1
    elif bad=='extended':body['execution_deadline_at']=time.time()+7200
    with pytest.raises(ServiceError):env.runs.import_scene_source(owner,env.import_parent['id'],body,'rejected-import')
    assert not env.classified.submissions and len(env.author.submissions)==1
    with env.store.transaction() as db:assert db.execute('SELECT count(*) FROM production_runs').fetchone()[0]==3

def test_same_key_concurrency_crash_and_restart_cannot_import_or_review_twice(import_env,monkeypatch):
    env=import_env;body=request_for(env)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:env.runs.import_scene_source(OWNER,env.import_parent['id'],body,'same-import-once'),range(2)))
    assert results[0]['id']==results[1]['id'];created=results[0]
    original=env.adapter._write
    class Crash(BaseException):pass
    def fail(value,*,insert=False):
        if insert and value['run_id']==created['id']:raise Crash()
        return original(value,insert=insert)
    monkeypatch.setattr(env.adapter,'_write',fail)
    with pytest.raises(Crash):env.runs.tick()
    before=deepcopy(env.spaces.get(OWNER,created['id'])['usage'])
    monkeypatch.setattr(env.adapter,'_write',original)
    env.runs=ProductionRunStore(env.store,env.joint,env.motion,env.production,
        scene_space_adapter=env.adapter,sequence_store=env.sequence)
    finish(env);assert env.runs.get(OWNER,created['id'])['status']=='completed'
    assert before['events']==[] and len(env.author.submissions)==len(env.classified.submissions)==1
    with pytest.raises(ServiceError):
        env.spaces.retry(OWNER,created['id'],env.adapter._read(OWNER,created['id'])['plan_sha256'])

def test_http_owner_csrf_ack_and_idempotency_and_readonly_preflight(import_env,monkeypatch):
    env=import_env;monkeypatch.setenv('SERVICE_AUTH_ISSUER','https://issuer.test')
    app=create_app(db_path=env.store.path,local_mode=False,background=False,production_runs_enabled=True,
        joint_plan_enabled=True,scene_space_adapter=env.adapter,trusted_hosts={'testserver'})
    path='/api/production-runs/'+env.import_parent['id']+'/scene-source-import'
    with TestClient(app,base_url='https://testserver',headers={'Origin':'https://testserver'}) as client:
        client.headers['x-forwarded-user']=OWNER;body=request_for(env)
        assert client.get(path).status_code==200 and not env.classified.submissions
        assert client.post(path,json=body,headers={'Idempotency-Key':'http-import-once'}).status_code==403
        session=client.get('/api/session').json();headers={'X-CSRF-Token':session['csrf_token'],'Idempotency-Key':'http-import-once'}
        missing=dict(body);missing.pop('allow_export_with_parent_unknown')
        assert client.post(path,json=missing,headers=headers).status_code==422
        with env.store.transaction() as db:before=db.execute('SELECT COUNT(*) FROM production_runs').fetchone()[0]
        response=client.post(path,json=body,headers=headers);assert response.status_code==409,response.text
        assert response.json()['detail']['code']=='review_standard_change_disabled_use_existing_standard'
        assert client.post(path,json=body,headers=headers).status_code==409
        with env.store.transaction() as db:assert db.execute('SELECT COUNT(*) FROM production_runs').fetchone()[0]==before
        client.headers['x-forwarded-user']='another-owner'
        assert client.get(path).status_code==404
    assert not env.classified.submissions

@pytest.mark.parametrize('history',['repair','attempts','attempt'])
def test_parent_with_unaccounted_repair_or_attempt_history_is_not_importable(import_env,history):
    env=import_env;body=request_for(env);row=env.adapter._read(OWNER,env.import_parent['id'])
    row[history]=({'attempted':True,'job_id':'an-extra-repair-job'} if history=='repair'
        else [{'review':{'job_id':'another-old-review'}}] if history=='attempts' else 1)
    env.adapter._write(row);body['expected_scene_sha256']=digest(row)
    with pytest.raises(ServiceError,match='parent_unavailable'):
        env.runs.import_scene_source(OWNER,env.import_parent['id'],body,'unaccounted-parent')
    assert not env.classified.submissions and len(env.author.submissions)==1

def test_actual_render_overrun_kept_and_blocks_new_review_or_export(import_env,monkeypatch):
    env=import_env;original=env.renderer.start
    def overrun(owner,request,key):
        result=original(owner,request,key)
        env.renderer.jobs[(owner,key)]['usage']['render_seconds']=601
        result['usage']['render_seconds']=601
        return result
    monkeypatch.setattr(env.renderer,'start',overrun)
    created=env.runs.import_scene_source(OWNER,env.import_parent['id'],request_for(env),'cpu-overrun-once')
    finish(env);assert env.runs.get(OWNER,created['id'])['status']=='failed'
    record=env.spaces.get(OWNER,created['id'])
    assert record['source_import_execution_status']['status']=='over_budget'
    assert record['source_import_execution_status']['used']['render_seconds']==601
    assert not env.classified.submissions and not any('frame_range' not in r
        for r in env.renderer.submissions[env.before_renders:])
