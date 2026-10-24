"""Offline integration of real review checks, fenced DB state and owner storage.

Official rendering commands use the explicit adapters from test_hermes_delivery;
these tests do not claim a live render, model call or PowerPoint acceptance.
"""
from __future__ import annotations

import copy
import shutil
from dataclasses import replace
from types import SimpleNamespace
import uuid

from fastapi import HTTPException
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from ppt_service.control.execution import Attempt, ControlError, RuntimeConfig
from ppt_service.control.hermes_delivery import run_candidate_delivery
from ppt_service.control.publisher import complete_index
from ppt_service.control.render_validation import hash_file
from ppt_service.control.review_delivery import save_review_candidate
from ppt_service.control.staging import atomic_json, read_json
from ppt_service.server.migrate import migrate
from ppt_service.server.models import Checkpoint, ExecutionSlot, Job, Project, Version
from ppt_service.server.repository import Repository
from ppt_service.tests.control.test_hermes_delivery import case


def index_transport(value):
    rows = [hash_file(value.root, p.relative_to(value.root).as_posix())
            for p in sorted(value.root.rglob('*'))
            if p.is_file() and p != value.root / 'transport-manifest.json']
    data = {'schema_version': 1, 'controller_nonce': value.case.request['validation_nonce'],
            'source_manifest_sha256': value.case.request['source_manifest_sha256'],
            'execution_image': value.case.request['execution_image'],
            'pod_uid': str(uuid.uuid4()), 'sandbox_exit_code': 0, 'writers_stopped': True,
            'transport_verified': True, 'controller_error_code': None,
            'files': rows, 'bytes': sum(row['bytes'] for row in rows)}
    atomic_json(value.root / 'transport-manifest.json', data)
    value.validated['persistent_evidence'].update(file_count=len(rows), bytes=data['bytes'])
    return data


def thaw(root):
    for p in [root, *root.rglob('*')]:
        p.chmod(0o700 if p.is_dir() else 0o600)


