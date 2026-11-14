"""Durable formal-film refinement allowances, independent of source hashes.

This is an admission ledger, not a production orchestrator or model client.
One stable sequence owns one automatic round and two explicitly requested user
rounds. Requirement batches retain that sequence and freeze their own budget.
Reservation consumes an allowance permanently, even before submission. There
is deliberately no release, refund, reset, adoption or laboratory API.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import re
import sqlite3
import time
from uuid import uuid4

from agent_service.contracts import ServiceError, digest


TERMINAL = frozenset({'completed', 'failed', 'cancelled'})
ACTIVE = frozenset({'reserved', 'submitting', 'submission_unknown', 'submitted', 'running'})
_SHA = re.compile(r'^[0-9a-f]{64}$')
_WORK_TABLES = ('production_sequence_rounds', 'production_requirement_batches')
_TABLES = frozenset({'production_sequences', 'production_sequence_runs',
    'production_sequence_sources', 'production_sequence_requests',
    'production_sequence_events', 'sqlite_sequence', *_WORK_TABLES})


def require(value, code='invalid_production_sequence_request', status=422):
    if not value:
        raise ServiceError(code, status)


def text(value, limit=200):
    require(type(value) is str and 1 <= len(value) <= limit and value.strip()
        and not any(ord(char) < 32 for char in value))
    return value


def prose(value, limit):
    require(type(value) is str and 1 <= len(value) <= limit and value.strip()
        and not any(ord(char) < 32 and char not in '\n\r\t' for char in value))
    return value


def sha(value):
    require(type(value) is str and _SHA.fullmatch(value) is not None)
    return value


def integer(value, low=1, high=2 ** 53 - 1):
    require(type(value) is int and low <= value <= high)
    return value


def packed(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
            ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ServiceError('invalid_production_sequence_request', 422) from None
    require(len(raw.encode('utf-8')) <= 512 * 1024, 'production_sequence_record_too_large', 413)
    return raw


def source_ref(value):
    require(type(value) is dict and set(value) == {'run_id', 'version_id', 'sha256'})
    return dict(run_id=text(value['run_id']), version_id=text(value['version_id']), sha256=sha(value['sha256']))


def user_request(value, source):
    require(type(value) is dict and set(value) == {'request_id', 'viewed_version_id', 'instruction'})
    require(value['viewed_version_id'] == source['version_id'], 'production_sequence_viewed_version_mismatch', 409)
    return dict(request_id=text(value['request_id']), viewed_version_id=text(value['viewed_version_id']),
        instruction=prose(value['instruction'], 16000))


def requirement_items(value):
    require(type(value) is list and 1 <= len(value) <= 64)
    rows = []
    for item in value:
        require(type(item) is dict and set(item) == {'scope', 'problem', 'expected'})
        scope = item['scope']
        require(type(scope) is dict)
        if scope.get('kind') == 'whole_film':
            require(set(scope) == {'kind'})
        else:
            require(set(scope) == {'kind', 'start_seconds', 'end_seconds'} and scope['kind'] == 'time_range')
            start, end = scope['start_seconds'], scope['end_seconds']
            require(type(start) in (int, float) and type(end) in (int, float)
                and 0 <= start < end <= 86400)
        rows.append(dict(scope=deepcopy(scope), problem=prose(item['problem'], 8000),
            expected=prose(item['expected'], 8000)))
    packed(rows)
    return rows


def requirement_budget(value):
    require(type(value) is dict and set(value) == {'max_agent_tasks', 'max_renders'})
    return dict(max_agent_tasks=integer(value['max_agent_tasks'], high=64),
        max_renders=integer(value['max_renders'], high=32))


class ProductionSequenceStore:
    """An isolated SQLite ledger with owner-scoped reads and atomic CAS writes.

    All identifiers returned by this class are local business identities. An
    execution_ref identifies the caller's *round/batch coordinator*, not each
    individual model call. Child submission and budget enforcement remain the
    integrating orchestrator's responsibility. Stop-proof and result digests
    bind evidence verified by that caller; this ledger never verifies media.
    """

    def __init__(self, path):
        self.path = str(Path(path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        db = self._connect()
        try:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            require(tables <= _TABLES, 'production_sequence_database_not_isolated', 409)
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS production_sequences (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, project_id TEXT NOT NULL,
                    origin_run_id TEXT NOT NULL, document TEXT NOT NULL, UNIQUE(owner, origin_run_id));
                CREATE TABLE IF NOT EXISTS production_sequence_runs (
                    owner TEXT NOT NULL, run_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    sequence_id TEXT NOT NULL REFERENCES production_sequences(id), PRIMARY KEY(owner, run_id));
                CREATE TABLE IF NOT EXISTS production_sequence_sources (
                    owner TEXT NOT NULL, version_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    sequence_id TEXT NOT NULL REFERENCES production_sequences(id), document TEXT NOT NULL,
                    PRIMARY KEY(owner, version_id));
                CREATE TABLE IF NOT EXISTS production_sequence_requests (
                    owner TEXT NOT NULL, idem TEXT NOT NULL, request_sha256 TEXT NOT NULL,
                    target_kind TEXT NOT NULL, target_id TEXT NOT NULL, PRIMARY KEY(owner, idem));
                CREATE TABLE IF NOT EXISTS production_sequence_rounds (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, project_id TEXT NOT NULL,
                    sequence_id TEXT NOT NULL REFERENCES production_sequences(id),
                    kind TEXT NOT NULL, round_index INTEGER NOT NULL, status TEXT NOT NULL,
                    user_request_id TEXT, document TEXT NOT NULL,
                    UNIQUE(sequence_id, kind, round_index), UNIQUE(sequence_id, user_request_id));
                CREATE TABLE IF NOT EXISTS production_requirement_batches (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, project_id TEXT NOT NULL,
                    sequence_id TEXT NOT NULL REFERENCES production_sequences(id), status TEXT NOT NULL,
                    document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS production_sequence_events (
                    position INTEGER PRIMARY KEY AUTOINCREMENT,
                    sequence_id TEXT NOT NULL REFERENCES production_sequences(id),
                    document TEXT NOT NULL);
            ''')
        finally:
            db.close()

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    @contextmanager
    def _transaction(self):
        db = self._connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _sequence(db, owner, project_id, sequence_id):
        row = db.execute('SELECT document FROM production_sequences WHERE id=? AND owner=? AND project_id=?',
            (sequence_id, owner, project_id)).fetchone()
        require(row is not None, 'production_sequence_not_found', 404)
        return json.loads(row[0])

    @staticmethod
    def _source(db, owner, project_id, sequence_id, version_id):
        row = db.execute('SELECT document FROM production_sequence_sources '
            'WHERE owner=? AND project_id=? AND sequence_id=? AND version_id=?',
            (owner, project_id, sequence_id, version_id)).fetchone()
        require(row is not None, 'production_sequence_source_not_found', 404)
        return json.loads(row[0])

    @staticmethod
    def _work(db, owner, project_id, operation_id):
        for table in _WORK_TABLES:
            row = db.execute('SELECT document FROM ' + table + ' WHERE id=? AND owner=? AND project_id=?',
                (operation_id, owner, project_id)).fetchone()
            if row:
                return table, json.loads(row[0])
        raise ServiceError('production_sequence_operation_not_found', 404)

    @staticmethod
    def _cas(document, expected_revision):
        integer(expected_revision)
        require(document['revision'] == expected_revision, 'production_sequence_revision_conflict', 409)

    @staticmethod
    def _event(db, sequence_id, event, **detail):
        db.execute('INSERT INTO production_sequence_events(sequence_id,document) VALUES (?,?)',
            (sequence_id, packed(dict(event=event, at=time.time(), **detail))))

    @classmethod
    def _bump_sequence(cls, db, sequence):
        sequence['revision'] += 1
        db.execute('UPDATE production_sequences SET document=? WHERE id=?', (packed(sequence), sequence['id']))

    @staticmethod
    def _request_hash(action, project_id, request):
        # owner scopes the idempotency namespace in SQL; action prevents a key
        # from being reused for a different API with coincidentally equal input.
        return digest(dict(action=action, project_id=project_id, request=request))

    def _replay(self, db, owner, project_id, idem, request_sha256):
        text(owner)
        text(project_id)
        text(idem)
        row = db.execute('SELECT * FROM production_sequence_requests WHERE owner=? AND idem=?',
            (owner, idem)).fetchone()
        if row is None:
            return None
        require(row['request_sha256'] == request_sha256, 'production_sequence_idempotency_conflict', 409)
        if row['target_kind'] == 'sequence':
            return self._sequence(db, owner, project_id, row['target_id'])
        if row['target_kind'] == 'source':
            source = db.execute('SELECT document FROM production_sequence_sources WHERE owner=? AND version_id=?',
                (owner, row['target_id'])).fetchone()
            require(source is not None, 'production_sequence_source_not_found', 404)
            return json.loads(source[0])
        return self._work(db, owner, project_id, row['target_id'])[1]

    @staticmethod
    def _remember(db, owner, idem, request_sha256, target_kind, target_id):
        db.execute('INSERT INTO production_sequence_requests VALUES (?,?,?,?,?)',
            (owner, idem, request_sha256, target_kind, target_id))

    @staticmethod
    def _register_source(db, owner, project_id, sequence_id, source):
        prior = db.execute('SELECT * FROM production_sequence_sources WHERE owner=? AND version_id=?',
            (owner, source['version_id'])).fetchone()
        if prior is not None:
            require(prior['project_id'] == project_id and prior['sequence_id'] == sequence_id
                and json.loads(prior['document']) == source, 'production_sequence_source_conflict', 409)
            return False
        run = db.execute('SELECT * FROM production_sequence_runs WHERE owner=? AND run_id=?',
            (owner, source['run_id'])).fetchone()
        require(run is None or run['project_id'] == project_id and run['sequence_id'] == sequence_id,
            'production_sequence_run_conflict', 409)
        if run is None:
            db.execute('INSERT INTO production_sequence_runs VALUES (?,?,?,?)',
                (owner, source['run_id'], project_id, sequence_id))
        db.execute('INSERT INTO production_sequence_sources VALUES (?,?,?,?,?)',
            (owner, source['version_id'], project_id, sequence_id, packed(source)))
        return True

    @staticmethod
    def _idle(db, sequence_id):
        for table in _WORK_TABLES:
            require(db.execute('SELECT 1 FROM ' + table + ' WHERE sequence_id=? '
                "AND status NOT IN ('completed','failed','cancelled') LIMIT 1", (sequence_id,)).fetchone() is None,
                'production_sequence_work_in_progress', 409)

    def create(self, owner, project_id, source, idem):
        """Create once for an original run; a source digest is never identity."""
        source = source_ref(source)
        fingerprint = self._request_hash('create', project_id, source)
        with self._transaction() as db:
            if replay := self._replay(db, owner, project_id, idem, fingerprint):
                return replay
            prior = db.execute('SELECT document FROM production_sequences WHERE owner=? AND origin_run_id=?',
                (owner, source['run_id'])).fetchone()
            if prior:
                sequence = json.loads(prior[0])
                require(sequence['project_id'] == project_id and sequence['origin_source'] == source,
                    'production_sequence_origin_conflict', 409)
            else:
                # An explicitly bound derivative run must not become a new
                # origin. Copy/save-as lineage must be supplied by the caller.
                require(db.execute('SELECT 1 FROM production_sequence_runs WHERE owner=? AND run_id=?',
                    (owner, source['run_id'])).fetchone() is None, 'production_sequence_run_conflict', 409)
                sequence = dict(schema='video.production-sequence.v1', id=str(uuid4()), owner=owner,
                    project_id=project_id, origin_run_id=source['run_id'], origin_source=source,
                    revision=1, limits=dict(automatic=1, user_requested=2), created_at=time.time())
                db.execute('INSERT INTO production_sequences VALUES (?,?,?,?,?)',
                    (sequence['id'], owner, project_id, source['run_id'], packed(sequence)))
                self._register_source(db, owner, project_id, sequence['id'], source)
                self._event(db, sequence['id'], 'sequence_created', source=source)
            self._remember(db, owner, idem, fingerprint, 'sequence', sequence['id'])
            return sequence

    def get(self, owner, project_id, sequence_id):
        with self._transaction() as db:
            return self._sequence(db, owner, project_id, sequence_id)

    def bind_source(self, owner, project_id, sequence_id, source, idem, *, expected_revision):
        """Explicitly carry the same sequence into a new run/version."""
        source = source_ref(source)
        request = dict(sequence_id=text(sequence_id), source=source, expected_revision=integer(expected_revision))
        fingerprint = self._request_hash('bind_source', project_id, request)
        with self._transaction() as db:
            if replay := self._replay(db, owner, project_id, idem, fingerprint):
                return replay
            sequence = self._sequence(db, owner, project_id, sequence_id)
            self._cas(sequence, expected_revision)
            if self._register_source(db, owner, project_id, sequence_id, source):
                self._bump_sequence(db, sequence)
                self._event(db, sequence_id, 'source_bound', source=source, revision=sequence['revision'])
            self._remember(db, owner, idem, fingerprint, 'source', source['version_id'])
            return source

    def reserve_round(self, owner, project_id, sequence_id, kind, source_version_id, input_sha256, idem,
                      *, expected_revision, requested_by_user=None):
        """Reserve one complete film round, never an allowance per scene."""
        require(type(kind) is str and kind in {'automatic', 'user_requested'})
        if kind == 'automatic':
            require(requested_by_user is None)
        else:
            requested_by_user = user_request(requested_by_user, {'version_id': source_version_id})
        request = dict(sequence_id=text(sequence_id), kind=kind, source_version_id=text(source_version_id),
            input_sha256=sha(input_sha256), expected_revision=integer(expected_revision),
            requested_by_user=deepcopy(requested_by_user))
        fingerprint = self._request_hash('reserve_round', project_id, request)
        with self._transaction() as db:
            if replay := self._replay(db, owner, project_id, idem, fingerprint):
                return replay
            sequence = self._sequence(db, owner, project_id, sequence_id)
            self._cas(sequence, expected_revision)
            source = self._source(db, owner, project_id, sequence_id, source_version_id)
            rounds = [json.loads(row[0]) for row in db.execute(
                'SELECT document FROM production_sequence_rounds WHERE sequence_id=?', (sequence_id,))]
            count = sum(row['kind'] == kind for row in rounds)
            require(count < sequence['limits'][kind], 'production_sequence_allowance_consumed', 409)
            if kind == 'user_requested':
                require(any(row['kind'] == 'automatic' and row['status'] in TERMINAL for row in rounds),
                    'production_sequence_initial_round_required', 409)
                require(all(row.get('requested_by_user', {}).get('request_id') != requested_by_user['request_id']
                    for row in rounds if row['kind'] == 'user_requested'),
                    'production_sequence_user_request_conflict', 409)
            self._idle(db, sequence_id)
            now = time.time()
            operation = dict(schema='video.production-sequence-round.v1', id=str(uuid4()), owner=owner,
                project_id=project_id, sequence_id=sequence_id, kind=kind,
                round_index=0 if kind == 'automatic' else count + 1, source=source,
                input_sha256=input_sha256, requested_by_user=request['requested_by_user'],
                idempotency_key=idem, status='reserved', revision=1, execution_ref=None,
                result_sha256=None, stop_proof_sha256=None, error=None, created_at=now, updated_at=now)
            db.execute('INSERT INTO production_sequence_rounds VALUES (?,?,?,?,?,?,?,?,?)',
                (operation['id'], owner, project_id, sequence_id, kind, operation['round_index'], 'reserved',
                 requested_by_user['request_id'] if requested_by_user is not None else None, packed(operation)))
            self._bump_sequence(db, sequence)
            self._remember(db, owner, idem, fingerprint, 'work', operation['id'])
            self._event(db, sequence_id, 'round_reserved', operation_id=operation['id'], kind=kind,
                round_index=operation['round_index'], input_sha256=input_sha256)
            return operation

    def reserve_requirements(self, owner, project_id, sequence_id, source_version_id, items, budget, idem,
                             *, expected_revision):
        """Admit explicit scoped work without creating or resetting a sequence."""
        request = dict(sequence_id=text(sequence_id), source_version_id=text(source_version_id),
            items=requirement_items(items), budget=requirement_budget(budget),
            expected_revision=integer(expected_revision))
        fingerprint = self._request_hash('reserve_requirements', project_id, request)
        with self._transaction() as db:
            if replay := self._replay(db, owner, project_id, idem, fingerprint):
                return replay
            sequence = self._sequence(db, owner, project_id, sequence_id)
            self._cas(sequence, expected_revision)
            source = self._source(db, owner, project_id, sequence_id, source_version_id)
            self._idle(db, sequence_id)
            now = time.time()
            operation = dict(schema='video.production-requirement-batch.v1', id=str(uuid4()), owner=owner,
                project_id=project_id, sequence_id=sequence_id, kind='requirements', source=source,
                items=request['items'], budget=request['budget'], input_sha256=fingerprint,
                idempotency_key=idem, status='reserved', revision=1, execution_ref=None,
                result_sha256=None, stop_proof_sha256=None, error=None, created_at=now, updated_at=now)
            db.execute('INSERT INTO production_requirement_batches VALUES (?,?,?,?,?,?)',
                (operation['id'], owner, project_id, sequence_id, 'reserved', packed(operation)))
            self._bump_sequence(db, sequence)
            self._remember(db, owner, idem, fingerprint, 'work', operation['id'])
            self._event(db, sequence_id, 'requirements_reserved', operation_id=operation['id'],
                budget=operation['budget'], input_sha256=fingerprint)
            return operation

    def operation(self, owner, project_id, operation_id):
        with self._transaction() as db:
            return self._work(db, owner, project_id, operation_id)[1]

    def operations(self, owner, project_id, sequence_id):
        with self._transaction() as db:
            self._sequence(db, owner, project_id, sequence_id)
            rows = [json.loads(row[0]) for table in _WORK_TABLES for row in db.execute(
                'SELECT document FROM ' + table + ' WHERE sequence_id=?', (sequence_id,))]
            return sorted(rows, key=lambda row: (row['created_at'], row['id']))

    def events(self, owner, project_id, sequence_id):
        with self._transaction() as db:
            self._sequence(db, owner, project_id, sequence_id)
            return [dict(position=row[0], **json.loads(row[1])) for row in db.execute(
                'SELECT position,document FROM production_sequence_events WHERE sequence_id=? ORDER BY position',
                (sequence_id,))]

    def _save_operation(self, db, table, operation, event):
        operation['revision'] += 1
        operation['updated_at'] = time.time()
        db.execute('UPDATE ' + table + ' SET status=?,document=? WHERE id=?',
            (operation['status'], packed(operation), operation['id']))
        self._event(db, operation['sequence_id'], event, operation_id=operation['id'],
            operation_revision=operation['revision'], status=operation['status'],
            execution_ref=operation['execution_ref'], error=operation['error'],
            result_sha256=operation['result_sha256'], stop_proof_sha256=operation['stop_proof_sha256'])
        return operation

    def claim_submission(self, owner, project_id, operation_id, *, expected_revision):
        """Only the successful CAS claimant may admit the original coordinator."""
        with self._transaction() as db:
            table, operation = self._work(db, owner, project_id, operation_id)
            self._cas(operation, expected_revision)
            if operation['status'] != 'reserved':
                return dict(operation, submission_claimed=False)
            operation['status'] = 'submitting'
            result = self._save_operation(db, table, operation, 'submission_claimed')
            return dict(result, submission_claimed=True)

    def submission_unknown(self, owner, project_id, operation_id, reason, *, expected_revision):
        text(reason, 2000)
        with self._transaction() as db:
            table, operation = self._work(db, owner, project_id, operation_id)
            self._cas(operation, expected_revision)
            require(operation['status'] in {'submitting', 'submission_unknown'},
                'production_sequence_transition_conflict', 409)
            operation.update(status='submission_unknown', error=reason)
            return self._save_operation(db, table, operation, 'submission_unknown')

    def submitted(self, owner, project_id, operation_id, execution_ref, *, expected_revision):
        text(execution_ref)
        with self._transaction() as db:
            table, operation = self._work(db, owner, project_id, operation_id)
            self._cas(operation, expected_revision)
            if operation['execution_ref'] is not None:
                require(operation['execution_ref'] == execution_ref, 'production_sequence_execution_conflict', 409)
                return operation
            require(operation['status'] in {'submitting', 'submission_unknown'},
                'production_sequence_transition_conflict', 409)
            operation.update(status='submitted', execution_ref=execution_ref, error=None)
            return self._save_operation(db, table, operation, 'submission_recorded')

    def finish(self, owner, project_id, operation_id, status, result_sha256, *, expected_revision,
               stop_proof_sha256=None, error=None):
        require(type(status) is str and status in TERMINAL)
        sha(result_sha256)
        if stop_proof_sha256 is not None:
            sha(stop_proof_sha256)
        if error is not None:
            text(error, 2000)
        with self._transaction() as db:
            table, operation = self._work(db, owner, project_id, operation_id)
            self._cas(operation, expected_revision)
            result = dict(status=status, result_sha256=result_sha256,
                stop_proof_sha256=stop_proof_sha256, error=error)
            if operation['status'] in TERMINAL:
                require(all(operation[key] == value for key, value in result.items()),
                    'production_sequence_result_conflict', 409)
                return operation
            if status == 'completed':
                require(operation['status'] in {'submitted', 'running'} and operation['execution_ref'] is not None,
                    'production_sequence_transition_conflict', 409)
            if operation['status'] != 'reserved':
                require(stop_proof_sha256 is not None, 'production_sequence_stop_proof_required', 409)
            operation.update(result)
            return self._save_operation(db, table, operation, 'work_finished')
