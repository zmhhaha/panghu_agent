from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from agent_service.contracts import ServiceError
from video_workbench.app import create_app


SOURCE = dict(run_id='run', version_id='draft', sha256='a' * 64)
OWNER = 'local-developer'


class Service:
    def __init__(self):
        self.calls = []
        self.rows = []

    def list(self, owner, project):
        return [row for row in self.rows if row['project_id'] == project]

    def start(self, owner, project_id, **body):
        self.calls.append((owner, project_id, body))
        row = dict(id='operation', project_id=project_id, status='running', phase='review',
                   **body)
        self.rows.append(row)
        return row

    def get(self, owner, operation_id):
        for row in self.rows:
            if row['id'] == operation_id:
                return row
        raise ServiceError('production_refinement_not_found', 404)

    def cancel(self, owner, operation_id):
        self.calls.append(('cancel', owner, operation_id))
        return self.get(owner, operation_id)


@pytest.fixture
def setup(tmp_path):
    service = Service()
    app = create_app(db_path=tmp_path / 'workbench.db', local_mode=True, background=False,
                     refinement_service=service, refinement_enabled=True)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        token = client.get('/api/session').json()['csrf_token']
        headers = {'X-CSRF-Token': token}
        project = client.post('/api/projects', json=dict(title='作品', script='原稿'), headers=headers).json()
        sequence = app.state.production_sequence_store.create(OWNER, project['id'], SOURCE, 'sequence')
        app.state.production_run_store.list = lambda owner, project_id: [dict(sequence_registration=dict(
            status='registered', sequence_id=sequence['id'], source=SOURCE))]
        yield SimpleNamespace(app=app, client=client, headers=headers, project=project, sequence=sequence, service=service)


def test_read_only_catalogue_projects_real_allowance_and_never_starts(setup):
    h = setup
    ledger = h.app.state.production_sequence_store
    ledger.reserve_round(OWNER, h.project['id'], h.sequence['id'], 'automatic', SOURCE['version_id'],
        'b' * 64, 'automatic', expected_revision=h.sequence['revision'])
    response = h.client.get(f"/api/projects/{h.project['id']}/refinements")
    assert response.status_code == 200
    catalogue = response.json()
    assert catalogue['enabled'] is True
    summary = catalogue['sequences'][0]
    assert summary['consumed'] == dict(automatic=1, user_requested=0)
    assert summary['remaining'] == dict(automatic=0, user_requested=2)
    assert summary['active'] is True and summary['automatic_finished'] is False
    assert summary['sources'] == [SOURCE]
    assert h.service.calls == []


def test_only_explicit_user_kinds_cross_mutation_and_original_owner_boundary(setup):
    h = setup
    path = f"/api/projects/{h.project['id']}/refinements"
    body = dict(source=SOURCE, kind='user_requested', request_id='explicit', instruction='改善主次')
    assert h.client.post(path, json=body).status_code == 403
    assert h.client.post(path, json=dict(body, kind='automatic'), headers=h.headers).status_code == 422
    assert h.service.calls == []
    response = h.client.post(path, json=body, headers=h.headers)
    assert response.status_code == 202
    assert h.service.calls == [(OWNER, h.project['id'], body)]
    other = h.app.state.store.create_project('someone-else', dict(title='私有', script='原稿'))
    assert h.client.get(f"/api/projects/{other['id']}/refinements").status_code == 404
    assert h.client.post(f"/api/projects/{other['id']}/refinements", json=body, headers=h.headers).status_code == 404
    assert len(h.service.calls) == 1


def test_requirements_preserve_exact_scope_and_budget_and_cancel_is_explicit(setup):
    h = setup
    body = dict(source=SOURCE, kind='requirements', request_id='targeted',
        items=[dict(scope=dict(kind='time_range', start_seconds=1, end_seconds=3), problem='遮挡', expected='移开')],
        budget=dict(max_agent_tasks=3, max_renders=1))
    response = h.client.post(f"/api/projects/{h.project['id']}/refinements", json=body, headers=h.headers)
    assert response.status_code == 202
    assert h.service.calls[0][2] == body
    assert h.client.get('/api/refinements/operation').status_code == 200
    assert h.client.post('/api/refinements/operation/cancel', json={}).status_code == 403
    assert h.client.post('/api/refinements/operation/cancel', json={}, headers=h.headers).status_code == 200
    assert h.service.calls[-1] == ('cancel', OWNER, 'operation')


def test_disabled_workflow_keeps_catalogue_readable_and_admits_nothing(setup):
    h = setup
    h.app.state.refinement_workflow.enabled = False
    assert h.client.get(f"/api/projects/{h.project['id']}/refinements").json()['enabled'] is False
    response = h.client.post(f"/api/projects/{h.project['id']}/refinements",
        json=dict(source=SOURCE, kind='user_requested', request_id='explicit', instruction='改善'), headers=h.headers)
    assert response.status_code == 503 and h.service.calls == []


def test_active_review_prevents_project_trash_even_before_module_work(setup):
    h = setup
    h.service.rows.append(dict(id='review', project_id=h.project['id'], status='running'))
    response = h.client.post(f"/api/projects/{h.project['id']}/trash",
        json=dict(expected_revision=h.project['revision']), headers=h.headers)
    assert response.status_code == 409
    assert h.client.get(f"/api/projects/{h.project['id']}").status_code == 200
