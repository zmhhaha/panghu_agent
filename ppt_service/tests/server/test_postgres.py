"""Run with TEST_DATABASE_URL set to a disposable migrated PostgreSQL database.
Never points at a production DB implicitly. Dedicated random projects isolate tests.
"""
import os,uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from sqlalchemy import create_engine,select,delete
from sqlalchemy.orm import sessionmaker
from ppt_service.server.models import *
from ppt_service.server.auth import Identity
from ppt_service.server.repository import Repository
from ppt_service.server.migrate import migrate

@pytest.fixture
def pg():
    url=os.getenv('TEST_DATABASE_URL')
    if not url:pytest.skip('TEST_DATABASE_URL not configured; no PostgreSQL concurrency claim')
    engine=create_engine(url,pool_size=5);migrate(engine);sessions=sessionmaker(engine,expire_on_commit=False)
    owner=Identity('https://test-issuer',str(uuid.uuid4()))
    yield sessions,owner
    # Preserve test evidence; disposable DB lifecycle belongs to harness.
    engine.dispose()
def test_pg_parallel_duplicate_admission(pg):
    sessions,owner=pg
    with sessions.begin() as s:
        p=Project(issuer=owner.issuer,subject=owner.sub,title='concurrent',settings={},external_model_ack=True);s.add(p);s.flush();pid=p.id
    barrier=Barrier(2)
    def submit():
        barrier.wait()
        with sessions.begin() as s:
            r=Repository(s)
            def action():return {'job_id':r.enqueue(r.project(pid,owner,True),owner,'plan_initial',1).id}
            return r.idempotent(owner,'test/'+pid,'same',{'request':1},action,
                                authorize=lambda:r.project(pid,owner,True))
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:submit(),range(2)))
    assert results[0]==results[1]
    with sessions.begin() as s:
        rows=s.scalars(select(Job).where(Job.project_id==pid)).all();assert len(rows)==1
        Repository(s).cancel(rows[0])
def test_pg_single_global_claim(pg):
    sessions,owner=pg
    with sessions.begin() as s:
        slot=s.get(ExecutionSlot,1,with_for_update=True)
        if slot.job_id:pytest.skip('Global slot already occupied by another integration test')
        r=Repository(s);r.admission_lock();r.quota(owner)
        ids=[]
        for n in range(2):
            p=Project(issuer=owner.issuer,subject=owner.sub,title='slot',settings={},external_model_ack=True);s.add(p);s.flush();ids.append(r.enqueue(p,owner,'plan_initial',1).id)
    barrier=Barrier(2)
    def claim():
        barrier.wait()
        with sessions.begin() as s:
            j=Repository(s).claim_next();return j.id if j else None
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:claim(),range(2)))
    assert sum(x is not None for x in results)==1
    with sessions.begin() as s:
        r=Repository(s);slot=s.get(ExecutionSlot,1);j=s.get(Job,slot.job_id)
        assert j.id in ids
        r.cancel(j);r.prove_stopped(j.id,j.attempt,j.fencing_token,job_uid=None,all_writers_stopped=True)
        for jid in ids:
            j=s.get(Job,jid)
            if j.status=='queued':r.cancel(j)
def test_pg_input_and_checkpoint_immutable(pg):
    sessions,owner=pg
    with sessions.begin() as s:
        p=Project(issuer=owner.issuer,subject=owner.sub,title='immutable',settings={});s.add(p);s.flush()
        c=Checkpoint(project_id=p.id,issuer=p.issuer,subject=p.subject,storage_key='test/'+str(uuid.uuid4()),manifest={});s.add(c);s.flush();cid=c.id
    from sqlalchemy.exc import DBAPIError
    with pytest.raises(DBAPIError):
        with sessions.begin() as s:s.get(Checkpoint,cid).manifest={'changed':True}
