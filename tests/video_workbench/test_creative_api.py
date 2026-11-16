"""Creative production admission and owner boundaries; no model or renderer I/O."""
from copy import deepcopy
import hashlib
import io
import wave

from fastapi.testclient import TestClient
import pytest

from agent_service.contracts import digest
from video_workbench.app import create_app
from video_workbench.config import MotionSettings
from video_workbench.animation_method_production import MethodProductionAdapter
from test_creative_method_contract import production_result


FIELDS = dict(title='任务进入队列', narration='先放入队列。再交给工作节点。',
              visual_description='让同一个请求沿轨迹移动到队列和工作节点。',
              duration_seconds=4, format='svg')


def pcm(value=1):
    target = io.BytesIO()
    with wave.open(target, 'wb') as stream:
        stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        stream.writeframes(value.to_bytes(2, 'little', signed=True) * 24000 * 4)
    return target.getvalue()


class CreativeGateway:
    def __init__(self):
        self.uploads = self.posts = self.collects = 0
        self.document = None

    def upload(self, document):
        self.uploads += 1
        return dict(sha256=digest(document))

    def submit(self, operation_id, document, deadline_at, input_ref):
        self.posts += 1
        self.document = deepcopy(document)
        return dict(job_id='creative-job')

    def poll(self, job_id):
        return dict(state='completed')

    def collect(self, job_id, input_sha256, document):
        self.collects += 1
        assert document == self.document
        files = {'scene.html': '<!doctype html><html><body><svg viewBox="0 0 1280 720">'
                 '<circle cx="100" cy="100" r="24"/></svg><script>'
                 'window.__animationExperiment={seek(t){document.body.dataset.t=String(t)}};'
                 '</script></body></html>'}
        value = production_result(document, source_html=files['scene.html'])
        value['source']['strategy']['description'] = '请求连续移动到处理节点'
        return value


def app_for(path, gateway=None, **settings):
    from pathlib import Path
    method_adapter = settings.pop('method_adapter', MethodProductionAdapter(
        lambda owner: [], lambda *args: None,
        ledger_path=Path(path).with_name('method-freezes.sqlite3')))
    return create_app(db_path=path, trusted_hosts={'testserver'}, background=False,
                      motion_enabled=settings.pop('motion_enabled', True),
                      creative_enabled=settings.pop('creative_enabled', True),
                      creative_gateway=gateway, method_adapter=method_adapter, skill_production=False, **settings)


def login(client, owner='alice'):
    client.headers['X-Forwarded-User'] = owner
    client.headers['X-CSRF-Token'] = client.get('/api/session').json()['csrf_token']


def scene_for(client, app, *, audio=True):
    project = client.post('/api/projects', json=dict(title='正式动画', script=FIELDS['narration'],
                                                   style='清晰图解')).json()
    project = app.state.store.save_scene('alice', project['id'], FIELDS)
    scene_id = project['scenes'][0]['id']
    if audio:
        project = app.state.store.upload_audio('alice', scene_id,
            dict(expected_revision=project['revision']), 'creative-audio-upload', pcm())
        audio_id = project['scenes'][0]['audio_versions'][0]['id']
        project = app.state.store.adopt_audio('alice', scene_id, audio_id, None)
    return project, scene_id


def generate(client, project, scene_id, key='creative-create'):
    scene = next(row for row in project['scenes'] if row['id'] == scene_id)
    return client.post(f'/api/scenes/{scene_id}/motion', headers={'Idempotency-Key': key},
        json=dict(expected_revision=project['revision'], diagram_kind='creative', instruction='保持对象连续移动',
                  expected_audio_version_id=scene.get('head_audio_version_id')))


def test_creative_environment_settings_are_opt_in_and_versioned_separately():
    baseline = MotionSettings.from_env({})
    assert baseline.creative_enabled is False and baseline.creative_revision == '1'
    settings = MotionSettings.from_env({'VIDEO_CREATIVE_ENABLED': '1',
                                       'VIDEO_CREATIVE_PROFILE_REVISION': 'review-2'})
    assert settings.creative_enabled is True and settings.creative_revision == 'review-2'
    assert settings.enabled is False and settings.process_enabled is False


@pytest.mark.parametrize('values', [
    {'VIDEO_CREATIVE_ENABLED': 'true'}, {'VIDEO_CREATIVE_ENABLED': ''},
    {'VIDEO_CREATIVE_ENABLED': 1}, {'VIDEO_CREATIVE_PROFILE_REVISION': 'bad revision'},
    {'VIDEO_CREATIVE_PROFILE_REVISION': ''},
])
def test_invalid_creative_environment_settings_fail_before_start(values):
    with pytest.raises(ValueError):
        MotionSettings.from_env(values)


@pytest.mark.parametrize('value', [0, 1, 'true', 'false', {}, []])
def test_programmatic_creative_gate_requires_a_boolean(tmp_path, value):
    with pytest.raises(ValueError, match='boolean creative'):
        app_for(tmp_path / 'state.db', creative_enabled=value)
    assert not (tmp_path / 'state.db').exists()


