"""Batch HTTP boundaries with real SQLite and synthetic Agent/TTS/media I/O.

These tests never call a model, speech provider, Kubernetes or FFmpeg. Synthetic
media proves routing and frozen identities; it does not prove listening quality.
"""
from copy import deepcopy
import hashlib
import io
from types import SimpleNamespace
import wave

import pytest
from fastapi.testclient import TestClient

from agent_service.contracts import digest
from video_workbench.app import create_app
from video_workbench.motion import example_plan
from video_workbench.motion_snapshot import verify_snapshot_v2
from video_workbench.speech import SpeechAudio, SpeechSettings, inspect_wav


OWNER = 'alice'
RUNTIME = dict(schema='video.motion-runtime.v1', hyperframes='0.8.149',
    hyperframes_source='2905162d925fc87fb8242e24ed0bbf025d307ea1', node='v22.20.0',
    browser='HeadlessChrome/141.0.7390.54', ffmpeg='ffmpeg version 5.1.7-0+deb12u1', gsap='3.14.2',
    gsap_sha256='a' * 64, font_sha256='f' * 64, dpkg_sha256='d' * 64,
    npm_lock_sha256='e' * 64, image_digest='sha256:' + '1' * 64)


def pcm(seconds=3):
    output = io.BytesIO()
    with wave.open(output, 'wb') as stream:
        stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        stream.writeframes(b'\x04\x08' * int(24000 * seconds))
    return output.getvalue()


class SyntheticGateway:
    def __init__(self):
        self.uploads, self.posts, self.lookups, self.cancels = [], [], [], []
        self.documents, self.jobs = {}, {}
        self.fail_titles = set()

    def upload(self, document):
        self.uploads.append(deepcopy(document))
        return dict(sha256=digest(document))

    def submit(self, operation_id, document, deadline_at, input_ref):
        self.posts.append(operation_id)
        job_id = 'synthetic-' + operation_id
        self.documents[job_id] = deepcopy(document)
        self.jobs[operation_id] = job_id
        return dict(job_id=job_id)

    def lookup(self, operation_id):
        self.lookups.append(operation_id)
        return dict(job_id=self.jobs[operation_id])

    def poll(self, job_id):
        return dict(state='failed' if self.documents[job_id]['title'] in self.fail_titles else 'completed')

    def collect(self, job_id, input_sha256, beats):
        return dict(schema='video.motion-result.v1', input_sha256=input_sha256, plan=example_plan(beats))

    def cancel(self, job_id):
        self.cancels.append(job_id)


class SyntheticSpeech:
    def __init__(self):
        self.settings = SpeechSettings('http://speech.invalid/v1', 'kokoro-rk3588', voice='zm_yunjian')
        self.requests = []

    @property
    def voice_catalog(self):
        return [dict(id='zm_yunjian', name='云健', default=True)]

    def synthesize(self, text, *, voice, speed, cancel_event):
        self.requests.append(dict(text=text, voice=voice, speed=speed))
        data = pcm()
        return SpeechAudio(**vars(inspect_wav(data)), data=data, sha256=hashlib.sha256(data).hexdigest(),
            input_sha256=digest(dict(model=self.settings.model, input=text, voice=voice,
                                    speed=speed, response_format='wav')))


class SyntheticRenderer:
    def __init__(self):
        self.calls = []

    def render(self, snapshot, directory, *, operation_id, owner, cancel_event):
        verify_snapshot_v2(snapshot)
        assert not cancel_event.is_set()
        self.calls.append(dict(snapshot=deepcopy(snapshot), operation_id=operation_id, owner=owner))
        path = directory / 'synthetic.mp4'
        path.write_bytes(b'fixture-media-routing-only')
        return dict(path=path, duration_seconds=snapshot['duration_seconds'])


@pytest.fixture
def env(tmp_path):
    gateway, speech, renderer = SyntheticGateway(), SyntheticSpeech(), SyntheticRenderer()
    app = create_app(db_path=tmp_path / 'state.sqlite', trusted_hosts={'testserver'}, background=False,
        motion_enabled=True, explanation_enabled=False, motion_gateway=gateway,
        motion_renderer=renderer, motion_runtime=RUNTIME, speech_client=speech, render_enabled=True)
    with TestClient(app) as client:
        client.headers['X-Forwarded-User'] = OWNER
        client.headers['X-CSRF-Token'] = client.get('/api/session').json()['csrf_token']
        yield SimpleNamespace(client=client, app=app, gateway=gateway, speech=speech, renderer=renderer)


