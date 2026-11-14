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
               'audio_seconds', 'motion_operation_id', 'motion_version_id', 'status', 'error',
               'module_operation_id', 'module_version_id', 'module_status', 'module_sha256', 'module_engine')
FILM_RENDERER = 'hyperframes-film'


def require(value, code='invalid_production_run_request', status=422):
    if not value:
        raise ServiceError(code, status)


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class ProductionRunStore:
    def __init__(self, store, joint_plan_store, motion_store, production, *, film_creation=None):
        self.store, self.joint, self.motion, self.production = store, joint_plan_store, motion_store, production
        self.film_creation = film_creation
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
            'created_at', 'finished_at', 'renderer_mode', 'draft_render_source')}
        raw_items = run.get('items') if type(run.get('items')) is list else []
        frozen = run.get('frozen') if type(run.get('frozen')) is dict else {}
        frozen_scenes = frozen.get('scenes') if type(frozen.get('scenes')) is list else []
        items = []
        for item, frozen in zip(raw_items, frozen_scenes):
            seconds = item.get('audio_seconds')
            duration = (math.ceil((seconds + frozen['leading_seconds'] + frozen['trailing_seconds']) * 24) / 24
                        if seconds is not None else None)
            items.append(dict({key: item.get(key) for key in PUBLIC_ITEM}, duration_seconds=duration,
                              candidate_current=bool(current and (item.get('motion_version_id')
                                                     or item.get('module_version_id')))))
        result.update(items=items, current=bool(current),
            duration_seconds=sum(item['duration_seconds'] for item in items)
                if items and all(item['duration_seconds'] is not None for item in items) else None,
            duration_source='measured_recordings_and_frame_quantized_pauses' if items else None,
            review_status='not_reviewed', selection='draft_only',
            progress=dict(done=sum(bool(item.get('audio_version_id'))
                                   + bool(item.get('motion_version_id') or item.get('module_version_id'))
                                   for item in raw_items) + int(run['status'] == 'completed'),
                          total=2 * len(raw_items) + 1))
        if 'audio_reuse' in run:
            result.update(audio_reuse=deepcopy(run['audio_reuse']),
                audio_reuse_sha256=run['audio_reuse_sha256'], work_budget=deepcopy(run['work_budget']))
        return result

    @staticmethod
    def _uses_film(run):
        return run.get('renderer_mode') == FILM_RENDERER

    def _film_service(self):
        require(self.film_creation is not None, 'production_run_film_unavailable', 503)
        return self.film_creation

    @staticmethod
    def _check_film_configuration(value):
        require(type(value) is dict and value.get('available') is True
            and value.get('renderer_mode') == FILM_RENDERER
            and type(value.get('configuration_sha256')) is str
            and re.fullmatch(r'[0-9a-f]{64}', value['configuration_sha256'])
            and type(value.get('max_seconds')) in (int, float)
            and 0 < value['max_seconds'] <= 60 and value.get('fps') == 24,
            'production_run_film_unavailable', 503)
        require(len(packed(value).encode()) <= 16384, 'production_run_film_configuration_invalid', 503)
        return deepcopy(value)

    def _require_film_configuration(self, run):
        current = self._check_film_configuration(self._film_service().readiness())
        require(current == run['film_creation_configuration'],
            'production_run_film_configuration_changed', 409)

    @staticmethod
    def _module_targets(run_id, count):
        prefix = run_id.replace('-', '')[:20]
        return [dict(module_id=f'film-{prefix}-scene-{index:03d}',
            namespace=f'f{prefix}_scene_{index:03d}', transition_target=None if index == 0 else dict(
                module_id=f'film-{prefix}-transition-{index:03d}', namespace=f'f{prefix}_transition_{index:03d}'))
            for index in range(count)]

    def _frozen_module_package(self, run, index):
        from .creative_film_module_contract import validate_module_package
        item = run['items'][index]
        package = validate_module_package(self._film_service().module_package(run['owner'], item['module_version_id']))
        require(digest(package) == item['module_package_sha256'],
            'production_run_module_package_changed', 409)
        return package

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
        try:
            head = self.joint._head(db, run['owner'], run['project_id'])
            return (head['version_id'] == run['joint_plan_version_id']
                and head['applied_binding_sha256'] == self.joint._binding(project)
                and digest(self._freeze(project, film_aware=bool(run.get('continuity_schema')))) == run['binding_sha256'])
        except (ServiceError, KeyError, TypeError):
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

    @staticmethod
    def _reuse_audio(db, run, source_run_id):
        """Admit original WAVs, without adopting heads or reopening old work.

        Read versions on the caller's transaction: AudioStore.audio_version
        would open another BEGIN IMMEDIATE connection while this one is held.
        Visual intent can change, but speech inputs and scene order cannot.
        """
        source = ProductionRunStore._read(db, source_run_id, run['owner'])
        source_project = db.execute('SELECT project_id FROM production_runs WHERE id=?', (source_run_id,)).fetchone()[0]
        require(source_project == source.get('project_id') == run['project_id'], 'not_found', 404)
        require(source.get('id') == source_run_id and source.get('owner') == run['owner'],
            'production_run_audio_reuse_mismatch', 409)
        require(source.get('status') == 'completed' and not source.get('draining')
            and not source.get('blocked_reason') and not source.get('cancel_requested'),
            'production_run_audio_reuse_source_not_completed', 409)
        frozen, items = source.get('frozen'), source.get('items')
        scenes = frozen.get('scenes') if type(frozen) is dict else None
        require(type(scenes) is list and type(items) is list
            and len(scenes) == len(items) == len(run['items'])
            and digest(frozen) == source.get('binding_sha256'),
            'production_run_audio_reuse_mismatch', 409)
        source_film = source.get('film')
        if source_film is not None:
            require(type(source_film) is dict and digest(source_film) == source.get('film_sha256')
                and source_film.get('content_binding_sha256') == source['binding_sha256']
                and type(source_film.get('scenes')) is list and len(source_film['scenes']) == len(items),
                'production_run_audio_reuse_mismatch', 409)
            for item, recording in zip(items, source_film['scenes']):
                require(type(item) is dict and type(recording) is dict and all(
                    item.get(key) == recording.get(key)
                    for key in ('scene_id', 'audio_version_id', 'audio_sha256', 'audio_seconds')),
                    'production_run_audio_reuse_mismatch', 409)
        ProductionRunStore._require_audio_reuse(source)
        recordings = []
        for previous, original, current, target in zip(scenes, items, run['frozen']['scenes'], run['items']):
            require(type(previous) is dict and type(original) is dict
                and previous.get('id') == original.get('scene_id') == current['id']
                and previous.get('audio_input') == current['audio_input'],
                'production_run_audio_reuse_mismatch', 409)
            version_id = original.get('audio_version_id')
            require(type(version_id) is str and 1 <= len(version_id) <= 100,
                'production_run_audio_reuse_unavailable', 409)
            row = db.execute('SELECT id,project_id,scene_id,document,data FROM audio_versions WHERE id=?',
                (version_id,)).fetchone()
            require(row is not None and row['project_id'] == run['project_id']
                and row['scene_id'] == current['id'], 'production_run_audio_reuse_unavailable', 409)
            try:
                audio, data = json.loads(row['document']), bytes(row['data'])
                info = inspect_wav(data)
            except (ServiceError, ValueError, TypeError):
                raise ServiceError('production_run_audio_reuse_unavailable', 409) from None
            checksum = hashlib.sha256(data).hexdigest()
            input_sha256 = narration_binding(current['audio_input'])
            require(type(audio) is dict and audio.get('id') == version_id
                and audio.get('input_sha256') == input_sha256
                and audio.get('narration') == current['audio_input']['narration']
                and audio.get('settings') == current['audio_input']['settings']
                and audio.get('sha256') == original.get('audio_sha256') == checksum
                and audio.get('duration_seconds') == original.get('audio_seconds') == info.duration_seconds,
                'production_run_audio_reuse_mismatch', 409)
            require(info.duration_seconds + current['leading_seconds'] + current['trailing_seconds'] <= 60,
                'motion_scene_duration_exceeded', 422)
            target.update(audio_version_id=version_id, audio_sha256=checksum, audio_seconds=info.duration_seconds)
            recordings.append(dict(scene_id=current['id'], audio_version_id=version_id,
                audio_sha256=checksum, audio_seconds=info.duration_seconds, input_sha256=input_sha256))
        run['audio_reuse'] = dict(schema='video.production-run-audio-reuse.v1', source_run_id=source_run_id,
            source_frozen_sha256=source['binding_sha256'], recordings=recordings)
        # These limits belong to this run. No source intent, allowance or
        # consumption is transferred; the existing single-attempt journal
        # admits only fresh visual children and one fresh whole-film render.
        run['work_budget'] = dict(schema='video.production-run-work-budget.v1', audio_operations=0,
            visual_operations=len(run['items']), draft_renders=1, automatic_retries=0)
        run['audio_reuse_sha256'] = digest(dict(receipt=run['audio_reuse'], work_budget=run['work_budget']))
        ProductionRunStore._freeze_film(run)
        require(run['film']['total_seconds'] <= MAX_PROJECT_SECONDS, 'media_project_too_long', 422)
        if ProductionRunStore._uses_film(run):
            require(run['film']['total_seconds'] <= run['film_creation_configuration']['max_seconds'],
                'production_run_film_too_long', 422)

    @staticmethod
    def _require_audio_reuse(run):
        if 'audio_reuse' not in run:
            return
        receipt, budget = run['audio_reuse'], run.get('work_budget')
        require(digest(dict(receipt=receipt, work_budget=budget)) == run.get('audio_reuse_sha256')
            and type(receipt) is dict and type(receipt.get('recordings')) is list
            and len(receipt['recordings']) == len(run['items']),
            'production_run_audio_reuse_mismatch', 409)
        for recording, item, scene in zip(receipt['recordings'], run['items'], run['frozen']['scenes']):
            require(all(item.get(key) == recording.get(key)
                for key in ('scene_id', 'audio_version_id', 'audio_sha256', 'audio_seconds'))
                and recording['input_sha256'] == narration_binding(scene['audio_input'])
                and item.get('audio_operation_id') is None,
                'production_run_audio_reuse_mismatch', 409)

    def start(self, owner, project_id, request, idem):
        required = {'expected_revision', 'joint_plan_version_id'}
        require(type(request) is dict and required <= set(request) <= required | {'reuse_audio_from_run_id'}
            and type(request['expected_revision']) is int and request['expected_revision'] >= 1
            and type(request['joint_plan_version_id']) is str and 1 <= len(request['joint_plan_version_id']) <= 100
            and ('reuse_audio_from_run_id' not in request or type(request['reuse_audio_from_run_id']) is str
                and 1 <= len(request['reuse_audio_from_run_id']) <= 100))
        require(type(idem) is str and re.fullmatch(r'[A-Za-z0-9_.:-]{8,128}', idem), 'idempotency_key_required')
        fingerprint = digest(dict(project_id=project_id, request=request))
        configuration = None
        if self.film_creation is not None:
            # The facade authorizes through this same SQLite store and may do
            # network preflight. Do not hold BEGIN IMMEDIATE while calling it.
            # Historical idempotent replays do not need fresh admission.
            with self.store.transaction() as db:
                replay = db.execute('SELECT 1 FROM production_runs WHERE owner=? AND idem=?', (owner, idem)).fetchone()
            if replay is None:
                configuration = self._check_film_configuration(
                    self.film_creation.freeze_configuration(owner, project_id))
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
                          'joint_plan_operations', 'video_renders', 'video_batches',
                          'film_module_operations', 'film_render_operations'):
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
            if configuration is not None:
                # This is an explicit plan budget, not a prediction of exact
                # TTS timing. Recheck the real frame clock after all recordings.
                planned_seconds = sum(scene['duration_seconds'] for scene in frozen['scenes'])
                pause_seconds = sum(scene['leading_seconds'] + scene['trailing_seconds']
                    for scene in frozen['scenes'])
                require(max(planned_seconds, pause_seconds) <= configuration['max_seconds'],
                    'production_run_film_plan_too_long', 422)
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
            if configuration is not None:
                run.update(renderer_mode=FILM_RENDERER, draft_render_source=FILM_RENDERER,
                    film_creation_configuration=configuration,
                    module_targets=self._module_targets(run['id'], len(run['items'])))
                for item in run['items']:
                    item.update(module_operation_id=None, module_version_id=None, module_status='queued',
                        module_sha256=None, module_package_sha256=None, module_engine=None)
            if 'reuse_audio_from_run_id' in request:
                self._reuse_audio(db, run, request['reuse_audio_from_run_id'])
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
            if run['status'] in ACTIVE or run.get('draining'):
                self._step(run, admit=False)
            return self.get(owner, run_id)

    def _lookup(self, run, intent):
        if self._uses_film(run) and intent['phase'] in {'module', 'draft'}:
            creation = self._film_service()
            lookup = creation.module_by_request_key if intent['phase'] == 'module' else creation.render_by_request_key
            try:
                child = lookup(run['owner'], intent['key'])
            except ServiceError as error:
                if error.code == 'not_found' and error.status == 404:
                    return None
                raise
            if child is None:
                return None
            require(child.get('project_id') == run['project_id']
                and child.get('request_key') == intent['key']
                and child.get('request_sha256') == intent['request_sha256']
                and child.get('configuration_sha256') == run['film_creation_configuration']['configuration_sha256']
                and child.get('film_sha256') == run['film_sha256']
                and (intent['phase'] == 'draft'
                     or child.get('scene_id') == run['items'][intent['index']]['scene_id']),
                'production_run_child_identity_mismatch', 409)
            return child
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
        if phase == 'module':
            item['module_status'] = status
        uncertain = ('uncertain' in (error or '') or 'unconfirmed' in (error or '')
            or phase == 'audio' and status in CHILD_TERMINAL - {'completed'}
                and child.get('submit_attempted') and child.get('upstream_stop_confirmed') is not True
            or self._uses_film(run) and phase in {'module', 'draft'}
                and status in CHILD_TERMINAL - {'completed'} and child.get('stop_proven') is not True)
        if self._uses_film(run) and phase in {'module', 'draft'} and child.get('stop_proven') is True:
            uncertain = False
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
            if self._uses_film(run):
                self._validate_film_draft(run, child)
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
        elif phase == 'module':
            self._sync_module(run, intent, child)
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

    def _sync_module(self, run, intent, child):
        from .creative_film_module_contract import validate_module_package, predecessor_from_package
        from .hyperframes_film_project import module_identity
        index = intent['index']
        item = run['items'][index]
        version_id = child.get('version_id')
        require(version_id, 'production_run_child_no_result', 409)
        if item.get('module_version_id'):
            require(item['module_version_id'] == version_id
                and item['module_input_sha256'] == child.get('input_sha256'),
                'production_run_module_package_changed', 409)
            return
        package = validate_module_package(self._film_service().module_package(run['owner'], version_id))
        document, module = package['input_document'], package['result']['module']
        context = document['film_context']
        predecessor = None if index == 0 else predecessor_from_package(self._frozen_module_package(run, index - 1))
        require(context['film'] == run['film'] and context['film_sha256'] == run['film_sha256']
            and context['current_scene_index'] == index and context['predecessor'] == predecessor
            and document['target'] == run['module_targets'][index]
            and document['scene']['scene_id'] == item['scene_id']
            and document['scene']['audio_sha256'] == item['audio_sha256']
            and child.get('input_sha256') == package['input_sha256'],
            'production_run_module_binding_mismatch', 409)
        checksum = digest(package)
        item.update(module_version_id=version_id, module_sha256=module_identity(module),
            module_package_sha256=checksum, module_input_sha256=package['input_sha256'],
            module_engine=module['engine'], module_status='completed',
            status='completed', error=None)

    @staticmethod
    def _validate_film_draft(run, child):
        metadata = child.get('result')
        require(type(metadata) is dict, 'production_run_film_result_invalid', 409)
        export = metadata.get('film_export')
        frames = round(run['film']['total_seconds'] * 24)
        require(metadata.get('visual_mode') == FILM_RENDERER and metadata.get('quality') == 'draft'
            and metadata.get('subtitles') is False and metadata.get('fps') == 24
            and metadata.get('audio_sample_rate') == 48000 and metadata.get('audio_channels') == 2
            and metadata.get('snapshot_sha256') == child.get('snapshot_sha256')
            and type(child.get('snapshot_sha256')) is str
            and re.fullmatch(r'[0-9a-f]{64}', child['snapshot_sha256'])
            and type(export) is dict and export.get('film_sha256') == run['film_sha256']
            and export.get('concatenated_scene_videos') is False
            and export.get('clock') == dict(fps=24, frame_count=frames, sample_rate=48000, sample_count=frames * 2000),
            'production_run_film_result_invalid', 409)
        run['render_snapshot_sha256'] = child['snapshot_sha256']

    def _stop(self, run):
        pending = False
        for intent in run['intents'].values():
            child = self._lookup(run, intent)
            if child is None:
                continue
            if child['status'] in CHILD_ACTIVE:
                if self._uses_film(run) and intent['phase'] in {'module', 'draft'}:
                    creation = self._film_service()
                    cancel = creation.cancel_module if intent['phase'] == 'module' else creation.cancel_render
                else:
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
        self._require_audio_reuse(run)
        for intent in run['intents'].values():
            child = self._lookup(run, intent)
            if child:
                self._sync(run, intent, child)
        if run['status'] not in ACTIVE or any(i.get('active') for i in run['intents'].values()):
            self._save(run)
            return
        if self._uses_film(run):
            self._step_film(run, project, admit=admit)
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

    def _film_child_request(self, run, phase, index, revision):
        configuration_sha256 = run['film_creation_configuration']['configuration_sha256']
        if phase == 'audio':
            return dict(expected_revision=revision)
        if phase == 'module':
            if index:
                self._frozen_module_package(run, index - 1)
            return dict(expected_revision=revision, film=deepcopy(run['film']), film_sha256=run['film_sha256'],
                current_scene_index=index,
                predecessor_version_id=None if index == 0 else run['items'][index - 1]['module_version_id'],
                target=deepcopy(run['module_targets'][index]), instruction='',
                configuration_sha256=configuration_sha256)
        for module_index in range(len(run['items'])):
            self._frozen_module_package(run, module_index)
        return dict(expected_revision=revision, film=deepcopy(run['film']), film_sha256=run['film_sha256'],
            module_version_ids=[item['module_version_id'] for item in run['items']],
            module_targets=deepcopy(run['module_targets']), production_run_id=run['id'], timeout_seconds=600,
            configuration_sha256=configuration_sha256)

    def _step_film(self, run, project, *, admit):
        """New runs author native modules and submit exactly one full project.

        The journal keeps only stable child references and request hashes; it
        never duplicates each large film request, package or render snapshot.
        """
        if all(item['audio_version_id'] for item in run['items']):
            self._freeze_film(run)
            require(run['film']['total_seconds'] <= run['film_creation_configuration']['max_seconds'],
                'production_run_film_too_long', 422)
            run['phase'] = 'module'
            if all(item['module_version_id'] for item in run['items']):
                run['phase'] = 'draft'
        else:
            run['phase'] = 'audio'
        if not admit:
            self._save(run)
            return
        phase = run['phase']
        version_field = 'audio_version_id' if phase == 'audio' else 'module_version_id'
        index = next((i for i, item in enumerate(run['items']) if not item[version_field]), None)
        if phase == 'draft':
            index = None
        name = phase + (':' + str(index) if index is not None else '')
        intent = run['intents'].get(name)
        revision = project['revision'] if intent is None else intent['request']['expected_revision']
        request = self._film_child_request(run, phase, index, revision)
        if intent is None:
            intent = dict(phase=phase, index=index, key='production-run:' + run['id'] + ':' + name,
                request=dict(expected_revision=revision), request_sha256=digest(request), active=False)
            run['intents'][name] = intent
        require(intent['request_sha256'] == digest(request), 'production_run_film_request_changed', 409)
        child = self._lookup(run, intent)
        if child is not None:
            self._sync(run, intent, child)
            self._save(run)
            return
        self._require_film_configuration(run)
        run.update(status='running', error=None)
        self._save(run)  # Intent is durable before any local child admission.
        if phase == 'audio':
            self.store.start_audio(run['owner'], run['items'][index]['scene_id'], request, intent['key'])
        elif phase == 'module':
            self._film_service().start_module(run['owner'], run['project_id'], run['items'][index]['scene_id'],
                request, intent['key'])
        else:
            self._film_service().start_render(run['owner'], run['project_id'], request, intent['key'])
        child = self._lookup(run, intent)
        require(child is not None, 'production_run_child_not_visible', 503)
        self._sync(run, intent, child)
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
