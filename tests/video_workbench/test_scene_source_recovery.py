"""Source-only recovery charges the real stopped author, without authoring again."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agent_service.contracts import ServiceError,digest
from video_workbench.app import create_app
from video_workbench.production_run_store import ProductionRunStore
from video_workbench.scene_space_production import SceneSpaceProductionAdapter
from test_scene_space_production import scene_env,gated_scene_env,legacy_env,begin,finish,OWNER


def raw_run(env,run_id):
    with env.store.transaction() as db:return env.runs._read(db,run_id,OWNER)


def original_rows(env,run_id):
    with env.store.transaction() as db:
        run=db.execute('SELECT document FROM production_runs WHERE id=?',(run_id,)).fetchone()[0]
    with env.adapter._connect() as db:
        scene=bytes(db.execute('SELECT document FROM scene_space_productions WHERE owner=? AND run_id=?',
            (OWNER,run_id)).fetchone()[0])
    with env.spaces._db() as db:
        task=db.execute('SELECT document FROM scene_tasks WHERE owner=? AND task_id=?',(OWNER,run_id)).fetchone()[0]
    return run,scene,task


def evidence_for(job,*,calls=4,tokens=120668):
    evidence=dict(schema='agent-model-usage-evidence.v1',job_id=job['job_id'],
        attempt_id=job['attempt']['attempt_id'],fence=job['attempt']['fence'],state='completed',
        execution_binding_sha256=job['pins']['execution_binding']['sha256'],
        stop_proven=True,all_attempts=True,authoritative=True,completeness='complete',
        request_count=calls,input_tokens=tokens-20703,output_tokens=20703,total_tokens=tokens,
        cache_read_tokens=40960,cache_write_tokens=0,cost_status='unknown',reported_cost=None,requests=[])
    return dict(evidence,sha256=digest(evidence))


@pytest.fixture
def recovery_env(gated_scene_env,monkeypatch):
    env=gated_scene_env
    submit=env.author.prepare_submission
    def unknown(*args,**kwargs):
        admit=submit(*args,**kwargs)
        def call():
            job=admit()
            env.author._job(job['job_id'])['usage']=dict(request_count=1,completeness='partial')
            return deepcopy(env.author._job(job['job_id']))
        return call
    monkeypatch.setattr(env.author,'prepare_submission',unknown)
    document=env.adapter._render_document
    def unavailable(*args,**kwargs):raise ServiceError('scene_appearance_usage_unknown',409)
    monkeypatch.setattr(env.adapter,'_render_document',unavailable)
    public=begin(env,key='original-source-failed');finish(env)
    source=raw_run(env,public['id'])
    assert source['status']=='failed' and source['error']=='scene_appearance_usage_unknown' and not source['draining']
    assert len(env.author.submissions)==1 and not env.reviewer.submissions and not env.renderer.submissions
    monkeypatch.setattr(env.adapter,'_render_document',document)
    monkeypatch.setattr(env.author,'prepare_submission',submit)
    row=env.adapter._read(OWNER,source['id'])
    job=env.author._job(row['author_job_id'])
    attempt=dict(attempt_id=uuid4().hex,fence=7)
    job.update(attempt=attempt,pins=dict(profile=env.author.profile,profile_revision=env.author.profile_revision,
        execution_binding=dict(sha256='c'*64)),
        stop_proof=dict(stop_proven=True,**attempt,request_sha256='a'*64,result_sha256='b'*64))
    job['usage_evidence']=evidence_for(job)
    env.source=source;env.author_job=job
    env.source_rows=original_rows(env,source['id'])
    return env


def request_for(env):
    run=raw_run(env,env.source['id']);row=env.adapter._read(OWNER,run['id'])
    task=env.spaces.get(OWNER,run['id'])
    return dict(expected_revision=env.store.project(OWNER,run['project_id'])['revision'],
        expected_source_run_sha256=digest(run),expected_scene_sha256=digest(row),
        expected_usage_sha256=task['usage']['sha256'],expected_author_job_id=row['author_job_id'],
        expected_author_input_sha256=row['author_input_sha256'],expected_author_result_sha256=digest(row['author_result']),
        expected_source_sha256=row['source_sha256'],expected_plan_sha256=row['plan_sha256'],
        expected_configuration_sha256=row['configuration']['configuration_sha256'],
        expected_usage_evidence_sha256=env.author_job['usage_evidence']['sha256'],
        execution_deadline_at=min(time.time()+600,row['deadline_at']))


def test_linked_recovery_reuses_exact_source_author_and_actual_spending_preserves_failed_rows(recovery_env):
    env=recovery_env;request=request_for(env)
    new=env.runs.recover_scene_source(OWNER,env.source['id'],request,'recover-source-once')
    assert new['id']!=env.source['id'] and new['scene_source_recovery']['extra_author_jobs']==0
    assert original_rows(env,env.source['id'])==env.source_rows
    # A restart between main-DB intent and scene/task installation resumes the
    # durable same new ID; it never calls a fresh author/upload.
    env.runs=ProductionRunStore(env.store,env.joint,env.motion,env.production,
        scene_space_adapter=env.adapter,sequence_store=env.sequence)
    finish(env)
    assert env.runs.get(OWNER,new['id'])['status']=='completed'
    row=env.adapter._read(OWNER,new['id']);old=env.adapter._read(OWNER,env.source['id'])
    assert row['author_job_id']==old['author_job_id'] and row['author_operation_id']==old['author_operation_id']
    assert row['author_document']==old['author_document'] and row['author_result']==old['author_result']
    assert row['source']==old['source'] and row['plan_sha256']==old['plan_sha256']
    assert row['configuration']==old['configuration'] and row['deadline_at']==request['execution_deadline_at']
    assert len(env.author.submissions)==len(env.author.uploads)==1 and len(env.reviewer.submissions)==1
    task=env.spaces.get(OWNER,new['id']);source_task=env.spaces.get(OWNER,env.source['id'])
    assert task['frozen']==source_task['frozen']
    assert task['usage']['baseline']==source_task['usage']['baseline']
    assert task['usage_status']['used']['model_calls']==5
    assert task['usage_status']['used']['model_tokens']==121468
    assert next(e for e in source_task['usage']['events'] if e['phase']=='generation')['model_tokens'] is None
    assert original_rows(env,env.source['id'])==env.source_rows
    assert env.runs.recover_scene_source(OWNER,env.source['id'],request,'recover-source-once')['id']==new['id']
    with pytest.raises(ServiceError,match='already_exists'):
        env.runs.recover_scene_source(OWNER,env.source['id'],request,'different-key')


def test_same_key_concurrent_recovery_creates_one_linked_run(recovery_env):
    env=recovery_env;request=request_for(env)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:env.runs.recover_scene_source(OWNER,env.source['id'],request,'concurrent'),range(2)))
    assert results[0]['id']==results[1]['id']
    with env.store.transaction() as db:assert db.execute('SELECT count(*) FROM production_runs').fetchone()[0]==2
    assert original_rows(env,env.source['id'])==env.source_rows
    assert len(env.author.submissions)==1


def test_crash_after_linked_task_before_scene_insert_reuses_same_usage_and_ids(recovery_env,monkeypatch):
    env=recovery_env;request=request_for(env)
    new=env.runs.recover_scene_source(OWNER,env.source['id'],request,'task-before-scene-crash')
    write=env.adapter._write
    class ProcessCrash(BaseException):pass
    def crash(value,*,insert=False):
        if insert and value['run_id']==new['id']:raise ProcessCrash()
        return write(value,insert=insert)
    monkeypatch.setattr(env.adapter,'_write',crash)
    with pytest.raises(ProcessCrash):env.runs.tick()
    usage=deepcopy(env.spaces.get(OWNER,new['id'])['usage'])
    assert len([e for e in usage['events'] if e['phase']=='generation'])==1
    assert not env.renderer.submissions and len(env.author.submissions)==1
    monkeypatch.setattr(env.adapter,'_write',write)
    finish(env)
    assert env.runs.get(OWNER,new['id'])['status']=='completed'
    result=env.spaces.get(OWNER,new['id'])['usage']
    assert [e for e in result['events'] if e['phase']=='generation']==[
        e for e in usage['events'] if e['phase']=='generation']
    assert len(env.author.submissions)==len(env.author.uploads)==1
    assert original_rows(env,env.source['id'])==env.source_rows


@pytest.mark.parametrize('bad',['owner','source_hash','evidence_hash','partial','untrusted','running','fence','expired','extended','budget'])
def test_rejects_untrusted_stale_running_or_unaffordable_recovery_without_writes(recovery_env,bad):
    env=recovery_env;request=request_for(env);owner=OWNER
    if bad=='owner':owner='another-owner'
    elif bad=='source_hash':request['expected_source_sha256']='f'*64
    elif bad=='evidence_hash':request['expected_usage_evidence_sha256']='f'*64
    elif bad=='partial':env.author_job['usage_evidence']['completeness']='partial'
    elif bad=='untrusted':env.author_job['usage_evidence']['authoritative']=False
    elif bad=='running':env.author_job.update(state='running',stop_proven=False)
    elif bad=='fence':env.author_job['stop_proof']['fence']+=1
    elif bad=='expired':request['execution_deadline_at']=time.time()-1
    elif bad=='extended':request['execution_deadline_at']=env.adapter._read(OWNER,env.source['id'])['deadline_at']+1
    elif bad=='budget':env.author_job['usage_evidence']=evidence_for(env.author_job,tokens=10**9)
    if bad in {'partial','untrusted','budget'}:
        evidence=env.author_job['usage_evidence']
        evidence['sha256']=digest({k:v for k,v in evidence.items() if k!='sha256'})
        request['expected_usage_evidence_sha256']=evidence['sha256']
    with pytest.raises(ServiceError):env.runs.recover_scene_source(owner,env.source['id'],request,'rejected')
    with env.store.transaction() as db:assert db.execute('SELECT count(*) FROM production_runs').fetchone()[0]==1
    assert original_rows(env,env.source['id'])==env.source_rows
    assert len(env.author.submissions)==1 and not env.renderer.submissions and not env.reviewer.submissions


def test_second_cas_rejects_project_revision_changed_during_actual_get(recovery_env,monkeypatch):
    env=recovery_env;request=request_for(env);poll=env.author.poll
    def changed(job_id):
        job=poll(job_id)
        with env.store.transaction() as db:
            project=env.store._project(db,OWNER,env.source['project_id']);env.store._save(db,project)
        return job
    monkeypatch.setattr(env.author,'poll',changed)
    with pytest.raises(ServiceError):env.runs.recover_scene_source(OWNER,env.source['id'],request,'revision-race')
    assert original_rows(env,env.source['id'])==env.source_rows
    with env.store.transaction() as db:assert db.execute('SELECT count(*) FROM production_runs').fetchone()[0]==1


def test_unknown_downstream_submission_is_not_permission_for_fresh_render(recovery_env):
    env=recovery_env;row=env.adapter._read(OWNER,env.source['id'])
    row['previews'][0].update(attempted=True,render_id=None)
    env.adapter._write(row)
    request=request_for(env)
    with pytest.raises(ServiceError,match='unavailable'):
        env.runs.recover_scene_source(OWNER,env.source['id'],request,'unknown-preview')
    assert not env.renderer.submissions and len(env.author.submissions)==1


def test_http_recovery_uses_existing_owner_csrf_and_idempotency_boundary(recovery_env,monkeypatch):
    env=recovery_env
    monkeypatch.setenv('SERVICE_AUTH_ISSUER','https://issuer.test')
    app=create_app(db_path=env.store.path,local_mode=False,background=False,
        production_runs_enabled=True,joint_plan_enabled=True,scene_space_adapter=env.adapter,
        trusted_hosts={'testserver'})
    path='/api/production-runs/'+env.source['id']+'/scene-source-recovery'
    with TestClient(app,base_url='https://testserver',headers={'Origin':'https://testserver'}) as client:
        client.headers['x-forwarded-user']=OWNER
        body=request_for(env)
        assert client.post(path,json=body,headers={'Idempotency-Key':'http-once'}).status_code==403
        session=client.get('/api/session').json()
        headers={'X-CSRF-Token':session['csrf_token'],'Idempotency-Key':'http-once'}
        response=client.post(path,json=body,headers=headers)
        assert response.status_code==202,response.text
        assert client.post(path,json=body,headers=headers).json()['id']==response.json()['id']
        altered=dict(body,expected_plan_sha256='f'*64)
        assert client.post(path,json=altered,headers=headers).status_code==409