def new_project(env, *, mode='motion', scenes=2):
    response = env.client.post('/api/projects', json=dict(title='批量图解',
        script='先接收请求，再进入队列。最后由执行节点处理。', style='逐步展开的技术图解', production_mode=mode))
    assert response.status_code == 200, response.text
    project = response.json()
    for index in range(scenes):
        response = env.client.post(f'/api/projects/{project["id"]}/scenes', json=dict(title=f'第{index + 1}镜',
            narration='先接收请求。再进入队列。最后交给执行节点。' if index == 0 else '执行节点取走任务。处理队列任务。最后返回结果。',
            visual_description='请求和队列逐步出现，箭头说明处理顺序。', duration_seconds=3, format='svg'))
        assert response.status_code == 200, response.text
        project = response.json()
    return project


def project_now(env, project):
    response = env.client.get(f'/api/projects/{project["id"]}')
    assert response.status_code == 200, response.text
    return response.json()


def batch_request(project, *, steps=None):
    return dict(expected_revision=project['revision'], scene_ids=[scene['id'] for scene in project['scenes']],
                steps=steps or ['motion', 'audio', 'draft'], reuse_current=True)


def preview(env, project, *, steps=None):
    body = batch_request(project, steps=steps)
    response = env.client.post(f'/api/projects/{project["id"]}/batches/preview', json=body)
    assert response.status_code == 200, response.text
    return body, response.json()


def create_batch(env, project, *, steps=None, key='batch-create-0001'):
    body, plan = preview(env, project, steps=steps)
    body['plan_sha256'] = plan['plan_sha256']
    response = env.client.post(f'/api/projects/{project["id"]}/batches', json=body,
                              headers={'Idempotency-Key': key})
    assert response.status_code == 202, response.text
    return response.json(), body


def get_batch(env, batch):
    response = env.client.get(f'/api/batches/{batch["id"]}')
    assert response.status_code == 200, response.text
    return response.json()


def finish_batch(env, batch):
    for _ in range(120):
        env.app.state.batch_workflow.tick()
        env.app.state.motion_workflow.tick()
        env.app.state.audio_workflow.tick()
        env.app.state.production.tick()
        current = get_batch(env, batch)
        if current['status'] in {'completed', 'partial_failed', 'blocked', 'cancelled'}:
            return current
    raise AssertionError('synthetic_batch_did_not_reach_terminal_state')


def task_counts(env):
    tables = ('motion_operations', 'audio_operations', 'video_renders', 'motion_versions',
              'audio_versions', 'motion_heads', 'motion_alignments')
    with env.app.state.store.transaction() as db:
        return {table: db.execute('SELECT count(*) FROM ' + table).fetchone()[0] for table in tables}


def heads(project):
    return [(scene['id'], scene['motion']['head_version_id'], scene.get('head_audio_version_id'),
             scene.get('head_version_id')) for scene in project['scenes']]


def adoption_request(project, batch):
    selections = []
    for scene in project['scenes']:
        motion = next(item for item in batch['items'] if item['scene_id'] == scene['id'] and item['step'] == 'motion')
        audio = next(item for item in batch['items'] if item['scene_id'] == scene['id'] and item['step'] == 'audio')
        assert motion['version_id'] and audio['version_id'], (motion, audio)
        selections.append(dict(scene_id=scene['id'], motion_version_id=motion['version_id'],
            audio_version_id=audio['version_id'], expected_motion_head_version_id=scene['motion']['head_version_id'],
            expected_audio_head_version_id=scene.get('head_audio_version_id')))
    return dict(expected_revision=project['revision'], selections=selections)


def assert_no_provider_io(env):
    assert env.gateway.posts == env.gateway.uploads == env.gateway.cancels == []
    assert env.speech.requests == env.renderer.calls == []


