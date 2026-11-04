"""Ordinary confirmation remains the only owner gate for source-bound charts."""
from sqlalchemy import select

from ppt_service.control.source_tables import bind_direction
from ppt_service.server.auth import Identity
from ppt_service.server.models import Job, Plan, Project
from ppt_service.server.repository import Repository
from test_api import accepted, confirm, ctx, getp, plan, project


def stage2(ctx):
    app, client, _ = ctx
    current = project(client)
    jid = plan(client, current).json()['id']
    revision, snapshot_hash = accepted(app, jid)
    current = getp(client, current)
    assert confirm(client, current, revision, snapshot_hash).status_code == 200
    current = getp(client, current)
    jid = plan(client, current, 'options', 'plan_options').json()['id']
    revision, snapshot_hash = accepted(app, jid, 'plan_options')
    return getp(client, current), revision, snapshot_hash


def replace_direction(app, pid, revision, direction):
    with app.state.sessions.begin() as session:
        saved = session.scalar(select(Plan).where(Plan.project_id == pid, Plan.revision == revision))
        saved.design = {'directions': [direction, {'id': 'B'}, {'id': 'C'}]}


def test_old_unbound_chart_plan_requires_replanning_before_normal_confirmation(ctx):
    app, client, _ = ctx
    current, revision, snapshot_hash = stage2(ctx)
    replace_direction(app, current['id'], revision, {'id': 'A', 'native_chart_specs': [
        {'table_id': 'old-unbound', 'category_column': 0, 'series_columns': [1], 'slide': 2, 'type': 'column'}]})
    response = confirm(client, current, revision, snapshot_hash, 'plan_options', 'unbound')
    assert response.status_code == 409 and response.json()['detail']['code'] == 'source_chart_binding_required'
    with app.state.sessions() as session:
        assert session.scalar(select(Plan).where(Plan.project_id == current['id'], Plan.revision == revision)).confirmed_at is None
        assert len(session.scalars(select(Job).where(Job.project_id == current['id'])).all()) == 2


def test_empty_service_binding_uses_existing_confirmation_once_and_preserves_idempotency(ctx):
    app, client, _ = ctx
    current, revision, snapshot_hash = stage2(ctx)
    with app.state.sessions() as session:
        snapshot = Repository(session).snapshot(session.get(Project, current['id']))
    selected = bind_direction({'id': 'A', 'page_plan': {'pages': [{} for _ in range(8)]}}, snapshot,
        {'input_snapshot_sha256': snapshot_hash, 'tables': []})
    replace_direction(app, current['id'], revision, selected)
    response = confirm(client, current, revision, snapshot_hash, 'plan_options', 'bound')
    assert response.status_code == 200
    assert confirm(client, current, revision, snapshot_hash, 'plan_options', 'bound').json() == response.json()
    current = getp(client, current)
    request = {'expected_revision': current['revision'], 'plan_revision': revision}
    response = client.post(f"/api/v1/projects/{current['id']}/generations", json=request,
                           headers={'Idempotency-Key': 'ordinary-generation'})
    assert response.status_code == 202 and response.json()['stage'] == 'prepare'
    assert client.post(f"/api/v1/projects/{current['id']}/generations", json=request,
                      headers={'Idempotency-Key': 'ordinary-generation'}).json() == response.json()


def test_generation_rechecks_frozen_binding_before_preparation_is_enqueued(ctx):
    app, client, _ = ctx
    current, revision, snapshot_hash = stage2(ctx)
    assert confirm(client, current, revision, snapshot_hash, 'plan_options', 'old-chart-free').status_code == 200
    current = getp(client, current)
    selected = {'id': 'A', 'native_chart_specs': [{'table_id': 'unbound'}]}
    replace_direction(app, current['id'], revision, selected)
    response = client.post(f"/api/v1/projects/{current['id']}/generations",
        json={'expected_revision': current['revision'], 'plan_revision': revision},
        headers={'Idempotency-Key': 'do-not-start'})
    assert response.status_code == 409 and response.json()['detail']['code'] == 'source_chart_binding_required'
    with app.state.sessions() as session:
        assert len(session.scalars(select(Job).where(Job.project_id == current['id'])).all()) == 2
        assert session.get(Project, current['id']).active_job_id is None


def test_source_mapping_and_binding_cannot_be_changed_by_confirmation_payload(ctx):
    _, client, _ = ctx
    current, revision, snapshot_hash = stage2(ctx)
    response = client.post(f"/api/v1/projects/{current['id']}/plans/{revision}/confirm", json={
        'expected_revision': current['revision'], 'input_snapshot_hash': snapshot_hash,
        'selection': {'direction_id': 'A', 'source_chart_binding': {'expected_charts': []}}},
        headers={'Idempotency-Key': 'forged-binding'})
    assert response.status_code == 422 and response.json()['detail']['code'] == 'invalid_confirmation_selection'


def test_foreign_owner_cannot_read_or_confirm_source_bound_plan(ctx):
    _, client, who = ctx
    current, revision, snapshot_hash = stage2(ctx)
    who[0] = Identity('https://issuer', 'bob')
    assert client.get(f"/api/v1/projects/{current['id']}").status_code == 404
    assert confirm(client, current, revision, snapshot_hash, 'plan_options', 'other-owner').status_code == 404
