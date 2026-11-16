"""Project trash preserves real SQLite media and blocks in-flight admissions."""
from concurrent.futures import ThreadPoolExecutor
import io
import json
import threading
import wave

import pytest
from fastapi.testclient import TestClient

from agent_service.contracts import ServiceError
from video_workbench.app import create_app
from video_workbench.store import Store


OWNER = 'local-developer'
SCENE = dict(title='任务', narration='请求进入队列。', visual_description='任务向右移动。',
             duration_seconds=4, format='svg')
SVG = '<svg xmlns="http://www.w3.org/2000/svg"><text>任务</text></svg>'


@pytest.fixture
def env(tmp_path):
    app = create_app(db_path=tmp_path / 'projects.db', local_mode=True,
                     background=False, trusted_hosts={'testserver'})
    with TestClient(app) as client:
        client.headers['X-CSRF-Token'] = client.get('/api/session').json()['csrf_token']
        project = client.post('/api/projects', json=dict(title='可恢复的项目', script='请求进入队列。')).json()
        yield client, app, project


def change(client, project, action, revision=None):
    return client.post(f'/api/projects/{project["id"]}/{action}',
        json=dict(expected_revision=project['revision'] if revision is None else revision))


def test_trash_restore_preserves_media_and_heads_across_restart(env):
    client, app, project = env
    store = app.state.store
    project = store.save_scene(OWNER, project['id'], SCENE)
    scene_id = project['scenes'][0]['id']
    op = store.start(OWNER, project['id'], 'scene', dict(expected_revision=project['revision']),
                     'original-scene-task', scene_id=scene_id)
    store.complete(op['id'], dict(source=SVG, format='svg', summary='原样片'))
    project = store.project(OWNER, project['id'])
    version = project['scenes'][0]['versions'][0]['id']
    project = store.adopt(OWNER, scene_id, version, None)
    output = io.BytesIO()
    with wave.open(output, 'wb') as stream:
        stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        stream.writeframes(b'\x04\x08' * 24000)
    audio = output.getvalue()
    project = store.upload_audio(OWNER, scene_id, dict(expected_revision=project['revision']),
                                  'original-upload-task', audio, '录音.wav')
    audio_id = project['scenes'][0]['audio_versions'][0]['id']
    project = store.adopt_audio(OWNER, scene_id, audio_id, None)
    before = store.project(OWNER, project['id'])
    with store.transaction() as db:
        rows_before = {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table)]
            for table in ('versions', 'audio_versions', 'operations', 'audio_operations')}
    deleted = change(client, project, 'trash')
    assert deleted.status_code == 200
    summary = deleted.json()['project']
    assert summary['revision'] == project['revision'] + 1 and summary['deleted_at'] > 0
    assert client.get('/api/projects').json() == dict(projects=[])
    assert client.get('/api/projects?deleted=true').json()['projects'] == [summary]
    for route in (f'/api/projects/{project["id"]}', f'/api/versions/{version}/source',
                  f'/api/audio-versions/{audio_id}/audio'):
        assert client.get(route).status_code == 404
    assert client.post(f'/api/scenes/{scene_id}/generate', json=dict(expected_revision=summary['revision']),
                       headers={'Idempotency-Key': 'after-trash-generate'}).status_code in {404, 503}
    with pytest.raises(ServiceError) as error:
        store.start(OWNER, project['id'], 'scene', dict(expected_revision=summary['revision']),
                    'new-admission-after-trash', scene_id=scene_id)
    assert error.value.status == 404
    # A reopened Store reads the tombstone; no in-memory visibility cache.
    reopened = Store(store.path)
    assert reopened.list_projects(OWNER) == []
    assert reopened.list_projects(OWNER, deleted=True) == [summary]
    restored = change(client, project, 'restore', summary['revision'])
    assert restored.status_code == 200 and restored.json()['project']['deleted_at'] is None
    after = store.project(OWNER, project['id'])
    assert after['scenes'] == before['scenes']
    assert after['script'] == before['script'] and after['revision'] == before['revision'] + 2
    assert store.version(OWNER, version)['source'] == SVG
    assert store.audio_version(OWNER, audio_id)['data'] == audio
    with store.transaction() as db:
        assert rows_before == {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table)]
            for table in rows_before}


