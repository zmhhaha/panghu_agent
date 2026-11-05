"""Dynamic authoring intent and legacy preservation, without model/TTS I/O."""
from copy import deepcopy
import json
import io
import wave

import pytest
from fastapi.testclient import TestClient

from agent_service.contracts import ServiceError
from video_workbench.app import create_app
from video_workbench.motion import example_plan


OWNER = 'alice'
SCENE = dict(title='队列', narration='先接收请求。然后放入队列。最后执行任务。',
             visual_description='用请求、队列和执行节点说明流程。', duration_seconds=8, format='svg')
SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><text x="40" y="60">原静态素材</text></svg>'
RUNTIME = dict(schema='video.motion-runtime.v1', hyperframes='0.8.149',
    hyperframes_source='2905162d925fc87fb8242e24ed0bbf025d307ea1', node='v22.20.0',
    browser='HeadlessChrome/141.0.7390.54', ffmpeg='ffmpeg version 5.1.7-0+deb12u1', gsap='3.14.2',
    gsap_sha256='a' * 64, font_sha256='f' * 64, dpkg_sha256='d' * 64,
    npm_lock_sha256='e' * 64, image_digest='sha256:' + '1' * 64)


@pytest.fixture
def env(tmp_path):
    app = create_app(db_path=tmp_path / 'state.db', trusted_hosts={'testserver'},
                     background=False, motion_enabled=True, render_enabled=True)
    with TestClient(app) as client:
        client.headers['X-Forwarded-User'] = OWNER
        client.headers['X-CSRF-Token'] = client.get('/api/session').json()['csrf_token']
        yield client, app


def new_project(env, **fields):
    response = env[0].post('/api/projects', json=dict(title='动态讲解', script=SCENE['narration'], **fields))
    assert response.status_code == 200, response.text
    return response.json()


def add_scene(env, project, **fields):
    response = env[0].post(f'/api/projects/{project["id"]}/scenes', json=dict(SCENE, **fields))
    assert response.status_code == 200, response.text
    return response.json()


def read_project(env, project):
    response = env[0].get(f'/api/projects/{project["id"]}')
    assert response.status_code == 200, response.text
    return response.json()


def project_raw(env, project):
    with env[1].state.store.transaction() as db:
        return db.execute('SELECT document FROM projects WHERE id=?', (project['id'],)).fetchone()[0]


def convert(env, project, **body):
    return env[0].patch(f'/api/projects/{project["id"]}/production-mode',
        json=dict(mode='motion', expected_revision=project['revision'], **body))


def adopt_static(env, project, scene):
    store = env[1].state.store
    operation = store.start(OWNER, project['id'], 'generate', dict(expected_revision=project['revision']),
                            'static-' + scene['id'], scene_id=scene['id'])
    store.complete(operation['id'], dict(source=SVG, format='svg', summary='已保存静态素材'))
    project = store.project(OWNER, project['id'])
    version = store._scene(project, scene['id'])['versions'][0]
    return store.adopt(OWNER, scene['id'], version['id'], None)


def test_new_manual_and_storyboard_scenes_are_pending_motion_without_submissions(env):
    client, app = env
    assert client.get('/api/session').json()['default_production_mode'] == 'motion'
    project = new_project(env)
    assert project['production_mode'] == 'motion'
    project = add_scene(env, project)
    scene = read_project(env, project)['scenes'][0]
    assert scene['production_mode'] == scene['motion']['mode'] == 'motion'
    assert scene['motion']['head_version_id'] is None and scene['motion']['versions'] == []
    assert scene['head_version_id'] is None and scene['head_audio_version_id'] is None
    with app.state.store.transaction() as db:
        for table in ('motion_operations', 'audio_operations', 'video_renders'):
            assert db.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0
    storyboard_project = new_project(env)
    operation = app.state.store.start(OWNER, storyboard_project['id'], 'storyboard',
        dict(expected_revision=storyboard_project['revision']), 'storyboard-fixture')
    app.state.store.complete(operation['id'], dict(scenes=[SCENE, dict(SCENE, title='执行')]))
    scenes = read_project(env, storyboard_project)['scenes']
    assert len(scenes) == 2 and all(s['motion']['mode'] == 'motion' for s in scenes)
    assert all(s['motion']['head_version_id'] is None for s in scenes)
    listed = client.get('/api/projects').json()['projects']
    assert all(p['production_mode'] == 'motion' for p in listed)


def test_editing_a_dynamic_scene_preserves_its_intent(env):
    project = add_scene(env, new_project(env))
    scene = project['scenes'][0]
    response = env[0].patch(f'/api/scenes/{scene["id"]}',
        json=dict(SCENE, title='更新标题', expected_revision=project['revision']))
    assert response.status_code == 200
    assert read_project(env, project)['scenes'][0]['production_mode'] == 'motion'


