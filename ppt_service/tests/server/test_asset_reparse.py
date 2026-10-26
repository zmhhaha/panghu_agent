"""Explicit parse retries use the normal API and never enqueue model work."""
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from ppt_service.server.auth import Identity
from ppt_service.server.models import Asset, Job, Project, now
from ppt_service.server.repository import Repository
from test_api import ctx, project, getp, plan


def failed_file(ctx):
    app, client, _ = ctx
    p = project(client)
    asset = client.post(f"/api/v1/projects/{p['id']}/assets",
                        files={'file': ('source.md', b'# Retained source\n', 'text/markdown')}).json()
    with app.state.sessions.begin() as session:
        row = session.get(Asset, asset['id'])
        row.parse_status = 'failed'
        row.parse_error = 'parse_interrupted'
        row.parse_attempt = 2
        row.parse_token = str(uuid.uuid4())
        row.parse_scope_uid = str(uuid.uuid4())
        row.parse_lease_until = None
    return getp(client, p), asset


def retry(client, p, asset, key='retry-file', revision=None):
    return client.post(f"/api/v1/projects/{p['id']}/assets/{asset['id']}/reparse",
                       json={'expected_revision': p['revision'] if revision is None else revision},
                       headers={'Idempotency-Key': key})


def test_reparse_retains_original_and_snapshot_without_starting_a_model(ctx):
    app, client, _ = ctx
    p, asset = failed_file(ctx)
    with app.state.sessions.begin() as session:
        row = session.get(Asset, asset['id'])
        original = app.state.storage.root / row.storage_key
        data = original.read_bytes()
        snapshot = Repository(session).snapshot(session.get(Project, p['id']))
    response = retry(client, p, asset)
    assert response.status_code == 202
    assert response.json() == {'revision': p['revision'] + 1, 'asset_id': asset['id'], 'parse_status': 'queued'}
    current = getp(client, p)
    assert current['assets'][0]['parse_status'] == 'queued'
    assert current['assets'][0]['sha256'] == asset['sha256']
    assert current['confirmed_plan_revision'] is None and current['stage'] == 'draft'
    assert current['active_job_id'] is None and original.read_bytes() == data
    with app.state.sessions() as session:
        row = session.get(Asset, asset['id'])
        assert row.parse_attempt == 0 and row.parse_token is None and row.parse_scope_uid is None
        assert Repository(session).snapshot(session.get(Project, p['id'])) == snapshot
        assert session.scalars(select(Job)).all() == []


def test_reparse_idempotency_replays_original_response_and_rejects_changed_body(ctx):
    _, client, _ = ctx
    p, asset = failed_file(ctx)
    one = retry(client, p, asset)
    two = retry(client, p, asset)
    assert one.status_code == two.status_code == 202 and one.json() == two.json()
    assert getp(client, p)['revision'] == p['revision'] + 1
    changed = retry(client, p, asset, revision=p['revision'] + 1)
    assert changed.status_code == 409 and changed.json()['detail']['code'] == 'idempotency_conflict'


@pytest.mark.parametrize('fault,status,code', [
    ('stale', 409, 'stale_project_revision'),
    ('other_owner', 404, 'not_found'),
    ('other_issuer', 404, 'not_found'),
    ('removed', 404, 'not_found'),
    ('active_job', 409, 'project_busy'),
    ('queued', 409, 'asset_parse_not_failed'),
    ('parsing', 409, 'asset_parse_not_failed'),
    ('stopping', 409, 'asset_parse_not_failed'),
    ('ready', 409, 'asset_parse_not_failed'),
    ('lease', 409, 'asset_parse_busy'),
])
def test_reparse_preserves_access_revision_and_live_attempt_boundaries(ctx, fault, status, code):
    app, client, who = ctx
    p, asset = failed_file(ctx)
    owner = who[0]
    revision = p['revision']
    if fault == 'stale':
        revision -= 1
    elif fault == 'other_owner':
        who[0] = Identity(owner.issuer, 'another-owner')
    elif fault == 'other_issuer':
        who[0] = Identity('https://another-issuer', owner.sub)
    elif fault == 'active_job':
        assert plan(client, p).status_code == 202
        p = getp(client, p)
        revision = p['revision']
    else:
        with app.state.sessions.begin() as session:
            row = session.get(Asset, asset['id'])
            if fault == 'removed':row.removed = True
            elif fault == 'lease':row.parse_lease_until = now() + timedelta(minutes=1)
            else:row.parse_status = fault
    response = retry(client, p, asset, revision=revision)
    assert response.status_code == status and response.json()['detail']['code'] == code
    who[0] = owner
    assert getp(client, p)['revision'] == p['revision']


def test_reparse_requires_stable_intent_and_rechecks_removed_asset_on_replay(ctx):
    _, client, _ = ctx
    p, asset = failed_file(ctx)
    path = f"/api/v1/projects/{p['id']}/assets/{asset['id']}/reparse"
    assert client.post(path, json={'expected_revision': p['revision']}).status_code == 400
    assert retry(client, p, asset).status_code == 202
    current = getp(client, p)
    assert client.delete(f"/api/v1/projects/{p['id']}/assets/{asset['id']}",
                         params={'expected_revision': current['revision']}).status_code == 200
    assert retry(client, p, asset).status_code == 404
