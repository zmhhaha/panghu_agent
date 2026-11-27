"""Explicit one-review continuation; synthetic media tests custody, not art."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agent_service.contracts import ServiceError, digest
from video_workbench.app import create_app
from video_workbench.production_run_store import ProductionRunStore
from video_workbench.scene_review_continuation import MODEL_LIMITS, MODEL_LIMIT_CAPABILITY
from test_scene_space_production import scene_env, gated_scene_env, legacy_env, finish, WorldReview, OWNER
from test_scene_source_recovery import recovery_env, request_for as source_request, raw_run, original_rows


def control_evidence(job, *, partial=False, bounded=False):
    attempt=dict(attempt_id=uuid4().hex,fence=8)
    job.update(attempt=attempt,pins=dict(profile='video.picture-learning-review.v1',profile_revision='1',
        execution_binding=dict(sha256='d'*64)),stop_proof=dict(stop_proven=True,**attempt,
            request_sha256='e'*64,result_sha256='f'*64))
    value=dict(schema='agent-model-usage-evidence.v1',job_id=job['job_id'],**attempt,
        state=job['state'],execution_binding_sha256='d'*64,stop_proven=True,all_attempts=True,
        authoritative=True,completeness='partial' if partial else 'complete',
        request_count=3 if partial else 1,input_tokens=None if partial else 500,
        output_tokens=None if partial else 300,total_tokens=None if partial else 800)
    if bounded:value.update(model_limits=deepcopy(MODEL_LIMITS),model_limits_verified=True)
    return dict(value,sha256=digest(value))


class BoundedReview(WorldReview):
    def preflight_model_limits(self,deadline,limits):
        assert deadline>time.time() and limits==MODEL_LIMITS
        return dict(ready=True,model_request_limits=deepcopy(MODEL_LIMIT_CAPABILITY))

    def prepare_submission(self,operation_id,document,deadline_at,refs,*,model_limits=None):
        assert model_limits==MODEL_LIMITS
        submit=super().prepare_submission(operation_id,document,deadline_at,refs)
        def call():
            try:submit()
            finally:
                job=self.jobs.get(operation_id)
                if job:
                    job['model_limits']=deepcopy(model_limits)
                    job['usage_evidence']=control_evidence(job,bounded=True)
            return deepcopy(job)
        return call


def seed_lineage(env,parent):
    planner,failed_resource,resource=(str(uuid4()) for _ in range(3))
    original=dict(id=str(uuid4()),owner=OWNER,project_id=parent['project_id'],version_id='failed-plan',
        status='failed',job_id=planner,resource_planning=dict(status='failed',job_id=failed_resource,stop_proven=True))
    operation=dict(id=str(uuid4()),owner=OWNER,project_id=parent['project_id'],version_id=parent['joint_plan_version_id'],
        status='completed',job_id=planner,resource_planning=dict(status='completed',job_id=resource,stop_proven=True),
        resource_recovery=dict(source_operation_id=original['id'],source_operation_sha256=digest(original),
            source_planner_job_id=planner,source_resource_job_id=failed_resource))
    version=dict(id=parent['joint_plan_version_id'],operation_id=operation['id'])
    with env.store.transaction() as db:
        for item in (original,operation):
            db.execute('INSERT INTO joint_plan_operations VALUES (?,?,?,?,?,?)',
                (item['id'],OWNER,parent['project_id'],item['id'],digest(item),json.dumps(item)))
        db.execute('INSERT INTO joint_plan_versions VALUES (?,?,?,?,?)',
            (version['id'],OWNER,parent['project_id'],operation['id'],json.dumps(version)))
    return planner,failed_resource,resource


@pytest.fixture
def continuation_env(recovery_env):
    env=recovery_env
    parent=env.runs.recover_scene_source(OWNER,env.source['id'],source_request(env),'original-source-recovery')
    env.reviewer.active=True
    for _ in range(40):
        env.runs.tick();scene=env.adapter._read(OWNER,parent['id'])
        if scene.get('review') and scene['review'].get('job_id'):break
    assert scene['review']['job_id'] and not env.renderer.submissions[-1].get('export')
    old=env.reviewer._job(scene['review']['job_id'])
    old['usage']=dict(request_count=3,completeness='partial')
    env.runs.cancel(OWNER,parent['id']);finish(env)
    old['usage_evidence']=control_evidence(old,partial=True)
    assert env.runs.get(OWNER,parent['id'])['status']=='cancelled'
    review=BoundedReview();review.__dict__.update(env.reviewer.__dict__);review.active=False
    env.reviewer=review;env.adapter.reviewer=review
    env.parent=raw_run(env,parent['id']);env.parent_scene=env.adapter._read(OWNER,parent['id'])
    env.parent_rows=original_rows(env,parent['id'])
    env.lineage=seed_lineage(env,env.parent)
    env.preview_count=len(env.renderer.submissions)
    return env


def request_for(env):
    parent=raw_run(env,env.parent['id']);row=env.adapter._read(OWNER,parent['id'])
    record=env.spaces.get(OWNER,parent['id']);task={k:v for k,v in record.items() if k!='usage_status'}
    return dict(expected_revision=env.store.project(OWNER,parent['project_id'])['revision'],
        expected_source_run_sha256=digest(parent),expected_scene_sha256=digest(row),
        expected_task_sha256=digest(task),expected_usage_sha256=task['usage']['sha256'],
        expected_author_job_id=row['author_job_id'],expected_author_input_sha256=row['author_input_sha256'],
        expected_author_result_sha256=digest(row['author_result']),expected_source_sha256=row['source_sha256'],
        expected_plan_sha256=row['plan_sha256'],expected_configuration_sha256=row['configuration']['configuration_sha256'],
        expected_usage_evidence_sha256=env.author_job['usage_evidence']['sha256'],
        expected_review_job_id=row['review']['job_id'],expected_review_input_sha256=row['review']['input_sha256'],
        expected_review_usage_evidence_sha256=env.reviewer._job(row['review']['job_id'])['usage_evidence']['sha256'],
        expected_lineage_job_ids=sorted([*env.lineage,row['author_job_id'],row['review']['job_id']]),
        execution_deadline_at=time.time()+1800,accept_legacy_usage_unknown=True,
        accept_original_token_cap_unproven=True,allow_export_with_legacy_unknown=True)


def test_once_continuation_keeps_legacy_unknown_reuses_author_and_previews_and_exports(continuation_env):
    env=continuation_env;body=request_for(env)
    new=env.runs.continue_scene_review(OWNER,env.parent['id'],body,'review-continuation')
    assert new['scene_review_continuation']['limits']['author_jobs']==0
    assert original_rows(env,env.parent['id'])==env.parent_rows
    finish(env)
    public=env.runs.get(OWNER,new['id']);assert public['status']=='completed'
    row=env.adapter._read(OWNER,new['id']);record=env.spaces.get(OWNER,new['id'])
    parent=env.spaces.get(OWNER,env.parent['id'])
    assert len(env.author.submissions)==1 and len(env.reviewer.submissions)==2
    assert len(env.renderer.submissions)==env.preview_count+1
    assert row['author_result']==env.parent_scene['author_result'] and row['source']==env.parent_scene['source']
    assert row['plan_sha256']==env.parent_scene['plan_sha256']
    assert row['configuration']==env.parent_scene['configuration']
    assert row['deadline_at']==body['execution_deadline_at']
    assert row['previews']==env.parent_scene['previews'] and row['clips']==env.parent_scene['clips']
    assert record['usage']==parent['usage'] and record['frozen']==parent['frozen']
    assert record['usage_status']['status']=='usage_unknown'
    assert record['usage_status']['used']['model_tokens'] is None
    assert record['usage_status']['original_total_token_cap_proven'] is False
    assert record['continuation_usage_status']['status']=='within_budget'
    assert record['continuation_usage_status']['used']['model_tokens']==800
    assert public['scene_space']['accounting']['cumulative']['status']=='usage_unknown'
    assert public['scene_space']['repair_limit']==0
    assert original_rows(env,env.parent['id'])==env.parent_rows
    assert original_rows(env,env.source['id'])==env.source_rows
    assert env.runs.continue_scene_review(OWNER,env.parent['id'],body,'review-continuation')['id']==new['id']
    with pytest.raises(ServiceError,match='already_exists'):
        env.runs.continue_scene_review(OWNER,env.parent['id'],body,'another-key')


def test_atomic_same_key_and_restart_after_task_insertion_never_authors_or_reviews_twice(continuation_env,monkeypatch):
    env=continuation_env;body=request_for(env)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:env.runs.continue_scene_review(OWNER,env.parent['id'],body,'same-once'),range(2)))
    assert results[0]['id']==results[1]['id'];new=results[0]
    write=env.adapter._write
    class Crash(BaseException):pass
    def crash(value,*,insert=False):
        if insert and value['run_id']==new['id']:raise Crash()
        return write(value,insert=insert)
    monkeypatch.setattr(env.adapter,'_write',crash)
    with pytest.raises(Crash):env.runs.tick()
    ledger=deepcopy(env.spaces.get(OWNER,new['id'])['continuation_usage'])
    monkeypatch.setattr(env.adapter,'_write',write)
    env.runs=ProductionRunStore(env.store,env.joint,env.motion,env.production,
        scene_space_adapter=env.adapter,sequence_store=env.sequence)
    finish(env)
    assert env.runs.get(OWNER,new['id'])['status']=='completed'
    assert len(env.author.submissions)==1 and len(env.reviewer.submissions)==2
    assert ledger['events']==[] and original_rows(env,env.parent['id'])==env.parent_rows


@pytest.mark.parametrize('bad',['owner','ack','extended','expired','parent_hash','task_hash','author_proof',
    'review_proof','lineage','unknown_extra','preview','capability'])
def test_rejects_invalid_or_unaccepted_exception_before_intent_or_model(continuation_env,monkeypatch,bad):
    env=continuation_env;body=request_for(env);owner=OWNER
    if bad=='owner':owner='different-owner'
    elif bad=='ack':body['allow_export_with_legacy_unknown']=False
    elif bad=='extended':body['execution_deadline_at']=time.time()+7200
    elif bad=='expired':body['execution_deadline_at']=time.time()-1
    elif bad=='parent_hash':body['expected_source_run_sha256']='f'*64
    elif bad=='task_hash':body['expected_task_sha256']='f'*64
    elif bad=='author_proof':body['expected_usage_evidence_sha256']='f'*64
    elif bad=='review_proof':body['expected_review_usage_evidence_sha256']='f'*64
    elif bad=='lineage':body['expected_lineage_job_ids']=body['expected_lineage_job_ids'][1:]
    elif bad=='unknown_extra':
        # Even an independently incurred new unknown is not part of the named legacy exception.
        task=env.spaces.get(OWNER,env.parent['id'])
        env.spaces.record_usage(OWNER,env.parent['id'],id='another-unknown',phase='collaboration',
            outcome='failed',model_calls=1,model_tokens=None,render_seconds=0)
        body=request_for(env)
    elif bad=='preview':env.renderer.artifacts[env.parent_scene['previews'][0]['render_id']]=(b'changed','image/png')
    elif bad=='capability':
        monkeypatch.setattr(env.reviewer,'preflight_model_limits',lambda *a:(_ for _ in ()).throw(
            ServiceError('animation_model_request_limits_unavailable',503)))
    with pytest.raises(ServiceError):env.runs.continue_scene_review(owner,env.parent['id'],body,'rejected')
    with env.store.transaction() as db:assert db.execute('SELECT count(*) FROM production_runs').fetchone()[0]==2
    assert len(env.author.submissions)==1 and len(env.reviewer.submissions)==1


@pytest.mark.parametrize('failure',['new_unknown','unverified_limits','rejected_score','over_token_threshold','worker_failed'])
def test_new_unsettled_usage_or_quality_failure_stops_without_repair_or_export(continuation_env,monkeypatch,failure):
    env=continuation_env;prepare=env.reviewer.prepare_submission
    if failure=='rejected_score':env.reviewer.scores=[20]
    def changed(*args,**kwargs):
        call=prepare(*args,**kwargs)
        def submit():
            job=call();actual=env.reviewer._job(job['job_id']);evidence=actual['usage_evidence']
            if failure=='new_unknown':evidence['completeness']='partial'
            elif failure=='unverified_limits':evidence['model_limits_verified']=False
            elif failure=='over_token_threshold':
                evidence.update(input_tokens=300000,output_tokens=300,total_tokens=300300)
            elif failure=='worker_failed':
                actual['state']='failed';evidence['state']='failed'
            evidence['sha256']=digest({k:v for k,v in evidence.items() if k!='sha256'})
            return deepcopy(actual)
        return submit
    monkeypatch.setattr(env.reviewer,'prepare_submission',changed)
    new=env.runs.continue_scene_review(OWNER,env.parent['id'],request_for(env),'one-failing-review')
    finish(env)
    public=env.runs.get(OWNER,new['id'])
    assert public['status']=='failed' and not public.get('draining')
    assert env.adapter._read(OWNER,new['id'])['stop_proven'] is True
    assert len(env.author.submissions)==1 and len(env.reviewer.submissions)==2
    assert len(env.renderer.submissions)==env.preview_count
    task=env.spaces.get(OWNER,new['id'])
    assert task['usage_status']['status']=='usage_unknown'
    if failure in {'new_unknown','unverified_limits'}:
        assert task['continuation_usage_status']['status']=='usage_unknown'
    elif failure=='over_token_threshold':assert task['continuation_usage_status']['status']=='over_budget'
    before=deepcopy(task['continuation_usage'])
    env.runs.cancel(OWNER,new['id']);env.runs.cancel(OWNER,new['id'])
    assert env.spaces.get(OWNER,new['id'])['continuation_usage']==before
    assert original_rows(env,env.parent['id'])==env.parent_rows


def test_unknown_submission_reconciles_same_review_and_expired_grant_cannot_restart(continuation_env):
    env=continuation_env;env.reviewer.lose_submit=True
    new=env.runs.continue_scene_review(OWNER,env.parent['id'],request_for(env),'lost-submit')
    finish(env)
    assert env.runs.get(OWNER,new['id'])['status']=='completed'
    assert len(env.reviewer.submissions)==2 and env.reviewer.lookups
    with pytest.raises(ServiceError):
        env.runs.continue_scene_review(OWNER,new['id'],request_for(env),'recursive')


def test_http_requires_owner_csrf_explicit_ack_and_idempotency(continuation_env,monkeypatch):
    env=continuation_env;monkeypatch.setenv('SERVICE_AUTH_ISSUER','https://issuer.test')
    app=create_app(db_path=env.store.path,local_mode=False,background=False,
        production_runs_enabled=True,joint_plan_enabled=True,scene_space_adapter=env.adapter,trusted_hosts={'testserver'})
    path='/api/production-runs/'+env.parent['id']+'/scene-review-continuation'
    with TestClient(app,base_url='https://testserver',headers={'Origin':'https://testserver'}) as client:
        client.headers['x-forwarded-user']=OWNER;body=request_for(env)
        assert client.post(path,json=body,headers={'Idempotency-Key':'http-review-once'}).status_code==403
        session=client.get('/api/session').json();headers={'X-CSRF-Token':session['csrf_token'],'Idempotency-Key':'http-review-once'}
        missing=dict(body);missing.pop('allow_export_with_legacy_unknown')
        assert client.post(path,json=missing,headers=headers).status_code==422
        response=client.post(path,json=body,headers=headers);assert response.status_code==202,response.text
        assert client.post(path,json=body,headers=headers).json()['id']==response.json()['id']
        altered=dict(body,execution_deadline_at=body['execution_deadline_at']+1)
        assert client.post(path,json=altered,headers=headers).status_code==409
