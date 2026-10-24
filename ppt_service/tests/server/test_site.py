"""Site boundary checks using the existing signed-RS256 fixture, no auth override."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, select, func
from ppt_service.server.models import Job
from ppt_service.server.site import create_site, HEADERS
from test_auth import auth_client


def symlink(target,link,*,directory=False):
    try:
        link.symlink_to(target,target_is_directory=directory)
    except OSError as exc:
        if os.name=='nt' and getattr(exc,'winerror',None)==1314:
            pytest.skip('Windows account cannot create symlinks; requires Linux validation')
        raise


@pytest.fixture
def site_client(auth_client, tmp_path, monkeypatch):
    monkeypatch.delenv('PPT_LOCAL_TEST_ONLY', raising=False)
    monkeypatch.delenv('PPT_EXECUTION_ENABLED', raising=False)
    monkeypatch.delenv('PPT_EXECUTION_BACKEND', raising=False)
    api, _, token = auth_client
    dist = tmp_path / 'dist'
    (dist / 'assets').mkdir(parents=True)
    (dist / 'index.html').write_text('<!doctype html><html>built app</html>')
    (dist / 'assets/app.js').write_text('console.log("built")')
    (dist / 'assets/app.js.map').write_text('private source map')
    (dist / 'assets/source.ts').write_text('private source')
    (dist / 'assets/.hidden.js').write_text('hidden')
    site = create_site(api.state.settings, api.state.engine, dist)
    site.state.jwks = api.state.jwks
    client = TestClient(site, base_url='https://ppt.test', client=('127.0.0.1', 12345))
    return site, client, token, dist


def login(client, token):
    headers = {'Authorization': 'Bearer ' + token(), 'X-Auth-Request-User': 'mallory'}
    me = client.get('/api/v1/me', headers=headers)
    assert me.status_code == 200
    assert me.json()['sub'] == 'stable-alice'
    assert me.json()['issuer'] == 'https://casdoor.test'
    assert 'Secure' in me.headers['set-cookie']
    return headers | {'Origin': 'https://ppt.test', 'X-CSRF-Token': me.json()['csrf_token']}


def test_enabled_kubernetes_admission_keeps_auth_csrf_and_idempotency(site_client, monkeypatch):
    old, _, token, dist = site_client
    monkeypatch.setenv('PPT_EXECUTION_ENABLED', 'true')
    monkeypatch.setenv('PPT_EXECUTION_BACKEND', 'kubernetes')
    site = create_site(old.state.settings, old.state.engine, dist)
    site.state.jwks = old.state.jwks
    c = TestClient(site, base_url='https://ppt.test', client=('127.0.0.1', 12345))
    headers = login(c, token)
    project = c.post('/api/v1/projects', headers=headers,
                     json={'title': 'Hermes canary', 'external_model_ack': True}).json()
    path = '/api/v1/projects/' + project['id'] + '/plans'
    body = {'expected_revision': project['revision']}
    assert c.post(path, json=body).status_code == 401
    assert c.post(path, headers={'Authorization': headers['Authorization']}, json=body).status_code == 403
    assert c.post(path, headers=headers | {'X-CSRF-Token': 'wrong'}, json=body).status_code == 403
    headers['Idempotency-Key'] = 'hermes-admission'
    first = c.post(path, headers=headers, json=body)
    assert first.status_code == 202
    assert first.json()['status'] == 'queued'
    assert c.post(path, headers=headers, json=body).json()['id'] == first.json()['id']
    with site.state.sessions() as s:
        assert s.scalar(select(func.count(Job.id))) == 1


def test_real_identity_project_settings_and_static(site_client):
    site, c, token, dist = site_client
    headers = login(c, token)
    result = c.post('/api/v1/projects', headers=headers, json={'title': 'Private', 'page_count': 6})
    assert result.status_code == 201
    project = result.json()
    result = c.patch('/api/v1/projects/' + project['id'], headers=headers,
                     json={'title': 'Updated', 'page_count': 9, 'expected_revision': project['revision']})
    assert result.status_code == 200
    assert result.json()['page_count'] == 9
    assert c.get('/api/v1/projects', headers=headers).json()[0]['title'] == 'Updated'
    for path in ('/', '/assets/app.js'):
        assert c.get(path).status_code == 401
        response = c.get(path, headers=headers)
        assert response.status_code == 200
        for key, value in HEADERS.items():
            assert response.headers[key] == value
        head = c.head(path, headers=headers)
        assert head.status_code == 200 and head.content == b''
        assert head.headers['content-length'] == response.headers['content-length']
        assert c.post(path, headers=headers).status_code == 405
    # Runtime changes cannot replace the prevalidated bytes with a symlink/source file.
    (dist / 'assets/app.js').write_text('changed after startup')
    assert c.get('/assets/app.js', headers=headers).text == 'console.log("built")'


@pytest.mark.parametrize('path', ['/api', '/api/no-such-route', '/api/v1/missing', '/unknown',
    '/oauth2/start', '/oauth2/sign_out', '/oauth2/callback', '/assets/missing.js',
    '/assets/app.js.map', '/assets/source.ts', '/assets/.hidden.js', '/server/auth.py',
    '/assets/%2e%2e/index.html', '/assets/%2e%2e/%2e%2e/server/auth.py',
    '/assets/%252e%252e/index.html', '/assets/%5c..%5cindex.html'])
def test_unknown_paths_never_return_index(site_client, path):
    _, c, token, _ = site_client
    result = c.get(path, headers={'Authorization': 'Bearer ' + token()})
    assert result.status_code == 404
    assert result.headers['content-type'].startswith('application/json')
    assert 'built app' not in result.text


@pytest.mark.parametrize('suffix', ['plans', 'plans/1/confirm', 'generations', 'revisions'])
def test_execution_auth_then_csrf_then_503_without_jobs(site_client, suffix):
    site, c, token, _ = site_client
    path = '/api/v1/projects/any/' + suffix
    assert c.post(path, json={}).status_code == 401
    bearer = {'Authorization': 'Bearer ' + token()}
    assert c.post(path, headers=bearer, json={}).status_code == 403
    assert c.post(path, headers=bearer | {'Origin': 'https://ppt.test'}, json={}).status_code == 403
    headers = login(c, token)
    assert c.post(path, headers=headers | {'Origin': 'https://evil.test'}, json={}).status_code == 403
    assert c.post(path, headers=headers | {'X-CSRF-Token': 'wrong'}, json={}).status_code == 403
    response = c.post(path, headers=headers, json={})
    assert response.status_code == 503
    assert response.json()['detail']['code'] == 'execution_not_connected'
    with site.state.sessions() as session:
        assert session.scalar(select(func.count(Job.id))) == 0
    assert c.post('/api/v1/not-projects/any/' + suffix, headers=headers, json={}).status_code == 404
    assert c.post(path + '/extra', headers=headers, json={}).status_code == 404


@pytest.mark.parametrize('claims', [{'iss': 'wrong'}, {'aud': 'wrong'}, {'sub': ''},
    {'email': 'unlisted@example.test'}, {'sub': 123}, {'exp': datetime.now(timezone.utc) - timedelta(minutes=1)}])
def test_invalid_claims_on_static_api_and_execution(site_client, claims):
    _, c, token, _ = site_client
    headers = {'Authorization': 'Bearer ' + token(**claims)}
    for path in ('/', '/assets/app.js', '/api/v1/me'):
        assert c.get(path, headers=headers).status_code == 401
    assert c.post('/api/v1/projects/p/plans', headers=headers).status_code == 401


def test_wrong_algorithm_and_untrusted_physical_peer(site_client):
    site, c, token, _ = site_client
    invalid = jwt.encode({'sub': 'stable-alice'}, 'fake-signing-key', algorithm='HS256')
    for path in ('/', '/assets/app.js', '/api/v1/me'):
        assert c.get(path, headers={'Authorization': 'Bearer ' + invalid}).status_code == 401
    other = TestClient(site, base_url='https://ppt.test', client=('192.0.2.3', 12345))
    headers = {'Authorization': 'Bearer ' + token(), 'X-Forwarded-For': '127.0.0.1',
               'Forwarded': 'for=127.0.0.1', 'X-Real-IP': '127.0.0.1'}
    for path in ('/', '/assets/app.js', '/api/v1/me'):
        assert other.get(path, headers=headers).status_code == 401
    assert other.post('/api/v1/projects/p/plans', headers=headers).status_code == 401


@pytest.mark.parametrize('name,value', [('PPT_LOCAL_TEST_ONLY', '1'), ('PPT_LOCAL_TEST_ONLY', '0'),
    ('PPT_EXECUTION_ENABLED', 'true')])
def test_forbidden_flags(site_client, monkeypatch, name, value):
    site, _, _, dist = site_client
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError):
        create_site(site.state.settings, site.state.engine, dist)


def test_startup_dist_and_no_implicit_migration(site_client, monkeypatch, tmp_path):
    site, _, _, dist = site_client
    config = site.state.settings
    monkeypatch.delenv('PPT_WEB_DIST', raising=False)
    with pytest.raises(RuntimeError, match='PPT_WEB_DIST'):
        create_site(config, site.state.engine)
    monkeypatch.setenv('PPT_WEB_DIST', str(dist))
    empty_engine = create_engine('sqlite://')
    create_site(config, empty_engine)
    assert inspect(empty_engine).get_table_names() == []
    with pytest.raises(RuntimeError, match='PostgreSQL'):
        create_site(config, dist=dist)
    with pytest.raises(RuntimeError):
        create_site(config, site.state.engine, tmp_path / 'missing')
    (dist / 'index.html').write_text('')
    with pytest.raises(RuntimeError):
        create_site(config, site.state.engine, dist)
    (dist / 'index.html').write_text('index')
    import ppt_service.server.site as site_module
    original = site_module.os.open
    def unreadable(path, flags):
        if Path(path).name == 'index.html':
            raise PermissionError('test unreadable index')
        return original(path, flags)
    monkeypatch.setattr(site_module.os, 'open', unreadable)
    with pytest.raises(RuntimeError):
        create_site(config, site.state.engine, dist)


@pytest.mark.parametrize('limit', ['index', 'file', 'total', 'count'])
def test_snapshot_limits(site_client, monkeypatch, limit):
    import ppt_service.server.site as site_module
    site, _, _, dist = site_client
    if limit == 'index':
        (dist / 'index.html').write_bytes(b'x' * (1024 * 1024 + 1))
    elif limit == 'file':
        monkeypatch.setattr(site_module, 'MAX_STATIC_FILE_BYTES', 1)
    elif limit == 'total':
        monkeypatch.setattr(site_module, 'MAX_STATIC_BYTES', 1)
    else:
        monkeypatch.setattr(site_module, 'MAX_STATIC_FILES', 1)
    with pytest.raises(RuntimeError):
        create_site(site.state.settings, site.state.engine, dist)


def test_actual_built_package(auth_client, monkeypatch):
    import os
    import hashlib
    from html.parser import HTMLParser
    dist_value = os.getenv('PPT_SITE_TEST_DIST')
    if not dist_value:
        pytest.skip('Set PPT_SITE_TEST_DIST to a separately built frontend for packaging integration')
    monkeypatch.delenv('PPT_LOCAL_TEST_ONLY', raising=False)
    monkeypatch.delenv('PPT_EXECUTION_ENABLED', raising=False)
    api, _, token = auth_client
    dist = Path(dist_value)
    site = create_site(api.state.settings, api.state.engine, dist)
    site.state.jwks = api.state.jwks
    c = TestClient(site, base_url='https://ppt.test', client=('127.0.0.1', 12345))
    headers = login(c, token)
    response = c.get('/', headers=headers)
    assert response.status_code == 200
    assert response.content == (dist / 'index.html').read_bytes()
    paths = []
    class References(HTMLParser):
        def handle_starttag(self, tag, attrs):
            for name, value in attrs:
                if name in ('src', 'href') and value and value.startswith('/assets/'):
                    paths.append(value)
    References().feed(response.text)
    assert any(path.endswith('.js') for path in paths)
    assert any(path.endswith('.css') for path in paths)
    for path in paths:
        result = c.get(path, headers=headers)
        assert result.status_code == 200
        assert hashlib.sha256(result.content).digest() == hashlib.sha256((dist / path.lstrip('/')).read_bytes()).digest()
        assert c.get(path).status_code == 401


@pytest.mark.parametrize('target', ['index', 'asset', 'assets', 'root'])
def test_symlinks_rejected_at_startup(site_client, tmp_path, target):
    site, _, _, dist = site_client
    outside = tmp_path / 'outside'
    outside.write_text('outside')
    if target == 'index':
        (dist / 'index.html').unlink()
        symlink(outside,dist / 'index.html')
    elif target == 'asset':
        symlink(outside,dist / 'assets/link.js')
    elif target == 'assets':
        other = tmp_path / 'other-assets'
        (dist / 'assets').rename(other)
        symlink(other,dist / 'assets',directory=True)
    else:
        alias = tmp_path / 'alias'
        symlink(dist,alias,directory=True)
        dist = alias
    with pytest.raises(RuntimeError):
        create_site(site.state.settings, site.state.engine, dist)