def test_revision_replay_csrf_and_stale_inverse_requests(env):
    client, app, project = env
    no_csrf = client.post(f'/api/projects/{project["id"]}/trash', json=dict(expected_revision=1),
                         headers={'X-CSRF-Token': ''})
    assert no_csrf.status_code == 403
    assert change(client, project, 'trash', True).status_code == 422
    first = change(client, project, 'trash').json()['project']
    assert change(client, project, 'trash').json()['project'] == first
    assert change(client, project, 'restore', project['revision']).status_code == 409
    restored = change(client, project, 'restore', first['revision']).json()['project']
    assert change(client, project, 'restore', first['revision']).json()['project'] == restored
    assert change(client, project, 'trash').status_code == 409
    assert len(app.state.store.list_projects(OWNER)) == 1


def test_owner_isolation_in_lists_transitions_and_media(env):
    _, app, project = env
    store = app.state.store
    assert store.list_projects('another-owner') == []
    for deleted in (False, True):
        with pytest.raises(ServiceError) as error:
            store.set_project_deleted('another-owner', project['id'], project['revision'], deleted=deleted)
        assert error.value.status == 404
    store.set_project_deleted(OWNER, project['id'], project['revision'], deleted=True)
    assert store.list_projects('another-owner', deleted=True) == []


def test_http_owner_cannot_see_or_restore_someone_elses_trash(tmp_path):
    app = create_app(db_path=tmp_path / 'owners.db', background=False, trusted_hosts={'testserver'})
    with TestClient(app) as client:
        def login(owner):
            client.headers['X-Forwarded-User'] = owner
            client.headers['X-CSRF-Token'] = client.get('/api/session').json()['csrf_token']
        login('alice')
        project = client.post('/api/projects', json=dict(title='Alice项目', script='原文。')).json()
        deleted = change(client, project, 'trash').json()['project']
        login('bob')
        assert client.get('/api/projects?deleted=true').json() == dict(projects=[])
        assert change(client, project, 'restore', deleted['revision']).status_code == 404
        assert change(client, project, 'trash', deleted['revision']).status_code == 404
        login('alice')
        assert change(client, project, 'restore', deleted['revision']).status_code == 200


@pytest.mark.parametrize('table,document', [
    ('operations', dict(status='queued')),
    ('audio_operations', dict(status='running')),
    ('motion_operations', dict(status='submitting', submit_attempted=True)),
    ('explanation_operations', dict(status='cancelling')),
    ('joint_plan_operations', dict(status='running')),
    ('video_renders', dict(status='running')),
    ('video_batches', dict(status='blocked', items=[])),
    ('production_runs', dict(status='running', intents={})),
    ('production_runs', dict(status='stale', draining=True, intents={})),
    ('production_runs', dict(status='failed', blocked_reason='submission_uncertain', intents={})),
    ('production_runs', dict(status='cancelled', intents={'child': dict(active=True)})),
    ('audio_operations', dict(status='failed', error='speech_submission_uncertain')),
    ('audio_operations', dict(status='cancelled', submit_attempted=True, upstream_stop_confirmed=False)),
    ('motion_operations', dict(status='unexpected-future-status')),
])
def test_all_work_channels_and_uncertain_stops_block_trash(env, table, document):
    client, app, project = env
    # Inserts only minimal journal metadata; no remote gateways or media jobs.
    document = dict(document, id='busy-work', owner=OWNER, project_id=project['id'])
    with app.state.store.transaction() as db:
        columns = [row[1] for row in db.execute('PRAGMA table_info(' + table + ')')]
        values = [json.dumps(document) if column == 'document' else '{}' if column == 'snapshot' else None if column == 'data'
                  else project['id'] if column == 'project_id' else OWNER if column == 'owner'
                  else 'busy-work' for column in columns]
        db.execute('INSERT INTO ' + table + ' VALUES (' + ','.join('?' for _ in columns) + ')', values)
    result = change(client, project, 'trash')
    assert result.status_code == 409 and result.json()['detail']['code'] == 'project_delete_busy'
    assert len(client.get('/api/projects').json()['projects']) == 1
    assert client.get('/api/projects?deleted=true').json()['projects'] == []
    with app.state.store.transaction() as db:
        assert json.loads(db.execute('SELECT document FROM ' + table).fetchone()[0]) == document


