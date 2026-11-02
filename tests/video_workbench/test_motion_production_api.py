"""Mixed production integration with real SQLite/WAV and synthetic Agent/media I/O."""
from copy import deepcopy
import io
import json
import wave

import pytest
from fastapi.testclient import TestClient

from agent_service.contracts import ServiceError, digest
from video_workbench.app import create_app
from video_workbench.config import MotionSettings
from video_workbench.motion import example_plan
from video_workbench.motion_snapshot import SCHEMA, verify_snapshot_v2
from video_workbench.production import Production
from video_workbench import production as production_module


RUNTIME = dict(schema='video.motion-runtime.v1', hyperframes='0.8.149',
    hyperframes_source='2905162d925fc87fb8242e24ed0bbf025d307ea1', node='v22.20.0',
    browser='HeadlessChrome/141.0.7390.54', ffmpeg='ffmpeg version 5.1.7-0+deb12u1', gsap='3.14.2',
    gsap_sha256='a' * 64, font_sha256='f' * 64, dpkg_sha256='d' * 64,
    npm_lock_sha256='e' * 64, image_digest='sha256:' + '1' * 64)
SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><rect width="1280" height="720" fill="blue"/></svg>'
FIELDS = dict(title='请求流程', narration='请求到达。进入队列。执行节点开始工作。', visual_description='请求进入队列后执行。',
              duration_seconds=2, format='svg')


def pcm(value=1, seconds=3):
    output = io.BytesIO()
    with wave.open(output, 'wb') as stream:
        stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        stream.writeframes(int(value).to_bytes(2, 'little', signed=True) * int(24000 * seconds))
    return output.getvalue()


class Gateway:
    def __init__(self):
        self.uploads = self.posts = self.lookups = self.cancels = 0
        self.document, self.state = None, 'completed'

    def upload(self, document):
        self.uploads += 1
        return dict(sha256=digest(document))

    def submit(self, operation_id, document, deadline_at, input_ref):
        self.posts += 1
        self.document = document
        return dict(job_id='motion-job')

    def lookup(self, operation_id):
        self.lookups += 1
        return dict(job_id='motion-job')

    def poll(self, job_id):
        return dict(state=self.state)

    def collect(self, job_id, input_sha256, beats):
        return dict(schema='video.motion-result.v1', input_sha256=input_sha256, plan=example_plan(beats))

    def cancel(self, job_id):
        self.cancels += 1
        self.state = 'cancelled'


class Renderer:
    def __init__(self):
        self.calls = []
        self.fail = None
        self.cancelled = []

    def render(self, snapshot, directory, *, operation_id, owner, cancel_event):
        verify_snapshot_v2(snapshot)
        self.calls.append(dict(snapshot=deepcopy(snapshot), operation_id=operation_id, owner=owner))
        if self.fail:
            raise self.fail
        if cancel_event.is_set():
            self.cancelled.append(operation_id)
            raise ServiceError('render_cancelled', 409)
        path = directory / 'video.mp4'
        path.write_bytes(b'synthetic-verified-render')
        return dict(path=path, duration_seconds=snapshot['duration_seconds'])


@pytest.fixture
def env(tmp_path):
    gateway, renderer = Gateway(), Renderer()
    app = create_app(db_path=tmp_path / 'state.db', trusted_hosts={'testserver'}, background=False,
                     motion_enabled=True, motion_gateway=gateway, motion_renderer=renderer,
                     motion_runtime=RUNTIME, render_enabled=True)
    with TestClient(app) as client:
        client.headers['X-Forwarded-User'] = 'alice'
        client.headers['X-CSRF-Token'] = client.get('/api/session').json()['csrf_token']
        yield client, app, gateway, renderer


