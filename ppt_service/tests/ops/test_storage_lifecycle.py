"""Server-only actual current PostgreSQL/file-tree lifecycle acceptance.

Only a new per-case database on an explicitly supplied private Unix socket is
accepted. Never binds production PostgreSQL, PVCs, keys, models or identities.
"""
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from ppt_service.deploy.ops import storage_lifecycle as lifecycle
from ppt_service.server.auth import Identity
from ppt_service.server.migrate import migrate
from ppt_service.server.models import (Project, Job, Asset, AgentOutbox, ExecutionSlot,
                                       Checkpoint, Version)
from ppt_service.server.repository import Repository


@pytest.fixture
def database(tmp_path):
    raw = os.environ.get('PPT_LIFECYCLE_TEST_ADMIN_URL')
    if not raw:
        pytest.skip('explicit isolated server PostgreSQL Unix socket required')
    url = make_url(raw)
    require = lifecycle.require
    socket = url.query.get('host', '')
    require(url.get_backend_name() == 'postgresql' and url.database == 'postgres'
            and url.host is None and url.password is None and url.username == 'appuser'
            and socket.startswith('/ppt-lifecycle-socket') and '..' not in socket.split('/'),
            'isolated_lifecycle_admin_socket_required')
    admin = create_engine(url, isolation_level='AUTOCOMMIT')
    name = 'ppt_lifecycle_' + uuid.uuid4().hex
    with admin.connect() as connection:
        connection.execute(text('CREATE DATABASE ' + name))
    engine = create_engine(url.set(database=name))
    try:
        migrate(engine)
        root = tmp_path / 'private-data'
        root.mkdir(mode=0o700)
        project_id, job_id = str(uuid.uuid4()), str(uuid.uuid4())
        owner = Identity('https://synthetic.invalid', 'lifecycle-fixture')
        with Session(engine) as session, session.begin():
            project = Project(id=project_id, issuer=owner.issuer, subject=owner.sub,
                              title='Synthetic lifecycle project', stage='ready')
            session.add(project)
            session.flush()
            session.add(Job(id=job_id, project_id=project_id, issuer=owner.issuer,
                subject=owner.sub, kind='generate', stage='generate', status='succeeded',
                stopped=True, attempt=1, fencing_token=4, input_snapshot={'hash': 'a' * 64}))
        yield engine, root, project_id, job_id, owner
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text('DROP DATABASE ' + name))
        admin.dispose()


