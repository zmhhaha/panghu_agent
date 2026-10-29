"""Deterministic adapters exist only in tests. No model admissions or fake results ship."""
import copy,hashlib,json,os,sqlite3,uuid,zipfile
from datetime import timedelta
from pathlib import Path
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine,select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from ppt_service.server.models import *
from ppt_service.server.auth import Identity
from ppt_service.server.repository import Repository,ACTIVE
from ppt_service.server.migrate import migrate
from ppt_service.control.execution import *
from ppt_service.control.staging import *
from ppt_service.control.parsing import parse_inputs,docx
from ppt_service.control.publisher import validate_private,complete_index
from ppt_service.control.dispatcher import Dispatcher
from ppt_service.control.pods import secure_container,DockerBackend
from ppt_service.worker.execute import read_task
from ppt_service.worker.prompts import input_message

class FakeBackend:
    ready=True
    def __init__(self,*,needs_input=False,hold=False):self.creates=0;self.running=False;self.stops=0;self.needs_input=needs_input;self.hold=hold
    def reconcile(self,a):return Handle(a.uid,a.job_id,a.attempt,a.token,'worker-'+a.uid,'proxy-'+a.uid)
    def create(self,a,layout):
        self.creates+=1;self.running=self.hold
        task=read_task(layout.task/'task.json');assert '/work/inputs/' in input_message(task)
        project=layout.project/'deck';project.mkdir(exist_ok=True)
        with sqlite3.connect(layout.home/'state.db') as db:
            db.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY)');db.execute('INSERT OR IGNORE INTO sessions VALUES (?)',(a.session_id,))
        (project/'source.md').write_text('Public deterministic test source')
        if a.phase=='plan_initial':plan={'outline':{'slides':[]},'design':{'mode':'free_design'}}
        else:
            page_plan={'pages':[{'ordinal':n,'title':f'Public test page {n}','purpose':'Explain the deterministic public test source.'} for n in range(1,4)]}
            plan={'outline':page_plan,'design':{'directions':[{'id':d,'name':f'Direction {d}','page_plan':page_plan,'production_parameters':{'language':'zh'}} for d in 'ABC']}}
        if self.needs_input:plan={'approval_status':'denied','tool_name':'terminal'}
        atomic_json(layout.result/'manifest.json',{'schema_version':1,'phase':a.phase,'status':'needs_input' if self.needs_input else 'awaiting_confirmation','input_snapshot_sha256':a.snapshot['hash'],'project_root':'deck','plan':plan,'questions':['operator approval needed'] if self.needs_input else [],'artifacts':[],'slides':[],'quality':{}})
        atomic_json(layout.result/'execution.json',{'job_id':a.job_id,'attempt':a.attempt,'phase':a.phase,'session_id':a.session_id,'status':'completed','gateway_stopped':True,'preview_stopped':True})
        atomic_json(layout.result/'agent-response.private.json',{'output':'Original actor response preserved'})
        return self.reconcile(a)
    def observe(self,h):return Observation(self.running,None if self.running else 0)
    def stop(self,h):self.stops+=1;self.running=False
    def prove_stopped(self,h):return StopProof(h.uid,not self.running,('worker-id','proxy-id'))
    def cleanup(self,h):assert not self.running
class TestSandbox:
    def parse(self,root,inventory):return parse_inputs(root,inventory)
    def validate(self,root,request):return validate_private(root,request)

@pytest.fixture(params=['sqlite','postgres'])
def store(request,tmp_path):
    url=os.getenv('TEST_DATABASE_URL') if request.param=='postgres' else 'sqlite://'
    if not url:pytest.skip('Dedicated PostgreSQL integration database not configured')
    engine=create_engine(url,**({'poolclass':StaticPool,'connect_args':{'check_same_thread':False}} if request.param=='sqlite' else {}));migrate(engine)
    sessions=sessionmaker(engine,expire_on_commit=False)
    owner=Identity('https://control.test',str(uuid.uuid4()))
    with sessions.begin() as s:
        p=Project(issuer=owner.issuer,subject=owner.sub,title='Control test',settings={'page_count':3},external_model_ack=True);s.add(p);s.flush();pid=p.id
        aid=str(uuid.uuid4());key=f'projects/{pid}/assets/{aid}/original';path=tmp_path/key;path.parent.mkdir(parents=True);path.write_bytes(b'# Public source\nChinese: \xe4\xb8\xad\xe6\x96\x87')
        s.add(Asset(id=aid,project_id=pid,storage_key=key,display_name='source.md',sha256=hashlib.sha256(path.read_bytes()).hexdigest(),media_type='text/markdown',bytes=path.stat().st_size));s.flush()
        r=Repository(s);r.admission_lock();r.quota(owner);j=r.enqueue(p,owner,'plan_initial',1);jid=j.id
    yield sessions,owner,pid,jid,tmp_path
    # Every test explicitly cancels or completes its own active job.
    with sessions.begin() as s:
        r=Repository(s)
        for j in s.scalars(select(Job).where(Job.project_id==pid,Job.status.in_(ACTIVE))):
            r.cancel(j)
            if j.status=='cancelling':r.prove_stopped(j.id,j.attempt,j.fencing_token,job_uid=j.job_uid,all_writers_stopped=True)
    engine.dispose()