@pytest.mark.parametrize('motion,creative,configured,expected', [
    (True, True, True, True), (True, False, True, False),
    (False, True, True, False), (True, True, False, False),
])
def test_session_distinguishes_creative_gate_and_configured_service(tmp_path, motion, creative, configured, expected):
    gateway = CreativeGateway() if configured else None
    app = app_for(tmp_path / 'state.db', gateway, motion_enabled=motion, creative_enabled=creative)
    with TestClient(app) as client:
        login(client)
        session = client.get('/api/session').json()
        assert session['creative_enabled'] is (motion and creative)
        assert session['creative_configured'] is expected
        assert 'document' not in session
        if gateway:
            assert (gateway.uploads, gateway.posts, gateway.collects) == (0, 0, 0)


def test_api_creative_kind_freezes_the_released_strategy_and_current_recording(tmp_path):
    gateway = CreativeGateway()
    app = app_for(tmp_path / 'state.db', gateway)
    with TestClient(app) as client:
        login(client)
        project, scene_id = scene_for(client, app)
        response = generate(client, project, scene_id)
        assert response.status_code == 202, response.text
        assert response.json()['kind'] == 'creative-scene'
        replay = generate(client, project, scene_id)
        assert replay.json() == response.json()
        assert (gateway.uploads, gateway.posts, gateway.collects) == (0, 0, 0)
        operation = app.state.motion_store.operation(response.json()['id'], 'alice')
        assert operation['document']['schema'] == 'video.animation-production-pending.v1'
        context = operation['document']['core_task']['scene']
        assert context['narration'] == FIELDS['narration']
        assert context['audio_sha256'] == project['scenes'][0]['audio_versions'][0]['sha256']
        app.state.motion_workflow.tick()
        view = client.get(f'/api/projects/{project["id"]}/motion').json()
        candidate = view['scenes'][0]['motion']['versions'][0]
        assert candidate['diagram_kind'] == 'creative'
        assert view['scenes'][0]['motion']['head_version_id'] is None
        result = client.get(f'/api/motion-versions/{candidate["id"]}').json()
        assert result['plan']['schema'] == 'video.creative-scene.v2'
        assert result['plan']['profile']['name'] == 'video.animation-production.v1'
        assert result['plan']['input_document']['method_delivery']['methods'] == []
        assert result['plan']['result']['source']['strategy']['description'] == '请求连续移动到处理节点'
        assert result['audio_version_id'] == project['scenes'][0]['head_audio_version_id']
        assert (gateway.uploads, gateway.posts, gateway.collects) == (1, 1, 1)


def test_missing_current_recording_rejects_creative_before_any_submission(tmp_path):
    gateway = CreativeGateway()
    app = app_for(tmp_path / 'state.db', gateway)
    with TestClient(app) as client:
        login(client)
        project, scene_id = scene_for(client, app, audio=False)
        response = generate(client, project, scene_id)
        assert response.status_code == 409, response.text
        assert response.json()['detail']['code'] == 'missing_current_audio'
        assert app.state.motion_store.active_operations() == []
        assert (gateway.uploads, gateway.posts, gateway.collects) == (0, 0, 0)


def test_api_recording_precondition_prevents_a_stale_tab_from_using_a_replaced_voice(tmp_path):
    gateway = CreativeGateway()
    app = app_for(tmp_path / 'state.db', gateway)
    with TestClient(app) as client:
        login(client)
        project, scene_id = scene_for(client, app)
        old = client.get(f'/api/projects/{project["id"]}/motion').json()
        old_scene = old['scenes'][0]
        updated = app.state.store.upload_audio('alice', scene_id,
            dict(expected_revision=project['revision']), 'concurrent-creative-audio', pcm(2))
        new_id = updated['scenes'][0]['audio_versions'][-1]['id']
        app.state.store.adopt_audio('alice', scene_id, new_id, old_scene['head_audio_version_id'])
        response = client.post(f'/api/scenes/{scene_id}/motion',
            headers={'Idempotency-Key': 'stale-voice-precondition'},
            json=dict(expected_revision=old['revision'], diagram_kind='creative',
                      expected_semantic_binding_sha256=old_scene['motion']['semantic_binding_sha256'],
                      expected_audio_version_id=old_scene['head_audio_version_id']))
        assert response.status_code == 409, response.text
        assert response.json()['detail']['code'] == 'creative_audio_binding_mismatch'
        assert app.state.motion_store.active_operations() == []
        assert (gateway.uploads, gateway.posts, gateway.collects) == (0, 0, 0)


