"""Real PostgreSQL tests for parse-only leases; no model Job or credential is used."""
import hashlib,os,uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
import pytest
from sqlalchemy import create_engine,select,text
from sqlalchemy.orm import sessionmaker
from ppt_service.server.models import Asset,Project,Job,ExecutionSlot,now
from ppt_service.server.migrate import migrate
from ppt_service.server.repository import Repository
from ppt_service.control.materials import MaterialParser,ControlError
from ppt_service.control.execution import RuntimeConfig
from ppt_service.control.parsing import parse_inputs
from ppt_service.control.pods import Docker,DockerSandbox

class Scopes:
    def __init__(self):self.stopped=True;self.stops=[]
    def prove_stopped(self,uid):return self.stopped
    def stop_and_prove(self,uid):self.stops.append(uid);return self.stopped
class SourceSandbox:
    def __init__(self):self.calls=0
    def parse(self,inputs,inventory):self.calls+=1;return parse_inputs(inputs,inventory)

def test_00_incremental_migration_preserves_original_rows():
    url=os.getenv('TEST_DATABASE_URL')
    if not url:pytest.skip('TEST_DATABASE_URL required for real PostgreSQL tests')
    engine=create_engine(url)
    with engine.connect() as c:
        before=c.execute(text('SELECT id, project_id, storage_key, sha256, bytes FROM assets ORDER BY id')).all()
    migrate(engine);migrate(engine)
    with engine.connect() as c:
        after=c.execute(text('SELECT id, project_id, storage_key, sha256, bytes FROM assets ORDER BY id')).all()
        assert after==before;assert c.scalar(text('SELECT max(revision) FROM api_schema_revision'))==2
        assert c.scalar(text('SELECT count(*) FROM assets WHERE parse_attempt IS NULL'))==0
    engine.dispose()

@pytest.fixture
def ctx(tmp_path):
    url=os.getenv('TEST_DATABASE_URL')
    if not url:pytest.skip('TEST_DATABASE_URL required for real PostgreSQL tests')
    engine=create_engine(url,pool_size=5);migrate(engine);sessions=sessionmaker(engine,expire_on_commit=False)
    root=tmp_path
    config=RuntimeConfig(root);sandbox=SourceSandbox();scopes=Scopes()
    with sessions.begin() as s:
        p=Project(issuer='urn:material-test',subject=str(uuid.uuid4()),title='Parse-only test',settings={'page_count':3},external_model_ack=True);s.add(p);s.flush();pid=p.id
        aid=str(uuid.uuid4());key=f'projects/{pid}/assets/{aid}/original';original=root/key;original.parent.mkdir(parents=True);data=('# Material public source\n'+'Useful content 中文 '*200).encode();original.write_bytes(data)
        a=Asset(id=aid,project_id=pid,storage_key=key,display_name='public.md',sha256=hashlib.sha256(data).hexdigest(),media_type='text/markdown',bytes=len(data));s.add(a);s.flush()
        snap=Repository(s).snapshot(p)['hash']
    runner=MaterialParser(sessions,config,sandbox,scopes,project_ids=(pid,))
    yield sessions,runner,aid,pid,original,sandbox,scopes,snap
    engine.dispose()

def result_for(c,text='Parsed public source'):
    return {'input_snapshot_sha256':c.fingerprint,'sources':[{'id':c.asset_id,'path':c.filename,'sha256':c.sha256,'parse_status':'succeeded','text':text,'pages':None}]}

def test_concurrent_claim_at_most_one(ctx):
    sessions,runner,aid,pid,*_=ctx;barrier=Barrier(2)
    def claim():barrier.wait();return runner.claim()
    with ThreadPoolExecutor(2) as pool:claims=list(pool.map(lambda _:claim(),range(2)))
    assert sum(c is not None for c in claims)==1
    with sessions() as s:assert s.get(Asset,aid).parse_attempt==1;assert s.get(Asset,aid).parse_status=='parsing';assert s.get(ExecutionSlot,1).job_id is None

