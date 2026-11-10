"""Independent, durable allowance for one animation review and creator revision.

This ledger grants execution slots, never aesthetic approval or adoption. Call
``mark_submitting`` before external I/O and submit only when its transient
``submission_claimed`` flag is true. A crash at that boundary is deliberately
ambiguous: reconcile the original operation ID instead of submitting again.
Only a reservation which never crossed that boundary can be released.

User-requested rounds and requirements tasks are intentionally not implemented.
The production orchestrator must retain the original lineage when revising a
project; changed output, copied candidates, and demand items are not new drafts.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sqlite3
import time
from contextlib import contextmanager
from uuid import uuid4

from agent_service.contracts import ServiceError, digest


STAGES = frozenset({'review', 'revision', 'verification'})
TERMINAL = frozenset({'completed', 'failed', 'cancelled', 'released'})
_TABLES = frozenset({'animation_revision_lineages', 'animation_revision_requests',
                     'animation_revision_operations', 'animation_revision_events',
                     'sqlite_sequence'})
_SHA = re.compile(r'^[0-9a-f]{64}$')
MAX_RECORD_BYTES = 2 * 1024 * 1024


def _require(condition, code='invalid_animation_revision_request', status=422):
    if not condition:
        raise ServiceError(code, status)


def _text(value, limit=200):
    _require(type(value) is str and 1 <= len(value) <= limit
             and not any(ord(char) < 32 for char in value))
    return value


def _sha(value):
    _require(type(value) is str and _SHA.fullmatch(value) is not None,
             'invalid_animation_revision_sha256')
    return value


def _json(value):
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(',', ':'),
                             ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ServiceError('invalid_animation_revision_json', 422) from None
    _require(len(encoded.encode('utf-8')) <= MAX_RECORD_BYTES,
             'animation_revision_record_too_large', 413)
    return encoded


def _media(value):
    _require(type(value) is list and len(value) <= 256)
    for item in value:
        _require(type(item) is dict)
        _text(item.get('version_id'))
        _sha(item.get('sha256'))
    return json.loads(_json(value))


class AnimationRevisionStore:
    """SQLite ledger at a dedicated path, separate from business state.db.

    Every stage has at most one unreleased operation per lineage, including
    failed/cancelled operations. Idempotency keys bind complete immutable input.
    New keys cannot allocate another consumed slot. A successful historical
    technical check is returned by its original key, with no new execution.
    """

    def __init__(self, path):
        self.path = str(Path(path).resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        db = self._connect()
        try:
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            _require(tables <= _TABLES, 'animation_revision_database_not_isolated', 409)
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('BEGIN IMMEDIATE')
            statements = [
                '''CREATE TABLE IF NOT EXISTS animation_revision_lineages (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, project_id TEXT NOT NULL,
                    source_version_id TEXT NOT NULL, source_sha256 TEXT NOT NULL,
                    document TEXT NOT NULL, UNIQUE(owner, project_id, source_sha256))''',
                '''CREATE TABLE IF NOT EXISTS animation_revision_requests (
                    owner TEXT NOT NULL, idem TEXT NOT NULL, request_hash TEXT NOT NULL,
                    lineage_id TEXT NOT NULL REFERENCES animation_revision_lineages(id),
                    PRIMARY KEY(owner, idem))''',
                '''CREATE TABLE IF NOT EXISTS animation_revision_operations (
                    id TEXT PRIMARY KEY,
                    lineage_id TEXT NOT NULL REFERENCES animation_revision_lineages(id),
                    stage TEXT NOT NULL, idem TEXT NOT NULL, request_hash TEXT NOT NULL,
                    status TEXT NOT NULL, document TEXT NOT NULL,
                    UNIQUE(lineage_id, idem))''',
                '''CREATE UNIQUE INDEX IF NOT EXISTS animation_revision_stage_slot
                    ON animation_revision_operations(lineage_id, stage)
                    WHERE status != 'released' ''',
                '''CREATE TABLE IF NOT EXISTS animation_revision_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    lineage_id TEXT NOT NULL REFERENCES animation_revision_lineages(id),
                    operation_id TEXT, event TEXT NOT NULL, document TEXT NOT NULL)''',
            ]
            for statement in statements:
                db.execute(statement)
            db.commit()
        except BaseException:
            db.rollback()
            raise
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
    def _lineage(db, lineage_id):
        row = db.execute('SELECT document FROM animation_revision_lineages WHERE id=?',
                         (lineage_id,)).fetchone()
        _require(row is not None, 'animation_revision_not_found', 404)
        return json.loads(row['document'])

    @staticmethod
    def _operation(db, operation_id):
        row = db.execute('SELECT document FROM animation_revision_operations WHERE id=?',
                         (operation_id,)).fetchone()
        _require(row is not None, 'animation_revision_not_found', 404)
        return json.loads(row['document'])

    @staticmethod
    def _stage(db, lineage_id, stage):
        row = db.execute('SELECT document FROM animation_revision_operations '
                         "WHERE lineage_id=? AND stage=? AND status!='released'",
                         (lineage_id, stage)).fetchone()
        return json.loads(row['document']) if row else None

    @staticmethod
    def _event(db, lineage_id, event, operation_id=None, **detail):
        document = dict(event=event, operation_id=operation_id, at=time.time(), **detail)
        db.execute('INSERT INTO animation_revision_events '
                   '(lineage_id,operation_id,event,document) VALUES (?,?,?,?)',
                   (lineage_id, operation_id, event, _json(document)))

    @classmethod
    def _write(cls, db, operation, event, **detail):
        operation['updated_at'] = time.time()
        db.execute('UPDATE animation_revision_operations SET status=?, document=? WHERE id=?',
                   (operation['status'], _json(operation), operation['id']))
        cls._event(db, operation['lineage_id'], event, operation['id'],
                   status=operation['status'], **detail)
        return operation

    def create_lineage(self, owner, project_id, source_version_id, source_sha256,
                       idempotency_key):
        """Bind the first draft once; aliases for the same draft share its budget."""
        request = dict(owner=_text(owner), project_id=_text(project_id),
                       source_version_id=_text(source_version_id), source_sha256=_sha(source_sha256))
        _text(idempotency_key)
        request_hash = digest(request)
        with self._transaction() as db:
            prior = db.execute('SELECT request_hash,lineage_id FROM animation_revision_requests '
                               'WHERE owner=? AND idem=?', (owner, idempotency_key)).fetchone()
            if prior:
                _require(prior['request_hash'] == request_hash, 'animation_revision_idempotency_conflict', 409)
                return self._lineage(db, prior['lineage_id'])
            row = db.execute('SELECT document FROM animation_revision_lineages '
                             'WHERE owner=? AND project_id=? AND source_sha256=?',
                             (owner, project_id, source_sha256)).fetchone()
            if row:
                lineage = json.loads(row['document'])
                _require(lineage['source_version_id'] == source_version_id,
                         'animation_revision_source_conflict', 409)
            else:
                lineage = dict(schema='video.animation-revision-lineage.v1', id=str(uuid4()),
                               **request, round_kind='automatic', round_index=0,
                               review_limit=1, creator_revision_limit=1, created_at=time.time())
                db.execute('INSERT INTO animation_revision_lineages VALUES (?,?,?,?,?,?)',
                           (lineage['id'], owner, project_id, source_version_id,
                            source_sha256, _json(lineage)))
                self._event(db, lineage['id'], 'lineage_created', source_sha256=source_sha256)
            db.execute('INSERT INTO animation_revision_requests VALUES (?,?,?,?)',
                       (owner, idempotency_key, request_hash, lineage['id']))
            return lineage

    def lineage(self, lineage_id):
        with self._transaction() as db:
            return self._lineage(db, lineage_id)

    def operation(self, operation_id):
        with self._transaction() as db:
            return self._operation(db, operation_id)

    def operations(self, lineage_id):
        with self._transaction() as db:
            self._lineage(db, lineage_id)
            return [json.loads(row['document']) for row in db.execute(
                'SELECT document FROM animation_revision_operations WHERE lineage_id=? ORDER BY rowid',
                (lineage_id,))]

    def events(self, lineage_id):
        with self._transaction() as db:
            self._lineage(db, lineage_id)
            return [dict(sequence=row['sequence'], **json.loads(row['document'])) for row in db.execute(
                'SELECT sequence,document FROM animation_revision_events WHERE lineage_id=? ORDER BY sequence',
                (lineage_id,))]

    def review_media_versions(self, review_operation_id, media_versions, profile, revision='1', *, operation_id=None):
        """Freeze the review execution identity alongside its immutable input.

        The two roles intentionally share an input schema, so its JSON digest
        alone cannot distinguish their idempotency namespaces. Old unlabelled
        reservations predate v2: they may only replay v1/revision 1, with their
        original metadata untouched. This helper reads but never migrates rows;
        the subsequent atomic reserve still arbitrates concurrent first calls.
        """
        _text(review_operation_id)
        operation_id = review_operation_id if operation_id is None else _text(operation_id)
        _require(type(profile) is str and profile in {
            'video.animation-review.v1', 'video.animation-review.v2'}
            and type(revision) is str and revision == '1',
            'animation_revision_review_profile_invalid')
        versions = _media(media_versions)
        fields = {'review_profile', 'review_profile_revision'}
        _require(bool(versions) and all(not fields.intersection(row) for row in versions))
        labelled = [dict(row, review_profile=profile, review_profile_revision=revision)
                    for row in versions]

        def retained(operation):
            previous = _media(operation['media_versions'])
            legacy = all(not fields.intersection(item) for item in previous)
            if legacy:
                _require(profile == 'video.animation-review.v1' and revision == '1'
                         and previous == versions, 'animation_revision_idempotency_conflict', 409)
            else:
                _require(previous == labelled, 'animation_revision_idempotency_conflict', 409)
            return previous

        with self._transaction() as db:
            row = db.execute('SELECT document FROM animation_revision_operations WHERE id=?',
                             (review_operation_id,)).fetchone()
            original = json.loads(row['document']) if row is not None else None
            if original is not None:
                _require(original['stage'] == 'review', 'animation_revision_idempotency_conflict', 409)
                review_versions = retained(original)
                if operation_id == review_operation_id:
                    return review_versions
            else:
                _require(operation_id == review_operation_id, 'animation_revision_review_required', 409)
                return labelled
            row = db.execute('SELECT document FROM animation_revision_operations WHERE id=?',
                             (operation_id,)).fetchone()
            if row is None:
                return labelled
            operation = json.loads(row['document'])
            _require(operation['lineage_id'] == original['lineage_id']
                     and operation['stage'] in {'revision', 'verification'},
                     'animation_revision_idempotency_conflict', 409)
            return retained(operation)

    def reserve(self, lineage_id, stage, operation_id, idempotency_key, input_sha256,
                media_versions=None):
        """Atomically reserve a stage, binding its input and the completed review.

        Revision requires nonempty recorded review issues. Verification can
        follow an issue-free review directly; otherwise its revision must have
        completed. No stage can run while another one remains active.
        """
        _require(type(stage) is str and stage in STAGES, 'animation_revision_stage_not_supported')
        request = dict(lineage_id=_text(lineage_id), stage=stage,
                       id=_text(operation_id), input_sha256=_sha(input_sha256),
                       media_versions=_media([] if media_versions is None else media_versions))
        _text(idempotency_key)
        request_hash = digest(request)
        with self._transaction() as db:
            self._lineage(db, lineage_id)
            prior = db.execute('SELECT request_hash,document FROM animation_revision_operations '
                               'WHERE id=? OR (lineage_id=? AND idem=?)',
                               (operation_id, lineage_id, idempotency_key)).fetchall()
            if prior:
                _require(len(prior) == 1 and prior[0]['request_hash'] == request_hash
                         and json.loads(prior[0]['document'])['idempotency_key'] == idempotency_key,
                         'animation_revision_idempotency_conflict', 409)
                return json.loads(prior[0]['document'])
            _require(self._stage(db, lineage_id, stage) is None,
                     'animation_revision_allowance_consumed', 409)
            active = db.execute('SELECT 1 FROM animation_revision_operations '
                                "WHERE lineage_id=? AND status NOT IN ('completed','failed','cancelled','released')",
                                (lineage_id,)).fetchone()
            _require(active is None, 'animation_revision_stage_in_progress', 409)
            review = self._stage(db, lineage_id, 'review')
            revision = self._stage(db, lineage_id, 'revision')
            verification = self._stage(db, lineage_id, 'verification')
            if stage != 'review':
                _require(review is not None and review['status'] == 'completed',
                         'animation_revision_review_required', 409)
            if stage == 'revision':
                _require(bool(review['issues']), 'animation_revision_no_issues', 409)
                _require(verification is None, 'animation_revision_verification_already_started', 409)
            if stage == 'verification' and review['issues']:
                _require(revision is not None and revision['status'] == 'completed',
                         'animation_revision_creator_result_required', 409)
            operation = dict(schema='video.animation-revision-operation.v1', **request,
                             idempotency_key=idempotency_key, request_sha256=request_hash,
                             round_kind='automatic', round_index=0, status='reserved', job_id=None,
                             created_at=time.time(), updated_at=time.time())
            if review is not None:
                operation.update(review_operation_id=review['id'],
                                 review_result_sha256=review['result_sha256'],
                                 review_issues_sha256=review['issues_sha256'])
            if stage == 'verification' and revision is not None:
                operation.update(revision_operation_id=revision['id'],
                                 revision_result_sha256=revision['result_sha256'])
            db.execute('INSERT INTO animation_revision_operations VALUES (?,?,?,?,?,?,?)',
                       (operation_id, lineage_id, stage, idempotency_key, request_hash,
                        'reserved', _json(operation)))
            self._event(db, lineage_id, 'reserved', operation_id,
                        stage=stage, input_sha256=input_sha256, request_sha256=request_hash)
            return operation

    def mark_submitting(self, operation_id):
        """Return submission_claimed=True only for the sole first submitter.

        Repeated calls return False, including after process restart. A caller
        that gets False must query/reconcile, never call submit again.
        """
        with self._transaction() as db:
            operation = self._operation(db, operation_id)
            if operation['status'] != 'reserved':
                return dict(operation, submission_claimed=False)
            operation['status'] = 'submitting'
            self._write(db, operation, 'submission_claimed')
            return dict(operation, submission_claimed=True)

    def mark_submission_unknown(self, operation_id, reason):
        _text(reason, 2000)
        with self._transaction() as db:
            operation = self._operation(db, operation_id)
            if operation['status'] == 'submission_unknown':
                return operation
            _require(operation['status'] == 'submitting', 'animation_revision_state_conflict', 409)
            operation.update(status='submission_unknown', submission_unknown_reason=reason)
            return self._write(db, operation, 'submission_unknown', reason=reason)

    def mark_submitted(self, operation_id, job_id):
        """Bind an accepted job, including lookup recovery of the original ID."""
        _text(job_id)
        with self._transaction() as db:
            operation = self._operation(db, operation_id)
            if operation['job_id'] is not None:
                _require(operation['job_id'] == job_id, 'animation_revision_job_conflict', 409)
                return operation
            _require(operation['status'] in {'submitting', 'submission_unknown'},
                     'animation_revision_state_conflict', 409)
            operation.update(status='submitted', job_id=job_id)
            return self._write(db, operation, 'submitted', job_id=job_id)

    def mark_running(self, operation_id):
        with self._transaction() as db:
            operation = self._operation(db, operation_id)
            if operation['status'] == 'running':
                return operation
            _require(operation['status'] == 'submitted', 'animation_revision_state_conflict', 409)
            operation['status'] = 'running'
            return self._write(db, operation, 'running')

    def finish(self, operation_id, status='completed', *, result=None, issues=None,
               media_versions=None):
        """Record an immutable outcome; failure/cancellation never frees a slot.

        Unknown submissions must first be reconciled to the original job.
        Failed submission means a definitive failure, not a transport timeout.
        Actual media versions and unresolved issues remain available for review;
        no completed operation creates an automatic second review.
        """
        _require(type(status) is str and status in {'completed', 'failed', 'cancelled'})
        result = {} if result is None else result
        _require(type(result) is dict)
        issues = result.get('issues', []) if issues is None else issues
        _require(type(issues) is list and all(type(issue) is dict for issue in issues))
        if 'issues' in result:
            _require(result['issues'] == issues, 'animation_revision_issues_conflict', 409)
        outcome = dict(status=status, result=result, issues=issues,
                       output_media_versions=_media([] if media_versions is None else media_versions))
        outcome = json.loads(_json(outcome))
        outcome_sha256 = digest(outcome)
        with self._transaction() as db:
            operation = self._operation(db, operation_id)
            if operation['status'] in TERMINAL:
                _require(operation.get('outcome_sha256') == outcome_sha256,
                         'animation_revision_outcome_conflict', 409)
                return operation
            _require(operation['status'] != 'submission_unknown',
                     'animation_revision_reconciliation_required', 409)
            if status == 'completed':
                _require(operation['status'] in {'submitted', 'running'},
                         'animation_revision_state_conflict', 409)
            if status == 'cancelled':
                _require(operation['status'] in {'reserved', 'submitted', 'running'},
                         'animation_revision_reconciliation_required', 409)
            operation.update(**outcome, outcome_sha256=outcome_sha256,
                             result_sha256=digest(outcome['result']),
                             issues_sha256=digest(outcome['issues']))
            return self._write(db, operation, 'finished', outcome_sha256=outcome_sha256)

    def release_unsubmitted(self, operation_id, evidence):
        """Release only a local reservation with no durable submit attempt.

        Evidence is a retained local explanation, not a remote absence claim.
        Even an asserted confirmed_not_submitted JSON cannot release ambiguous
        work: this module offers no submitting/unknown release transition.
        """
        _require(type(evidence) is dict and type(evidence.get('reason')) is str)
        _text(evidence['reason'], 2000)
        evidence = json.loads(_json(evidence))
        with self._transaction() as db:
            operation = self._operation(db, operation_id)
            if operation['status'] == 'released':
                _require(operation['release_evidence'] == evidence,
                         'animation_revision_release_conflict', 409)
                return operation
            _require(operation['status'] == 'reserved',
                     'animation_revision_unsubmitted_proof_required', 409)
            operation.update(status='released', release_evidence=evidence)
            return self._write(db, operation, 'unsubmitted_released', evidence=evidence)