def test_preview_is_read_only_and_creation_defers_all_provider_io(env):
    project = new_project(env)
    before = task_counts(env)
    body, plan = preview(env, project)
    assert plan['estimated_tasks'] == dict(motion=2, audio=2, draft=2)
    assert len(plan['items']) == 6 and len(plan['plan_sha256']) == 64
    assert task_counts(env) == before
    assert project_now(env, project)['revision'] == project['revision']
    assert env.client.get(f'/api/projects/{project["id"]}/batches').json()['batches'] == []
    body['plan_sha256'] = plan['plan_sha256']
    response = env.client.post(f'/api/projects/{project["id"]}/batches', json=body,
                              headers={'Idempotency-Key': 'deferred-batch-01'})
    assert response.status_code == 202, response.text
    batch = response.json()
    assert batch['project_id'] == project['id'] and batch['status'] == 'queued'
    assert len(batch['items']) == 6 and all(item['status'] == 'queued' for item in batch['items'])
    assert task_counts(env) == before
    assert heads(project_now(env, project)) == heads(project)
    assert_no_provider_io(env)


def test_create_replay_is_exact_and_changed_idempotent_intent_is_rejected(env):
    project = new_project(env)
    batch, body = create_batch(env, project)
    endpoint = f'/api/projects/{project["id"]}/batches'
    replay = env.client.post(endpoint, json=body, headers={'Idempotency-Key': 'batch-create-0001'})
    assert replay.status_code == 202 and replay.json()['id'] == batch['id']
    changed = dict(body, steps=['motion'])
    assert env.client.post(endpoint, json=changed, headers={'Idempotency-Key': 'batch-create-0001'}).status_code == 409
    assert len(env.client.get(endpoint).json()['batches']) == 1
    assert_no_provider_io(env)


def test_preview_revision_and_plan_digest_protect_submission(env):
    project = new_project(env)
    body, plan = preview(env, project)
    endpoint = f'/api/projects/{project["id"]}/batches'
    wrong_revision = dict(body, expected_revision=1)
    assert env.client.post(endpoint + '/preview', json=wrong_revision).status_code == 409
    body['plan_sha256'] = '0' * 64
    assert env.client.post(endpoint, json=body, headers={'Idempotency-Key': 'wrong-plan-0001'}).status_code == 409
    assert env.client.post(endpoint, json=dict(body, plan_sha256=plan['plan_sha256'], expected_revision=1),
        headers={'Idempotency-Key': 'stale-plan-0001'}).status_code == 409
    assert env.client.get(endpoint).json()['batches'] == []
    assert_no_provider_io(env)


@pytest.mark.parametrize('change', [
    dict(scene_ids=[]), dict(scene_ids=['missing-scene']), dict(steps=[]),
    dict(steps=['motion', 'motion']), dict(steps=['delivery']), dict(reuse_current=False),
])
def test_invalid_preview_is_rejected_without_persisting_work(env, change):
    project = new_project(env)
    body = dict(batch_request(project), **change)
    response = env.client.post(f'/api/projects/{project["id"]}/batches/preview', json=body)
    assert response.status_code in {404, 422}, response.text
    assert env.client.get(f'/api/projects/{project["id"]}/batches').json()['batches'] == []
    assert_no_provider_io(env)


def test_batch_mutations_require_csrf_and_owner_reads_do_not_leak(env):
    project = new_project(env)
    batch, body = create_batch(env, project)
    requests = [
        (f'/api/projects/{project["id"]}/batches/preview', batch_request(project)),
        (f'/api/projects/{project["id"]}/batches', body),
        (f'/api/batches/{batch["id"]}/cancel', {}),
        (f'/api/batches/{batch["id"]}/retry', dict(expected_revision=project['revision'], item_ids=[batch['items'][0]['id']])),
        (f'/api/projects/{project["id"]}/batch-adopt', dict(expected_revision=project['revision'], selections=[
            dict(scene_id=project['scenes'][0]['id'], motion_version_id='missing-version', expected_motion_head_version_id=None)])),
    ]
    for endpoint, request in requests:
        response = env.client.post(endpoint, json=request,
            headers={'X-CSRF-Token': 'invalid', 'Idempotency-Key': 'security-boundary-01'})
        assert response.status_code == 403, (endpoint, response.text)
    env.client.headers['X-Forwarded-User'] = 'bob'
    env.client.headers['X-CSRF-Token'] = env.client.get('/api/session').json()['csrf_token']
    for endpoint in (f'/api/batches/{batch["id"]}', f'/api/projects/{project["id"]}/batches'):
        assert env.client.get(endpoint).status_code == 404
    for endpoint, request in requests:
        response = env.client.post(endpoint, json=request, headers={'Idempotency-Key': 'other-owner-0001'})
        assert response.status_code == 404, (endpoint, response.text)
    assert_no_provider_io(env)


