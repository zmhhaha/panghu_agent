"""HTTP routing tests; sentinel bytes are not claimed to be rendered media."""
import json
import hashlib

import pytest
from fastapi.testclient import TestClient

from agent_service.contracts import ServiceError
from video_workbench.app import create_app


class FilmAccess:
    enabled = False

    def __init__(self):
        self.rows, self.data, self.cancels = {}, {}, []

    def renders(self, owner, project_id):
        self.store.project(owner, project_id)
        return [dict(row) for row in self.rows.values() if row['project_id'] == project_id]

    def video(self, owner, identity):
        if identity not in self.rows:
            raise ServiceError('not_found', 404)
        row = self.rows[identity]
        self.store.project(owner, row['project_id'])
        if row['status'] != 'completed':
            raise ServiceError('render_not_ready', 409)
        return self.data[identity]

    def cancel_render(self, owner, identity):
        if identity not in self.rows:
            raise ServiceError('not_found', 404)
        row = self.rows[identity]
        self.store.project(owner, row['project_id'])
        self.cancels.append(identity)
        row['status'] = 'cancelling'
        return dict(row)

    def stop(self):
        pass


@pytest.fixture
def env(tmp_path):
    film = FilmAccess()
    app = create_app(db_path=tmp_path / 'video.db', local_mode=True, background=False,
                     trusted_hosts={'testserver'}, film_creation=film)
    film.store = app.state.store
    with TestClient(app) as client:
        client.headers['X-CSRF-Token'] = client.get('/api/session').json()['csrf_token']
        project = client.post('/api/projects', json={'title': '统一草稿', 'script': '完整讲解。'}).json()
        film.rows['film-render'] = dict(id='film-render', project_id=project['id'],
            renderer_mode='hyperframes-film', status='completed', created_at=2,
            quality='draft', selection='draft_only', actual_duration_seconds=1.0,
            content_state='current')
        film.data['film-render'] = b'sentinel-http-routing-bytes'
        yield app, client, project, film


def test_whole_project_draft_uses_existing_list_download_and_range_routes(env):
    _, client, project, film = env
    rows = client.get(f'/api/projects/{project["id"]}/renders').json()['renders']
    assert rows == [film.rows['film-render']]
    response = client.get('/api/renders/film-render/video', headers={'Range': 'bytes=0-7'})
    assert response.status_code == 206 and response.content == b'sentinel'
    assert response.headers['content-range'].startswith('bytes 0-7/')
    assert client.get('/api/renders/missing/video').status_code == 404
    assert film.cancels == []


def test_legacy_not_ready_does_not_fall_through_to_new_renderer(env):
    app, client, project, film = env
    # An existing old-route ID must keep its own status, even if a new-route
    # sentinel happens to share it. Only a genuine 404 permits dispatch.
    old = dict(id='film-render', status='queued', project_id=project['id'])
    with app.state.store.transaction() as db:
        db.execute('INSERT INTO video_renders VALUES (?,?,?,?,?,?,?,NULL)',
            ('film-render', 'local-developer', project['id'], 'old-render-key', 'old-hash',
             json.dumps(old), '{}'))
    assert client.get('/api/renders/film-render/video').status_code == 409
    assert film.cancels == []


def test_cancel_is_explicit_and_csrf_protected(env):
    _, client, _, film = env
    response = client.post('/api/renders/film-render/cancel', headers={'X-CSRF-Token': 'wrong'})
    assert response.status_code == 403 and film.cancels == []
    response = client.post('/api/renders/film-render/cancel')
    assert response.status_code == 200 and film.cancels == ['film-render']


def test_deleted_project_hides_new_media_and_restore_preserves_it(env):
    _, client, project, _ = env
    response = client.post(f'/api/projects/{project["id"]}/trash', json={'expected_revision': project['revision']})
    assert response.status_code == 200
    assert client.get('/api/renders/film-render/video').status_code == 404
    assert client.get(f'/api/projects/{project["id"]}/renders').status_code == 404
    revision = response.json()['project']['revision']
    assert client.post(f'/api/projects/{project["id"]}/restore', json={'expected_revision': revision}).status_code == 200
    assert client.get('/api/renders/film-render/video').content == b'sentinel-http-routing-bytes'
    with client.app.state.store.transaction() as db:
        db.execute('UPDATE projects SET owner=? WHERE id=?', ('another-owner', project['id']))
    assert client.get('/api/renders/film-render/video').status_code == 404
    assert client.post('/api/renders/film-render/cancel').status_code == 404