def configured(root):return RuntimeConfig(root,state=RuntimeState.CONFIGURED)
def test_disabled_does_not_claim(store):
    sessions,owner,pid,jid,root=store
    controller=Dispatcher(sessions,RuntimeConfig(root),FakeBackend(),TestSandbox())
    assert controller.tick()['status']=='disabled'
    with sessions() as s:assert s.get(Job,jid).status=='queued';assert s.get(ExecutionSlot,1).job_id is None

@pytest.mark.parametrize('reason,public_code',[
    ('pdf_page_limit','parse_failed'),
    ('scanned_pdf_unsupported','parse_failed'),
    ('sandbox_policy_cleanup_failed','execution_interrupted'),
])
def test_parser_reasons_remain_public_parse_failure_before_model_launch(store,reason,public_code):
    sessions,owner,pid,jid,root=store;backend=FakeBackend()
    class RejectedSource(TestSandbox):
        def parse(self,*args):raise ControlError(reason)
    controller=Dispatcher(sessions,configured(root),backend,RejectedSource())
    assert controller.tick()=={'status':'failed','job_id':jid,'error_code':public_code}
    assert backend.creates==0
    with sessions() as s:
        assert s.get(Job,jid).error_code==public_code
        assert s.get(Job,jid).status=='failed'
        assert s.get(ExecutionSlot,1).job_id is None


def test_plan_pipeline_real_transactions_and_two_gate_context(store):
    sessions,owner,pid,jid,root=store;backend=FakeBackend();controller=Dispatcher(sessions,configured(root),backend,TestSandbox())
    # A subsequent model phase parses a private copy again. Its internal parser
    # vocabulary must not replace the website's material status or hide the
    # summary already delivered to the user by the material parser.
    from ppt_service.server.app import asset_dto
    summary='Public source Chinese: 中文'
    with sessions.begin() as s:
        source=s.scalar(select(Asset).where(Asset.project_id==pid))
        source.parse_status='ready';source.text_summary=summary
    def assert_material_still_ready():
        with sessions() as s:
            material=asset_dto(s.scalar(select(Asset).where(Asset.project_id==pid)))
            assert material['parse_status']=='ready'
            assert material['text_summary']==summary and material['parse_error'] is None
    assert controller.tick()['status']=='started';assert_material_still_ready()
    assert controller.tick()['status']=='succeeded'
    with sessions.begin() as s:
        p=s.get(Project,pid);assert p.stage=='awaiting_confirmation';initial=s.scalar(select(Plan).where(Plan.project_id==pid));assert initial.phase=='plan_initial'
        initial.confirmation_selection={'design_mode':'free_design','template_id':None};initial.confirmed_at=now();p.confirmed_plan_revision=initial.revision
        repo=Repository(s);repo.admission_lock();repo.quota(owner)
        j=repo.enqueue(p,owner,'plan_options',p.revision,{'confirmation_selection':initial.confirmation_selection});j.checkpoint_id=initial.checkpoint_id;options_jid=j.id
    assert controller.tick()['status']=='started'
    assert_material_still_ready()
    a,h=controller.owned[options_jid];task=read_task(controller.stager.layout(a).task/'task.json');message=json.loads(input_message(task).split('\n',1)[1])
    assert message['stage1_confirmation']['channel']=='website';assert message['stage1_confirmation']['selection']['design_mode']=='free_design'
    assert controller.tick()['status']=='succeeded'
    with sessions() as s:
        assert len(s.scalars(select(Checkpoint).where(Checkpoint.project_id==pid)).all())==2
        plans=s.scalars(select(Plan).where(Plan.project_id==pid).order_by(Plan.revision)).all();assert plans[1].phase=='plan_options';assert len(plans[1].design['directions'])==3
    assert backend.creates==2

