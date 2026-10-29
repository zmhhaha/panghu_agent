"""Full API factory, lifecycle and actual neutral HTTP-client integration."""
import hashlib
import json
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.scientific_agent import create_app
from scientific_agent.agent_client import ScientificAgentClient
from scientific_agent.pi_client import WorkerError
from scientific_agent.pi_projection import sql
from scientific_agent.workspace import WorkspaceStore
from test_workspace import SQLite, Agent


class Database(SQLite):
    def init_db(self, service):
        assert service == 'scientific'
        self._execute('CREATE TABLE IF NOT EXISTS scientific_tasks '
            '(id TEXT PRIMARY KEY,topic TEXT,status TEXT,user_id TEXT,report TEXT,error TEXT)')
        self._execute('CREATE TABLE IF NOT EXISTS scientific_reports '
            '(id TEXT PRIMARY KEY,task_id TEXT,topic TEXT,summary TEXT,keywords TEXT,content TEXT,'
            'model_used TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP)')

    def get_report(self, report_id):
        rows = self._query('SELECT * FROM scientific_reports WHERE id=' + sql(report_id))
        return rows[0] if rows else None

    def get_report_by_task(self, task_id):
        rows = self._query('SELECT * FROM scientific_reports WHERE task_id=' + sql(task_id))
        return rows[0] if rows else None

    def get_task(self, task_id):
        rows = self._query('SELECT * FROM scientific_tasks WHERE id=' + sql(task_id))
        return rows[0] if rows else None


@pytest.fixture
def factory():
    database = Database()
    remote = Agent(WorkspaceStore(database))
    def transport(method, url, headers, body, timeout, limit):
        parsed = urlsplit(url)
        path = parsed.path + ('?' + parsed.query if parsed.query else '')
        try:
            if method == 'POST' and parsed.path == '/v1/inputs':
                name = parse_qs(parsed.query)['name'][0]
                ref = remote._upload(name, body)
                result = dict(ref, name=name, size=len(body))
            else:
                binary = parsed.path.startswith('/v1/artifacts/')
                payload = json.loads(body) if body else None
                result = remote._request(method, path, payload, raw=binary, limit=limit, headers=headers)
                if binary:
                    raw, result_headers = result
                    return 200, result_headers, raw
            return 200, {'content-type': 'application/json'}, json.dumps(result).encode('utf-8')
        except WorkerError as exc:
            return exc.status, {'content-type': 'application/json'}, json.dumps(
                {'detail': {'code': exc.code}}).encode('utf-8')
    worker = ScientificAgentClient(database, 'http://agent.fixture', 's' * 40,
        transport=transport, providers=('crossref',), max_results=2)
    app = create_app(database=database, worker=worker, start_reconciler=False)
    with TestClient(app, headers={'X-Forwarded-User': 'alice'}) as client:
        yield client, app.state.workspace, remote, database
    database.connection.close()


def mutate(client, path, payload, key=None, method='POST'):
    response = client.request(method, '/api/v1/research' + path, json=payload,
                              headers={'Idempotency-Key': key or str(uuid4())})
    assert response.status_code in (200, 201), response.text
    return response.json()


def finish(client, service, remote, project_id, stage):
    project = client.get('/api/v1/research/projects/' + project_id).json()
    job = next(job for job in project['jobs'] if job['id'] == project['active_job_id'])
    assert job['stage'] == stage
    remote.complete('alice', project_id, job['id'])
    response = client.get('/api/v1/research/projects/' + project_id)
    assert response.status_code == 200, response.text
    return response.json()


