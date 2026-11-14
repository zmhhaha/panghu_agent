"""Offline verified project -> full frames -> one MP4 with the original audio clock.

No Agent, business adoption, Job submission or production admission runs here.
The initial exporter accepts at most 60 seconds; chunked export is a later unit.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time

from agent_service.contracts import ServiceError, canonical_bytes, digest
from .hyperframes_film_audio import mix_film_audio
from .hyperframes_film_browser import verify_film_browser_bundle

SCHEMA = 'video.hyperframes-film-offline-export.v1'
MAX_FRAMES, MAX_MEDIA_BYTES = 1440, 128 * 1024 * 1024


def _require(value, code='hyperframes_film_export_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _file_sha(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            checksum.update(chunk)
    return checksum.hexdigest()


def _binary(name):
    _require(type(name) is str and bool(name.strip()))
    selected = shutil.which(name)
    _require(selected is not None, 'hyperframes_film_export_binary_unavailable', 503)
    path = Path(selected).resolve(strict=True)
    _require(path.is_file(), 'hyperframes_film_export_binary_unavailable', 503)
    return path


def _run(command, *, cwd, deadline):
    remaining = deadline - time.monotonic()
    _require(remaining > 0, 'hyperframes_film_export_timeout', 504)
    try:
        process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            **({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt'
               else {'start_new_session': True}))
    except OSError:
        raise ServiceError('hyperframes_film_export_process_unavailable', 503) from None
    try:
        stdout, stderr = process.communicate(timeout=remaining)
    except BaseException as error:
        # Killing Node alone skips its browser cleanup. Terminate the owned
        # process tree before removing any of the temporary project resources.
        if os.name == 'nt':
            killer = Path(os.environ['SystemRoot']) / 'System32/taskkill.exe'
            subprocess.run([str(killer), '/PID', str(process.pid), '/T', '/F'],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=15, check=False)
        else:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=15)
        if isinstance(error, subprocess.TimeoutExpired):
            raise ServiceError('hyperframes_film_export_timeout', 504) from None
        raise
    if process.returncode != 0:
        error = ServiceError('hyperframes_film_export_process_failed', 502)
        error.process_diagnostic = stderr[-4096:].decode('utf-8', errors='replace')
        raise error
    _require(len(stdout) <= 4 * 1024 * 1024,
        'hyperframes_film_export_output_budget', 502)
    return stdout


def _validate_frames(receipt, work, bundle, checksum, project):
    count, config = project['clock']['frame_count'], bundle['root_config']
    _require(type(receipt) is dict and receipt.get('schema') == 'video.hyperframes-film-frame-capture.v1'
        and receipt.get('bundle_sha256') == checksum
        and receipt.get('project_manifest_sha256') == bundle['project_manifest_sha256']
        and receipt.get('film_sha256') == project['film_sha256']
        and receipt.get('fps') == 24 and receipt.get('frame_count') == count
        and receipt.get('width') == config['width'] and receipt.get('height') == config['height'],
        'hyperframes_film_export_capture_mismatch', 502)
    frames = receipt.get('frames')
    _require(type(frames) is list and len(frames) == count,
        'hyperframes_film_export_capture_incomplete', 502)
    expected, total = set(), 0
    for index, frame in enumerate(frames):
        name = 'frame-' + str(index).zfill(6) + '.png'
        path = work / 'frames' / name
        _require(type(frame) is dict and frame.get('frame') == index and frame.get('path') == name
            and path.is_file() and not path.is_symlink() and path.stat().st_size <= 12 * 1024 * 1024,
            'hyperframes_film_export_frame_invalid', 502)
        raw = path.read_bytes()
        _require(raw.startswith(b'\x89PNG\r\n\x1a\n') and len(raw) == frame.get('bytes')
            and _sha(raw) == frame.get('sha256'), 'hyperframes_film_export_frame_changed', 409)
        total += len(raw)
        _require(total <= 512 * 1024 * 1024, 'hyperframes_film_export_frame_budget', 502)
        expected.add(name)
    _require({path.name for path in (work / 'frames').iterdir()} == expected
        and receipt.get('frame_bytes') == total, 'hyperframes_film_export_frame_inventory', 502)
    probe = receipt.get('probe', {})
    _require(type(probe) is dict, 'hyperframes_film_export_probe_incomplete', 502)
    observations = probe.get('observations', [])
    order = [*range(count), count - 1, 0, count // 2]
    _require(probe.get('bundle_sha256') == checksum
        and probe.get('core_sha256') == config['hyperframes_core_sha256']
        and type(observations) is list and len(observations) == len(order)
        and probe.get('service_worker_accessor_disabled') is True
        and probe.get('errors') == [] and probe.get('blocked') == []
        and probe.get('cleanup', {}).get('remaining_iframes') == 0
        and probe.get('cleanup', {}).get('pending_cleanup_modules') == 0,
        'hyperframes_film_export_probe_incomplete', 502)
    modules = {row['module_id']: row for row in project['modules']}
    previous_dispatches = 0
    for index, frame in enumerate(order):
        row = observations[index]
        _require(type(row) is dict and row.get('frame_sha256') == frames[frame]['sha256']
            and row.get('requested_frame') == frame and row.get('completed_frame') == frame
            and type(row.get('actual_seconds')) in (int, float)
            and abs(row['actual_seconds'] - frame / 24) <= 1e-7
            and row.get('manifest_sha256') == bundle['project_manifest_sha256']
            and row.get('film_sha256') == project['film_sha256']
            and row.get('core_seek_count') == index + 1
            and type(row.get('dispatch_count')) is int and row['dispatch_count'] > previous_dispatches,
            'hyperframes_film_export_probe_mismatch', 502)
        previous_dispatches = row['dispatch_count']
        active = {}
        for scene in project['scenes']:
            if scene['active_start_frame'] <= frame < scene['active_end_frame']:
                active[scene['module_id']] = dict(global_frame=frame, local_frame=frame - scene['start_frame'],
                    phase='entering' if frame < scene['start_frame'] else 'exiting' if frame >= scene['end_frame'] else 'active')
        for boundary in project['boundaries']:
            transition = boundary['transition']
            if transition and transition['start_frame'] <= frame < transition['end_frame']:
                active[transition['module_id']] = dict(global_frame=frame,
                    local_frame=frame - transition['start_frame'], phase='active')
        realms = row.get('realms')
        _require(type(realms) is list and len(realms) == len(active),
            'hyperframes_film_export_realm_mismatch', 502)
        seen = set()
        for realm in realms:
            mid = realm.get('module_id') if type(realm) is dict else None
            _require(mid in active and mid not in seen and realm.get('module_sha256') == modules[mid]['module_sha256']
                and realm.get('sandbox') == 'allow-scripts' and realm.get('realm_origin') == 'null'
                and realm.get('context') == active[mid], 'hyperframes_film_export_realm_mismatch', 502)
            observation = realm.get('observation')
            _require(type(observation) is dict and observation.get('engine') == modules[mid]['engine']
                and observation.get('revision') == 'film-opaque-realm-guard-1'
                and observation.get('pending_animation_frames') == 0
                and observation.get('service_worker_available') is False,
                'hyperframes_film_export_engine_mismatch', 502)
            contexts = observation.get('contexts')
            _require(type(contexts) is list, 'hyperframes_film_export_engine_mismatch', 502)
            engine = modules[mid]['engine']
            if engine != 'html-svg':
                _require(any(type(value) is dict and value.get('kind') == ('2d' if engine == 'canvas' else 'webgl2')
                    and value.get('connected') is True and value.get('visible') is True
                    and type(value.get('width')) is int and 0 < value['width'] <= 4096
                    and type(value.get('height')) is int and 0 < value['height'] <= 4096
                    and (engine != 'three' or (value.get('software') is True
                        and value.get('context_lost') is False and value.get('gl_error') == 0)) for value in contexts),
                    'hyperframes_film_export_native_context_missing', 502)
            seen.add(mid)


@dataclass(frozen=True)
class FilmExport:
    data: bytes
    receipt: dict
    capture: dict
    audio_mix: dict


def export_film_browser_bundle(files, *, expected_bundle_sha256, scratch_dir,
        node='node', ffmpeg='ffmpeg', ffprobe='ffprobe', playwright_module=None,
        chromium_path=None, hyperframes_dist=None, timeout_seconds=600):
    """Render one frozen project, encode once, decode-check and return new bytes.

    The external bundle digest and release-owned hosts must be selected at the
    caller's trusted boundary. This function verifies bytes, not authorization.
    It never updates adoption, history or production capabilities.
    """
    _require(type(timeout_seconds) in (int, float) and 0 < timeout_seconds <= 1200)
    _require(type(files) is dict)
    frozen = dict(files)
    bundle = verify_film_browser_bundle(frozen, expected_bundle_sha256=expected_bundle_sha256)
    project_files = {name[len('project/'):]: raw for name, raw in frozen.items() if name.startswith('project/')}
    project = json.loads(project_files['manifest.json'])
    count, config = project['clock']['frame_count'], bundle['root_config']
    _require(0 < count <= MAX_FRAMES, 'hyperframes_film_export_duration_budget')
    # yuv420p's even dimensions are a delivery requirement, not a silent crop.
    _require(config['width'] % 2 == 0 and config['height'] % 2 == 0,
        'hyperframes_film_export_even_dimensions_required')
    root = Path(scratch_dir)
    _require(root.is_absolute() and root.is_dir() and not root.is_symlink() and root.resolve() == root,
        'hyperframes_film_export_scratch_invalid')
    binaries = {name: _binary(value) for name, value in dict(node=node, ffmpeg=ffmpeg, ffprobe=ffprobe).items()}
    runner = Path(__file__).parent / 'motion_renderer/hyperframes_film_capture.mjs'
    _require(runner.is_file() and not runner.is_symlink(), 'hyperframes_film_export_runner_missing', 503)
    identities = {name: _file_sha(path) for name, path in {**binaries, 'capture_entry': runner}.items()}
    deadline = time.monotonic() + timeout_seconds
    with tempfile.TemporaryDirectory(prefix='hf-film-export-', dir=root) as temporary:
        work = Path(temporary)
        for name, raw in frozen.items():
            target = work / 'bundle' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                stream.write(raw)
        audio = mix_film_audio(project_files, expected_manifest_sha256=bundle['project_manifest_sha256'],
            scratch_dir=work, ffmpeg=str(binaries['ffmpeg']),
            timeout_seconds=min(120, max(.001, deadline - time.monotonic())))
        (work / 'delivery.wav').write_bytes(audio.data)
        command_config = dict(bundleRoot=str(work / 'bundle'), expectedBundleSha256=expected_bundle_sha256,
            framesRoot=str(work / 'frames'))
        for key, value in dict(playwright_module=playwright_module, executablePath=chromium_path,
                hyperFramesDist=hyperframes_dist).items():
            if value is not None:
                _require(type(value) is str and bool(value))
                command_config[key] = value
        (work / 'capture-config.json').write_bytes(canonical_bytes(command_config))
        try:
            capture = json.loads(_run([str(binaries['node']), str(runner), str(work / 'capture-config.json')],
                cwd=work, deadline=deadline))
        except (ValueError, UnicodeError):
            raise ServiceError('hyperframes_film_export_capture_invalid', 502) from None
        _validate_frames(capture, work, bundle, expected_bundle_sha256, project)
        _run([str(binaries['ffmpeg']), '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
            '-threads', '1', '-filter_threads', '1', '-framerate', '24', '-start_number', '0',
            '-i', 'frames/frame-%06d.png', '-i', 'delivery.wav', '-map', '0:v:0', '-map', '1:a:0',
            '-frames:v', str(count), '-c:v', 'libx264', '-threads:v', '1', '-crf', '20', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2', '-map_metadata', '-1',
            '-movie_timescale', '48000', '-movflags', '+faststart', 'film.mp4'], cwd=work, deadline=deadline)
        output = work / 'film.mp4'
        _require(output.is_file() and not output.is_symlink() and 0 < output.stat().st_size <= MAX_MEDIA_BYTES,
            'hyperframes_film_export_media_invalid', 502)
        raw = output.read_bytes()
        try:
            media = json.loads(_run([str(binaries['ffprobe']), '-v', 'error', '-count_frames',
                '-show_streams', '-of', 'json', 'film.mp4'], cwd=work, deadline=deadline))
            streams = media['streams']
            video, sound = [row for row in streams if row['codec_type'] == 'video'], [row for row in streams if row['codec_type'] == 'audio']
            _require(len(streams) == 2 and len(video) == len(sound) == 1)
            v, a = video[0], sound[0]
            _require(v['codec_name'] == 'h264' and int(v['nb_read_frames']) == count
                and (v['width'], v['height']) == (config['width'], config['height'])
                and Fraction(v['avg_frame_rate']) == 24 and Fraction(v['start_time']) == 0
                and Fraction(v['duration_ts']) * Fraction(v['time_base']) == Fraction(count, 24)
                and a['codec_name'] == 'aac' and int(a['sample_rate']) == 48000 and a['channels'] == 2
                and Fraction(a['start_time']) == 0
                and Fraction(a['duration_ts']) * Fraction(a['time_base']) == Fraction(count, 24),
                'hyperframes_film_export_media_timing_mismatch', 502)
        except (KeyError, ValueError, TypeError, ZeroDivisionError):
            raise ServiceError('hyperframes_film_export_media_metadata_invalid', 502) from None
        _run([str(binaries['ffmpeg']), '-v', 'error', '-xerror', '-nostdin', '-threads', '1',
            '-i', 'film.mp4', '-map', '0:v:0', '-map', '0:a:0', '-f', 'null', '-'], cwd=work, deadline=deadline)
        _require(_sha((work / 'delivery.wav').read_bytes()) == audio.receipt['output']['sha256'],
            'hyperframes_film_export_audio_changed', 409)
        for name, source in {**binaries, 'capture_entry': runner}.items():
            _require(_file_sha(source) == identities[name], 'hyperframes_film_export_binary_changed', 409)
        for name, content in frozen.items():
            _require(_file_sha(work / 'bundle' / name) == _sha(content),
                'hyperframes_film_export_input_changed', 409)
    receipt = dict(schema=SCHEMA, status='exported', bundle_sha256=expected_bundle_sha256,
        project_manifest_sha256=bundle['project_manifest_sha256'], film_sha256=project['film_sha256'],
        clock=dict(fps=24, frame_count=count, sample_rate=48000, sample_count=count * 2000),
        capture_sha256=digest(capture), audio_mix_sha256=audio.receipt['sha256'],
        delivery_wav_sha256=audio.receipt['output']['sha256'], binaries=identities,
        media=dict(format='mp4', video='h264', audio='aac', width=config['width'], height=config['height'],
            bytes=len(raw), sha256=_sha(raw), full_decode_passed=True, starts_at_zero=True),
        sound_effects_mixed=False, concatenated_scene_videos=False,
        evidence_scope='actual_offline_whole_project_export_not_agent_quality_or_production_admission')
    receipt['sha256'] = digest(receipt)
    return FilmExport(raw, receipt, capture, audio.receipt)