def test_cancel_running_stops_before_slot_release(store):
    sessions,owner,pid,jid,root=store;backend=FakeBackend(hold=True);controller=Dispatcher(sessions,configured(root),backend,TestSandbox())
    controller.tick()
    with sessions.begin() as s:
        r=Repository(s);r.cancel(s.get(Job,jid));assert s.get(ExecutionSlot,1).job_id==jid
    controller.tick()
    with sessions() as s:
        assert s.get(Job,jid).status=='cancelled';assert s.get(ExecutionSlot,1).job_id is None
        assert not s.scalars(select(Checkpoint).where(Checkpoint.project_id==pid)).all()
    assert backend.stops==1

def test_controller_progress_is_stopped_fenced_and_records_real_work_once(store):
    sessions,owner,pid,jid,root=store;backend=FakeBackend()
    controller=Dispatcher(sessions,configured(root),backend,TestSandbox())
    assert controller.tick()['status']=='started'
    a,h=controller.owned[jid]
    with pytest.raises(ControlError,match='stop_proof_required'):
        controller.progress(jid,a.attempt,a.token,'collecting')
    with pytest.raises(HTTPException) as stale:
        controller.progress(jid,a.attempt,a.token+1,'collecting')
    assert stale.value.detail['code']=='stale_execution'
    original_collect=controller.stager.collect
    original_validate=controller.publisher.validate
    def collect(attempt,layout):
        with sessions() as s:
            j=s.get(Job,jid)
            assert j.stopped and not backend.running
            assert (j.status,j.stage)==('collecting','collecting')
        controller.progress(jid,a.attempt,a.token,'collecting')
        return original_collect(attempt,layout)
    def validate(snapshot,request):
        with sessions() as s:
            j=s.get(Job,jid)
            assert j.stopped and not backend.running
            assert (j.status,j.stage)==('validating','validating')
        controller.progress(jid,a.attempt,a.token,'validating')
        with pytest.raises(ControlError,match='invalid_progress'):
            controller.progress(jid,a.attempt,a.token,'collecting')
        return original_validate(snapshot,request)
    controller.stager.collect=collect;controller.publisher.validate=validate
    assert controller.tick()['status']=='succeeded'
    with sessions() as s:
        events=s.scalars(select(JobEvent).where(JobEvent.job_id==jid).order_by(JobEvent.seq)).all()
        assert [e.type for e in events]==['job.queued','job.started','stage.changed','stage.changed','plan.ready','job.succeeded']
        assert [e.payload for e in events if e.type=='stage.changed']==[{'stage':'collecting'},{'stage':'validating'}]

def test_cancelled_attempt_cannot_emit_progress_or_release_writers_early(store):
    sessions,owner,pid,jid,root=store;backend=FakeBackend(hold=True)
    controller=Dispatcher(sessions,configured(root),backend,TestSandbox())
    assert controller.tick()['status']=='started';a,h=controller.owned[jid]
    with sessions.begin() as s:Repository(s).cancel(s.get(Job,jid))
    with pytest.raises(HTTPException) as cancelled:
        controller.progress(jid,a.attempt,a.token,'collecting')
    assert cancelled.value.detail['code']=='execution_revoked'
    with sessions() as s:
        assert s.get(Job,jid).status=='cancelling' and not s.get(Job,jid).stopped
        assert s.get(ExecutionSlot,1).job_id==jid
        assert not s.scalars(select(JobEvent).where(JobEvent.job_id==jid,JobEvent.type=='stage.changed')).all()
    assert controller.tick()['status']=='stopped'
    with sessions() as s:
        assert s.get(Job,jid).status=='cancelled'
        assert s.get(ExecutionSlot,1).job_id is None

def test_expired_lease_reconciles_never_reexecutes(store):
    sessions,owner,pid,jid,root=store;backend=FakeBackend(hold=True);controller=Dispatcher(sessions,configured(root),backend,TestSandbox());controller.tick()
    with sessions.begin() as s:s.get(Job,jid).lease_until=now()-timedelta(seconds=1)
    replacement=Dispatcher(sessions,configured(root),backend,TestSandbox())
    assert replacement.tick()['status']=='reconciled';assert backend.creates==1
    with sessions() as s:assert s.get(Job,jid).status=='failed';assert s.get(ExecutionSlot,1).job_id is None

