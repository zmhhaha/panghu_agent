"""Project explanation candidates; explicit adoption changes content atomically.

Agent jobs never select media or a voice. Input bindings describe actual content
and speech settings, rather than project revision counters or unrelated jobs.
"""
from __future__ import annotations

import json
import time
from uuid import uuid4

from agent_service.contracts import ServiceError, digest
from agent_service.profiles.video_explanation import (
    validate_explanation_input, validate_explanation_result,
)
from .audio_store import speech_options


ACTIVE = frozenset({'queued', 'submitting', 'running', 'cancelling'})
TERMINAL = frozenset({'completed', 'failed', 'needs_input', 'cancelled', 'timed_out'})
MAX_PROJECT_CANDIDATES = 64
MAX_PROJECT_OPERATIONS = 256
MAX_PROJECT_JSON_BYTES = 32 * 1024 * 1024
MAX_TOTAL_JSON_BYTES = 128 * 1024 * 1024
# Reserve the complete bounded result and coordinator metadata before admission.
# These limits measure stored JSON, not SQLite/WAL disk usage.
OUTPUT_RESERVE_BYTES = 1024 * 1024 + 16384


def _require(value, code='invalid_explanation_request', status=422):
    if not value:
        raise ServiceError(code, status)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _size(value):
    return len(_json(value).encode('utf-8'))


def scene_visual_binding(scene):
    return digest({key: scene[key] for key in
                   ('title', 'narration', 'visual_description', 'duration_seconds', 'format')})


def visual_generation_description(scene, *, enforce_limit=False):
    """Consume adopted visual direction without changing the legacy Agent schema.

    A subsequent manual scene edit stops using that direction. The short key
    points and visual progression are presentation instructions, not subtitles
    or a second copy of the narration.
    """
    description = scene['visual_description']
    joint = scene.get('joint_plan')
    if isinstance(joint, dict) and joint.get('visual_binding_sha256') == scene_visual_binding(scene):
        extra = ('\n\n已采用的整片联合方案：\n表达构思：' + joint['expression']
                 + '\n统一视觉风格：' + joint['visual_style']
                 + '\n跨段衔接意图：' + joint['continuity']
                 + '\n画面关键点：\n' + '\n'.join('- ' + point for point in joint['key_points'])
                 + '\n画面展开顺序：\n' + '\n'.join(f'{i}. {step}' for i, step in enumerate(joint['visual_steps'], 1))
                 + '\n旁白表达提示：' + joint['delivery_note']
                 + '\n让对象和关系随讲解展开，关键词服务于理解，避免铺满旁白字幕。')
        if enforce_limit:
            _require(len(description) + len(extra) <= 10000, 'joint_plan_visual_requires_split')
        return description + extra
    explanation = scene.get('explanation')
    if not isinstance(explanation, dict) or explanation.get('visual_binding_sha256') != scene_visual_binding(scene):
        return description
    strategy = {'flow': '流程推演', 'compare': '对比说明', 'focus': '聚焦解释'}[explanation['visual_strategy']]
    extra = ('\n\n已采用的讲解方案：\n表现方式：' + strategy
             + '\n画面关键点：\n' + '\n'.join('- ' + item for item in explanation['key_points'])
             + '\n画面展开顺序：\n' + '\n'.join(f'{i}. {step}' for i, step in enumerate(explanation['visual_steps'], 1))
             + '\n以少量关键词和图形关系解释内容，避免把完整旁白铺满画面。')
    # Truncating the tail can silently discard a required URL, version/date or
    # a containment requirement. Resolve excess input before admitting a task.
    if enforce_limit:
        _require(len(description) + len(extra) <= 10000,
                 'explanation_visual_requires_split')
    return description + extra


