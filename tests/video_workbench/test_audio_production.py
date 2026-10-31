"""Real persistence and HTTP boundaries; no model calls or external services."""
import io
import json
import wave

import httpx
import pytest
from fastapi.testclient import TestClient

from video_workbench.app import create_app
from video_workbench.speech import SpeechClient, SpeechSettings

SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><rect width="1280" height="720" fill="blue"/></svg>'
FIELDS = dict(title='原理', narration='这里介绍完整原理。', visual_description='蓝色背景', duration_seconds=1, format='svg')


def pcm():
    out = io.BytesIO()
    with wave.open(out, 'wb') as stream:
        stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        stream.writeframes(b'\x01\x02' * 36000)
    return out.getvalue()


@pytest.fixture
def env(tmp_path):
    calls = []
    def upstream(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, stream=httpx.ByteStream(pcm()))
    speech = SpeechClient(SpeechSettings('http://fixture/v1', 'kokoro-rk3588', voice='zm_yunjian'),
                          transport=httpx.MockTransport(upstream))
    snapshots = []
    def renderer(snapshot, directory, *, cancel_event):
        snapshots.append(snapshot)
        path = directory / 'video.mp4'
        path.write_bytes(b'fixture-video')
        return dict(path=path, duration_seconds=snapshot['duration_seconds'])
    app = create_app(db_path=tmp_path / 'state.db', trusted_hosts={'testserver'},
                     background=False, speech_client=speech, renderer=renderer, render_enabled=True)
    with TestClient(app) as client:
        client.headers['X-Forwarded-User'] = 'alice'
        client.headers['X-CSRF-Token'] = client.get('/api/session').json()['csrf_token']
        yield client, app, calls, snapshots
    speech.close()


def scene(env):
    client, app, *_ = env
    p = client.post('/api/projects', json=dict(title='视频', script='稿件', style='')).json()
    p = client.post(f'/api/projects/{p["id"]}/scenes', json=FIELDS).json()
    sid = p['scenes'][0]['id']
    store = app.state.store
    op = store.start('alice', p['id'], 'scene', dict(expected_revision=p['revision']), 'visual-fixture', scene_id=sid)
    store.complete(op['id'], dict(source=SVG, format='svg', summary='fixture'))
    p = store.project('alice', p['id'])
    p = store.adopt('alice', sid, p['scenes'][0]['versions'][0]['id'], None)
    return p, sid


def uploaded(env):
    client = env[0]
    p, sid = scene(env)
    response = client.post(f'/api/scenes/{sid}/audio/upload?expected_revision={p["revision"]}',
                           content=pcm(), headers={'Idempotency-Key': 'upload-fixture', 'Content-Type': 'audio/wav'})
    assert response.status_code == 200, response.text
    p = response.json()
    aid = p['scenes'][0]['audio_versions'][0]['id']
    assert p['scenes'][0]['head_audio_version_id'] is None
    response = client.post(f'/api/scenes/{sid}/audio/adopt', json=dict(version_id=aid, expected_head_audio_version_id=None))
    assert response.status_code == 200, response.text
    return response.json(), sid, aid


def test_generate_is_candidate_and_replay_never_synthesizes_twice(env):
    client, app, calls, _ = env
    p, sid = scene(env)
    body = dict(expected_revision=p['revision'])
    r = client.post(f'/api/scenes/{sid}/audio/generate', json=body, headers={'Idempotency-Key': 'speech-fixture'})
    assert r.status_code == 202
    app.state.audio_workflow.tick()
    repeated = client.post(f'/api/scenes/{sid}/audio/generate', json=body, headers={'Idempotency-Key': 'speech-fixture'})
    app.state.audio_workflow.tick()
    assert repeated.json()['id'] == r.json()['id'] and len(calls) == 1
    p = client.get(f'/api/projects/{p["id"]}').json()
    assert len(p['scenes'][0]['audio_versions']) == 1
    assert p['scenes'][0]['head_audio_version_id'] is None


