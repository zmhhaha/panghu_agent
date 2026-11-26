"""HTTP regressions for the deployed proxy/credential authentication contract.

Browser identity comes from the authenticated, header-scrubbing upstream proxy;
the application does not pass a proxy CIDR list to the shared SDK. These tests
exercise that existing boundary, real SQLite writes and a loopback lab server.
All credentials are generated per test; no model or external service is used.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import secrets
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from service_auth import CSRF_COOKIE
from video_workbench.animation_creation_http import CreationHTTPServer, CreationTransportError
from video_workbench.app import create_app


OWNER = 'test-issuer/fixed-learning-owner'
BROWSER = 'test-issuer/alice'
OTHER_BROWSER = 'test-issuer/bob'
PROJECT = {'title': 'HTTP authentication regression', 'script': 'A local test only.',
           'production_mode': 'static'}


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()['detail']['code'] == code


class LearningLibrary:
    """Owner-scoped local data, behind the real private HTTP transport."""

    def __init__(self, kind):
        self.kind = kind
        self.calls = []
        self.projects = {
            (OWNER, 'shared-project'): {'id': 'shared-project', 'title': 'Owner project'},
            ('service:dsh', 'actor-project'): {'id': 'actor-project', 'title': 'Actor project'},
            (OTHER_BROWSER, 'foreign-project'): {'id': 'foreign-project', 'title': 'Other project'},
        }

    def catalogue(self, owner):
        self.calls.append(('catalogue', owner))
        return {'projects': [value for (actor, _), value in self.projects.items() if actor == owner],
                'references': [{'media_url': f'/api/{self.kind}-lab/references/ref-1/media'}]}

    def start(self, owner, body):
        self.calls.append(('start', owner, body))
        value = {'id': 'started-project', 'title': 'Started locally'}
        self.projects[owner, value['id']] = value
        return value

    def get(self, owner, project_id):
        self.calls.append(('get', owner, project_id))
        if (owner, project_id) not in self.projects:
            raise CreationTransportError('learning_project_not_found', 404)
        return self.projects[owner, project_id]

    def experiment(self, owner, project_id, body):
        self.calls.append(('experiment', owner, project_id, body))
        return {'id': 'local-experiment', 'status': 'queued'}


@pytest.fixture
def release_http(tmp_path, monkeypatch):
    # Never inherit live endpoints or credentials from the developer shell.
    for name in list(os.environ):
        if name.startswith(('VIDEO_', 'SERVICE_AUTH_')):
            monkeypatch.delenv(name)
    tokens = {name: secrets.token_urlsafe(40) for name in
              ('dsh', 'hermes', 'pi', 'other', 'read-only', 'expired', 'wrong-service', 'disabled')}
    expires = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    config = {
        'version': 1,
        'principals': {name: {'subject': 'service:' + name} for name in ('dsh', 'hermes', 'pi', 'other')},
        'credentials': [],
    }
    config['principals']['disabled'] = {'subject': 'service:disabled', 'enabled': False}
    for name, token in tokens.items():
        config['credentials'].append({
            'id': 'test-' + name,
            'principal': 'dsh' if name in {'read-only', 'expired', 'wrong-service'} else name,
            'token_hash': hashlib.sha256(token.encode()).hexdigest(),
            'expires_at': ((datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
                           if name == 'expired' else expires),
            'services': ['other-service' if name == 'wrong-service' else 'video-service'],
            'operations': ['read'] if name == 'read-only' else ['read', 'write'],
        })
    credentials = tmp_path / 'credentials.json'
    credentials.write_text(json.dumps(config), encoding='utf-8')
    monkeypatch.setenv('SERVICE_AUTH_ISSUER', 'https://issuer.test')
    monkeypatch.setenv('SERVICE_AUTH_TOKENS_FILE', str(credentials))
    monkeypatch.setenv('VIDEO_LEARNING_OWNER', OWNER)
    pictures, videos = LearningLibrary('picture'), LearningLibrary('video')
    lab_token = secrets.token_urlsafe(40)
    server = CreationHTTPServer(object(), lab_token, pump=False, picture_service=pictures,
                                video_learning_service=videos)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    monkeypatch.setenv('VIDEO_METHOD_LAB_URL', f'http://127.0.0.1:{server.server_port}')
    monkeypatch.setenv('VIDEO_METHOD_LAB_TOKEN', lab_token)
    try:
        app = create_app(db_path=tmp_path / 'workbench.db', gateway=object(),
                         local_mode=False, background=False, trusted_hosts={'testserver'},
                         method_adapter=object(), skill_adapter=object(),
                         motion_enabled=False, process_enabled=False, creative_enabled=False,
                         explanation_enabled=False, joint_plan_enabled=False,
                         production_runs_enabled=False, refinement_enabled=False, render_enabled=False)
        with TestClient(app, base_url='https://testserver') as client:
            yield SimpleNamespace(client=client, app=app, tokens=tokens, config=config,
                                  credentials=credentials, pictures=pictures, videos=videos)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def bearer(env, name='dsh'):
    return {'Authorization': 'Bearer ' + env.tokens[name]}


def test_browser_identity_csrf_origin_and_sqlite_owner_isolation(release_http):
    env = release_http
    client = env.client
    client.headers['X-Forwarded-User'] = BROWSER
    session = client.get('/api/session')
    assert session.status_code == 200
    assert session.json()['user_id'] == BROWSER
    csrf = session.json()['csrf_token']
    cookie = session.headers['set-cookie'].lower()
    assert CSRF_COOKIE.lower() in cookie
    assert all(flag in cookie for flag in ('secure', 'httponly', 'samesite=strict', 'path=/'))
    assert_error(client.post('/api/projects', json=PROJECT,
                            headers={'Origin': 'https://testserver'}), 403, 'csrf_rejected')
    assert env.app.state.store.list_projects(BROWSER) == []
    write_headers = {'Origin': 'https://testserver', 'X-CSRF-Token': csrf}
    assert_error(client.post('/api/projects', json=PROJECT,
                            headers={**write_headers, 'Origin': 'https://foreign.test'}),
                 403, 'origin_rejected')
    created = client.post('/api/projects', json=PROJECT, headers=write_headers)
    assert created.status_code == 200, created.text
    project_id = created.json()['id']
    assert env.app.state.store.project(BROWSER, project_id)['title'] == PROJECT['title']
    client.headers['X-Forwarded-User'] = OTHER_BROWSER
    assert_error(client.post('/api/projects', json=PROJECT, headers=write_headers), 403, 'csrf_rejected')
    assert client.get('/api/projects').json() == {'projects': []}
    assert client.get('/api/projects/' + project_id).status_code == 404
    assert env.app.state.store.list_projects(OTHER_BROWSER) == []


@pytest.mark.parametrize('headers', [
    {},
    {'X-Forwarded-User': 'service:dsh'},
    {'X-Forwarded-User': 'service:hermes'},
    {'X-Forwarded-User': 'service:pi'},
    {'X-Auth-Request-User': BROWSER},
    {'X-Forwarded-Email': BROWSER},
])
def test_missing_or_forged_identity_cannot_authenticate(release_http, headers):
    assert_error(release_http.client.get('/api/session', headers=headers), 401, 'authentication_required')


def test_invalid_or_duplicate_bearer_never_falls_back_to_browser(release_http):
    env = release_http
    for authorization in ('Bearer ' + secrets.token_urlsafe(40), 'Basic invalid', 'Bearer short'):
        assert_error(env.client.get('/api/session', headers={
            'Authorization': authorization, 'X-Forwarded-User': BROWSER,
        }), 401, 'service_auth_token_invalid')
    response = env.client.get('/api/session', headers=[
        ('Authorization', 'Bearer ' + env.tokens['dsh']),
        ('Authorization', 'Bearer ' + env.tokens['pi']),
        ('X-Forwarded-User', BROWSER),
    ])
    assert_error(response, 401, 'service_auth_token_invalid')


@pytest.mark.parametrize('credential,status,code', [
    ('expired', 401, 'service_auth_token_expired'),
    ('wrong-service', 403, 'service_auth_service_forbidden'),
    ('disabled', 403, 'service_auth_principal_disabled'),
])
def test_credential_restrictions_do_not_fall_back(release_http, credential, status, code):
    headers = {**bearer(release_http, credential), 'X-Forwarded-User': BROWSER}
    assert_error(release_http.client.get('/api/projects', headers=headers), status, code)


def test_read_only_credential_reads_but_cannot_write_either_surface(release_http):
    env = release_http
    headers = bearer(env, 'read-only')
    assert env.client.get('/api/projects', headers=headers).status_code == 200
    assert env.client.get('/api/learning/videos/catalog', headers=headers).status_code == 200
    before = list(env.videos.calls)
    for path in ('/api/projects', '/api/learning/videos/projects'):
        assert_error(env.client.post(path, json=PROJECT, headers=headers),
                     403, 'service_auth_operation_forbidden')
    assert env.app.state.store.list_projects('service:dsh') == []
    assert env.videos.calls == before


def test_service_session_and_writes_use_credential_subject_without_browser_csrf(release_http):
    env = release_http
    headers = {**bearer(env), 'X-Forwarded-User': OTHER_BROWSER}
    session = env.client.get('/api/session', headers=headers)
    assert session.json()['user_id'] == 'service:dsh'
    assert session.json()['csrf_token'] == ''
    assert 'set-cookie' not in session.headers
    response = env.client.post('/api/projects', json=PROJECT, headers=headers)
    assert response.status_code == 200, response.text
    project_id = response.json()['id']
    assert env.app.state.store.project('service:dsh', project_id)['title'] == PROJECT['title']
    assert env.app.state.store.list_projects(OTHER_BROWSER) == []
    assert env.client.get('/api/projects/' + project_id, headers=bearer(env, 'pi')).status_code == 404


@pytest.mark.parametrize('subject', ['dsh', 'hermes', 'pi'])
@pytest.mark.parametrize('library', ['pictures', 'videos'])
def test_allowed_machine_subjects_use_fixed_owner_over_real_private_http(release_http, subject, library):
    env = release_http
    backend = getattr(env, library)
    headers = {**bearer(env, subject), 'X-Forwarded-User': OTHER_BROWSER,
               'X-Animation-Creation-Owner': OTHER_BROWSER, 'X-Learning-Owner': OTHER_BROWSER}
    prefix = '/api/learning/' + library
    catalog = env.client.get(prefix + '/catalog?owner=' + OTHER_BROWSER, headers=headers)
    assert catalog.status_code == 200, catalog.text
    assert [row['id'] for row in catalog.json()['projects']] == ['shared-project']
    assert catalog.json()['references'][0]['media_url'] == (
        f'/api/v1/learning/{library}/references/ref-1/media')
    assert backend.calls[-1] == ('catalogue', OWNER)
    body = {'reference_id': 'ref-1', 'owner': OTHER_BROWSER, 'user_id': 'service:' + subject}
    created = env.client.post(prefix + '/projects', json=body, headers=headers)
    assert created.status_code == 202, created.text
    assert backend.calls[-1] == ('start', OWNER, body)
    assert (OWNER, 'started-project') in backend.projects
    assert (OTHER_BROWSER, 'started-project') not in backend.projects
    assert env.client.get(prefix + '/projects/shared-project', headers=headers).status_code == 200
    for project_id in ('actor-project', 'foreign-project'):
        assert_error(env.client.get(prefix + '/projects/' + project_id, headers=headers),
                     404, 'learning_project_not_found')
        assert backend.calls[-1] == ('get', OWNER, project_id)


@pytest.mark.parametrize('actor', ['browser', 'other', 'forged-service'])
def test_learning_routes_reject_unadmitted_actors_before_private_calls(release_http, actor):
    env = release_http
    headers = ({'X-Forwarded-User': BROWSER} if actor == 'browser' else
               {'X-Forwarded-User': 'service:dsh'} if actor == 'forged-service' else bearer(env, 'other'))
    status, code = ((401, 'authentication_required') if actor == 'forged-service'
                    else (403, 'learning_access_forbidden'))
    for method, path, body in (
        ('GET', '/api/learning/pictures/catalog', None),
        ('POST', '/api/learning/videos/projects', {'reference_id': 'ref-1'}),
    ):
        assert_error(env.client.request(method, path, headers=headers, json=body), status, code)
    assert env.pictures.calls == env.videos.calls == []


@pytest.mark.parametrize('library', ['pictures', 'videos'])
@pytest.mark.parametrize('body', [{'mode': 'transfer'}, {'approve_candidate': True}, {'approve_candidate': False}])
def test_machine_experiments_preserve_user_approval_boundary(release_http, library, body):
    env = release_http
    response = env.client.post(f'/api/learning/{library}/projects/shared-project/experiments',
                               json=body, headers=bearer(env))
    assert_error(response, 403, 'learning_approval_required')
    assert getattr(env, library).calls == []


def test_projected_credential_revocation_takes_effect_without_restart(release_http):
    env = release_http
    headers = bearer(env)
    assert env.client.get('/api/projects', headers=headers).status_code == 200
    env.config['credentials'] = [row for row in env.config['credentials'] if row['id'] != 'test-dsh']
    env.credentials.write_text(json.dumps(env.config), encoding='utf-8')
    assert_error(env.client.get('/api/projects', headers={**headers, 'X-Forwarded-User': BROWSER}),
                 401, 'service_auth_token_invalid')
