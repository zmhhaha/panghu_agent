"""Resumable draft journals with recorded or explicit visual clocks.

Only local child admissions run here. Their request and stable key are committed
before calling existing stores, so a restart finds or replays the same child.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
import sqlite3
import time
from uuid import uuid4

from agent_service.contracts import ServiceError, digest
from .audio_store import narration_binding, speech_options
from .media import MAX_PROJECT_SECONDS, MAX_SCENES
from .production_film_context import freeze_production_inputs
from .speech import inspect_wav
from .hyperframes_film_limits import MAX_SECONDS, render_timeout


ACTIVE = frozenset({'queued', 'running'})
CHILD_ACTIVE = frozenset({'queued', 'submitting', 'running', 'cancelling'})
CHILD_TERMINAL = frozenset({'completed', 'failed', 'needs_input', 'cancelled', 'timed_out'})
TABLES = {'audio': 'audio_operations', 'motion': 'motion_operations', 'draft': 'video_renders'}
PUBLIC_ITEM = ('scene_id', 'title', 'audio_operation_id', 'audio_version_id', 'audio_sha256',
               'audio_seconds', 'motion_operation_id', 'motion_version_id', 'status', 'error',
               'module_operation_id', 'module_version_id', 'module_status', 'module_sha256', 'module_engine')
FILM_RENDERER = 'hyperframes-film'
SILENT_FILM_CLOCK = 'video.hyperframes-film-clock.v1'
RESOURCE_PLAN_SECONDS = 7200


def require(value, code='invalid_production_run_request', status=422):
    if not value:
        raise ServiceError(code, status)


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class ProductionRunStore:
    def __init__(self, store, joint_plan_store, motion_store, production, *, film_creation=None, sequence_store=None,
            resource_stage=None, scene_space_adapter=None):
        self.store, self.joint, self.motion, self.production = store, joint_plan_store, motion_store, production
        self.film_creation = film_creation
        self.sequence_store = sequence_store
        self.scene_space_adapter = scene_space_adapter
        if resource_stage is None and film_creation is not None:
            from .production_resource_stage import ProductionResourceStage
            resource_stage = ProductionResourceStage(store, film_creation)
        self.resource_stage = resource_stage
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
            'created_at', 'finished_at', 'renderer_mode', 'draft_render_source', 'library_binding')}
        silent = run.get('audio_mode') == 'silent'
        if 'audio_mode' in run:
            result['audio_mode'] = run['audio_mode']
        if 'scene_source_recovery' in run:
            result['scene_source_recovery'] = {key:deepcopy(run['scene_source_recovery'][key]) for key in (
                'schema','source_run_id','source_run_sha256','source_scene_sha256','source_task_sha256',
                'source_usage_sha256','author_job_id','author_input_sha256','author_result_sha256',
                'source_sha256','plan_sha256','configuration_sha256','film_sha256',
                'measured_author_usage','original_deadline_at','execution_deadline_at','extra_author_jobs')}
        if 'rendering_route' in run:
            result['rendering_route'] = deepcopy(run['rendering_route'])
        raw_items = run.get('items') if type(run.get('items')) is list else []
        frozen = run.get('frozen') if type(run.get('frozen')) is dict else {}
        frozen_scenes = frozen.get('scenes') if type(frozen.get('scenes')) is list else []
        items = []
        for item, frozen in zip(raw_items, frozen_scenes):
            seconds = item.get('audio_seconds')
            duration = (math.ceil(frozen['duration_seconds'] * 24) / 24 if silent else
                math.ceil((seconds + frozen['leading_seconds'] + frozen['trailing_seconds']) * 24) / 24
                if seconds is not None else None)
            items.append(dict({key: item.get(key) for key in PUBLIC_ITEM}, duration_seconds=duration,
                              candidate_current=bool(current and (item.get('motion_version_id')
                                                     or item.get('module_version_id')
                                                     or run.get('renderer_mode')=='scene-space' and item.get('world_source_sha256')))))
            if silent:
                items[-1].update(audio_status=item['audio_status'], clock_source='silent')
        result.update(items=items, current=bool(current),
            duration_seconds=sum(item['duration_seconds'] for item in items)
                if items and all(item['duration_seconds'] is not None for item in items) else None,
            duration_source=('planned_visual_durations_on_24fps_clock' if silent
                else 'measured_recordings_and_frame_quantized_pauses') if items else None,
            review_status='not_reviewed', selection='draft_only',
            progress=dict(done=sum((0 if silent else bool(item.get('audio_version_id')))
                                   + bool(item.get('motion_version_id') or item.get('module_version_id'))
                                   for item in raw_items) + int(run['status'] == 'completed'),
                          total=(1 if silent else 2) * len(raw_items) + 1))
        if 'audio_reuse' in run:
            result.update(audio_reuse=deepcopy(run['audio_reuse']),
                audio_reuse_sha256=run['audio_reuse_sha256'], work_budget=deepcopy(run['work_budget']))
        if 'narration_resumes' in run:
            result['narration_resumes'] = deepcopy(run['narration_resumes'])
        if 'module_reuse' in run:
            result.update(module_reuse=deepcopy(run['module_reuse']),
                module_reuse_sha256=run['module_reuse_sha256'],
                module_work_budget=deepcopy(run['module_work_budget']))
        if 'source_runtime_admission_receipt' in run:
            result['source_runtime_admission_receipt'] = deepcopy(run['source_runtime_admission_receipt'])
        if 'sequence_registration' in run:
            result.update(sequence_id=run['sequence_registration']['sequence_id'],
                sequence_registration=deepcopy(run['sequence_registration']))
        if 'resource_authoring' in run:
            result.update(resource_authoring=deepcopy(run['resource_authoring']),
                resource_work_budget=deepcopy(run['resource_work_budget']))
        if 'resource_plan_registration' in run:
            result.update(resource_plan_registration=deepcopy(run['resource_plan_registration']),
                resources=deepcopy(run.get('resource_progress', {})))
        if 'scene_space' in run:
            result['scene_space'] = deepcopy(run['scene_space'])
            for public_item, item in zip(result['items'],raw_items):
                public_item.update(world_source_sha256=item.get('world_source_sha256'),
                    scene_space_plan_sha256=item.get('scene_space_plan_sha256'))
            result['progress']['done'] = sum(bool(item.get('world_source_sha256')) for item in raw_items) + int(run['status']=='completed')
        if 'resource_deadline_resumes' in run:
            result['resource_deadline_resumes'] = deepcopy(run['resource_deadline_resumes'])
        if 'module_retries' in run:
            result['module_retries'] = deepcopy(run['module_retries'])
        return result

    @staticmethod
    def _registration(sequence_id=None, parent_source=None):
        return dict(schema='video.production-run-sequence-registration.v1', status='awaiting_draft',
            sequence_id=sequence_id, source=None, parent_source=deepcopy(parent_source), error=None)

    def _verified_sequence_source(self, run):
        """Resolve actual immutable film bytes and the already admitted snapshot.

        This is a local custody check, not another render or media observation.
        Call outside a business transaction: creation owns its own connections.
        """
        if self._uses_scene_space(run):
            require(run['status']=='completed' and not (run.get('cancel_requested') or run.get('draining')
                or run.get('blocked_reason')),'production_run_sequence_source_not_completed',409)
            require(digest(run['frozen'])==run['binding_sha256'] and digest(run['film'])==run['film_sha256'],
                'production_run_sequence_source_changed',409)
            return self._scene_space_service().verified_source(run)
        from .film_render_adapter import validate_film_render_result
        self._require_module_reuse(run)
        require(self._uses_film(run) and run['status'] == 'completed'
            and not (run.get('cancel_requested') or run.get('draining') or run.get('blocked_reason')),
            'production_run_sequence_source_not_completed', 409)
        require(digest(run['frozen']) == run['binding_sha256']
            and digest(run['film']) == run['film_sha256']
            and run['film']['content_binding_sha256'] == run['binding_sha256'],
            'production_run_sequence_source_changed', 409)
        intent = run.get('intents', {}).get('draft')
        require(type(intent) is dict and intent.get('phase') == 'draft',
            'production_run_sequence_source_invalid', 409)
        child = self._lookup(run, intent)
        require(type(child) is dict and child.get('id') == run.get('draft_render_id')
            and child.get('status') == 'completed' and child.get('stop_proven') is True
            and not child.get('cancel_requested') and child.get('error') is None,
            'production_run_sequence_source_invalid', 409)
        previous_snapshot = run.get('render_snapshot_sha256')
        self._validate_film_draft(run, child)
        require(previous_snapshot in (None, run['render_snapshot_sha256']),
            'production_run_sequence_source_changed', 409)
        snapshot = self._film_service().render_snapshot(child['id'])
        require(snapshot['sha256'] == run['render_snapshot_sha256']
            and snapshot['project_id'] == run['project_id'] and snapshot['production_run_id'] == run['id']
            and snapshot['film_sha256'] == run['film_sha256']
            and snapshot['module_sha256'] == {target['module_id']: item['module_sha256']
                for target, item in zip(run['module_targets'], run['items'])},
            'production_run_sequence_snapshot_mismatch', 409)
        checked = validate_film_render_result(child['result'], snapshot,
            runtime_fingerprint=snapshot['runtime_fingerprint'])
        raw = self._film_service().video(run['owner'], child['id'])
        require(type(raw) is bytes and len(raw) == checked['bytes']
            and hashlib.sha256(raw).hexdigest() == checked['sha256'],
            'production_run_sequence_media_changed', 409)
        return dict(source=dict(run_id=run['id'], version_id=child['id'], sha256=checked['sha256']),
            snapshot_sha256=snapshot['sha256'], film_sha256=run['film_sha256'],
            request_sha256=child['request_sha256'], configuration_sha256=child['configuration_sha256'])

    def _sequence_parent(self, owner, project_id, source_run_id, seen=None):
        """A mutation-side preparation; GET/list never register historical work."""
        with self.store.lock:
            with self.store.transaction() as db:
                run = self._read(db, source_run_id, owner)
                self.store._project(db, owner, project_id)
                require(run['owner'] == owner and run['project_id'] == project_id, 'not_found', 404)
            seen = set() if seen is None else set(seen)
            require(source_run_id not in seen and len(seen) < 64,
                'production_run_sequence_lineage_invalid', 409)
            seen.add(source_run_id)
            try:
                self._register_sequence(run, seen=seen)
            except ServiceError as error:
                self._registration_error(run, error.code, blocked=True)
                raise
            except (OSError, sqlite3.Error):
                self._registration_error(run, 'production_run_sequence_unavailable', blocked=True)
                raise ServiceError('production_run_sequence_unavailable', 503) from None
            except Exception:
                self._registration_error(run, 'production_run_sequence_registration_interrupted', blocked=False)
                raise ServiceError('production_run_sequence_registration_interrupted', 503) from None
            registration = run['sequence_registration']
            require(registration['status'] == 'registered', 'production_run_sequence_registration_pending', 409)
            return dict(sequence_id=registration['sequence_id'], source=deepcopy(registration['source']),
                snapshot_sha256=run['sequence_intent']['binding']['snapshot_sha256'])

    def _registration_error(self, run, code, *, blocked):
        if 'sequence_registration' not in run:
            run['sequence_registration'] = self._registration()
        run['sequence_registration'].update(status='blocked' if blocked else 'pending', error=code)
        self._save(run)

    def _require_sequence_admission(self, run):
        if 'sequence_registration' not in run:
            return
        require(self.sequence_store is not None, 'production_run_sequence_unavailable', 503)
        try:
            # Read-only lookup also checks the existing sidecar before any new
            # paid child. A lost database is never recreated by this path.
            self.sequence_store.source_for_run(run['owner'], run['project_id'], run['id'])
            lineage = run['sequence_lineage']
            require(digest(lineage) == run['sequence_lineage_sha256'],
                'production_run_sequence_lineage_changed', 409)
            parent = lineage['parent']
            if parent is not None:
                prior = self.sequence_store.source_for_run(run['owner'], run['project_id'],
                    parent['source']['run_id'], parent['source']['version_id'])
                require(prior is not None and prior['source'] == parent['source']
                    and prior['sequence']['id'] == parent['sequence_id'],
                    'production_run_sequence_parent_missing', 409)
        except (OSError, sqlite3.Error):
            raise ServiceError('production_run_sequence_unavailable', 503) from None

    def _freeze_sequence_intent(self, run, binding, *, seen=None):
        lineage = run.get('sequence_lineage')
        if lineage is None:
            parent = None
            if 'audio_reuse' in run:
                parent = self._sequence_parent(run['owner'], run['project_id'],
                    run['audio_reuse']['source_run_id'], seen=seen)
            lineage = dict(origin_run_id=run['id'] if parent is None else None, parent=parent)
            run['sequence_lineage'], run['sequence_lineage_sha256'] = lineage, digest(lineage)
        require(digest(lineage) == run.get('sequence_lineage_sha256'),
            'production_run_sequence_lineage_changed', 409)
        parent = lineage['parent']
        registration = run.get('sequence_registration') or self._registration(
            parent['sequence_id'] if parent else None, parent['source'] if parent else None)
        require(registration.get('source') in (None, binding['source']),
            'production_run_sequence_source_changed', 409)
        existing = self.sequence_store.source_for_run(run['owner'], run['project_id'],
            run['id'], binding['source']['version_id'])
        sequence_id = parent['sequence_id'] if parent else registration['sequence_id']
        # A known binding may be recovered; a missing known binding must never
        # become a fresh sequence with replenished allowances.
        if sequence_id is not None:
            self.sequence_store.get(run['owner'], run['project_id'], sequence_id)
        if existing is not None:
            require(existing['source'] == binding['source']
                and sequence_id in (None, existing['sequence']['id']),
                'production_run_sequence_binding_changed', 409)
        elif registration['status'] == 'registered':
            raise ServiceError('production_run_sequence_binding_missing', 409)
        if parent:
            prior = self.sequence_store.source_for_run(run['owner'], run['project_id'],
                parent['source']['run_id'], parent['source']['version_id'])
            require(prior is not None and prior['source'] == parent['source']
                and prior['sequence']['id'] == parent['sequence_id'],
                'production_run_sequence_parent_missing', 409)
        action = 'bind' if parent else 'create'
        revision = (self.sequence_store.get(run['owner'], run['project_id'], sequence_id)['revision']
            if parent else None)
        intent = dict(schema='video.production-run-sequence-intent.v1', action=action,
            key='production-sequence:' + run['id'] + ':' + action + ':0',
            binding=deepcopy(binding), sequence_id=sequence_id, expected_revision=revision,
            parent=deepcopy(parent), attempt=0)
        registration.update(status='pending', source=deepcopy(binding['source']), error=None)
        run['sequence_registration'], run['sequence_intent'] = registration, intent
        run['sequence_intent_sha256'] = digest(intent)
        self._save(run)  # Durable outbox precedes the independent ledger write.

    def _register_sequence(self, run, *, seen=None):
        require(self.sequence_store is not None, 'production_run_sequence_unavailable', 503)
        binding = self._verified_sequence_source(run)
        if 'sequence_intent' not in run:
            self._freeze_sequence_intent(run, binding, seen=seen)
        intent = run['sequence_intent']
        require(digest(intent) == run.get('sequence_intent_sha256')
            and intent['binding'] == binding
            and digest(run['sequence_lineage']) == run['sequence_lineage_sha256']
            and intent['parent'] == run['sequence_lineage']['parent']
            and run['sequence_registration']['source'] == binding['source']
            and run['sequence_registration']['parent_source'] == (intent['parent']['source'] if intent['parent'] else None),
            'production_run_sequence_intent_changed', 409)
        owner, project_id, source = run['owner'], run['project_id'], binding['source']
        existing = self.sequence_store.source_for_run(owner, project_id, run['id'], source['version_id'])
        recorded_id = run['sequence_registration']['sequence_id']
        if existing is not None:
            require(existing['source'] == source and recorded_id in (None, existing['sequence']['id'])
                and intent['sequence_id'] in (None, existing['sequence']['id']),
                'production_run_sequence_binding_changed', 409)
            if intent['action'] == 'create':
                require(existing['sequence']['origin_run_id'] == run['id']
                    and existing['sequence']['origin_source'] == source,
                    'production_run_sequence_lineage_invalid', 409)
            sequence_id = existing['sequence']['id']
        elif recorded_id is not None and intent['action'] == 'create':
            raise ServiceError('production_run_sequence_binding_missing', 409)
        elif intent['action'] == 'create':
            require(run['sequence_lineage']['origin_run_id'] == run['id']
                and 'audio_reuse' not in run and intent['parent'] is None,
                'production_run_sequence_lineage_invalid', 409)
            sequence_id = self.sequence_store.create(owner, project_id, source, intent['key'])['id']
        else:
            parent = intent['parent']
            prior = self.sequence_store.source_for_run(owner, project_id,
                parent['source']['run_id'], parent['source']['version_id'])
            require(prior is not None and prior['source'] == parent['source']
                and prior['sequence']['id'] == intent['sequence_id'] == recorded_id,
                'production_run_sequence_parent_missing', 409)
            try:
                self.sequence_store.bind_source(owner, project_id, intent['sequence_id'], source,
                    intent['key'], expected_revision=intent['expected_revision'])
            except ServiceError as error:
                if error.code != 'production_sequence_revision_conflict':
                    raise
                # Preserve the rejected CAS intent, then freeze a distinct
                # metadata-only attempt. No old key ever acquires a new body.
                current = self.sequence_store.get(owner, project_id, intent['sequence_id'])
                history = run.setdefault('sequence_intent_history', [])
                require(len(history) < 64, 'production_run_sequence_registration_conflict', 409)
                history.append(dict(intent_sha256=digest(intent), key=intent['key'],
                    expected_revision=intent['expected_revision'], error=error.code))
                updated = deepcopy(intent)
                updated.update(attempt=intent['attempt'] + 1, expected_revision=current['revision'],
                    key='production-sequence:' + run['id'] + ':bind:' + str(intent['attempt'] + 1))
                run['sequence_intent'], run['sequence_intent_sha256'] = updated, digest(updated)
                run['sequence_registration'].update(status='pending', error=error.code)
                self._save(run)
                return
            sequence_id = intent['sequence_id']
        run['sequence_registration'].update(status='registered', sequence_id=sequence_id,
            source=deepcopy(source), error=None)
        self._save(run)

    @staticmethod
    def _uses_film(run):
        return run.get('renderer_mode') == FILM_RENDERER

    @staticmethod
    def _uses_scene_space(run):
        return run.get('renderer_mode') == 'scene-space'

    def _scene_space_service(self):
        require(self.scene_space_adapter is not None,'scene_space_production_unavailable',503)
        return self.scene_space_adapter

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
            and 0 < value['max_seconds'] <= MAX_SECONDS and value.get('fps') == 24,
            'production_run_film_unavailable', 503)
        require(len(packed(value).encode()) <= 16384, 'production_run_film_configuration_invalid', 503)
        return deepcopy(value)

    def _require_film_configuration(self, run):
        current = self._check_film_configuration(self._film_service().readiness())
        require(current == run['film_creation_configuration'],
            'production_run_film_configuration_changed', 409)
        if 'source_runtime_admission_receipt' in run:
            self._film_service().source_runtime_configuration_for_child(run, 'draft')

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
            source = run.get('resource_plan_source')
            if source is not None:
                row = db.execute('SELECT document FROM joint_plan_versions WHERE id=? AND owner=? AND project_id=?',
                    (source['version_id'], run['owner'], run['project_id'])).fetchone()
                actual = project.get('joint_plan', {}).get('resource_plan')
                version = json.loads(row[0]) if row else {}
                original = version.get('resource_plan')
                if (digest(actual) != source['requirements_sha256']
                        or digest(original) != source['source_sha256']):
                    return False
                adoption = source.get('adoption_source')
                if adoption is not None and (adoption != project.get('joint_plan', {}).get('resource_plan_source')
                        or adoption.get('version_id') != source['version_id']
                        or adoption.get('candidate_resource_plan_sha256') != digest(original)
                        or adoption.get('requirements_sha256') != digest(actual)
                        or adoption.get('resource_planning_input_sha256')
                            != version.get('resource_planning', {}).get('input_sha256')
                        or adoption.get('resource_planning_result_sha256')
                            != version.get('resource_planning', {}).get('result_sha256')):
                    return False
            head = self.joint._head(db, run['owner'], run['project_id'])
            return (head['version_id'] == run['joint_plan_version_id']
                and head['applied_binding_sha256'] == self.joint._binding(project)
                and digest(self._freeze(project, film_aware=bool(run.get('continuity_schema')))) == run['binding_sha256'])
        except (ServiceError, KeyError, TypeError):
            return False

    @staticmethod
    def _freeze_film(run):
        """Freeze measured speech or an explicitly requested visual clock.

        Padding includes the renderer's final partial-frame hold, so later
        authors receive the actual joined-video clock. Silence uses approved
        scene durations; missing speech never selects that mode. Old journals
        deliberately continue their original per-scene contract.
        """
        if not run.get('continuity_schema') or run.get('film') is not None:
            return
        frozen, rows, cursor = run['frozen'], [], 0
        silent, cursor_frames = run.get('audio_mode') == 'silent', 0
        for item, scene in zip(run['items'], frozen['scenes']):
            if silent:
                ProductionRunStore._require_silent_item(item)
                seconds = scene['duration_seconds']
                require(type(seconds) in {int, float} and math.isfinite(seconds) and 0 < seconds <= 60,
                    'invalid_production_run_timing')
                frames = math.ceil(seconds * 24)
                rows.append(dict(scene_id=item['scene_id'], title=scene['title'], narration='',
                    visual_intent=scene['visual_intent'], clock_source='silent',
                    audio_version_id=None, audio_sha256=None, audio_seconds=0,
                    start_seconds=cursor_frames / 24, end_seconds=(cursor_frames + frames) / 24,
                    leading_silence_seconds=0, trailing_silence_seconds=frames / 24))
                cursor_frames += frames
                cursor = cursor_frames / 24
                continue
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
        if silent:
            run['film']['schema'] = SILENT_FILM_CLOCK
        run['film_sha256'] = digest(run['film'])

    @staticmethod
    def _require_silent_item(item):
        require(item.get('audio_status') == 'not_requested'
            and type(item.get('audio_seconds')) in {int, float} and item['audio_seconds'] == 0
            and all(item.get(key) is None for key in
                ('audio_operation_id', 'audio_version_id', 'audio_sha256')),
            'production_run_silent_audio_identity_changed', 409)

    @staticmethod
    def _audio_ready(run):
        if run.get('audio_mode') == 'silent':
            for item in run['items']:
                ProductionRunStore._require_silent_item(item)
            return True
        return all(item['audio_version_id'] for item in run['items'])

    @staticmethod
    def _film_context(run, index, predecessor):
        if not run.get('continuity_schema'):
            return None
        if run.get('audio_mode') == 'silent':
            from agent_service.profiles.video_animation_film_module_contract import (
                EXPLICIT_CONTEXT_SCHEMA, validate_author_film,
            )
            film = validate_author_film(run['film'], run['film_sha256'], explicit=True)
            return dict(schema=EXPLICIT_CONTEXT_SCHEMA, film=film, film_sha256=run['film_sha256'],
                current_scene_index=index, predecessor=predecessor)
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
        return (run['status'] in ACTIVE or run.get('draining') or run.get('blocked_reason')
            or run['status'] == 'completed' and run.get('sequence_registration', {}).get('status')
                in {'awaiting_draft', 'pending', 'blocked'})

    @staticmethod
    def _require_idle_project_children(db, project_id):
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('audio_operations', 'motion_operations', 'operations', 'explanation_operations',
                      'joint_plan_operations', 'video_renders', 'video_batches',
                      'film_module_operations', 'film_render_operations'):
            if table in tables:
                for row in db.execute('SELECT document FROM ' + table + ' WHERE project_id=?', (project_id,)):
                    require(json.loads(row[0]).get('status') not in CHILD_ACTIVE | {'blocked'},
                            'production_run_project_busy', 409)

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
        # These technical work limits belong to this run. Source child intents
        # are not reopened. Formal refinement allowances, when configured, keep
        # their independent stable sequence identity in the caller below.
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

    @staticmethod
    def _has_planned_resources(project):
        value = project.get('joint_plan', {}).get('resource_plan')
        if value is None:
            return False
        # Nonempty and malformed graphs retain the existing film validation
        # path. Only the planner's complete, valid empty contract means no work.
        if type(value) is not dict or type(value.get('nodes')) is not list or value['nodes']:
            return True
        budget = value.get('budget')
        require(set(value) == {'nodes', 'budget'} and type(budget) is dict
            and set(budget) == {'max_attempts', 'max_attempts_per_node'}
            and all(type(v) is int and 1 <= v <= 32 for v in budget.values())
            and budget['max_attempts_per_node'] <= budget['max_attempts'],
            'production_resource_plan_invalid')
        return False

    def start(self, owner, project_id, request, idem):
        required = {'expected_revision', 'joint_plan_version_id'}
        require(type(request) is dict and required <= set(request) <= required | {
            'reuse_audio_from_run_id', 'resource_authoring', 'resource_plan', 'audio_mode',
            'reuse_completed_prefix_from_run_id'}
            and type(request['expected_revision']) is int and request['expected_revision'] >= 1
            and type(request['joint_plan_version_id']) is str and 1 <= len(request['joint_plan_version_id']) <= 100
            and ('reuse_audio_from_run_id' not in request or type(request['reuse_audio_from_run_id']) is str
                and 1 <= len(request['reuse_audio_from_run_id']) <= 100))
        if 'reuse_completed_prefix_from_run_id' in request:
            source_id = request['reuse_completed_prefix_from_run_id']
            require(type(source_id) is str and 1 <= len(source_id) <= 100
                and request.get('audio_mode') == 'silent'
                and not any(key in request for key in
                    ('reuse_audio_from_run_id', 'resource_authoring', 'resource_plan')),
                'production_run_module_reuse_unsupported')
        audio_mode = request.get('audio_mode', 'narration')
        require(type(audio_mode) is str and audio_mode in {'narration', 'silent'})
        if audio_mode == 'silent':
            require('reuse_audio_from_run_id' not in request, 'production_run_silent_audio_reuse_conflict')
        resource_authoring = request.get('resource_authoring')
        if resource_authoring is not None:
            require(type(resource_authoring) is dict and 1 <= len(resource_authoring) <= 8
                and all(type(key) is str and 1 <= len(key) <= 100 and type(value) is dict
                    for key, value in resource_authoring.items()), 'production_run_resource_options_invalid')
        require(type(idem) is str and re.fullmatch(r'[A-Za-z0-9_.:-]{8,128}', idem), 'idempotency_key_required')
        fingerprint = digest(dict(project_id=project_id, request=request))
        # Frozen historical admissions survive a newly configured scene route,
        # including replays of old resource-bearing requests and deleted views.
        with self.store.transaction() as db:
            previous = db.execute('SELECT request_hash,document FROM production_runs WHERE owner=? AND idem=?',
                (owner,idem)).fetchone()
            if previous:
                require(previous[0]==fingerprint,'idempotency_conflict',409)
                run=json.loads(previous[1]);project=self.store._project(db,owner,project_id,include_deleted=True)
                lifecycle=self.store._lifecycle(db,project_id)
                current=not(lifecycle and lifecycle['deleted_at'] is not None)
                return self.public(run,current=current and self._matches(run,project,db))
        if audio_mode=='silent':
            require(self.film_creation is not None or self.scene_space_adapter is not None
                and getattr(self.scene_space_adapter,'enabled',True),
                'production_run_silent_requires_film',503)
        parent_sequence = None
        if self.sequence_store is not None and self.film_creation is not None and 'reuse_audio_from_run_id' in request:
            with self.store.transaction() as db:
                replay = db.execute('SELECT 1 FROM production_runs WHERE owner=? AND idem=?', (owner, idem)).fetchone()
            if replay is None:
                # Resolve the real completed candidate before admitting fresh
                # visual work. Failed registration never falls back to a new
                # sequence or fresh recordings. No GET performs this mutation.
                parent_sequence = self._sequence_parent(owner, project_id, request['reuse_audio_from_run_id'])
        configuration = resource_plan = resource_source = None
        with self.store.transaction() as db:
            rendering_project = self.store._project(db,owner,project_id)
            rendering_intent = deepcopy(rendering_project.get('joint_plan',{}).get('rendering_intent'))
        rendering_route = None
        if rendering_intent is not None:
            from .spatial_rendering_route import preferred_route
            use_scene_space = preferred_route(rendering_intent) == 'scene-space'
            if use_scene_space:
                use_scene_space = (self.scene_space_adapter is not None
                    and getattr(self.scene_space_adapter,'enabled',True)
                    and resource_authoring is None and 'resource_plan' not in request
                    and not self._has_planned_resources(rendering_project))
            if not use_scene_space:
                require(self.film_creation is not None,'spatial_rendering_route_unavailable',503)
        else:
            # Historical plans without the new frozen offer retain v1 routing.
            use_scene_space = (self.scene_space_adapter is not None
                and getattr(self.scene_space_adapter,'enabled',True) and audio_mode == 'silent')
        if use_scene_space:
            # The new route has its own admission. Never require or borrow the
            # old film-module profile/host merely to admit the scene compositor.
            require(resource_authoring is None and 'resource_plan' not in request,
                'scene_space_production_resource_not_admitted')
            with self.store.transaction() as db:
                replay = db.execute('SELECT 1 FROM production_runs WHERE owner=? AND idem=?',(owner,idem)).fetchone()
                planning_project = self.store._project(db,owner,project_id)
                require(not self._has_planned_resources(planning_project),
                    'scene_space_production_resource_not_admitted')
            if replay is None:
                configuration = self.scene_space_adapter.freeze_configuration(owner,project_id)
                require(type(configuration) is dict and configuration.get('available') is True
                    and configuration.get('renderer_mode')=='scene-space'
                    and configuration.get('fps')==24 and configuration.get('max_seconds')==300
                    and configuration.get('audio_modes')==['silent']
                    and type(configuration.get('configuration_sha256')) is str
                    and re.fullmatch(r'[0-9a-f]{64}',configuration['configuration_sha256']),
                    'scene_space_production_unavailable',503)
                if rendering_intent is not None:
                    from .spatial_rendering_route import admit_route
                    try:
                        admit_route(rendering_intent,configuration,audio_mode=audio_mode)
                    except ServiceError as error:
                        if self.film_creation is None or error.code not in {
                                'spatial_rendering_audio_mode_not_admitted',
                                'spatial_rendering_required_features_unavailable',
                                'spatial_rendering_optional_downgrade_forbidden'}:
                            raise
                        # A different admitted implementation must still satisfy
                        # every core request; no unsupported sound is discarded.
                        use_scene_space=False;configuration=None
        if self.film_creation is not None and not use_scene_space:
            # The facade authorizes through this same SQLite store and may do
            # network preflight. Do not hold BEGIN IMMEDIATE while calling it.
            # Historical idempotent replays do not need fresh admission.
            with self.store.transaction() as db:
                replay = db.execute('SELECT 1 FROM production_runs WHERE owner=? AND idem=?', (owner, idem)).fetchone()
            if replay is None:
                configuration = self._check_film_configuration(
                    self.film_creation.freeze_configuration(owner, project_id))
                with self.store.transaction() as db:
                    planning_project = self.store._project(db, owner, project_id)
                    if callable(getattr(getattr(self.joint, 'skill_adapter', None), 'pin', None)):
                        require('library_binding' in planning_project.get('joint_plan', {}),
                            'production_skill_new_plan_required', 409)
                    source_row = db.execute('SELECT document FROM joint_plan_versions WHERE id=? AND owner=? AND project_id=?',
                        (request['joint_plan_version_id'], owner, project_id)).fetchone()
                    selected_plan = planning_project.get('joint_plan', {}).get('resource_plan')
                    original_plan = json.loads(source_row[0]).get('resource_plan') if source_row else None
                if 'resource_plan' in request:
                    require(resource_authoring is None, 'production_run_conflicting_resource_plans')
                    selected_plan = request['resource_plan']
                elif resource_authoring is not None:
                    # The former optional map is normalized into the shared
                    # run-level graph. New module tasks never launch resources.
                    require(set(resource_authoring) <= {s['id'] for s in planning_project['scenes']},
                        'production_run_resource_scene_invalid', 409)
                    selected_plan = dict(nodes=[dict(id='manim_' + scene_id, kind='animation', producer='manim',
                        inputs=dict(resource_authoring=dict(options, global_start_frame=0)), dependencies=[],
                        objective=next(s['visual_description'] for s in planning_project['scenes'] if s['id'] == scene_id),
                        required_capabilities=['manim'], key_effects=[], local_timing={},
                        placement=dict(scene_id=scene_id, start_frame=options.get('global_start_frame', 0)))
                        for scene_id, options in resource_authoring.items()])
                else:
                    resource_source = dict(version_id=request['joint_plan_version_id'],
                        requirements_sha256=digest(selected_plan), source_sha256=digest(original_plan),
                        adoption_source=deepcopy(planning_project.get('joint_plan', {}).get('resource_plan_source')))
                from .production_resource_stage import production_plan
                resource_plan = production_plan(selected_plan, self._freeze(planning_project, film_aware=True),
                    deadline_at=time.time() + RESOURCE_PLAN_SECONDS,
                    library_binding=planning_project.get('joint_plan', {}).get('library_binding'),
                    audio_mode=audio_mode)
                if resource_plan is not None:
                    self.resource_stage.preflight(owner, resource_plan)
        require(resource_authoring is None or self.film_creation is not None,
            'production_run_resources_require_film', 503)
        require('resource_plan' not in request or self.film_creation is not None,
            'production_run_resources_require_film', 503)
        if rendering_intent is not None:
            from .spatial_rendering_route import admit_route
            admission = deepcopy(configuration)
            if not use_scene_space:
                admission['runtime_capabilities'] = deepcopy(
                    self.film_creation.configuration.get('runtime_capabilities', {}))
            rendering_route = admit_route(rendering_intent,admission,audio_mode=audio_mode)
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
            require(project.get('joint_plan',{}).get('rendering_intent') == rendering_intent,
                'spatial_rendering_intent_changed',409)
            head = self.joint._head(db, owner, project_id)
            require(head['version_id'] == request['joint_plan_version_id']
                and project.get('joint_plan', {}).get('version_id') == head['version_id'],
                'joint_plan_head_conflict', 409)
            require(head['applied_binding_sha256'] == self.joint._binding(project), 'joint_plan_stale', 409)
            require(1 <= len(project['scenes']) <= MAX_SCENES, 'production_run_requires_scenes', 409)
            if resource_authoring is not None:
                require(set(resource_authoring) <= {scene['id'] for scene in project['scenes']},
                    'production_run_resource_scene_invalid', 409)
                from .manim_method_coordinator import authoring_request
                from .manim_resource import MAX_DECODED_BYTES
                reserved = [authoring_request(value, 60) for value in resource_authoring.values()]
                caps = self.film_creation.configuration['runtime_capabilities']
                require(sum(value['width'] * value['height'] * value['frame_count'] * 4 for value in reserved)
                        <= MAX_DECODED_BYTES
                    and sum(value['frame_count'] + 1 for value in reserved) + 2 + len(caps['assets']) <= 128,
                    'production_run_resource_budget_exceeded', 413)
            for row in db.execute('SELECT document FROM production_runs WHERE owner=? AND project_id=?',
                                  (owner, project_id)):
                require(not self._unfinished(json.loads(row[0])), 'production_run_in_progress', 409)
            self._require_idle_project_children(db, project_id)
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
                pause_seconds = (0 if audio_mode == 'silent' else sum(
                    scene['leading_seconds'] + scene['trailing_seconds'] for scene in frozen['scenes']))
                if audio_mode == 'silent':
                    require(all(type(scene['duration_seconds']) in {int, float}
                        and math.isfinite(scene['duration_seconds']) and 0 < scene['duration_seconds'] <= 60
                        for scene in frozen['scenes']), 'invalid_production_run_timing')
                    planned_seconds = sum(math.ceil(scene['duration_seconds'] * 24)
                        for scene in frozen['scenes']) / 24
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
            if 'audio_mode' in request:
                run['audio_mode'] = audio_mode
            if rendering_route is not None:
                run['rendering_route'] = rendering_route
            if audio_mode == 'silent':
                run.update(voice=None, phase='module')
                for item in run['items']:
                    item.update(audio_status='not_requested', audio_seconds=0)
                self._freeze_film(run)
            if 'library_binding' in project.get('joint_plan', {}):
                run['library_binding'] = deepcopy(project['joint_plan']['library_binding'])
            if configuration is not None:
                run.update(renderer_mode=FILM_RENDERER, draft_render_source=FILM_RENDERER,
                    film_creation_configuration=configuration,
                    module_targets=self._module_targets(run['id'], len(run['items'])))
                for item in run['items']:
                    item.update(module_operation_id=None, module_version_id=None, module_status='queued',
                        module_sha256=None, module_package_sha256=None, module_engine=None)
                if use_scene_space:
                    run.update(renderer_mode='scene-space',draft_render_source='scene-space',
                        scene_space_configuration=deepcopy(configuration),scene_space=dict(status='pending',
                            stop_proven=True,plan_sha256=None,source_sha256=None,render_id=None,error=None))
                if resource_plan is not None:
                    run.update(resource_plan=resource_plan,
                        resource_plan_registration=dict(status='pending', operation_id=run['id'],
                            request_sha256=digest(resource_plan)), resource_progress={})
                if resource_source is not None:
                    run['resource_plan_source'] = resource_source
                if resource_authoring is not None:
                    run.update(resource_authoring=deepcopy(resource_authoring), resource_work_budget=dict(
                        recipe_jobs=len(resource_authoring), resource_executions=len(resource_authoring)))
            if 'reuse_audio_from_run_id' in request:
                original = self._read(db, request['reuse_audio_from_run_id'], owner)
                if 'sequence_registration' in original:
                    require(self.sequence_store is not None and configuration is not None,
                        'production_run_sequence_unavailable', 503)
                self._reuse_audio(db, run, request['reuse_audio_from_run_id'])
            if 'reuse_completed_prefix_from_run_id' in request:
                from .production_module_reuse import attach
                attach(self, db, run, request['reuse_completed_prefix_from_run_id'])
            if configuration is not None and self.sequence_store is not None:
                if 'reuse_audio_from_run_id' in request:
                    original = self._read(db, request['reuse_audio_from_run_id'], owner)
                    registration = original.get('sequence_registration', {})
                    require(parent_sequence is not None and registration.get('status') == 'registered'
                        and registration.get('sequence_id') == parent_sequence['sequence_id']
                        and registration.get('source') == parent_sequence['source'],
                        'production_run_sequence_parent_changed', 409)
                lineage = dict(origin_run_id=run['id'] if parent_sequence is None else None,
                    parent=deepcopy(parent_sequence))
                run.update(sequence_lineage=lineage, sequence_lineage_sha256=digest(lineage),
                    sequence_registration=self._registration(parent_sequence['sequence_id'] if parent_sequence else None,
                        parent_sequence['source'] if parent_sequence else None))
                self._require_sequence_admission(run)
            raw = packed(run)
            require(len(raw.encode()) <= 512 * 1024, 'production_run_storage_limit', 413)
            # Reserve the full-film copy before paying for recordings. Child
            # intents retain only exact references/hashes, never nested sources.
            probe = deepcopy(run)
            if audio_mode != 'silent':
                for item in probe['items']:
                    item.update(audio_version_id='x' * 100, audio_sha256='a' * 64, audio_seconds=.1)
            self._freeze_film(probe)
            self._film_context(probe, 0, None)
            require(len(packed(probe).encode()) <= 448 * 1024, 'production_run_storage_limit', 413)
            db.execute('INSERT INTO production_runs VALUES (?, ?, ?, ?, ?, ?)',
                (run['id'], owner, project_id, idem, fingerprint, raw))
            return self.public(run, current=True)

    @staticmethod
    def _speech_request_sha256(document, speed):
        settings = document['settings']
        return digest(dict(model=settings['model'], input=document['spoken_text'],
                           voice=settings['voice'], speed=speed, response_format='wav'))

    def _scene_source_recovery_replay(self,db,owner,source_run_id,idem,fingerprint):
        row=db.execute('SELECT request_hash,document FROM production_runs WHERE owner=? AND idem=?',
            (owner,idem)).fetchone()
        if row is None:return None
        run=json.loads(row['document'])
        require(row['request_hash']==fingerprint
            and run.get('scene_source_recovery',{}).get('source_run_id')==source_run_id,
            'idempotency_conflict',409)
        return self.public(run,current=True)

    def _scene_source_recovery_target(self,db,owner,source_run_id,request):
        source=self._read(db,source_run_id,owner)
        project=self.store._project(db,owner,source['project_id'])
        self.store._revision(project,request['expected_revision'])
        require(digest(source)==request['expected_source_run_sha256']
            and source['status']=='failed' and source.get('error')=='scene_appearance_usage_unknown'
            and self._uses_scene_space(source) and source.get('audio_mode')=='silent'
            and not (source.get('cancel_requested') or source.get('draining') or source.get('blocked_reason'))
            and not source.get('scene_source_recovery') and not source.get('draft_render_id')
            and not source.get('intents') and self._matches(source,project,db),
            'production_run_scene_source_recovery_unavailable',409)
        existing=db.execute("SELECT id FROM production_runs WHERE owner=? AND project_id=? "
            "AND json_extract(document,'$.scene_source_recovery.source_run_id')=? LIMIT 1",
            (owner,source['project_id'],source_run_id)).fetchone()
        require(existing is None,'production_run_scene_source_recovery_already_exists',409)
        for row in db.execute('SELECT document FROM production_runs WHERE owner=? AND project_id=? AND id<>?',
                (owner,source['project_id'],source_run_id)):
            require(not self._unfinished(json.loads(row[0])),'production_run_in_progress',409)
        self._require_idle_project_children(db,source['project_id'])
        require(db.execute('SELECT count(*) FROM production_runs WHERE project_id=?',(source['project_id'],)).fetchone()[0]<64
            and db.execute('SELECT count(*) FROM production_runs').fetchone()[0]<1024,'production_run_storage_limit',409)
        self._scene_space_service().source_recovery_snapshot(source,request)
        return source

    def recover_scene_source(self,owner,source_run_id,request,idem):
        fields={'expected_revision','expected_source_run_sha256','expected_scene_sha256','expected_usage_sha256',
            'expected_author_job_id','expected_author_input_sha256','expected_author_result_sha256',
            'expected_source_sha256','expected_plan_sha256','expected_configuration_sha256',
            'expected_usage_evidence_sha256','execution_deadline_at'}
        require(type(request) is dict and set(request)==fields
            and type(request['expected_revision']) is int and request['expected_revision']>=1
            and type(request['expected_author_job_id']) is str and 1<=len(request['expected_author_job_id'])<=100
            and all(type(request[key]) is str and re.fullmatch('[0-9a-f]{64}',request[key])
                for key in fields if key.endswith('_sha256'))
            and type(request['execution_deadline_at']) in {int,float} and math.isfinite(request['execution_deadline_at']),
            'invalid_scene_source_recovery_request')
        fingerprint=digest(dict(kind='scene-source-recovery',source_run_id=source_run_id,request=request,idem=idem))
        # Serialize against local workflow admissions while keeping network GETs
        # outside SQLite transactions. CAS is repeated after real job/artifact GET.
        with self.store.lock:
            with self.store.transaction() as db:
                replay=self._scene_source_recovery_replay(db,owner,source_run_id,idem,fingerprint)
                if replay is not None:return replay
                source=self._scene_source_recovery_target(db,owner,source_run_id,request)
            recovery=self._scene_space_service().prepare_source_recovery(source,request)
            with self.store.transaction() as db:
                replay=self._scene_source_recovery_replay(db,owner,source_run_id,idem,fingerprint)
                if replay is not None:return replay
                current=self._scene_source_recovery_target(db,owner,source_run_id,request)
                require(current==source,'production_run_scene_source_recovery_changed',409)
                run=deepcopy(source)
                run.update(id=str(uuid4()),request_key=idem,status='queued',phase='scene_source_recovery_pending',
                    error=None,blocked_reason=None,cancel_requested=False,draining=False,created_at=time.time(),
                    finished_at=None,draft_render_id=None,scene_source_recovery=recovery,
                    scene_space=dict(status='source_recovery_pending',stop_proven=True,
                        plan_sha256=recovery['plan_sha256'],source_sha256=recovery['source_sha256'],render_id=None,error=None))
                for item in run['items']:item.update(status='queued',error=None)
                if 'sequence_registration' in source:
                    require(self.sequence_store is not None,'production_run_sequence_unavailable',503)
                    run.update(sequence_lineage=dict(origin_run_id=run['id'],parent=None),
                        sequence_registration=self._registration())
                    run['sequence_lineage_sha256']=digest(run['sequence_lineage'])
                raw=packed(run)
                require(len(raw.encode())<=512*1024,'production_run_storage_limit',413)
                # This durable intent is committed before any new task/scene DB
                # row. A restart installs the same IDs idempotently, never authors.
                db.execute('INSERT INTO production_runs VALUES (?,?,?,?,?,?)',
                    (run['id'],owner,run['project_id'],idem,fingerprint,raw))
                return self.public(run,current=True)

    @staticmethod
    def _narration_resume_replay(run, idem, fingerprint):
        for receipt in run.get('narration_resumes', []):
            if receipt['request_id'] == idem:
                require(receipt['request_sha256'] == fingerprint, 'idempotency_conflict', 409)
                return True
        return False

    def _narration_resume_target(self, db, run, project, expected_revision):
        self.store._revision(project, expected_revision)
        require(self._matches(run, project, db), 'production_run_inputs_changed', 409)
        require(run['status'] == 'failed' and run['phase'] == 'audio'
                and run.get('audio_mode', 'narration') == 'narration'
                and not (run.get('cancel_requested') or run.get('draining'))
                and all(intent['phase'] == 'audio' and not intent.get('active')
                        for intent in run['intents'].values()),
                'production_run_narration_resume_unavailable', 409)
        for row in db.execute('SELECT document FROM production_runs WHERE owner=? AND project_id=? AND id<>?',
                              (run['owner'], run['project_id'], run['id'])):
            require(not self._unfinished(json.loads(row[0])), 'production_run_in_progress', 409)
        self._require_idle_project_children(db, run['project_id'])

        index = next((i for i, item in enumerate(run['items']) if not item.get('audio_version_id')), None)
        name = 'audio:' + str(index)
        intent = run['intents'].get(name)
        require(index is not None and intent is not None and intent['index'] == index,
                'production_run_narration_resume_unavailable', 409)
        item = run['items'][index]
        row = db.execute('SELECT document FROM audio_operations WHERE owner=? AND idem=?',
                         (run['owner'], intent['key'])).fetchone()
        require(row is not None, 'production_run_narration_resume_unavailable', 409)
        child = json.loads(row[0])
        document = run['frozen']['scenes'][index]['audio_input']
        stop_confirmed = child.get('upstream_stop_confirmed') is True
        require(stop_confirmed, 'speech_upstream_stop_unconfirmed', 409)
        input_sha256 = child.get('provider_input_sha256')
        original_speed = child.get('provider_speed', document['settings']['speed'])
        require(child['id'] == item.get('audio_operation_id') and child['kind'] == 'speech'
                and child['status'] == 'failed' and child.get('submit_attempted') is True
                and child['project_id'] == run['project_id'] and child['scene_id'] == item['scene_id']
                and child['input'] == document and child['input_sha256'] == narration_binding(document)
                and child['error'] == run['error'] == item['error']
                and run.get('blocked_reason') in {None, child['error']}
                and input_sha256 == self._speech_request_sha256(document, original_speed),
                'production_run_narration_resume_unavailable', 409)
        new_rejection = (child.get('error') == 'speech_duration_profile_unsupported'
            and child.get('provider_rejection') == dict(code=child['error'],
                input_sha256=input_sha256, inference_started=False))
        reconciliation = child.get('stop_reconciliation')
        legacy_rejection = (child.get('error') == 'speech_upstream_unavailable'
            and type(reconciliation) is dict
            and reconciliation.get('kind') == 'provider_restart_and_exact_preflight'
            and reconciliation.get('input_sha256') == input_sha256)
        require(new_rejection or legacy_rejection, 'production_run_narration_rejection_unverified', 409)
        evidence = child['provider_rejection'] if new_rejection else reconciliation
        return name, index, intent, item, child, document, evidence

    def resume_narration(self, owner, run_id, expected_revision, speed, idem, speech_client):
        """Preflight and atomically replace the stopped narration child."""
        require(type(expected_revision) is int and expected_revision >= 1)
        require(type(idem) is str and re.fullmatch(r'[A-Za-z0-9_.:-]{8,128}', idem), 'idempotency_key_required')
        speed = speech_options(speed=speed)['speed']
        fingerprint = digest(dict(expected_revision=expected_revision, provider_speed=speed))
        with self.store.transaction() as db:
            run = self._read(db, run_id, owner)
            project = self.store._project(db, owner, run['project_id'])
            if self._narration_resume_replay(run, idem, fingerprint):
                return self.public(run, current=self._matches(run, project, db))
            _, _, _, _, child, document, _ = self._narration_resume_target(db, run, project, expected_revision)
            source = digest(dict(run=run, child=child))

        require(speech_client is not None, 'speech_not_configured', 503)
        plan = speech_client.plan(document['spoken_text'], voice=document['settings']['voice'], speed=speed)
        provider_input_sha256 = self._speech_request_sha256(document, speed)
        require(type(plan) is dict and plan.get('input_sha256') == provider_input_sha256
                and plan.get('inference_started') is False, 'production_run_narration_plan_invalid', 502)
        require(plan.get('admissible') is True, 'production_run_narration_plan_rejected', 422)

        with self.store.lock, self.store.transaction() as db:
            run = self._read(db, run_id, owner)
            project = self.store._project(db, owner, run['project_id'])
            if self._narration_resume_replay(run, idem, fingerprint):
                return self.public(run, current=self._matches(run, project, db))
            name, index, intent, item, child, _, evidence = self._narration_resume_target(
                db, run, project, expected_revision)
            require(digest(dict(run=run, child=child)) == source,
                    'production_run_narration_resume_changed', 409)
            key = 'production-run:' + run_id + ':' + name + ':resume:' + uuid4().hex
            run.setdefault('narration_resumes', []).append(dict(request_id=idem, request_sha256=fingerprint,
                scene_id=item['scene_id'], failed_operation_id=child['id'], failed_error=child['error'],
                previous_provider_input_sha256=child['provider_input_sha256'], previous_intent_key=intent['key'],
                provider_input_sha256=provider_input_sha256, provider_speed=speed, replacement_intent_key=key,
                stop_evidence=deepcopy(evidence),
                previous_finished_at=run['finished_at'], created_at=time.time()))
            run['intents'][name] = dict(phase='audio', index=index, key=key, request=None, active=False,
                provider_speed=speed, provider_input_sha256=provider_input_sha256)
            item.update(status='queued', audio_operation_id=None, error=None)
            run.update(status='queued', error=None, blocked_reason=None, finished_at=None)
            self._write(db, run)
            return self.public(run, current=True)

    def _require_unstarted_resource_plan(self, run):
        registration = run.get('resource_plan_registration') or {}
        plan = run.get('resource_plan')
        require(self.resource_stage is not None and type(plan) is dict
                and registration.get('status') == 'pending'
                and registration.get('operation_id') == run['id']
                and registration.get('request_sha256') == digest(plan)
                and not (run.get('resource_progress') or run.get('prepared_resource_references')),
                'production_run_resources_already_started', 409)
        try:
            self.resource_stage.ledger.get(run['owner'], run['id'])
        except ServiceError as error:
            if error.code == 'resource_plan_not_found' and error.status == 404:
                return
            raise
        raise ServiceError('production_run_resources_already_started', 409)

    def resume_resources(self, owner, run_id, expected_revision, idem):
        """Admit a fresh time window for a resource plan with no admitted work."""
        require(type(expected_revision) is int and expected_revision >= 1)
        require(type(idem) is str and re.fullmatch(r'[A-Za-z0-9_.:-]{8,128}', idem), 'idempotency_key_required')
        with self.store.lock, self.store.transaction() as db:
            run = self._read(db, run_id, owner)
            project = self.store._project(db, owner, run['project_id'])
            for receipt in run.get('resource_deadline_resumes', []):
                if receipt['request_id'] == idem:
                    require(receipt['expected_revision'] == expected_revision, 'idempotency_conflict', 409)
                    return self.public(run, current=self._matches(run, project, db))

            self.store._revision(project, expected_revision)
            require(self._matches(run, project, db), 'production_run_inputs_changed', 409)
            require(run['status'] == 'failed' and run['phase'] == 'audio'
                    and run['error'] == 'resource_plan_deadline_expired'
                    and self._uses_film(run) and run.get('audio_mode', 'narration') == 'narration'
                    and not (run.get('cancel_requested') or run.get('blocked_reason') or run.get('draft_render_id'))
                    and all(intent['phase'] == 'audio' and not intent.get('active')
                            for intent in run['intents'].values()),
                    'production_run_resource_resume_unavailable', 409)
            self._require_unstarted_resource_plan(run)
            self._require_idle_project_children(db, run['project_id'])
            self._require_recovery_recordings(db, run)
            self._freeze_film(run)

            plan, registration = run['resource_plan'], run['resource_plan_registration']
            now = time.time()
            previous_deadline = plan['deadline_at']
            require(previous_deadline <= now, 'production_run_resource_resume_unavailable', 409)
            previous_sha256 = registration['request_sha256']
            plan['deadline_at'] = now + RESOURCE_PLAN_SECONDS
            registration['request_sha256'] = digest(plan)
            run.setdefault('resource_deadline_resumes', []).append(dict(
                request_id=idem, expected_revision=expected_revision, created_at=now,
                previous_deadline_at=previous_deadline, deadline_at=plan['deadline_at'],
                previous_request_sha256=previous_sha256, request_sha256=registration['request_sha256'],
                previous_finished_at=run['finished_at']))
            run.update(status='queued', error=None, draining=False, finished_at=None)
            self._write(db, run)
            return self.public(run, current=True)

    @staticmethod
    def _require_recovery_recordings(db, run):
        require(len(run['items']) == len(run['frozen']['scenes'])
                and all(item.get('audio_version_id') for item in run['items']),
                'production_run_resource_resume_unavailable', 409)
        for index, (item, scene) in enumerate(zip(run['items'], run['frozen']['scenes'])):
            audio = db.execute('SELECT document,data,scene_id,project_id,operation_id FROM audio_versions WHERE id=?',
                               (item['audio_version_id'],)).fetchone()
            intent = run['intents'].get('audio:' + str(index))
            require(audio is not None and intent is not None, 'production_run_audio_binding_mismatch', 409)
            child = db.execute('SELECT document FROM audio_operations WHERE owner=? AND idem=?',
                               (run['owner'], intent['key'])).fetchone()
            require(child is not None, 'production_run_audio_binding_mismatch', 409)
            child = json.loads(child[0])
            metadata, data = json.loads(audio['document']), audio['data']
            require(child['status'] == 'completed' and child['upstream_stop_confirmed'] is True
                    and child['id'] == audio['operation_id'] == item['audio_operation_id']
                    and child['candidate_id'] == metadata['id'] == item['audio_version_id']
                    and child['input_sha256'] == metadata['input_sha256'] == narration_binding(scene['audio_input'])
                    and child['project_id'] == audio['project_id'] == run['project_id']
                    and child['scene_id'] == audio['scene_id'] == scene['id'] == item['scene_id']
                    and hashlib.sha256(data).hexdigest() == metadata['sha256'] == item['audio_sha256']
                    and inspect_wav(data).duration_seconds == metadata['duration_seconds'] == item['audio_seconds'],
                    'production_run_audio_binding_mismatch', 409)

    def retry_module(self, owner, run_id, expected_revision, idem):
        """Retry one deadline-cancelled module after its producer stopped."""
        from .production_module_reuse import _child

        require(type(expected_revision) is int and expected_revision >= 1)
        require(type(idem) is str and re.fullmatch(r'[A-Za-z0-9_.:-]{8,128}', idem), 'idempotency_key_required')
        with self.store.lock, self.store.transaction() as db:
            run = self._read(db, run_id, owner)
            project = self.store._project(db, owner, run['project_id'])
            retries = run.get('module_retries', [])
            for receipt in retries:
                if receipt['request_id'] == idem:
                    require(receipt['expected_revision'] == expected_revision, 'idempotency_conflict', 409)
                    return self.public(run, current=self._matches(run, project, db))

            self.store._revision(project, expected_revision)
            require(self._matches(run, project, db), 'production_run_inputs_changed', 409)
            require(run['status'] == 'failed' and run['phase'] == 'module'
                    and run['error'] == 'production_run_child_cancelled'
                    and self._uses_film(run) and run.get('audio_mode', 'narration') == 'narration'
                    and not (run.get('cancel_requested') or run.get('draining')
                             or run.get('blocked_reason') or run.get('draft_render_id'))
                    and all(intent['phase'] in {'audio', 'module'} and not intent.get('active')
                            for intent in run['intents'].values())
                    and digest(run['film']) == run['film_sha256'],
                    'production_run_module_retry_unavailable', 409)
            for row in db.execute('SELECT document FROM production_runs WHERE owner=? AND project_id=? AND id<>?',
                                  (owner, run['project_id'], run_id)):
                require(not self._unfinished(json.loads(row[0])), 'production_run_in_progress', 409)
            self._require_idle_project_children(db, run['project_id'])
            self._require_recovery_recordings(db, run)

            index = next((i for i, item in enumerate(run['items']) if not item.get('module_version_id')), None)
            require(index is not None, 'production_run_module_retry_unavailable', 409)
            name = 'module:' + str(index)
            intent = run['intents'].get(name)
            item = run['items'][index]
            require(intent is not None and intent['index'] == index
                    and all(not later.get('module_operation_id') and not later.get('module_version_id')
                            for later in run['items'][index + 1:]),
                    'production_run_module_retry_unavailable', 409)
            require(not any(receipt['scene_id'] == item['scene_id'] for receipt in retries),
                    'production_run_module_retry_exhausted', 409)
            child = _child(db, run, intent)
            require(child['id'] == item.get('module_operation_id')
                    and child['kind'] == 'module' and child['status'] == 'cancelled'
                    and child.get('submit_attempted') is True and child.get('job_id')
                    and child.get('cancel_requested') is False and not child.get('version_id')
                    and child.get('cancel_attempts', 0) > 0
                    and child['created_at'] < child['deadline_at'] <= child['finished_at']
                    and child['next_cancel_at'] > child['deadline_at'],
                    'production_run_module_deadline_stop_unverified', 409)
            for previous in range(index):
                prior_item = run['items'][previous]
                prior_child = _child(db, run, run['intents'].get('module:' + str(previous)))
                require(prior_child['status'] == 'completed' and prior_child['id'] == prior_item['module_operation_id']
                        and prior_child['version_id'] == prior_item['module_version_id']
                        and prior_child['input_sha256'] == prior_item['module_input_sha256'],
                        'production_run_module_binding_mismatch', 409)

            immutable_request = {key: value for key, value in child['request'].items() if key != 'expected_revision'}
            key = 'production-run:' + run_id + ':' + name + ':retry:' + uuid4().hex
            run.setdefault('module_retries', []).append(dict(request_id=idem, expected_revision=expected_revision,
                scene_id=item['scene_id'], previous_operation_id=child['id'], previous_job_id=child['job_id'],
                previous_intent_key=intent['key'], previous_request_sha256=child['request_sha256'],
                previous_input_sha256=child['input_sha256'], previous_deadline_at=child['deadline_at'],
                previous_finished_at=child['finished_at'], previous_cancel_attempts=child['cancel_attempts'],
                stop_proven=True, replacement_intent_key=key, created_at=time.time()))
            run['intents'][name] = dict(phase='module', index=index, key=key, request=None, active=False,
                previous_content_sha256=digest(immutable_request))
            item.update(status='queued', module_status='queued', module_operation_id=None, error=None)
            run.update(status='queued', error=None, finished_at=None)
            self._write(db, run)
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
            configuration_sha256 = run['film_creation_configuration']['configuration_sha256']
            if 'source_runtime_admission_receipt' in run:
                configuration_sha256 = creation.source_runtime_configuration_for_child(
                    run, intent['phase'], intent.get('index'))
            require(child.get('project_id') == run['project_id']
                and child.get('request_key') == intent['key']
                and child.get('request_sha256') == intent['request_sha256']
                and child.get('configuration_sha256') == configuration_sha256
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
            if 'provider_speed' in intent:
                require(child.get('provider_speed') == intent['provider_speed']
                        and child.get('provider_input_sha256') == intent['provider_input_sha256'],
                        'production_run_audio_binding_mismatch', 409)
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
        if run.get('audio_mode') == 'silent':
            require(metadata.get('silent') is True and metadata.get('contains_narration') is False,
                'production_run_film_result_invalid', 409)
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
        if self._uses_scene_space(run):
            pending = not self._scene_space_service().cancel(run)
        if 'resource_plan_registration' in run:
            self._ensure_resource_plan(run)
            resources = self.resource_stage.cancel(run)
            self._resource_progress(run, resources)
            pending = not resources['stop_proven']
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
        self._require_module_reuse(run)
        self._require_audio_reuse(run)
        for intent in run['intents'].values():
            child = self._lookup(run, intent)
            if child:
                self._sync(run, intent, child)
        if run['status'] not in ACTIVE or any(i.get('active') for i in run['intents'].values()):
            self._save(run)
            return
        # Reconcile already-paid children before checking admission. Missing
        # sequence metadata must block fresh work, not erase a finished film.
        if admit:
            self._require_sequence_admission(run)
        if self._uses_scene_space(run):
            require(self._audio_ready(run),'scene_space_production_clock_not_ready',409)
            self._freeze_film(run)
            self._scene_space_service().advance(run,admit=admit,persist=self._save)
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
            self._start_narration_child(run, intent, request)
        elif phase == 'motion':
            self.motion.start_candidate(run['owner'], run['items'][index]['scene_id'], request,
                intent['key'], audio_version_id=run['items'][index]['audio_version_id'],
                **(dict(film_context=self._intent_context(run, intent))
                   if run.get('continuity_schema') else {}))
        else:
            self.production.start_candidate_render(run['owner'], run['project_id'], request, intent['key'])
        self._sync(run, intent, self._lookup(run, intent))
        self._save(run)

    def _require_module_reuse(self, run):
        if not any(key in run for key in ('module_reuse', 'module_reuse_sha256', 'module_work_budget',
                'source_runtime_admission_receipt')):
            return
        from .production_module_reuse import verify
        with self.store.transaction() as db:
            verify(self, db, run)

    def _film_child_request(self, run, phase, index, revision):
        self._require_module_reuse(run)
        configuration_sha256 = run['film_creation_configuration']['configuration_sha256']
        if phase == 'audio':
            return dict(expected_revision=revision)
        if phase == 'module':
            if index:
                self._frozen_module_package(run, index - 1)
            result = dict(expected_revision=revision, film=deepcopy(run['film']), film_sha256=run['film_sha256'],
                current_scene_index=index,
                predecessor_version_id=None if index == 0 else run['items'][index - 1]['module_version_id'],
                target=deepcopy(run['module_targets'][index]), instruction='',
                configuration_sha256=configuration_sha256)
            if 'rendering_route' in run:
                result['rendering_intent']=deepcopy(run['rendering_route']['intent'])
                result['rendering_quality']=deepcopy(run['rendering_route']['quality'])
            if 'library_binding' in run:
                result['library_binding'] = deepcopy(run['library_binding'])
            references = run.get('prepared_resource_references', {}).get(run['items'][index]['scene_id'], [])
            if references:
                result['resource_references'] = deepcopy(references)
            return result
        for module_index in range(len(run['items'])):
            self._frozen_module_package(run, module_index)
        result = dict(expected_revision=revision, film=deepcopy(run['film']), film_sha256=run['film_sha256'],
            module_version_ids=[item['module_version_id'] for item in run['items']],
            module_targets=deepcopy(run['module_targets']), production_run_id=run['id'],
            timeout_seconds=render_timeout(round(run['film']['total_seconds'] * 24)),
            configuration_sha256=configuration_sha256)
        if 'source_runtime_admission_receipt' in run:
            result['source_runtime_admission_receipt'] = deepcopy(run['source_runtime_admission_receipt'])
        return result

    def _start_narration_child(self, run, intent, request):
        override = ({'provider_speed': intent['provider_speed']} if 'provider_speed' in intent else {})
        self.store.start_audio(run['owner'], run['items'][intent['index']]['scene_id'],
                               request, intent['key'], **override)

    def _step_film(self, run, project, *, admit):
        """New runs author native modules and submit exactly one full project.

        The journal keeps only stable child references and request hashes; it
        never duplicates each large film request, package or render snapshot.
        """
        if self._audio_ready(run):
            self._freeze_film(run)
            require(run['film']['total_seconds'] <= run['film_creation_configuration']['max_seconds'],
                'production_run_film_too_long', 422)
            if 'resource_plan_registration' in run:
                self._ensure_resource_plan(run)
                self.resource_stage.validate_timing(run)
                resources = self.resource_stage.advance(run, admit=admit)
                self._resource_progress(run, resources)
                if resources['status'] in {'blocked', 'cancelled', 'timed_out'}:
                    self._finish(run, 'failed', 'production_run_resource_' + resources['status'])
                    run['draining'] = not resources['stop_proven']
                    self._save(run)
                    return
                if resources['status'] != 'completed':
                    run['phase'] = 'resources'
                    self._save(run)
                    return
                if 'prepared_resource_references' not in run:
                    run['prepared_resource_references'] = self.resource_stage.module_references(run, resources)
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
        revision = project['revision'] if intent is None or intent['request'] is None else intent['request']['expected_revision']
        request = self._film_child_request(run, phase, index, revision)
        if intent and 'previous_content_sha256' in intent:
            require(intent['previous_content_sha256'] == digest(
                {key: value for key, value in request.items() if key != 'expected_revision'}),
                'production_run_module_retry_inputs_changed', 409)
        request_sha256 = digest(dict(request=request, provider_speed=intent['provider_speed'])
                                if intent and 'provider_speed' in intent else request)
        if intent is None:
            intent = dict(phase=phase, index=index, key='production-run:' + run['id'] + ':' + name,
                request=None, active=False)
            run['intents'][name] = intent
        if intent['request'] is None:
            intent.update(request=dict(expected_revision=revision), request_sha256=request_sha256)
        require(intent['request_sha256'] == request_sha256, 'production_run_film_request_changed', 409)
        child = self._lookup(run, intent)
        if child is not None:
            self._sync(run, intent, child)
            self._save(run)
            return
        self._require_film_configuration(run)
        run.update(status='running', error=None)
        self._save(run)  # Intent is durable before any local child admission.
        if phase == 'audio':
            self._start_narration_child(run, intent, request)
        elif phase == 'module':
            self._film_service().start_module(run['owner'], run['project_id'], run['items'][index]['scene_id'],
                request, intent['key'])
        else:
            self._film_service().start_render(run['owner'], run['project_id'], request, intent['key'])
        child = self._lookup(run, intent)
        require(child is not None, 'production_run_child_not_visible', 503)
        self._sync(run, intent, child)
        self._save(run)

    def _ensure_resource_plan(self, run):
        self.resource_stage.ensure(run)
        # Persist successful registration before any producer can claim work.
        # Missing registered ledgers fail closed, rather than rebuilding tasks.
        self._save(run)

    @staticmethod
    def _resource_progress(run, view):
        run['resource_progress'] = dict(status=view['status'], stop_proven=view['stop_proven'],
            usage=deepcopy(view['usage']), budget=deepcopy(view['budget']),
            nodes=[dict(id=node_id, kind=row['definition']['kind'], producer=row['definition']['producer'],
                status=row['status'], stop_proven=row['stop_proven'], error=row['error'],
                resource_version_id=(row['output'] or {}).get('resource_version_id'))
                for node_id, row in view['nodes'].items()])

    def tick(self):
        with self.store.lock:
            with self.store.transaction() as db:
                runs = [json.loads(row[0]) for row in db.execute('SELECT document FROM production_runs ORDER BY rowid')]
            for run in runs:
                if run['status'] not in ACTIVE and not run.get('draining'):
                    self._recover_sequence_registration(run)
                    continue
                try:
                    self._step(run)
                except ServiceError as error:
                    self._finish(run, 'failed', error.code)
                    run['draining'] = True
                    if error.code == 'production_resource_ledger_missing':
                        run['blocked_reason'] = error.code
                    self._save(run)
                except Exception:
                    # The local child may have committed: recover by stable key
                    # on the next tick rather than constructing another intent.
                    run['error'] = 'production_run_reconciliation_interrupted'
                    self._save(run)
                self._recover_sequence_registration(run)

    def _recover_sequence_registration(self, run):
        """Recover only explicit pending metadata; never replay creation work."""
        if (self.sequence_store is None or not (self._uses_film(run) or self._uses_scene_space(run)) or run['status'] != 'completed'
                or 'sequence_registration' not in run
                or run['sequence_registration']['status'] == 'registered'):
            return
        try:
            with self.store.transaction() as db:
                self.store._project(db, run['owner'], run['project_id'])
            self._register_sequence(run, seen={run['id']})
        except ServiceError as error:
            self._registration_error(run, error.code, blocked=True)
        except (OSError, sqlite3.Error):
            self._registration_error(run, 'production_run_sequence_unavailable', blocked=True)
        except Exception:
            # The ledger may already have committed. Its exact source lookup
            # precedes any repeat write after restart; never refund or reset.
            self._registration_error(run, 'production_run_sequence_registration_interrupted', blocked=False)
