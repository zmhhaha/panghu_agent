"""Project joint_plan candidates; explicit adoption changes content atomically.

Agent jobs never select media or a voice. Input bindings describe actual content
and speech settings, rather than project revision counters or unrelated jobs.
"""
from __future__ import annotations

import json
import time
from uuid import uuid4

from agent_service.contracts import ServiceError, digest
from agent_service.profiles.video_joint_plan import (
    validate_joint_plan_input, validate_joint_plan_result,
)
from agent_service.profiles.video_joint_plan_contract import planning_method_task
from .audio_store import speech_options
from .explanation_store import scene_visual_binding, visual_generation_description
from .animation_runtime_capabilities import validate_runtime_capabilities


ACTIVE = frozenset({'queued', 'submitting', 'running', 'cancelling'})
TERMINAL = frozenset({'completed', 'failed', 'needs_input', 'cancelled', 'timed_out'})
MAX_PROJECT_CANDIDATES = 64
MAX_PROJECT_OPERATIONS = 256
MAX_PROJECT_JSON_BYTES = 32 * 1024 * 1024
MAX_TOTAL_JSON_BYTES = 128 * 1024 * 1024
# Reserve the complete bounded result and coordinator metadata before admission.
# These limits measure stored JSON, not SQLite/WAL disk usage.
OUTPUT_RESERVE_BYTES = 1024 * 1024 + 16384


def _require(value, code='invalid_joint_plan_request', status=422):
    if not value:
        raise ServiceError(code, status)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _size(value):
    return len(_json(value).encode('utf-8'))


