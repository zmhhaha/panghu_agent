"""HTTP ACL and transaction-state checks (SQLite); PG concurrency is separate."""
import os,uuid
from pathlib import Path
import pytest
from sqlalchemy import create_engine,select
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from ppt_service.server.app import create_app
from ppt_service.server.auth import Settings,Identity,authenticate
from ppt_service.server.migrate import migrate
from ppt_service.server.models import *
from ppt_service.server.repository import Repository
@pytest.fixture
def ctx(tmp_path):
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    migrate(engine)
    app=create_app(Settings('sqlite://',str(tmp_path)),engine)
    who=[Identity('https://issuer','alice')]
    app.dependency_overrides[authenticate]=lambda:who[0]
    client=TestClient(app)
    return app,client,who

def project(c):
    r=c.post('/api/v1/projects',json={'title':'Test','external_model_ack':True});assert r.status_code==201;return r.json()
def plan(c,p,key='plan',phase='plan_initial'):
    return c.post(f"/api/v1/projects/{p['id']}/plans",json={'expected_revision':p['revision'],'phase':phase},headers={'Idempotency-Key':key})
def getp(c,p):return c.get('/api/v1/projects/'+p['id']).json()
def accepted(app,jid,phase='plan_initial'):
    with app.state.sessions.begin() as s:
        r=Repository(s);j=r.claim_next();assert j.id==jid
        r.attach_execution(j.id,j.attempt,j.fencing_token,job_uid='k8s-uid',session_id='session')
        r.prove_stopped(j.id,j.attempt,j.fencing_token,job_uid='k8s-uid',all_writers_stopped=True)
        pid=j.project_id
        result=r.accept_plan(j.id,j.attempt,j.fencing_token,outline={'slides':[]},design={'directions':[{'id':'A'},{'id':'B'},{'id':'C'}]} if phase=='plan_options' else {},checkpoint_key=f'projects/{pid}/checkpoints/{uuid.uuid4()}',checkpoint_manifest={'trusted':True},independently_validated=True)
        return result.revision,result.input_snapshot_hash

def confirm(c,p,rev,h,phase='plan_initial',key='confirm'):
    return c.post(f"/api/v1/projects/{p['id']}/plans/{rev}/confirm",json={'expected_revision':p['revision'],'input_snapshot_hash':h,'selection':{'design_mode':'free_design','template_id':None} if phase=='plan_initial' else {'direction_id':'A'}},headers={'Idempotency-Key':key})
def test_two_owners_projects_jobs_events(ctx):
    app,c,who=ctx;p=project(c);j=plan(c,p).json();c.post('/api/v1/jobs/'+j['id']+'/cancel',headers={'Idempotency-Key':'stop'})
    assert 'job.cancelled' in c.get('/api/v1/jobs/'+j['id']+'/events').text
    who[0]=Identity('https://issuer','bob')
    for route in ('/projects/'+p['id'],'/jobs/'+j['id'],'/jobs/'+j['id']+'/events','/projects/'+p['id']+'/versions'):
        assert c.get('/api/v1'+route).status_code==404
    assert c.get('/api/v1/projects').json()==[]
    who[0]=Identity('https://other-issuer','alice');assert c.get('/api/v1/projects/'+p['id']).status_code==404

def test_idempotency_is_original_and_conflict(ctx):
    app,c,_=ctx;p=project(c)
    one=plan(c,p);two=plan(c,p);assert one.status_code==202;assert two.json()==one.json()
    changed=c.post('/api/v1/projects/'+p['id']+'/plans',json={'expected_revision':p['revision'],'feedback':'different'},headers={'Idempotency-Key':'plan'})
    assert changed.status_code==409
    with app.state.sessions() as s:assert len(s.scalars(select(Job)).all())==1

