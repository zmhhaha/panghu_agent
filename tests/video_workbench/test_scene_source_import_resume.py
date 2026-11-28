"""Pre-model transport failure recovery: synthetic media, actual ce9 state shape."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import time

import pytest
from fastapi.testclient import TestClient

from agent_service.contracts import ServiceError,digest
from video_workbench.app import create_app
from video_workbench.production_run_store import ProductionRunStore
from video_workbench.scene_source_import import task_document
from video_workbench.scene_source_import_resume import ERROR,unused_execution,require_native_wire_budget,require_supplied_frame_receipt
from video_workbench.scene_review_continuation import MODEL_LIMIT_CAPABILITY
from video_workbench.scene_space_production import SceneSpaceClassifiedReviewGateway
from test_scene_source_import import import_env,request_for as import_request
from test_scene_review_continuation import continuation_env
from test_scene_source_recovery import recovery_env,raw_run,original_rows
from test_scene_space_production import scene_env,gated_scene_env,legacy_env,finish,OWNER

USED=49.178791753482074


@pytest.fixture
def premodeled_env(import_env,monkeypatch):
    env=import_env;original_start=env.renderer.start;seconds=iter([10.0,10.0,29.178791753482074])
    original_configuration=env.adapter.freeze_configuration
    def historical_configuration(owner,project):
        configuration=original_configuration(owner,project)
        configuration.pop('review_canonicalization_revision',None)
        configuration['configuration_sha256']=digest({key:value for key,value in configuration.items() if key!='configuration_sha256'})
        return configuration
    monkeypatch.setattr(env.adapter,'freeze_configuration',historical_configuration)
    # Preserve the fixture's full world/film while reproducing ce9's exact
    # admission shape: two static renders and the first 48-frame clip.
    import video_workbench.scene_space_production as production
    preview_plan=production.production_preview_plan
    def one_window(frozen,**kwargs):
        result=preview_plan(frozen,**kwargs)
        result['static_frames']=[0,frozen['plan']['duration_frames']-1]
        result['clips']=[dict(result['clips'][0],start_frame=0,end_frame=48)]
        return result
    monkeypatch.setattr(production,'production_preview_plan',one_window)
    def measured(owner,request,key):
        result=original_start(owner,request,key);job=env.renderer.jobs[(owner,key)]
        job.update(source_sha256=job['result']['source_sha256'],plan_sha256=job['result']['plan_sha256'])
        job['usage']['render_seconds']=next(seconds)
        return deepcopy(job)
    monkeypatch.setattr(env.renderer,'start',measured)
    upload=env.classified.upload
    monkeypatch.setattr(env.classified,'upload',lambda *a:(_ for _ in ()).throw(ServiceError(ERROR,413)))
    body=import_request(env)
    created=env.runs.import_scene_source(OWNER,env.import_parent['id'],body,'original-formal-pre-model')
    finish(env);parent=raw_run(env,created['id']);row=env.adapter._read(OWNER,parent['id'])
    assert parent['status']=='failed' and parent['error']==ERROR and not parent['draining']
    assert row['status']=='cancelled' and row['error'] is None and row['stop_proven'] is True
    assert row['review']['attempted'] is False and row['review']['job_id'] is None
    assert len(row['previews'])==2 and len(row['clips'])==1 and not env.classified.submissions
    status=env.spaces.get(OWNER,parent['id'])['source_import_execution_status']
    assert status['used']['render_seconds']==USED and status['used']['model_tokens']==0
    env.premodel_parent=parent;env.premodel_rows=original_rows(env,parent['id'])
    env.premodel_task=task_document(env.spaces.get(OWNER,parent['id']))
    env.premodel_render_count=len(env.renderer.submissions)
    monkeypatch.setattr(env.adapter,'freeze_configuration',original_configuration)
    checker=SceneSpaceClassifiedReviewGateway.__new__(SceneSpaceClassifiedReviewGateway)
    checker.profile_revision='1'
    monkeypatch.setattr(env.classified,'_files',checker._files,raising=False)
    original_preflight=env.classified.preflight_model_limits
    def bounded_route(deadline,limits):
        original_preflight(deadline,limits)
        return dict(profile=env.classified.profile,profile_revision='1',max_input_files=9,
            max_input_bytes=2*1024*1024,max_input_total_bytes=18*1024*1024,
            execution_binding=dict(sha256='d'*64),model_request_limits=deepcopy(MODEL_LIMIT_CAPABILITY))
    monkeypatch.setattr(env.classified,'preflight_model_limits',bounded_route)
    def faithful_receipt(job_id):
        document=env.classified._job(job_id)['document']
        provenance={item['frame_id']:item for item in document['constraints']['review_evidence']['frames']}
        return dict(images=[dict(sha256=frame['sha256'],model_request_completed=True,
            model_request_sha256='e'*64,media_type='image/png',byte_length=provenance[frame['frame_id']]['model_input_bytes'],
            input_item_index=1,part_index=index) for index,frame in enumerate(document['observation']['frames'])])
    monkeypatch.setattr(env.classified,'model_frame_receipt',faithful_receipt)
    monkeypatch.setattr(env.classified,'upload',upload)
    def actual_export(owner,request,key):
        result=original_start(owner,request,key);job=env.renderer.jobs[(owner,key)]
        job.update(source_sha256=job['result']['source_sha256'],plan_sha256=job['result']['plan_sha256'])
        return deepcopy(job)
    monkeypatch.setattr(env.renderer,'start',actual_export)
    return env


def preflight(env):
    return env.runs.source_import_resume_preflight(OWNER,env.premodel_parent['id'])


def resume(env,draft=None):
    draft=draft or preflight(env)
    return env.runs.resume_scene_source_import(OWNER,env.premodel_parent['id'],draft['request'],draft['request_key'])


def test_reuses_three_renders_preserves_failed_rows_and_original_budget_then_only_review_export(premodeled_env):
    env=premodeled_env;draft=preflight(env)
    assert draft['model_requests']==draft['jobs_created']==0 and draft['grant_created'] is False
    assert draft['execution_usage']['remaining']['render_seconds']==600-USED
    created=resume(env,draft);assert not env.classified.submissions
    env.runs.tick()
    opening=task_document(env.spaces.get(OWNER,created['id']))
    assert opening['usage']==env.premodel_task['usage']
    assert opening['reservations'][:3]==env.premodel_task['reservations']
    assert opening['source_import_parent_ledgers']==env.premodel_task['source_import_parent_ledgers']
    finish(env)
    result=env.runs.get(OWNER,created['id']);assert result['status']=='completed',result.get('error')
    scene=env.adapter._read(OWNER,created['id']);task=env.spaces.get(OWNER,created['id'])
    assert scene['deadline_at']==env.premodel_parent['scene_source_import']['execution_deadline_at']
    assert scene['source_import']==env.premodel_parent['scene_source_import']
    assert scene['configuration']==env.premodel_parent['scene_space_configuration']
    assert digest(scene['review']['document'])==scene['source_import_resume']['review_input_sha256']
    evidence=scene['review']['document']['constraints']['review_evidence']['canonicalization']
    assert len(evidence['original_frames'])==4 and len(evidence['supplied_frame_ids'])==3
    assert evidence['capture_to_supplied_frame']['clip-0-0']=='preview-0'
    assert scene['source']==env.adapter._read(OWNER,env.premodel_parent['id'])['source']
    assert task['frozen']==env.premodel_task['frozen'] and task['usage']['limits']['render_seconds']==600
    assert task['source_import_execution_status']['used']['render_seconds']==USED+3
    assert task['usage_status']['status']=='usage_unknown' and task['usage_status']['used']['model_tokens'] is None
    assert len(env.author.submissions)==len(env.classified.submissions)==1
    new_renders=env.renderer.submissions[env.premodel_render_count:]
    assert len(new_renders)==1 and 'frame_range' not in new_renders[0]
    assert original_rows(env,env.premodel_parent['id'])==env.premodel_rows
    assert original_rows(env,env.import_parent['id'])==env.import_parent_rows
    assert resume(env,draft)['id']==created['id']
    window=dict(draft['request'],renewal_authorization_sha256='b'*64,
        phase_execution_deadline_at=time.time()+600,ack_unused_allocation_only=True)
    with pytest.raises(ServiceError):
        env.runs.resume_scene_source_import_window(OWNER,env.premodel_parent['id'],window,draft['request_key'])
    with pytest.raises(ServiceError):env.runs.import_scene_source(OWNER,env.import_parent['id'],import_request(env),'another-import')
    with pytest.raises(ServiceError):env.runs.source_import_resume_preflight(OWNER,created['id'])


def test_every_cas_field_rejects_before_new_run_or_model_admission(premodeled_env):
    env=premodeled_env;draft=preflight(env)
    for field in ('expected_source_run_sha256','expected_scene_sha256','expected_task_sha256',
            'expected_usage_sha256','expected_configuration_sha256','expected_artifacts_sha256','expected_grant_sha256',
            'authorization_sha256','execution_deadline_at','expected_revision'):
        bad=deepcopy(draft)
        bad['request'][field]=(bad['request'][field]+1 if field in {'expected_revision','execution_deadline_at'} else 'f'*64)
        with pytest.raises(ServiceError):resume(env,bad)
    assert not env.classified.submissions and len(env.renderer.submissions)==env.premodel_render_count
    assert original_rows(env,env.premodel_parent['id'])==env.premodel_rows
    with env.store.transaction() as db:assert db.execute('SELECT count(*) FROM production_runs').fetchone()[0]==4


def test_fixed_key_concurrency_and_restart_after_task_insertion_never_resets_or_duplicates(premodeled_env,monkeypatch):
    env=premodeled_env;draft=preflight(env)
    with ThreadPoolExecutor(max_workers=2) as pool:created=list(pool.map(lambda _:resume(env,draft),range(2)))
    assert created[0]['id']==created[1]['id']
    with pytest.raises(ServiceError,match='fixed_key'):
        env.runs.resume_scene_source_import(OWNER,env.premodel_parent['id'],draft['request'],'another-key')
    original=env.adapter._write
    class Crash(BaseException):pass
    def fail(value,*,insert=False):
        if insert and value.get('source_import_resume'):raise Crash()
        return original(value,insert=insert)
    monkeypatch.setattr(env.adapter,'_write',fail)
    with pytest.raises(Crash):env.runs.tick()
    assert env.spaces.get(OWNER,created[0]['id'])['usage']==env.premodel_task['usage']
    monkeypatch.setattr(env.adapter,'_write',original)
    env.runs=ProductionRunStore(env.store,env.joint,env.motion,env.production,scene_space_adapter=env.adapter,sequence_store=env.sequence)
    finish(env)
    assert env.runs.get(OWNER,created[0]['id'])['status']=='completed'
    assert len(env.classified.submissions)==1 and len(env.renderer.submissions)==env.premodel_render_count+1
    assert original_rows(env,env.premodel_parent['id'])==env.premodel_rows


def test_unsafe_parent_is_not_resumable(premodeled_env,monkeypatch):
    env=premodeled_env;parent=env.premodel_parent
    for bad in ('review_attempt','review_job','review_refs','review_usage','export','repair','attempts',
            'stopping_child','changed_child_cost','changed_child_request','capability','current_runtime','artifact','expired','different_error'):
        original=env.adapter._read(OWNER,parent['id']);row=deepcopy(original)
        jobs=deepcopy(env.renderer.jobs);artifact=None
        with monkeypatch.context() as change:
            if bad=='review_attempt':row['review']['attempted']=True
            elif bad=='review_job':row['review']['job_id']='some-job'
            elif bad=='review_refs':row['review']['input_refs']=[]
            elif bad=='review_usage':row['review']['usage']={}
            elif bad=='export':row['render_submit_attempted']=True
            elif bad=='repair':row['repair']={'attempted':False}
            elif bad=='attempts':row['attempts']=[{'attempt':0}]
            elif bad in {'stopping_child','changed_child_cost','changed_child_request'}:
                child=next(value for value in env.renderer.jobs.values() if value['id']==row['previews'][0]['render_id'])
                if bad=='stopping_child':child['stop_proven']=False
                elif bad=='changed_child_cost':child['usage']['render_seconds']+=1
                else:child['request_sha256']='f'*64
            elif bad=='capability':row['previews'][0]['request']['document']['constraints']['scene_space_capability']['host_sha256']='f'*64
            elif bad=='current_runtime':change.setattr(env.renderer,'runtime_sha256','f'*64)
            elif bad=='artifact':
                path=env.adapter.root/'artifacts'/(row['previews'][0]['artifact_sha256']+'.png')
                artifact=(path,path.read_bytes());path.write_bytes(b'changed')
            elif bad=='expired':change.setattr(env.adapter,'clock',lambda:row['deadline_at']+1)
            else:row['error']='another-error'
            external={'stopping_child','changed_child_cost','changed_child_request','current_runtime','artifact','expired'}
            if bad not in external:env.adapter._write(row)
            try:
                with pytest.raises(ServiceError):preflight(env)
            finally:
                if bad not in external:env.adapter._write(original)
                if artifact:artifact[0].write_bytes(artifact[1])
                env.renderer.jobs=jobs
        assert not env.classified.submissions and len(env.renderer.submissions)==env.premodel_render_count


def test_new_task_only_review_export_and_cannot_change_carried_ledger(premodeled_env):
    env=premodeled_env;created=resume(env);env.runs.tick();identity=created['id']
    for phase in ('preview','generation','repair'):
        with pytest.raises(ServiceError):env.spaces.reserve(OWNER,identity,'forbidden-'+phase,phase=phase,model_calls=1,model_tokens=10)
    with pytest.raises(ServiceError):env.spaces.retry(OWNER,identity,env.premodel_task['frozen']['plan_sha256'])
    with env.spaces._db() as db:
        task=env.spaces._load(db,OWNER,identity);task['usage']['events'][0]['render_seconds']=0
        task['usage']['sha256']=digest({k:v for k,v in task['usage'].items() if k!='sha256'})
        env.spaces._put(db,task)
    with pytest.raises(ServiceError,match='usage_prefix_changed'):env.spaces.get(OWNER,identity)
    assert original_rows(env,env.premodel_parent['id'])==env.premodel_rows


def test_original_deadline_expiring_after_successor_commit_cannot_start_review(premodeled_env,monkeypatch):
    env=premodeled_env;created=resume(env)
    monkeypatch.setattr(env.adapter,'clock',lambda:created['scene_source_import']['execution_deadline_at']+1)
    finish(env)
    assert not env.classified.submissions and len(env.renderer.submissions)==env.premodel_render_count
    assert original_rows(env,env.premodel_parent['id'])==env.premodel_rows


def test_review_route_is_bound_in_cas_and_checked_again_before_admission(premodeled_env,monkeypatch):
    env=premodeled_env;draft=preflight(env);original=env.classified.preflight_model_limits
    def changed(*args):
        route=original(*args);route['execution_binding']['sha256']='f'*64;return route
    with monkeypatch.context() as change:
        change.setattr(env.classified,'preflight_model_limits',changed)
        with pytest.raises(ServiceError):resume(env,draft)
    created=resume(env,draft)
    monkeypatch.setattr(env.classified,'preflight_model_limits',changed)
    finish(env)
    assert env.runs.get(OWNER,created['id'])['status']=='failed'
    assert not env.classified.submissions and len(env.renderer.submissions)==env.premodel_render_count
    assert original_rows(env,env.premodel_parent['id'])==env.premodel_rows


def test_native_wire_budget_rejects_four_large_distinct_clock_images_without_changing_worker():
    files={'picture-learning-input.json':b'{"schema":"synthetic-budget-only"}'}
    files.update({str(frame)+'.png':bytes([frame])*838118 for frame in (0,24,47,71)})
    with pytest.raises(ServiceError,match='native_wire_too_large'):
        require_native_wire_budget('picture-learning-input.json',files)
    del files['24.png']
    assert require_native_wire_budget('picture-learning-input.json',files)<4*1024*1024


def test_http_owner_csrf_fixed_key_idempotency_and_exact_body(premodeled_env,monkeypatch):
    env=premodeled_env;monkeypatch.setenv('SERVICE_AUTH_ISSUER','https://issuer.test')
    app=create_app(db_path=env.store.path,local_mode=False,background=False,production_runs_enabled=True,
        joint_plan_enabled=True,scene_space_adapter=env.adapter,trusted_hosts={'testserver'})
    path='/api/production-runs/'+env.premodel_parent['id']+'/scene-source-import-resume'
    with TestClient(app,base_url='https://testserver',headers={'Origin':'https://testserver'}) as client:
        client.headers['x-forwarded-user']=OWNER
        draft=client.get(path).json();body=draft['request'];key=draft['request_key']
        assert client.post(path,json=body,headers={'Idempotency-Key':key}).status_code==403
        headers={'X-CSRF-Token':client.get('/api/session').json()['csrf_token'],'Idempotency-Key':key}
        altered=dict(body,execution_deadline_at=body['execution_deadline_at']+1)
        assert client.post(path,json=altered,headers=headers).status_code==409
        extra=dict(body,reset_budget=True)
        assert client.post(path,json=extra,headers=headers).status_code==422
        with env.store.transaction() as db:before=db.execute('SELECT COUNT(*) FROM production_runs').fetchone()[0]
        created=client.post(path,json=body,headers=headers);assert created.status_code==409,created.text
        assert created.json()['detail']['code']=='review_standard_change_disabled_use_existing_standard'
        assert client.post(path,json=body,headers=headers).status_code==409
        with env.store.transaction() as db:assert db.execute('SELECT COUNT(*) FROM production_runs').fetchone()[0]==before
        client.headers['x-forwarded-user']='another-owner'
        assert client.get(path).status_code==404
    assert not env.classified.submissions and len(env.renderer.submissions)==env.premodel_render_count


def test_separate_explicit_owner_window_keeps_original_grant_and_all_costs_and_cannot_fallback(premodeled_env,monkeypatch):
    env=premodeled_env;old=env.premodel_parent['scene_source_import'];now=old['execution_deadline_at']+1
    monkeypatch.setattr(env.adapter,'clock',lambda:now)
    with pytest.raises(ServiceError):preflight(env)
    draft=env.runs.source_import_resume_window_preflight(OWNER,env.premodel_parent['id'])
    assert draft['grant_created'] is False and draft['request']['execution_deadline_at']==old['execution_deadline_at']
    body=dict(draft['request'],renewal_authorization_sha256='b'*64,
        phase_execution_deadline_at=now+600,ack_unused_allocation_only=True)
    for changes in ({'renewal_authorization_sha256':old['authorization_sha256']},
            {'ack_unused_allocation_only':False},{'phase_execution_deadline_at':now+3601},
            {'phase_execution_deadline_at':now},{'phase_execution_deadline_at':float('nan')},
            {'execution_deadline_at':now+600}):
        with pytest.raises(ServiceError):
            env.runs.resume_scene_source_import_window(OWNER,env.premodel_parent['id'],dict(body,**changes),draft['request_key'])
    with pytest.raises(ServiceError):env.runs.resume_scene_source_import(OWNER,env.premodel_parent['id'],body,draft['request_key'])
    monkeypatch.setenv('SERVICE_AUTH_ISSUER','https://issuer.test')
    app=create_app(db_path=env.store.path,local_mode=False,background=False,production_runs_enabled=True,
        joint_plan_enabled=True,scene_space_adapter=env.adapter,trusted_hosts={'testserver'})
    path='/api/production-runs/'+env.premodel_parent['id']+'/scene-source-import-resume-window'
    with TestClient(app,base_url='https://testserver',headers={'Origin':'https://testserver'}) as client:
        client.headers['x-forwarded-user']=OWNER
        assert client.get(path).status_code==200
        assert client.post(path,json=body,headers={'Idempotency-Key':draft['request_key']}).status_code==403
        headers={'X-CSRF-Token':client.get('/api/session').json()['csrf_token'],'Idempotency-Key':draft['request_key']}
        assert client.post(path.removesuffix('-window'),json=body,headers=headers).status_code==422
        with env.store.transaction() as db:before=db.execute('SELECT COUNT(*) FROM production_runs').fetchone()[0]
        response=client.post(path,json=body,headers=headers);assert response.status_code==409,response.text
        assert response.json()['detail']['code']=='review_standard_change_disabled_use_existing_standard'
        assert client.post(path,json=body,headers=headers).status_code==409
        changed=dict(body,phase_execution_deadline_at=now+700)
        assert client.post(path,json=changed,headers=headers).status_code==409
        with env.store.transaction() as db:assert db.execute('SELECT COUNT(*) FROM production_runs').fetchone()[0]==before
        client.headers['x-forwarded-user']='another-owner'
        assert client.get(path).status_code==404
    # Reconstruct the previously admitted window directly to keep validating
    # historical frozen execution; the public mutation above creates no work.
    created=env.runs.resume_scene_source_import_window(OWNER,env.premodel_parent['id'],body,draft['request_key'])
    certificate=created['scene_source_import_resume']
    assert certificate['schema']=='video.scene-source-import-resume-window.v1'
    assert certificate['execution_deadline_at']==old['execution_deadline_at']
    assert certificate['authorization_sha256']==old['authorization_sha256']
    assert certificate['phase_execution_deadline_at']==now+600 and certificate['renewal_authorization_sha256']=='b'*64
    assert created['scene_source_import']==old
    finish(env);public=env.runs.get(OWNER,created['id']);assert public['status']=='completed',public.get('error')
    scene=env.adapter._read(OWNER,created['id']);task=env.spaces.get(OWNER,created['id'])
    assert scene['deadline_at']==now+600 and task['source_import']==old
    assert task['usage']['limits']['render_seconds']==600 and task['source_import_execution_status']['used']['render_seconds']==USED+3
    assert task['usage']['events'][:3]==env.premodel_task['usage']['events']
    assert task['usage_status']['status']=='usage_unknown'
    assert original_rows(env,env.premodel_parent['id'])==env.premodel_rows
    assert len(env.classified.submissions)==1 and len(env.renderer.submissions)==env.premodel_render_count+1
    with pytest.raises(ServiceError):env.runs.source_import_resume_window_preflight(OWNER,env.premodel_parent['id'])
    with pytest.raises(ServiceError):env.runs.resume_scene_source_import(OWNER,env.premodel_parent['id'],draft['request'],draft['request_key'])
    monkeypatch.setattr(env.adapter,'clock',lambda:now+601)
    # An identical old receipt can be reconciled after expiry without admission.
    assert env.runs.resume_scene_source_import_window(OWNER,env.premodel_parent['id'],body,draft['request_key'])['id']==created['id']


def test_explicit_new_phase_expiring_after_commit_never_starts_review_or_export(premodeled_env,monkeypatch):
    env=premodeled_env;old=env.premodel_parent['scene_source_import'];now=old['execution_deadline_at']+1
    monkeypatch.setattr(env.adapter,'clock',lambda:now)
    draft=env.runs.source_import_resume_window_preflight(OWNER,env.premodel_parent['id'])
    body=dict(draft['request'],renewal_authorization_sha256='b'*64,
        phase_execution_deadline_at=now+300,ack_unused_allocation_only=True)
    created=env.runs.resume_scene_source_import_window(OWNER,env.premodel_parent['id'],body,draft['request_key'])
    monkeypatch.setattr(env.adapter,'clock',lambda:now+301)
    finish(env)
    assert env.runs.get(OWNER,created['id'])['status']=='failed'
    assert not env.classified.submissions and len(env.renderer.submissions)==env.premodel_render_count
    assert original_rows(env,env.premodel_parent['id'])==env.premodel_rows


def test_receipt_needs_three_separate_parts_even_when_all_png_hashes_match():
    digest_value='a'*64
    document=dict(observation=dict(frames=[dict(frame_id=frame,sha256=digest_value) for frame in ('preview-0','clip-0-47','preview-71')]),
        constraints=dict(review_evidence=dict(frames=[dict(frame_id=frame,model_input_bytes=838118)
            for frame in ('preview-0','clip-0-47','preview-71')])))
    rows=[dict(sha256=digest_value,media_type='image/png',byte_length=838118,model_request_completed=True,
        model_request_sha256='b'*64,input_item_index=1,part_index=index) for index in range(3)]
    require_supplied_frame_receipt(document,dict(images=rows))
    with pytest.raises(ServiceError):require_supplied_frame_receipt(document,dict(images=rows[:1]))
    with pytest.raises(ServiceError):require_supplied_frame_receipt(document,dict(images=[rows[0]]*3))
