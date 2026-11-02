"""Owner-scoped timing, captions and durable frozen-snapshot render jobs."""
from __future__ import annotations

import hashlib
import base64
import json
import math
from pathlib import Path
import threading
import time
import tempfile
from uuid import uuid4

from agent_service.contracts import ServiceError, digest
from . import media
from .motion_snapshot import SCHEMA as MOTION_SCHEMA, freeze_snapshot_v2, verify_snapshot_v2

ACTIVE = {'queued', 'running', 'cancelling'}
MAX_PROJECT_AUDIO_BYTES = 64 * 1024 * 1024
MAX_RENDER_BYTES = 256 * 1024 * 1024
MAX_STORED_RENDER_BYTES = 256 * 1024 * 1024
MAX_MOTION_SCENE_SECONDS = 60
MAX_MOTION_AUDIO_BYTES = 8 * 1024 * 1024


class Production:
    def __init__(self, store, *, renderer=None, enabled=False, motion_store=None,
                 motion_timing=None, motion_renderer=None, motion_enabled=False,
                 runtime_fingerprint=None):
        self.store = store
        self.renderer = renderer or media.render_snapshot
        self.enabled = enabled
        self.motion_store, self.motion_timing = motion_store, motion_timing
        self.motion_renderer, self.motion_enabled = motion_renderer, motion_enabled
        self.runtime_fingerprint = runtime_fingerprint
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.current_cancel = None
        self.current_id = None
        self.thread = None
        with store.transaction() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS video_renders (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, project_id TEXT NOT NULL,
                idem TEXT NOT NULL, request_hash TEXT NOT NULL, document TEXT NOT NULL,
                snapshot TEXT NOT NULL, data BLOB, UNIQUE(owner, idem))''')

    @staticmethod
    def _public(job):
        return {k: v for k, v in job.items() if k not in {'owner', 'cancel_requested'}}

    def timing(self, owner, scene_id, fields, revision):
        values = {k: fields[k] for k in ('leading_seconds', 'trailing_seconds')}
        if any(type(v) not in {int, float} or not math.isfinite(v) or not 0 <= v <= 10 for v in values.values()):
            raise ServiceError('invalid_timing', 422)
        project_id = self.store.scene_project(owner, scene_id)
        with self.store.transaction() as db:
            project = self.store._project(db, owner, project_id)
            self.store._revision(project, revision)
            scene = self.store._scene(project, scene_id)
            changed = values['leading_seconds'] != scene.get('leading_seconds', 0)
            scene.update(values)
            # Cue times include the leading pause. A change requires a fresh
            # estimate or explicit correction; never silently shift reviewed cues.
            if changed and isinstance(scene.get('captions'), dict):
                scene['captions']['timing_stale'] = True
            self.store._save(db, project)
        return self.store.project(owner, project_id)

    def captions(self, owner, scene_id, revision, *, cues=None, audio_version_id=None):
        project_id = self.store.scene_project(owner, scene_id)
        project = self.store.project(owner, project_id)
        self.store._revision(project, revision)
        scene = self.store._scene(project, scene_id)
        audio_id = scene.get('head_audio_version_id')
        if not audio_id or scene.get('audio_stale'):
            raise ServiceError('missing_current_audio', 409)
        audio = self.store.audio_version(owner, audio_id)
        timing = media.scene_timing(scene['duration_seconds'], audio['duration_seconds'],
                                    scene.get('leading_seconds', 0), scene.get('trailing_seconds', 0))
        if cues is None:
            state = media.estimate_captions(scene['narration'], audio['duration_seconds'], scene.get('leading_seconds', 0))
        else:
            if audio_version_id != audio_id:
                raise ServiceError('audio_head_conflict', 409)
            state = media.validate_captions(cues, timing['duration_seconds'], audio['sha256'],
                leading_seconds=scene.get('leading_seconds', 0), audio_seconds=audio['duration_seconds'])
            state.update(alignment='manual', requires_review=False)
        state.update(audio_version_id=audio_id, audio_sha256=audio['sha256'], timing_stale=False)
        with self.store.transaction() as db:
            latest = self.store._project(db, owner, project_id)
            self.store._revision(latest, revision)
            self.store._scene(latest, scene_id)['captions'] = state
            self.store._save(db, latest)
        return self.store.project(owner, project_id)

    def _scenes(self, owner, project, *, silent=False, subtitles=True):
        if not project['scenes']:
            raise ServiceError('unadopted_scenes', 409)
        results = []
        total_bytes = 0
        for scene in project['scenes']:
            visual_id = scene.get('head_version_id')
            if not visual_id:
                raise ServiceError('unadopted_scenes', 409)
            visual = self.store.version(owner, visual_id)
            audio = None
            if not silent:
                audio_id = scene.get('head_audio_version_id')
                if not audio_id or scene.get('audio_stale'):
                    raise ServiceError('missing_current_audio', 409)
                audio = self.store.audio_version(owner, audio_id)
                total_bytes += len(audio['data'])
                if total_bytes > MAX_PROJECT_AUDIO_BYTES:
                    raise ServiceError('project_audio_limit', 413)
            captions = scene.get('captions') if subtitles and not silent else None
            if subtitles and not silent and captions is None:
                raise ServiceError('missing_captions', 409)
            if captions and (captions.get('stale') or captions.get('timing_stale')):
                raise ServiceError('stale_captions', 409)
            if silent:
                # Silent export is explicit and uses only applicable captions.
                captions = None
            results.append(dict(id=scene['id'], title=scene['title'], narration=scene['narration'],
                duration_seconds=scene['duration_seconds'], visual=visual, audio=audio,
                leading_seconds=scene.get('leading_seconds', 0), trailing_seconds=scene.get('trailing_seconds', 0),
                captions=captions))
        return results

    def snapshot(self, owner, project_id, *, silent=False, subtitles=True):
        project = self.store.project(owner, project_id)
        return media.freeze_snapshot(project_id, project['revision'], project['title'],
            self._scenes(owner, project, silent=silent, subtitles=subtitles), silent=silent, subtitles=subtitles)

    def timeline(self, owner, project_id, *, silent=False, subtitles=True):
        project = self.store.project(owner, project_id)
        scenes = self._scenes(owner, project, silent=silent, subtitles=False)
        cursor = 0.0
        result = []
        for scene in scenes:
            audio = scene['audio']
            timing = media.scene_timing(scene['duration_seconds'], audio['duration_seconds'] if audio else 0,
                                        scene['leading_seconds'], scene['trailing_seconds'])
            timing['duration_seconds'] = math.ceil(timing['duration_seconds'] * media.FPS) / media.FPS
            original = self.store._scene(project, scene['id'])
            captions = original.get('captions') or {}
            result.append(dict(id=scene['id'], title=scene['title'], start_seconds=cursor,
                duration_seconds=timing['duration_seconds'], configured_seconds=scene['duration_seconds'],
                leading_seconds=scene['leading_seconds'], trailing_seconds=scene['trailing_seconds'],
                visual_version_id=scene['visual']['id'], audio_version_id=audio['id'] if audio else None,
                audio_duration_seconds=audio['duration_seconds'] if audio else 0, audio_stale=False,
                cues=[] if silent or not subtitles or captions.get('stale') or captions.get('timing_stale') else captions.get('cues', [])))
            cursor += timing['duration_seconds']
        return dict(project_id=project_id, project_revision=project['revision'], duration_seconds=cursor, scenes=result, silent=silent)

    def subtitles(self, owner, project_id):
        snapshot = self.snapshot(owner, project_id)
        if any(not s.get('captions') for s in snapshot['scenes']):
            raise ServiceError('missing_captions', 409)
        return media.subtitles_srt(snapshot)

    def start_render(self, owner, project_id, request, idem):
        if not self.enabled:
            raise ServiceError('render_not_configured', 503)
        fingerprint = digest(dict(project_id=project_id, request=request))
        with self.store.transaction() as db:
            row = db.execute('SELECT document,request_hash FROM video_renders WHERE owner=? AND idem=?', (owner, idem)).fetchone()
            if row:
                if row['request_hash'] != fingerprint:
                    raise ServiceError('idempotency_conflict', 409)
                return self._public(json.loads(row['document']))
        snapshot = self.snapshot(owner, project_id, silent=request['silent'], subtitles=request['subtitles'])
        with self.store.transaction() as db:
            project = self.store._project(db, owner, project_id)
            self.store._revision(project, request['expected_revision'])
            if project['revision'] != snapshot['project_revision']:
                raise ServiceError('revision_conflict', 409)
            row = db.execute('SELECT document,request_hash FROM video_renders WHERE owner=? AND idem=?', (owner, idem)).fetchone()
            if row:
                if row['request_hash'] != fingerprint:
                    raise ServiceError('idempotency_conflict', 409)
                return self._public(json.loads(row['document']))
            for row in db.execute('SELECT document FROM video_renders WHERE owner=?', (owner,)):
                if json.loads(row['document'])['status'] in ACTIVE:
                    raise ServiceError('render_in_progress', 409)
            job = dict(id=str(uuid4()), owner=owner, project_id=project_id, project_revision=project['revision'],
                status='queued', error=None, silent=snapshot['silent'], subtitles=snapshot['subtitles'],
                visual_mode='static', created_at=time.time(), snapshot_sha256=snapshot['sha256'], cancel_requested=False)
            # Immutable audio candidates already preserve exact bytes. Keep
            # references in the queue rather than duplicating them in each job.
            packed = dict(snapshot, scenes=[dict(s, audio={k: v for k, v in s['audio'].items() if k != 'data_base64'}
                if s['audio'] else None) for s in snapshot['scenes']])
            db.execute('INSERT INTO video_renders VALUES (?, ?, ?, ?, ?, ?, ?, NULL)',
                (job['id'], owner, project_id, idem, fingerprint, json.dumps(job), json.dumps(packed)))
        return self._public(job)

    def snapshot_v2(self, owner, project_id, request):
        """Resolve owner-scoped exact candidates and freeze one consistent view."""
        if self.motion_store is None or self.motion_timing is None or self.runtime_fingerprint is None:
            raise ServiceError('motion_runtime_not_configured', 503)
        purpose = request['purpose']
        selection = [request.get(key) for key in ('scene_id', 'motion_version_id', 'audio_version_id')]
        if purpose not in {'draft', 'delivery'} or any(selection) and (purpose != 'draft' or not all(selection)):
            raise ServiceError('invalid_motion_render_selection', 422)
        with self.store.lock:
            project = self.motion_store.project(owner, project_id)
            self.store._revision(project, request['expected_revision'])
            scenes = project['scenes']
            if all(selection):
                scenes = [self.store._scene(project, request['scene_id'])]
            if not scenes:
                raise ServiceError('unadopted_scenes', 409)
            resolved, adopted, total = [], {}, 0
            for scene in scenes:
                mode = 'motion' if all(selection) else scene['motion']['mode']
                audio_id = request['audio_version_id'] if all(selection) else scene.get('head_audio_version_id')
                if not audio_id:
                    raise ServiceError('missing_current_audio', 409)
                audio = self.store.audio_version(owner, audio_id)
                if audio['project_id'] != project_id or audio['scene_id'] != scene['id']:
                    raise ServiceError('not_found', 404)
                if audio['input_sha256'] != scene['audio_input_sha256']:
                    raise ServiceError('audio_stale', 409)
                total += len(audio['data'])
                if total > MAX_PROJECT_AUDIO_BYTES:
                    raise ServiceError('project_audio_limit', 413)
                item = dict(id=scene['id'], title=scene['title'], narration=scene['narration'], audio=audio,
                    visual_mode=mode, duration_seconds=scene['duration_seconds'],
                    leading_seconds=scene.get('leading_seconds', 0), trailing_seconds=scene.get('trailing_seconds', 0),
                    captions=scene.get('captions'))
                if mode == 'motion':
                    if len(audio['data']) > MAX_MOTION_AUDIO_BYTES:
                        raise ServiceError('motion_audio_size_exceeded', 413)
                    version_id = request['motion_version_id'] if all(selection) else scene['motion']['head_version_id']
                    if not version_id:
                        raise ServiceError('missing_motion_plan', 409)
                    version = self.motion_store.version(owner, version_id)
                    if version['project_id'] != project_id or version['scene_id'] != scene['id']:
                        raise ServiceError('not_found', 404)
                    timing = self.motion_timing.get(owner, version_id, audio_id)
                    if timing['timeline']['duration_seconds'] > MAX_MOTION_SCENE_SECONDS:
                        raise ServiceError('motion_scene_duration_exceeded', 422)
                    item['motion'] = dict(id=version_id, plan=version['plan'], beats=timing['beats'],
                                          alignment=timing['alignment'], stale=version['stale'])
                else:
                    version_id = scene.get('head_version_id')
                    if not version_id:
                        raise ServiceError('unadopted_scenes', 409)
                    item['visual'] = self.store.version(owner, version_id)
                adopted[scene['id']] = dict(visual_mode=mode, visual_version_id=version_id,
                                            audio_version_id=scene.get('head_audio_version_id'))
                resolved.append(item)
            return freeze_snapshot_v2(project_id, project['revision'], project['title'], resolved,
                runtime_fingerprint=self.runtime_fingerprint, purpose=purpose,
                quality='final' if purpose == 'delivery' else 'draft', subtitles=request.get('subtitles', False),
                current_adoptions=adopted if purpose == 'delivery' else None)

    def start_motion_render(self, owner, project_id, request, idem):
        if not self.motion_enabled:
            raise ServiceError('motion_disabled', 503)
        if self.motion_renderer is None:
            raise ServiceError('motion_render_not_configured', 503)
        fingerprint = digest(dict(project_id=project_id, request=request, schema=MOTION_SCHEMA))
        with self.store.transaction() as db:
            row = db.execute('SELECT document,request_hash FROM video_renders WHERE owner=? AND idem=?', (owner, idem)).fetchone()
            if row:
                if row['request_hash'] != fingerprint:
                    raise ServiceError('idempotency_conflict', 409)
                return self._public(json.loads(row['document']))
        snapshot = self.snapshot_v2(owner, project_id, request)
        with self.store.transaction() as db:
            project = self.store._project(db, owner, project_id)
            self.store._revision(project, snapshot['project_revision'])
            row = db.execute('SELECT document,request_hash FROM video_renders WHERE owner=? AND idem=?', (owner, idem)).fetchone()
            if row:
                if row['request_hash'] != fingerprint:
                    raise ServiceError('idempotency_conflict', 409)
                return self._public(json.loads(row['document']))
            for row in db.execute('SELECT document FROM video_renders WHERE owner=?', (owner,)):
                if json.loads(row['document'])['status'] in ACTIVE:
                    raise ServiceError('render_in_progress', 409)
            job = dict(id=str(uuid4()), owner=owner, project_id=project_id, project_revision=project['revision'],
                status='queued', error=None, silent=False, subtitles=snapshot['subtitles'],
                schema=MOTION_SCHEMA, purpose=snapshot['purpose'], quality=snapshot['quality'], visual_mode='mixed',
                created_at=time.time(), snapshot_sha256=snapshot['sha256'], cancel_requested=False, render_attempted=False)
            packed = dict(snapshot, scenes=[dict(s, audio={key: value for key, value in s['audio'].items()
                if key != 'data_base64'}) for s in snapshot['scenes']])
            db.execute('INSERT INTO video_renders VALUES (?, ?, ?, ?, ?, ?, ?, NULL)',
                (job['id'], owner, project_id, idem, fingerprint, json.dumps(job), json.dumps(packed)))
        return self._public(job)

    def renders(self, owner, project_id):
        self.store.project(owner, project_id)
        with self.store.transaction() as db:
            return [self._public(json.loads(r[0])) for r in db.execute(
                'SELECT document FROM video_renders WHERE owner=? AND project_id=? ORDER BY rowid DESC', (owner, project_id))]

    def cancel(self, owner, job_id):
        with self.store.transaction() as db:
            row = db.execute('SELECT document FROM video_renders WHERE owner=? AND id=?', (owner, job_id)).fetchone()
            if row is None:
                raise ServiceError('not_found', 404)
            job = json.loads(row[0])
            if job['status'] in ACTIVE:
                never_admitted = job['status'] == 'queued' and not (job.get('schema') == MOTION_SCHEMA and job.get('render_attempted'))
                job.update(cancel_requested=True, status='cancelled' if never_admitted else 'cancelling')
                db.execute('UPDATE video_renders SET document=? WHERE id=?', (json.dumps(job), job_id))
                if self.current_id == job_id and self.current_cancel:
                    self.current_cancel.set()
        return self._public(job)

    def video(self, owner, job_id):
        with self.store.transaction() as db:
            row = db.execute('SELECT document,data FROM video_renders WHERE owner=? AND id=?', (owner, job_id)).fetchone()
            if row is None:
                raise ServiceError('not_found', 404)
            job = json.loads(row['document'])
            if job['status'] != 'completed' or not row['data']:
                raise ServiceError('render_not_ready', 409)
            return row['data']

    def _recover(self):
        with self.store.transaction() as db:
            for row in db.execute('SELECT id,document FROM video_renders'):
                job = json.loads(row['document'])
                if job['status'] in {'running', 'cancelling'}:
                    if job.get('schema') == MOTION_SCHEMA:
                        # The parent renderer restores its journal using this
                        # same business id; recovery must never create a new job.
                        job.update(status='queued', error='render_recovering')
                    else:
                        job.update(status='cancelled' if job['cancel_requested'] else 'failed', error='render_interrupted')
                    db.execute('UPDATE video_renders SET document=? WHERE id=?', (json.dumps(job), row['id']))

    def start(self):
        self._recover()
        self.thread = threading.Thread(target=self._run, name='video-render', daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.current_cancel:
            self.current_cancel.set()
        if self.thread:
            self.thread.join(timeout=12)

    def _run(self):
        while not self.stopping.is_set():
            self.tick()
            self.stopping.wait(1)

    def tick(self):
        if not (self.enabled or self.motion_renderer is not None) or not self.lock.acquire(blocking=False):
            return
        job = None
        try:
            with self.store.transaction() as db:
                for row in db.execute('SELECT id,document,snapshot FROM video_renders ORDER BY rowid'):
                    candidate = json.loads(row['document'])
                    if candidate['status'] == 'queued' or (candidate['status'] == 'cancelling' and candidate.get('schema') == MOTION_SCHEMA):
                        motion = candidate.get('schema') == MOTION_SCHEMA
                        if motion and not self.motion_enabled and not candidate.get('render_attempted'):
                            candidate.update(status='cancelled', error='motion_disabled')
                            db.execute('UPDATE video_renders SET document=? WHERE id=?', (json.dumps(candidate), candidate['id']))
                            continue
                        if (motion and self.motion_renderer is None) or (not motion and not self.enabled):
                            continue
                        job, snapshot = candidate, json.loads(row['snapshot'])
                        job.update(status='running')
                        if motion:
                            job['render_attempted'] = True
                        db.execute('UPDATE video_renders SET document=? WHERE id=?', (json.dumps(job), job['id']))
                        self.current_id, self.current_cancel = job['id'], threading.Event()
                        if job['cancel_requested']:
                            self.current_cancel.set()
                        break
            if job is None:
                return
            try:
                for scene in snapshot['scenes']:
                    if scene['audio']:
                        candidate = self.store.audio_version(job['owner'], scene['audio']['id'])
                        if candidate['sha256'] != scene['audio']['sha256']:
                            raise ServiceError('media_snapshot_corrupt', 409)
                        scene['audio']['data_base64'] = base64.b64encode(candidate['data']).decode('ascii')
                motion = snapshot.get('schema') == MOTION_SCHEMA
                (verify_snapshot_v2 if motion else media.verify_snapshot)(snapshot)
                with tempfile.TemporaryDirectory(prefix='video-render-') as directory:
                    if motion:
                        render = getattr(self.motion_renderer, 'render', self.motion_renderer)
                        result = render(snapshot, Path(directory), cancel_event=self.current_cancel,
                                        operation_id=job['id'], owner=job['owner'])
                    else:
                        result = self.renderer(snapshot, Path(directory), cancel_event=self.current_cancel)
                    path = Path(result['path']).resolve()
                    if not path.is_relative_to(Path(directory).resolve()) or not 1 <= path.stat().st_size <= MAX_RENDER_BYTES:
                        raise ServiceError('render_invalid_output', 502)
                    data = path.read_bytes()
                    sha = hashlib.sha256(data).hexdigest()
                    with self.store.transaction() as db:
                        latest = json.loads(db.execute('SELECT document FROM video_renders WHERE id=?', (job['id'],)).fetchone()[0])
                        if latest['cancel_requested'] or self.stopping.is_set():
                            latest.update(status='cancelled', error=None)
                            data = None
                        else:
                            stored = db.execute('SELECT coalesce(sum(length(data)),0) FROM video_renders').fetchone()[0]
                            if stored + len(data) > MAX_STORED_RENDER_BYTES:
                                raise ServiceError('render_storage_limit', 409)
                            latest.update(status='completed', error=None, sha256=sha, bytes=len(data),
                                          duration_seconds=result['duration_seconds'], finished_at=time.time())
                        db.execute('UPDATE video_renders SET document=?,data=? WHERE id=?', (json.dumps(latest), data, job['id']))
            except Exception as exc:
                with self.store.transaction() as db:
                    latest = json.loads(db.execute('SELECT document FROM video_renders WHERE id=?', (job['id'],)).fetchone()[0])
                    if (job.get('schema') == MOTION_SCHEMA and isinstance(exc, ServiceError) and exc.status >= 500
                            and (exc.code.startswith(('service_', 'http_'))
                                 or exc.code == 'motion_render_service_unavailable')):
                        # The parent service may already own work. Keep the
                        # exact id for lookup/cancel after a transient outage.
                        latest.update(status='queued', error=exc.code)
                    else:
                        latest.update(status='cancelled' if latest['cancel_requested'] or self.stopping.is_set() else 'failed',
                                      error=exc.code if isinstance(exc, ServiceError) else 'render_failed')
                    db.execute('UPDATE video_renders SET document=?,data=NULL WHERE id=?', (json.dumps(latest), job['id']))
        finally:
            self.current_id, self.current_cancel = None, None
            self.lock.release()