def test_real_factory_upload_two_gates_draft_revision_and_private_export(factory):
    client, service, remote, database = factory
    project = mutate(client, '/projects', {'requirements': {'question': 'Compare reproducible build approaches'}})
    project_id = project['id']
    response = client.post('/api/v1/research/projects/' + project_id + '/materials',
        data={'expected_revision': project['revision']}, files={'file': ('scope.markdown', b'# Supplied scope\nSynthetic fixture.')},
        headers={'Idempotency-Key': 'markdown-upload'})
    assert response.status_code == 200, response.text
    project = response.json()
    material_id = project['materials'][0]['material_id']
    assert project['materials'][0]['access_level'] == 'full_text'
    # Exclude this source to keep the synthetic draft fixture's sole citation
    # explicit. The text remains historical; no fake observation is created.
    project = client.delete('/api/v1/research/projects/' + project_id + '/materials/' + material_id,
        params={'expected_revision': project['revision']}, headers={'Idempotency-Key': 'exclude-markdown'}).json()
    project = mutate(client, '/projects/' + project_id + '/actions/plan', {'expected_revision': project['revision']})
    project = finish(client, service, remote, project_id, 'plan')
    assert project['plan']['source_policy'] == dict(providers=['crossref'], max_results=2, max_search_calls=3)
    assert project['plan']['execution_timeout_seconds'] == 1800
    project = mutate(client, '/projects/' + project_id + '/actions/confirm_plan', {'expected_revision': project['revision']})
    project = finish(client, service, remote, project_id, 'retrieve')
    project = finish(client, service, remote, project_id, 'outline')
    assert project['active_job_id'] is None and not project['confirmations']['evidence']
    project = mutate(client, '/projects/' + project_id + '/actions/confirm_evidence', {'expected_revision': project['revision']})
    project = finish(client, service, remote, project_id, 'draft')
    version_id = project['current_version_id']
    assert version_id and project['stage'] == 'review'
    version = client.get('/api/v1/research/projects/' + project_id + '/versions/' + version_id).json()
    assert version['references'] == [{'n': 1, 'paper_id': 'doi:10.1234/fixture'}]
    assert version['sources'][0]['availability']['abstract']['available'] is True
    assert version['sources'][0]['availability']['full_text']['available'] is False
    section_id = version['section_index']['sections'][0]['section_id']
    assert version['revision_sections']
    assert all(item['title'] != 'References' for item in version['revision_sections'])
    project = mutate(client, '/projects/' + project_id + '/actions/revise', dict(
        expected_revision=project['revision'], version_id=version_id, section_ids=[section_id], feedback='Clarify recorded finding'))
    project = finish(client, service, remote, project_id, 'revise')
    assert project['current_version_id'] == version_id
    candidate_id = project['versions'][-1]['id']
    comparison = client.get('/api/v1/research/projects/' + project_id + '/compare',
        params={'base_version_id': version_id, 'candidate_version_id': candidate_id})
    assert comparison.status_code == 200 and comparison.json()['changed']
    project = mutate(client, '/projects/' + project_id + '/actions/adopt', dict(
        expected_revision=project['revision'], version_id=candidate_id))
    project = mutate(client, '/projects/' + project_id + '/actions/finalize', dict(
        expected_revision=project['revision'], version_id=candidate_id))
    assert project['stage'] == 'final' and not project['finalizations'][0]['publication_authorized']
    export_path = '/api/v1/research/projects/' + project_id + '/versions/' + candidate_id + '/download'
    export = client.get(export_path, params={'format': 'sources'})
    assert export.status_code == 200 and export.json()['status'] == 'final'
    assert export.json()['references'] and export.json()['evidence'] and export.json()['claims']
    assert client.get(export_path, headers={'X-Forwarded-User': 'bob'}).status_code == 404
    assert remote.posts == 5 and remote.acks == 5
    # Staged outputs are private project versions; they never enter the legacy
    # shared report list or create invented legacy planning confirmations.
    assert client.get('/scientific-reports').json() == []


def test_factory_identity_revision_conflict_and_get_owner_isolation(factory):
    client, _, _, _ = factory
    project = mutate(client, '/projects', {'requirements': {'question': 'Owner-scoped question'}})
    path = '/api/v1/research/projects/' + project['id']
    assert client.get(path, headers={'X-Forwarded-User': 'bob'}).status_code == 404
    assert client.get(path, headers={'X-Forwarded-User': ''}).status_code == 401
    assert client.get('/api/v1/research/projects', headers={'X-Forwarded-User': 'bob'}).json() == {'projects': []}
    response = client.patch(path, json={'expected_revision': 0, 'title': 'Updated title'},
                            headers={'Idempotency-Key': 'title-one'})
    assert response.status_code == 200
    conflict = client.patch(path, json={'expected_revision': 0, 'title': 'Stale second title'},
                            headers={'Idempotency-Key': 'title-two'})
    assert conflict.status_code == 409 and conflict.json()['detail']['code'] == 'project_revision_conflict'
    replay = client.patch(path, json={'expected_revision': 0, 'title': 'Updated title'},
                          headers={'Idempotency-Key': 'title-one'})
    assert replay.status_code == 200 and replay.json()['revision'] == 1


def test_factory_legacy_owned_aliases_remain_private_before_pagination(factory):
    client, _, remote, database = factory
    owner_task, owner_report, alias_task, shared = [str(uuid4()) for _ in range(4)]
    for task_id in (owner_task, owner_report):
        database._execute('INSERT INTO scientific_pi_index '
            '(id,user_id,input_digest,job,email,dispatch,created_at,updated_at) VALUES ('
            + ','.join(map(sql, (task_id, 'bob', 'a' * 64, '{}', '', 'prepared', 'now', 'now'))) + ')')
    records = [(owner_task, owner_task, 'Owned task report'),
               (alias_task, owner_task, 'Alias by task'),
               (owner_report, alias_task, 'Alias by report id'),
               (shared, shared, 'Legacy shared report')]
    for report_id, task_id, title in records:
        database._execute('INSERT INTO scientific_reports (id,task_id,topic,content) VALUES ('
            + ','.join(map(sql, (report_id, task_id, title, '# ' + title))) + ')')
    for report_id in (owner_task, alias_task, owner_report):
        assert client.get('/scientific-reports/' + report_id).status_code == 404
        assert client.get('/scientific-download/' + report_id).status_code == 404
    rows = client.get('/scientific-reports', params={'limit': 1, 'offset': 0})
    assert rows.status_code == 200 and [row['id'] for row in rows.json()] == [shared]
    assert client.get('/scientific-reports/' + shared).status_code == 200
    assert client.get('/scientific-download/' + shared).text == '# Legacy shared report'
    bob_rows = client.get('/scientific-reports', headers={'X-Forwarded-User': 'bob'}).json()
    assert len(bob_rows) == 4
    assert remote.posts == 0
