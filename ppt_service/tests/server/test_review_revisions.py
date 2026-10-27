"""Real HTTP draft-branch admission and controller fencing; no model calls.

The shared source fixture contains synthetic bytes, not programmatic acceptance
evidence. These tests exercise the mounted resolver and actual database state.
"""
import copy
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from ppt_service.control.attempt_builder import build_attempt
from ppt_service.control.dispatcher import Dispatcher
from ppt_service.control.execution import ControlError, RuntimeConfig, RuntimeState
from ppt_service.server.auth import Identity
from ppt_service.server.models import Checkpoint, Job, Plan, Project, Version, now
from ppt_service.server.repository import Repository
from ppt_service.server.review_revisions import recheck_review_source
from ppt_service.tests.control.test_control import FakeBackend, TestSandbox
from test_api import ctx
from review_fixture import seed_review


@pytest.fixture
def draft(ctx):
    app, client, owner = ctx
    seeded = seed_review(app)
    with app.state.sessions.begin() as session:
        original = session.get(Job, seeded['jid'])
        project = session.get(Project, seeded['pid'])
        checkpoint_id = str(uuid.uuid4())
        plan = Plan(project_id=project.id, revision=2, phase='plan_options',
                    input_snapshot_hash=original.input_snapshot['hash'],
                    outline={'pages': [{'ordinal': n} for n in range(1, 4)]},
                    design={'directions': [{'id': 'A', 'name': 'Synthetic direction'}]},
                    checkpoint_id=checkpoint_id, confirmation_selection={'direction_id': 'A'},
                    confirmed_at=now())
        context = {'stage2_confirmation': {'channel': 'website', 'plan_revision': plan.revision,
                    'input_snapshot_sha256': plan.input_snapshot_hash,
                    'selection': plan.confirmation_selection, 'outline': plan.outline,
                    'design': plan.design, 'selected_direction': plan.design['directions'][0]}}
        checkpoint = Checkpoint(id=checkpoint_id, project_id=project.id,
                               issuer=project.issuer, subject=project.subject,
                               storage_key=f'projects/{project.id}/checkpoints/{checkpoint_id}',
                               manifest={'schema_version': 1, 'session_id': original.session_id,
                               'input_snapshot_sha256': original.input_snapshot['hash'],
                               'control_context': context})
        session.add(checkpoint)
        session.flush()
        session.add(plan)
        original.checkpoint_id = checkpoint.id
        project.confirmed_plan_revision = plan.revision
    response = client.get(f"/api/v1/projects/{seeded['pid']}/versions/{seeded['vid']}/acceptance")
    assert response.status_code == 200, response.text
    snapshot = response.json()
    seeded.update(context=context, body={'expected_revision': snapshot['source_revision'],
                  'base_version_id': seeded['vid'], 'feedback': 'Clarify the title on page 2.',
                  'pages': [2], 'review_binding': snapshot['binding'],
                  'review_revision': snapshot['revision'], 'external_model_ack': True}, snapshot=snapshot)
    return app, client, owner, seeded


def revise(client, draft, key='review-revise', **changes):
    return client.post(f"/api/v1/projects/{draft['pid']}/review-revisions",
                       json=draft['body'] | changes, headers={'Idempotency-Key': key})


def test_review_revision_queues_explicit_draft_branch_with_real_confirmed_lineage(draft):
    app, client, _, data = draft
    response = revise(client, data)
    assert response.status_code == 202, response.text
    with app.state.sessions.begin() as session:
        original = session.get(Version, data['vid'])
        checkpoint = session.get(Checkpoint, original.checkpoint_id)
        queued = session.get(Job, response.json()['job_id'])
        project = session.get(Project, data['pid'])
        assert queued.kind == 'revise' and queued.status == 'queued'
        assert queued.base_version_id == original.id and queued.checkpoint_id == checkpoint.id
        assert queued.input_snapshot['feedback'] == data['body']['feedback']
        assert queued.input_snapshot['pages'] == [2]
        assert queued.input_snapshot['review_base']['target_status'] == 'review'
        assert queued.input_snapshot['review_base']['review_binding'] == data['body']['review_binding']
        assert project.revision == data['body']['expected_revision'] + 1
        assert project.active_job_id == queued.id and project.stage == 'revising'
        assert original.status == 'review' and original.manifest['publication_authorization'] is False
        assert checkpoint.manifest['eligible_as_revision_base'] is False
        assert len(session.scalars(select(Version)).all()) == 1
        claim = Repository(session).claim_next()
        attempt, meta = build_attempt(session, claim)
        assert attempt.base_version_id == original.id
        assert attempt.snapshot['control_context'] == data['context']
        assert meta['eligible_as_revision_base'] is False
        recheck_review_source(session, app.state.storage, claim)