def setup(env):
    client, app, *_ = env
    project = client.post('/api/projects', json=dict(title='混合视频', script=FIELDS['narration'], style='清晰图解')).json()
    store = app.state.store
    for i in range(2):
        project = store.save_scene('alice', project['id'], dict(FIELDS, title=f'第{i + 1}镜'))
        sid = project['scenes'][-1]['id']
        op = store.start('alice', project['id'], 'scene', dict(expected_revision=project['revision']), f'visual-{i}', scene_id=sid)
        store.complete(op['id'], dict(source=SVG, format='svg', summary='已有静态镜头'))
        project = store.project('alice', project['id'])
        project = store.adopt('alice', sid, project['scenes'][-1]['versions'][0]['id'], None)
        project = store.upload_audio('alice', sid, dict(expected_revision=project['revision']), f'upload-{i}-audio', pcm())
        project = store.adopt_audio('alice', sid, project['scenes'][-1]['audio_versions'][0]['id'], None)
    sid = project['scenes'][1]['id']
    response = client.post(f'/api/scenes/{sid}/motion', json=dict(expected_revision=project['revision']),
                           headers={'Idempotency-Key': 'motion-plan-1'})
    assert response.status_code == 202, response.text
    app.state.motion_workflow.tick()
    project = client.get(f'/api/projects/{project["id"]}').json()
    version_id = project['scenes'][1]['motion']['versions'][0]['id']
    audio_id = project['scenes'][1]['head_audio_version_id']
    return project, sid, version_id, audio_id


def timing(env, version_id, audio_id):
    response = env[0].get(f'/api/motion-versions/{version_id}/timing', params=dict(audio_version_id=audio_id))
    assert response.status_code == 200, response.text
    return response.json()


def review(env, version_id, audio_id):
    current = timing(env, version_id, audio_id)
    response = env[0].post(f'/api/motion-versions/{version_id}/timing/verify', json=dict(
        expected_revision=current['project_revision'], audio_version_id=audio_id,
        expected_alignment_sha256=current['alignment_sha256']))
    assert response.status_code == 200, response.text
    return response.json()


def adopt(env, project, sid, version_id):
    response = env[0].post(f'/api/scenes/{sid}/motion/adopt', json=dict(expected_revision=project['revision'],
                            version_id=version_id, expected_head_version_id=None))
    assert response.status_code == 200, response.text
    return response.json()


def draft_request(project, sid, version_id, audio_id):
    return dict(expected_revision=project['revision'], purpose='draft', subtitles=False,
                scene_id=sid, motion_version_id=version_id, audio_version_id=audio_id)


def test_http_generation_replay_uses_one_agent_submit_and_keeps_candidate_unadopted(env):
    client, app, gateway, _ = env
    project, sid, version_id, aid = setup(env)
    op = app.state.motion_store.operation(project['scenes'][1]['motion']['operation']['id'])
    response = client.post(f'/api/scenes/{sid}/motion', json=dict(expected_revision=op['document'] and project['revision'] - 2),
                           headers={'Idempotency-Key': 'motion-plan-1'})
    assert response.status_code == 202, response.text
    app.state.motion_workflow.tick()
    assert gateway.posts == gateway.uploads == 1
    assert project['scenes'][1]['motion']['head_version_id'] is None
    assert project['scenes'][1]['motion']['mode'] == 'static'
    assert client.get(f'/api/motion-versions/{version_id}').json()['plan']


def test_estimate_is_bound_to_actual_wav_and_saving_does_not_imply_review(env):
    project, sid, version_id, audio_id = setup(env)
    current = timing(env, version_id, audio_id)
    assert current['alignment']['method'] == 'estimated' and current['requires_review']
    assert current['alignment']['audio_seconds'] == 3
    assert current['alignment']['segments'][-1]['end'] == 3
    assert current['alignment']['reviewed_by'] is None
    assert env[1].state.store.project('alice', project['id'])['revision'] == project['revision']
    anchors = deepcopy(current['alignment']['segments'])
    anchors[0]['end'] -= .05
    response = env[0].patch(f'/api/motion-versions/{version_id}/timing', json=dict(
        expected_revision=current['project_revision'], audio_version_id=audio_id,
        expected_alignment_sha256=current['alignment_sha256'], segments=anchors))
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved['alignment']['method'] == 'manual_anchors' and saved['requires_review']
    verified = review(env, version_id, audio_id)
    assert verified['alignment']['review_status'] == 'verified'
    assert verified['alignment']['reviewed_by'] == 'alice' and verified['alignment']['reviewed_at']
    assert verified['alignment']['corrections'] == saved['alignment']['corrections']
    assert verified['alignment']['provenance'] == saved['alignment']['provenance']
    assert not verified['requires_review']


