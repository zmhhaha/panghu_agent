"""Optional motion candidates beside the existing static Store.

These tables never reinterpret static source/format, rewrite a static head, or
adopt Agent output. Semantic dependencies intentionally exclude voice and audio.
"""
from __future__ import annotations

import json
import time
from uuid import uuid4

from agent_service.contracts import ServiceError, digest
from agent_service.profiles.video_motion import validate_motion_input, validate_motion_result
from .motion import make_beats
from .explanation_store import visual_generation_description


ACTIVE = frozenset({'queued', 'submitting', 'running', 'cancelling'})
TERMINAL = frozenset({'completed', 'failed', 'needs_input', 'cancelled', 'timed_out'})
MAX_CANDIDATES_PER_SCENE = 64
MAX_PROJECT_OPERATIONS = 256
MAX_PROJECT_JSON_BYTES = 32 * 1024 * 1024
MAX_TOTAL_JSON_BYTES = 128 * 1024 * 1024
# Input beats (256KiB), plan (128KiB), and bounded coordinator metadata fit in
# this reservation. JSON budgets do not claim to measure SQLite/WAL disk bytes.
OUTPUT_RESERVE_BYTES = 512 * 1024


def _require(condition, code='invalid_motion_request', status=422):
    if not condition:
        raise ServiceError(code, status)


def semantic_binding(project, scene):
    """The scene's intended explanation; pronunciations are resolved later."""
    return digest(dict(schema='video.motion-semantic-binding.v1', title=scene['title'],
        narration=scene['narration'], visual_description=visual_generation_description(scene),
        style=project.get('style', '')))


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _size(value):
    return len(_json(value).encode('utf-8'))