def test_ready_summary_persists_across_runner_restart_and_hash_unchanged(ctx):
    sessions,runner,aid,pid,original,sandbox,scopes,snap=ctx
    raw=original.read_bytes();assert runner.tick()['status']=='ready'
    with sessions() as s:
        a=s.get(Asset,aid);p=s.get(Project,pid);assert a.parse_status=='ready';assert len(a.text_summary)==2000;assert a.parse_error is None;assert a.parse_attempt==1
        assert Repository(s).snapshot(p)['hash']==snap;assert p.revision==1;assert not s.scalars(select(Job).where(Job.project_id==pid)).all()
    replacement=MaterialParser(sessions,runner.config,sandbox,scopes,project_ids=(pid,));assert replacement.tick()['status']=='idle';assert sandbox.calls==1;assert original.read_bytes()==raw

@pytest.mark.parametrize('change',['removed','deleted','source_changed','expired'])
def test_late_callback_cannot_write_after_invalidation(ctx,change):
    sessions,runner,aid,pid,*_=ctx;c=runner.claim()
    with sessions.begin() as s:
        a=s.get(Asset,aid)
        if change=='removed':a.removed=True
        elif change=='deleted':s.get(Project,pid).deletion_requested=True
        elif change=='source_changed':a.sha256='f'*64
        else:a.parse_lease_until=now()-timedelta(seconds=1)
    assert runner.complete(c,result_for(c)) is False
    with sessions() as s:assert s.get(Asset,aid).text_summary is None;assert s.get(Asset,aid).parse_status!='ready'

@pytest.mark.parametrize('problem',['missing','changed','symlink'])
def test_failed_source_is_safe_and_never_executes_parser(ctx,problem):
    sessions,runner,aid,pid,original,sandbox,*_=ctx
    if problem=='missing':original.unlink();expected='source_unavailable'
    elif problem=='changed':original.write_bytes(b'Changed original');expected='source_hash_mismatch'
    else:
        target=original.with_name('other');target.write_bytes(b'not authorized');original.unlink();original.symlink_to(target);expected='source_unavailable'
    assert runner.tick()['status']=='failed';assert sandbox.calls==0
    with sessions() as s:
        a=s.get(Asset,aid);assert a.parse_error==expected;assert a.text_summary is None
    assert runner.tick()['status']=='idle'

@pytest.mark.parametrize('reason,expected',[
    ('pdf_page_limit','pdf_page_limit'),
    ('scanned_pdf_unsupported','scanned_pdf_unsupported'),
    ('/private/source.pdf SECRET','parse_failed'),
])
def test_material_failure_preserves_only_known_parser_reasons(ctx,reason,expected):
    sessions,runner,aid,pid,original,sandbox,*_=ctx
    def reject(*args):raise ControlError(reason)
    sandbox.parse=reject
    assert runner.tick()['status']=='failed'
    with sessions() as s:
        asset=s.get(Asset,aid)
        assert asset.parse_error==expected and asset.text_summary is None
        assert asset.parse_status=='failed'
        assert not s.scalars(select(Job).where(Job.project_id==pid)).all()


def test_restart_requires_stop_proof_and_old_token_never_returns(ctx):
    sessions,runner,aid,pid,original,sandbox,scopes,_=ctx;old=runner.claim()
    with sessions.begin() as s:s.get(Asset,aid).parse_lease_until=now()-timedelta(seconds=1)
    scopes.stopped=False
    replacement=MaterialParser(sessions,runner.config,sandbox,scopes,project_ids=(pid,))
    assert replacement.recover_one()['status']=='stopping';assert replacement.claim() is None;assert replacement.complete(old,result_for(old)) is False
    scopes.stopped=True
    with sessions.begin() as s:s.get(Asset,aid).parse_lease_until=now()-timedelta(seconds=1)
    assert replacement.recover_one()['status']=='queued';new=replacement.claim();assert new.token!=old.token;assert new.attempt==2
    assert replacement.complete(old,result_for(old)) is False;assert replacement.execute(new)['status']=='ready';assert scopes.stops==[old.scope_uid,old.scope_uid]

