"""Continuous project snapshots through normal authoring/render APIs.

Only Agent and media execution are synthetic; SQLite, ownership, WAV identity,
candidate adoption, audio-bound timing, queue recovery and HTTP routes are real.
"""
from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest

from agent_service.contracts import ServiceError
from video_workbench.app import create_app
from video_workbench.continuity_snapshot import verify_motion_snapshot
from video_workbench.production import Production
from test_motion_production_api import (Gateway, RUNTIME, setup as setup_mixed,
                                       adopt as adopt_motion, timing, review, pcm)


class Renderer:
    def __init__(self):
        self.calls, self.cancelled = [], []
        self.fail = None

    def render(self, snapshot, directory, *, operation_id, owner, cancel_event):
        verify_motion_snapshot(snapshot)
        self.calls.append(dict(snapshot=deepcopy(snapshot), operation_id=operation_id, owner=owner))
        if self.fail:
            raise self.fail
        if cancel_event.is_set():
            self.cancelled.append(operation_id)
            raise ServiceError('render_cancelled', 409)
        path = directory / 'video.mp4'
        path.write_bytes(b'synthetic-continuity-render')
        return dict(path=path, duration_seconds=snapshot['duration_seconds'])


@pytest.fixture
def env(tmp_path):
    gateway, renderer = Gateway(), Renderer()
    app = create_app(db_path=tmp_path / 'state.sqlite', trusted_hosts={'testserver'}, background=False,
                     motion_enabled=True, motion_gateway=gateway, motion_renderer=renderer,
                     motion_runtime=RUNTIME, render_enabled=True)
    with TestClient(app) as client:
        client.headers['X-Forwarded-User'] = 'alice'
        client.headers['X-CSRF-Token'] = client.get('/api/session').json()['csrf_token']
        yield client, app, gateway, renderer


def setup(env):
    client, app, gateway, renderer = env
    project, sid, version_id, _ = setup_mixed(env)
    project = adopt_motion(env, project, sid, version_id)
    first_id = project['scenes'][0]['id']
    response = client.post(f'/api/scenes/{first_id}/motion',
                           json=dict(expected_revision=project['revision']),
                           headers={'Idempotency-Key': 'continuity-first-motion'})
    assert response.status_code == 202, response.text
    app.state.motion_workflow.tick()
    project = client.get(f'/api/projects/{project["id"]}').json()
    first_version = project['scenes'][0]['motion']['versions'][-1]['id']
    project = adopt_motion(env, project, first_id, first_version)
    for scene in project['scenes']:
        timing(env, scene['motion']['head_version_id'], scene['head_audio_version_id'])
    response = client.post(f'/api/projects/{project["id"]}/continuity/proposals',
                           json=dict(expected_revision=project['revision']))
    assert response.status_code == 201, response.text
    candidate = response.json()
    assert candidate['plan']['links'] and not candidate['stale']
    return project, candidate


def render_body(project, candidate, purpose='draft'):
    return dict(expected_revision=project['revision'], purpose=purpose, subtitles=False,
                continuity_version_id=candidate['id'])


def adopt_continuity(env, project, candidate, head=None):
    response = env[0].post(f'/api/projects/{project["id"]}/continuity/adopt',
                           json=dict(expected_revision=project['revision'], version_id=candidate['id'],
                                     expected_head_version_id=head))
    assert response.status_code == 200, response.text
    return response.json()


def submit(env, project, body, key):
    return env[0].post(f'/api/projects/{project["id"]}/motion-renders', json=body,
                       headers={'Idempotency-Key': key})


