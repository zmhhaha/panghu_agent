"""Real FFmpeg encoding with synthetic frame transport, not browser/core proof."""
from copy import deepcopy
from fractions import Fraction
import hashlib
import io
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time
import wave
import zlib

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench import hyperframes_film_export as E
from video_workbench.hyperframes_film_browser import HOST_NAMES, compile_film_browser_bundle
from video_workbench.hyperframes_film_project import compile_film_project, module_identity
from test_hyperframes_film_project import inputs

ROOT = Path(__file__).resolve().parents[2]


def fixture(count):
    value = inputs()
    if count == 24:
        value['film']['scenes'] = value['film']['scenes'][:1]
        value['modules'] = value['modules'][:1]
        value['boundaries'] = []
        stream = io.BytesIO()
        with wave.open(stream, 'wb') as out:
            out.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            out.writeframes(b''.join(struct.pack('<h', round(8000 * math.sin(index * math.tau * 440 / 24000)))
                for index in range(24000)))
        raw = stream.getvalue()
        row = value['film']['scenes'][0]
        row.update(audio_seconds=1, end_seconds=1, leading_silence_seconds=0,
            trailing_silence_seconds=0, audio_sha256=hashlib.sha256(raw).hexdigest())
        value['film']['total_seconds'] = 1
        value['recordings'] = [dict(scene_id=row['scene_id'], audio_version_id=row['audio_version_id'],
            sha256=row['audio_sha256'], spoken_text=row['narration'], data=raw)]
        value['film_sha256'] = digest(value['film'])
        value['modules'][0]['film_sha256'] = value['film_sha256']
    project = compile_film_project(**value)
    assert json.loads(project['manifest.json'])['clock']['frame_count'] == count
    files = compile_film_browser_bundle(project,
        expected_project_manifest_sha256=hashlib.sha256(project['manifest.json']).hexdigest(),
        width=640, height=360,
        host_files={name: (ROOT / 'video_workbench/motion_renderer' / name).read_bytes() for name in HOST_NAMES})
    return files, hashlib.sha256(files['browser-manifest.json']).hexdigest()


def png():
    def chunk(name, data):
        return struct.pack('>I', len(data)) + name + data + struct.pack('>I', zlib.crc32(name + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 640, 360, 8, 2, 0, 0, 0))
        + chunk(b'IDAT', zlib.compress((b'\0' + b'\x20\x60\xa0' * 640) * 360)) + chunk(b'IEND', b''))


