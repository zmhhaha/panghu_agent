"""Hash-bound public planning fixtures; no model, live DB or historical promotion."""
from __future__ import annotations
import copy,hashlib,json,os,uuid
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,select
from ppt_service.control.planning import project_planning,project_stage1
from ppt_service.control.execution import ControlError,RuntimeConfig
from ppt_service.control.dispatcher import Dispatcher
from ppt_service.server.app import create_app
from ppt_service.server.auth import Settings,Identity,authenticate
from ppt_service.server.models import Project,Job,Plan,Checkpoint,UsageRecord,now
from ppt_service.server.repository import Repository,ACTIVE
from ppt_service.server.migrate import migrate

STAGE2_SHA='8a6d81bb8e020a00127b45a942f67d2db7c2c28beefcdd95400a9e532f3a8793'
STAGE1_SHA='689efee818ada1df9d1c07f58110a88b581e112b48052d68be33069fed741c02'
IDS=['evidence-handbook','acceptance-console','guided-walkthrough']
def output(name,value):
    directory=os.getenv('PLANNING_PROJECTION_OUTPUT')
    if directory:
        from ppt_service.control.staging import atomic_json
        atomic_json(Path(directory)/name,value)
@pytest.fixture
def frozen():
    here=Path(__file__).resolve();base=here.parents[2] if len(here.parents)>2 else Path('/fixture-not-configured')
    second=Path(os.getenv('PLANNING_STAGE2_FIXTURE',str(base/'.runtime/recovered-options/result/manifest.json')))
    first=Path(os.getenv('PLANNING_STAGE1_FIXTURE',str(base/'.runtime/recovered-options/generate-snapshot/acceptance-public-sample_20261021/planning/stage1-proposal.json')))
    if not second.is_file() or not first.is_file():pytest.skip('Hash-verified frozen public planning fixtures required')
    raw=second.read_bytes();initial=first.read_bytes()
    assert hashlib.sha256(raw).hexdigest()==STAGE2_SHA;assert hashlib.sha256(initial).hexdigest()==STAGE1_SHA
    yield json.loads(raw),json.loads(initial)
    assert hashlib.sha256(second.read_bytes()).hexdigest()==STAGE2_SHA;assert hashlib.sha256(first.read_bytes()).hexdigest()==STAGE1_SHA

def project(source):return project_planning(source,source_manifest_sha256=STAGE2_SHA,expected_page_count=6)

def test_actual_nested_fixture_preserves_ids_pages_constraints_and_recommendation(frozen):
    source,initial=frozen;before=copy.deepcopy(source);dto=project(source);plan=source['plan'];options=plan['options'];shared=plan['page_plan']
    assert dto['outline']==shared;assert dto['design']['page_plan']==shared
    assert [d['id'] for d in dto['design']['directions']]==IDS
    for i,direction in enumerate(dto['design']['directions']):
        candidate=options['design_directions']['candidates'][i]
        assert all(direction[key]==value for key,value in candidate.items())
        assert direction['page_expression']==candidate['page_expression'];assert direction['page_plan']==shared
        assert direction['production_parameters']=={k:v for k,v in options.items() if k!='design_directions'}
        assert 'confirmation_selection' not in direction and 'confirmed_at' not in direction
    assert dto['design']['recommendation']['direction_id']=='evidence-handbook';assert dto['design']['recommendation']['selection_is']=='agent_recommendation_only_not_user_confirmation'
    assert dto['design']['source_plan']==plan;assert dto['design']['source_projection']['source_manifest_sha256']==STAGE2_SHA
    output('stage2-projection.json',dto)
    assert source==before;assert not {'confirmed_at','confirmation_selection','confirmed'}.intersection(dto['design'])
    dto['design']['directions'][0]['page_plan']['pages'][0]['title']='Test mutation of one projection only'
    assert dto['design']['directions'][1]['page_plan']==shared;assert source==before

def test_real_stage1_proposal_is_not_a_confirmation(frozen):
    _,proposal=frozen;before=copy.deepcopy(proposal);dto=project_stage1(proposal,source_manifest_sha256=STAGE1_SHA)
    output('stage1-proposal-projection.json',dto)
    assert dto['outline']['communication']==proposal;assert dto['design']['communication']['confirmation_received'] is False
    assert not {'confirmed_at','confirmation_selection','confirmed'}.intersection(dto['design']);assert proposal==before