class JointPlanStore:
    def __init__(self, store, *, runtime_capabilities=None):
        self.store = store
        self.allowed_engines = (['html-svg'] if runtime_capabilities is None else
                                validate_runtime_capabilities(runtime_capabilities)['allowed_engines'])
        with store.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS joint_plan_operations (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, project_id TEXT NOT NULL,
                    idem TEXT NOT NULL, request_hash TEXT NOT NULL, document TEXT NOT NULL,
                    UNIQUE(owner, idem));
                CREATE TABLE IF NOT EXISTS joint_plan_versions (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, project_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL UNIQUE, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS joint_plan_heads (
                    owner TEXT NOT NULL, project_id TEXT NOT NULL, version_id TEXT NOT NULL,
                    applied_binding_sha256 TEXT NOT NULL, PRIMARY KEY(owner, project_id));
                CREATE INDEX IF NOT EXISTS joint_plan_operations_project ON joint_plan_operations(project_id);
                CREATE INDEX IF NOT EXISTS joint_plan_versions_project ON joint_plan_versions(project_id);
                CREATE TABLE IF NOT EXISTS joint_plan_adoptions (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, project_id TEXT NOT NULL,
                    version_id TEXT NOT NULL, document TEXT NOT NULL);
            ''')

    def _content(self, project):
        return dict(project=dict(title=project['title'], script=project['script'], style=project.get('style', ''),
                    voice=self.store._project_speech_provider(project)['voice']),
            scenes=[dict(scene_id=scene['id'], **{key: scene[key] for key in
                ('title', 'narration', 'duration_seconds', 'format')},
                visual_description=visual_generation_description(scene),
                speech=speech_options(**{key: scene.get('speech_settings', {}).get(key, default)
                    for key, default in [('speed', 1.0), ('pronunciation', {})]}))
                for scene in project['scenes']])

    def _binding(self, project):
        return digest(dict(schema='video-joint-plan-content-binding.v1',
                           allowed_engines=self.allowed_engines, **self._content(project)))

    @staticmethod
    def _usage(db, project_id=None):
        clause, args = (' WHERE project_id=?', (project_id,)) if project_id is not None else ('', ())
        op = db.execute('SELECT COALESCE(SUM(length(CAST(document AS BLOB))),0), '
            "COALESCE(SUM(CASE WHEN json_extract(document,'$.status') IN "
            "('queued','submitting','running','cancelling') THEN 1 ELSE 0 END),0) "
            'FROM joint_plan_operations' + clause, args).fetchone()
        versions = db.execute('SELECT COALESCE(SUM(length(CAST(document AS BLOB))),0) '
                              'FROM joint_plan_versions' + clause, args).fetchone()[0]
        adoptions = db.execute('SELECT COALESCE(SUM(length(CAST(document AS BLOB))),0) '
                              'FROM joint_plan_adoptions' + clause, args).fetchone()[0]
        return op[0] + versions + adoptions + op[1] * OUTPUT_RESERVE_BYTES

    def _capacity(self, db, project_id, *, extra_bytes=0, active_delta=0):
        extra = extra_bytes + active_delta * OUTPUT_RESERVE_BYTES
        _require(self._usage(db, project_id) + extra <= MAX_PROJECT_JSON_BYTES
                 and self._usage(db) + extra <= MAX_TOTAL_JSON_BYTES, 'joint_plan_storage_limit', 413)

    @staticmethod
    def _public(op):
        return {key: op.get(key) for key in ('id', 'kind', 'status', 'error', 'job_id', 'version_id', 'created_at')}

    @staticmethod
    def _op(db, operation_id, owner=None):
        row = db.execute('SELECT owner,document FROM joint_plan_operations WHERE id=?', (operation_id,)).fetchone()
        if row is None or owner is not None and row['owner'] != owner:
            raise ServiceError('not_found', 404)
        return json.loads(row['document'])

    @staticmethod
    def _write_op(db, op):
        db.execute('UPDATE joint_plan_operations SET document=? WHERE id=?', (_json(op), op['id']))

    @staticmethod
    def _head(db, owner, project_id):
        row = db.execute('SELECT version_id,applied_binding_sha256 FROM joint_plan_heads '
                         'WHERE owner=? AND project_id=?', (owner, project_id)).fetchone()
        return dict(row) if row else dict(version_id=None, applied_binding_sha256=None)

    @staticmethod
    def _version(db, owner, version_id):
        _require(type(version_id) is str and 1 <= len(version_id) <= 100)
        row = db.execute('SELECT v.document FROM joint_plan_versions v JOIN projects p ON p.id=v.project_id '
                         'WHERE v.id=? AND v.owner=? AND p.owner=?', (version_id, owner, owner)).fetchone()
        if row is None:
            raise ServiceError('not_found', 404)
        return json.loads(row['document'])

    @staticmethod
    def _stale(version, binding, head):
        # The adopted plan writes new content itself. Bind its head to that exact
        # result, so adoption does not immediately mark its own head as stale.
        expected = (head['applied_binding_sha256'] if version['id'] == head['version_id']
                    else version['input_binding_sha256'])
        return expected != binding

    def project(self, owner, project_id):
        with self.store.transaction() as db:
            project = self.store._project(db, owner, project_id)
            binding, head = self._binding(project), self._head(db, owner, project_id)
            versions = [json.loads(row['document']) for row in db.execute(
                'SELECT document FROM joint_plan_versions WHERE owner=? AND project_id=? ORDER BY rowid',
                (owner, project_id))]
            for version in versions:
                version['stale'] = self._stale(version, binding, head)
                version['adoptable'] = not version['stale']
            latest = db.execute('SELECT document FROM joint_plan_operations WHERE owner=? AND project_id=? '
                                'ORDER BY rowid DESC LIMIT 1', (owner, project_id)).fetchone()
            latest_op = json.loads(latest['document']) if latest else None
            adopted = next((v for v in versions if v['id'] == head['version_id']), None)
            return dict(operation=self._public(latest_op) if latest_op else None,
                brief=latest_op['document']['brief'] if latest_op else None,
                instruction=latest_op['document'].get('instruction', '') if latest_op else '',
                preserve_script=latest_op['document']['preserve_script'] if latest_op else False,
                head_version_id=head['version_id'], head_stale=bool(head['version_id'] and
                    (adopted is None or adopted['stale'])), versions=versions)

    def version(self, owner, version_id):
        with self.store.transaction() as db:
            version = self._version(db, owner, version_id)
            project = self.store._project(db, owner, version['project_id'])
            head = self._head(db, owner, project['id'])
            version['stale'] = self._stale(version, self._binding(project), head)
            version['adoptable'] = not version['stale']
            return version

    def start(self, owner, project_id, request, idem, *, timeout=600):
        _require(type(request) is dict and {'expected_revision', 'brief'} <= set(request)
                 and set(request) <= {'expected_revision', 'brief', 'instruction', 'preserve_script'}
                 and type(request['expected_revision']) is int and request['expected_revision'] >= 1)
        _require(type(idem) is str and 1 <= len(idem) <= 128 and all(33 <= ord(c) <= 126 for c in idem))
        _require(type(timeout) in (int, float) and 1 <= timeout <= 600)
        fingerprint = digest(dict(project_id=project_id, request=request, timeout=timeout))
        with self.store.transaction() as db:
            existing = db.execute('SELECT request_hash,document FROM joint_plan_operations WHERE owner=? AND idem=?',
                                  (owner, idem)).fetchone()
            if existing:
                _require(existing['request_hash'] == fingerprint, 'idempotency_conflict', 409)
                return self._public(json.loads(existing['document']))
            project = self.store._project(db, owner, project_id)
            self.store._revision(project, request['expected_revision'])
            active = [op for row in db.execute('SELECT document FROM joint_plan_operations WHERE owner=?', (owner,))
                      if (op := json.loads(row['document']))['status'] in ACTIVE]
            _require(not any(op['project_id'] == project_id for op in active), 'joint_plan_operation_in_progress', 409)
            _require(len(active) < 12, 'joint_plan_queue_limit', 409)
            _require(db.execute('SELECT COUNT(*) FROM joint_plan_operations WHERE project_id=?', (project_id,))
                     .fetchone()[0] < MAX_PROJECT_OPERATIONS, 'joint_plan_operation_limit', 409)
            _require(db.execute('SELECT COUNT(*) FROM joint_plan_versions WHERE project_id=?', (project_id,))
                     .fetchone()[0] < MAX_PROJECT_CANDIDATES, 'joint_plan_candidate_limit', 409)
            document = dict(schema_version='video-joint-plan-input.v1', **self._content(project),
                            brief=request['brief'], instruction=request.get('instruction', ''),
                            preserve_script=request.get('preserve_script', False), method_delivery=None,
                            allowed_engines=list(self.allowed_engines))
            document = validate_joint_plan_input(document)
            now = time.time()
            op = dict(id=str(uuid4()), owner=owner, project_id=project_id, kind='joint-plan',
                document=document, input_sha256=digest(document), input_binding_sha256=self._binding(project),
                status='queued', error=None, job_id=None, input_ref=None, submit_attempted=False,
                method_prepared=False,
                cancel_requested=False, cancel_attempts=0, next_cancel_at=0.0,
                deadline_at=now + timeout, created_at=now, version_id=None)
            self._capacity(db, project_id, extra_bytes=_size(op), active_delta=1)
            db.execute('INSERT INTO joint_plan_operations VALUES (?, ?, ?, ?, ?, ?)',
                       (op['id'], owner, project_id, idem, fingerprint, _json(op)))
            self.store._save(db, project)
            return self._public(op)

    def prepare(self, operation_id, adapter=None):
        """Freeze optional published methods before the one paid Agent submit.

        The adapter's durable ledger reuses the same intent across crashes.
        No HTTP request runs while holding the project database transaction.
        """
        op = self.operation(operation_id)
        if op['status'] not in ACTIVE or op['method_prepared'] or op['cancel_requested']:
            return op
        content = self._content_from_document(op['document'])
        snapshot = None
        if adapter is not None:
            task = planning_method_task(content)
            snapshot = adapter.freeze(op['owner'], 'video-joint-plan:' + op['id'], task,
                                      content['allowed_engines'])
        with self.store.transaction() as db:
            current = self._op(db, operation_id)
            if current['status'] not in ACTIVE or current['method_prepared'] or current['cancel_requested']:
                return current
            old_size = _size(current)
            current['document'] = validate_joint_plan_input(dict(current['document'], method_delivery=snapshot))
            current.update(method_prepared=True, input_sha256=digest(current['document']))
            self._capacity(db, current['project_id'], extra_bytes=_size(current) - old_size)
            self._write_op(db, current)
            return current

    @staticmethod
    def _content_from_document(document):
        return {key: value for key, value in document.items() if key != 'method_delivery'}

    def operation(self, operation_id, owner=None):
        with self.store.transaction() as db:
            op = self._op(db, operation_id, owner)
            if owner is not None:
                self.store._project(db, owner, op['project_id'])
            return op

    def public_operation(self, owner, operation_id):
        return self._public(self.operation(operation_id, owner))

    def active_operations(self):
        with self.store.transaction() as db:
            return [op for row in db.execute('SELECT document FROM joint_plan_operations')
                    if (op := json.loads(row['document']))['status'] in ACTIVE]

    def update_operation(self, operation_id, **fields):
        _require(set(fields) <= {'status', 'error', 'job_id', 'input_ref'})
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
        _require(type(now) in (int, float) and 0 <= now <= 10 ** 12
                 and type(retry_delays) in (tuple, list) and 1 <= len(retry_delays) <= 4
                 and all(type(delay) in (int, float) and 1 <= delay <= 60 for delay in retry_delays))
        with self.store.transaction() as db:
            op = self._op(db, operation_id)
            if op['status'] not in ACTIVE or not (op['cancel_requested'] or now >= op['deadline_at']):
                return None
            attempts = op['cancel_attempts']
            if attempts >= len(retry_delays):
                op.update(status='cancelling', error='cancellation_retry_exhausted')
                self._write_op(db, op)
                return None
            if now < op['next_cancel_at']:
                return None
            op.update(status='cancelling', error=None, cancel_attempts=attempts + 1,
                      next_cancel_at=now + retry_delays[attempts])
            self._write_op(db, op)
            return op

    def cancel(self, owner, operation_id):
        with self.store.transaction() as db:
            op = self._op(db, operation_id, owner)
            self.store._project(db, owner, op['project_id'])
            if op['status'] in ACTIVE:
                op.update(cancel_requested=True, status='cancelling')
                self._write_op(db, op)
            return self._public(op)

    def complete(self, operation_id, result):
        with self.store.transaction() as db:
            op = self._op(db, operation_id)
            if op['status'] not in ACTIVE:
                return self._public(op)
            old_size = _size(op)
            project = self.store._project(db, op['owner'], op['project_id'])
            if op['cancel_requested']:
                op.update(status='completed', error='cancel_arrived_after_completion')
            elif time.time() >= op['deadline_at']:
                op.update(status='timed_out', error='joint_plan_result_after_deadline')
            else:
                result = validate_joint_plan_result(result, op['input_sha256'], op['document'])
                version = dict(id=str(uuid4()), project_id=op['project_id'], operation_id=op['id'],
                    created_at=time.time(), input_sha256=op['input_sha256'],
                    input_binding_sha256=op['input_binding_sha256'], result=result)
                _require(_size(version) <= OUTPUT_RESERVE_BYTES - 8192, 'joint_plan_candidate_too_large', 413)
                op.update(status='completed', error=None, version_id=version['id'])
                self._capacity(db, project['id'], extra_bytes=_size(version) + _size(op) - old_size, active_delta=-1)
                db.execute('INSERT INTO joint_plan_versions VALUES (?, ?, ?, ?, ?)',
                           (version['id'], op['owner'], project['id'], op['id'], _json(version)))
            self._write_op(db, op)
            self.store._save(db, project)
            return self._public(op)

    @staticmethod
    def _require_idle(db, project_id):
        # Optional subsystems may not have initialized their tables yet. Check
        # all scenes, including visual operations outside the currently viewed
        # scene; the adoption is one project-wide write.
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, code in [('operations', 'operation_in_progress'), ('motion_operations', 'motion_operation_in_progress'),
                            ('audio_operations', 'audio_operation_in_progress'), ('video_renders', 'render_in_progress')]:
            if table in tables:
                for row in db.execute('SELECT document FROM ' + table + ' WHERE project_id=?', (project_id,)):
                    _require(json.loads(row[0])['status'] not in ACTIVE, code, 409)

    def adopt(self, owner, project_id, request):
        _require(type(request) is dict and set(request) ==
                 {'expected_revision', 'version_id', 'expected_head_version_id'}
                 and type(request['expected_revision']) is int and request['expected_revision'] >= 1
                 and (request['expected_head_version_id'] is None or
                      type(request['expected_head_version_id']) is str))
        with self.store.transaction() as db:
            project = self.store._project(db, owner, project_id)
            self.store._revision(project, request['expected_revision'])
            version = self._version(db, owner, request['version_id'])
            _require(version['project_id'] == project_id, 'not_found', 404)
            head = self._head(db, owner, project_id)
            _require(head['version_id'] == request['expected_head_version_id'], 'joint_plan_head_conflict', 409)
            _require(not self._stale(version, self._binding(project), head), 'joint_plan_stale', 409)
            self._require_idle(db, project_id)
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ('joint_plan_operations', 'explanation_operations'):
                if table in tables:
                    for row in db.execute('SELECT document FROM ' + table + ' WHERE project_id=?', (project_id,)):
                        _require(json.loads(row[0])['status'] not in ACTIVE, 'joint_plan_operation_in_progress', 409)
            if 'video_batches' in tables:
                for row in db.execute('SELECT document FROM video_batches WHERE project_id=?', (project_id,)):
                    _require(json.loads(row[0])['status'] in {'completed', 'partial_failed', 'cancelled'},
                             'batch_project_busy', 409)
            if version['id'] != head['version_id']:
                result = version['result']
                prior = dict(scenes=project['scenes'], joint_plan=project.get('joint_plan'),
                             revision=project['revision'], adopted_at=time.time())
                self._capacity(db, project_id, extra_bytes=_size(prior))
                db.execute('INSERT INTO joint_plan_adoptions VALUES (?, ?, ?, ?, ?)',
                           (str(uuid4()), owner, project_id, version['id'], _json(prior)))
                scenes = []
                for proposed in result['scenes']:
                    scene = dict(id=str(uuid4()), production_mode=project.get('production_mode', 'static'),
                        format='html', head_version_id=None, versions=[], head_audio_version_id=None,
                        **{key: proposed[key] for key in
                           ('title', 'narration', 'visual_description', 'duration_seconds')},
                        speech_settings=speech_options(1.0, proposed['pronunciation']))
                    scene['joint_plan'] = dict(version_id=version['id'],
                        **{key: proposed[key] for key in ('segment_id', 'source_scene_ids', 'key_points',
                            'expression', 'visual_steps', 'delivery_note', 'source_quotes')},
                        visual_style=result['visual_style'], continuity=result['continuity'],
                        visual_binding_sha256=scene_visual_binding(scene))
                    visual_generation_description(scene, enforce_limit=True)
                    scenes.append(scene)
                project['scenes'] = scenes
                project['joint_plan'] = dict(version_id=version['id'],
                    **{key: result[key] for key in ('summary', 'understanding_goal', 'visual_style',
                                                  'continuity', 'method_decision')})
                applied_binding = self._binding(project)
                db.execute('INSERT INTO joint_plan_heads VALUES (?, ?, ?, ?) ON CONFLICT(owner,project_id) '
                           'DO UPDATE SET version_id=excluded.version_id,applied_binding_sha256=excluded.applied_binding_sha256',
                           (owner, project_id, version['id'], applied_binding))
                self.store._save(db, project)
        return self.store.project(owner, project_id)