def capture_double(monkeypatch, *, mutate=None):
    real_run = E._run

    def run(command, *, cwd, deadline):
        if len(command) < 2 or Path(command[1]).name != 'hyperframes_film_capture.mjs':
            return real_run(command, cwd=cwd, deadline=deadline)
        config = json.loads(Path(command[2]).read_bytes())
        root, frames_root = Path(config['bundleRoot']), Path(config['framesRoot'])
        bundle = json.loads((root / 'browser-manifest.json').read_bytes())
        project = json.loads((root / 'project/manifest.json').read_bytes())
        count = project['clock']['frame_count']
        frames_root.mkdir()
        raw, frames = png(), []
        checksum = hashlib.sha256(raw).hexdigest()
        for index in range(count):
            name = 'frame-' + str(index).zfill(6) + '.png'
            (frames_root / name).write_bytes(raw)
            frames.append(dict(frame=index, path=name, bytes=len(raw), sha256=checksum))
        modules = {row['module_id']: row for row in project['modules']}
        observations = []
        for ordinal, frame in enumerate([*range(count), count - 1, 0, count // 2]):
            realms = []
            active = [(row['module_id'], row['start_frame'],
                'entering' if frame < row['start_frame'] else 'exiting' if frame >= row['end_frame'] else 'active')
                for row in project['scenes'] if row['active_start_frame'] <= frame < row['active_end_frame']]
            active += [(row['transition']['module_id'], row['transition']['start_frame'], 'active')
                for row in project['boundaries'] if row['transition']
                and row['transition']['start_frame'] <= frame < row['transition']['end_frame']]
            for mid, start, phase in active:
                module = modules[mid]
                realms.append(dict(module_id=mid, module_sha256=module['module_sha256'], sandbox='allow-scripts',
                    realm_origin='null', context=dict(global_frame=frame, local_frame=frame - start, phase=phase),
                    observation=dict(revision='film-opaque-realm-guard-1', engine=module['engine'],
                        pending_animation_frames=0, service_worker_available=False,
                        contexts=[] if module['engine'] == 'html-svg' else [dict(kind='2d', width=640, height=360,
                            connected=True, visible=True)])))
            observations.append(dict(requested_frame=frame, completed_frame=frame, actual_seconds=frame / 24,
                manifest_sha256=bundle['project_manifest_sha256'], film_sha256=project['film_sha256'],
                core_seek_count=ordinal + 1, dispatch_count=ordinal + 1, frame_sha256=checksum, realms=realms))
        receipt = dict(schema='video.hyperframes-film-frame-capture.v1',
            bundle_sha256=config['expectedBundleSha256'], project_manifest_sha256=bundle['project_manifest_sha256'],
            film_sha256=project['film_sha256'], fps=24, width=640, height=360,
            frame_count=count, frame_bytes=len(raw) * count, frames=frames,
            probe=dict(bundle_sha256=config['expectedBundleSha256'],
                core_sha256=bundle['root_config']['hyperframes_core_sha256'], observations=observations,
                errors=[], blocked=[], service_worker_accessor_disabled=True,
                cleanup=dict(remaining_iframes=0, pending_cleanup_modules=0)))
        if mutate: mutate(receipt, cwd)
        return canonical_bytes(receipt)

    monkeypatch.setattr(E, '_run', run)


@pytest.mark.parametrize('count', [24, 56])
def test_actual_single_mp4_and_mixed_rate_audio_have_exact_duration_and_cleanup(count, tmp_path, monkeypatch):
    if not all(shutil.which(name) for name in ('node', 'ffmpeg', 'ffprobe')):
        pytest.skip('Existing Node and FFmpeg are required; nothing is downloaded.')
    files, checksum = fixture(count)
    original = dict(files)
    sentinel = tmp_path / 'film.mp4'
    sentinel.write_bytes(b'caller-owned')
    capture_double(monkeypatch)
    result = E.export_film_browser_bundle(files, expected_bundle_sha256=checksum, scratch_dir=tmp_path)
    assert files == original and set(tmp_path.iterdir()) == {sentinel}
    assert sentinel.read_bytes() == b'caller-owned'
    assert result.receipt['clock'] == dict(fps=24, frame_count=count, sample_rate=48000, sample_count=count * 2000)
    assert result.receipt['media']['sha256'] == hashlib.sha256(result.data).hexdigest()
    assert result.receipt['media']['full_decode_passed'] is True
    assert result.receipt['concatenated_scene_videos'] is False
    assert result.receipt['sha256'] == digest({k: v for k, v in result.receipt.items() if k != 'sha256'})
    assert result.receipt['audio_mix_sha256'] == result.audio_mix['sha256']
    if count == 24:
        output = tmp_path / 'actual-encoding.mp4'
        output.write_bytes(result.data)
        signal = subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-i', str(output), '-map', '0:a:0',
            '-ac', '1', '-f', 's16le', '-'], capture_output=True, check=True).stdout
        samples = struct.unpack('<' + 'h' * (len(signal) // 2), signal)
        # The last 20ms of a voice that ends at the film boundary must survive AAC.
        tail = samples[47040:48000]
        assert len(samples) >= 48000 and math.sqrt(sum(x * x for x in tail) / len(tail)) > 3000


@pytest.mark.parametrize('change', ['time', 'frame', 'project', 'module', 'context', 'extra', 'missing', 'bytes', 'late_error'])
def test_invalid_frame_or_clock_evidence_fails_and_cleans_its_owned_directory(change, tmp_path, monkeypatch):
    if not all(shutil.which(name) for name in ('node', 'ffmpeg', 'ffprobe')):
        pytest.skip('Existing runtime tools required.')
    files, checksum = fixture(24)
    def mutate(receipt, work):
        row = receipt['probe']['observations'][0]
        if change == 'time': row['actual_seconds'] = .5
        elif change == 'frame': row['requested_frame'] = 1
        elif change == 'project': row['manifest_sha256'] = 'f' * 64
        elif change == 'module': row['realms'][0]['module_sha256'] = 'f' * 64
        elif change == 'context': row['realms'][0]['context']['local_frame'] = 1
        elif change == 'extra': (work / 'frames/extra').write_bytes(b'not-listed')
        elif change == 'missing': (work / 'frames/frame-000023.png').unlink()
        elif change == 'bytes': (work / 'frames/frame-000000.png').write_bytes(b'changed')
        else: receipt['probe']['errors'].append('late-failure')
    capture_double(monkeypatch, mutate=mutate)
    with pytest.raises(ServiceError):
        E.export_film_browser_bundle(files, expected_bundle_sha256=checksum, scratch_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_timeout_terminates_a_real_child_process_tree_before_returning(tmp_path):
    psutil = pytest.importorskip('psutil')
    script = tmp_path / 'parent.py'
    child_pid = tmp_path / 'child.pid'
    script.write_text("import subprocess,sys,time\nfrom pathlib import Path\n"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
        "Path('child.pid').write_text(str(child.pid))\ntime.sleep(60)\n", encoding='utf-8')
    with pytest.raises(ServiceError, match='hyperframes_film_export_timeout'):
        E._run([sys.executable, str(script)], cwd=tmp_path, deadline=time.monotonic() + 1.5)
    assert child_pid.exists()
    pid = int(child_pid.read_text())
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() in {psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD}


def test_encoded_audio_shorter_than_film_preserves_probe_in_failure_and_cleans(tmp_path, monkeypatch):
    if not all(shutil.which(name) for name in ('node', 'ffmpeg', 'ffprobe')):
        pytest.skip('Existing runtime tools required.')
    files, checksum = fixture(24)
    capture_double(monkeypatch)
    capture_run = E._run
    def run(command, *, cwd, deadline):
        raw = capture_run(command, cwd=cwd, deadline=deadline)
        if Path(command[0]).name.lower().startswith('ffprobe'):
            media = json.loads(raw)
            audio, = [row for row in media['streams'] if row['codec_type'] == 'audio']
            audio['duration_ts'] -= 1024
            return canonical_bytes(media)
        return raw
    monkeypatch.setattr(E, '_run', run)
    with pytest.raises(ServiceError, match='hyperframes_film_export_media_timing_mismatch') as caught:
        E.export_film_browser_bundle(files, expected_bundle_sha256=checksum, scratch_dir=tmp_path)
    audio, = [row for row in caught.value.media_probe['streams'] if row['codec_type'] == 'audio']
    assert Fraction(audio['duration_ts']) * Fraction(audio['time_base']) < 1
    assert list(tmp_path.iterdir()) == []


def test_wrong_bundle_digest_stops_before_launching_any_process(tmp_path, monkeypatch):
    files, _ = fixture(24)
    monkeypatch.setattr(E, '_run', lambda *args, **kwargs: pytest.fail('must not launch'))
    with pytest.raises(ServiceError):
        E.export_film_browser_bundle(files, expected_bundle_sha256='f' * 64, scratch_dir=tmp_path)
    assert list(tmp_path.iterdir()) == []