def test_timing_cas_revision_bad_anchors_and_user_review_claims_are_rejected(env):
    project, sid, vid, aid = setup(env)
    current = timing(env, vid, aid)
    body = dict(expected_revision=project['revision'], audio_version_id=aid,
                expected_alignment_sha256='0' * 64, segments=current['alignment']['segments'])
    endpoint = f'/api/motion-versions/{vid}/timing'
    assert env[0].patch(endpoint, json=body).status_code == 409
    body['expected_alignment_sha256'] = current['alignment_sha256']
    body['expected_revision'] = 1
    assert env[0].patch(endpoint, json=body).status_code == 409
    body['expected_revision'] = project['revision']
    body['segments'][0]['end'] = 4
    assert env[0].patch(endpoint, json=body).status_code == 422
    body['reviewed_by'] = 'invented-reviewer'
    assert env[0].patch(endpoint, json=body).status_code == 422
    assert env[1].state.store.project('alice', project['id'])['revision'] == project['revision']


def test_draft_previews_exact_unadopted_motion_candidate_without_changing_heads(env):
    client, app, _, renderer = env
    project, sid, vid, aid = setup(env)
    body = draft_request(project, sid, vid, aid)
    endpoint = f'/api/projects/{project["id"]}/motion-renders'
    response = client.post(endpoint, json=body, headers={'Idempotency-Key': 'draft-preview-1'})
    assert response.status_code == 202, response.text
    job = response.json()
    assert job['purpose'] == job['quality'] == 'draft' and job['schema'] == SCHEMA
    assert client.post(endpoint, json=body, headers={'Idempotency-Key': 'draft-preview-1'}).json()['id'] == job['id']
    app.state.production.tick()
    assert len(renderer.calls) == 1 and renderer.calls[0]['operation_id'] == job['id']
    assert renderer.calls[0]['owner'] == 'alice'
    snapshot = renderer.calls[0]['snapshot']
    assert len(snapshot['scenes']) == 1 and snapshot['scenes'][0]['motion']['id'] == vid
    assert snapshot['scenes'][0]['motion']['alignment']['review_status'] == 'pending'
    assert client.get(f'/api/renders/{job["id"]}/video').content == b'synthetic-verified-render'
    after = app.state.motion_store.project('alice', project['id'])
    assert after['scenes'][1]['motion']['head_version_id'] is None
    assert after['scenes'][1]['head_audio_version_id'] == aid


def test_delivery_mixes_static_and_motion_and_refuses_unreviewed_alignment(env):
    client, app, _, renderer = env
    project, sid, vid, aid = setup(env)
    project = adopt(env, project, sid, vid)
    body = dict(expected_revision=project['revision'], purpose='delivery', subtitles=False)
    endpoint = f'/api/projects/{project["id"]}/motion-renders'
    response = client.post(endpoint, json=body, headers={'Idempotency-Key': 'delivery-before-review'})
    assert response.status_code == 409 and response.json()['detail']['code'] == 'motion_timing_needs_review'
    checked = review(env, vid, aid)
    body['expected_revision'] = checked['project_revision']
    response = client.post(endpoint, json=body, headers={'Idempotency-Key': 'delivery-after-review'})
    assert response.status_code == 202, response.text
    assert response.json()['purpose'] == 'delivery' and response.json()['quality'] == 'final'
    app.state.production.tick()
    snapshot = renderer.calls[0]['snapshot']
    assert [s['visual_mode'] for s in snapshot['scenes']] == ['static', 'motion']
    assert snapshot['scenes'][1]['motion']['alignment']['review_status'] == 'verified'
    assert snapshot['scenes'][0]['audio']['id'] == project['scenes'][0]['head_audio_version_id']
    assert snapshot['scenes'][1]['audio']['id'] == aid


def test_pronunciation_change_reuses_semantic_plan_but_requires_new_audio_timing(env):
    project, sid, vid, aid = setup(env)
    review(env, vid, aid)
    store = env[1].state.store
    project = store.project('alice', project['id'])
    project = store.save_speech_settings('alice', sid, dict(expected_revision=project['revision'],
                                                        pronunciation={'队列': '队 列'}))
    assert not env[1].state.motion_store.version('alice', vid)['stale']
    old = env[0].get(f'/api/motion-versions/{vid}/timing', params=dict(audio_version_id=aid))
    assert old.status_code == 409 and old.json()['detail']['code'] == 'audio_stale'
    project = store.upload_audio('alice', sid, dict(expected_revision=project['revision']), 'new-pronunciation-audio', pcm(2))
    new_aid = project['scenes'][1]['audio_versions'][-1]['id']
    new = timing(env, vid, new_aid)
    assert '队 列' in new['beats']['spoken_text'] and new['requires_review']
    assert new['alignment']['review_status'] == 'pending'