def test_two_confirmation_gates_and_stale_snapshot(ctx):
    app,c,_=ctx;p=project(c);j=plan(c,p).json();rev,h=accepted(app,j['id']);p=getp(c,p)
    assert c.post('/api/v1/projects/'+p['id']+'/generations',json={'expected_revision':p['revision'],'plan_revision':rev},headers={'Idempotency-Key':'badgen'}).status_code==409
    assert confirm(c,p,rev,h).status_code==200;p=getp(c,p)
    j=plan(c,p,'options','plan_options').json();rev,h=accepted(app,j['id'],'plan_options');p=getp(c,p)
    assert confirm(c,p,rev,h,'plan_options','confirm-options').status_code==200;p=getp(c,p)
    response=c.post('/api/v1/projects/'+p['id']+'/generations',json={'expected_revision':p['revision'],'plan_revision':rev},headers={'Idempotency-Key':'generate'})
    assert response.status_code==202;assert response.json()['stage']=='prepare'
    duplicate=c.post('/api/v1/projects/'+p['id']+'/generations',json={'expected_revision':p['revision'],'plan_revision':rev},headers={'Idempotency-Key':'generate'})
    assert duplicate.json()==response.json()
    with app.state.sessions() as s:
        q=s.get(UserQuota,('https://issuer','alice'));assert q.generation_count==1;assert not q.enabled
    with app.state.sessions.begin() as s:
        r=Repository(s);prepare=r.claim_next();assert prepare.kind=='prepare';old_token=prepare.fencing_token
        r.attach_execution(prepare.id,1,old_token,job_uid='prepare-uid')
        r.prove_stopped(prepare.id,1,old_token,job_uid='prepare-uid',all_writers_stopped=True)
        generate=r.accept_prepared(prepare.id,1,old_token,checkpoint_key=f"projects/{p['id']}/checkpoints/{uuid.uuid4()}",checkpoint_manifest={'test_validation':True},independently_validated=True)
        gid=generate.id
    with app.state.sessions.begin() as s:
        r=Repository(s);j=r.claim_next();assert j.id==gid;assert j.fencing_token>old_token
        r.attach_execution(j.id,1,j.fencing_token,job_uid='generate-uid')
        r.prove_stopped(j.id,1,j.fencing_token,job_uid='generate-uid',all_writers_stopped=True)
        root=f"projects/{p['id']}/versions/{uuid.uuid4()}/published"
        v=r.publish_version(j.id,1,j.fencing_token,manifest={'pptx_key':root+'/deck.pptx','slides':[{'preview_key':root+'/1.png'}]},artifact_root=root,checkpoint_key=f"projects/{p['id']}/checkpoints/{uuid.uuid4()}",checkpoint_manifest={'test_validation':True},independently_validated=True)
        vid=v.id
        folder=app.state.storage.root/root;folder.mkdir(parents=True);(folder/'deck.pptx').write_bytes(b'PK-test');(folder/'1.png').write_bytes(b'\x89PNG\r\n\x1a\n')
    assert getp(c,p)['latest_version_id']==vid
    assert c.get('/api/v1/versions/'+vid+'/download').status_code==200

def test_upload_invalidates_confirmation_and_no_parse(ctx):
    app,c,_=ctx;p=project(c);j=plan(c,p).json();rev,h=accepted(app,j['id']);p=getp(c,p)
    assert confirm(c,p,rev,h).status_code==200
    response=c.post('/api/v1/projects/'+p['id']+'/assets',files={'file':('../../trick.md',b'# input','text/markdown')})
    assert response.status_code==201;assert response.json()['parse_status']=='queued';assert response.json()['display_name']=='trick.md'
    p=getp(c,p);assert p['confirmed_plan_revision'] is None
    assert confirm(c,p,rev,h,key='stale').status_code==409
    assert c.post('/api/v1/projects/'+p['id']+'/assets',files={'file':('x.svg',b'<svg/>','image/svg+xml')}).status_code==415
    assert c.post('/api/v1/projects/'+p['id']+'/assets',files={'file':('x.png',b'<svg/>','image/png')}).status_code==415

def test_cancellation_fences_late_result_and_unknown_cost(ctx):
    app,c,_=ctx;p=project(c);jid=plan(c,p).json()['id']
    with app.state.sessions.begin() as s:
        r=Repository(s);j=r.claim_next();token=j.fencing_token;r.attach_execution(j.id,1,token,job_uid='uid')
    assert c.post('/api/v1/jobs/'+jid+'/cancel',headers={'Idempotency-Key':'cancel'}).json()['status']=='cancelling'
    with app.state.sessions.begin() as s:
        r=Repository(s)
        with pytest.raises(Exception):r.accept_plan(jid,1,token,outline={},design={},checkpoint_key='x',checkpoint_manifest={},independently_validated=True)
        r.record_usage(jid,1,phase='plan_initial',outcome='cancelled',provider='provider',provider_request_id='r1',input_tokens=4)
        r.prove_stopped(jid,1,token,job_uid='uid',all_writers_stopped=True)
    assert c.get('/api/v1/jobs/'+jid).json()['status']=='cancelled'
    with app.state.sessions() as s:
        u=s.scalar(select(UsageRecord));assert u.cost_status=='unknown';assert u.reported_cost is None
        assert s.get(ExecutionSlot,1).job_id is None

def test_deletion_stop_first_and_event_cursor(ctx):
    app,c,_=ctx;p=project(c);jid=plan(c,p).json()['id']
    with app.state.sessions.begin() as s:
        r=Repository(s);j=r.claim_next();token=j.fencing_token;r.attach_execution(j.id,1,token,job_uid='uid');j.event_floor=1
    assert c.get('/api/v1/jobs/'+jid+'/events',headers={'Last-Event-ID':'0'}).status_code==410
    response=c.delete('/api/v1/projects/'+p['id']);assert response.status_code==202;assert not response.json()['cleanup_ready']
    assert c.get('/api/v1/jobs/'+jid).status_code==404
    with app.state.sessions.begin() as s:
        r=Repository(s);r.prove_stopped(jid,1,token,job_uid='uid',all_writers_stopped=True)
        assert s.get(Project,p['id']).cleanup_ready

