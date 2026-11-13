"""Dynamic authoring intent and candidates beside historical static state.

These tables never reinterpret static source/format, rewrite a static head, or
adopt Agent output. Semantic dependencies intentionally exclude voice and audio.
"""
from __future__ import annotations

import json
import time
from uuid import uuid4

from agent_service.contracts import ServiceError, digest
from agent_service.profiles.video_motion import validate_motion_input, validate_motion_result
from agent_service.profiles.video_process import validate_process_input, validate_process_result
from .motion import make_beats, estimated_alignment
from .audio_store import narration_document, narration_binding
from .explanation_store import visual_generation_description
from .animation_runtime_capabilities import validate_runtime_capabilities


ACTIVE = frozenset({'queued', 'submitting', 'running', 'cancelling'})
TERMINAL = frozenset({'completed', 'failed', 'needs_input', 'cancelled', 'timed_out'})
MAX_CANDIDATES_PER_SCENE = 64
MAX_PROJECT_OPERATIONS = 256
MAX_PROJECT_JSON_BYTES = 32 * 1024 * 1024
MAX_TOTAL_JSON_BYTES = 128 * 1024 * 1024
# Input beats (256KiB), plan (128KiB), and bounded coordinator metadata fit in
# this reservation. JSON budgets do not claim to measure SQLite/WAL disk bytes.
OUTPUT_RESERVE_BYTES = 512 * 1024
CREATIVE_OUTPUT_RESERVE_BYTES = 5 * 1024 * 1024
CREATIVE_PENDING_SCHEMA = 'video.animation-production-pending.v1'
PROCESS_CANDIDATE_COMPILER = 'motion-process-compiler-2'


def _require(condition, code='invalid_motion_request', status=422):
    if not condition:
        raise ServiceError(code, status)


def semantic_binding(project, scene):
    """The scene's intended explanation; pronunciations are resolved later."""
    return digest(dict(schema='video.motion-semantic-binding.v1', title=scene['title'],
        narration=scene['narration'], visual_description=visual_generation_description(scene),
        style=project.get('style', '')))


def plan_diagram_kind(plan):
    _require(type(plan) is dict and type(plan.get('schema')) is str
             and plan['schema'] in {'video.motion-plan.v1', 'video.process-plan.v1', 'video.creative-scene.v1',
                                   'video.creative-scene.v2', 'video.creative-scene.v3'},
             'motion_candidate_schema_invalid')
    if plan['schema'] in {'video.creative-scene.v1', 'video.creative-scene.v2', 'video.creative-scene.v3'}:
        return 'creative'
    return 'process' if plan['schema'] == 'video.process-plan.v1' else 'diagram'


def operation_diagram_kind(operation):
    document = operation.get('document')
    _require(type(document) is dict and type(document.get('schema')) is str and document['schema'] in {
        'video.motion-input.v1', 'video.process-input.v1', 'video.animation-experiment-input.v1',
        'video.animation-production-input.v1', 'video.animation-production-input.v2',
        CREATIVE_PENDING_SCHEMA}, 'motion_operation_schema_invalid')
    if document['schema'] in {'video.animation-experiment-input.v1', 'video.animation-production-input.v1',
                             'video.animation-production-input.v2', CREATIVE_PENDING_SCHEMA}:
        return 'creative'
    return 'process' if document['schema'] == 'video.process-input.v1' else 'diagram'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _size(value):
    return len(_json(value).encode('utf-8'))


