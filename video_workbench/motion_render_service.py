"""Independent, internal production renderer; run with one Uvicorn process.

The business service submits frozen bytes with a stable ID and an owner digest.
This service alone owns execution credentials and its persistent journal/PVC.
"""
from __future__ import annotations

import base64
from contextlib import asynccontextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import threading
import time
import wave

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from agent_service.contracts import ServiceError, canonical_bytes
from . import media
from .motion_snapshot import _runtime
from .continuity_snapshot import verify_motion_snapshot
from .motion_render_adapter import read_secret, safe_progress, safe_diagnostic, safe_motion_context, UNAVAILABLE
from .motion_render_k8s import ExecutionJournal, KubeHTTP, KubeSceneRenderer, operation_key, require

MAX_REQUEST_BYTES = 192 * 1024 * 1024
MAX_STORAGE_BYTES = 8 * 1024 * 1024 * 1024
FILM_SCHEMA = 'video.hyperframes-film-execution-snapshot.v1'
FILM_RESERVATION_BYTES = 768 * 1024 * 1024


def verify_media(path, seconds, *, cwd, cancel_event=None, audio=True):
    ffprobe = media._binary('ffprobe', os.environ.get('VIDEO_FFPROBE_BIN'))
    ffmpeg = media._binary('ffmpeg', os.environ.get('VIDEO_FFMPEG_BIN'))
    require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= media.MAX_VIDEO_BYTES,
            'motion_media_output_invalid')
    try:
        raw = media._run([ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)],
                          cwd=cwd, timeout=30, cancel_event=cancel_event)
        probe = json.loads(raw)
        videos = [v for v in probe['streams'] if v['codec_type'] == 'video']
        audios = [v for v in probe['streams'] if v['codec_type'] == 'audio']
        require(len(videos) == 1, 'motion_media_output_invalid')
        video = videos[0]
        n, d = map(int, video['avg_frame_rate'].split('/'))
        require(video['codec_name'] == 'h264' and (video['width'], video['height']) == (1280, 720)
                and d > 0 and n / d == 24 and int(video['nb_frames']) == round(seconds * 24)
                and abs(float(video['duration']) - seconds) <= .002
                and abs(float(probe['format']['duration']) - seconds) <= .15,
                'motion_media_output_invalid')
        if audio:
            require(len(audios) == 1 and audios[0]['codec_name'] == 'aac'
                    and abs(float(audios[0]['duration']) - seconds) <= .12,
                    'motion_media_output_invalid')
        else:
            require(not audios, 'motion_media_output_invalid')
        media._run([ffmpeg, '-v', 'error', '-nostdin', '-xerror', '-i', str(path), '-f', 'null', '-'],
                   cwd=cwd, timeout=180, cancel_event=cancel_event)
        return probe
    except (ValueError, TypeError, KeyError, ZeroDivisionError):
        raise ServiceError('motion_media_output_invalid', 502) from None