@pytest.mark.parametrize('key',['directions','options'])
def test_explicit_direct_shapes_keep_stable_ids_and_complete_fields(frozen,key):
    source,_=frozen;actual=project(source)
    direct={'phase':'plan_options','status':'awaiting_confirmation','input_snapshot_sha256':source['input_snapshot_sha256'],'plan':{'outline':copy.deepcopy(actual['outline']),'design':{key:copy.deepcopy(actual['design']['directions']),'page_plan':copy.deepcopy(actual['outline']),'production_parameters':copy.deepcopy(actual['design']['production_parameters']),'selected':0}}}
    before=copy.deepcopy(direct);dto=project_planning(direct,expected_page_count=6)
    assert [d['id'] for d in dto['design']['directions']]==IDS;assert dto['outline']==actual['outline'];assert dto['design']['recommendation']['direction_id']==IDS[0];assert direct==before

@pytest.mark.parametrize('problem',['two','four','duplicate','empty_id','long_id','cross_id','missing_name','cross_name','cross_candidates','empty_pages','cross_pages','out_of_order','empty_content','wrong_count','cross_production','arbitrary_dict','selected_bool'])
def test_malformed_or_ambiguous_planning_is_rejected(frozen,problem):
    source,_=frozen;source=copy.deepcopy(source);plan=source['plan'];options=plan['options'];container=options['design_directions'];candidates=container['candidates']
    if problem=='two':container['candidates']=candidates[:2]
    elif problem=='four':container['candidates']=candidates+[copy.deepcopy(candidates[0])]
    elif problem=='duplicate':candidates[1]['id']=candidates[0]['id']
    elif problem=='empty_id':candidates[0]['id']=''
    elif problem=='long_id':candidates[0]['id']='a'*101
    elif problem=='cross_id':candidates[0]['id']={'value':IDS[0]}
    elif problem=='missing_name':candidates[0].pop('name')
    elif problem=='cross_name':candidates[0]['name']=['Wrong type']
    elif problem=='cross_candidates':container['candidates']={d['id']:d for d in candidates}
    elif problem=='empty_pages':plan['page_plan']['pages']=[]
    elif problem=='cross_pages':plan['page_plan']['pages']={}
    elif problem=='out_of_order':plan['page_plan']['pages'][0]['ordinal']=2
    elif problem=='empty_content':plan['page_plan']['pages'][0]={'ordinal':1,'title':'A label is not a full page plan'}
    elif problem=='wrong_count':options['page_count']['value']='7'
    elif problem=='cross_production':candidates[0]['production_parameters']=[]
    elif problem=='arbitrary_dict':plan['options']={'some_unknown_direction_container':container}
    elif problem=='selected_bool':container['selected']=True
    with pytest.raises(ControlError):project(source)

@pytest.fixture
def pg_api(tmp_path):
    url=os.getenv('TEST_DATABASE_URL')
    if not url:pytest.skip('Isolated PostgreSQL database required')
    engine=create_engine(url,pool_pre_ping=True);migrate(engine);app=create_app(Settings(url,str(tmp_path)),engine)
    owner=Identity('urn:planning-contract-test',str(uuid.uuid4()));app.dependency_overrides[authenticate]=lambda:owner
    client=TestClient(app);p=client.post('/api/v1/projects',json={'title':'Frozen planning DTO projection integration test','page_count':6,'external_model_ack':True}).json()
    response=client.post('/api/v1/projects/'+p['id']+'/assets',files={'file':('public-test.md',b'Public planning projection test namespace; not a historical candidate acceptance.','text/markdown')});assert response.status_code==201
    yield app,client,owner,p['id']
    with app.state.sessions.begin() as s:
        repo=Repository(s)
        for j in s.scalars(select(Job).where(Job.project_id==p['id'],Job.status.in_(ACTIVE))):
            repo.cancel(j)
            if j.status=='cancelling':repo.prove_stopped(j.id,j.attempt,j.fencing_token,job_uid=j.job_uid,all_writers_stopped=True)
    engine.dispose()