def test_explicit_candidate_creates_v3_draft_without_adoption_or_new_speech(env):
    client, app, gateway, renderer = env
    project, candidate = setup(env)
    before = app.state.store.project('alice', project['id'])
    plain = app.state.production.snapshot_v2('alice', project['id'],
        dict(expected_revision=project['revision'], purpose='draft', subtitles=False))
    assert plain['schema'] == 'video.render-snapshot.v2' and 'continuity' not in plain
    body = render_body(project, candidate)
    response = submit(env, project, body, 'continuous-draft')
    assert response.status_code == 202, response.text
    job = response.json()
    assert job['schema'] == 'video.render-snapshot.v3' and job['continuity_version_id'] == candidate['id']
    with app.state.store.transaction() as db:
        queued = json.loads(db.execute('SELECT snapshot FROM video_renders WHERE id=?', (job['id'],)).fetchone()[0])
    assert queued['visual_mode'] == 'continuous' and queued['continuity']['version_id'] == candidate['id']
    assert [scene['audio']['id'] for scene in queued['scenes']] == [scene['head_audio_version_id'] for scene in project['scenes']]
    assert all('data_base64' not in scene['audio'] for scene in queued['scenes'])
    assert submit(env, project, body, 'continuous-draft').json()['id'] == job['id']
    app.state.production.tick()
    completed = app.state.production.renders('alice', project['id'])[0]
    assert completed.get('error') is None, completed.get('error')
    assert completed['status'] == 'completed', completed
    assert renderer.calls[0]['snapshot']['schema'] == 'video.render-snapshot.v3'
    assert renderer.calls[0]['snapshot']['source_snapshot_sha256'] == plain['sha256']
    assert renderer.calls[0]['operation_id'] == job['id'] and renderer.calls[0]['owner'] == 'alice'
    assert client.get(f'/api/renders/{job["id"]}/video').content == b'synthetic-continuity-render'
    assert client.get(f'/api/projects/{project["id"]}/continuity').json()['head_version_id'] is None
    assert app.state.store.project('alice', project['id']) == before
    assert gateway.posts == 2 and gateway.uploads == 2
    assert app.state.store.active_audio_operations() == []


def test_delivery_requires_exact_continuity_head_and_each_audio_review(env):
    client, app, _, _ = env
    project, candidate = setup(env)
    endpoint = f'/api/projects/{project["id"]}/motion-renders'
    response = submit(env, project, render_body(project, candidate, 'delivery'), 'unreviewed-continuity')
    assert response.status_code == 409 and response.json()['detail']['code'] == 'motion_timing_needs_review'
    assert app.state.production.renders('alice', project['id']) == []
    for scene in project['scenes']:
        review(env, scene['motion']['head_version_id'], scene['head_audio_version_id'])
    project = client.get(f'/api/projects/{project["id"]}').json()
    response = submit(env, project, render_body(project, candidate, 'delivery'), 'unadopted-continuity')
    assert response.status_code == 409 and response.json()['detail']['code'] == 'continuity_head_conflict'
    project = adopt_continuity(env, project, candidate)
    response = submit(env, project, render_body(project, candidate, 'delivery'), 'reviewed-continuity')
    assert response.status_code == 202, response.text
    assert response.json()['purpose'] == 'delivery' and response.json()['schema'] == 'video.render-snapshot.v3'


def test_continuity_routes_and_render_remain_owner_isolated(env):
    client, app, _, _ = env
    project, candidate = setup(env)
    job = submit(env, project, render_body(project, candidate), 'alice-continuity').json()
    client.headers['X-Forwarded-User'] = 'bob'
    client.headers['X-CSRF-Token'] = client.get('/api/session').json()['csrf_token']
    endpoint = f'/api/projects/{project["id"]}/continuity'
    assert client.get(endpoint).status_code == 404
    assert client.post(endpoint + '/proposals', json=dict(expected_revision=project['revision'])).status_code == 404
    assert client.post(endpoint + '/adopt', json=dict(expected_revision=project['revision'],
        version_id=candidate['id'], expected_head_version_id=None)).status_code == 404
    assert submit(env, project, render_body(project, candidate), 'bob-continuity').status_code == 404
    assert client.get(f'/api/renders/{job["id"]}/video').status_code == 404
    client.headers['X-Forwarded-User'] = 'alice'
    client.headers.pop('X-CSRF-Token')
    assert client.post(endpoint + '/proposals', json=dict(expected_revision=project['revision'])).status_code == 403


