"""Real HTTP planning recovery on isolated synthetic data; no model or live DB."""
from datetime import timedelta
import uuid

import pytest
from sqlalchemy import select

from ppt_service.server.auth import Identity
from ppt_service.server.models import Checkpoint, Job, Plan, Project, now
from ppt_service.server.repository import Repository
from test_api import accepted, confirm, ctx, getp, plan, project


def confirmed_initial(context):
    app, client, _ = context
    current = project(client)
    response = plan(client, current)
    assert response.status_code == 202
    revision, snapshot_hash = accepted(app, response.json()['id'])
    current = getp(client, current)
    assert confirm(client, current, revision, snapshot_hash).status_code == 200
    return getp(client, current), revision, snapshot_hash


def fail_stopped(app, job_id):
    with app.state.sessions.begin() as session:
        repo = Repository(session)
        job = repo.claim_next()
        assert job.id == job_id
        repo.attach_execution(job.id, job.attempt, job.fencing_token, job_uid='stopped-test-worker')
        repo.prove_stopped(job.id, job.attempt, job.fencing_token,
                           job_uid='stopped-test-worker', all_writers_stopped=True)
        repo.finish_failed(job.id, job.attempt, job.fencing_token, error_code='execution_timeout')


def test_stage2_failure_retains_confirmation_and_requires_explicit_fresh_retry(ctx):
    app, client, _ = ctx
    before, revision, snapshot_hash = confirmed_initial(ctx)
    first = plan(client, before, 'first-options', 'plan_options')
    assert first.status_code == 202
    current = getp(client, before)
    assert current['confirmed_plan_revision'] == revision
    assert current['latest_job']['id'] == first.json()['id']
    # Stage1 confirmation is not a Stage2 direction confirmation.
    denied = client.post(f"/api/v1/projects/{before['id']}/generations",
                         json={'expected_revision': current['revision'], 'plan_revision': revision},
                         headers={'Idempotency-Key': 'no-implicit-generation'})
    assert denied.status_code == 409 and denied.json()['detail']['code'] == 'options_confirmation_required'
    fail_stopped(app, first.json()['id'])
    current = getp(client, before)
    assert current['active_job_id'] is None and current['confirmed_plan_revision'] == revision
    assert current['latest_job']['status'] == 'failed'
    assert current['latest_job']['error_code'] == 'execution_timeout'
    # Replaying the original intent only returns that original failed job.
    replay = plan(client, before, 'first-options', 'plan_options')
    assert replay.status_code == 202 and replay.json()['id'] == first.json()['id']
    retried = plan(client, current, 'explicit-options-retry', 'plan_options')
    assert retried.status_code == 202 and retried.json()['id'] != first.json()['id']
    with app.state.sessions() as session:
        initial = session.scalar(select(Plan).where(Plan.project_id == before['id'], Plan.revision == revision))
        old = session.get(Job, first.json()['id'])
        new = session.get(Job, retried.json()['id'])
        assert initial.confirmed_at is not None
        assert initial.confirmation_selection == {'design_mode': 'free_design', 'template_id': None}
        assert old.status == 'failed' and old.stopped is True
        assert new.status == 'queued' and new.checkpoint_id == old.checkpoint_id == initial.checkpoint_id
        assert new.input_snapshot['hash'] == snapshot_hash
        assert new.input_snapshot['confirmation_selection'] == initial.confirmation_selection
        assert len(session.scalars(select(Checkpoint).where(Checkpoint.project_id == before['id'])).all()) == 1


def test_stage2_success_clears_prior_confirmation_until_real_direction_selection(ctx):
    app, client, _ = ctx
    before, initial_revision, _ = confirmed_initial(ctx)
    queued = plan(client, before, 'options', 'plan_options')
    assert queued.status_code == 202
    revision, snapshot_hash = accepted(app, queued.json()['id'], 'plan_options')
    current = getp(client, before)
    assert current['confirmed_plan_revision'] is None
    assert current['plans'][-1]['phase'] == 'plan_options' and current['plans'][-1]['confirmed_at'] is None
    for unconfirmed in (initial_revision, revision):
        denied = client.post(f"/api/v1/projects/{before['id']}/generations",
                             json={'expected_revision': current['revision'], 'plan_revision': unconfirmed},
                             headers={'Idempotency-Key': 'unconfirmed-' + str(unconfirmed)})
        assert denied.status_code == 409
    assert confirm(client, current, revision, snapshot_hash, 'plan_options', 'select-direction').status_code == 200
    current = getp(client, before)
    generated = client.post(f"/api/v1/projects/{before['id']}/generations",
                            json={'expected_revision': current['revision'], 'plan_revision': revision},
                            headers={'Idempotency-Key': 'explicit-generation'})
    assert generated.status_code == 202 and generated.json()['kind'] == 'prepare'