def test_needs_input_preserves_actor_and_never_checkpoint(store):
    sessions,owner,pid,jid,root=store;backend=FakeBackend(needs_input=True);controller=Dispatcher(sessions,configured(root),backend,TestSandbox())
    controller.tick();a,_=controller.owned[jid];layout=controller.stager.layout(a)
    original=(layout.result/'manifest.json').read_bytes();assert controller.tick()['status']=='needs_input'
    assert (layout.result/'manifest.json').read_bytes()==original
    result=read_json(layout.audit,'validation-result.json');assert result['status']=='needs_input';assert result['blocked_kind']=='operator_permission';assert result['manifest']['status']=='needs_input'
    with sessions() as s:assert s.get(Job,jid).status=='failed';assert not s.scalars(select(Checkpoint).where(Checkpoint.project_id==pid)).all()

def test_unknown_cost_from_trusted_ledger_after_cancel(store):
    sessions,owner,pid,jid,root=store;backend=FakeBackend(hold=True);controller=Dispatcher(sessions,configured(root),backend,TestSandbox());controller.tick()
    a,h=controller.owned[jid];layout=controller.stager.layout(a);ledger=layout.audit/'proxy-ledger';ledger.mkdir()
    row={'event':'request_finished','proxy_request_id':str(uuid.uuid4()),'job_id':jid,'attempt':1,'phase':'plan_initial','model':'gpt-6.1-sol','outcome':'client_disconnected','input_tokens':3,'output_tokens':None,'cost_usd':None}
    (ledger/'usage.jsonl').write_text(json.dumps(row)+'\n')
    with sessions.begin() as s:Repository(s).cancel(s.get(Job,jid))
    controller.tick()
    with sessions() as s:
        usage=s.scalar(select(UsageRecord).where(UsageRecord.job_id==jid));assert usage.cost_status=='unknown';assert usage.reported_cost is None;assert usage.input_tokens==3

def test_staging_rejects_source_hash_symlink_and_hardlink(tmp_path):
    root=tmp_path;pid=str(uuid.uuid4());aid=str(uuid.uuid4());key=f'projects/{pid}/assets/{aid}/original';p=root/key;p.parent.mkdir(parents=True);p.write_bytes(b'input')
    a=Attempt(str(uuid.uuid4()),pid,1,1,'plan_initial',{'hash':'a'*64,'assets':[{'id':aid,'storage_key':key,'sha256':'0'*64,'bytes':5,'media_type':'text/markdown'}]},'session')
    with pytest.raises(ControlError):Stager(configured(root)).stage(a)
    link=root/'linked';link.symlink_to(p)
    with pytest.raises((ControlError,OSError)):open_regular(root,'linked',100)
    os.link(p,root/'hardlink')
    with pytest.raises(ControlError):open_regular(root,'hardlink',100)

def test_worker_task_all_five_phases_with_explicit_base(tmp_path):
    for phase in sorted(PHASES):
        root=tmp_path/phase;layout=Layout(root,root/'inputs',root/'task',root/'home',root/'project',root/'result',root/'audit',root/'base')
        layout.inputs.mkdir(parents=True);layout.task.mkdir()
        source={'id':str(uuid.uuid4()),'path':'public.md','sha256':'a'*64,'bytes':5,'media_type':'text/markdown'}
        atomic_json(layout.inputs/'inventory.json',{'sources':[source]})
        context={'stage1_confirmation':{'channel':'website','selection':{'design_mode':'free_design'},'outline':{'real':True}},'stage2_confirmation':{'channel':'website','selection':{'direction_id':'B'},'selected_direction':{'id':'B','page_plan':[1,2,3],'production_parameters':{'font':'Noto'}}}}
        base=str(uuid.uuid4()) if phase=='revise' else None
        a=Attempt(str(uuid.uuid4()),str(uuid.uuid4()),1,1,phase,{'hash':'a'*64,'settings':{'page_count':3},'assets':[],'control_context':context},'session',base_version_id=base)
        Stager(configured(root)).finish_inputs(a,layout,{'input_snapshot_sha256':'a'*64,'sources':[]})
        task=read_task(layout.task/'task.json');visible=json.loads(input_message(task).split('\n',1)[1]);assert visible['assets'][0]['path']=='/work/inputs/public.md';assert visible['requirements']['parsed_materials']=='/work/inputs/parsed.json';assert visible['base_version_id']==base
        if phase in ('prepare','generate','revise'):assert visible['stage2_confirmation']['selected_direction']['id']=='B'

def test_docx_bomb_active_external_and_plaintext(tmp_path):
    p=tmp_path/'safe.docx'
    content=b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types" />'
    with zipfile.ZipFile(p,'w') as z:
        z.writestr('[Content_Types].xml',content);z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Public source</w:t></w:r></w:p></w:document>')
    assert docx(p)=='Public source'
    for name,data,compression in [('word/vbaProject.bin',b'bad',zipfile.ZIP_STORED),('word/bomb.xml',b'a'*100000,zipfile.ZIP_DEFLATED),('../traversal',b'bad',zipfile.ZIP_STORED)]:
        path=tmp_path/(str(uuid.uuid4())+'.docx')
        with zipfile.ZipFile(path,'w',compression=compression) as z:z.writestr('[Content_Types].xml',content);z.writestr(name,data)
        with pytest.raises(ControlError):docx(path)