def write(root, name, data=b'synthetic retained bytes'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def age(root, days=8):
    stamp = time.time() - days * 86400
    for folder, dirs, files in os.walk(root, topdown=False):
        for name in files:
            os.utime(Path(folder) / name, (stamp, stamp))
        os.utime(folder, (stamp, stamp))


def make_retained(database):
    engine, root, project_id, job_id, owner = database
    prefix = 'projects/' + project_id
    cpkey, artifact = prefix + '/checkpoints/' + str(uuid.uuid4()), prefix + '/versions/' + str(uuid.uuid4())
    source = prefix + '/agent-results/' + job_id + '/4'
    write(root, cpkey + '/checkpoint.json', b'{"synthetic":"checkpoint"}')
    write(root, artifact + '/presentation.pptx', b'synthetic old deck, not a real PPTX')
    write(root, source + '/project/project.json', b'synthetic lineage source')
    with Session(engine) as session, session.begin():
        cp = Checkpoint(project_id=project_id, issuer=owner.issuer, subject=owner.sub,
                        storage_key=cpkey, manifest={'agent_source': {'storage_key': source}})
        session.add(cp)
        session.flush()
        version = Version(project_id=project_id, job_id=job_id, issuer=owner.issuer, subject=owner.sub,
                          checkpoint_id=cp.id, artifact_root=artifact, manifest={}, status='review')
        session.add(version)
    return cpkey, artifact, source


def test_staging_expiry_removes_exact_scratch_and_keeps_checkpoint_source_old_version(database):
    engine, root, project_id, job_id, _ = database
    cpkey, artifact, source = make_retained(database)
    scratch = 'projects/' + project_id + '/attempts/' + job_id + '/1-4'
    write(root, scratch + '/result/private-candidate', b'old stopped scratch')
    age(root)
    before = {name: lifecycle.tree(root / name) for name in (cpkey, artifact, source)}
    with Session(engine) as session, session.begin():
        document = lifecycle.plan(session, root, project_id, 'staging')
        assert set(document['candidates']) == {scratch}
        assert (root / scratch).exists(), 'dry run must not remove files'
    with Session(engine) as session, session.begin():
        result = lifecycle.execute(session, document, lifecycle.checksum(document))
        assert result['passed'] and result['retained_data_verified']
    assert not (root / scratch).exists()
    assert {name: lifecycle.tree(root / name) for name in before} == before
    with Session(engine) as session:
        assert session.get(Job, job_id).status == 'succeeded'
        assert len(session.scalars(select(Checkpoint)).all()) == len(session.scalars(select(Version)).all()) == 1


def test_active_project_is_retained_regardless_of_file_age(database):
    engine, root, project_id, job_id, _ = database
    make_retained(database)
    age(root, 999)
    before = lifecycle.tree(root / ('projects/' + project_id))
    with Session(engine) as session, session.begin():
        with pytest.raises(RuntimeError, match='explicit_owner_deletion'):
            lifecycle.plan(session, root, project_id, 'deleted-project')
    assert lifecycle.tree(root / ('projects/' + project_id)) == before


@pytest.mark.parametrize('fault', ['not_ready', 'active_project', 'running_job', 'unproven_stop',
                                   'slot', 'parser_lease', 'parser_running', 'outbox', 'job_lease', 'outbox_fence'])
def test_reclamation_refuses_every_unresolved_writer_or_missing_owner_intent(database, fault):
    engine, root, project_id, job_id, owner = database
    write(root, 'projects/' + project_id + '/assets/' + str(uuid.uuid4()) + '/original')
    with Session(engine) as session, session.begin():
        project = session.get(Project, project_id)
        project.deletion_requested, project.cleanup_ready = True, fault != 'not_ready'
        job = session.get(Job, job_id)
        if fault == 'active_project':
            project.active_job_id = job_id
        elif fault == 'running_job':
            job.status = 'running'
        elif fault == 'unproven_stop':
            job.stopped = False
        elif fault == 'slot':
            session.get(ExecutionSlot, 1).job_id = job_id
        elif fault == 'job_lease':
            from datetime import datetime, timezone, timedelta
            job.lease_until = datetime.now(timezone.utc) + timedelta(seconds=60)
        elif fault in ('parser_lease', 'parser_running'):
            from datetime import datetime, timezone, timedelta
            session.add(Asset(project_id=project_id, storage_key='synthetic/' + str(uuid.uuid4()),
                display_name='source.md', sha256='a' * 64, media_type='text/markdown', bytes=1,
                parse_status='parsing' if fault == 'parser_running' else 'ready',
                parse_lease_until=datetime.now(timezone.utc) + timedelta(seconds=60) if fault == 'parser_lease' else None))
        elif fault in ('outbox', 'outbox_fence'):
            session.add(AgentOutbox(job_id=job_id, attempt=1, fencing_token=4, business_ref='synthetic:' + job_id,
                idempotency_key='synthetic:' + job_id, request={},
                state='submitted' if fault == 'outbox' else 'mapped', acked=fault == 'outbox_fence'))
            if fault == 'outbox_fence':
                job.fencing_token = 5
    before = lifecycle.tree(root / ('projects/' + project_id))
    with Session(engine) as session, session.begin():
        with pytest.raises(RuntimeError):
            lifecycle.plan(session, root, project_id, 'deleted-project')
    assert lifecycle.tree(root / ('projects/' + project_id)) == before


def test_owner_soft_deletion_revokes_api_before_explicit_stopped_file_reclamation(database):
    engine, root, project_id, job_id, owner = database
    make_retained(database)
    with Session(engine) as session, session.begin():
        project = session.get(Project, project_id)
        project.deletion_requested = project.cleanup_ready = True
        project.revision += 1
    with Session(engine) as session, session.begin():
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as error:
            Repository(session).project(project_id, owner)
        assert error.value.status_code == 404
        document = lifecycle.plan(session, root, project_id, 'deleted-project')
    assert (root / ('projects/' + project_id)).exists()
    with Session(engine) as session, session.begin():
        result = lifecycle.execute(session, document, lifecycle.checksum(document))
    assert result['passed'] and not result['database_rows_deleted']
    assert not (root / ('projects/' + project_id)).exists()
    with Session(engine) as session:
        assert session.get(Project, project_id).deletion_requested is True
        assert session.get(Job, job_id).stopped is True
        assert len(session.scalars(select(Version)).all()) == 1, 'immutable audit rows are retained'


@pytest.mark.parametrize('drift', ['revision', 'file', 'plan_digest'])
def test_reviewed_plan_never_authorizes_a_changed_project_or_tree(database, drift):
    engine, root, project_id, job_id, _ = database
    path = write(root, 'projects/' + project_id + '/attempts/' + job_id + '/1-4/file')
    age(root)
    with Session(engine) as session, session.begin():
        document = lifecycle.plan(session, root, project_id, 'staging')
    expected = lifecycle.checksum(document)
    if drift == 'revision':
        with Session(engine) as session, session.begin():
            session.get(Project, project_id).revision += 1
    elif drift == 'file':
        path.write_bytes(b'changed after dry run')
    else:
        expected = 'f' * 64
    with Session(engine) as session, session.begin():
        with pytest.raises(RuntimeError):
            lifecycle.execute(session, document, expected)
    assert path.exists()


@pytest.mark.parametrize('kind', ['symlink', 'hardlink'])
def test_linked_scratch_is_refused_and_outside_target_survives(database, kind):
    engine, root, project_id, job_id, _ = database
    outside = root.parent / 'outside-retained'
    outside.write_bytes(b'must survive')
    target = root / ('projects/' + project_id + '/attempts/' + job_id + '/1-4/linked')
    target.parent.mkdir(parents=True)
    if kind == 'symlink':
        target.symlink_to(outside)
    else:
        os.link(outside, target)
    with Session(engine) as session, session.begin():
        with pytest.raises((RuntimeError, OSError, lifecycle.snapshot.Refused)):
            lifecycle.plan(session, root, project_id, 'staging')
    assert outside.read_bytes() == b'must survive'


def test_recent_stopped_staging_is_retained(database):
    engine, root, project_id, job_id, _ = database
    path = write(root, 'projects/' + project_id + '/attempts/' + job_id + '/1-4/file')
    with Session(engine) as session, session.begin():
        document = lifecycle.plan(session, root, project_id, 'staging')
    assert document['candidates'] == {} and path.exists()


@pytest.mark.parametrize('status', ['cancelled', 'interrupted'])
def test_terminal_interrupted_or_cancelled_only_reclaims_with_positive_stop(database, status):
    engine, root, project_id, job_id, _ = database
    path = write(root, 'projects/' + project_id + '/attempts/' + job_id + '/1-4/file')
    age(root)
    with Session(engine) as session, session.begin():
        session.get(Job, job_id).status = status
    with Session(engine) as session, session.begin():
        document = lifecycle.plan(session, root, project_id, 'staging')
        assert document['candidates'] and path.exists()
    with Session(engine) as session, session.begin():
        session.get(Job, job_id).stopped = False
    with Session(engine) as session, session.begin():
        with pytest.raises(RuntimeError, match='writers_not_proven_stopped'):
            lifecycle.plan(session, root, project_id, 'staging')


@pytest.mark.parametrize('fault', ['identity_missing', 'original_journal_missing', 'pod_remains'])
def test_ready_parser_status_cannot_replace_original_scope_stop_identity(database, fault):
    engine, root, project_id, job_id, _ = database
    aid, token = str(uuid.uuid4()), str(uuid.uuid4())
    uid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'ppt-material:' + aid + ':' + token))
    write(root, 'projects/' + project_id + '/assets/' + aid + '/original')
    with Session(engine) as session, session.begin():
        session.add(Asset(id=aid, project_id=project_id,
            storage_key='projects/' + project_id + '/assets/' + aid + '/original',
            display_name='source.md', sha256='a' * 64, media_type='text/markdown', bytes=1,
            parse_status='ready', parse_attempt=1,
            parse_token=None if fault == 'identity_missing' else token,
            parse_scope_uid=None if fault == 'identity_missing' else uid))
    if fault == 'pod_remains':
        image = 'synthetic.invalid/ppt-validator@sha256:' + 'a' * 64
        journal = dict(schema=1, metadata_prefix='ppt-service.panghuer.top/', sandbox_only=True, phase='parse', project_id=None,
            identity=dict(uid=uid, job_id=str(uuid.uuid5(uuid.NAMESPACE_URL, 'ppt-material-job:' + uid)),
                          attempt=1, token=1), runtime={'namespace': 'ppt-disposable', 'execution_image': image},
            resources=[dict(resource='pods', role='sandbox', name='synthetic-parser',
                            api_uid=str(uuid.uuid4()), deleted=False)])
        write(root, 'kubernetes-authority/' + uid + '.json', json.dumps(journal).encode())
    with Session(engine) as session, session.begin():
        with pytest.raises((RuntimeError, OSError)):
            lifecycle.plan(session, root, project_id, 'staging')
    assert (root / ('projects/' + project_id + '/assets/' + aid + '/original')).exists()