def test_pending_dynamic_intent_never_becomes_a_valid_adoption_or_static_export(env):
    project = add_scene(env, new_project(env))
    production = env[1].state.production
    # An environment identity is present; resolving a missing adoption stops
    # before compilation/renderer access, even if audio is also still missing.
    production.runtime_fingerprint = {}
    with pytest.raises(ServiceError, match='missing_motion_plan'):
        production.snapshot_v2(OWNER, project['id'],
            dict(expected_revision=project['revision'], purpose='delivery', subtitles=False))
    assert read_project(env, project)['scenes'][0]['motion']['head_version_id'] is None


def test_gate_off_defaults_static_and_rejects_explicit_dynamic(tmp_path):
    app = create_app(db_path=tmp_path / 'off.db', local_mode=True, background=False,
                     motion_enabled=False, trusted_hosts={'testserver'})
    with TestClient(app) as client:
        session = client.get('/api/session').json()
        client.headers['X-CSRF-Token'] = session['csrf_token']
        assert session['default_production_mode'] == 'static'
        response = client.post('/api/projects', json=dict(title='兼容', script='原稿'))
        assert response.status_code == 200 and response.json()['production_mode'] == 'static'
        explicit = client.post('/api/projects', json=dict(title='动态', script='原稿', production_mode='motion'))
        assert explicit.status_code == 503 and explicit.json()['detail']['code'] == 'motion_disabled'
        converted = client.patch(f'/api/projects/{response.json()["id"]}/production-mode',
                                 json=dict(mode='motion', expected_revision=1))
        assert converted.status_code == 503
        assert len(client.get('/api/projects').json()['projects']) == 1


def test_legacy_read_does_not_write_defaults_or_reinterpret_static_media(env):
    project = add_scene(env, new_project(env, production_mode='static'))
    project = adopt_static(env, project, project['scenes'][0])
    with env[1].state.store.transaction() as db:
        legacy = env[1].state.store._project(db, OWNER, project['id'])
        legacy.pop('production_mode')
        legacy['scenes'][0].pop('production_mode')
        db.execute('UPDATE projects SET document=? WHERE id=?', (json.dumps(legacy), project['id']))
    raw = project_raw(env, project)
    shown = read_project(env, project)
    assert shown['production_mode'] == shown['scenes'][0]['motion']['mode'] == 'static'
    assert shown['scenes'][0]['head_version_id'] == legacy['scenes'][0]['head_version_id']
    assert project_raw(env, project) == raw
    assert env[0].get(f'/api/projects/{project["id"]}/export').status_code == 200
    version_id = shown['scenes'][0]['head_version_id']
    assert env[0].get(f'/api/versions/{version_id}/source').text == SVG


def test_conversion_preserves_selections_and_only_promotes_unselected_scenes(env):
    project = add_scene(env, new_project(env, production_mode='static'), title='已有静态采用')
    project = adopt_static(env, project, project['scenes'][0])
    project = add_scene(env, project, title='未制作')
    project = add_scene(env, project, title='明确保留静态')
    project = env[1].state.motion_store.set_mode(OWNER, project['scenes'][2]['id'], 'static', project['revision'])
    before = deepcopy(project)
    with env[1].state.store.transaction() as db:
        static_rows = [tuple(row) for row in db.execute('SELECT * FROM versions')]
        old_modes = [tuple(row) for row in db.execute('SELECT * FROM motion_heads')]
    response = convert(env, project)
    assert response.status_code == 200, response.text
    converted = response.json()
    assert converted['production_mode'] == 'motion' and converted['revision'] == before['revision'] + 1
    assert [s['motion']['mode'] for s in converted['scenes']] == ['static', 'motion', 'static']
    assert converted['script'] == before['script'] and converted['style'] == before['style']
    for old, scene in zip(before['scenes'], converted['scenes']):
        for key in ('id', 'title', 'narration', 'visual_description', 'head_version_id', 'versions',
                    'head_audio_version_id', 'speech_settings'):
            assert scene[key] == old[key]
        assert scene['motion']['head_version_id'] == old['motion']['head_version_id']
    with env[1].state.store.transaction() as db:
        assert [tuple(row) for row in db.execute('SELECT * FROM versions')] == static_rows
        assert [tuple(row) for row in db.execute('SELECT * FROM motion_heads')] == old_modes
    assert convert(env, converted).json()['revision'] == converted['revision']
    later = add_scene(env, converted, title='新加镜头')
    assert read_project(env, later)['scenes'][-1]['motion']['mode'] == 'motion'


def test_conversion_requires_owner_revision_and_csrf(env):
    project = new_project(env, production_mode='static')
    before = project_raw(env, project)
    assert convert(env, dict(project, revision=99)).status_code == 409
    env[0].headers['X-Forwarded-User'] = 'bob'
    session = env[0].get('/api/session').json()
    env[0].headers['X-CSRF-Token'] = session['csrf_token']
    assert convert(env, project).status_code == 404
    env[0].headers['X-Forwarded-User'] = OWNER
    assert convert(env, project).status_code == 403
    assert project_raw(env, project) == before


