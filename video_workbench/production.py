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

ACTIVE = {'queued', 'running', 'cancelling'}
MAX_PROJECT_AUDIO_BYTES = 64 * 1024 * 1024
MAX_RENDER_BYTES = 256 * 1024 * 1024
MAX_STORED_RENDER_BYTES = 256 * 1024 * 1024


class Production:
    def __init__(self, store, *, renderer=None, enabled=False):
        self.store = store
        self.renderer = renderer or media.render_snapshot
        self.enabled = enabled
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
                job.update(cancel_requested=True, status='cancelled' if job['status'] == 'queued' else 'cancelling')
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

    def start(self):
        with self.store.transaction() as db:
            for row in db.execute('SELECT id,document FROM video_renders'):
                job = json.loads(row['document'])
                if job['status'] in {'running', 'cancelling'}:
                    job.update(status='cancelled' if job['cancel_requested'] else 'failed', error='render_interrupted')
                    db.execute('UPDATE video_renders SET document=? WHERE id=?', (json.dumps(job), row['id']))
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
        if not self.enabled or not self.lock.acquire(blocking=False):
            return
        job = None
        try:
            with self.store.transaction() as db:
                for row in db.execute('SELECT id,document,snapshot FROM video_renders ORDER BY rowid'):
                    candidate = json.loads(row['document'])
                    if candidate['status'] == 'queued':
                        job, snapshot = candidate, json.loads(row['snapshot'])
                        job.update(status='running')
                        db.execute('UPDATE video_renders SET document=? WHERE id=?', (json.dumps(job), job['id']))
                        self.current_id, self.current_cancel = job['id'], threading.Event()
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
                media.verify_snapshot(snapshot)
                with tempfile.TemporaryDirectory(prefix='video-render-') as directory:
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
                    latest.update(status='cancelled' if latest['cancel_requested'] or self.stopping.is_set() else 'failed',
                                  error=exc.code if isinstance(exc, ServiceError) else 'render_failed')
                    db.execute('UPDATE video_renders SET document=?,data=NULL WHERE id=?', (json.dumps(latest), job['id']))
        finally:
            self.current_id, self.current_cancel = None, None
            self.lock.release()
