"""HTTP cutover admission on synthetic history; no engine or live data is used."""
import uuid

import pytest
from sqlalchemy import select

from ppt_service.server.models import Checkpoint, Job, Plan, UserQuota, Version
from test_api import accepted, confirm, ctx, getp, plan, project


def old_confirmed_plan(context, phase='plan_initial'):
    app, client, _ = context
    current = project(client)
    for kind in ('plan_initial', 'plan_options'):
        response = plan(client, current, kind, kind)
        assert response.status_code == 202
        revision, snapshot_hash = accepted(app, response.json()['id'], kind)
        current = getp(client, current)
        assert confirm(client, current, revision, snapshot_hash, kind, 'confirm-' + kind).status_code == 200
        current = getp(client, current)
        if kind == phase:
            return current, revision


@pytest.mark.parametrize('kind', ['plan_options', 'prepare', 'revise'])
def test_new_legacy_recovery_is_rejected_before_job_revision_or_quota_changes(ctx, monkeypatch, kind):
    monkeypatch.delenv('PPT_EXECUTION_BACKEND', raising=False)
    app, client, _ = ctx
    current, revision = old_confirmed_plan(ctx, 'plan_initial' if kind == 'plan_options' else 'plan_options')
    if kind == 'revise':
        with app.state.sessions.begin() as session:
            source = session.scalar(select(Plan).where(Plan.project_id == current['id'], Plan.revision == revision))
            source_job = session.scalar(select(Job).where(Job.project_id == current['id']).order_by(Job.created_at.desc()))
            version = Version(project_id=current['id'], issuer='https://issuer', subject='alice',
                job_id=source_job.id, checkpoint_id=source.checkpoint_id, artifact_root='synthetic-history',
                manifest={'slides': [{'title': 'Existing published page'}]}, status='published')
            session.add(version); session.flush(); version_id = version.id
    with app.state.sessions() as session:
        count = len(session.scalars(select(Job)).all())
        quota = session.get(UserQuota, ('https://issuer', 'alice'))
        charged = quota.generation_count, quota.revision_count
    monkeypatch.setenv('PPT_EXECUTION_BACKEND', 'agent-service')
    if kind == 'plan_options':
        response = plan(client, current, 'after-cutover', kind)
    else:
        route = 'generations' if kind == 'prepare' else 'revisions'
        body = {'expected_revision': current['revision']}
        body.update({'plan_revision': revision} if kind == 'prepare' else
                    {'base_version_id': version_id, 'feedback': 'Clarify this page', 'pages': [1]})
        response = client.post(f"/api/v1/projects/{current['id']}/{route}", json=body,
                               headers={'Idempotency-Key': 'after-cutover'})
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'legacy_checkpoint_requires_replan'
    refreshed = getp(client, current)
    assert refreshed['revision'] == current['revision'] and refreshed['active_job_id'] is None
    with app.state.sessions() as session:
        assert len(session.scalars(select(Job)).all()) == count
        quota = session.get(UserQuota, ('https://issuer', 'alice'))
        assert (quota.generation_count, quota.revision_count) == charged


def test_cutover_keeps_original_idempotent_reply_and_allows_explicit_replanning(ctx, monkeypatch):
    monkeypatch.delenv('PPT_EXECUTION_BACKEND', raising=False)
    app, client, _ = ctx
    current, _ = old_confirmed_plan(ctx)
    original = plan(client, current, 'before-cutover', 'plan_options')
    assert original.status_code == 202
    monkeypatch.setenv('PPT_EXECUTION_BACKEND', 'agent-service')
    replay = plan(client, current, 'before-cutover', 'plan_options')
    assert replay.status_code == 202 and replay.json() == original.json()
    assert client.post('/api/v1/jobs/' + original.json()['id'] + '/cancel',
                       headers={'Idempotency-Key': 'stop-old-queued'}).status_code == 200
    current = getp(client, current)
    restarted = plan(client, current, 'explicit-new-planning', 'plan_initial')
    assert restarted.status_code == 202 and restarted.json()['kind'] == 'plan_initial'
    with app.state.sessions() as session:
        assert session.get(Job, restarted.json()['id']).checkpoint_id is None
        assert session.scalars(select(Checkpoint)).all()  # Historical checkpoints are retained.


def test_cutover_preserves_existing_published_versions_and_downloads(ctx, monkeypatch):
    monkeypatch.delenv('PPT_EXECUTION_BACKEND', raising=False)
    app, client, _ = ctx
    current, revision = old_confirmed_plan(ctx)
    with app.state.sessions.begin() as session:
        source = session.scalar(select(Plan).where(Plan.project_id == current['id'], Plan.revision == revision))
        job = session.scalar(select(Job).where(Job.project_id == current['id']))
        version_id = str(uuid.uuid4())
        prefix = f"projects/{current['id']}/versions/{version_id}/published"
        version = Version(id=version_id, project_id=current['id'], issuer='https://issuer', subject='alice',
            job_id=job.id, checkpoint_id=source.checkpoint_id, artifact_root=prefix,
            manifest={'pptx_key': prefix + '/presentation.pptx', 'slides': []}, status='published')
        session.add(version)
        folder = app.state.storage.root / prefix
        folder.mkdir(parents=True); (folder / 'presentation.pptx').write_bytes(b'PK synthetic retained history')
    monkeypatch.setenv('PPT_EXECUTION_BACKEND', 'agent-service')
    versions = client.get(f"/api/v1/projects/{current['id']}/versions")
    assert versions.status_code == 200 and versions.json()[0]['id'] == version_id
    downloaded = client.get('/api/v1/versions/' + version_id + '/download')
    assert downloaded.status_code == 200 and downloaded.content == b'PK synthetic retained history'