def test_paused_old_launcher_flock_prevents_requeue_and_late_execution(ctx):
    sessions,runner,aid,pid,original,sandbox,scopes,_=ctx;old=runner.claim()
    with runner.scope_lock(aid) as acquired:
        assert acquired
        with sessions.begin() as s:s.get(Asset,aid).parse_lease_until=now()-timedelta(seconds=1)
        replacement=MaterialParser(sessions,runner.config,sandbox,scopes,project_ids=(pid,))
        assert replacement.recover_one()['status']=='waiting_for_old_parser';assert replacement.claim() is None;assert not scopes.stops
    with sessions.begin() as s:s.get(Asset,aid).parse_lease_until=now()-timedelta(seconds=1)
    assert replacement.recover_one()['status']=='queued';assert replacement.execute(old)['status']=='stale';assert sandbox.calls==0

def test_duplicate_execution_and_mismatched_result_reject(ctx):
    sessions,runner,aid,pid,original,sandbox,*_=ctx;c=runner.claim();bad=result_for(c);bad['sources'][0]['sha256']='0'*64
    assert runner.complete(c,bad)=='failed'
    assert runner.execute(c)['status']=='stale';assert sandbox.calls==0
    with sessions() as s:assert s.get(Asset,aid).parse_error=='parse_result_mismatch'

@pytest.mark.parametrize('blocked',['removed','deleted','model_job'])
def test_claim_skips_blocked_asset_or_project(ctx,blocked):
    sessions,runner,aid,pid,*_=ctx
    with sessions.begin() as s:
        if blocked=='removed':s.get(Asset,aid).removed=True
        elif blocked=='deleted':s.get(Project,pid).deletion_requested=True
        else:s.get(Project,pid).active_job_id=str(uuid.uuid4())
    assert runner.claim() is None

@pytest.mark.skipif(os.getenv('PPT_RUN_MATERIAL_DOCKER')!='true',reason='Explicit real parser integration flag required')
def test_actual_network_none_parser_commits_real_pg_summary(tmp_path):
    url=os.environ['TEST_DATABASE_URL'];engine=create_engine(url);migrate(engine);sessions=sessionmaker(engine,expire_on_commit=False)
    root=Path(os.environ['PPT_CONTROL_HOST_DATA_ROOT'])/('material-test-'+uuid.uuid4().hex);root.mkdir(parents=True)
    config=RuntimeConfig(root,code_root=Path(os.environ['PPT_CONTROL_HOST_CODE_ROOT']))
    with sessions.begin() as s:
        p=Project(issuer='urn:material-docker-test',subject=str(uuid.uuid4()),title='Real parser test',settings={},external_model_ack=True);s.add(p);s.flush();pid=p.id
        aid=str(uuid.uuid4());key=f'projects/{pid}/assets/{aid}/original';path=root/key;path.parent.mkdir(parents=True);data='Real network-none material extraction 中文'.encode();path.write_bytes(data)
        s.add(Asset(id=aid,project_id=pid,storage_key=key,display_name='source.md',sha256=hashlib.sha256(data).hexdigest(),media_type='text/markdown',bytes=len(data)))
    assert config.allow_paid_execution is False
    runner=MaterialParser(sessions,config,DockerSandbox(config),project_ids=(pid,));assert runner.tick()['status']=='ready'
    with sessions() as s:
        a=s.get(Asset,aid);assert a.text_summary=='Real network-none material extraction 中文';scope=a.parse_scope_uid
    assert Docker().ids(scope)==();engine.dispose()

if __name__=='__main__':
    import sys
    from sqlalchemy.engine import make_url
    os.environ['TEST_DATABASE_URL']=make_url(os.environ['DATABASE_URL']).set(database='ppt_service_integration').render_as_string(hide_password=False)
    raise SystemExit(pytest.main([__file__,'/opt/ppt-service/tests/server','-q','--tb=short','-p','no:cacheprovider']))