def test_original_parse_journal_positive_is_recorded_and_retained_after_scratch_cleanup(database, monkeypatch):
    """Synthetic journal format follows actual R2 r2 controller-result authority.

    The API absence observer is explicitly substituted; this case proves file
    retention/identity consumption, not a new live Kubernetes stop observation.
    """
    engine, root, project_id, job_id, _ = database
    make_retained(database)
    aid, token = str(uuid.uuid4()), str(uuid.uuid4())
    uid = str(uuid.uuid5(uuid.NAMESPACE_URL, 'ppt-material:' + aid + ':' + token))
    api_uid = str(uuid.uuid4())
    image = 'synthetic.invalid/ppt-validator@sha256:' + 'a' * 64
    container_id = 'containerd://' + 'b' * 64
    receipt = dict(api_uid=api_uid, resource_version='123', terminated=True,
        status={'phase': 'Succeeded', 'containerStatuses': [dict(name='sandbox', restartCount=0,
            containerID=container_id, state={'terminated': dict(exitCode=0,
                finishedAt='2026-11-04T00:00:00Z', containerID=container_id, reason='Completed')})]})
    journal = dict(schema=1, metadata_prefix='ppt-service.panghuer.top/', sandbox_only=True,
        phase='parse', project_id=None, launch_not_started=False,
        identity=dict(uid=uid, job_id=str(uuid.uuid5(uuid.NAMESPACE_URL, 'ppt-material-job:' + uid)),
                      attempt=1, token=1),
        runtime=dict(namespace='ppt-disposable', execution_image=image),
        resources=[dict(resource='pods', role='sandbox', name='ppt-' + uid.replace('-', '') + '-parse',
            api_uid=api_uid, deleted=True, finalized=True, finalizer_release_requested=True,
            execution_image=image, stop_receipt=receipt)])
    authority = write(root, 'kubernetes-authority/' + uid + '.json', json.dumps(journal).encode())
    original = write(root, 'projects/' + project_id + '/assets/' + aid + '/original')
    scratch = write(root, 'projects/' + project_id + '/attempts/' + job_id + '/1-4/file')
    with Session(engine) as session, session.begin():
        session.add(Asset(id=aid, project_id=project_id,
            storage_key=original.relative_to(root).as_posix(), display_name='source.md',
            sha256=hashlib.sha256(original.read_bytes()).hexdigest(), media_type='text/markdown',
            bytes=original.stat().st_size, parse_status='ready', parse_attempt=1,
            parse_token=token, parse_scope_uid=uid))
    age(root)
    observed = []
    def synthetic_absence(namespace, scope_uid):
        observed.append((namespace, scope_uid))
        return True
    monkeypatch.setattr(lifecycle, 'observe_parser_absent', synthetic_absence)
    before = authority.read_bytes()
    with Session(engine) as session, session.begin():
        document = lifecycle.plan(session, root, project_id, 'staging')
        stop = document['database_gate']['parser_stop_receipts'][aid]
        assert stop['scope_uid'] == uid and stop['pods'][0]['api_uid'] == api_uid
        assert stop['pods'][0]['stop_receipt'] == receipt and stop['live_scope_absent']
    with Session(engine) as session, session.begin():
        result = lifecycle.execute(session, document, lifecycle.checksum(document))
    assert result['passed'] and not scratch.exists() and original.exists()
    assert authority.read_bytes() == before and observed == [('ppt-disposable', uid)] * 2
