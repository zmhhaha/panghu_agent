"""DB-backed checks at the last boundary before a model worker can start."""
from datetime import timedelta
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from ppt_service.server.models import Project, Job, Asset, now
from ppt_service.server.migrate import migrate
from ppt_service.server.repository import Repository
from ppt_service.server.auth import Identity
from ppt_service.control.attempt_builder import build_attempt
from ppt_service.control.dispatcher import build_controller
from ppt_service.control.execution import RuntimeConfig, RuntimeState, ControlError
from ppt_service.control.materials import MaterialParser

@pytest.fixture
def runtime(tmp_path, monkeypatch):
    from ppt_service.control import kubernetes
    monkeypatch.setenv('PPT_EXECUTION_BACKEND','kubernetes')
    monkeypatch.setenv('PPT_WORKER_IMAGE','registry.example/ppt@sha256:'+'a'*64)
    monkeypatch.setattr(kubernetes,'KubernetesClient',lambda namespace:SimpleNamespace())
    engine=create_engine('sqlite://')
    migrate(engine)
    sessions=sessionmaker(engine,expire_on_commit=False)
    config=RuntimeConfig(tmp_path,state=RuntimeState.CONFIGURED,allow_paid_execution=True,policy_approved=True,lease_seconds=300)
    controller,materials=build_controller(sessions,config)
    with sessions.begin() as s:
        owner=Identity('https://issuer.test','owner')
        p=Project(issuer=owner.issuer,subject=owner.sub,title='Public canary',settings={'page_count':3},external_model_ack=True)
        s.add(p);s.flush()
        repo=Repository(s);j=repo.enqueue(p,owner,'plan_initial',1);j=repo.claim_next(300)
        attempt,_=build_attempt(s,j);j.job_uid=attempt.uid
    yield sessions,controller,materials,attempt
    engine.dispose()

@pytest.mark.parametrize('revoked',['cancel','expired','consent'])
def test_prelaunch_db_recheck_prevents_revoked_paid_work(runtime,revoked):
    sessions,controller,materials,a=runtime
    assert controller.backend.ready
    assert isinstance(materials,MaterialParser)
    with sessions.begin() as s:
        j=s.get(Job,a.job_id)
        if revoked=='cancel':Repository(s).cancel(j)
        elif revoked=='expired':j.deadline=now()-timedelta(seconds=1)
        else:s.get(Project,a.project_id).external_model_ack=False
    with pytest.raises((HTTPException,ControlError)):
        controller.backend.before_worker(a)

def test_valid_prelaunch_claim_renews_only_its_own_lease(runtime):
    sessions,controller,_,a=runtime
    controller.backend.before_worker(a)
    with sessions() as s:
        j=s.get(Job,a.job_id)
        assert j.status=='starting' and not j.stopped and j.job_uid==a.uid

def test_unsupported_backend_fails_before_admission(tmp_path,monkeypatch):
    monkeypatch.setenv('PPT_EXECUTION_BACKEND','unknown')
    with pytest.raises(ControlError,match='unsupported_execution_backend'):
        build_controller(None,RuntimeConfig(tmp_path))

@pytest.mark.parametrize('scope_failure',[False,True])
def test_material_claim_has_recoverable_scope_before_commit(tmp_path,scope_failure):
    engine=create_engine('sqlite://');migrate(engine)
    sessions=sessionmaker(engine,expire_on_commit=False)
    with sessions.begin() as s:
        p=Project(issuer='https://issuer.test',subject='owner',title='Public material',settings={})
        s.add(p);s.flush()
        asset=Asset(project_id=p.id,storage_key='unread-until-execute',display_name='source.md',
                    sha256='b'*64,media_type='text/markdown',bytes=1)
        s.add(asset);s.flush();asset_id=asset.id
    scopes=[]
    def ensure(uid):
        if scope_failure:raise ControlError('scope_unavailable')
        scopes.append(uid)
    parser=MaterialParser(sessions,RuntimeConfig(tmp_path),SimpleNamespace(),SimpleNamespace(ensure_scope=ensure))
    if scope_failure:
        with pytest.raises(ControlError,match='scope_unavailable'):parser.claim()
    else:
        claim=parser.claim()
        assert scopes==[claim.scope_uid]
    with sessions() as s:
        a=s.get(Asset,asset_id)
        assert a.parse_status==('queued' if scope_failure else 'parsing')
        assert a.parse_attempt==(0 if scope_failure else 1)
        assert bool(a.parse_scope_uid)==(not scope_failure)
    engine.dispose()