def test_same_review_intent_replays_once_and_checks_current_ownership(draft):
    app, client, owner, data = draft
    one = revise(client, data)
    two = revise(client, data)
    assert one.status_code == two.status_code == 202 and one.json() == two.json()
    assert revise(client, data, feedback='Different feedback').status_code == 409
    with app.state.sessions() as session:
        assert len(session.scalars(select(Job).where(Job.kind == 'revise')).all()) == 1
    owner[0] = Identity('https://issuer', 'another-owner')
    assert revise(client, data).status_code == 404


@pytest.mark.parametrize('change,status,code', [
    ('project_revision', 409, 'stale_project_revision'),
    ('source_binding', 409, 'review_source_changed'),
    ('review_revision', 409, 'review_records_changed'),
    ('feedback', 422, 'review_feedback_required'),
    ('pages', 422, 'invalid_slide_number'),
    ('duplicate_pages', 422, 'invalid_slide_number'),
    ('consent', 422, 'external_model_ack_required'),
    ('fresh_consent', 422, 'external_model_ack_required'),
    ('active_job', 409, 'project_busy'),
    ('input_changed', 409, 'review_source_changed'),
    ('missing_confirmation', 409, 'review_checkpoint_unavailable'),
    ('source_bytes', 503, 'review_evidence_unavailable'),
    ('other_owner', 404, 'not_found'),
    ('other_issuer', 404, 'not_found'),
])
def test_review_branch_fails_closed_before_queueing(draft, change, status, code):
    app, client, owner, data = draft
    body = copy.deepcopy(data['body'])
    if change == 'project_revision':body['expected_revision'] -= 1
    elif change == 'source_binding':body['review_binding']['pptx_sha256'] = '0' * 64
    elif change == 'review_revision':body['review_revision'] += 1
    elif change == 'feedback':body['feedback'] = '   '
    elif change == 'pages':body['pages'] = [4]
    elif change == 'duplicate_pages':body['pages'] = [2, 2]
    elif change == 'fresh_consent':body['external_model_ack'] = False
    elif change == 'source_bytes':
        (app.state.storage.root / data['cp_key'] / 'project/deck/spec_lock.md').write_bytes(b'Changed bytes')
    elif change == 'other_owner':owner[0] = Identity('https://issuer', 'another-owner')
    elif change == 'other_issuer':owner[0] = Identity('https://another-issuer', 'alice')
    else:
        with app.state.sessions.begin() as session:
            project = session.get(Project, data['pid'])
            if change == 'consent':project.external_model_ack = False
            elif change == 'active_job':project.active_job_id = data['jid']
            elif change == 'input_changed':project.settings = {'page_count': 4}
            elif change == 'missing_confirmation':
                session.scalar(select(Plan).where(Plan.project_id == data['pid'])).confirmed_at = None
    response = client.post(f"/api/v1/projects/{data['pid']}/review-revisions", json=body,
                           headers={'Idempotency-Key': 'rejected-draft'})
    assert response.status_code == status, response.text
    assert response.json()['detail']['code'] == code
    with app.state.sessions() as session:
        assert not session.scalars(select(Job).where(Job.kind == 'revise')).all()


def test_saved_review_revision_conflict_requires_explicit_reread(draft):
    app, client, _, data = draft
    snapshot = data['snapshot']
    saved = client.post(f"/api/v1/projects/{data['pid']}/versions/{data['vid']}/acceptance/records",
             headers={'Idempotency-Key': 'page-observations'}, json={
                 'kind': 'visual_review', 'expected_revision': 0, 'binding': snapshot['binding'],
                 'pages': [{'number': row['number'], 'preview_sha256': row['preview_sha256'],
                            'verdict': 'pending', 'issues': []} for row in snapshot['pages']],
                 'note': 'Synthetic observed review'})
    assert saved.status_code == 200, saved.text
    stale = revise(client, data)
    assert stale.status_code == 409 and stale.json()['detail']['code'] == 'review_records_changed'
    current = revise(client, data, key='new-review-intent', review_revision=1)
    assert current.status_code == 202


def test_published_revision_api_still_rejects_review_baseline(draft):
    _, client, _, data = draft
    body = {key: value for key, value in data['body'].items() if not key.startswith('review_') and key != 'external_model_ack'}
    response = client.post(f"/api/v1/projects/{data['pid']}/revisions", json=body,
                           headers={'Idempotency-Key': 'published-intent'})
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'review_version_not_revision_base'