def test_replaced_recording_reuses_visual_candidate_but_binds_new_audio_and_timing(env):
    client, app, gateway, _ = env
    project, candidate = setup(env)
    project = adopt_continuity(env, project, candidate)
    first = project['scenes'][0]
    review(env, first['motion']['head_version_id'], first['head_audio_version_id'])
    project = client.get(f'/api/projects/{project["id"]}').json()
    old = app.state.production.snapshot_v2('alice', project['id'], render_body(project, candidate))
    project = app.state.store.upload_audio('alice', first['id'], dict(expected_revision=project['revision']),
                                           'replacement-recording', pcm(value=2, seconds=4))
    replacement = project['scenes'][0]['audio_versions'][-1]['id']
    app.state.store.adopt_audio('alice', first['id'], replacement, first['head_audio_version_id'])
    project = client.get(f'/api/projects/{project["id"]}').json()
    current = client.get(f'/api/projects/{project["id"]}/continuity').json()
    assert current['head_version_id'] == candidate['id'] and not current['head_stale']
    assert current['versions'][0]['input_binding_sha256'] == candidate['input_binding_sha256']
    refreshed = app.state.production.snapshot_v2('alice', project['id'], render_body(project, candidate))
    assert refreshed['scenes'][0]['audio']['id'] == replacement
    assert refreshed['scenes'][0]['audio']['sha256'] != old['scenes'][0]['audio']['sha256']
    assert refreshed['scenes'][0]['motion']['alignment']['review_status'] != 'verified'
    assert old['scenes'][0]['audio']['id'] == first['head_audio_version_id']
    assert old['sha256'] != refreshed['sha256'] and gateway.posts == 2
    verify_motion_snapshot(old)
    verify_motion_snapshot(refreshed)


def test_v3_restart_recovers_same_queue_snapshot_and_render_identity(env):
    client, app, gateway, renderer = env
    project, candidate = setup(env)
    response = submit(env, project, render_body(project, candidate), 'continuity-restart')
    assert response.status_code == 202, response.text
    job = response.json()
    with app.state.store.transaction() as db:
        row = db.execute('SELECT document,snapshot FROM video_renders WHERE id=?', (job['id'],)).fetchone()
        queued_snapshot = row['snapshot']
        document = json.loads(row['document'])
        document.update(status='running', render_attempted=True)
        db.execute('UPDATE video_renders SET document=? WHERE id=?', (json.dumps(document), job['id']))
    restarted = Production(app.state.store, enabled=False, motion_enabled=False, motion_renderer=renderer,
        motion_store=app.state.motion_store, motion_timing=app.state.motion_timing,
        runtime_fingerprint=RUNTIME, continuity_store=app.state.continuity_store)
    restarted._recover()
    restarted.tick()
    assert renderer.calls[0]['operation_id'] == job['id']
    assert renderer.calls[0]['snapshot']['schema'] == 'video.render-snapshot.v3'
    assert restarted.renders('alice', project['id'])[0]['status'] == 'completed'
    with app.state.store.transaction() as db:
        assert db.execute('SELECT snapshot FROM video_renders WHERE id=?', (job['id'],)).fetchone()[0] == queued_snapshot
    assert gateway.posts == 2


def test_v3_outage_then_cancel_keeps_same_identity_and_late_progress_is_ignored(env):
    client, app, _, renderer = env
    project, candidate = setup(env)
    job = submit(env, project, render_body(project, candidate), 'continuity-outage').json()
    renderer.fail = ServiceError('motion_render_service_unavailable', 503)
    app.state.production.tick()
    pending = app.state.production.renders('alice', project['id'])[0]
    assert pending['status'] == 'queued' and pending['render_attempted']
    app.state.production.motion_enabled = False
    app.state.production.cancel('alice', job['id'])
    renderer.fail = None
    app.state.production.tick()
    final = app.state.production.renders('alice', project['id'])[0]
    assert final['status'] == 'cancelled' and renderer.cancelled == [job['id']]
    assert [call['operation_id'] for call in renderer.calls] == [job['id'], job['id']]
    assert renderer.calls[0]['snapshot'] == renderer.calls[1]['snapshot']
    app.state.production._render_progress(job['id'], job['snapshot_sha256'], dict(progress=dict(
        stage='completed', scene_index=1, completed_scenes=2, total_scenes=2, updated_at=100)))
    assert app.state.production.renders('alice', project['id'])[0] == final


@pytest.mark.parametrize('change', ['single_scene', 'subtitles'])
def test_continuity_selection_does_not_mix_single_scene_or_subtitle_modes(env, change):
    client, app, _, _ = env
    project, candidate = setup(env)
    body = render_body(project, candidate)
    if change == 'subtitles':
        body['subtitles'] = True
    else:
        scene = project['scenes'][0]
        body.update(scene_id=scene['id'], motion_version_id=scene['motion']['head_version_id'],
                    audio_version_id=scene['head_audio_version_id'])
    response = submit(env, project, body, 'continuity-invalid-' + change)
    assert response.status_code == 422, response.text
    assert app.state.production.renders('alice', project['id']) == []
