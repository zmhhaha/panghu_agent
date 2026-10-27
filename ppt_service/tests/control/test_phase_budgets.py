"""Fixed phase deadlines across claim, heartbeat and mocked Docker launch."""
from datetime import timedelta, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ppt_service.control.execution import Attempt, Layout, RuntimeConfig, RuntimeState
from ppt_service.control.pods import DockerBackend
from ppt_service.server.auth import Identity
from ppt_service.server.migrate import migrate
from ppt_service.server.models import ExecutionSlot, Job, Project, now
from ppt_service.server.phase_limits import phase_pod_timeout_seconds, phase_timeout_seconds
from ppt_service.server.repository import Repository


PHASE_BUDGETS = [('plan_initial', 600), ('plan_options', 1200), ('prepare', 1800),
                 ('generate', 1800), ('revise', 1800)]


@pytest.mark.parametrize('phase', ['unknown', None, []])
def test_unknown_phase_cannot_acquire_an_execution_budget(phase):
    with pytest.raises(ValueError, match='Unsupported execution phase'):
        phase_timeout_seconds(phase)


@pytest.mark.parametrize('phase,seconds', PHASE_BUDGETS)
def test_claim_sets_one_absolute_phase_deadline_and_heartbeat_cannot_extend_it(tmp_path, monkeypatch, phase, seconds):
    engine = create_engine('sqlite://')
    migrate(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    instant = now()
    monkeypatch.setattr('ppt_service.server.repository.now', lambda: instant)
    owner = Identity('urn:phase-test', str(uuid.uuid4()))
    with sessions.begin() as session:
        project = Project(issuer=owner.issuer, subject=owner.sub, title='Synthetic deadline test',
                          settings={}, external_model_ack=True)
        session.add(project)
        session.flush()
        repo = Repository(session)
        queued = repo.enqueue(project, owner, phase, project.revision)
        claimed = repo.claim_next()
        assert claimed.id == queued.id
        deadline = claimed.deadline
        assert deadline == instant + timedelta(seconds=seconds)
        assert phase_timeout_seconds(phase) == seconds and phase_pod_timeout_seconds(phase) == seconds + 120
        job_id, token = claimed.id, claimed.fencing_token
    monkeypatch.setattr('ppt_service.server.repository.now', lambda: instant + timedelta(seconds=10))
    with sessions.begin() as session:
        repo = Repository(session)
        assert repo.claim_next() is None
        repo.heartbeat(job_id, 1, token, lease_seconds=90)
        actual = session.get(Job, job_id).deadline
        if actual.tzinfo is None:
            actual = actual.replace(tzinfo=timezone.utc)
        assert actual == deadline
        assert session.get(ExecutionSlot, 1).job_id == job_id
    engine.dispose()


@pytest.mark.parametrize('phase,seconds', PHASE_BUDGETS)
def test_docker_worker_uses_shared_phase_budget_without_relaxing_container_contract(tmp_path, monkeypatch, phase, seconds):
    calls = []
    class RecordedDocker:
        def ids(self, uid):
            return ()
        def call(self, *arguments, **kwargs):
            calls.append(arguments)
            return 0, b''
    class RecordedFirewall:
        def install(self, uid, upstream):
            pass
    monkeypatch.setattr('ppt_service.control.pods.os.chown', lambda *_: None)
    credential = tmp_path / 'synthetic-upstream.env'
    credential.write_text('SYNTHETIC_ONLY=1\n')
    config = RuntimeConfig(tmp_path, state=RuntimeState.CONFIGURED, allow_paid_execution=True,
                           policy_approved=True, upstream_env_file=credential)
    attempt = Attempt(str(uuid.uuid4()), str(uuid.uuid4()), 1, 1, phase, {'hash': 'a' * 64}, 'test-session')
    root = tmp_path / 'attempt'
    names = ('inputs', 'task', 'home', 'project', 'result', 'audit', 'base')
    for name in names:
        (root / name).mkdir(parents=True)
    layout = Layout(root, *(root / name for name in names))
    backend = DockerBackend(config, RecordedDocker(), RecordedFirewall())
    handle = backend.create(attempt, layout)
    worker = next(arguments for arguments in calls if arguments[0] == 'create' and handle.worker_name in arguments)
    assert worker[worker.index('--deadline') + 1] == str(seconds)
    assert worker[worker.index('--user') + 1] == '10000:10000'
    assert '--read-only' in worker and '--privileged' not in worker
    assert worker[worker.index('--cap-drop') + 1] == 'ALL'