def test_upload_playback_ranges_and_owner_boundary(env):
    client, _, _, _ = env
    p, sid, aid = uploaded(env)
    url = f'/api/audio-versions/{aid}/audio'
    assert client.get(url).content == pcm()
    part = client.get(url, headers={'Range': 'bytes=0-43'})
    assert part.status_code == 206 and part.content == pcm()[:44]
    assert part.headers['content-range'].endswith('/' + str(len(pcm())))
    assert client.get(url, headers={'Range': 'bytes=-10'}).content == pcm()[-10:]
    assert client.get(url, headers={'Range': 'bytes=' + '9' * 1000 + '-'}).status_code == 416
    client.headers['X-Forwarded-User'] = 'bob'
    assert client.get(url, headers={'Range': 'bytes=0-43'}).status_code == 404
    assert client.get(f'/api/projects/{p["id"]}/timeline').status_code == 404


def test_timing_and_manual_captions_use_complete_audio(env):
    client = env[0]
    p, sid, aid = uploaded(env)
    p = client.patch(f'/api/scenes/{sid}/timing', json=dict(expected_revision=p['revision'], leading_seconds=.25, trailing_seconds=.5)).json()
    timeline = client.get(f'/api/projects/{p["id"]}/timeline').json()
    assert timeline['duration_seconds'] == 2.25  # audio1.5 + explicit padding
    p = client.post(f'/api/scenes/{sid}/captions/estimate', json=dict(expected_revision=p['revision'])).json()
    assert p['scenes'][0]['captions']['requires_review'] is True
    cues = [dict(start=.25, end=1.75, text='这里介绍完整原理。')]
    p = client.put(f'/api/scenes/{sid}/captions', json=dict(expected_revision=p['revision'], audio_version_id=aid, cues=cues)).json()
    assert p['scenes'][0]['captions']['requires_review'] is False
    srt = client.get(f'/api/projects/{p["id"]}/subtitles')
    assert srt.status_code == 200 and '00:00:00,250 --> 00:00:01,750' in srt.text
    bad = client.put(f'/api/scenes/{sid}/captions', json=dict(expected_revision=p['revision'], audio_version_id=aid,
        cues=[dict(start=.1, end=1, text='过早')]))
    assert bad.status_code == 422


def test_local_invalidation_preserves_visual_and_rejects_old_audio(env):
    client = env[0]
    p, sid, aid = uploaded(env)
    visual = p['scenes'][0]['head_version_id']
    p = client.post(f'/api/scenes/{sid}/captions/estimate', json=dict(expected_revision=p['revision'])).json()
    p = client.patch(f'/api/scenes/{sid}', json=dict(FIELDS, narration='新的旁白内容。', expected_revision=p['revision'])).json()
    assert p['scenes'][0]['audio_stale'] and p['scenes'][0]['captions']['stale']
    assert p['scenes'][0]['head_version_id'] == visual
    assert client.get(f'/api/audio-versions/{aid}/audio').status_code == 200
    assert client.get(f'/api/projects/{p["id"]}/timeline').status_code == 409
    assert client.get(f'/api/projects/{p["id"]}/export').status_code == 200


def test_leading_pause_change_invalidates_old_subtitle_timing(env):
    client = env[0]
    p, sid, aid = uploaded(env)
    p = client.post(f'/api/scenes/{sid}/captions/estimate', json=dict(expected_revision=p['revision'])).json()
    p = client.patch(f'/api/scenes/{sid}/timing', json=dict(expected_revision=p['revision'], leading_seconds=1, trailing_seconds=0)).json()
    assert p['scenes'][0]['captions']['timing_stale']
    assert client.get(f'/api/projects/{p["id"]}/subtitles').status_code == 409
    assert client.get(f'/api/projects/{p["id"]}/timeline').json()['scenes'][0]['cues'] == []


