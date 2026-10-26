"""Real HTTP removal and quota contracts with isolated synthetic assets."""
import hashlib

import pytest
from sqlalchemy import func, select

from ppt_service.server import app as app_module
from ppt_service.server.auth import Identity
from ppt_service.server.models import Asset, Job, Project
from ppt_service.server.repository import Repository

from test_api import ctx, getp, plan, project


def upload(client, pid, name='source.md', data=b'# Synthetic source\n'):
    return client.post(f'/api/v1/projects/{pid}/assets',
                       files={'file': (name, data, 'text/markdown')})


def remove(client, pid, aid, revision):
    return client.delete(f'/api/v1/projects/{pid}/assets/{aid}',
                         params={'expected_revision': revision})


@pytest.mark.parametrize('parse_status', ['failed', 'ready'])
def test_owner_removes_failed_or_ready_asset_and_retains_snapshot_bytes(ctx, parse_status):
    app, client, _ = ctx
    p = project(client)
    data = b'# Synthetic retained source\n'
    asset = upload(client, p['id'], data=data).json()
    with app.state.sessions.begin() as session:
        row = session.get(Asset, asset['id'])
        row.parse_status = parse_status
        row.parse_error = 'pdf_page_limit' if parse_status == 'failed' else None
        row.text_summary = 'Synthetic summary' if parse_status == 'ready' else None
        original = app.state.storage.root / row.storage_key
        current = session.get(Project, p['id'])
        current.confirmed_plan_revision = 1
        current.stage = 'awaiting_confirmation'
        snapshot = Repository(session).snapshot(current)
        historical = Job(project_id=p['id'], issuer=current.issuer, subject=current.subject,
                         kind='plan_initial', stage='plan_initial', status='succeeded',
                         input_snapshot=snapshot)
        session.add(historical)
        session.flush()
        historical_id = historical.id
    before = getp(client, p)
    response = remove(client, p['id'], asset['id'], before['revision'])
    assert response.status_code == 200
    assert response.json() == {'revision': before['revision'] + 1}
    after = getp(client, p)
    assert after['assets'] == [] and after['confirmed_plan_revision'] is None
    assert after['stage'] == 'draft' and after['input_snapshot_hash'] != before['input_snapshot_hash']
    assert original.read_bytes() == data
    assert hashlib.sha256(original.read_bytes()).hexdigest() == asset['sha256']
    with app.state.sessions() as session:
        assert session.get(Asset, asset['id']).removed is True
        assert session.get(Job, historical_id).input_snapshot == snapshot


def test_removal_releases_current_project_byte_allowance(ctx, monkeypatch):
    app, client, _ = ctx
    p = project(client)
    data = b'# Synthetic bounded project source\n'
    monkeypatch.setattr(app_module, 'MAX_PROJECT', len(data))
    asset = upload(client, p['id'], data=data).json()
    rejected = upload(client, p['id'], name='over-limit.md', data=data)
    assert rejected.status_code == 413
    assert rejected.json()['detail']['code'] == 'storage_full'
    assert remove(client, p['id'], asset['id'], getp(client, p)['revision']).status_code == 200
    replacement = upload(client, p['id'], name='replacement.md', data=data)
    assert replacement.status_code == 201
    current = getp(client, p)
    assert [row['id'] for row in current['assets']] == [replacement.json()['id']]
    assert sum(row['bytes'] for row in current['assets']) == len(data)
    with app.state.sessions() as session:
        assert session.scalar(select(func.sum(Asset.bytes)).where(Asset.project_id == p['id'])) == 2 * len(data)
        original = app.state.storage.root / session.get(Asset, asset['id']).storage_key
    assert original.read_bytes() == data


def test_removal_releases_current_project_file_count(ctx):
    app, client, _ = ctx
    p = project(client)
    assets = []
    for number in range(10):
        response = upload(client, p['id'], name=f'source-{number}.md')
        assert response.status_code == 201
        assets.append(response.json())
    rejected = upload(client, p['id'], name='eleventh.md')
    assert rejected.status_code == 413
    assert rejected.json()['detail']['code'] == 'project_file_limit'
    assert remove(client, p['id'], assets[0]['id'], getp(client, p)['revision']).status_code == 200
    assert upload(client, p['id'], name='replacement.md').status_code == 201
    assert len(getp(client, p)['assets']) == 10


def test_removed_original_still_counts_toward_global_storage(ctx, monkeypatch):
    app, client, _ = ctx
    p = project(client)
    data = b'# Synthetic retained global bytes\n'
    monkeypatch.setattr(app_module, 'MAX_PROJECT', 2 * len(data))
    app.state.settings.max_storage_bytes = len(data)
    asset = upload(client, p['id'], data=data).json()
    assert remove(client, p['id'], asset['id'], getp(client, p)['revision']).status_code == 200
    rejected = upload(client, p['id'], name='replacement.md', data=data)
    assert rejected.status_code == 413
    assert rejected.json()['detail']['code'] == 'storage_full'
    assert getp(client, p)['assets'] == []
    with app.state.sessions() as session:
        rows = session.scalars(select(Asset).where(Asset.project_id == p['id'])).all()
        assert len(rows) == 1 and rows[0].removed is True
        original = app.state.storage.root / rows[0].storage_key
    assert original.read_bytes() == data


@pytest.mark.parametrize('fault,status,code', [
    ('stale', 409, 'stale_project_revision'),
    ('other_owner', 404, 'not_found'),
    ('other_issuer', 404, 'not_found'),
    ('active_job', 409, 'project_busy'),
    ('missing_query', 422, None),
])
def test_asset_removal_preserves_revision_acl_busy_and_query_contract(ctx, fault, status, code):
    app, client, who = ctx
    p = project(client)
    asset = upload(client, p['id']).json()
    owner = who[0]
    current = getp(client, p)
    revision = current['revision']
    if fault == 'stale':
        revision -= 1
    elif fault == 'other_owner':
        who[0] = Identity(owner.issuer, 'another-owner')
    elif fault == 'other_issuer':
        who[0] = Identity('https://another-issuer', owner.sub)
    elif fault == 'active_job':
        assert plan(client, current, key='asset-removal-busy').status_code == 202
        current = getp(client, p)
        revision = current['revision']
    if fault == 'missing_query':
        response = client.request('DELETE', f"/api/v1/projects/{p['id']}/assets/{asset['id']}",
                                  json={'expected_revision': revision})
    else:
        response = remove(client, p['id'], asset['id'], revision)
    assert response.status_code == status
    if code:
        assert response.json()['detail']['code'] == code
    who[0] = owner
    after = getp(client, p)
    assert after['revision'] == current['revision']
    assert [row['id'] for row in after['assets']] == [asset['id']]
    with app.state.sessions() as session:
        row = session.get(Asset, asset['id'])
        assert row.removed is False
