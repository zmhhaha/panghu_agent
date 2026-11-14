"""Durable owner-bound Manim resources for native whole-film module authors.

The existing source-only recipe role and private Cairo executor remain the
execution boundary. Reserving or reading an operation never invokes either.
An independent coordinator directory prevents formal work from becoming a lab
commission or consuming a developer experiment's budget.
"""
from contextlib import closing
from copy import copy, deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time

from agent_service.contracts import ServiceError, canonical_bytes, digest
from .animation_creation_service import _run_lock, _uuid
from .manim_method_coordinator import authoring_request
from .manim_method_resources import PRODUCER_REQUIREMENT, helper_assets
from .manim_resource import require

SCHEMA = 'video.manim-film-resource-operation.v1'
REQUEST_FIELDS = frozenset({'operation_id', 'project_id', 'scene_id', 'film_sha256',
    'goal', 'guidance', 'duration_seconds', 'scene_start_frame', 'scene_end_frame',
    'resource_authoring', 'deadline_at'})
TERMINAL = frozenset({'completed', 'failed', 'cancelled'})
_IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}\Z')
_OWNER = re.compile(r'[A-Za-z0-9][A-Za-z0-9._@:/+-]{0,199}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')


class _AdmissionBoundary:
    """Recheck persisted stop/gate state immediately before external claims."""
    def __init__(self, peer, reason):
        self.peer, self.reason, self.blocked = peer, reason, None

    def __getattr__(self, name):
        return getattr(self.peer, name)

    def _check(self):
        reason = self.reason()
        if reason:
            self.blocked = reason
            raise ServiceError(reason, 409 if reason == 'film_manim_cancel_requested' else 503)

    def prepare_submission(self, *args, **kwargs):
        self._check()
        admit = self.peer.prepare_submission(*args, **kwargs)
        def checked():
            self._check()
            return admit()
        return checked

    def execute(self, *args, **kwargs):
        self._check()
        return self.peer.execute(*args, **kwargs)


def validate_request(value):
    prepared = type(value) is dict and value.get('schema') == 'video.prepared-manim-resource-request.v1'
    if prepared:
        require(set(value) == {'schema', 'operation_id', 'project_id', 'node_id', 'goal', 'guidance',
            'duration_seconds', 'resource_authoring', 'deadline_at'}, 'film_manim_request_invalid')
        require(_uuid(value['operation_id']) and type(value['project_id']) is str
            and _IDENTIFIER.fullmatch(value['project_id']) and type(value['node_id']) is str
            and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}', value['node_id']),
            'film_manim_scope_invalid')
        require(type(value['duration_seconds']) in (int, float) and math.isfinite(value['duration_seconds'])
            and 0 < value['duration_seconds'] <= 60, 'film_manim_clock_invalid')
        options = authoring_request(value['resource_authoring'], value['duration_seconds'])
        require(options['global_start_frame'] == 0
            and options['frame_count'] == round(value['duration_seconds'] * 24),
            'film_manim_local_clock_invalid')
        legacy = dict(value)
        legacy.pop('schema'); legacy.pop('node_id')
        legacy.update(scene_id='prepared-resource', film_sha256='0' * 64,
            scene_start_frame=0, scene_end_frame=options['frame_count'])
        validate_request(legacy)
        return deepcopy(value)
    require(type(value) is dict and set(value) == REQUEST_FIELDS,
        'film_manim_request_invalid')
    require(_uuid(value['operation_id']) and all(type(value[key]) is str
        and _IDENTIFIER.fullmatch(value[key]) for key in ('project_id', 'scene_id'))
        and type(value['film_sha256']) is str and _SHA.fullmatch(value['film_sha256']),
        'film_manim_scope_invalid')
    for name in ('goal', 'guidance'):
        text = value[name]
        require(type(text) is str and len(text) <= 16000
            and (name == 'guidance' or bool(text.strip()))
            and not any(ord(c) < 32 and c not in '\t\r\n' or 0xD800 <= ord(c) <= 0xDFFF for c in text),
            'film_manim_instruction_invalid')
    duration = value['duration_seconds']
    require(type(duration) in (int, float) and math.isfinite(duration)
        and 0 < duration <= 60 and abs(duration * 24 - round(duration * 24)) < 1e-6,
        'film_manim_clock_invalid')
    start, end = value['scene_start_frame'], value['scene_end_frame']
    require(type(start) is int and type(end) is int and 0 <= start < end <= round(duration * 24),
        'film_manim_clock_invalid')
    options = authoring_request(value['resource_authoring'], duration)
    require(start <= options['global_start_frame']
        and options['global_start_frame'] + options['frame_count'] <= end,
        'film_manim_resource_outside_scene')
    deadline = value['deadline_at']
    require(type(deadline) in (int, float) and math.isfinite(deadline) and deadline > 0,
        'film_manim_deadline_invalid')
    require(len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode()) <= 128 * 1024,
        'film_manim_request_too_large', 413)
    # Keep request identity exact; defaults are applied only inside the frozen
    # coordinator input, never on a replay against a later installed version.
    return deepcopy(value)