class MotionStore:
    def __init__(self, store, *, runtime_capabilities=None):
        self.store = store
        self.runtime_capabilities = (None if runtime_capabilities is None
                                     else validate_runtime_capabilities(runtime_capabilities))
        with store.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS motion_operations (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, project_id TEXT NOT NULL,
                    scene_id TEXT NOT NULL, idem TEXT NOT NULL, request_hash TEXT NOT NULL,
                    document TEXT NOT NULL, UNIQUE(owner, idem));
                CREATE TABLE IF NOT EXISTS motion_versions (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, project_id TEXT NOT NULL,
                    scene_id TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS motion_heads (
                    owner TEXT NOT NULL, project_id TEXT NOT NULL, scene_id TEXT NOT NULL,
                    version_id TEXT, mode TEXT NOT NULL,
                    PRIMARY KEY(owner, project_id, scene_id));
                CREATE INDEX IF NOT EXISTS motion_operations_project ON motion_operations(project_id,scene_id);
                CREATE INDEX IF NOT EXISTS motion_versions_scene ON motion_versions(project_id,scene_id);
            ''')

    @staticmethod
    def _usage(db, project_id=None):
        clause, args = (' WHERE project_id=?', (project_id,)) if project_id is not None else ('', ())
        operations = db.execute('SELECT COALESCE(SUM(length(CAST(document AS BLOB))),0), '
            "COALESCE(SUM(CASE WHEN json_extract(document,'$.status') IN "
            "('queued','submitting','running','cancelling') THEN "
            "COALESCE(json_extract(document,'$.output_reserve_bytes'),512*1024) ELSE 0 END),0) "
            'FROM motion_operations' + clause, args).fetchone()
        versions = db.execute('SELECT COALESCE(SUM(length(CAST(document AS BLOB))),0) '
                              'FROM motion_versions' + clause, args).fetchone()[0]
        alignments = 0
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='motion_alignments'").fetchone():
            alignments = db.execute('SELECT COALESCE(SUM(length(CAST(document AS BLOB))),0) '
                                    'FROM motion_alignments' + clause, args).fetchone()[0]
        return operations[0] + versions + alignments + operations[1]

    def _capacity(self, db, project_id, *, extra_bytes=0, active_delta=0, reserve_bytes=OUTPUT_RESERVE_BYTES):
        extra = extra_bytes + active_delta * reserve_bytes
        _require(self._usage(db, project_id) + extra <= MAX_PROJECT_JSON_BYTES
                 and self._usage(db) + extra <= MAX_TOTAL_JSON_BYTES, 'motion_storage_limit', 413)

    @staticmethod
    def _public(op):
        from .motion_render_adapter import safe_motion_context
        result = {k: op.get(k) for k in ('id', 'kind', 'status', 'error', 'job_id', 'version_id', 'created_at')}
        if context := safe_motion_context(op.get('motion_context')):
            result['motion_context'] = context
        return result

    @staticmethod
    def _op(db, operation_id, owner=None):
        row = db.execute('SELECT owner,document FROM motion_operations WHERE id=?', (operation_id,)).fetchone()
        if row is None or owner is not None and row['owner'] != owner:
            raise ServiceError('not_found', 404)
        return json.loads(row['document'])

    @staticmethod
    def _write_op(db, op):
        db.execute('UPDATE motion_operations SET document=? WHERE id=?', (_json(op), op['id']))

    @staticmethod
    def _head(db, owner, project_id, scene_id, *, default_mode='static'):
        row = db.execute('SELECT version_id,mode FROM motion_heads WHERE owner=? AND project_id=? AND scene_id=?',
                         (owner, project_id, scene_id)).fetchone()
        return dict(row) if row else dict(version_id=None, mode=default_mode)

    @staticmethod
    def _version(db, owner, version_id):
        _require(type(version_id) is str and 1 <= len(version_id) <= 100)
        row = db.execute('SELECT v.document FROM motion_versions v JOIN projects p ON p.id=v.project_id '
                         'WHERE v.id=? AND v.owner=? AND p.owner=?', (version_id, owner, owner)).fetchone()
        if row is None:
            raise ServiceError('not_found', 404)
        return json.loads(row['document'])

    @staticmethod
    def _metadata(version, binding):
        creative = plan_diagram_kind(version['plan']) == 'creative'
        from .creative_contract import scene_context
        production = scene_context(version['plan']) if creative else None
        return {k: version[k] for k in ('id', 'created_at', 'parent_version_id', 'input_sha256',
                                       'plan_sha256', 'semantic_binding_sha256')} | {
            'title': production['title'] if creative else version['plan']['title'],
            'template': 'creative' if creative else version['plan']['template'],
            'stale': version['semantic_binding_sha256'] != binding,
            'diagram_kind': plan_diagram_kind(version['plan'])} | (
                {'compiler_version': version['compiler_version']} if 'compiler_version' in version else {})

    def _creative_audio(self, db, project, scene, *, version_id=None):
        """Resolve the exact current owner-scoped recording inside the transaction."""
        identity = scene.get('head_audio_version_id') if version_id is None else version_id
        _require(bool(identity), 'missing_current_audio', 409)
        row = db.execute('SELECT document,data FROM audio_versions WHERE id=? AND project_id=? AND scene_id=?',
                         (identity, project['id'], scene['id'])).fetchone()
        _require(row is not None, 'missing_current_audio', 409)
        audio = dict(json.loads(row['document']), data=bytes(row['data']))
        document = narration_document(scene, self.store._project_speech_provider(project))
        _require(audio['input_sha256'] == narration_binding(document), 'audio_stale', 409)
        from .motion_snapshot import _audio
        checked = _audio(audio)
        _require(checked['duration_seconds'] <= 60, 'motion_scene_duration_exceeded', 422)
        return audio, document, checked

    @staticmethod
    def _audio_stale(version, scene):
        if plan_diagram_kind(version['plan']) != 'creative':
            return False
        selected = next((a for a in scene.get('audio_versions', [])
                         if a['id'] == scene.get('head_audio_version_id')), None)
        return (selected is None or selected.get('stale', False)
                or selected.get('sha256') != version['audio_sha256'])

    def project(self, owner, project_id):
        # The existing lock keeps the static and additive views on one revision,
        # without opening nested SQLite transactions.
        with self.store.lock:
            project = self.store.project(owner, project_id)
            with self.store.transaction() as db:
                for scene in project['scenes']:
                    binding = semantic_binding(project, scene)
                    head = self._head(db, owner, project_id, scene['id'],
                                      default_mode=scene.get('production_mode', 'static'))
                    versions = [json.loads(r['document']) for r in db.execute(
                        'SELECT document FROM motion_versions WHERE owner=? AND project_id=? AND scene_id=? ORDER BY rowid',
                        (owner, project_id, scene['id']))]
                    ops = [json.loads(r['document']) for r in db.execute(
                        'SELECT document FROM motion_operations WHERE owner=? AND project_id=? AND scene_id=? ORDER BY rowid',
                        (owner, project_id, scene['id']))]
                    metadata = [dict(self._metadata(v, binding),
                                     stale=v['semantic_binding_sha256'] != binding or self._audio_stale(v, scene))
                                for v in versions]
                    adopted = next((v for v in metadata if v['id'] == head['version_id']), None)
                    scene['motion'] = dict(mode=head['mode'], head_version_id=head['version_id'],
                        head_stale=bool(head['version_id'] and (adopted is None or adopted['stale'])),
                        semantic_binding_sha256=binding, versions=metadata,
                        operation=self._public(ops[-1]) if ops else None)
            return project

    def enable_project_motion(self, owner, project_id, expected_revision):
        """Change future/pending intent without replacing any selected media.

        Old scenes with an adopted static version or an explicit visual mode
        keep their existing selection. Unselected scenes can become pending
        motion, which has a null plan head until the user explicitly adopts one.
        """
        _require(type(expected_revision) is int and expected_revision >= 1)
        with self.store.transaction() as db:
            project = self.store._project(db, owner, project_id)
            self.store._revision(project, expected_revision)
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            terminal = {'completed', 'failed', 'cancelled', 'timed_out', 'needs_input'}
            for table in ('operations', 'motion_operations', 'audio_operations',
                          'explanation_operations', 'video_renders', 'video_batches'):
                if table in tables:
                    allowed = {'completed', 'partial_failed', 'cancelled'} if table == 'video_batches' else terminal
                    _require(all(json.loads(row[0]).get('status') in allowed for row in db.execute(
                        'SELECT document FROM ' + table + ' WHERE project_id=?', (project_id,))),
                        'project_production_mode_busy', 409)
            changed = project.get('production_mode', 'static') != 'motion'
            project['production_mode'] = 'motion'
            for scene in project['scenes']:
                selected = scene.get('head_version_id') or db.execute(
                    'SELECT 1 FROM motion_heads WHERE owner=? AND project_id=? AND scene_id=?',
                    (owner, project_id, scene['id'])).fetchone()
                if not selected and scene.get('production_mode', 'static') != 'motion':
                    scene['production_mode'] = 'motion'
                    changed = True
            if changed:
                self.store._save(db, project)
        return self.project(owner, project_id)

    def version(self, owner, version_id):
        with self.store.transaction() as db:
            version = self._version(db, owner, version_id)
            project = self.store._project(db, owner, version['project_id'])
            if not any(s['id'] == version['scene_id'] for s in project['scenes']):
                # Retired by an adopted joint plan; the immutable media and
                # source remain owner-readable, but cannot be adopted anew.
                return dict(version, stale=True, retired=True)
            scene = self.store._scene(project, version['scene_id'])
            self.store._attach_audio(db, project)
            version['stale'] = (version['semantic_binding_sha256'] != semantic_binding(project, scene)
                                or self._audio_stale(version, scene))
            return version

    def candidate_version(self, owner, version_id, audio_version_id):
        """Resolve a draft pair without granting or borrowing user adoption.

        Head freshness is meaningful for the manual production path. A run's
        private candidate instead binds its exact recording, current speech
        input, semantics and measured WAV bytes in the same transaction.
        """
        with self.store.transaction() as db:
            version = self._version(db, owner, version_id)
            project = self.store._project(db, owner, version['project_id'])
            scene = self.store._scene(project, version['scene_id'])
            _require(plan_diagram_kind(version['plan']) == 'creative', 'motion_base_kind_mismatch', 409)
            audio, _, checked = self._creative_audio(db, project, scene, version_id=audio_version_id)
            _require(version.get('audio_version_id') == audio['id']
                     and version.get('audio_sha256') == checked['sha256'],
                     'creative_audio_binding_mismatch', 409)
            return dict(version, stale=version['semantic_binding_sha256'] != semantic_binding(project, scene))

    def start_candidate(self, owner, scene_id, request, idem, *, audio_version_id):
        """Coordinator-only admission; no HTTP request can set this selection."""
        _require(type(audio_version_id) is str and 1 <= len(audio_version_id) <= 100)
        _require(type(request) is dict and request.get('diagram_kind') == 'creative'
                 and 'expected_audio_version_id' not in request)
        return self.start(owner, scene_id, request, idem, _draft_audio_version_id=audio_version_id)

    def start(self, owner, scene_id, request, idem, *, timeout=600, _draft_audio_version_id=None):
        _require(type(request) is dict and {'expected_revision'} <= set(request)
                 and set(request) <= {'expected_revision', 'instruction', 'base_version_id', 'diagram_kind',
                                      'expected_semantic_binding_sha256', 'expected_audio_version_id'}
                 and type(request['expected_revision']) is int and request['expected_revision'] >= 1)
        expected_binding = request.get('expected_semantic_binding_sha256')
        _require(expected_binding is None or type(expected_binding) is str and len(expected_binding) == 64
                 and all(char in '0123456789abcdef' for char in expected_binding))
        diagram_kind = request.get('diagram_kind', 'diagram')
        _require(type(diagram_kind) is str and diagram_kind in {'diagram', 'process', 'creative'})
        expected_audio = request.get('expected_audio_version_id')
        _require('expected_audio_version_id' not in request or diagram_kind == 'creative'
                 and type(expected_audio) is str and 1 <= len(expected_audio) <= 100)
        if diagram_kind == 'diagram' and 'diagram_kind' in request:
            # Only normalize this newly introduced default. Preserve the old
            # instruction/base defaults and every historical request hash.
            request = {key: value for key, value in request.items() if key != 'diagram_kind'}
        _require(type(idem) is str and 1 <= len(idem) <= 128
                 and all(33 <= ord(c) <= 126 for c in idem))
        _require(type(timeout) in (int, float) and 1 <= timeout <= 600)
        project_id = self.store.scene_project(owner, scene_id)
        identity = dict(scene_id=scene_id, request=request, timeout=timeout)
        if _draft_audio_version_id is not None:
            _require(diagram_kind == 'creative' and type(_draft_audio_version_id) is str
                     and 1 <= len(_draft_audio_version_id) <= 100)
            identity.update(selection='draft_only', audio_version_id=_draft_audio_version_id)
        fingerprint = digest(identity)
        with self.store.transaction() as db:
            existing = db.execute('SELECT request_hash,document FROM motion_operations WHERE owner=? AND idem=?',
                                  (owner, idem)).fetchone()
            if existing:
                _require(existing['request_hash'] == fingerprint, 'idempotency_conflict', 409)
                return self._public(json.loads(existing['document']))
            project = self.store._project(db, owner, project_id)
            scene = self.store._scene(project, scene_id)
            binding = semantic_binding(project, scene)
            # A result on another scene changes the project revision without
            # changing this generation's input. Compare the server-issued scene
            # binding inside the same transaction; legacy requests stay strict.
            if project['revision'] != request['expected_revision']:
                _require(request['expected_revision'] < project['revision'] and expected_binding is not None,
                         'revision_conflict', 409)
                if diagram_kind == 'creative':
                    _require(expected_audio is not None or _draft_audio_version_id is not None,
                             'revision_conflict', 409)
            if expected_binding is not None:
                _require(expected_binding == binding, 'motion_generation_conflict', 409)
            if expected_audio is not None:
                _require(scene.get('head_audio_version_id') == expected_audio, 'creative_audio_binding_mismatch', 409)
            active = [o for r in db.execute('SELECT document FROM motion_operations WHERE owner=?', (owner,))
                      if (o := json.loads(r['document']))['status'] in ACTIVE]
            _require(not any(o['scene_id'] == scene_id for o in active), 'motion_operation_in_progress', 409)
            _require(len(active) < 12, 'motion_queue_limit', 409)
            _require(db.execute('SELECT COUNT(*) FROM motion_operations WHERE project_id=?',
                                (project_id,)).fetchone()[0] < MAX_PROJECT_OPERATIONS,
                     'motion_operation_limit', 409)
            _require(db.execute('SELECT COUNT(*) FROM motion_versions WHERE project_id=? AND scene_id=?',
                                (project_id, scene_id)).fetchone()[0] < MAX_CANDIDATES_PER_SCENE,
                     'motion_candidate_limit', 409)
            base_id = request.get('base_version_id')
            base = None
            if base_id is not None:
                _require(type(base_id) is str and 1 <= len(base_id) <= 100)
                base = self._version(db, owner, base_id)
                _require(base['scene_id'] == scene_id and base['project_id'] == project_id, 'not_found', 404)
                _require(base['semantic_binding_sha256'] == binding, 'motion_base_stale', 409)
                _require(plan_diagram_kind(base['plan']) == diagram_kind, 'motion_base_kind_mismatch', 409)
            audio_binding = {}
            if diagram_kind == 'creative':
                from .creative_method_contract import prepare_core_task
                from .creative_contract import scene_source
                audio, narration, checked = self._creative_audio(db, project, scene,
                    version_id=_draft_audio_version_id)
                beats = make_beats(scene['narration'], checked['spoken_text'])
                alignment = estimated_alignment(beats, checked['sha256'], checked['duration_seconds'])
                core = prepare_core_task(scene_id=scene_id, title=scene['title'],
                    visual_description=visual_generation_description(scene, enforce_limit=True),
                    narration=scene['narration'], beats=beats, alignment=alignment,
                    audio_sha256=checked['sha256'], duration_seconds=checked['duration_seconds'],
                    style=project.get('style', ''),
                    instruction=request.get('instruction', ''),
                    base_source=scene_source(base['plan']) if base else None,
                    engine_aware=self.runtime_capabilities is not None,
                    **(dict(allowed_engines=self.runtime_capabilities['allowed_engines'],
                            runtime_identity=self.runtime_capabilities['runtime_identity'],
                            assets=self.runtime_capabilities['assets'])
                       if self.runtime_capabilities is not None else {}))
                # Reserve durable identity before network I/O. The workflow
                # freezes knowledge outside this business transaction.
                document = dict(schema=CREATIVE_PENDING_SCHEMA, core_task=core)
                audio_binding = dict(audio_version_id=audio['id'], audio_sha256=checked['sha256'],
                                     output_reserve_bytes=CREATIVE_OUTPUT_RESERVE_BYTES)
                if _draft_audio_version_id is not None:
                    audio_binding['selection'] = 'draft_only'
            else:
                document = dict(schema='video.process-input.v1' if diagram_kind == 'process' else 'video.motion-input.v1',
                    title=scene['title'], style=project.get('style', ''),
                    visual_description=visual_generation_description(scene, enforce_limit=True), beats=make_beats(scene['narration']),
                    instruction=request.get('instruction', ''), base_plan=base['plan'] if base else None)
                (validate_process_input if diagram_kind == 'process' else validate_motion_input)(document)
            now = time.time()
            op = dict(id=str(uuid4()), owner=owner, project_id=project_id, scene_id=scene_id,
                kind={'process': 'process-plan', 'creative': 'creative-scene', 'diagram': 'motion-plan'}[diagram_kind],
                document=document, input_sha256=digest(document),
                semantic_binding_sha256=binding, parent_version_id=base_id, status='queued', error=None,
                job_id=None, input_ref=None, submit_attempted=False, cancel_requested=False,
                cancel_attempted=False, cancel_attempts=0, next_cancel_at=0.0,
                deadline_at=now + timeout, created_at=now, version_id=None, **audio_binding)
            self._capacity(db, project_id, extra_bytes=_size(op), active_delta=1,
                           reserve_bytes=op.get('output_reserve_bytes', OUTPUT_RESERVE_BYTES))
            db.execute('INSERT INTO motion_operations VALUES (?, ?, ?, ?, ?, ?, ?)',
                (op['id'], owner, project_id, scene_id, idem, fingerprint, _json(op)))
            self.store._save(db, project)
            return self._public(op)

    def operation(self, operation_id, owner=None):
        with self.store.transaction() as db:
            return self._op(db, operation_id, owner)

    def prepare_creative(self, operation_id, adapter):
        """Freeze one exact knowledge delivery before Agent upload/admission.

        A crash during the external read recovers the adapter's durable intent
        under this operation's stable consumer id. No HTTP runs in a Store
        transaction, and no published update can change a completed freeze.
        """
        from .creative_method_contract import build_scene_input
        op = self.operation(operation_id)
        if op['document'].get('schema') != CREATIVE_PENDING_SCHEMA:
            return op
        _require(adapter is not None, 'creative_methods_unavailable', 503)
        if op['status'] not in ACTIVE or op['cancel_requested'] or time.time() >= op['deadline_at']:
            return op
        core = op['document']['core_task']
        snapshot = adapter.freeze(op['owner'], 'video-production:' + op['id'], core,
                                  core['constraints']['allowed_engines'])
        document = build_scene_input(core_task=core, method_delivery=snapshot,
                                     engine_aware='runtime_identity' in core['constraints'])
        with self.store.transaction() as db:
            current = self._op(db, operation_id)
            if current['document'].get('schema') != CREATIVE_PENDING_SCHEMA:
                _require(current['document'] == document, 'production_method_frozen_input_changed', 409)
                return current
            _require(current['document'] == op['document'] and not current['submit_attempted']
                     and current['input_ref'] is None and current['job_id'] is None,
                     'production_method_frozen_input_changed', 409)
            if current['status'] not in ACTIVE or current['cancel_requested'] or time.time() >= current['deadline_at']:
                return current
            old_size = _size(current)
            current.update(document=document, input_sha256=digest(document))
            self._capacity(db, current['project_id'], extra_bytes=_size(current) - old_size)
            self._write_op(db, current)
            return current

    def public_operation(self, owner, operation_id):
        return self._public(self.operation(operation_id, owner))

    def active_operations(self):
        with self.store.transaction() as db:
            return [op for r in db.execute('SELECT document FROM motion_operations')
                    if (op := json.loads(r['document']))['status'] in ACTIVE]

    def update_operation(self, operation_id, **fields):
        _require(set(fields) <= {'status', 'error', 'job_id', 'input_ref', 'cancel_attempted', 'motion_context'})
        if 'motion_context' in fields:
            from .motion_render_adapter import safe_motion_context
            fields['motion_context'] = safe_motion_context(fields['motion_context'])
        if 'status' in fields:
            _require(type(fields['status']) is str and fields['status'] in ACTIVE | TERMINAL)
        with self.store.transaction() as db:
            op = self._op(db, operation_id)
            if op['status'] in TERMINAL:
                return op
            op.update(fields)
            self._write_op(db, op)
            return op

    def claim_submission(self, operation_id):
        with self.store.transaction() as db:
            op = self._op(db, operation_id)
            if op['status'] not in ACTIVE or op['submit_attempted']:
                return None
            if op['cancel_requested'] or time.time() >= op['deadline_at']:
                op.update(status='cancelled' if op['cancel_requested'] else 'timed_out', error=None)
                claimed = None
            else:
                op.update(submit_attempted=True, status='submitting', error=None)
                claimed = op
            self._write_op(db, op)
            return claimed

    def claim_cancellation(self, operation_id, *, now, retry_delays):
        """Reserve a bounded idempotent cancel attempt across process restarts.

        Persist the next retry time before I/O. A crash can consume one attempt,
        but it cannot permanently suppress all later cancellation attempts.
        The caller must first observe that the same remote job is still queued
        or running; an observed cancelling/terminal job needs no more requests.
        """
        _require(type(now) in (int, float) and 0 <= now <= 10 ** 12
                 and type(retry_delays) in (tuple, list) and 1 <= len(retry_delays) <= 8
                 and all(type(delay) in (int, float) and 1 <= delay <= 60 for delay in retry_delays))
        with self.store.transaction() as db:
            op = self._op(db, operation_id)
            if op['status'] not in ACTIVE or not (op['cancel_requested'] or now >= op['deadline_at']):
                return None
            attempts = op.get('cancel_attempts', 0)
            if attempts >= len(retry_delays):
                op.update(status='cancelling', error='cancellation_retry_exhausted')
                self._write_op(db, op)
                return None
            if now < op.get('next_cancel_at', 0):
                return None
            op.update(status='cancelling', error=None, cancel_attempted=True,
                      cancel_attempts=attempts + 1, next_cancel_at=now + retry_delays[attempts])
            self._write_op(db, op)
            return op

    def cancel(self, owner, operation_id):
        with self.store.transaction() as db:
            op = self._op(db, operation_id, owner)
            if op['status'] in ACTIVE:
                op.update(cancel_requested=True, status='cancelling')
                self._write_op(db, op)
            return self._public(op)

    def complete(self, operation_id, result):
        with self.store.transaction() as db:
            op = self._op(db, operation_id)
            if op['status'] not in ACTIVE:
                return self._public(op)
            old_op_size = _size(op)
            project = self.store._project(db, op['owner'], op['project_id'])
            self.store._scene(project, op['scene_id'])
            if op['cancel_requested']:
                # Upstream completion won the race. Do not misreport a proven
                # cancellation or keep a candidate the user asked to discard.
                op.update(status='completed', error='cancel_arrived_after_completion')
            elif time.time() >= op['deadline_at']:
                op.update(status='timed_out', error='motion_result_after_deadline')
            else:
                kind = operation_diagram_kind(op)
                process, creative = kind == 'process', kind == 'creative'
                if creative:
                    from .creative_method_contract import collect_scene_result
                    from .creative_contract import scene_source, scene_context
                    plan = collect_scene_result(op['document'], result)
                    beats = scene_context(plan)['beats']
                    if op['parent_version_id'] is not None:
                        parent = self._version(db, op['owner'], op['parent_version_id'])
                        if scene_source(plan)['files_sha256'] == scene_source(parent['plan'])['files_sha256']:
                            op.update(status='needs_input', error='motion_revision_unchanged')
                            self._write_op(db, op)
                            self.store._save(db, project)
                            return self._public(op)
                else:
                    (validate_process_result if process else validate_motion_result)(
                        result, op['input_sha256'], op['document']['beats'])
                    plan, beats = result['plan'], op['document']['beats']
                agent_plan_sha256 = digest(plan)
                if not process and not creative:
                    from agent_service.profiles.video_references import preserve_plan_references
                    from .motion import preflight_plan
                    plan = preserve_plan_references(plan, op['document'])
                    if (op['parent_version_id'] is not None
                            and op['document']['instruction'].strip()
                            and plan == op['document']['base_plan']):
                        # A new ID alone is not evidence that a requested edit
                        # happened. Keep the original adoption/media and avoid
                        # saving or automatically retrying an unchanged result.
                        op.update(status='needs_input', error='motion_revision_unchanged')
                        self._write_op(db, op)
                        self.store._save(db, project)
                        return self._public(op)
                    preflight_plan(plan, op['document']['beats'])
                version = dict(id=str(uuid4()), project_id=op['project_id'], scene_id=op['scene_id'],
                    operation_id=op['id'], created_at=time.time(), parent_version_id=op['parent_version_id'],
                    input_sha256=op['input_sha256'], semantic_binding_sha256=op['semantic_binding_sha256'],
                    beats=beats, plan=plan, plan_sha256=digest(plan))
                if creative:
                    from .creative_renderer import METHOD_COMPILER, ENGINE_METHOD_COMPILER
                    compiler = ENGINE_METHOD_COMPILER if plan['schema'] == 'video.creative-scene.v3' else METHOD_COMPILER
                    version.update(diagram_kind='creative', compiler_version=compiler,
                        audio_version_id=op['audio_version_id'], audio_sha256=op['audio_sha256'])
                elif process:
                    version.update(diagram_kind='process', compiler_version=PROCESS_CANDIDATE_COMPILER)
                else:
                    from .motion import COMPILER_VERSION
                    version.update(compiler_version=COMPILER_VERSION,
                        agent_plan_sha256=agent_plan_sha256,
                        preparation='reference-preservation-and-layout-preflight-v1')
                reserve = op.get('output_reserve_bytes', OUTPUT_RESERVE_BYTES)
                _require(_size(version) <= reserve - 8192, 'motion_candidate_too_large', 413)
                op.update(status='completed', error=None, version_id=version['id'])
                self._capacity(db, op['project_id'],
                    extra_bytes=_size(version) + _size(op) - old_op_size, active_delta=-1,
                    reserve_bytes=reserve)
                db.execute('INSERT INTO motion_versions VALUES (?, ?, ?, ?, ?, ?)',
                    (version['id'], op['owner'], op['project_id'], op['scene_id'], op['id'], _json(version)))
            self._write_op(db, op)
            self.store._save(db, project)
            return self._public(op)

    def adopt_batch(self, owner, project_id, selections, expected_revision):
        """Explicit atomic adoption of exact versions; no 'latest' lookups."""
        _require(type(selections) is list and 1 <= len(selections) <= 12)
        _require(type(expected_revision) is int and expected_revision >= 1)
        with self.store.transaction() as db:
            project = self.store._project(db, owner, project_id)
            self.store._revision(project, expected_revision)
            checked, scenes = [], set()
            for selection in selections:
                _require(type(selection) is dict
                         and set(selection) == {'scene_id', 'version_id', 'expected_head_version_id'})
                scene_id = selection['scene_id']
                _require(type(scene_id) is str and scene_id not in scenes)
                _require(type(selection['version_id']) is str and 1 <= len(selection['version_id']) <= 100
                         and (selection['expected_head_version_id'] is None or
                              type(selection['expected_head_version_id']) is str))
                scenes.add(scene_id)
                scene = self.store._scene(project, scene_id)
                version = self._version(db, owner, selection['version_id'])
                _require(version['scene_id'] == scene_id and version['project_id'] == project_id, 'not_found', 404)
                head = self._head(db, owner, project_id, scene_id)
                _require(head['version_id'] == selection['expected_head_version_id'], 'motion_head_conflict', 409)
                _require(version['semantic_binding_sha256'] == semantic_binding(project, scene), 'motion_plan_stale', 409)
                if plan_diagram_kind(version['plan']) == 'creative':
                    _, _, audio = self._creative_audio(db, project, scene)
                    _require(audio['sha256'] == version['audio_sha256'], 'creative_audio_binding_mismatch', 409)
                checked.append((scene_id, version['id']))
            for scene_id, version_id in checked:
                db.execute('INSERT INTO motion_heads VALUES (?, ?, ?, ?, ?) ON CONFLICT(owner,project_id,scene_id) '
                           'DO UPDATE SET version_id=excluded.version_id,mode=excluded.mode',
                           (owner, project_id, scene_id, version_id, 'motion'))
            self.store._save(db, project)
        return self.project(owner, project_id)

    def adopt(self, owner, scene_id, version_id, expected_head_version_id, expected_revision):
        project_id = self.store.scene_project(owner, scene_id)
        return self.adopt_batch(owner, project_id, [dict(scene_id=scene_id, version_id=version_id,
            expected_head_version_id=expected_head_version_id)], expected_revision)

    def set_mode(self, owner, scene_id, mode, expected_revision):
        _require(type(mode) is str and mode in {'static', 'motion'})
        _require(type(expected_revision) is int and expected_revision >= 1)
        project_id = self.store.scene_project(owner, scene_id)
        with self.store.transaction() as db:
            project = self.store._project(db, owner, project_id)
            self.store._revision(project, expected_revision)
            scene = self.store._scene(project, scene_id)
            head = self._head(db, owner, project_id, scene_id)
            if mode == 'motion':
                _require(head['version_id'] is not None, 'missing_motion_plan', 409)
                version = self._version(db, owner, head['version_id'])
                _require(version['semantic_binding_sha256'] == semantic_binding(project, scene), 'motion_plan_stale', 409)
                if plan_diagram_kind(version['plan']) == 'creative':
                    _, _, audio = self._creative_audio(db, project, scene)
                    _require(audio['sha256'] == version['audio_sha256'], 'creative_audio_binding_mismatch', 409)
            db.execute('INSERT INTO motion_heads VALUES (?, ?, ?, ?, ?) ON CONFLICT(owner,project_id,scene_id) '
                       'DO UPDATE SET mode=excluded.mode', (owner, project_id, scene_id, head['version_id'], mode))
            self.store._save(db, project)
        return self.project(owner, project_id)