def test_two_scene_success_keeps_candidates_unadopted_and_timing_unreviewed(env):
    project = new_project(env)
    batch, _ = create_batch(env, project)
    completed = finish_batch(env, batch)
    assert completed['status'] == 'completed', completed
    assert len(env.gateway.posts) == len(env.speech.requests) == len(env.renderer.calls) == 2
    assert all(item['status'] == 'completed' for item in completed['items'])
    after = project_now(env, project)
    assert heads(after) == heads(project)
    assert task_counts(env)['motion_alignments'] == 0
    for item in completed['items']:
        if item['step'] == 'draft':
            assert item['render_id']
        else:
            assert item['version_id']
    for call in env.renderer.calls:
        snapshot = call['snapshot']
        assert snapshot['purpose'] == 'draft' and snapshot['quality'] == 'draft'
        assert snapshot['scenes'][0]['motion']['alignment']['review_status'] == 'pending'
    adopted = env.client.post(f'/api/projects/{project["id"]}/batch-adopt', json=adoption_request(after, completed))
    assert adopted.status_code == 200, adopted.text
    assert all(scene['motion']['head_version_id'] and scene['head_audio_version_id'] for scene in adopted.json()['scenes'])
    delivery = env.client.post(f'/api/projects/{project["id"]}/motion-renders',
        json=dict(expected_revision=adopted.json()['revision'], purpose='delivery', subtitles=False),
        headers={'Idempotency-Key': 'batch-delivery-pending-01'})
    assert delivery.status_code == 409 and delivery.json()['detail']['code'] == 'motion_timing_needs_review'
    assert task_counts(env)['motion_alignments'] == 0


def test_batch_adoption_rolls_back_all_selections_if_one_exact_version_is_invalid(env):
    project = new_project(env)
    batch, _ = create_batch(env, project, steps=['motion', 'audio'])
    completed = finish_batch(env, batch)
    assert completed['status'] == 'completed', completed
    current = project_now(env, project)
    request = adoption_request(current, completed)
    request['selections'][1]['audio_version_id'] = request['selections'][0]['audio_version_id']
    response = env.client.post(f'/api/projects/{project["id"]}/batch-adopt', json=request)
    assert response.status_code == 404, response.text
    unchanged = project_now(env, project)
    assert unchanged['revision'] == current['revision'] and heads(unchanged) == heads(current)


def test_dual_head_and_revision_conflicts_do_not_partially_adopt(env):
    project = new_project(env)
    batch, _ = create_batch(env, project, steps=['motion', 'audio'])
    completed = finish_batch(env, batch)
    assert completed['status'] == 'completed', completed
    current = project_now(env, project)
    endpoint = f'/api/projects/{project["id"]}/batch-adopt'
    for key in ('expected_motion_head_version_id', 'expected_audio_head_version_id'):
        request = adoption_request(current, completed)
        request['selections'][1][key] = 'changed-head'
        response = env.client.post(endpoint, json=request)
        assert response.status_code == 409, response.text
        assert project_now(env, project)['revision'] == current['revision']
        assert heads(project_now(env, project)) == heads(current)
    request = dict(adoption_request(current, completed), expected_revision=1)
    assert env.client.post(endpoint, json=request).status_code == 409
    assert heads(project_now(env, project)) == heads(current)