class MotionStore:
    def __init__(self, store):
        self.store = store
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
            "('queued','submitting','running','cancelling') THEN 1 ELSE 0 END),0) "
            'FROM motion_operations' + clause, args).fetchone()
        versions = db.execute('SELECT COALESCE(SUM(length(CAST(document AS BLOB))),0) '
                              'FROM motion_versions' + clause, args).fetchone()[0]
        return operations[0] + versions + operations[1] * OUTPUT_RESERVE_BYTES

    def _capacity(self, db, project_id, *, extra_bytes=0, active_delta=0):
        extra = extra_bytes + active_delta * OUTPUT_RESERVE_BYTES
        _require(self._usage(db, project_id) + extra <= MAX_PROJECT_JSON_BYTES
                 and self._usage(db) + extra <= MAX_TOTAL_JSON_BYTES, 'motion_storage_limit', 413)

    @staticmethod
    def _public(op):
        return {k: op.get(k) for k in ('id', 'kind', 'status', 'error', 'job_id', 'version_id')}

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
    def _head(db, owner, project_id, scene_id):
        row = db.execute('SELECT version_id,mode FROM motion_heads WHERE owner=? AND project_id=? AND scene_id=?',
                         (owner, project_id, scene_id)).fetchone()
        return dict(row) if row else dict(version_id=None, mode='static')

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
        return {k: version[k] for k in ('id', 'created_at', 'parent_version_id', 'input_sha256',
                                       'plan_sha256', 'semantic_binding_sha256')} | {
            'title': version['plan']['title'], 'template': version['plan']['template'],
            'stale': version['semantic_binding_sha256'] != binding}

    def project(self, owner, project_id):
        # The existing lock keeps the static and additive views on one revision,
        # without opening nested SQLite transactions.
        with self.store.lock:
            project = self.store.project(owner, project_id)
            with self.store.transaction() as db:
                for scene in project['scenes']:
                    binding = semantic_binding(project, scene)
                    head = self._head(db, owner, project_id, scene['id'])
                    versions = [json.loads(r['document']) for r in db.execute(
                        'SELECT document FROM motion_versions WHERE owner=? AND project_id=? AND scene_id=? ORDER BY rowid',
                        (owner, project_id, scene['id']))]
                    ops = [json.loads(r['document']) for r in db.execute(
                        'SELECT document FROM motion_operations WHERE owner=? AND project_id=? AND scene_id=? ORDER BY rowid',
                        (owner, project_id, scene['id']))]
                    metadata = [self._metadata(v, binding) for v in versions]
                    adopted = next((v for v in metadata if v['id'] == head['version_id']), None)
                    scene['motion'] = dict(mode=head['mode'], head_version_id=head['version_id'],
                        head_stale=bool(head['version_id'] and (adopted is None or adopted['stale'])),
                        semantic_binding_sha256=binding, versions=metadata,
                        operation=self._public(ops[-1]) if ops else None)
            return project

    def version(self, owner, version_id):
        with self.store.transaction() as db:
            version = self._version(db, owner, version_id)
            project = self.store._project(db, owner, version['project_id'])
            scene = self.store._scene(project, version['scene_id'])
            version['stale'] = version['semantic_binding_sha256'] != semantic_binding(project, scene)
            return version

    def start(self, owner, scene_id, request, idem, *, timeout=600):
        _require(type(request) is dict and {'expected_revision'} <= set(request)
                 and set(request) <= {'expected_revision', 'instruction', 'base_version_id'}
                 and type(request['expected_revision']) is int and request['expected_revision'] >= 1)
        _require(type(idem) is str and 1 <= len(idem) <= 128
                 and all(33 <= ord(c) <= 126 for c in idem))
        _require(type(timeout) in (int, float) and 1 <= timeout <= 600)
        project_id = self.store.scene_project(owner, scene_id)
        fingerprint = digest(dict(scene_id=scene_id, request=request, timeout=timeout))
        with self.store.transaction() as db:
            existing = db.execute('SELECT request_hash,document FROM motion_operations WHERE owner=? AND idem=?',
                                  (owner, idem)).fetchone()
            if existing:
                _require(existing['request_hash'] == fingerprint, 'idempotency_conflict', 409)
                return self._public(json.loads(existing['document']))
            project = self.store._project(db, owner, project_id)
            self.store._revision(project, request['expected_revision'])
            scene = self.store._scene(project, scene_id)
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
            binding = semantic_binding(project, scene)
            base_id = request.get('base_version_id')
            base = None
            if base_id is not None:
                _require(type(base_id) is str and 1 <= len(base_id) <= 100)
                base = self._version(db, owner, base_id)
                _require(base['scene_id'] == scene_id and base['project_id'] == project_id, 'not_found', 404)
                _require(base['semantic_binding_sha256'] == binding, 'motion_base_stale', 409)
            document = dict(schema='video.motion-input.v1', title=scene['title'], style=project.get('style', ''),
                visual_description=visual_generation_description(scene), beats=make_beats(scene['narration']),
                instruction=request.get('instruction', ''), base_plan=base['plan'] if base else None)
            validate_motion_input(document)
            now = time.time()
            op = dict(id=str(uuid4()), owner=owner, project_id=project_id, scene_id=scene_id,
                kind='motion-plan', document=document, input_sha256=digest(document),
                semantic_binding_sha256=binding, parent_version_id=base_id, status='queued', error=None,
                job_id=None, input_ref=None, submit_attempted=False, cancel_requested=False,
                cancel_attempted=False, cancel_attempts=0, next_cancel_at=0.0,
                deadline_at=now + timeout, created_at=now, version_id=None)
            self._capacity(db, project_id, extra_bytes=_size(op), active_delta=1)
            db.execute('INSERT INTO motion_operations VALUES (?, ?, ?, ?, ?, ?, ?)',
                (op['id'], owner, project_id, scene_id, idem, fingerprint, _json(op)))
            self.store._save(db, project)
            return self._public(op)

    def operation(self, operation_id, owner=None):
        with self.store.transaction() as db:
            return self._op(db, operation_id, owner)

    def public_operation(self, owner, operation_id):
        return self._public(self.operation(operation_id, owner))

    def active_operations(self):
        with self.store.transaction() as db:
            return [op for r in db.execute('SELECT document FROM motion_operations')
                    if (op := json.loads(r['document']))['status'] in ACTIVE]

    def update_operation(self, operation_id, **fields):
        _require(set(fields) <= {'status', 'error', 'job_id', 'input_ref', 'cancel_attempted'})
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
            validate_motion_result(result, op['input_sha256'], op['document']['beats'])
            old_op_size = _size(op)
            project = self.store._project(db, op['owner'], op['project_id'])
            self.store._scene(project, op['scene_id'])
            if op['cancel_requested']:
                # Upstream completion won the race. Do not misreport a proven
                # cancellation or keep a candidate the user asked to discard.
                op.update(status='completed', error='cancel_arrived_after_completion')
            else:
                version = dict(id=str(uuid4()), project_id=op['project_id'], scene_id=op['scene_id'],
                    operation_id=op['id'], created_at=time.time(), parent_version_id=op['parent_version_id'],
                    input_sha256=op['input_sha256'], semantic_binding_sha256=op['semantic_binding_sha256'],
                    beats=op['document']['beats'], plan=result['plan'], plan_sha256=digest(result['plan']))
                _require(_size(version) <= OUTPUT_RESERVE_BYTES - 8192, 'motion_candidate_too_large', 413)
                op.update(status='completed', error=None, version_id=version['id'])
                self._capacity(db, op['project_id'],
                    extra_bytes=_size(version) + _size(op) - old_op_size, active_delta=-1)
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
            db.execute('INSERT INTO motion_heads VALUES (?, ?, ?, ?, ?) ON CONFLICT(owner,project_id,scene_id) '
                       'DO UPDATE SET mode=excluded.mode', (owner, project_id, scene_id, head['version_id'], mode))
            self.store._save(db, project)
        return self.project(owner, project_id)