@pytest.fixture
def review(case, tmp_path):
    # Retained official tool reports have deep paths; keep the fixture root
    # short enough for Windows hosts without long-path support.
    data_root = tmp_path.parent / ('r' + uuid.uuid4().hex[:6])
    data_root.mkdir()
    shutil.copytree(case.snapshot, data_root / 'snapshot')
    case.snapshot = data_root / 'snapshot'
    case.project = case.snapshot / 'project/deck'
    engine = create_engine('sqlite://')
    migrate(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    previous = str(uuid.uuid4())
    with sessions.begin() as s:
        p = Project(issuer='https://issuer', subject='alice', title='Review draft',
                    latest_version_id=previous, external_model_ack=True)
        s.add(p); s.flush()
        j = Job(id=case.request['job_id'], project_id=p.id, issuer=p.issuer, subject=p.subject,
                kind='generate', stage='generate', input_snapshot={'hash': case.request['input_snapshot_sha256']})
        s.add(j); s.flush(); p.active_job_id = j.id
        repo = Repository(s); claimed = repo.claim_next(); assert claimed.id == j.id
        attempt = Attempt(j.id, p.id, j.attempt, j.fencing_token, j.kind, j.input_snapshot, case.request['session_id'])
        repo.attach_execution(j.id, j.attempt, j.fencing_token, job_uid=attempt.uid, session_id=attempt.session_id)
        repo.prove_stopped(j.id, j.attempt, j.fencing_token, job_uid=attempt.uid, all_writers_stopped=True)
    root = case.snapshot.parent / 'validation-evidence' / case.request['validation_nonce']
    case.output = root / 'delivery'
    receipt = run_candidate_delivery(case.snapshot, case.request, case.oracle, case.authorized, case.output).report()
    validated = {'accepted': False, 'review_candidate': True,
                 'source_manifest_sha256': case.request['source_manifest_sha256'],
                 'validation_nonce': case.request['validation_nonce'],
                 'manifest': complete_index(case.snapshot, case.manifest)[0],
                 'persistent_evidence': {
                     'path': str(root), 'controller_nonce': case.request['validation_nonce'],
                     'source_manifest_sha256': case.request['source_manifest_sha256'],
                     'execution_image': case.request['execution_image'],
                     'transport_manifest': 'transport-manifest.json',
                     'transport_verified': True, 'publication_authorization': False}}
    value = SimpleNamespace(case=case, config=RuntimeConfig(data_root=data_root), sessions=sessions,
                            attempt=attempt, validated=validated, root=root, receipt=receipt,
                            previous=previous)
    index_transport(value)
    yield value
    engine.dispose()
    thaw(data_root)


def save(value, **overrides):
    with value.sessions.begin() as s:
        args = dict(repo=Repository(s), a=value.attempt, snapshot=value.case.snapshot,
                    request=value.case.request, validated=value.validated, config=value.config)
        args.update(overrides)
        result = save_review_candidate(**args)
        return result.id


def assert_no_version(value):
    with value.sessions() as s:
        assert not s.scalars(select(Version)).all()
        assert not s.scalars(select(Checkpoint)).all()
    assert not list(value.config.data_root.glob('projects/*/versions/*'))


def test_stopped_fenced_candidate_saves_private_review_without_publishing(review):
    vid = save(review)
    with review.sessions() as s:
        v = s.get(Version, vid); p = s.get(Project, review.attempt.project_id)
        j = s.get(Job, review.attempt.job_id); cp = s.get(Checkpoint, v.checkpoint_id)
        assert v.status == v.manifest['status'] == cp.manifest['status'] == 'review'
        assert v.manifest['publication_authorization'] is False
        assert v.manifest['eligible_as_revision_base'] is cp.manifest['eligible_as_revision_base'] is False
        assert v.manifest['review_evidence']['execution_image'] == review.case.request['execution_image']
        assert p.latest_version_id == review.previous
        assert p.active_job_id is None and p.stage == 'awaiting_review'
        assert j.status == 'succeeded' and j.stage == 'review' and j.stopped
        assert s.get(ExecutionSlot, 1).job_id is None
        target = review.config.data_root / v.artifact_root
        assert sorted(path.name for path in target.iterdir()) == ['1.png', '2.png', '3.png', 'presentation.pptx']
        assert (target / 'presentation.pptx').read_bytes() == (review.case.project / 'deck.pptx').read_bytes()
        assert (target / '1.png').read_bytes() == (review.root / 'delivery/review-artifacts/slides/1.png').read_bytes()
        assert (target / '1.png').read_bytes() != (review.case.project / '.preview/01.png').read_bytes()
        checkpoint = review.config.data_root / cp.storage_key
        assert read_json(checkpoint, 'checkpoint.json') == cp.manifest
        assert (checkpoint / 'home/state.db').is_file()
        assert not (target / 'candidate-delivery.json').exists()


@pytest.mark.parametrize('fault', ['not_stopped', 'running', 'cancel', 'deleted', 'slot', 'token', 'uid', 'session'])
def test_stop_revocation_and_fencing_fail_before_version_storage(review, fault):
    with review.sessions.begin() as s:
        j = s.get(Job, review.attempt.job_id)
        if fault == 'not_stopped': j.stopped = False
        elif fault == 'running': j.status = 'running'
        elif fault == 'cancel': j.status = 'cancelling'
        elif fault == 'deleted': s.get(Project, j.project_id).deletion_requested = True
        elif fault == 'slot': s.get(ExecutionSlot, 1).job_id = None
        elif fault == 'token': s.get(ExecutionSlot, 1).fencing_token += 1
        elif fault == 'uid': j.job_uid = str(uuid.uuid4())
        elif fault == 'session': j.session_id = 'another-session'
    with pytest.raises((ControlError, HTTPException)):
        save(review)
    assert_no_version(review)


@pytest.mark.parametrize('fault', ['unvalidated', 'accepted', 'path', 'nonce', 'source', 'manifest',
                                  'image', 'unverified', 'count', 'size'])
def test_untrusted_transport_descriptor_never_creates_review_version(review, fault):
    data = copy.deepcopy(review.validated); descriptor = data['persistent_evidence']
    if fault == 'unvalidated': data['review_candidate'] = False
    elif fault == 'accepted': data['accepted'] = True
    elif fault == 'path': descriptor['path'] = str(review.root.parent)
    elif fault == 'nonce': descriptor['controller_nonce'] = str(uuid.uuid4())
    elif fault == 'source': descriptor['source_manifest_sha256'] = 'f' * 64
    elif fault == 'manifest': data['manifest']['status'] = 'failed'
    elif fault == 'image': descriptor['execution_image'] = 'other/image@sha256:' + 'f' * 64
    elif fault == 'unverified': descriptor['transport_verified'] = False
    elif fault == 'count': descriptor['file_count'] += 1
    elif fault == 'size': descriptor['bytes'] += 1
    with pytest.raises((ControlError, FileNotFoundError)):
        save(review, validated=data)
    assert_no_version(review)


@pytest.mark.parametrize('fault', ['stopped', 'exit', 'image', 'changed', 'extra', 'nested_manifest', 'receipt_image'])
def test_controller_transport_is_rehashed_and_runtime_bound(review, fault):
    thaw(review.root)
    transport = read_json(review.root, 'transport-manifest.json')
    if fault == 'stopped': transport['writers_stopped'] = False
    elif fault == 'exit': transport['sandbox_exit_code'] = 1
    elif fault == 'image': transport['execution_image'] = 'other/image@sha256:' + 'f' * 64
    elif fault == 'changed': (review.root / 'delivery/review-artifacts/slides/1.png').write_bytes(b'corrupted')
    elif fault == 'extra': (review.root / 'unindexed').write_text('extra')
    elif fault == 'nested_manifest': (review.root / 'delivery/transport-manifest.json').write_text('extra')
    elif fault == 'receipt_image':
        receipt = read_json(review.case.output, 'candidate-delivery.json')
        receipt['execution_image'] = 'other/image@sha256:' + 'f' * 64
        atomic_json(review.case.output / 'candidate-delivery.json', receipt)
        transport = index_transport(review)
    atomic_json(review.root / 'transport-manifest.json', transport)
    with pytest.raises(ControlError):
        save(review)
    assert_no_version(review)


def test_attempt_rebinding_and_full_disk_leave_no_visible_version(review):
    with pytest.raises(ControlError, match='review_attempt_binding_invalid'):
        save(review, a=replace(review.attempt, session_id='another-session'))
    with pytest.raises(ControlError, match='storage_full'):
        save(review, config=replace(review.config, max_storage_bytes=1))
    assert_no_version(review)