def test_creative_timing_is_read_only_until_explicit_review_and_audio_changes_stale_the_candidate(tmp_path):
    gateway = CreativeGateway()
    app = app_for(tmp_path / 'state.db', gateway)
    with TestClient(app) as client:
        login(client)
        project, scene_id = scene_for(client, app)
        assert generate(client, project, scene_id).status_code == 202
        app.state.motion_workflow.tick()
        project = client.get(f'/api/projects/{project["id"]}/motion').json()
        version_id = project['scenes'][0]['motion']['versions'][0]['id']
        audio_id = project['scenes'][0]['head_audio_version_id']
        path = f'/api/motion-versions/{version_id}/timing'
        response = client.get(path, params=dict(audio_version_id=audio_id))
        assert response.status_code == 200, response.text
        timing = response.json()
        assert timing['requires_review'] is True
        request = dict(expected_revision=project['revision'], audio_version_id=audio_id,
                       expected_alignment_sha256=timing['alignment_sha256'])
        response = client.patch(path, json=dict(request, segments=timing['alignment']['segments'],
                                               correction_reason='试图改写已生成动画时序'))
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'creative_timing_requires_regeneration'
        verified = client.post(path + '/verify', json=request)
        assert verified.status_code == 200, verified.text
        assert verified.json()['requires_review'] is False
        project = client.get(f'/api/projects/{project["id"]}/motion').json()
        adopted = client.post(f'/api/scenes/{scene_id}/motion/adopt', json=dict(
            expected_revision=project['revision'], version_id=version_id, expected_head_version_id=None))
        assert adopted.status_code == 200, adopted.text
        project = adopted.json()
        assert project['scenes'][0]['motion']['head_stale'] is False
        original = client.get(f'/api/motion-versions/{version_id}').json()['plan']
        project = app.state.store.upload_audio('alice', scene_id,
            dict(expected_revision=project['revision']), 'changed-creative-audio', pcm(2))
        current = project['scenes'][0]['audio_versions'][-1]['id']
        app.state.store.adopt_audio('alice', scene_id, current, audio_id)
        project = client.get(f'/api/projects/{project["id"]}/motion').json()
        assert project['scenes'][0]['motion']['head_stale'] is True
        candidate = client.get(f'/api/motion-versions/{version_id}').json()
        assert candidate['stale'] is True and candidate['plan'] == original
        assert (gateway.uploads, gateway.posts, gateway.collects) == (1, 1, 1)


def test_disabling_new_creative_generation_preserves_readable_history(tmp_path):
    gateway = CreativeGateway()
    path = tmp_path / 'state.db'
    app = app_for(path, gateway)
    with TestClient(app) as client:
        login(client)
        project, scene_id = scene_for(client, app)
        assert generate(client, project, scene_id).status_code == 202
        app.state.motion_workflow.tick()
        project = client.get(f'/api/projects/{project["id"]}/motion').json()
        version_id = project['scenes'][0]['motion']['versions'][0]['id']
        original = client.get(f'/api/motion-versions/{version_id}').json()
    disabled = app_for(path, gateway, creative_enabled=False)
    with TestClient(disabled) as client:
        login(client)
        assert client.get('/api/session').json()['creative_configured'] is False
        assert client.get(f'/api/motion-versions/{version_id}').json() == original
        assert client.get(f'/api/projects/{project["id"]}/motion').status_code == 200
        response = generate(client, project, scene_id, key='disabled-new-creative')
        assert response.status_code == 503
        assert response.json()['detail']['code'] == 'creative_disabled'
        assert (gateway.uploads, gateway.posts, gateway.collects) == (1, 1, 1)


def test_creative_routes_preserve_owner_csrf_and_origin_boundaries(tmp_path):
    gateway = CreativeGateway()
    app = app_for(tmp_path / 'state.db', gateway)
    with TestClient(app) as client:
        login(client)
        project, scene_id = scene_for(client, app)
        assert generate(client, project, scene_id).status_code == 202
        app.state.motion_workflow.tick()
        project = client.get(f'/api/projects/{project["id"]}/motion').json()
        version_id = project['scenes'][0]['motion']['versions'][0]['id']
        alice_token = client.headers['X-CSRF-Token']
        login(client, 'bob')
        for path in (f'/api/projects/{project["id"]}/motion', f'/api/motion-versions/{version_id}'):
            assert client.get(path).status_code == 404
        response = generate(client, project, scene_id, key='foreign-creative')
        assert response.status_code == 404
        client.headers['X-CSRF-Token'] = alice_token
        assert generate(client, project, scene_id, key='foreign-csrf-creative').status_code == 403
        login(client)
        response = client.post(f'/api/scenes/{scene_id}/motion',
            headers={'Idempotency-Key': 'foreign-origin-creative', 'Origin': 'https://attacker.example'},
            json=dict(expected_revision=project['revision'], diagram_kind='creative'))
        assert response.status_code == 403
        client.headers.pop('X-Forwarded-User')
        assert client.get(f'/api/motion-versions/{version_id}').status_code == 401
        assert (gateway.uploads, gateway.posts, gateway.collects) == (1, 1, 1)