def test_owner_csrf_candidate_cross_scene_and_revision_boundaries(env):
    client, app, *_ = env
    project, sid, vid, aid = setup(env)
    current = timing(env, vid, aid)
    endpoint = f'/api/motion-versions/{vid}/timing'
    body = dict(expected_revision=project['revision'], audio_version_id=aid,
                expected_alignment_sha256=current['alignment_sha256'])
    assert client.post(endpoint + '/verify', json=body, headers={'X-CSRF-Token': 'wrong'}).status_code == 403
    first_audio = project['scenes'][0]['head_audio_version_id']
    assert client.get(endpoint, params=dict(audio_version_id=first_audio)).status_code == 404
    client.headers['X-Forwarded-User'] = 'bob'
    client.headers['X-CSRF-Token'] = client.get('/api/session').json()['csrf_token']
    for url in [f'/api/projects/{project["id"]}/motion', f'/api/motion-versions/{vid}', endpoint + '?audio_version_id=' + aid]:
        assert client.get(url).status_code == 404
    assert client.post(endpoint + '/verify', json=body).status_code == 404


@pytest.mark.parametrize('change', ['partial', 'delivery_selection', 'paths', 'quality'])
def test_render_input_cannot_select_unsafe_or_ambiguous_execution_parameters(env, change):
    project, sid, vid, aid = setup(env)
    body = draft_request(project, sid, vid, aid)
    if change == 'partial':
        body.pop('audio_version_id')
    elif change == 'delivery_selection':
        body['purpose'] = 'delivery'
    else:
        body[change] = '/etc/passwd' if change == 'paths' else 'unbounded'
    response = env[0].post(f'/api/projects/{project["id"]}/motion-renders', json=body,
                           headers={'Idempotency-Key': 'invalid-motion-render'})
    assert response.status_code == 422
    assert env[1].state.production.renders('alice', project['id']) == []


def test_remote_outage_recovery_and_disabled_gate_cancel_keep_same_render_identity(env):
    client, app, _, renderer = env
    project, sid, vid, aid = setup(env)
    body = draft_request(project, sid, vid, aid)
    response = client.post(f'/api/projects/{project["id"]}/motion-renders', json=body,
                           headers={'Idempotency-Key': 'recover-motion-render'})
    job = response.json()
    renderer.fail = ServiceError('motion_render_service_unavailable', 503)
    app.state.production.tick()
    pending = app.state.production.renders('alice', project['id'])[0]
    assert pending['status'] == 'queued' and pending['render_attempted']
    app.state.production.motion_enabled = False
    app.state.production.cancel('alice', job['id'])
    renderer.fail = None
    app.state.production.tick()
    assert [call['operation_id'] for call in renderer.calls] == [job['id'], job['id']]
    assert renderer.cancelled == [job['id']]
    assert app.state.production.renders('alice', project['id'])[0]['status'] == 'cancelled'


def test_closed_gate_history_reads_and_retraction_remain_available_after_restart(env):
    client, app, gateway, renderer = env
    project, sid, vid, aid = setup(env)
    project = adopt(env, project, sid, vid)
    disabled = create_app(db_path=app.state.store.path, trusted_hosts={'testserver'}, background=False,
                          motion_enabled=False, motion_gateway=gateway, motion_renderer=renderer, motion_runtime=RUNTIME)
    with TestClient(disabled) as other:
        other.headers['X-Forwarded-User'] = 'alice'
        other.headers['X-CSRF-Token'] = other.get('/api/session').json()['csrf_token']
        assert other.get(f'/api/projects/{project["id"]}/motion').status_code == 200
        assert other.get(f'/api/motion-versions/{vid}/timing', params=dict(audio_version_id=aid)).status_code == 200
        assert other.post(f'/api/scenes/{sid}/motion', json=dict(expected_revision=project['revision']),
                          headers={'Idempotency-Key': 'disabled-motion-plan'}).status_code == 503
        assert other.post(f'/api/projects/{project["id"]}/motion-renders', json=dict(expected_revision=project['revision']),
                          headers={'Idempotency-Key': 'disabled-motion-render'}).status_code == 503
        response = other.post(f'/api/scenes/{sid}/visual-mode', json=dict(expected_revision=project['revision'], mode='static'))
        assert response.status_code == 200, response.text
        assert response.json()['scenes'][1]['motion']['mode'] == 'static'