@pytest.mark.parametrize('fault', ['target', 'checkpoint', 'binding', 'source_job', 'engine', 'phase', 'profile', 'format'])
def test_attempt_builder_rejects_unbound_or_other_engine_review_jobs(draft, fault):
    app, client, _, data = draft
    response = revise(client, data)
    assert response.status_code == 202
    with app.state.sessions.begin() as session:
        job = Repository(session).claim_next()
        changed = copy.deepcopy(job.input_snapshot)
        if fault == 'target':changed['review_base']['target_status'] = 'published'
        elif fault == 'checkpoint':changed['review_base']['checkpoint_manifest_sha256'] = '0' * 64
        elif fault == 'binding':changed['review_base']['review_binding']['pptx_sha256'] = '0' * 64
        elif fault == 'source_job':changed['review_base']['base_job_id'] = str(uuid.uuid4())
        elif fault == 'phase':job.kind = 'generate'
        elif fault == 'profile':changed['review_base']['review_binding']['runtime']['profile'] = 'ppt-master:unsupported'
        elif fault == 'format':changed['review_base']['review_binding']['runtime']['checkpoint_format'] = 'different-native-session'
        job.input_snapshot = changed
        with pytest.raises(ControlError):
            build_attempt(session, job, engine='pi' if fault == 'engine' else 'hermes')


@pytest.mark.parametrize('fault', ['source', 'extra_source', 'home', 'preview', 'pptx',
                                  'receipt', 'transport', 'missing_receipt',
                                  'checkpoint_manifest', 'project_revision', 'input_changed',
                                  'source_stage', 'source_unstopped'])
def test_review_source_rechecked_before_worker_launch(draft, fault):
    app, client, _, data = draft
    assert revise(client, data).status_code == 202
    root = app.state.storage.root
    paths = {'source': data['cp_key'] + '/project/deck/spec_lock.md',
             'extra_source': data['cp_key'] + '/project/deck/new-source.md',
             'home': data['cp_key'] + '/home/state.db',
             'preview': data['artifact_key'] + '/2.png',
             'pptx': data['artifact_key'] + '/presentation.pptx',
             'receipt': data['evidence_key'] + '/delivery/candidate-delivery.json',
             'transport': data['evidence_key'] + '/transport-manifest.json',
             'checkpoint_manifest': data['cp_key'] + '/checkpoint.json'}
    if fault in paths:
        (root / paths[fault]).write_bytes(b'Changed immutable evidence')
    elif fault == 'missing_receipt':
        (root / data['evidence_key'] / 'delivery/candidate-delivery.json').unlink()
    else:
        with app.state.sessions.begin() as session:
            if fault == 'project_revision':session.get(Project, data['pid']).revision += 1
            elif fault == 'input_changed':session.get(Project, data['pid']).settings = {'page_count': 4}
            elif fault == 'source_stage':session.get(Job, data['jid']).stage = 'running'
            elif fault == 'source_unstopped':session.get(Job, data['jid']).stopped = False
    backend = FakeBackend()
    controller = Dispatcher(app.state.sessions,
                           RuntimeConfig(app.state.storage.root, state=RuntimeState.CONFIGURED),
                           backend, TestSandbox(), oracle_provider=lambda *_: {'expected_charts': [], 'source_sha256': []})
    result = controller.tick()
    assert result['status'] == 'failed' and backend.creates == 0
    with app.state.sessions() as session:
        assert session.get(Version, data['vid']).status == 'review'


def test_review_branch_never_publishes_even_with_internal_pass_flag(draft):
    app, client, _, data = draft
    assert revise(client, data).status_code == 202
    with app.state.sessions.begin() as session:
        repo = Repository(session)
        job = repo.claim_next()
        repo.attach_execution(job.id, job.attempt, job.fencing_token, job_uid='synthetic-stopped')
        repo.prove_stopped(job.id, job.attempt, job.fencing_token,
                          job_uid='synthetic-stopped', all_writers_stopped=True)
        with pytest.raises(HTTPException) as error:
            repo.publish_version(job.id, job.attempt, job.fencing_token, manifest={},
                                 artifact_root=f"projects/{data['pid']}/versions/new",
                                 checkpoint_key='', checkpoint_manifest={}, independently_validated=True)
        assert error.value.detail['code'] == 'review_revision_not_publishable'
        assert len(session.scalars(select(Version)).all()) == 1