def test_conversion_keeps_real_adopted_motion_candidate_valid(env):
    project = add_scene(env, new_project(env, production_mode='static'))
    motion = env[1].state.motion_store
    scene_id = project['scenes'][0]['id']
    operation = motion.start(OWNER, scene_id, dict(expected_revision=project['revision']), 'synthetic-motion')
    operation = motion.operation(operation['id'], OWNER)
    complete = motion.complete(operation['id'], dict(schema='video.motion-result.v1',
        input_sha256=operation['input_sha256'], plan=example_plan(operation['document']['beats'])))
    project = read_project(env, project)
    project = motion.adopt(OWNER, scene_id, complete['version_id'], None, project['revision'])
    before = deepcopy(project['scenes'][0]['motion'])
    converted = convert(env, project)
    assert converted.status_code == 200
    assert converted.json()['scenes'][0]['motion'] == before
    assert not motion.version(OWNER, complete['version_id'])['stale']


def test_converted_all_static_adoptions_can_export_v2_and_keep_history_replay(env):
    client, app = env
    project = add_scene(env, new_project(env, production_mode='static'))
    project = adopt_static(env, project, project['scenes'][0])
    scene_id = project['scenes'][0]['id']
    request = dict(expected_revision=project['revision'], silent=True, subtitles=False)
    old = app.state.production.start_render(OWNER, project['id'], request, 'old-static-export')
    with app.state.store.transaction() as db:
        record = json.loads(db.execute('SELECT document FROM video_renders WHERE id=?', (old['id'],)).fetchone()[0])
        record.update(status='completed')
        db.execute('UPDATE video_renders SET document=?,data=? WHERE id=?',
                   (json.dumps(record), b'fixture-old-media', old['id']))
    audio = io.BytesIO()
    with wave.open(audio, 'wb') as stream:
        stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        stream.writeframes(b'\x01\x00' * 24000)
    project = app.state.store.upload_audio(OWNER, scene_id,
        dict(expected_revision=project['revision']), 'existing-upload', audio.getvalue())
    project = app.state.store.adopt_audio(OWNER, scene_id, project['scenes'][0]['audio_versions'][0]['id'], None)
    converted = convert(env, project)
    assert converted.status_code == 200, converted.text
    project = converted.json()
    assert project['scenes'][0]['motion']['mode'] == 'static'
    app.state.production.runtime_fingerprint = RUNTIME
    snapshot = app.state.production.snapshot_v2(OWNER, project['id'],
        dict(expected_revision=project['revision'], purpose='delivery', subtitles=False))
    assert snapshot['schema'] == 'video.render-snapshot.v2'
    assert snapshot['scenes'][0]['visual_mode'] == 'static'
    assert app.state.production.start_render(OWNER, project['id'], request, 'old-static-export')['id'] == old['id']
    assert client.get(f'/api/renders/{old["id"]}/video').content == b'fixture-old-media'
    assert client.get(f'/api/projects/{project["id"]}/export').status_code == 200


@pytest.mark.parametrize('table', ['operations', 'motion_operations', 'audio_operations',
                                  'explanation_operations', 'video_renders'])
def test_conversion_refuses_inflight_work_atomically(env, table):
    project = new_project(env, production_mode='static')
    before = project_raw(env, project)
    with env[1].state.store.transaction() as db:
        # Real schemas/transaction; synthetic in-flight records avoid any
        # upstream submission while exercising all five admission boundaries.
        columns = [row[1] for row in db.execute('PRAGMA table_info(' + table + ')')]
        values = {key: 'fixture-' + key for key in columns}
        values.update(id='busy-' + table, owner=OWNER, project_id=project['id'],
                      document=json.dumps(dict(status='running')))
        db.execute('INSERT INTO ' + table + ' (' + ','.join(columns) + ') VALUES (' +
                   ','.join('?' for _ in columns) + ')', [values[key] for key in columns])
    response = convert(env, project)
    assert response.status_code == 409 and response.json()['detail']['code'] == 'project_production_mode_busy'
    assert project_raw(env, project) == before


@pytest.mark.parametrize('route', ['timeline', 'renders'])
def test_static_endpoint_cannot_bypass_dynamic_project_intent(env, route):
    project = add_scene(env, new_project(env))
    project = adopt_static(env, project, project['scenes'][0])
    client = env[0]
    if route == 'timeline':
        response = client.get(f'/api/projects/{project["id"]}/timeline?silent=true&subtitles=false')
    else:
        response = client.post(f'/api/projects/{project["id"]}/renders',
            json=dict(expected_revision=project['revision'], silent=True, subtitles=False),
            headers={'Idempotency-Key': 'must-not-become-static'})
    assert response.status_code == 409 and response.json()['detail']['code'] == 'dynamic_workflow_required'
    assert client.get(f'/api/projects/{project["id"]}/renders').json()['renders'] == []


def test_legacy_static_project_with_a_dynamic_selection_cannot_use_v1(env):
    project = add_scene(env, new_project(env, production_mode='static'))
    with env[1].state.store.transaction() as db:
        db.execute('INSERT INTO motion_heads VALUES (?, ?, ?, ?, ?)',
                   (OWNER, project['id'], project['scenes'][0]['id'], None, 'motion'))
    response = env[0].get(f'/api/projects/{project["id"]}/timeline?silent=true&subtitles=false')
    assert response.status_code == 409 and response.json()['detail']['code'] == 'dynamic_workflow_required'