class ExplanationStore:
    def __init__(self, store):
        self.store = store
        with store.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS explanation_operations (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, project_id TEXT NOT NULL,
                    idem TEXT NOT NULL, request_hash TEXT NOT NULL, document TEXT NOT NULL,
                    UNIQUE(owner, idem));
                CREATE TABLE IF NOT EXISTS explanation_versions (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, project_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL UNIQUE, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS explanation_heads (
                    owner TEXT NOT NULL, project_id TEXT NOT NULL, version_id TEXT NOT NULL,
                    applied_binding_sha256 TEXT NOT NULL, PRIMARY KEY(owner, project_id));
                CREATE INDEX IF NOT EXISTS explanation_operations_project ON explanation_operations(project_id);
                CREATE INDEX IF NOT EXISTS explanation_versions_project ON explanation_versions(project_id);
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
        return digest(dict(schema='video-explanation-content-binding.v1', **self._content(project)))

    @staticmethod
    def _usage(db, project_id=None):
        clause, args = (' WHERE project_id=?', (project_id,)) if project_id is not None else ('', ())
        op = db.execute('SELECT COALESCE(SUM(length(CAST(document AS BLOB))),0), '
            "COALESCE(SUM(CASE WHEN json_extract(document,'$.status') IN "
            "('queued','submitting','running','cancelling') THEN 1 ELSE 0 END),0) "
            'FROM explanation_operations' + clause, args).fetchone()
        versions = db.execute('SELECT COALESCE(SUM(length(CAST(document AS BLOB))),0) '
                              'FROM explanation_versions' + clause, args).fetchone()[0]
        return op[0] + versions + op[1] * OUTPUT_RESERVE_BYTES

    def _capacity(self, db, project_id, *, extra_bytes=0, active_delta=0):
        extra = extra_bytes + active_delta * OUTPUT_RESERVE_BYTES
        _require(self._usage(db, project_id) + extra <= MAX_PROJECT_JSON_BYTES
                 and self._usage(db) + extra <= MAX_TOTAL_JSON_BYTES, 'explanation_storage_limit', 413)

    @staticmethod
    def _public(op):
        return {key: op.get(key) for key in ('id', 'kind', 'status', 'error', 'job_id', 'version_id', 'created_at')}

    @staticmethod
    def _op(db, operation_id, owner=None):
        row = db.execute('SELECT owner,document FROM explanation_operations WHERE id=?', (operation_id,)).fetchone()
        if row is None or owner is not None and row['owner'] != owner:
            raise ServiceError('not_found', 404)
        return json.loads(row['document'])

    @staticmethod
    def _write_op(db, op):
        db.execute('UPDATE explanation_operations SET document=? WHERE id=?', (_json(op), op['id']))

    @staticmethod
    def _head(db, owner, project_id):
        row = db.execute('SELECT version_id,applied_binding_sha256 FROM explanation_heads '
                         'WHERE owner=? AND project_id=?', (owner, project_id)).fetchone()
        return dict(row) if row else dict(version_id=None, applied_binding_sha256=None)

    @staticmethod
    def _version(db, owner, version_id):
        _require(type(version_id) is str and 1 <= len(version_id) <= 100)
        row = db.execute('SELECT v.document FROM explanation_versions v JOIN projects p ON p.id=v.project_id '
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
                'SELECT document FROM explanation_versions WHERE owner=? AND project_id=? ORDER BY rowid',
                (owner, project_id))]
            for version in versions:
                version['stale'] = self._stale(version, binding, head)
                version['adoptable'] = not version['stale']
            latest = db.execute('SELECT document FROM explanation_operations WHERE owner=? AND project_id=? '
                                'ORDER BY rowid DESC LIMIT 1', (owner, project_id)).fetchone()
            latest_op = json.loads(latest['document']) if latest else None
            adopted = next((v for v in versions if v['id'] == head['version_id']), None)
            return dict(operation=self._public(latest_op) if latest_op else None,
                brief=latest_op['document']['brief'] if latest_op else None,
                instruction=latest_op['document'].get('instruction', '') if latest_op else '',
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
                 and set(request) <= {'expected_revision', 'brief', 'instruction'}
                 and type(request['expected_revision']) is int and request['expected_revision'] >= 1)
        _require(type(idem) is str and 1 <= len(idem) <= 128 and all(33 <= ord(c) <= 126 for c in idem))
        _require(type(timeout) in (int, float) and 1 <= timeout <= 600)
        fingerprint = digest(dict(project_id=project_id, request=request, timeout=timeout))
        with self.store.transaction() as db:
            existing = db.execute('SELECT request_hash,document FROM explanation_operations WHERE owner=? AND idem=?',
                                  (owner, idem)).fetchone()
            if existing:
                _require(existing['request_hash'] == fingerprint, 'idempotency_conflict', 409)
                return self._public(json.loads(existing['document']))
            project = self.store._project(db, owner, project_id)
            self.store._revision(project, request['expected_revision'])
            _require(bool(project['scenes']), 'explanation_requires_scenes', 409)
            active = [op for row in db.execute('SELECT document FROM explanation_operations WHERE owner=?', (owner,))
                      if (op := json.loads(row['document']))['status'] in ACTIVE]
            _require(not any(op['project_id'] == project_id for op in active), 'explanation_operation_in_progress', 409)
            _require(len(active) < 12, 'explanation_queue_limit', 409)
            _require(db.execute('SELECT COUNT(*) FROM explanation_operations WHERE project_id=?', (project_id,))
                     .fetchone()[0] < MAX_PROJECT_OPERATIONS, 'explanation_operation_limit', 409)
            _require(db.execute('SELECT COUNT(*) FROM explanation_versions WHERE project_id=?', (project_id,))
                     .fetchone()[0] < MAX_PROJECT_CANDIDATES, 'explanation_candidate_limit', 409)
            document = dict(schema_version='video-explanation-input.v1', **self._content(project),
                            brief=request['brief'], instruction=request.get('instruction', ''))
            document = validate_explanation_input(document)
            now = time.time()
            op = dict(id=str(uuid4()), owner=owner, project_id=project_id, kind='explanation-plan',
                document=document, input_sha256=digest(document), input_binding_sha256=self._binding(project),
                status='queued', error=None, job_id=None, input_ref=None, submit_attempted=False,
                cancel_requested=False, cancel_attempts=0, next_cancel_at=0.0,
                deadline_at=now + timeout, created_at=now, version_id=None)
            self._capacity(db, project_id, extra_bytes=_size(op), active_delta=1)
            db.execute('INSERT INTO explanation_operations VALUES (?, ?, ?, ?, ?, ?)',
                       (op['id'], owner, project_id, idem, fingerprint, _json(op)))
            self.store._save(db, project)
            return self._public(op)

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
            return [op for row in db.execute('SELECT document FROM explanation_operations')
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
                op.update(status='timed_out', error='explanation_result_after_deadline')
            else:
                result = validate_explanation_result(result, op['input_sha256'], op['document'])
                version = dict(id=str(uuid4()), project_id=op['project_id'], operation_id=op['id'],
                    created_at=time.time(), input_sha256=op['input_sha256'],
                    input_binding_sha256=op['input_binding_sha256'], result=result)
                _require(_size(version) <= OUTPUT_RESERVE_BYTES - 8192, 'explanation_candidate_too_large', 413)
                op.update(status='completed', error=None, version_id=version['id'])
                self._capacity(db, project['id'], extra_bytes=_size(version) + _size(op) - old_size, active_delta=-1)
                db.execute('INSERT INTO explanation_versions VALUES (?, ?, ?, ?, ?)',
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
            _require(head['version_id'] == request['expected_head_version_id'], 'explanation_head_conflict', 409)
            _require(not self._stale(version, self._binding(project), head), 'explanation_plan_stale', 409)
            self._require_idle(db, project_id)
            if version['id'] != head['version_id']:
                for scene, proposed in zip(project['scenes'], version['result']['scenes']):
                    _require(scene['id'] == proposed['scene_id'], 'explanation_scene_order_mismatch', 409)
                    before = digest(dict(binding=scene_visual_binding(scene),
                                         description=visual_generation_description(scene)))
                    scene.update({key: proposed[key] for key in
                                  ('title', 'narration', 'visual_description', 'duration_seconds')})
                    speed = scene.get('speech_settings', {}).get('speed', 1.0)
                    scene['speech_settings'] = speech_options(speed, proposed['pronunciation'])
                    scene['explanation'] = dict(version_id=version['id'], **{key: proposed[key] for key in
                        ('key_points', 'visual_strategy', 'visual_steps', 'delivery_note', 'source_quotes')},
                        visual_binding_sha256=scene_visual_binding(scene))
                    after = digest(dict(binding=scene_visual_binding(scene),
                                        description=visual_generation_description(scene)))
                    if before != after:
                        # Keep every candidate and old approval in history, but
                        # require an explicit new visual before export.
                        scene['head_version_id'] = None
                applied_binding = self._binding(project)
                db.execute('INSERT INTO explanation_heads VALUES (?, ?, ?, ?) ON CONFLICT(owner,project_id) '
                           'DO UPDATE SET version_id=excluded.version_id,applied_binding_sha256=excluded.applied_binding_sha256',
                           (owner, project_id, version['id'], applied_binding))
                self.store._save(db, project)
        return self.store.project(owner, project_id)