class ManimFilmResourceService:
    def __init__(self, root, *, coordinator, enabled=False, candidate=False, clock=time.time):
        require(type(enabled) is bool and type(candidate) is bool, 'film_manim_configuration_invalid')
        self.root = Path(root).absolute()
        require(not any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)()
            for p in (self.root, *self.root.parents)), 'film_manim_path_invalid')
        self.root.mkdir(parents=True, exist_ok=True)
        self.locks = self.root / 'locks'
        self.locks.mkdir(exist_ok=True)
        self.path = self.root / 'film-manim.sqlite3'
        require(not self.path.is_symlink() and not self.locks.is_symlink(), 'film_manim_path_invalid')
        self.coordinator, self.enabled, self.candidate, self.clock = coordinator, enabled, candidate, clock
        self.admission = None
        if not self.path.exists():
            with closing(self.coordinator._connect()) as db:
                require(db.execute('SELECT count(*) FROM resources').fetchone()[0] == 0,
                    'film_manim_ledger_missing', 409)
        with closing(self._db()) as db:
            db.execute('CREATE TABLE IF NOT EXISTS film_resources ('
                'owner TEXT NOT NULL,id TEXT NOT NULL,request_sha256 TEXT NOT NULL,'
                'document BLOB NOT NULL,sha256 TEXT NOT NULL,PRIMARY KEY(owner,id))')

    def _db(self):
        require(not self.path.is_symlink(), 'film_manim_path_invalid')
        db = sqlite3.connect(self.path, isolation_level=None, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        return db

    @staticmethod
    def _identity(owner, operation_id):
        require(type(owner) is str and _OWNER.fullmatch(owner) and _uuid(operation_id),
            'film_manim_not_found', 404)

    def _read(self, owner, operation_id):
        self._identity(owner, operation_id)
        with closing(self._db()) as db:
            row = db.execute('SELECT * FROM film_resources WHERE owner=? AND id=?',
                (owner, operation_id)).fetchone()
        require(row is not None, 'film_manim_not_found', 404)
        value = json.loads(row['document'])
        require(digest(value) == row['sha256'] and value['owner'] == owner
            and value['id'] == operation_id and digest(value['request']) == row['request_sha256']
            and value['request']['operation_id'] == operation_id,
            'film_manim_record_changed', 409)
        validate_request(value['request'])
        return value

    def capabilities(self, owner=None):
        return dict(schema='video.manim-film-resource-capabilities.v1', enabled=self.enabled,
            status='admitted' if self.enabled else 'prepared_not_admitted',
            producer=deepcopy(PRODUCER_REQUIREMENT) if self.enabled else {},
            resource_authoring=deepcopy(PRODUCER_REQUIREMENT) if self.enabled else {},
            helpers={name: hashlib.sha256(raw).hexdigest() for name, raw in helper_assets().items()},
            admission_sha256=self.admission['sha256'] if self.enabled and self.admission else None,
            resource_image=self.coordinator.executor.image, max_frames=60,
            latex_available=False, independent_video_concatenation=False)

    def start(self, owner, request):
        request = validate_request(request)
        self._identity(owner, request['operation_id'])
        checksum = digest(request)
        with closing(self._db()) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT request_sha256 FROM film_resources WHERE owner=? AND id=?',
                (owner, request['operation_id'])).fetchone()
            if row is not None:
                require(row['request_sha256'] == checksum, 'film_manim_idempotency_conflict', 409)
                db.commit()
                return self.get(owner, request['operation_id'])
            require(self.enabled or self.candidate, 'film_manim_not_admitted', 503)
            require(0 < request['deadline_at'] - self.clock()
                <= (86400 if request.get('schema') == 'video.prepared-manim-resource-request.v1' else 900),
                'film_manim_deadline_invalid')
            require(db.execute('SELECT count(*) FROM film_resources').fetchone()[0] < 4096,
                'film_manim_operation_limit', 413)
            value = dict(schema=SCHEMA, owner=owner, id=request['operation_id'], request=request,
                cancel_requested=False, cancelled_at=None, error=None, terminal=None,
                created_at=self.clock(), finished_at=None)
            db.execute('INSERT INTO film_resources VALUES(?,?,?,?,?)',
                (owner, value['id'], checksum, canonical_bytes(value), digest(value)))
            db.commit()
        return self.get(owner, request['operation_id'])

    def _update(self, owner, operation_id, **changes):
        with closing(self._db()) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT document,sha256 FROM film_resources WHERE owner=? AND id=?',
                (owner, operation_id)).fetchone()
            require(row is not None, 'film_manim_not_found', 404)
            value = json.loads(row['document'])
            require(digest(value) == row['sha256'], 'film_manim_record_changed', 409)
            if changes.get('cancel_requested') and value['terminal'] in TERMINAL:
                db.commit()
                return
            if changes.get('terminal') == 'completed' and value['cancel_requested']:
                changes = dict(changes, terminal='cancelled')
            value.update(changes)
            db.execute('UPDATE film_resources SET document=?,sha256=? WHERE owner=? AND id=?',
                (canonical_bytes(value), digest(value), owner, operation_id))
            db.commit()

    def get(self, owner, operation_id):
        value = self._read(owner, operation_id)
        view = self.coordinator.view(owner, 'film-' + operation_id)
        status = value['terminal'] or view['status']
        if status == 'pending':
            status = 'queued'
        descriptor = (self.coordinator.context(owner, 'film-' + operation_id)
            if status == 'completed' and not value['cancel_requested'] else None)
        return dict(schema=SCHEMA, operation_id=operation_id, project_id=value['request']['project_id'],
            **({key: value['request'][key] for key in ('node_id', 'scene_id', 'film_sha256') if key in value['request']}),
            request_sha256=digest(value['request']), status=status,
            error=value['error'] or view.get('error'), cancel_requested=value['cancel_requested'],
            stop_proven=status in TERMINAL, descriptor=descriptor,
            resource_image=self.coordinator.executor.image,
            budget=view['budget'], usage=view['usage'])

    def cancel(self, owner, operation_id):
        self._read(owner, operation_id)
        if self.get(owner, operation_id)['status'] not in TERMINAL:
            self._update(owner, operation_id, cancel_requested=True, cancelled_at=self.clock())
            original = self.coordinator._get(owner, 'film-' + operation_id)
            # Persist cancellation in the native executor while its bounded
            # attachment loop may still be active in the independent pump.
            if original is not None and 'plan' in original:
                try:
                    self.coordinator.executor.cancel(**original['scope'])
                except ServiceError as error:
                    if error.status != 404:
                        self._update(owner, operation_id, error=error.code)
        return self.get(owner, operation_id)

    def cancel_reservation(self, owner, request):
        """Cancel an exact intent even if its original POST never arrived.

        The cancelled reservation wins atomically against a delayed start and
        is never eligible for the producer pump. Existing work keeps its own
        stop lifecycle: a cancellation request is not proof that it stopped.
        """
        request = validate_request(request)
        operation_id = request['operation_id']
        self._identity(owner, operation_id)
        checksum = digest(request)
        with closing(self._db()) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM film_resources WHERE owner=? AND id=?',
                (owner, operation_id)).fetchone()
            if row is None:
                # An authenticated caller cannot cancel another owner's
                # existing identity by creating a matching tombstone.
                require(db.execute('SELECT 1 FROM film_resources WHERE id=?',
                    (operation_id,)).fetchone() is None, 'film_manim_not_found', 404)
                # A missing reservation with existing producer custody is an
                # integrity failure, never evidence of unstarted work.
                require(self.coordinator._get(owner, 'film-' + operation_id) is None,
                    'film_manim_ledger_missing', 409)
                require(db.execute('SELECT count(*) FROM film_resources').fetchone()[0] < 4096,
                    'film_manim_operation_limit', 413)
                now = self.clock()
                value = dict(schema=SCHEMA, owner=owner, id=operation_id, request=request,
                    cancel_requested=True, cancelled_at=now, error=None, terminal='cancelled',
                    created_at=now, finished_at=now)
                db.execute('INSERT INTO film_resources VALUES(?,?,?,?,?)',
                    (owner, operation_id, checksum, canonical_bytes(value), digest(value)))
            else:
                value = json.loads(row['document'])
                require(digest(value) == row['sha256'] and value['owner'] == owner
                    and value['id'] == operation_id and digest(value['request']) == row['request_sha256'],
                    'film_manim_record_changed', 409)
                require(row['request_sha256'] == checksum, 'film_manim_idempotency_conflict', 409)
                if value['terminal'] not in TERMINAL:
                    value.update(cancel_requested=True, cancelled_at=self.clock())
                    db.execute('UPDATE film_resources SET document=?,sha256=? WHERE owner=? AND id=?',
                        (canonical_bytes(value), digest(value), owner, operation_id))
            db.commit()
        # This also propagates cancellation to an existing native executor.
        # New tombstones return directly through the terminal branch.
        return self.cancel(owner, operation_id)

    def envelope(self, owner, operation_id):
        view = self.get(owner, operation_id)
        require(view['status'] == 'completed' and not view['cancel_requested'], 'film_manim_resource_not_ready', 409)
        # Reopening exact coordinator custody validates every delivered resource
        # before releasing the immutable envelope over the private connection.
        self.coordinator.resolve(view['descriptor'])
        value = self.coordinator._get(owner, 'film-' + operation_id)
        return deepcopy(value['envelope'])

    def pending(self, limit=100):
        require(type(limit) is int and 1 <= limit <= 100, 'film_manim_pending_limit_invalid')
        with closing(self._db()) as db:
            rows = db.execute('SELECT owner,id,document FROM film_resources ORDER BY rowid').fetchall()
        result = []
        for row in rows:
            value = json.loads(row['document'])
            if value.get('terminal') not in TERMINAL:
                result.append(dict(owner=row['owner'], id=row['id']))
                if len(result) == limit:
                    break
        return result

    def advance(self, owner, operation_id):
        self._read(owner, operation_id)
        path = self.locks / (digest(dict(owner=owner, operation_id=operation_id)) + '.lock')
        with _run_lock(path) as acquired:
            if not acquired:
                return
            value = self._read(owner, operation_id)
            if value['terminal'] in TERMINAL:
                return
            request = value['request']
            cancelled = value['cancel_requested'] or self.clock() >= request['deadline_at']
            run = dict(id='film-' + operation_id, deadline_at=request['deadline_at'], frozen=dict(
                duration_seconds=request['duration_seconds'], goal=request['goal'],
                feedback=request['guidance'], resource_authoring=deepcopy(request['resource_authoring'])))
            def admission_reason():
                current = self._read(owner, operation_id)
                if current['cancel_requested'] or self.clock() >= request['deadline_at']:
                    return 'film_manim_cancel_requested'
                if not (self.enabled or self.candidate):
                    return 'film_manim_not_admitted'
                return None
            coordinator = copy(self.coordinator)
            coordinator.gateway = _AdmissionBoundary(coordinator.gateway, admission_reason)
            coordinator.executor = _AdmissionBoundary(coordinator.executor, admission_reason)
            try:
                state = coordinator.advance(owner, run, cancelled=cancelled)
            except ServiceError as error:
                # Unknown external admission/collection remains on the same
                # coordinator identity, never a new recipe or container.
                self._update(owner, operation_id, error=error.code)
                state = None
            blocked = coordinator.gateway.blocked or coordinator.executor.blocked
            if blocked == 'film_manim_cancel_requested':
                # The wrapper knows it never invoked the paid submission or
                # native execution. The coordinator's conservative unknown
                # marker may therefore be closed without inventing a job.
                original = coordinator._get(owner, run['id'])
                if original is not None:
                    stopped = deepcopy(original)
                    stopped.update(state='cancelled', error='stopped_before_external_admission')
                    coordinator._save(stopped, original)
                state = 'cancelled'
            elif blocked:
                self._update(owner, operation_id, error=blocked)
                return
            if state is None:
                return
            latest = self._read(owner, operation_id)
            if state in TERMINAL:
                # A concurrent cancel never adopts a just-finished resource.
                if state == 'completed' and (latest['cancel_requested'] or self.clock() >= request['deadline_at']):
                    state = 'cancelled'
                self._update(owner, operation_id, terminal=state, finished_at=self.clock(), error=None)
            elif latest['error']:
                self._update(owner, operation_id, error=None)