def getp(c,pid):return c.get('/api/v1/projects/'+pid).json()
def accept_projected_plan(app,jid,projection,source_sha):
    # This is an isolated API/repository protocol fixture. No historical task,
    # session or artifact is promoted, and no execution backend is started.
    with app.state.sessions.begin() as s:
        repo=Repository(s);j=repo.claim_next();assert j.id==jid
        controller=Dispatcher(app.state.sessions,RuntimeConfig(Path(app.state.settings.data_root)),None,None)
        a,cp=controller._attempt(s,j)
        uid=str(uuid.uuid4());repo.attach_execution(jid,j.attempt,j.fencing_token,job_uid=uid,session_id=a.session_id)
        repo.prove_stopped(jid,j.attempt,j.fencing_token,job_uid=uid,all_writers_stopped=True)
        checkpoint={'test_fixture_only':True,'session_id':a.session_id,'source_projection_sha256':source_sha,'control_context':a.snapshot['control_context']}
        plan=repo.accept_plan(jid,j.attempt,j.fencing_token,outline=projection['outline'],design=projection['design'],checkpoint_key=f'projects/{j.project_id}/checkpoints/{uuid.uuid4()}',checkpoint_manifest=checkpoint,independently_validated=True)
        return plan.revision

def prepare_options(pg_api,frozen):
    app,c,owner,pid=pg_api;stage2,stage1=frozen;p=getp(c,pid)
    response=c.post('/api/v1/projects/'+pid+'/plans',json={'expected_revision':p['revision'],'phase':'plan_initial'},headers={'Idempotency-Key':'initial'});assert response.status_code==202
    rev=accept_projected_plan(app,response.json()['job_id'],project_stage1(stage1,source_manifest_sha256=STAGE1_SHA),STAGE1_SHA)
    p=getp(c,pid);assert p['confirmed_plan_revision'] is None
    response=c.post(f'/api/v1/projects/{pid}/plans/{rev}/confirm',json={'expected_revision':p['revision'],'input_snapshot_hash':p['input_snapshot_hash'],'selection':{'design_mode':'free_design','template_id':None}},headers={'Idempotency-Key':'confirm-initial'});assert response.status_code==200
    p=getp(c,pid);response=c.post('/api/v1/projects/'+pid+'/plans',json={'expected_revision':p['revision'],'phase':'plan_options'},headers={'Idempotency-Key':'options'});assert response.status_code==202
    rev=accept_projected_plan(app,response.json()['job_id'],project(stage2),STAGE2_SHA)
    return app,c,owner,pid,rev