def test_transport_bounds_and_consent(ctx):
    app,c,_=ctx
    p=c.post('/api/v1/projects',json={'title':'No consent'}).json()
    assert c.post('/api/v1/projects/'+p['id']+'/assets',files={'file':('a.md',b'private')}).status_code==422
    assert plan(c,p).status_code==422
    assert c.post('/api/v1/projects',content=b'{}',headers={'Content-Type':'application/json','Content-Length':str(129*1024)}).status_code==413
    assert c.post('/api/v1/projects/'+p['id']+'/assets',content=b'x',headers={'Content-Length':str(27*1024**2)}).status_code==413


@pytest.mark.parametrize('ack', [False, True])
def test_project_model_consent_survives_refresh_and_omitted_update(ctx, ack):
    _, c, _ = ctx
    p = c.post('/api/v1/projects', json={'title': 'Saved consent', 'external_model_ack': ack}).json()
    assert getp(c, p)['external_model_ack'] is ack
    assert c.get('/api/v1/projects').json()[0]['external_model_ack'] is ack
    response = c.patch('/api/v1/projects/'+p['id'], json={
        'title': 'Updated requirements', 'expected_revision': p['revision']})
    assert response.status_code == 200, response.text
    assert response.json()['external_model_ack'] is ack
    refreshed = getp(c, p)
    assert refreshed['external_model_ack'] is ack
    assert refreshed['revision'] == p['revision'] + 1


def test_project_model_consent_can_be_explicitly_revoked(ctx):
    _, c, _ = ctx
    p = project(c)
    response = c.patch('/api/v1/projects/'+p['id'], json={
        'title': p['title'], 'expected_revision': p['revision'], 'external_model_ack': False})
    assert response.status_code == 200, response.text
    assert response.json()['external_model_ack'] is False
    p = getp(c, p)
    assert p['external_model_ack'] is False
    assert c.post('/api/v1/projects/'+p['id']+'/assets',
                  files={'file': ('source.md', b'# source', 'text/markdown')}).status_code == 422
    assert plan(c, p).status_code == 422


def test_no_auth_header_bypass(ctx):
    app,c,_=ctx;app.dependency_overrides.clear()
    assert c.get('/api/v1/me',headers={'X-Auth-Request-User':'alice','X-Auth-Request-Email':'a@x'}).status_code==401

def test_liveness_does_not_require_auth_or_database(ctx):
    app,c,_=ctx;app.dependency_overrides.clear()
    app.state.engine.dispose()
    assert c.get('/healthz').json()=={'status':'ok'}
    assert c.get('/api/v1/me').status_code==401

def seed_version(app,p):
    with app.state.sessions.begin() as s:
        j=Job(project_id=p['id'],issuer='https://issuer',subject='alice',kind='generate',stage='generate',status='succeeded',input_snapshot={});s.add(j);s.flush()
        cp=Checkpoint(project_id=p['id'],issuer='https://issuer',subject='alice',storage_key='unused',manifest={});s.add(cp);s.flush()
        vid=str(uuid.uuid4());root=f"projects/{p['id']}/versions/{vid}/published"
        v=Version(id=vid,project_id=p['id'],issuer='https://issuer',subject='alice',job_id=j.id,checkpoint_id=cp.id,artifact_root=root,manifest={'pptx_key':root+'/deck.pptx','slides':[{'preview_key':root+'/1.png'}]});s.add(v)
        folder=app.state.storage.root/root;folder.mkdir(parents=True);(folder/'deck.pptx').write_bytes(b'PK-test');(folder/'1.png').write_bytes(b'\x89PNG\r\n\x1a\n')
        return vid,folder

def test_version_download_preview_acl_and_no_svg(ctx):
    app,c,who=ctx;p=project(c);vid,folder=seed_version(app,p)
    assert c.get('/api/v1/versions/'+vid+'/download').status_code==200
    assert c.get('/api/v1/versions/'+vid+'/slides/1/preview').headers['content-type']=='image/png'
    assert c.get('/api/v1/versions/'+vid+'/slides/0/preview').status_code==404
    who[0]=Identity('https://issuer','bob')
    assert c.get('/api/v1/versions/'+vid+'/download').status_code==404
    assert c.get('/api/v1/versions/'+vid+'/slides/1/preview').status_code==404
    who[0]=Identity('https://issuer','alice')
    with app.state.sessions.begin() as s:
        v=s.get(Version,vid);v.manifest={'slides':[{'preview_key':v.artifact_root+'/evil.svg'}]}
    assert c.get('/api/v1/versions/'+vid+'/slides/1/preview').status_code==404

def test_storage_rejects_traversal_and_symlink(ctx,tmp_path):
    app,c,_=ctx;p=project(c);vid,folder=seed_version(app,p)
    with pytest.raises(ValueError):app.state.storage.open('../secret',100)
    target=folder/'1.png';target.unlink()
    try:target.symlink_to(folder/'deck.pptx')
    except OSError:pytest.skip('symlink permission unavailable on Windows')
    assert c.get('/api/v1/versions/'+vid+'/slides/1/preview').status_code==404