def test_missing_trusted_oracle_prevents_worker_creation(store):
    sessions,owner,pid,jid,root=store;backend=FakeBackend();controller=Dispatcher(sessions,configured(root),backend,TestSandbox())
    controller.tick();controller.tick()
    with sessions.begin() as s:
        plan=s.scalar(select(Plan).where(Plan.project_id==pid));p=s.get(Project,pid)
        repo=Repository(s);repo.admission_lock();repo.quota(owner)
        # Internal queue fixture intentionally has no oracle: admission must stop
        # before a worker is created, even with a readable explicit checkpoint.
        job=repo.enqueue(p,owner,'generate',p.revision);job.checkpoint_id=plan.checkpoint_id;gid=job.id
    result=controller.tick();assert result['status']=='failed';assert backend.creates==1
    with sessions() as s:assert s.get(Job,gid).status=='failed';assert s.get(ExecutionSlot,1).job_id is None

def test_late_cancel_during_validation_cannot_create_checkpoint(store):
    sessions,owner,pid,jid,root=store
    class CancellingSandbox(TestSandbox):
        def validate(self,snapshot,request):
            result=super().validate(snapshot,request)
            assert result['accepted'] is True
            with sessions.begin() as s:Repository(s).cancel(s.get(Job,jid))
            return result
    controller=Dispatcher(sessions,configured(root),FakeBackend(),CancellingSandbox())
    assert controller.tick()['status']=='started'
    assert controller.tick()['status']=='cancelled'
    with sessions() as s:
        assert s.get(Job,jid).status=='cancelled';assert s.get(ExecutionSlot,1).job_id is None
        assert not s.scalars(select(Checkpoint).where(Checkpoint.project_id==pid)).all()
        assert not s.scalars(select(Plan).where(Plan.project_id==pid)).all()
    assert not (root/f'projects/{pid}/checkpoints').exists()

@pytest.mark.parametrize('problem',['missing','corrupt','wrong_session'])
def test_session_proof_rejects_unrecoverable_baseline(tmp_path,problem):
    from ppt_service.control.publisher import check_session_database
    home=tmp_path/'home';home.mkdir()
    if problem=='corrupt':(home/'state.db').write_bytes(b'not a SQLite database')
    elif problem=='wrong_session':
        with sqlite3.connect(home/'state.db') as db:
            db.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY)');db.execute('INSERT INTO sessions VALUES (?)',('other-session',))
    expected={'missing':'session_database_missing','corrupt':'session_database_inconsistent','wrong_session':'checkpoint_session_not_found'}[problem]
    with pytest.raises(ControlError,match=expected):check_session_database(tmp_path,'expected-session')

@pytest.mark.parametrize('valid',[True,False])
def test_session_database_connection_closes_before_scratch_cleanup(tmp_path,monkeypatch,valid):
    from contextlib import closing
    from ppt_service.control.publisher import check_session_database
    home=tmp_path/'home';home.mkdir()
    with closing(sqlite3.connect(home/'state.db')) as db:
        with db:
            db.execute('CREATE TABLE sessions (id TEXT PRIMARY KEY)')
            db.execute('INSERT INTO sessions VALUES (?)',('expected-session' if valid else 'other-session',))
    connect=sqlite3.connect;connections=[]
    def observed_connect(*args,**kwargs):
        connection=connect(*args,**kwargs);connections.append(connection);return connection
    monkeypatch.setattr(sqlite3,'connect',observed_connect)
    if valid:check_session_database(tmp_path,'expected-session')
    else:
        with pytest.raises(ControlError,match='checkpoint_session_not_found'):
            check_session_database(tmp_path,'expected-session')
    assert len(connections)==1
    with pytest.raises(sqlite3.ProgrammingError,match='closed'):
        connections[0].execute('SELECT 1')

def test_secure_template_and_disabled_real_backend(tmp_path):
    args=secure_container('worker','uid');text=' '.join(args)
    assert '--user 10000:10000' in text and '--read-only' in args and '--cap-drop' in args and '--pids-limit' in args
    assert '--privileged' not in args and '--pid' not in args and '/var/run/docker.sock' not in text
    assert not DockerBackend(RuntimeConfig(tmp_path)).ready
