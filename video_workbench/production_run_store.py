"""Resumable audio-first draft journals; generated candidates never adopt media.

Only local child admissions run here. Their request and stable key are committed
before calling existing stores, so a restart finds or replays the same child.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
import time
from uuid import uuid4

from agent_service.contracts import ServiceError, digest
from .audio_store import narration_binding
from .media import MAX_PROJECT_SECONDS, MAX_SCENES
from .production_film_context import freeze_production_inputs
from .speech import inspect_wav


ACTIVE = frozenset({'queued', 'running'})
CHILD_ACTIVE = frozenset({'queued', 'submitting', 'running', 'cancelling'})
CHILD_TERMINAL = frozenset({'completed', 'failed', 'needs_input', 'cancelled', 'timed_out'})
TABLES = {'audio': 'audio_operations', 'motion': 'motion_operations', 'draft': 'video_renders'}
PUBLIC_ITEM = ('scene_id', 'title', 'audio_operation_id', 'audio_version_id', 'audio_sha256',
               'audio_seconds', 'motion_operation_id', 'motion_version_id', 'status', 'error')


def require(value, code='invalid_production_run_request', status=422):
    if not value:
        raise ServiceError(code, status)


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class ProductionRunStore:
    def __init__(self, store, joint_plan_store, motion_store, production):
        self.store, self.joint, self.motion, self.production = store, joint_plan_store, motion_store, production
        with store.transaction() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS production_runs (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, project_id TEXT NOT NULL,
                idem TEXT NOT NULL, request_hash TEXT NOT NULL, document TEXT NOT NULL,
                UNIQUE(owner, idem));
                CREATE INDEX IF NOT EXISTS production_runs_project ON production_runs(project_id);''')

    @staticmethod
    def public(run, *, current=False):
        result = {key: run.get(key) for key in ('id', 'project_id', 'joint_plan_version_id',
            'request_key', 'status', 'phase', 'voice', 'draft_render_id', 'error', 'blocked_reason', 'draining',
            'created_at', 'finished_at')}
        items = []
        for item, frozen in zip(run['items'], run['frozen']['scenes']):
            seconds = item.get('audio_seconds')
            duration = (math.ceil((seconds + frozen['leading_seconds'] + frozen['trailing_seconds']) * 24) / 24
                        if seconds is not None else None)
            items.append(dict({key: item.get(key) for key in PUBLIC_ITEM}, duration_seconds=duration,
                              candidate_current=bool(current and item.get('motion_version_id'))))
        result.update(items=items, current=bool(current),
            duration_seconds=sum(item['duration_seconds'] for item in items)
                if all(item['duration_seconds'] is not None for item in items) else None,
            duration_source='measured_recordings_and_frame_quantized_pauses',
            review_status='not_reviewed', selection='draft_only',
            progress=dict(done=sum(bool(item.get('audio_version_id')) + bool(item.get('motion_version_id'))
                                   for item in run['items']) + int(run['status'] == 'completed'),
                          total=2 * len(run['items']) + 1))
        return result

    @staticmethod
    def _read(db, run_id, owner=None):
        row = db.execute('SELECT owner,document FROM production_runs WHERE id=?', (run_id,)).fetchone()
        if row is None or owner is not None and row['owner'] != owner:
            raise ServiceError('not_found', 404)
        return json.loads(row['document'])

    @staticmethod
    def _write(db, run):
        raw = packed(run)
        require(len(raw.encode()) <= 512 * 1024, 'production_run_storage_limit', 413)
        db.execute('UPDATE production_runs SET document=? WHERE id=?', (raw, run['id']))

    def _save(self, run):
        with self.store.transaction() as db:
            self._write(db, run)

    def get(self, owner, run_id):
        with self.store.transaction() as db:
            run = self._read(db, run_id, owner)
            project = self.store._project(db, owner, run['project_id'])
            return self.public(run, current=self._matches(run, project, db))

    def list(self, owner, project_id):
        with self.store.transaction() as db:
            project = self.store._project(db, owner, project_id)
            runs = [json.loads(row[0]) for row in db.execute(
                'SELECT document FROM production_runs WHERE owner=? AND project_id=? ORDER BY rowid DESC',
                (owner, project_id))]
            return [self.public(run, current=self._matches(run, project, db)) for run in runs]

    def _freeze(self, project, *, film_aware=False):
        return freeze_production_inputs(self.store, project, film_aware=film_aware)

    def _matches(self, run, project, db):
        head = self.joint._head(db, run['owner'], run['project_id'])
        try:
            return (head['version_id'] == run['joint_plan_version_id']
                and head['applied_binding_sha256'] == self.joint._binding(project)
                and digest(self._freeze(project, film_aware=bool(run.get('continuity_schema')))) == run['binding_sha256'])
        except ServiceError:
            return False

    @staticmethod
    def _freeze_film(run):
        """Freeze a single bounded full-film clock after every WAV is measured.

        Padding includes the renderer's final partial-frame hold, so later
        authors receive the actual joined-video clock, not estimated speech.
        Old journals deliberately continue their original per-scene contract.
        """
        if not run.get('continuity_schema') or run.get('film') is not None:
            return
        frozen, rows, cursor = run['frozen'], [], 0
        for item, scene in zip(run['items'], frozen['scenes']):
            seconds = item['audio_seconds']
            require(seconds is not None and item['audio_version_id'] and item['audio_sha256'],
                    'production_run_film_requires_recordings', 409)
            frames = math.ceil((seconds + scene['leading_seconds'] + scene['trailing_seconds']) * 24)
            end = cursor + frames / 24
            rows.append(dict(scene_id=item['scene_id'], title=scene['title'],
                narration=scene['audio_input']['narration'], visual_intent=scene['visual_intent'],
                audio_version_id=item['audio_version_id'], audio_sha256=item['audio_sha256'],
                audio_seconds=seconds, start_seconds=cursor, end_seconds=end,
                leading_silence_seconds=scene['leading_seconds'],
                trailing_silence_seconds=frames / 24 - seconds - scene['leading_seconds']))
            cursor = end
        joint = frozen.get('joint_plan') or {}
        run['film'] = dict(project_id=run['project_id'], joint_plan_version_id=run['joint_plan_version_id'],
            content_binding_sha256=run['binding_sha256'], title=frozen['title'], script=frozen['script'],
            understanding_goal=joint.get('understanding_goal') or joint.get('summary') or frozen['title'],
            style=joint.get('visual_style') or frozen['style'], scenes=rows, total_seconds=cursor)
        run['film_sha256'] = digest(run['film'])

    @staticmethod
    def _film_context(run, index, predecessor):
        if not run.get('continuity_schema'):
            return None
        from agent_service.profiles.video_animation_production_contract import validate_film_context
        return validate_film_context(dict(schema=run['continuity_schema'], film=run['film'],
            film_sha256=run['film_sha256'], current_scene_index=index, predecessor=predecessor))

    def _intent_context(self, run, intent):
        index = intent['index']
        predecessor = (None if index == 0 else self.motion.continuity_predecessor(
            run['owner'], intent['predecessor_motion_version_id'], run['items'][index - 1]['audio_version_id']))
        context = self._film_context(run, index, predecessor)
        require(digest(context) == intent['film_context_sha256'],
                'production_run_continuity_binding_mismatch', 409)
        return context

    @staticmethod
    def _unfinished(run):
        return run['status'] in ACTIVE or run.get('draining') or run.get('blocked_reason')

    def start(self, owner, project_id, request, idem):
        require(type(request) is dict and set(request) == {'expected_revision', 'joint_plan_version_id'}
            and type(request['expected_revision']) is int and request['expected_revision'] >= 1
            and type(request['joint_plan_version_id']) is str and 1 <= len(request['joint_plan_version_id']) <= 100)
        require(type(idem) is str and re.fullmatch(r'[A-Za-z0-9_.:-]{8,128}', idem), 'idempotency_key_required')
        fingerprint = digest(dict(project_id=project_id, request=request))
        with self.store.transaction() as db:
            row = db.execute('SELECT request_hash,document FROM production_runs WHERE owner=? AND idem=?',
                             (owner, idem)).fetchone()
            if row:
                require(row[0] == fingerprint, 'idempotency_conflict', 409)
                run = json.loads(row[1])
                project = self.store._project(db, owner, project_id, include_deleted=True)
                lifecycle = self.store._lifecycle(db, project_id)
                current = not (lifecycle and lifecycle['deleted_at'] is not None)
                return self.public(run, current=current and self._matches(run, project, db))
            project = self.store._project(db, owner, project_id)
            self.store._revision(project, request['expected_revision'])
            head = self.joint._head(db, owner, project_id)
            require(head['version_id'] == request['joint_plan_version_id']
                and project.get('joint_plan', {}).get('version_id') == head['version_id'],
                'joint_plan_head_conflict', 409)
            require(head['applied_binding_sha256'] == self.joint._binding(project), 'joint_plan_stale', 409)
            require(1 <= len(project['scenes']) <= MAX_SCENES, 'production_run_requires_scenes', 409)
            for row in db.execute('SELECT document FROM production_runs WHERE owner=? AND project_id=?',
                                  (owner, project_id)):
                require(not self._unfinished(json.loads(row[0])), 'production_run_in_progress', 409)
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ('audio_operations', 'motion_operations', 'operations', 'explanation_operations',
                          'joint_plan_operations', 'video_renders', 'video_batches'):
                if table in tables:
                    for row in db.execute('SELECT document FROM ' + table + ' WHERE project_id=?', (project_id,)):
                        require(json.loads(row[0]).get('status') not in CHILD_ACTIVE | {'blocked'},
                                'production_run_project_busy', 409)
            require(db.execute('SELECT count(*) FROM production_runs WHERE project_id=?', (project_id,))
                    .fetchone()[0] < 64 and db.execute('SELECT count(*) FROM production_runs').fetchone()[0] < 1024,
                    'production_run_storage_limit', 409)
            frozen = self._freeze(project, film_aware=True)
            for scene in frozen['scenes']:
                require(all(type(scene[key]) in (int, float) and math.isfinite(scene[key])
                    and 0 <= scene[key] <= 60 for key in ('leading_seconds', 'trailing_seconds')),
                    'invalid_production_run_timing')
            run = dict(id=str(uuid4()), owner=owner, project_id=project_id, request_key=idem,
                joint_plan_version_id=head['version_id'], voice=frozen['provider']['voice'],
                binding_sha256=digest(frozen), frozen=frozen, status='queued', phase='audio',
                error=None, blocked_reason=None, cancel_requested=False, draining=False,
                created_at=time.time(), finished_at=None, draft_render_id=None, intents={},
                continuity_schema='video.film-context.v1', film=None, film_sha256=None,
                items=[dict(scene_id=scene['id'], title=scene['title'], status='queued', error=None,
                    audio_operation_id=None, audio_version_id=None, audio_sha256=None,
                    audio_seconds=None, motion_operation_id=None, motion_version_id=None)
                    for scene in frozen['scenes']])
            raw = packed(run)
            require(len(raw.encode()) <= 512 * 1024, 'production_run_storage_limit', 413)
            # Reserve the full-film copy before paying for recordings. Child
            # intents retain only exact references/hashes, never nested sources.
            probe = deepcopy(run)
            for item in probe['items']:
                item.update(audio_version_id='x' * 100, audio_sha256='a' * 64, audio_seconds=.1)
            self._freeze_film(probe)
            self._film_context(probe, 0, None)
            require(len(packed(probe).encode()) <= 448 * 1024, 'production_run_storage_limit', 413)
            db.execute('INSERT INTO production_runs VALUES (?, ?, ?, ?, ?, ?)',
                (run['id'], owner, project_id, idem, fingerprint, raw))
            return self.public(run, current=True)

    def cancel(self, owner, run_id):
        with self.store.lock:
            with self.store.transaction() as db:
                run = self._read(db, run_id, owner)
                self.store._project(db, owner, run['project_id'])
                if run['status'] in ACTIVE:
                    run['cancel_requested'] = True
                    run['draining'] = True
                    self._write(db, run)
            self._step(run, admit=False)
            return self.get(owner, run_id)

    def _lookup(self, run, intent):
        with self.store.transaction() as db:
            row = db.execute('SELECT document FROM ' + TABLES[intent['phase']] + ' WHERE owner=? AND idem=?',
                (run['owner'], intent['key'])).fetchone()
            if row is None:
                return None
            child = json.loads(row[0])
            require(child.get('project_id') == run['project_id'] and (intent['phase'] == 'draft'
                or child.get('scene_id') == run['items'][intent['index']]['scene_id']),
                'production_run_child_identity_mismatch', 409)
            if intent['phase'] == 'motion' and run.get('continuity_schema'):
                require(child.get('film_context_sha256') == intent['film_context_sha256'],
                    'production_run_continuity_binding_mismatch', 409)
            return child

    @staticmethod
    def _finish(run, status, error=None):
        run.update(status=status, error=error, finished_at=run.get('finished_at') or time.time())

    def _sync(self, run, intent, child):
        phase, index = intent['phase'], intent.get('index')
        item = run['items'][index] if index is not None else None
        if phase == 'draft':
            run['draft_render_id'] = child['id']
        else:
            item[phase + '_operation_id'] = child['id']
        status, error = child['status'], child.get('error')
        uncertain = ('uncertain' in (error or '') or 'unconfirmed' in (error or '')
            or phase == 'audio' and status in CHILD_TERMINAL - {'completed'}
                and child.get('submit_attempted') and child.get('upstream_stop_confirmed') is not True)
        intent['active'] = status in CHILD_ACTIVE
        if phase != 'audio' and intent['active']:
            # Agent/render workflows reconcile their original remote identity.
            # An ambiguous submit is not permission for another child, and is
            # not a terminal failure while that existing child is recoverable.
            if item is not None:
                item.update(status='running', error=error)
            if run['status'] in ACTIVE and not run['cancel_requested'] and not run.get('draining'):
                run['error'] = error
            return
        if uncertain:
            run['blocked_reason'] = error or 'production_run_upstream_stop_unconfirmed'
            run['draining'] = intent['active']
            self._finish(run, 'failed', run['blocked_reason'])
            if item is not None:
                item.update(status='failed', error=run['blocked_reason'])
            return
        if status in CHILD_ACTIVE:
            if item is not None:
                item['status'] = 'running'
            return
        require(status in CHILD_TERMINAL, 'production_run_child_state_unknown', 409)
        if status != 'completed':
            if item is not None:
                item.update(status='cancelled' if run['cancel_requested'] else 'failed', error=error or status)
            if not run['cancel_requested'] and run['status'] in ACTIVE:
                self._finish(run, 'failed', error or 'production_run_child_' + status)
            return
        if run['cancel_requested'] or run['status'] not in ACTIVE:
            return
        if phase == 'draft':
            self._finish(run, 'completed')
            run['phase'] = 'completed'
        elif phase == 'audio':
            version_id = child.get('candidate_id')
            require(version_id, 'production_run_child_no_result', 409)
            if item['audio_version_id'] == version_id:
                return
            audio = self.store.audio_version(run['owner'], version_id)
            frozen = run['frozen']['scenes'][index]
            info = inspect_wav(audio['data'])
            checksum = hashlib.sha256(audio['data']).hexdigest()
            require(audio['project_id'] == run['project_id'] and audio['scene_id'] == item['scene_id']
                and audio['input_sha256'] == narration_binding(frozen['audio_input'])
                and audio['sha256'] == checksum and audio['duration_seconds'] == info.duration_seconds,
                'production_run_audio_binding_mismatch', 409)
            require(info.duration_seconds + frozen['leading_seconds'] + frozen['trailing_seconds'] <= 60,
                    'motion_scene_duration_exceeded', 422)
            item.update(audio_version_id=version_id, audio_sha256=checksum,
                        audio_seconds=info.duration_seconds, status='queued', error=None)
        else:
            version_id = child.get('version_id')
            require(version_id, 'production_run_child_no_result', 409)
            if item['motion_version_id'] == version_id:
                return
            version = self.motion.candidate_version(run['owner'], version_id, item['audio_version_id'])
            require(version['project_id'] == run['project_id'] and version['scene_id'] == item['scene_id']
                and not version.get('stale'), 'production_run_motion_binding_mismatch', 409)
            if run.get('continuity_schema'):
                require(version.get('film_context_sha256') == intent['film_context_sha256'],
                        'production_run_continuity_binding_mismatch', 409)
            item.update(motion_version_id=version_id, status='completed', error=None)

    def _stop(self, run):
        pending = False
        for intent in run['intents'].values():
            child = self._lookup(run, intent)
            if child is None:
                continue
            if child['status'] in CHILD_ACTIVE:
                cancel = {'audio': self.store.cancel_audio, 'motion': self.motion.cancel,
                          'draft': self.production.cancel}[intent['phase']]
                cancel(run['owner'], child['id'])
                child = self._lookup(run, intent)
            self._sync(run, intent, child)
            pending = pending or intent['active']
        run['draining'] = pending
        if pending:
            run['blocked_reason'] = run.get('blocked_reason') or 'production_run_cancellation_pending'
        elif run.get('blocked_reason') == 'production_run_cancellation_pending':
            run['blocked_reason'] = None
        if run['cancel_requested'] and not pending and not run.get('blocked_reason'):
            self._finish(run, 'cancelled')
            for item in run['items']:
                if item['status'] != 'completed':
                    item['status'] = 'cancelled'

    def _step(self, run, *, admit=True):
        with self.store.transaction() as db:
            project = self.store._project(db, run['owner'], run['project_id'])
            matches = self._matches(run, project, db)
        if not matches and run['status'] in ACTIVE:
            self._finish(run, 'stale', 'production_run_inputs_changed')
            run['draining'] = True
        if run['cancel_requested'] or run.get('draining'):
            self._stop(run)
            self._save(run)
            return
        if run['status'] not in ACTIVE:
            return
        for intent in run['intents'].values():
            child = self._lookup(run, intent)
            if child:
                self._sync(run, intent, child)
        if run['status'] not in ACTIVE or any(i.get('active') for i in run['intents'].values()):
            self._save(run)
            return
        if all(item['audio_version_id'] for item in run['items']):
            total = sum(item['audio_seconds'] + frozen['leading_seconds'] + frozen['trailing_seconds']
                        for item, frozen in zip(run['items'], run['frozen']['scenes']))
            require(total <= MAX_PROJECT_SECONDS, 'media_project_too_long', 422)
            self._freeze_film(run)
            if run.get('film'):
                require(run['film']['total_seconds'] <= MAX_PROJECT_SECONDS, 'media_project_too_long', 422)
            run['phase'] = 'motion'
        if all(item['motion_version_id'] for item in run['items']):
            run['phase'] = 'draft'
        if not admit:
            self._save(run)
            return
        phase = run['phase']
        index = next((i for i, item in enumerate(run['items'])
                      if not item[phase + '_version_id']), None) if phase != 'draft' else None
        name = phase + (':' + str(index) if index is not None else '')
        intent = run['intents'].get(name)
        if intent is None:
            intent = dict(phase=phase, index=index, key='production-run:' + run['id'] + ':' + name,
                          request=None, active=False)
            if phase == 'motion' and run.get('continuity_schema'):
                predecessor = (None if index == 0 else self.motion.continuity_predecessor(
                    run['owner'], run['items'][index - 1]['motion_version_id'],
                    run['items'][index - 1]['audio_version_id']))
                intent['predecessor_motion_version_id'] = predecessor['motion_version_id'] if predecessor else None
                intent['film_context_sha256'] = digest(self._film_context(run, index, predecessor))
            run['intents'][name] = intent
        # A previous local start may have committed before its response was lost.
        child = self._lookup(run, intent)
        if child:
            self._sync(run, intent, child)
            self._save(run)
            return
        request = dict(expected_revision=project['revision'])
        if phase == 'motion':
            request.update(diagram_kind='creative', expected_semantic_binding_sha256=
                           run['frozen']['scenes'][index]['semantic_binding'])
        elif phase == 'draft':
            request['selections'] = [{key: item[key] for key in
                ('scene_id', 'motion_version_id', 'audio_version_id')} for item in run['items']]
        intent['request'] = request
        run.update(status='running', error=None)
        self._save(run)
        if phase == 'audio':
            self.store.start_audio(run['owner'], run['items'][index]['scene_id'], request, intent['key'])
        elif phase == 'motion':
            self.motion.start_candidate(run['owner'], run['items'][index]['scene_id'], request,
                intent['key'], audio_version_id=run['items'][index]['audio_version_id'],
                **(dict(film_context=self._intent_context(run, intent))
                   if run.get('continuity_schema') else {}))
        else:
            self.production.start_candidate_render(run['owner'], run['project_id'], request, intent['key'])
        self._sync(run, intent, self._lookup(run, intent))
        self._save(run)

    def tick(self):
        with self.store.lock:
            with self.store.transaction() as db:
                runs = [json.loads(row[0]) for row in db.execute('SELECT document FROM production_runs ORDER BY rowid')]
            for run in runs:
                if run['status'] not in ACTIVE and not run.get('draining'):
                    continue
                try:
                    self._step(run)
                except ServiceError as error:
                    self._finish(run, 'failed', error.code)
                    run['draining'] = True
                    self._save(run)
                except Exception:
                    # The local child may have committed: recover by stable key
                    # on the next tick rather than constructing another intent.
                    run['error'] = 'production_run_reconciliation_interrupted'
                    self._save(run)