def test_real_projection_database_api_confirm_and_explicit_prepare_context(pg_api,frozen):
    app,c,owner,pid,rev=prepare_options(pg_api,frozen);source,_=frozen;p=getp(c,pid);dto=p['plans'][-1]
    output('stage2-api-plan-unconfirmed.json',dto)
    assert [d['id'] for d in dto['design']['directions']]==IDS;assert [d['id'] for d in dto['options']]==IDS
    assert dto['outline']==source['plan']['page_plan'];assert dto['confirmation_selection'] is None;assert dto['confirmed_at'] is None;assert p['confirmed_plan_revision'] is None
    response=c.post('/api/v1/projects/'+pid+'/generations',json={'expected_revision':p['revision'],'plan_revision':rev},headers={'Idempotency-Key':'recommendation-is-not-confirmation'});assert response.status_code==409
    for invalid in ('A','unknown-direction'):
        response=c.post(f'/api/v1/projects/{pid}/plans/{rev}/confirm',json={'expected_revision':p['revision'],'input_snapshot_hash':p['input_snapshot_hash'],'selection':{'direction_id':invalid}},headers={'Idempotency-Key':'invalid-'+invalid});assert response.status_code==422
    changed=copy.deepcopy(dto['design']['directions'][0]['page_plan']);changed['pages'][0]['title']='An unplanned edit'
    response=c.post(f'/api/v1/projects/{pid}/plans/{rev}/confirm',json={'expected_revision':p['revision'],'input_snapshot_hash':p['input_snapshot_hash'],'selection':{'direction_id':IDS[0],'page_plan':changed}},headers={'Idempotency-Key':'changed-page-plan'});assert response.status_code==409
    response=c.post(f'/api/v1/projects/{pid}/plans/{rev}/confirm',json={'expected_revision':p['revision'],'input_snapshot_hash':p['input_snapshot_hash'],'selection':{'direction_id':IDS[0]}},headers={'Idempotency-Key':'actual-user-confirmation'});assert response.status_code==200
    p=getp(c,pid);response=c.post('/api/v1/projects/'+pid+'/generations',json={'expected_revision':p['revision'],'plan_revision':rev},headers={'Idempotency-Key':'queue-prepare'});assert response.status_code==202;assert response.json()['stage']=='prepare'
    jid=response.json()['job_id']
    with app.state.sessions.begin() as s:
        plan=s.scalar(select(Plan).where(Plan.project_id==pid,Plan.revision==rev));job=s.get(Job,jid)
        assert plan.confirmation_selection=={'direction_id':IDS[0]};assert job.input_snapshot['confirmation_selection']==plan.confirmation_selection;assert job.input_snapshot['plan_revision']==rev;assert job.input_snapshot['hash']==p['input_snapshot_hash']
        assert job.status=='queued'
        claimed=Repository(s).claim_next();assert claimed.id==jid;job=claimed
        cp_id=job.checkpoint_id
        # A newer unrelated checkpoint must never replace the explicit binding.
        s.add(Checkpoint(project_id=pid,issuer=owner.issuer,subject=owner.sub,storage_key=f'projects/{pid}/checkpoints/{uuid.uuid4()}',manifest={'session_id':'wrong-latest-session','test_fixture_only':True}));s.flush()
        controller=Dispatcher(app.state.sessions,RuntimeConfig(Path(app.state.settings.data_root)),None,None)
        a,baseline=controller._attempt(s,job);context=a.snapshot['control_context']['stage2_confirmation']
        assert job.checkpoint_id==cp_id;assert a.session_id!='wrong-latest-session';assert baseline['test_fixture_only'] is True
        assert context['channel']=='website';assert context['plan_revision']==rev;assert context['input_snapshot_sha256']==job.input_snapshot['hash'];assert context['selection']==plan.confirmation_selection
        assert context['outline']==source['plan']['page_plan'];assert context['design']==plan.design;assert context['selected_direction']==plan.design['directions'][0]
        assert context['selected_direction']['page_plan']==source['plan']['page_plan'];assert context['selected_direction']['page_expression']==source['plan']['options']['design_directions']['candidates'][0]['page_expression']
        output('prepare-context.test-only.json',{'test_fixture_only':True,'job_id':jid,'checkpoint_id':cp_id,'input_snapshot_hash':job.input_snapshot['hash'],'stage2_confirmation':context,'stage1_confirmation':a.snapshot['control_context']['stage1_confirmation']})
        assert a.snapshot['control_context']['stage1_confirmation']['channel']=='website';assert not s.scalars(select(UsageRecord).where(UsageRecord.project_id==pid)).all()

def test_real_projection_rejects_confirmation_after_source_upload(pg_api,frozen):
    app,c,owner,pid,rev=prepare_options(pg_api,frozen);old=getp(c,pid)
    response=c.post('/api/v1/projects/'+pid+'/assets',files={'file':('more.md',b'New material changes the immutable input snapshot.','text/markdown')});assert response.status_code==201
    current=getp(c,pid);assert current['input_snapshot_hash']!=old['input_snapshot_hash'];assert current['confirmed_plan_revision'] is None
    response=c.post(f'/api/v1/projects/{pid}/plans/{rev}/confirm',json={'expected_revision':current['revision'],'input_snapshot_hash':old['input_snapshot_hash'],'selection':{'direction_id':IDS[0]}},headers={'Idempotency-Key':'stale-source'});assert response.status_code==409;assert response.json()['detail']['code']=='stale_input_snapshot'
    with app.state.sessions() as s:
        plan=s.scalar(select(Plan).where(Plan.project_id==pid,Plan.revision==rev));assert plan.confirmed_at is None;assert plan.confirmation_selection is None