def test_render_freezes_adopted_bytes_and_does_not_follow_later_edits(env):
    client, app, _, snapshots = env
    p, sid, aid = uploaded(env)
    p = client.post(f'/api/scenes/{sid}/captions/estimate', json=dict(expected_revision=p['revision'])).json()
    body = dict(expected_revision=p['revision'], silent=False, subtitles=True)
    r = client.post(f'/api/projects/{p["id"]}/renders', json=body, headers={'Idempotency-Key': 'render-fixture'})
    assert r.status_code == 202, r.text
    rid = r.json()['id']
    client.patch(f'/api/scenes/{sid}', json=dict(FIELDS, narration='渲染后新文稿。', expected_revision=p['revision']))
    app.state.production.tick()
    assert snapshots[0]['scenes'][0]['narration'] == FIELDS['narration']
    assert snapshots[0]['scenes'][0]['audio']['id'] == aid
    replay = client.post(f'/api/projects/{p["id"]}/renders', json=body, headers={'Idempotency-Key': 'render-fixture'})
    assert replay.json()['id'] == rid
    assert client.get(f'/api/renders/{rid}/video').content == b'fixture-video'
    client.headers['X-Forwarded-User'] = 'bob'
    assert client.get(f'/api/renders/{rid}/video').status_code == 404


def test_missing_audio_requires_explicit_silent_export(env):
    client, app, _, snapshots = env
    p, sid = scene(env)
    url = f'/api/projects/{p["id"]}/renders'
    assert client.post(url, json=dict(expected_revision=p['revision']), headers={'Idempotency-Key': 'missing-audio'}).status_code == 409
    r = client.post(url, json=dict(expected_revision=p['revision'], silent=True), headers={'Idempotency-Key': 'silent-fixture'})
    assert r.status_code == 202
    app.state.production.tick()
    assert snapshots[0]['silent'] and snapshots[0]['scenes'][0]['audio'] is None


def test_cancelled_queued_render_never_starts_renderer(env):
    client, app, _, snapshots = env
    p, sid = scene(env)
    r = client.post(f'/api/projects/{p["id"]}/renders', json=dict(expected_revision=p['revision'], silent=True), headers={'Idempotency-Key': 'cancel-fixture'})
    assert client.post('/api/renders/' + r.json()['id'] + '/cancel').json()['status'] == 'cancelled'
    app.state.production.tick()
    assert snapshots == []


def test_explicit_silent_export_ignores_stale_audio_and_subtitles(env):
    client, app, _, snapshots = env
    p, sid, aid = uploaded(env)
    p = client.post(f'/api/scenes/{sid}/captions/estimate', json=dict(expected_revision=p['revision'])).json()
    p = client.patch(f'/api/scenes/{sid}', json=dict(FIELDS, narration='改变后的旁白', expected_revision=p['revision'])).json()
    r = client.post(f'/api/projects/{p["id"]}/renders', json=dict(expected_revision=p['revision'], silent=True), headers={'Idempotency-Key': 'stale-silent-fixture'})
    assert r.status_code == 202, r.text
    app.state.production.tick()
    assert snapshots[0]['silent'] and snapshots[0]['scenes'][0]['captions']['cues'] == []


def test_audio_and_render_mutations_require_csrf(env):
    client = env[0]
    p, sid, aid = uploaded(env)
    client.headers.pop('X-CSRF-Token')
    assert client.post(f'/api/scenes/{sid}/audio/generate', json=dict(expected_revision=p['revision']), headers={'Idempotency-Key': 'csrf-speech'}).status_code == 403
    assert client.post(f'/api/projects/{p["id"]}/renders', json=dict(expected_revision=p['revision'], silent=True), headers={'Idempotency-Key': 'csrf-render'}).status_code == 403


def test_bad_upload_does_not_create_a_candidate(env):
    client = env[0]
    p, sid = scene(env)
    r = client.post(f'/api/scenes/{sid}/audio/upload?expected_revision={p["revision"]}', content=b'not a wav', headers={'Idempotency-Key': 'bad-recording'})
    assert r.status_code == 422
    assert client.get(f'/api/projects/{p["id"]}').json()['scenes'][0]['audio_versions'] == []