@pytest.mark.parametrize('table', ['film_module_operations', 'film_render_operations'])
def test_inflight_film_children_block_project_removal_in_same_transaction(env, table):
    app, client, project, _ = env
    with app.state.store.transaction() as db:
        db.execute('CREATE TABLE ' + table + ' (project_id TEXT,document TEXT)')
        db.execute('INSERT INTO ' + table + ' VALUES (?,?)', (project['id'], json.dumps({'status': 'submitting'})))
    response = client.post(f'/api/projects/{project["id"]}/trash', json={'expected_revision': project['revision']})
    assert response.status_code == 409 and response.json()['detail']['code'] == 'project_delete_busy'


def test_env_factory_installs_whole_film_path_without_borrowing_old_credentials(tmp_path, monkeypatch):
    from test_film_production_config import release
    _, path = release(tmp_path)
    monkeypatch.setenv('VIDEO_FILM_PRODUCTION_RELEASE_FILE', str(path))
    monkeypatch.setenv('VIDEO_FILM_PRODUCTION_ENABLED', '0')
    app = create_app(db_path=tmp_path / 'factory.db', local_mode=True, background=False,
                     trusted_hosts={'testserver'})
    assert app.state.film_creation is not None
    assert app.state.film_creation.gateway is None
    assert app.state.film_creation.renderer is None
    assert app.state.production_run_store.film_creation is app.state.film_creation
    with TestClient(app) as client:
        session = client.get('/api/session').json()
        assert session['production_runs_enabled'] is False
    with pytest.raises(ServiceError) as error:
        app.state.film_creation.readiness()
    assert error.value.status == 503


def test_removing_new_admission_release_does_not_hide_previous_film_media(tmp_path, monkeypatch):
    from video_workbench.store import Store
    from video_workbench.film_creation_store import FilmCreationStore
    from video_workbench.film_production_config import FilmProductionSettings
    from test_film_production_config import release
    _, path = release(tmp_path)
    settings = FilmProductionSettings.from_env({'VIDEO_FILM_PRODUCTION_RELEASE_FILE': str(path)})
    store = Store(tmp_path / 'history.db')
    FilmCreationStore(store, runtime_capabilities=settings.runtime_capabilities,
        runtime_fingerprint=settings.runtime_fingerprint, host_files=settings.host_files,
        trusted_host_sha256=settings.trusted_host_sha256, image=settings.image, assets=settings.assets)
    project = store.create_project('local-developer', {'title':'历史片段', 'script':'原始文稿', 'style':''})
    raw = b'sentinel-previous-film'
    op = dict(id='previous-film', kind='render', status='completed', project_id=project['id'],
        duration_seconds=1, created_at=1,
        request={'film': {'content_binding_sha256':'a' * 64}},
        result={'duration_seconds':1, 'sha256':hashlib.sha256(raw).hexdigest()})
    with store.transaction() as db:
        db.execute('INSERT INTO film_render_operations VALUES(?,?,?,?,?,?,?,?)',
            (op['id'], 'local-developer', project['id'], 'history-key', 'hash', json.dumps(op), '{}', raw))
    monkeypatch.delenv('VIDEO_FILM_PRODUCTION_RELEASE_FILE', raising=False)
    monkeypatch.setenv('VIDEO_FILM_PRODUCTION_ENABLED', '0')
    app = create_app(db_path=store.path, local_mode=True, background=False, trusted_hosts={'testserver'})
    assert app.state.film_creation is not None and not app.state.film_creation.enabled
    with TestClient(app) as client:
        assert client.get('/api/renders/previous-film/video').content == raw
        rows = client.get(f'/api/projects/{project["id"]}/renders').json()['renders']
        assert rows[0]['id'] == 'previous-film' and rows[0]['content_state'] == 'stale'
        assert client.get('/api/session').json()['production_runs_enabled'] is False
