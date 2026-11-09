"""Isolated v4 delivery custody and real PCM integration.

The ordinary test freezes real API/SQLite state but does not claim rendering.
The optional encoded test requires artifacts from the exact external renderer;
there is no color-clip, synthetic-video, Docker or live-service fallback.

Prepare external inputs (a new directory only)::

    python tests/video_workbench/test_creative_delivery_integration.py prepare \
      --directory <new-fixture-directory> --runtime <actual-runtime.json>

An operator renders the two ``inputs/scene-NNN`` directories using the pinned
image's run.mjs render+observe, and the static snapshot through render_snapshot.
Keep dynamic artifacts under artifacts/scene-NNN and static output/report under
artifacts/scene-000. Then run ``collect`` to bind their bytes, followed by pytest
with VIDEO_CREATIVE_DELIVERY_FIXTURE_DIR pointing to the prepared directory.

All timing verification below belongs exclusively to a new fixture owner and
database. It is not a user's audition, project approval or production mutation.
"""
from __future__ import annotations

import argparse
from array import array
import base64
from contextlib import closing, contextmanager
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import wave

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient

from agent_service.contracts import canonical_bytes, digest
from video_workbench import media
from video_workbench.app import create_app
from video_workbench.creative_renderer import compile_creative_project
from video_workbench.creative_snapshot import verify_snapshot_v4
from video_workbench.motion import example_plan
from video_workbench.motion_dispatch import scene_compiler
from video_workbench.motion_render_k8s import validate_artifacts
from video_workbench.motion_render_service import MixedMotionRenderer, verify_media


OWNER = 'fixture-creative-delivery-only'
SCHEMA = 'video.creative-delivery-integration-fixture.v1'
RATE = 24000
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
       '<rect width="1280" height="720" fill="#172c46"/>'
       '<circle cx="640" cy="360" r="100" fill="#55d8bd"/></svg>')
HTML = '''<!doctype html><html><head><style>
html,body{margin:0;width:1280px;height:720px;overflow:hidden;background:#172c46}
svg{display:block;width:1280px;height:720px}</style></head><body>
<svg viewBox="0 0 1280 720"><circle id="request" r="48" cy="360" fill="#55d8bd"/></svg>
<script>window.__animationExperiment={seek(t){document.getElementById('request').setAttribute('cx',200+Math.min(2,Math.max(0,t))*400)}};
window.__animationExperiment.seek(0);</script></body></html>'''


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def json_file(path):
    return json.loads(Path(path).read_bytes())


def write_json(path, document):
    Path(path).write_bytes(canonical_bytes(document) + b'\n')


def tone(index):
    """Three distinguishable, zero-mean recordings with a changing envelope."""
    frequencies = ((317, 659), (433, 947), (587, 1201))[index]
    values = array('h')
    for sample in range(RATE * 2):
        t = sample / RATE
        envelope = .55 + .25 * math.sin(2 * math.pi * (1.3 + index * .2) * t)
        value = sum(math.sin(2 * math.pi * frequency * t) for frequency in frequencies)
        values.append(round(10000 * envelope * value))
    if sys.byteorder != 'little':
        values.byteswap()
    result = io.BytesIO()
    with wave.open(result, 'wb') as stream:
        stream.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
        stream.writeframes(values.tobytes())
    return result.getvalue()


@contextmanager
def isolated_environment():
    keys = ('VIDEO_TTS_URL', 'VIDEO_TTS_MODEL', 'VIDEO_TTS_VOICE',
            'VIDEO_TTS_TOKEN', 'VIDEO_TTS_TOKEN_FILE', 'VIDEO_TTS_VOICE_DISCOVERY',
            'VIDEO_STATIC_RENDERER')
    saved = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            os.environ.pop(key, None)
        os.environ['VIDEO_STATIC_RENDERER'] = 'weasyprint'
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class NeverRender:
    def render(self, *args, **kwargs):
        raise AssertionError('API-only fixture must not execute media or services')