def test_adoption_uses_frozen_explicit_versions_after_newer_candidates_arrive(env):
    project = new_project(env)
    batch, _ = create_batch(env, project, steps=['motion', 'audio'])
    completed = finish_batch(env, batch)
    assert completed['status'] == 'completed', completed
    current = project_now(env, project)
    scene_id = current['scenes'][0]['id']
    response = env.client.post(f'/api/scenes/{scene_id}/motion',
        json=dict(expected_revision=current['revision']), headers={'Idempotency-Key': 'later-motion-0001'})
    assert response.status_code == 202, response.text
    env.app.state.motion_workflow.tick()
    current = project_now(env, project)
    response = env.client.post(f'/api/scenes/{scene_id}/audio/generate',
        json=dict(expected_revision=current['revision']), headers={'Idempotency-Key': 'later-audio-0001'})
    assert response.status_code == 202, response.text
    env.app.state.audio_workflow.tick()
    current = project_now(env, project)
    request = adoption_request(current, completed)
    original = request['selections'][0]
    assert current['scenes'][0]['motion']['versions'][-1]['id'] != original['motion_version_id']
    assert current['scenes'][0]['audio_versions'][-1]['id'] != original['audio_version_id']
    response = env.client.post(f'/api/projects/{project["id"]}/batch-adopt', json=request)
    assert response.status_code == 200, response.text
    chosen = response.json()['scenes'][0]
    assert chosen['motion']['head_version_id'] == original['motion_version_id']
    assert chosen['head_audio_version_id'] == original['audio_version_id']
    assert task_counts(env)['motion_alignments'] == 0


@pytest.mark.parametrize('head_key', ['expected_motion_head_version_id', 'expected_audio_head_version_id'])
def test_explicit_null_head_is_required_and_omission_never_assumes_null(env, head_key):
    project = new_project(env)
    batch, _ = create_batch(env, project, steps=['motion', 'audio'])
    completed = finish_batch(env, batch)
    current = project_now(env, project)
    request = adoption_request(current, completed)
    del request['selections'][0][head_key]
    response = env.client.post(f'/api/projects/{project["id"]}/batch-adopt', json=request)
    assert response.status_code == 422, response.text
    assert project_now(env, project)['revision'] == current['revision']
    assert heads(project_now(env, project)) == heads(current)


def test_repeated_batch_reuses_exact_unadopted_candidates_and_drafts_without_new_io(env):
    project = new_project(env)
    batch, _ = create_batch(env, project)
    completed = finish_batch(env, batch)
    assert completed['status'] == 'completed', completed
    current = project_now(env, project)
    before = task_counts(env)
    _, plan = preview(env, current)
    assert plan['estimated_tasks'] == dict(motion=0, audio=0, draft=0)
    assert all(item['status'] == 'reused' for item in plan['items'])
    reused, _ = create_batch(env, current, key='second-reused-batch-01')
    assert reused['status'] == 'completed' and reused['progress']['reused'] == 6
    assert [(item['version_id'], item['render_id']) for item in reused['items']] == [
        (item['version_id'], item['render_id']) for item in completed['items']]
    env.app.state.batch_workflow.tick()
    assert task_counts(env) == before
    assert len(env.gateway.posts) == len(env.speech.requests) == len(env.renderer.calls) == 2
    assert heads(project_now(env, project)) == heads(project)


def test_changed_audio_anchors_invalidate_only_the_affected_reusable_draft(env):
    project = new_project(env)
    batch, _ = create_batch(env, project)
    completed = finish_batch(env, batch)
    assert completed['status'] == 'completed', completed
    current = project_now(env, project)
    selection = adoption_request(current, completed)['selections'][0]
    endpoint = f'/api/motion-versions/{selection["motion_version_id"]}/timing'
    response = env.client.get(endpoint, params=dict(audio_version_id=selection['audio_version_id']))
    assert response.status_code == 200, response.text
    timing = response.json()
    anchors = deepcopy(timing['alignment']['segments'])
    anchors[0]['end'] -= .05
    response = env.client.patch(endpoint, json=dict(expected_revision=current['revision'],
        audio_version_id=selection['audio_version_id'], expected_alignment_sha256=timing['alignment_sha256'],
        segments=anchors, correction_reason='Synthetic timing conflict check'))
    assert response.status_code == 200, response.text
    assert response.json()['alignment']['review_status'] == 'pending'
    current = project_now(env, project)
    before = task_counts(env)
    _, plan = preview(env, current)
    assert plan['estimated_tasks'] == dict(motion=0, audio=0, draft=1)
    drafts = [item for item in plan['items'] if item['step'] == 'draft']
    assert [item['status'] for item in drafts] == ['queued', 'reused']
    assert task_counts(env) == before
    assert len(env.gateway.posts) == len(env.speech.requests) == len(env.renderer.calls) == 2
    assert heads(current) == heads(project)