def test_motion_configuration_is_opt_in_and_reads_bounded_operator_runtime(tmp_path):
    assert MotionSettings.from_env({}) == MotionSettings(False, '1', None)
    runtime = tmp_path / 'runtime.json'
    runtime.write_text(json.dumps(RUNTIME), encoding='utf-8')
    settings = MotionSettings.from_env(dict(VIDEO_MOTION_ENABLED='1', VIDEO_MOTION_PROFILE_REVISION='reviewed-2',
                                           VIDEO_MOTION_RUNTIME_FILE=str(runtime)))
    assert settings.enabled and settings.revision == 'reviewed-2'
    assert settings.runtime_fingerprint == RUNTIME
    runtime.write_text('x' * 16385)
    with pytest.raises(ValueError, match='runtime fingerprint'):
        MotionSettings.from_env(dict(VIDEO_MOTION_RUNTIME_FILE=str(runtime)))
    with pytest.raises(ValueError):
        MotionSettings.from_env(dict(VIDEO_MOTION_ENABLED='yes'))


def test_restart_restores_same_inflight_motion_identity_even_after_gate_is_closed(env):
    client, app, _, renderer = env
    project, sid, vid, aid = setup(env)
    response = client.post(f'/api/projects/{project["id"]}/motion-renders',
        json=draft_request(project, sid, vid, aid), headers={'Idempotency-Key': 'restart-motion-render'})
    job = response.json()
    with app.state.store.transaction() as db:
        pending = json.loads(db.execute('SELECT document FROM video_renders WHERE id=?', (job['id'],)).fetchone()[0])
        pending.update(status='running', render_attempted=True)
        db.execute('UPDATE video_renders SET document=? WHERE id=?', (json.dumps(pending), job['id']))
    recovered = Production(app.state.store, enabled=False, motion_enabled=False, motion_renderer=renderer,
                           motion_store=app.state.motion_store, motion_timing=app.state.motion_timing,
                           runtime_fingerprint=RUNTIME)
    recovered._recover()
    recovered.tick()
    assert len(renderer.calls) == 1 and renderer.calls[0]['operation_id'] == job['id']
    assert recovered.renders('alice', project['id'])[0]['status'] == 'completed'


def test_closed_motion_gate_still_cancels_admitted_agent_job_without_new_submission(env):
    client, app, gateway, _ = env
    project, sid, vid, aid = setup(env)
    gateway.state = 'running'
    op = app.state.motion_store.start('alice', sid, dict(expected_revision=project['revision']), 'pending-motion-plan')
    app.state.motion_workflow.tick()
    assert gateway.posts == 2
    app.state.motion_workflow.enabled = False
    app.state.motion_store.cancel('alice', op['id'])
    app.state.motion_workflow.tick()
    app.state.motion_workflow.tick()
    assert gateway.cancels == 1 and gateway.posts == 2
    assert app.state.motion_store.operation(op['id'])['status'] == 'cancelled'


def test_closed_gate_abandons_unsubmitted_agent_queue_without_starting_remote_work(env):
    client, app, gateway, _ = env
    project, sid, vid, aid = setup(env)
    op = app.state.motion_store.start('alice', sid, dict(expected_revision=project['revision']), 'unsubmitted-motion-plan')
    app.state.motion_workflow.enabled = False
    app.state.motion_workflow.start()
    assert app.state.motion_workflow.thread is None
    assert app.state.motion_store.operation(op['id'])['status'] == 'cancelled'
    assert gateway.posts == gateway.uploads == 1


@pytest.mark.parametrize('budget', ['seconds', 'audio_bytes'])
def test_motion_render_execution_limits_reject_before_queueing(env, monkeypatch, budget):
    client, app, *_ = env
    project, sid, vid, aid = setup(env)
    if budget == 'seconds':
        store = app.state.store
        project = store.upload_audio('alice', sid, dict(expected_revision=project['revision']), 'long-audio-fixture', pcm(seconds=65))
        aid = project['scenes'][1]['audio_versions'][-1]['id']
    else:
        monkeypatch.setattr(production_module, 'MAX_MOTION_AUDIO_BYTES', 100)
    response = client.post(f'/api/projects/{project["id"]}/motion-renders',
        json=draft_request(project, sid, vid, aid), headers={'Idempotency-Key': 'bounded-motion-render'})
    assert response.status_code == (422 if budget == 'seconds' else 413)
    assert response.json()['detail']['code'] == ('motion_scene_duration_exceeded' if budget == 'seconds' else 'motion_audio_size_exceeded')
    assert app.state.production.renders('alice', project['id']) == []