def app_for(path, runtime, renderer=None):
    # An explicit inert gateway prevents discovery of an operator's environment.
    return create_app(db_path=path, gateway=object(), trusted_hosts={'testserver'},
        background=False, motion_enabled=True, creative_enabled=True,
        motion_renderer=renderer or NeverRender(), motion_runtime=runtime,
        render_enabled=False)


def login(client, owner=OWNER):
    client.headers['X-Forwarded-User'] = owner
    client.headers['X-CSRF-Token'] = client.get('/api/session').json()['csrf_token']


def checked(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def project(client, identity):
    return checked(client.get(f'/api/projects/{identity}/motion'))


def stored_snapshot(app, render_id):
    with app.state.store.transaction() as db:
        packed = json.loads(db.execute('SELECT snapshot FROM video_renders WHERE id=?', (render_id,)).fetchone()[0])
    for scene in packed['scenes']:
        recording = app.state.store.audio_version(OWNER, scene['audio']['id'])
        assert recording['sha256'] == scene['audio']['sha256']
        scene['audio']['data_base64'] = base64.b64encode(recording['data']).decode()
    return verify_snapshot_v4(packed)


def fixture_state(directory, runtime):
    """Only called with a new private fixture directory, never a business DB."""
    directory = Path(directory)
    assert not directory.exists(), 'new_fixture_directory_required'
    directory.mkdir(parents=True)
    path = directory / 'fixture-only.sqlite'
    with isolated_environment():
        app = app_for(path, runtime)
        with TestClient(app) as client:
            login(client)
            p = checked(client.post('/api/projects', json=dict(title='FIXTURE ONLY - v4 delivery',
                script='isolated integration fixture', style='fixture', production_mode='static')))
            ids, dynamic = [], []
            for index, kind in enumerate(('static', 'creative', 'motion')):
                fields = dict(title=f'FIXTURE {kind}', narration=f'第{index + 1}段。请求到达。处理完成。',
                    visual_description='A request moves from left to right.', duration_seconds=2, format='svg')
                p = checked(client.post(f'/api/projects/{p["id"]}/scenes', json=fields))
                sid = p['scenes'][-1]['id']
                ids.append(sid)
                p = checked(client.patch(f'/api/scenes/{sid}/timing', json=dict(expected_revision=p['revision'],
                    leading_seconds=.25, trailing_seconds=.25)))
                started = app.state.store.start(OWNER, p['id'], 'scene',
                    dict(expected_revision=p['revision']), 'fixture-static-' + str(index), scene_id=sid)
                app.state.store.complete(started['id'], dict(source=SVG, format='svg', summary='hand-authored fixture'))
                p = project(client, p['id'])
                scene = next(row for row in p['scenes'] if row['id'] == sid)
                p = app.state.store.adopt(OWNER, sid, scene['versions'][-1]['id'], None)
                p = app.state.store.upload_audio(OWNER, sid, dict(expected_revision=p['revision']),
                    'fixture-recording-' + str(index), tone(index), source_name=f'fixture-tone-{index}.wav')
                scene = next(row for row in p['scenes'] if row['id'] == sid)
                aid = scene['audio_versions'][-1]['id']
                app.state.store.adopt_audio(OWNER, sid, aid, None)
                if kind != 'static':
                    p = project(client, p['id'])
                    operation = app.state.motion_store.start(OWNER, sid,
                        dict(expected_revision=p['revision'], diagram_kind='creative' if kind == 'creative' else 'diagram'),
                        'fixture-authored-' + kind)
                    request = app.state.motion_store.operation(operation['id'])
                    if kind == 'creative':
                        result = dict(schema='video.animation-experiment-result.v1', input_sha256=digest(request['document']),
                            prompt_revision=request['document']['prompt_revision'], engine='html-svg',
                            strategy=dict(description='Hand-authored integration fixture; not Agent generated.', tradeoffs=''),
                            files={'scene.html': HTML}, files_sha256={'scene.html': sha(HTML.encode())})
                    else:
                        result = dict(schema='video.motion-result.v1', input_sha256=digest(request['document']),
                                      plan=example_plan(request['document']['beats']))
                    completed = app.state.motion_store.complete(operation['id'], result)
                    p = project(client, p['id'])
                    p = checked(client.post(f'/api/scenes/{sid}/motion/adopt', json=dict(expected_revision=p['revision'],
                        version_id=completed['version_id'], expected_head_version_id=None)))
                    dynamic.append((completed['version_id'], aid))
                p = project(client, p['id'])
            endpoint = f'/api/projects/{p["id"]}/motion-renders'
            rejected = client.post(endpoint, json=dict(expected_revision=p['revision'], purpose='delivery', subtitles=False),
                                   headers={'Idempotency-Key': 'fixture-must-reject-pending'})
            assert rejected.status_code == 409
            assert rejected.json()['detail']['code'] == 'motion_timing_needs_review'
            with app.state.store.transaction() as db:
                assert db.execute('SELECT count(*) FROM video_renders').fetchone()[0] == 0
            # This is test data, not an attestation about the production user.
            assert OWNER.startswith('fixture-') and path.name == 'fixture-only.sqlite'
            for version, audio in dynamic:
                timing = checked(client.get(f'/api/motion-versions/{version}/timing', params=dict(audio_version_id=audio)))
                assert timing['requires_review'] is True
                p = project(client, p['id'])
                checked(client.post(f'/api/motion-versions/{version}/timing/verify', json=dict(
                    expected_revision=p['revision'], audio_version_id=audio,
                    expected_alignment_sha256=timing['alignment_sha256'])))
            p = project(client, p['id'])
            body = dict(expected_revision=p['revision'], purpose='delivery', subtitles=False)
            job = checked(client.post(endpoint, json=body, headers={'Idempotency-Key': 'fixture-delivery'}), 202)
            snapshot = stored_snapshot(app, job['id'])
            assert [s['visual_mode'] for s in snapshot['scenes']] == ['static', 'creative', 'motion']
            assert snapshot['quality'] == 'final' and snapshot['purpose'] == 'delivery'
            assert snapshot['duration_seconds'] == 7.5
            replay = checked(client.post(endpoint, json=body, headers={'Idempotency-Key': 'fixture-delivery'}), 202)
            assert replay['id'] == job['id']
            manifest = dict(schema=SCHEMA, owner=OWNER, project_id=p['id'], render_id=job['id'],
                request=body, scene_ids=ids, runtime=runtime, snapshot_sha256=snapshot['sha256'],
                hand_authored_fixture=True, agent_requests=0, tts_requests=0,
                fixture_timing_confirmation_only=True, production_user_timing_approval=False)
    write_json(directory / 'fixture.json', manifest)
    write_json(directory / 'snapshot.json', snapshot)
    for index, scene in enumerate(snapshot['scenes']):
        target = directory / 'inputs' / f'scene-{index:03d}'
        target.mkdir(parents=True)
        if scene['visual_mode'] == 'static':
            frozen = static_snapshot(snapshot, scene)
            write_json(target / 'snapshot.json', frozen)
        else:
            for name, raw in source_files(scene, runtime).items():
                (target / name).write_bytes(raw)
    return manifest, snapshot


def source_files(scene, runtime):
    if scene['visual_mode'] == 'creative':
        source = scene['creative']
        files = compile_creative_project(source['package'], source['beats'], source['alignment'],
            runtime_fingerprint=runtime, leading_seconds=scene['timing']['leading_seconds'],
            trailing_seconds=scene['timing']['trailing_seconds'], compiler_version=source['timeline']['compiler'])
    else:
        source = scene['motion']
        _, compiler, version = scene_compiler(source['plan'], compiler_version=source['timeline']['compiler'])
        files = compiler(source['plan'], source['beats'], source['alignment'], runtime_fingerprint=runtime,
            leading_seconds=scene['timing']['leading_seconds'], trailing_seconds=scene['timing']['trailing_seconds'],
            compiler_version=version)
    assert {name: sha(raw) for name, raw in files.items()} == source['project_files_sha256']
    return dict(files, **{'audio.wav': base64.b64decode(scene['audio']['data_base64'], validate=True)})


def static_snapshot(snapshot, scene):
    audio = dict(scene['audio'])
    audio['data'] = base64.b64decode(audio.pop('data_base64'), validate=True)
    original = dict(id=scene['id'], title=scene['title'], narration=scene['narration'],
        duration_seconds=scene['timing']['configured_seconds'], visual=scene['visual'], audio=audio,
        leading_seconds=scene['timing']['leading_seconds'], trailing_seconds=scene['timing']['trailing_seconds'])
    frozen = media.freeze_snapshot(snapshot['project_id'], snapshot['project_revision'], snapshot['title'],
                                  [original], subtitles=False)
    frozen['static_renderer'] = scene['static_renderer']
    frozen['sha256'] = sha(media._canonical({k: v for k, v in frozen.items() if k != 'sha256'}))
    return frozen


def collect(directory, *, persist=True):
    """Bind actual external outputs after semantic/receipt verification."""
    root = Path(directory).resolve()
    fixture, snapshot = json_file(root / 'fixture.json'), json_file(root / 'snapshot.json')
    verify_snapshot_v4(snapshot)
    assert fixture['schema'] == SCHEMA and fixture['owner'] == OWNER
    assert fixture['snapshot_sha256'] == snapshot['sha256']
    rows = []
    for index, scene in enumerate(snapshot['scenes']):
        folder = root / 'artifacts' / f'scene-{index:03d}'
        assert folder.is_dir() and not folder.is_symlink()
        entries = {path.relative_to(folder).as_posix(): path.read_bytes() for path in folder.rglob('*') if path.is_file()}
        assert not any(path.is_symlink() for path in folder.rglob('*'))
        video = 'video.mp4' if scene['visual_mode'] == 'static' else 'output.mp4'
        verify_media(folder / video, scene['timing']['duration_seconds'], cwd=folder)
        if scene['visual_mode'] == 'static':
            report = json.loads(entries['report.json'])
            assert report['snapshot_sha256'] == static_snapshot(snapshot, scene)['sha256']
            assert report['sha256'] == sha(entries[video]) and report['static_renderer'] == scene['static_renderer']
            review = dict(status='static_render_report_verified', snapshot_sha256=report['snapshot_sha256'])
        else:
            files = source_files(scene, fixture['runtime'])
            review = validate_artifacts(entries, files, fixture['runtime'], 'final')
        rows.append(dict(index=index, scene_id=scene['id'], visual_mode=scene['visual_mode'],
            video=video, artifact_sha256={name: sha(raw) for name, raw in sorted(entries.items())}, review=review))
    receipt = dict(schema='video.creative-delivery-encoded-fixture.v1', snapshot_sha256=snapshot['sha256'],
        runtime=fixture['runtime'], image_digest=fixture['runtime']['image_digest'], scenes=rows,
        fixture_timing_confirmation_only=True, production_user_timing_approval=False)
    if persist:
        target = root / 'encoded-receipt.json'
        raw = canonical_bytes(receipt) + b'\n'
        if target.exists():
            assert not target.is_symlink() and target.read_bytes() == raw, 'encoded_evidence_changed'
        else:
            with target.open('xb') as stream:
                stream.write(raw)
    return receipt


class RecordedSceneSource:
    """Replay verified real external encodes, never generate substitute clips."""
    def __init__(self, root, snapshot, receipt):
        self.root, self.snapshot, self.receipt = Path(root), snapshot, receipt
        self.runtime, self.calls = receipt['runtime'], []

    def clip(self, index, scene):
        assert scene == self.snapshot['scenes'][index]
        row = self.receipt['scenes'][index]
        folder = self.root / 'artifacts' / f'scene-{index:03d}'
        for name, expected in row['artifact_sha256'].items():
            path = folder / name
            assert path.resolve().is_relative_to(folder.resolve()) and not path.is_symlink()
            assert sha(path.read_bytes()) == expected
        return folder / row['video']

    def render(self, scene, *, scene_index, quality, **kwargs):
        assert quality == 'final'
        self.calls.append(scene_index)
        return dict(path=str(self.clip(scene_index, scene)), technical_review=self.receipt['scenes'][scene_index]['review'])

    def static(self, frozen, directory, **kwargs):
        assert frozen == static_snapshot(self.snapshot, self.snapshot['scenes'][0])
        self.calls.append(0)
        return dict(path=str(self.clip(0, self.snapshot['scenes'][0])))


class LocalComposer:
    def __init__(self, source):
        self.source = source
        self.renderer = MixedMotionRenderer(source, static_renderer=source.static)
        self.calls = []

    def render(self, snapshot, directory, *, operation_id, owner, cancel_event):
        assert owner == OWNER and snapshot == self.source.snapshot
        self.calls.append(operation_id)
        return self.renderer.render(snapshot, directory, key=operation_id, cancel_event=cancel_event)


def samples(raw):
    with wave.open(io.BytesIO(raw), 'rb') as stream:
        assert (stream.getnchannels(), stream.getsampwidth(), stream.getframerate()) == (1, 2, RATE)
        values = array('h', stream.readframes(stream.getnframes()))
    if sys.byteorder != 'little':
        values.byteswap()
    return values


def correlation(left, right):
    assert len(left) == len(right) and len(left) > RATE // 4
    lm, rm = sum(left) / len(left), sum(right) / len(right)
    dot = sum((a - lm) * (b - rm) for a, b in zip(left, right))
    energy = math.sqrt(sum((a - lm) ** 2 for a in left) * sum((b - rm) ** 2 for b in right))
    assert energy > 0
    return dot / energy


def replace_current_adoptions(app, client, snapshot):
    """Change both live media heads; the queued delivery must keep old bytes."""
    p = project(client, snapshot['project_id'])
    scene = p['scenes'][0]
    p = checked(client.patch(f'/api/scenes/{scene["id"]}', json=dict(title='edited after freezing',
        narration='Changed after the immutable delivery request.', visual_description='Changed visual text.',
        duration_seconds=2, format='svg', expected_revision=p['revision'])))
    changed = app.state.store.start(OWNER, p['id'], 'scene', dict(expected_revision=p['revision']),
        'fixture-edit-after-freeze', scene_id=scene['id'])
    app.state.store.complete(changed['id'], dict(source=SVG.replace('#55d8bd', '#ffdd00'),
        format='svg', summary='New adopted visual after the frozen request'))
    p = project(client, p['id'])
    edited = p['scenes'][0]
    p = app.state.store.adopt(OWNER, edited['id'], edited['versions'][-1]['id'], edited['head_version_id'])
    p = app.state.store.upload_audio(OWNER, edited['id'], dict(expected_revision=p['revision']),
        'fixture-audio-after-freeze', tone(1), source_name='replaced-after-freeze.wav')
    edited = p['scenes'][0]
    p = app.state.store.adopt_audio(OWNER, edited['id'], edited['audio_versions'][-1]['id'],
        edited['head_audio_version_id'])
    assert p['scenes'][0]['head_version_id'] != snapshot['scenes'][0]['visual']['id']
    assert p['scenes'][0]['head_audio_version_id'] != snapshot['scenes'][0]['audio']['id']


def test_fixture_delivery_api_freezes_mixed_v4_and_human_gate_remains_explicit(tmp_path):
    from test_motion_snapshot import RUNTIME
    root = tmp_path / 'new-private-fixture'
    manifest, snapshot = fixture_state(root, RUNTIME)
    assert manifest['fixture_timing_confirmation_only'] is True
    assert manifest['production_user_timing_approval'] is False
    assert snapshot['schema'] == 'video.render-snapshot.v4'
    assert [s['audio']['sha256'] for s in snapshot['scenes']] == [sha(tone(i)) for i in range(3)]
    assert all(s['timing']['leading_seconds'] == .25 and s['timing']['trailing_seconds'] == .25 for s in snapshot['scenes'])
    with isolated_environment():
        app = app_for(root / 'fixture-only.sqlite', RUNTIME)
        with TestClient(app) as client:
            login(client)
            replace_current_adoptions(app, client, snapshot)
            assert stored_snapshot(app, manifest['render_id']) == snapshot


def test_real_encoded_creative_delivery_replays_frozen_history_and_original_pcm(tmp_path):
    import pytest
    configured = os.environ.get('VIDEO_CREATIVE_DELIVERY_FIXTURE_DIR')
    if not configured:
        pytest.skip('External exact-image animation evidence not supplied; API-only checks are separate')
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('Real FFmpeg/FFprobe required; no substitute encoder')
    source_root = Path(configured).resolve(strict=True)
    fixture = json_file(source_root / 'fixture.json')
    snapshot = verify_snapshot_v4(json_file(source_root / 'snapshot.json'))
    receipt = json_file(source_root / 'encoded-receipt.json')
    assert fixture['schema'] == SCHEMA and fixture['owner'] == OWNER
    assert receipt['snapshot_sha256'] == snapshot['sha256'] == fixture['snapshot_sha256']
    assert receipt['runtime'] == snapshot['runtime_fingerprint']
    assert fixture['fixture_timing_confirmation_only'] is True and fixture['production_user_timing_approval'] is False
    # Re-run the actual artifact checks, rather than trusting a stale receipt.
    assert collect(source_root, persist=False) == receipt
    path = tmp_path / 'fixture-only.sqlite'
    source_db = source_root / 'fixture-only.sqlite'
    assert not Path(str(source_db) + '-wal').exists(), 'fixture_database_must_be_closed_and_checkpointed'
    assert not Path(str(source_db) + '-journal').exists(), 'fixture_database_must_be_closed_and_checkpointed'
    # Immutable read-only opening cannot create source WAL/SHM sidecars.
    with closing(sqlite3.connect(source_db.as_uri() + '?mode=ro&immutable=1', uri=True)) as original, closing(sqlite3.connect(path)) as copied:
        original.backup(copied)
    source = RecordedSceneSource(source_root, snapshot, receipt)
    composer = LocalComposer(source)
    endpoint = f'/api/projects/{fixture["project_id"]}/motion-renders'
    with isolated_environment():
        app = app_for(path, fixture['runtime'], composer)
        with TestClient(app) as client:
            login(client)
            replay = checked(client.post(endpoint, json=fixture['request'], headers={'Idempotency-Key': 'fixture-delivery'}), 202)
            assert replay['id'] == fixture['render_id'] and composer.calls == []
            before = canonical_bytes(stored_snapshot(app, fixture['render_id']))
            replace_current_adoptions(app, client, snapshot)
            assert canonical_bytes(stored_snapshot(app, fixture['render_id'])) == before
            app.state.production.tick()
            history = checked(client.get(f'/api/projects/{fixture["project_id"]}/renders'))['renders']
            finished = next(row for row in history if row['id'] == fixture['render_id'])
            assert finished['status'] == 'completed', finished
            assert composer.calls == [fixture['render_id']] and source.calls == [0, 1, 2]
            video_url = f'/api/renders/{fixture["render_id"]}/video'
            response = client.get(video_url)
            assert response.status_code == 200 and response.headers['content-type'].startswith('video/mp4')
            raw = response.content
            assert sha(raw) == finished['sha256']
            part = client.get(video_url, headers={'Range': 'bytes=0-255'})
            assert part.status_code == 206 and part.content == raw[:256]
            login(client, 'fixture-unrelated-owner')
            assert client.get(video_url).status_code == 404
            assert client.get(video_url, headers={'Range': 'bytes=0-255'}).status_code == 404
            login(client)
            assert checked(client.post(endpoint, json=fixture['request'], headers={'Idempotency-Key': 'fixture-delivery'}), 202)['id'] == fixture['render_id']
            assert composer.calls == [fixture['render_id']]
        restarted = app_for(path, fixture['runtime'], NeverRender())
        with TestClient(restarted) as client:
            login(client)
            assert client.get(video_url).content == raw
            assert checked(client.post(endpoint, json=fixture['request'], headers={'Idempotency-Key': 'fixture-delivery'}), 202)['id'] == fixture['render_id']
            assert canonical_bytes(stored_snapshot(restarted, fixture['render_id'])) == before
    output = tmp_path / 'actual-delivery.mp4'
    output.write_bytes(raw)
    probe = verify_media(output, snapshot['duration_seconds'], cwd=tmp_path)
    assert len([s for s in probe['streams'] if s['codec_type'] == 'audio']) == 1
    decoded = tmp_path / 'decoded.wav'
    media._run([shutil.which('ffmpeg'), '-v', 'error', '-nostdin', '-y', '-i', str(output),
        '-map', '0:a:0', '-c:a', 'pcm_s16le', str(decoded)], cwd=tmp_path, timeout=30)
    actual = samples(decoded.read_bytes())
    cursor, correlations = 0, []
    for index, scene in enumerate(snapshot['scenes']):
        start = cursor + round(scene['timing']['leading_seconds'] * RATE)
        expected = samples(base64.b64decode(scene['audio']['data_base64'], validate=True))
        observed = actual[start:start + len(expected)]
        score = correlation(expected, observed)
        middle = correlation(expected[RATE // 4:-RATE // 4], observed[RATE // 4:-RATE // 4])
        wrong = correlation(samples(tone((index + 1) % 3)), observed)
        assert score > .99 and middle > .99 and abs(wrong) < .1, (index, score, middle, wrong)
        correlations.append(dict(scene=index, full=score, middle=middle, wrong_recording=wrong))
        # Padding remains silent away from the AAC transient at its boundary.
        pad = actual[cursor + RATE // 20:start - RATE // 20]
        assert pad and math.sqrt(sum(v * v for v in pad) / len(pad)) < 100
        end = cursor + round(scene['timing']['duration_seconds'] * RATE)
        tail = actual[start + len(expected) + RATE // 20:end - RATE // 20]
        assert tail and math.sqrt(sum(v * v for v in tail) / len(tail)) < 100
        cursor = end
    write_json(tmp_path / 'delivery-integration-result.json', dict(status='passed',
        snapshot_sha256=snapshot['sha256'], media_sha256=sha(raw), correlations=correlations,
        source_image_digest=receipt['image_digest'], fixture_timing_confirmation_only=True,
        production_user_timing_approval=False, agent_requests=0, tts_requests=0))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('prepare', 'collect', 'render-static'))
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--runtime', type=Path)
    args = parser.parse_args()
    if args.operation == 'prepare':
        assert args.runtime is not None
        fixture, _ = fixture_state(args.directory, json_file(args.runtime))
        print(json.dumps(fixture, sort_keys=True))
    elif args.operation == 'render-static':
        snapshot = json_file(args.directory / 'inputs/scene-000/snapshot.json')
        result = media.render_snapshot(snapshot, args.directory / 'artifacts/scene-000')
        print(json.dumps({key: value for key, value in result.items() if key != 'path'}, sort_keys=True))
    else:
        print(json.dumps(collect(args.directory), sort_keys=True))


if __name__ == '__main__':
    main()