class MixedMotionRenderer:
    """Compose exact validated scene video with one original-PCM master track."""
    def __init__(self, scene_renderer, *, static_renderer=None):
        self.scene_renderer = scene_renderer
        self.static_renderer = static_renderer or media.render_snapshot

    def render(self, snapshot, output_dir, *, key, cancel_event=None):
        verify_motion_snapshot(snapshot)
        require(snapshot['runtime_fingerprint'] == self.scene_renderer.runtime,
                'motion_render_runtime_conflict', 409)
        if cancel_event is not None and cancel_event.is_set():
            self.scene_renderer.cancel_operation(snapshot, key, cancel_event)
            raise ServiceError('media_render_cancelled', 409)
        root = Path(output_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        # Owned scratch is reconstructed after a restart; scene Jobs/evidence
        # remain outside this directory and are resumed by their durable IDs.
        work = root / 'assets'
        if work.exists():
            require(work.is_dir() and not work.is_symlink(), 'motion_scratch_invalid')
            shutil.rmtree(work)
        work.mkdir()
        ffmpeg = media._binary('ffmpeg', os.environ.get('VIDEO_FFMPEG_BIN'))
        common = [ffmpeg, '-v', 'error', '-nostdin', '-y', '-threads', '2', '-filter_threads', '2',
                  '-filter_complex_threads', '2']
        started, concat, checks = time.monotonic(), [], []
        total = len(snapshot['scenes'])
        def progress(stage, index=None, completed=0):
            report = getattr(cancel_event, 'report_progress', None)
            if report is not None:
                report(stage, scene_index=index, completed_scenes=completed)
        def run(args, timeout=180):
            return media._run(args, cwd=work, timeout=timeout, cancel_event=cancel_event)
        with wave.open(str(work / 'master.wav'), 'wb') as master:
            master.setparams((1, 2, media.AUDIO_RATE, 0, 'NONE', 'not compressed'))
            for index, scene in enumerate(snapshot['scenes']):
                media._cancel(cancel_event)
                progress('preparing_scene', index, index)
                seconds, name = scene['timing']['duration_seconds'], f'scene-{index:03d}'
                if scene['visual_mode'] in {'motion', 'creative'}:
                    result = self.scene_renderer.render(scene, key=key, scene_index=index,
                        quality=snapshot['quality'], cancel_event=cancel_event)
                    source = Path(result['path']).resolve()
                    checks.append({k: v for k, v in result.items() if k != 'path'})
                else:
                    audio = dict(scene['audio'])
                    audio['data'] = base64.b64decode(audio.pop('data_base64'), validate=True)
                    original = dict(id=scene['id'], title=scene['title'], narration=scene['narration'],
                        duration_seconds=scene['timing']['configured_seconds'], visual=scene['visual'], audio=audio,
                        leading_seconds=scene['timing']['leading_seconds'], trailing_seconds=scene['timing']['trailing_seconds'])
                    frozen = media.freeze_snapshot(snapshot['project_id'], snapshot['project_revision'],
                                                    snapshot['title'], [original], subtitles=False)
                    frozen['static_renderer'] = scene['static_renderer']
                    frozen['sha256'] = media._sha(media._canonical({k: v for k, v in frozen.items() if k != 'sha256'}))
                    result = self.static_renderer(frozen, work / (name + '-static'), cancel_event=cancel_event)
                    source = Path(result['path']).resolve()
                progress('validating_scene', index, index)
                verify_media(source, seconds, cwd=work, cancel_event=cancel_event)
                filters = ['fps=24']
                if snapshot['subtitles'] and scene['captions']['cues']:
                    (work / (name + '.srt')).write_text(media._srt(scene['captions']['cues']), encoding='utf-8')
                    filters.append(f"subtitles={name}.srt:force_style='FontName=Noto Sans CJK SC,FontSize=22,Outline=1,MarginV=18'")
                # Normalize every clip's H264 profile/timebase before concat;
                # disregard AAC from per-scene validation encodes completely.
                progress('normalizing_scene', index, index)
                run(common + ['-i', str(source), '-map', '0:v:0', '-an', '-vf', ','.join(filters),
                    '-t', f'{seconds:.9f}', '-c:v', 'libx264', '-preset', 'veryfast', '-crf',
                    '18' if snapshot['quality'] == 'final' else '25', '-pix_fmt', 'yuv420p',
                    '-r', '24', '-video_track_timescale', '12288', name + '.mp4'])
                verify_media(work / (name + '.mp4'), seconds, cwd=work, cancel_event=cancel_event, audio=False)
                concat.append(f"file '{name}.mp4'")
                raw = base64.b64decode(scene['audio']['data_base64'], validate=True)
                require(hashlib.sha256(raw).hexdigest() == scene['audio']['sha256'], 'motion_audio_corrupt')
                (work / (name + '-original.wav')).write_bytes(raw)
                samples = round(seconds * media.AUDIO_RATE)
                delay = round(scene['timing']['leading_seconds'] * media.AUDIO_RATE)
                progress('preparing_audio', index, index)
                run(common + ['-i', name + '-original.wav', '-map', '0:a:0', '-vn', '-af',
                    f'aresample={media.AUDIO_RATE},adelay={delay}S:all=1,apad=whole_len={samples}',
                    '-t', f'{seconds:.9f}', '-ac', '1', '-ar', str(media.AUDIO_RATE),
                    '-c:a', 'pcm_s16le', name + '-padded.wav'])
                with wave.open(str(work / (name + '-padded.wav')), 'rb') as segment:
                    require(segment.getnframes() == samples and segment.getnchannels() == 1
                            and segment.getframerate() == media.AUDIO_RATE and segment.getsampwidth() == 2,
                            'motion_audio_timing_failed')
                    while chunk := segment.readframes(65536):
                        master.writeframesraw(chunk)
                progress('preparing_audio', index, index + 1)
        (work / 'concat.txt').write_text('\n'.join(concat) + '\n', encoding='ascii')
        seconds = snapshot['duration_seconds']
        progress('compositing', completed=total)
        run(common + ['-protocol_whitelist', 'file,pipe', '-f', 'concat', '-safe', '1', '-i', 'concat.txt',
            '-i', 'master.wav', '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac',
            '-b:a', '128k', '-ar', str(media.AUDIO_RATE), '-ac', '1', '-t', f'{seconds:.9f}',
            '-movflags', '+faststart', 'combined.mp4'])
        progress('validating_video', completed=total)
        probe = verify_media(work / 'combined.mp4', seconds, cwd=work, cancel_event=cancel_event)
        audio = next(v for v in probe['streams'] if v['codec_type'] == 'audio')
        require(audio.get('channels') == 1 and int(audio.get('sample_rate', 0)) == media.AUDIO_RATE,
                'motion_media_output_invalid')
        media._cancel(cancel_event)
        destination = root / 'video.mp4'
        (work / 'combined.mp4').replace(destination)
        with destination.open('rb') as stream:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
        result = dict(path=str(destination), sha256=checksum,
            bytes=destination.stat().st_size, duration_seconds=float(probe['format']['duration']),
            expected_duration_seconds=seconds, width=1280, height=720, fps=24, video_codec='h264', audio_codec='aac',
            silent=False, subtitles=snapshot['subtitles'], visual_mode=snapshot['visual_mode'], static_renderer='per-scene',
            snapshot_sha256=snapshot['sha256'], quality=snapshot['quality'],
            runtime_fingerprint=snapshot['runtime_fingerprint'], motion_checks=checks,
            elapsed_seconds=time.monotonic() - started)
        (root / 'report.json').write_bytes(canonical_bytes({k: v for k, v in result.items() if k != 'path'}))
        shutil.rmtree(work)
        return result

    def cleanup(self, snapshot, *, key, state, error, status):
        self.scene_renderer.cleanup_operation(snapshot, key, state=state, error=error, status=status)


class JournalCancellation:
    def __init__(self, journal, key, total_scenes=1):
        self.journal, self.key, self.total_scenes = journal, key, total_scenes
    def is_set(self):
        return self.journal.cancelled(self.key)
    def report_progress(self, stage, *, scene_index=None, completed_scenes=0):
        self.journal.progress(self.key, stage, scene_index=scene_index,
                              completed_scenes=completed_scenes, total_scenes=self.total_scenes)


class MotionExecutionService:
    def __init__(self, journal, renderer, *, root, token, runtime, image, release_id,
                 max_request_bytes=MAX_REQUEST_BYTES, max_storage_bytes=MAX_STORAGE_BYTES,
                 film_renderer=None):
        self.journal, self.renderer, self.root = journal, renderer, Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.token, self.runtime, self.image, self.release_id = token, _runtime(runtime), image, release_id
        require(image.endswith(':latest@' + self.runtime['image_digest']), 'motion_runtime_image_conflict', 422)
        self.max_request_bytes, self.max_storage_bytes = max_request_bytes, max_storage_bytes
        self.film_renderer = film_renderer
        self.intake_lock = threading.Lock()
        self.stopping, self.worker_lock = threading.Event(), threading.Lock()
        self.last_error = None

    def usage(self):
        paths = list(self.root.rglob('*'))
        if self.film_renderer is not None:
            pvc = self.film_renderer.pvc_root
            if pvc != self.root and self.root not in pvc.parents:
                paths += list(pvc.rglob('*'))
        paths += [Path(self.journal.path + suffix) for suffix in ('', '-wal', '-shm')]
        return sum(path.stat().st_size for path in set(paths) if path.is_file() and not path.is_symlink())

    def _renderer_for(self, snapshot):
        if snapshot.get('schema') == FILM_SCHEMA:
            require(self.film_renderer is not None, 'hyperframes_film_execution_disabled', 503)
            return self.film_renderer
        return self.renderer

    def submit(self, owner, operation_id, snapshot):
        # A re-query/idempotent replay never needs fresh capacity admission.
        previous = self.journal.operation(owner, operation_id)
        if previous:
            require(previous['snapshot_sha256'] == snapshot.get('sha256'), 'motion_operation_snapshot_conflict', 409)
            return previous
        raw = canonical_bytes(snapshot)
        require(len(raw) <= self.max_request_bytes, 'motion_render_input_too_large', 413)
        if snapshot.get('schema') == FILM_SCHEMA:
            renderer = self._renderer_for(snapshot)
            renderer.verify_snapshot(snapshot)
            from .hyperframes_film_snapshot import MAX_REQUEST_BYTES as film_request_limit
            require(len(raw) <= film_request_limit, 'motion_render_input_too_large', 413)
            # Reserve outstanding deliveries and staging before storing another
            # large JSON archive. A queue entry is not free storage capacity.
            with self.journal.lock, self.journal.connect() as db:
                pending = db.execute("SELECT count(*) FROM motion_operations WHERE state IN ('queued','running')").fetchone()[0]
            reserve = (pending + 1) * FILM_RESERVATION_BYTES
            require(self.usage() + reserve <= self.max_storage_bytes
                and shutil.disk_usage(renderer.pvc_root).free > reserve,
                'motion_render_storage_exceeded', 507)
            return self.journal.submit(owner, operation_id, snapshot)
        verify_motion_snapshot(snapshot)
        require(snapshot['runtime_fingerprint'] == self.runtime, 'motion_render_runtime_conflict', 409)
        require(all(s['visual_mode'] not in {'motion', 'creative'} or s['timing']['duration_seconds'] <= 60 for s in snapshot['scenes']),
                'motion_scene_duration_exceeded', 422)
        require(all(s['visual_mode'] not in {'motion', 'creative'} or len(base64.b64decode(s['audio']['data_base64'], validate=True))
                    <= 8 * 1024 * 1024 for s in snapshot['scenes']), 'motion_audio_size_exceeded', 413)
        reserve = len(raw) * 2 + 512 * 1024 * 1024
        require(self.usage() + reserve <= self.max_storage_bytes
                and shutil.disk_usage(self.root).free > reserve, 'motion_render_storage_exceeded', 507)
        return self.journal.submit(owner, operation_id, snapshot)

    def tick(self):
        if not self.worker_lock.acquire(blocking=False):
            return False
        try:
            row = self.journal.next_operation()
            if row is None:
                return False
            key = row['key']
            self.journal.update(key, state='running')
            snapshot = json.loads(row['snapshot'])
            event = JournalCancellation(self.journal, key, 1 if snapshot.get('schema') == FILM_SCHEMA
                                        else len(snapshot['scenes']))
            previous_result = json.loads(row['result']) if row['result'] else {}
            if 'cleanup_pending' in previous_result:
                self._finish_failure(row, previous_result['cleanup_pending'])
                return True
            try:
                result = self._renderer_for(snapshot).render(snapshot, self.root / key / 'result',
                                               key=key, cancel_event=event)
                path = Path(result['path']).resolve()
                expected = (self.root / key / 'result' / 'video.mp4').resolve()
                require(path == expected and not path.is_symlink() and path.is_file()
                        and path.stat().st_size == result['bytes'], 'motion_service_output_invalid')
                if event.is_set():
                    raise ServiceError('media_render_cancelled', 409)
                else:
                    public = {k: v for k, v in result.items() if k != 'path'}
                    event.report_progress('completed', completed_scenes=event.total_scenes)
                    latest = self.journal.operation(row['owner'], row['operation_id'])
                    public['progress'] = json.loads(latest['result'])['progress']
                    self.journal.update(key, state='completed', result=json.dumps(public), error=None)
            except ServiceError as error:
                self.last_error = error.code
                if error.code == UNAVAILABLE:
                    self.journal.update(key, state='queued', error=None)
                else:
                    state = 'cancelled' if error.code in {'media_render_cancelled', 'media_cancelled'} else ('timed_out' if error.status == 504 else 'failed')
                    self._begin_failure(row, state, error.code, error.status,
                                        motion_context=getattr(error, 'motion_context', None))
            except Exception as error:
                # Do not expose filesystem, credentials or exception text.
                self.last_error = 'motion_execution_storage_error' if isinstance(error, OSError) else 'motion_execution_internal_error'
                self._begin_failure(row, 'failed', self.last_error, 507 if isinstance(error, OSError) else 502)
            return True
        finally:
            self.worker_lock.release()

    def _begin_failure(self, row, state, error, status, *, motion_context=None):
        pending = dict(state=state, error=error, status=status)
        # Persist cleanup intent before touching resources. A process restart
        # takes this path directly, never renders/submits another scene Job.
        latest = self.journal.operation(row['owner'], row['operation_id'])
        result = json.loads(latest['result']) if latest['result'] else {}
        progress = safe_progress(result.get('progress'))
        diagnostic = failure_diagnostic(row['key'], error, progress, motion_context=motion_context)
        self.journal.update(row['key'], result=json.dumps({**result,
            'cleanup_pending': pending, 'diagnostic': diagnostic}), error=None)
        self._finish_failure(row, pending)

    def _finish_failure(self, row, pending):
        key = row['key']
        try:
            snapshot = json.loads(row['snapshot'])
            cleanup = getattr(self._renderer_for(snapshot), 'cleanup', None)
            if cleanup is not None:
                cleanup(snapshot, key=key, **pending)
            root = self.root / key / 'result'
            require(not root.is_symlink(), 'motion_scratch_invalid')
            work = root / 'assets'
            if work.exists() or work.is_symlink():
                require(work.is_dir() and not work.is_symlink(), 'motion_scratch_invalid')
                shutil.rmtree(work)
            for name in ('video.mp4', 'report.json'):
                path = root / name
                require(not path.is_symlink(), 'motion_scratch_invalid')
                path.unlink(missing_ok=True)
        except Exception:
            self.last_error = 'motion_cleanup_pending'
            self.journal.update(key, state='queued', error=None)
            return
        latest = self.journal.operation(row['owner'], row['operation_id'])
        result = json.loads(latest['result']) if latest['result'] else {}
        result.pop('cleanup_pending', None)
        self.journal.update(key, state=pending['state'], error=pending['error'],
                            result=json.dumps(result) if result else None)

    def run(self):
        while not self.stopping.is_set():
            try:
                self.tick()
            except Exception:
                # The durable running/queued row remains recoverable after a
                # transient journal/PVC fault; keep the single worker alive.
                self.last_error = 'motion_service_worker_unavailable'
            self.stopping.wait(2)

    def capabilities(self):
        self.renderer.scene_renderer.check_policy()
        result = dict(schema='video.motion-render-capabilities.v1', configured=True,
            image=self.image, release_id=self.release_id, runtime_fingerprint=self.runtime,
            runtime_verification='each_job_checks_installed_binaries_and_pod_image_digest',
            quality=['draft', 'final'], quality_presets={'draft': 'draft', 'final': 'delivery'},
            max_scene_seconds=60, max_scene_audio_bytes=8 * 1024 * 1024, max_scenes=24,
            max_artifact_gzip_bytes=6 * 1024 * 1024, worker_concurrency=1,
            original_pcm_master=True, arbitrary_html=False, automatic_fallback=False,
            uncertain_submission='same_name_suspended_fence_no_pods_no_audio_configmaps')
        if self.film_renderer is not None:
            self.film_renderer.check_policy()
            result['hyperframes_film'] = dict(configured=True, schema=FILM_SCHEMA,
                image=self.film_renderer.image, release_id=self.film_renderer.release_id,
                runtime_fingerprint=self.film_renderer.runtime,
                trusted_host_sha256=self.film_renderer.hosts,
                admission_sha256=self.film_renderer.admission['sha256'],
                max_seconds=60, fps=24, audio_sample_rate=48000, audio_channels=2,
                uncertain_submission='same_name_query_only_cleanup_pending_until_resolved')
        return result


def failure_diagnostic(key, code, progress=None, *, motion_context=None):
    category = ('resource' if code == 'motion_execution_oom' else
        'timeout' if code in {'motion_execution_timed_out', 'media_render_timed_out'} else
        'artifact_budget' if code == 'motion_artifact_budget_exceeded' else
        'render' if code in {'motion_render_stage_failed', 'motion_audio_mux_failed'} else
        'observation' if code == 'motion_observation_stage_failed' else
        'storage' if code in {'motion_execution_storage_error', 'motion_render_storage_exceeded'} else
        'submission' if code in {'motion_submission_unconfirmed', 'motion_execution_disappeared'} else
        'cancelled' if code in {'media_render_cancelled', 'media_cancelled'} else
        'validation' if code in {'motion_media_output_invalid', 'motion_observation_invalid',
            'motion_artifact_invalid', 'motion_cached_media_corrupt', 'motion_text_overflow',
            'motion_expected_highlight_missing', 'motion_invisible_highlight',
            'motion_expected_draw_missing', 'motion_expected_reveal_missing', 'motion_expected_hide_missing',
            'motion_invisible_move', 'motion_expected_move_missing', 'motion_frozen_frames',
            'motion_seek_time_mismatch', 'motion_reverse_seek_failed'} else 'execution')
    result = dict(code=code, category=category, stage=progress['stage'] if progress else None,
        scene_index=progress['scene_index'] if progress else None,
        diagnostic_id='MR-' + hashlib.sha256((key + ':' + code).encode()).hexdigest()[:16])
    if context := safe_motion_context(motion_context):
        result['motion_context'] = context
    return result


def public_operation(row):
    result = dict(operation_id=row['operation_id'], snapshot_sha256=row['snapshot_sha256'], state=row['state'])
    details = json.loads(row['result']) if row['result'] else {}
    if progress := safe_progress(details.get('progress')):
        if 'cleanup_pending' in details:
            progress['stage'] = 'cleanup'
        result['progress'] = progress
    if row['state'] == 'completed' and row['result']:
        result['result'] = {k: v for k, v in details.items() if k not in {'progress', 'diagnostic'}}
    if row['error']:
        result['error'] = row['error']
        diagnostic = safe_diagnostic(details.get('diagnostic'))
        if diagnostic:
            result['diagnostic'] = diagnostic
    return result


def create_app(service, *, start_worker=True):
    @asynccontextmanager
    async def lifespan(app):
        thread = None
        if start_worker:
            thread = threading.Thread(target=service.run, name='motion-render', daemon=True)
            thread.start()
        yield
        # SIGTERM/restart does not turn durable operations into user cancels.
        # The next single replica resumes exact Job identities from the PVC.
        service.stopping.set()
        if thread:
            thread.join(timeout=2)
    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(ServiceError)
    async def service_error(request, error):
        return JSONResponse({'error': error.code}, status_code=error.status)

    def authenticate(request, *, owner=True):
        import hmac
        supplied = request.headers.get('authorization', '')
        require(hmac.compare_digest(supplied.encode(), ('Bearer ' + service.token).encode()), 'unauthorized', 401)
        value = request.headers.get('x-video-owner', '')
        require(not owner or bool(re.fullmatch(r'[0-9a-f]{64}', value)), 'motion_render_owner_invalid', 422)
        return value

    def get_operation(request, operation_id):
        owner = authenticate(request)
        row = service.journal.operation(owner, operation_id)
        require(row is not None, 'not_found', 404)
        return row

    @app.get('/health/live')
    def live():
        return {'status': 'ok'}

    @app.get('/health/ready')
    def ready():
        service.renderer.scene_renderer.check_policy()
        if service.film_renderer is not None:
            service.film_renderer.check_policy()
        require(shutil.disk_usage(service.root).free > 512 * 1024 * 1024, 'motion_render_storage_exceeded', 503)
        return {'status': 'ready'}

    @app.get('/v1/capabilities')
    def capabilities(request: Request):
        authenticate(request, owner=False)
        return service.capabilities()

    @app.put('/v1/renders/{operation_id}')
    async def submit(operation_id: str, request: Request):
        owner = authenticate(request)
        operation_key(owner, operation_id)
        require(service.intake_lock.acquire(blocking=False), 'motion_render_intake_busy', 429)
        try:
            length = request.headers.get('content-length')
            require(length is None or length.isdigit() and int(length) <= service.max_request_bytes,
                    'motion_render_input_too_large', 413)
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                require(len(raw) <= service.max_request_bytes, 'motion_render_input_too_large', 413)
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeError, RecursionError):
                raise ServiceError('motion_render_input_invalid', 422) from None
            del raw
            require(type(body) is dict and set(body) == {'snapshot'} and type(body['snapshot']) is dict,
                    'motion_render_input_invalid', 422)
            return public_operation(service.submit(owner, operation_id, body['snapshot']))
        finally:
            service.intake_lock.release()

    @app.get('/v1/renders/{operation_id}')
    def query(operation_id: str, request: Request):
        return public_operation(get_operation(request, operation_id))

    @app.post('/v1/renders/{operation_id}/cancel')
    def cancel(operation_id: str, request: Request):
        row = get_operation(request, operation_id)
        if row['state'] not in {'completed', 'failed', 'cancelled', 'timed_out'}:
            service.journal.update(row['key'], cancel_requested=1)
            row = service.journal.operation(row['owner'], operation_id)
        return public_operation(row)

    @app.get('/v1/renders/{operation_id}/video')
    def video(operation_id: str, request: Request):
        row = get_operation(request, operation_id)
        require(row['state'] == 'completed', 'motion_render_not_completed', 409)
        result = json.loads(row['result'])
        path = service.root / row['key'] / 'result' / 'video.mp4'
        require(not path.is_symlink() and path.is_file() and path.stat().st_size == result['bytes'],
                'motion_media_unavailable', 410)
        return FileResponse(path, media_type='video/mp4', filename='video.mp4', headers={
            'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})
    return app


def from_environment(environ=None):
    env = os.environ if environ is None else environ
    def needed(name):
        require(bool(env.get(name)), 'motion_service_configuration_incomplete', 503)
        return env[name]
    runtime_file = Path(needed('VIDEO_MOTION_RUNTIME_FILE'))
    require(runtime_file.is_absolute() and runtime_file.is_file() and runtime_file.stat().st_size <= 16384,
            'motion_runtime_fingerprint_invalid', 503)
    runtime = _runtime(json.loads(runtime_file.read_bytes()))
    journal_path = Path(env.get('VIDEO_MOTION_JOURNAL_PATH', '/data/motion-render-jobs.db'))
    require(journal_path.is_absolute(), 'motion_journal_path_invalid', 503)
    root = Path(env.get('VIDEO_MOTION_DATA_DIR', '/data/executions'))
    require(root.is_absolute(), 'motion_data_path_invalid', 503)
    journal = ExecutionJournal(journal_path)
    kube = KubeHTTP(needed('VIDEO_MOTION_K8S_API'),
        env.get('VIDEO_MOTION_K8S_TOKEN_FILE', '/var/run/secrets/kubernetes.io/serviceaccount/token'),
        env.get('VIDEO_MOTION_K8S_CA_FILE', '/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'),
        env.get('VIDEO_MOTION_NAMESPACE', 'video-motion-render'))
    renderer = KubeSceneRenderer(journal, kube, image=needed('VIDEO_MOTION_RENDER_IMAGE'),
        release_id=needed('VIDEO_MOTION_RENDER_RELEASE'), runtime=runtime, root=root)
    film_renderer = None
    if env.get('VIDEO_HYPERFRAMES_FILM_ENABLED') == '1':
        from .hyperframes_film_execution import _ordinary
        from .hyperframes_film_k8s import KubeFilmRenderer
        def film_json(name):
            path = Path(needed(name))
            require(path.is_absolute(), 'hyperframes_film_configuration_invalid', 503)
            return json.loads(_ordinary(path, 32768))
        film_kube = KubeHTTP(needed('VIDEO_MOTION_K8S_API'),
            env.get('VIDEO_MOTION_K8S_TOKEN_FILE', '/var/run/secrets/kubernetes.io/serviceaccount/token'),
            env.get('VIDEO_MOTION_K8S_CA_FILE', '/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'),
            needed('VIDEO_HYPERFRAMES_FILM_NAMESPACE'))
        film_pvc_selector = json.loads(needed('VIDEO_HYPERFRAMES_FILM_PVC_WORKLOAD_SELECTOR'))
        require(type(film_pvc_selector) is dict and bool(film_pvc_selector),
            'hyperframes_film_pvc_selector_invalid', 503)
        film_renderer = KubeFilmRenderer(journal, film_kube,
            image=needed('VIDEO_HYPERFRAMES_FILM_IMAGE'),
            release_id=needed('VIDEO_HYPERFRAMES_FILM_RELEASE'),
            runtime=film_json('VIDEO_HYPERFRAMES_FILM_RUNTIME_FILE'),
            trusted_host_sha256=film_json('VIDEO_HYPERFRAMES_FILM_HOSTS_FILE'),
            admission=film_json('VIDEO_HYPERFRAMES_FILM_ADMISSION_FILE'),
            pvc_root=Path(needed('VIDEO_HYPERFRAMES_FILM_PVC_ROOT')),
            pvc_name=needed('VIDEO_HYPERFRAMES_FILM_PVC_NAME'), root=root,
            pvc_workload_selector=film_pvc_selector)
    return MotionExecutionService(journal, MixedMotionRenderer(renderer), root=root,
        token=read_secret(needed('VIDEO_MOTION_SERVICE_TOKEN_FILE')), runtime=runtime,
        image=renderer.image, release_id=renderer.release_id, film_renderer=film_renderer)


def app_factory():
    return create_app(from_environment())