@pytest.mark.parametrize('fault,code,status', [
    ('stale_revision', 'stale_project_revision', 409),
    ('changed_material', 'initial_confirmation_required', 409),
    ('missing_confirmation', 'initial_confirmation_required', 409),
    ('other_owner', 'not_found', 404),
    ('other_issuer', 'not_found', 404),
])
def test_stage2_retry_keeps_revision_input_confirmation_and_owner_gates(ctx, fault, code, status):
    app, client, owner = ctx
    before, revision, _ = confirmed_initial(ctx)
    first = plan(client, before, 'first-options', 'plan_options')
    assert first.status_code == 202
    fail_stopped(app, first.json()['id'])
    current = getp(client, before)
    if fault == 'stale_revision':
        current['revision'] -= 1
    elif fault == 'changed_material':
        uploaded = client.post(f"/api/v1/projects/{before['id']}/assets",
                               files={'file': ('new.md', b'New authorized material changes the snapshot.', 'text/markdown')})
        assert uploaded.status_code == 201
        current = getp(client, before)
    elif fault == 'missing_confirmation':
        with app.state.sessions.begin() as session:
            session.scalar(select(Plan).where(Plan.project_id == before['id'], Plan.revision == revision)).confirmed_at = None
    elif fault == 'other_owner':
        owner[0] = Identity('https://issuer', 'another-owner')
    elif fault == 'other_issuer':
        owner[0] = Identity('https://another-issuer', 'alice')
    rejected = plan(client, current, 'rejected-retry', 'plan_options')
    assert rejected.status_code == status and rejected.json()['detail']['code'] == code
    with app.state.sessions() as session:
        assert len(session.scalars(select(Job).where(Job.project_id == before['id'], Job.kind == 'plan_options')).all()) == 1


def test_legacy_stage2_failure_can_reconfirm_existing_stage1_through_normal_api(ctx):
    app, client, _ = ctx
    before, revision, snapshot_hash = confirmed_initial(ctx)
    failed = plan(client, before, 'old-options', 'plan_options')
    assert failed.status_code == 202
    fail_stopped(app, failed.json()['id'])
    # Synthetic representation of the pre-fix enqueue; no production row edit.
    with app.state.sessions.begin() as session:
        session.get(Project, before['id']).confirmed_plan_revision = None
    current = getp(client, before)
    assert plan(client, current, 'legacy-missing-confirmation', 'plan_options').status_code == 409
    assert confirm(client, current, revision, snapshot_hash, key='explicit-reconfirmation').status_code == 200
    retried = plan(client, getp(client, before), 'legacy-explicit-retry', 'plan_options')
    assert retried.status_code == 202


def test_project_detail_latest_job_is_nullable_scoped_ordered_and_sanitized(ctx):
    app, client, owner = ctx
    current = project(client)
    assert getp(client, current)['latest_job'] is None
    assert all('latest_job' not in item for item in client.get('/api/v1/projects').json())
    other = project(client)
    timestamp = now()
    newest = 'ffffffff-ffff-4fff-8fff-ffffffffffff'
    with app.state.sessions.begin() as session:
        rows = [
            ('00000000-0000-4000-8000-000000000001', current['id'], owner[0], timestamp - timedelta(seconds=1)),
            ('00000000-0000-4000-8000-000000000002', current['id'], owner[0], timestamp),
            (newest, current['id'], owner[0], timestamp),
            (str(uuid.uuid4()), other['id'], owner[0], timestamp + timedelta(seconds=5)),
            (str(uuid.uuid4()), current['id'], Identity('https://issuer', 'foreign'), timestamp + timedelta(seconds=10)),
        ]
        for identifier, project_id, identity, created in rows:
            session.add(Job(id=identifier, project_id=project_id, issuer=identity.issuer, subject=identity.sub,
                            kind='plan_options', stage='plan_options', status='failed', error_code='execution_timeout',
                            input_snapshot={'private_material': 'must not appear'}, created_at=created,
                            session_id='private-session', run_id='private-run', job_uid='private-worker'))
    latest = getp(client, current)['latest_job']
    assert latest['id'] == latest['job_id'] == newest
    assert latest['status'] == 'failed' and latest['error_code'] == 'execution_timeout'
    assert set(latest) == {'id', 'job_id', 'project_id', 'kind', 'status', 'stage', 'event_cursor',
                           'attempt', 'error_code', 'base_version_id'}
    owner[0] = Identity('https://issuer', 'another-owner')
    assert client.get('/api/v1/projects/' + current['id']).status_code == 404