def test_content_edit_after_creation_blocks_undispatched_work_without_provider_io(env):
    project = new_project(env)
    batch, _ = create_batch(env, project)
    current = project_now(env, project)
    scene = current['scenes'][0]
    fields = {key: scene[key] for key in ('title', 'narration', 'visual_description', 'duration_seconds', 'format')}
    fields.update(expected_revision=current['revision'], narration='新的旁白。解释新的队列。最后返回新的结果。')
    response = env.client.patch(f'/api/scenes/{scene["id"]}', json=fields)
    assert response.status_code == 200, response.text
    for _ in range(3):
        env.app.state.batch_workflow.tick()
    stopped = get_batch(env, batch)
    assert stopped['status'] == 'blocked', stopped
    assert any(item['error'] == 'batch_inputs_changed' for item in stopped['items'])
    assert heads(project_now(env, project)) == heads(project)
    assert_no_provider_io(env)


def test_cancel_before_dispatch_is_repeatable_and_does_not_submit_children(env):
    project = new_project(env)
    batch, _ = create_batch(env, project)
    endpoint = f'/api/batches/{batch["id"]}/cancel'
    response = env.client.post(endpoint)
    assert response.status_code == 200, response.text
    finished = finish_batch(env, batch)
    assert finished['status'] == 'cancelled', finished
    assert all(item['status'] in {'cancelled', 'skipped'} for item in finished['items'])
    assert env.client.post(endpoint).status_code == 200
    assert task_counts(env)['motion_operations'] == task_counts(env)['audio_operations'] == 0
    assert heads(project_now(env, project)) == heads(project)
    assert_no_provider_io(env)


def test_active_batch_blocks_legacy_conversion_before_any_child_is_created(env):
    project = new_project(env, mode='static')
    batch, _ = create_batch(env, project, steps=['motion'])
    current = project_now(env, project)
    response = env.client.patch(f'/api/projects/{project["id"]}/production-mode',
                               json=dict(mode='motion', expected_revision=current['revision']))
    assert response.status_code == 409 and response.json()['detail']['code'] == 'project_production_mode_busy'
    assert get_batch(env, batch)['status'] == 'queued'
    assert project_now(env, project)['production_mode'] == 'static'
    assert_no_provider_io(env)


def test_one_scene_failure_does_not_erase_other_scene_result_and_retry_is_explicit(env):
    project = new_project(env)
    env.gateway.fail_titles.add(project['scenes'][0]['title'])
    batch, _ = create_batch(env, project, steps=['motion'])
    failed = finish_batch(env, batch)
    assert failed['status'] == 'partial_failed', failed
    first, second = failed['items']
    assert first['status'] == 'failed' and second['status'] == 'completed'
    assert first['version_id'] is None and second['version_id']
    assert len(env.gateway.posts) == 2
    env.app.state.batch_workflow.tick()
    assert len(env.gateway.posts) == 2
    current = project_now(env, project)
    request = dict(expected_revision=current['revision'], item_ids=[first['id']])
    endpoint = f'/api/batches/{batch["id"]}/retry'
    env.gateway.fail_titles.clear()
    response = env.client.post(endpoint, json=request, headers={'Idempotency-Key': 'explicit-retry-01'})
    assert response.status_code == 202, response.text
    retry = response.json()
    assert retry['id'] != batch['id'] and retry['parent_batch_id'] == batch['id']
    replay = env.client.post(endpoint, json=request, headers={'Idempotency-Key': 'explicit-retry-01'})
    assert replay.status_code == 202 and replay.json()['id'] == retry['id']
    assert finish_batch(env, retry)['status'] == 'completed'
    assert len(env.gateway.posts) == 3
    assert heads(project_now(env, project)) == heads(project)