def test_real_queued_creation_must_end_before_trash(env):
    client, app, project = env
    store = app.state.store
    operation = store.start(OWNER, project['id'], 'storyboard', dict(expected_revision=1), 'pending-storyboard')
    current = store.project(OWNER, project['id'])
    assert change(client, current, 'trash').status_code == 409
    store.update_operation(operation['id'], status='failed', error='invalid_agent_output')
    assert change(client, current, 'trash').status_code == 200


@pytest.mark.parametrize('terminal', [
    dict(status='failed', error='speech_upstream_rejected', submit_attempted=True),
    dict(status='failed', error='speech_provider_changed', submit_attempted=True, upstream_stop_confirmed=True),
    dict(status='cancelled', error=None, submit_attempted=False),
    dict(status='timed_out', error='speech_timeout', submit_attempted=True, upstream_stop_confirmed=True),
])
def test_known_ended_and_historical_audio_failures_do_not_make_project_undeletable(env, terminal):
    client, app, project = env
    document = dict(terminal, id='finished-audio', owner=OWNER, project_id=project['id'])
    with app.state.store.transaction() as db:
        db.execute('INSERT INTO audio_operations VALUES (?, ?, ?, ?, ?, ?, ?)',
            ('finished-audio', OWNER, project['id'], 'old-scene', 'finished-audio', 'checksum', json.dumps(document)))
    assert change(client, project, 'trash').status_code == 200


def test_deleted_private_draft_runs_and_media_require_restore(env):
    client, app, project = env
    with app.state.store.transaction() as db:
        for table, document in (
            ('video_batches', dict(id='old-batch', project_id=project['id'], status='completed', items=[])),
            ('production_runs', dict(id='old-run', project_id=project['id'], status='completed', items=[], intents={})),
        ):
            db.execute('INSERT INTO ' + table + ' VALUES (?, ?, ?, ?, ?, ?)',
                (document['id'], OWNER, project['id'], document['id'], 'checksum', json.dumps(document)))
        db.execute('INSERT INTO video_renders VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            ('old-video', OWNER, project['id'], 'old-video', 'checksum',
             json.dumps(dict(project_id=project['id'], status='completed')), '{}', b'preserved-media'))
    routes = ('/api/batches/old-batch', '/api/production-runs/old-run', '/api/renders/old-video/video')
    assert all(client.get(route).status_code == 200 for route in routes)
    removed = change(client, project, 'trash').json()['project']
    assert all(client.get(route).status_code == 404 for route in routes)
    assert change(client, project, 'restore', removed['revision']).status_code == 200
    assert all(client.get(route).status_code == 200 for route in routes)
    assert client.get('/api/renders/old-video/video').content == b'preserved-media'


def test_project_management_module_is_served_by_real_static_mount(env):
    client, _, _ = env
    response = client.get('/assets/project-management.mjs')
    assert response.status_code == 200 and 'createProjectManagement' in response.text


def test_independent_sqlite_admission_and_trash_cannot_both_win(tmp_path):
    path = tmp_path / 'race.db'
    admission, trash = Store(path), Store(path)
    project = admission.create_project('alice', dict(title='并发', script='同一个项目', style=''))
    barrier = threading.Barrier(2)
    def admit():
        barrier.wait()
        try:
            admission.start('alice', project['id'], 'storyboard', dict(expected_revision=1), 'race-storyboard')
            return 'admitted'
        except ServiceError as error:
            assert error.status in {404, 409}
            return 'rejected'
    def remove():
        barrier.wait()
        try:
            trash.set_project_deleted('alice', project['id'], 1, deleted=True)
            return 'trashed'
        except ServiceError as error:
            assert error.status == 409
            return 'rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:
        left, right = pool.submit(admit), pool.submit(remove)
        outcomes = [left.result(), right.result()]
    assert outcomes in (['admitted', 'rejected'], ['rejected', 'trashed'])
    assert not (admission.active_operations() and admission.list_projects('alice', deleted=True))
