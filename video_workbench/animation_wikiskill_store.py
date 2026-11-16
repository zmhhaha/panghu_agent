"""Owner-scoped, durable whole-library releases and single-admission ledger.

The five-version limit controls browsing, not artifact retention. Old releases,
snapshots and original job receipts remain available to frozen production tasks.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from agent_service.animation_wikiskill_contract import (
    RELEASE_SCHEMA, EVIDENCE_SCOPE, require, identity, uuid_identity, text,
    validate_snapshot, validate_input, validate_result, validate_release,
)
from agent_service.contracts import canonical_bytes, digest

JOB_SCHEMA = 'video.animation-wikiskill-job.v1'
CATALOGUE_SCHEMA = 'video.animation-wikiskill-catalogue.v1'
ACTIVATION_SCHEMA = 'video.animation-wikiskill-activation.v1'
TERMINAL = frozenset({'failed', 'published'})
STATES = TERMINAL | {'queued', 'uploading', 'prepared', 'submitting', 'running', 'submission_unknown'}
_TABLES = {'wikiskill_snapshots', 'wikiskill_jobs', 'wikiskill_releases',
           'wikiskill_active', 'wikiskill_activations'}


def validate_start(body):
    require(type(body) is dict and {'request_id'} <= set(body) <= {'request_id', 'note'})
    return dict(request_id=uuid_identity(body['request_id']), note=text(body.get('note', ''), 4000, empty=True))


def _json(value):
    return canonical_bytes(value).decode('utf-8')


def _metadata(release):
    result = {key: deepcopy(release[key]) for key in ('id', 'owner', 'sequence', 'created_at',
        'summary', 'sha256', 'snapshot_id', 'snapshot_sha256', 'evidence_scope')}
    result.update(package_count=len(release['packages']),
        file_count=sum(len(row['files']) for row in release['packages']))
    return result


class AnimationWikiSkillStore:
    def __init__(self, path, *, clock=time.time):
        target = Path(path).absolute()
        require(not any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)()
            for p in (target, *target.parents)), 'animation_wikiskill_path_invalid', 409)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.clock = str(target), clock
        with self._transaction() as db:
            existing = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            require(existing <= _TABLES, 'animation_wikiskill_database_not_isolated', 409)
            for sql in (
                '''CREATE TABLE IF NOT EXISTS wikiskill_snapshots (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, sha256 TEXT NOT NULL, document TEXT NOT NULL)''',
                '''CREATE TABLE IF NOT EXISTS wikiskill_jobs (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_id TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL, state TEXT NOT NULL,
                    lease_token TEXT, lease_until REAL NOT NULL DEFAULT 0, document TEXT NOT NULL,
                    UNIQUE(owner, request_id))''',
                '''CREATE TABLE IF NOT EXISTS wikiskill_releases (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, sequence INTEGER NOT NULL,
                    job_id TEXT NOT NULL UNIQUE, document TEXT NOT NULL, UNIQUE(owner, sequence))''',
                '''CREATE TABLE IF NOT EXISTS wikiskill_active (
                    owner TEXT PRIMARY KEY, release_id TEXT NOT NULL, activated_at REAL NOT NULL)''',
                '''CREATE TABLE IF NOT EXISTS wikiskill_activations (
                    owner TEXT NOT NULL, request_id TEXT NOT NULL, request_sha256 TEXT NOT NULL,
                    document TEXT NOT NULL, PRIMARY KEY(owner, request_id))''',
            ):
                db.execute(sql)

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.execute('COMMIT')
        except BaseException:
            if db.in_transaction:
                db.execute('ROLLBACK')
            raise
        finally:
            db.close()

    @staticmethod
    def _job(db, owner, job_id):
        row = db.execute('SELECT * FROM wikiskill_jobs WHERE owner=? AND id=?', (owner, job_id)).fetchone()
        require(row is not None, 'animation_wikiskill_job_not_found', 404)
        value = json.loads(row['document'])
        require(value['id'] == job_id and value['owner'] == owner and value['state'] == row['state'],
                'animation_wikiskill_ledger_changed', 409)
        return row, value

    @staticmethod
    def _save(db, value):
        db.execute('UPDATE wikiskill_jobs SET state=?,document=? WHERE owner=? AND id=?',
            (value['state'], _json(value), value['owner'], value['id']))

    @staticmethod
    def _present(value):
        return {key: deepcopy(item) for key, item in value.items()
            if key not in {'input_document', 'input_refs'}}

    def create(self, owner, body, snapshot, document):
        identity(owner)
        request = validate_start(body)
        snapshot = validate_snapshot(snapshot)
        document = validate_input(document)
        require(snapshot['owner'] == owner and document['snapshot']['sha256'] == snapshot['sha256']
            and document['snapshot']['owner'] == owner and document['note'] == request['note'],
            'animation_wikiskill_input_binding_mismatch', 409)
        fingerprint = digest(request)
        with self._transaction() as db:
            prior = db.execute('SELECT request_sha256,document FROM wikiskill_jobs WHERE owner=? AND request_id=?',
                (owner, request['request_id'])).fetchone()
            if prior:
                require(prior['request_sha256'] == fingerprint, 'animation_wikiskill_request_conflict', 409)
                return self._present(json.loads(prior['document']))
            active_job = db.execute("SELECT id FROM wikiskill_jobs WHERE owner=? AND state NOT IN ('published','failed')",
                (owner,)).fetchone()
            require(active_job is None, 'animation_wikiskill_publication_in_progress', 409)
            now, job_id = self.clock(), str(uuid4())
            value = dict(schema=JOB_SCHEMA, id=job_id, owner=owner, request_id=request['request_id'],
                request_sha256=fingerprint, state='queued', snapshot_id=snapshot['id'],
                snapshot_sha256=snapshot['sha256'], input_sha256=digest(document),
                operation_id=job_id, job_id=None, release_id=None, error=None,
                created_at=now, updated_at=now, input_document=document, input_refs=None,
                remote_state=None, remote_stop_proven=False, remote_receipt=None,
                submission_attempted=False, deadline_at=now + 900)
            db.execute('INSERT INTO wikiskill_snapshots VALUES (?,?,?,?)',
                (snapshot['id'], owner, snapshot['sha256'], _json(snapshot)))
            db.execute('INSERT INTO wikiskill_jobs VALUES (?,?,?,?,?,?,?,?)',
                (job_id, owner, request['request_id'], fingerprint, 'queued', None, 0, _json(value)))
            return self._present(value)

    def get(self, owner, job_id):
        identity(owner)
        uuid_identity(job_id)
        with self._transaction() as db:
            return self._present(self._job(db, owner, job_id)[1])

    get_job = get

    def recover(self, owner, request_id):
        identity(owner)
        uuid_identity(request_id)
        with self._transaction() as db:
            row = db.execute('SELECT document FROM wikiskill_jobs WHERE owner=? AND request_id=?',
                (owner, request_id)).fetchone()
            require(row is not None, 'animation_wikiskill_request_not_found', 404)
            return self._present(json.loads(row['document']))

    def snapshot(self, owner, snapshot_id):
        identity(owner)
        uuid_identity(snapshot_id)
        with self._transaction() as db:
            row = db.execute('SELECT document,sha256 FROM wikiskill_snapshots WHERE owner=? AND id=?',
                (owner, snapshot_id)).fetchone()
            require(row is not None, 'animation_wikiskill_snapshot_not_found', 404)
            value = validate_snapshot(json.loads(row['document']))
            require(value['sha256'] == row['sha256'], 'animation_wikiskill_snapshot_changed', 409)
            return value

    def operation(self, owner, job_id, token=None):
        with self._transaction() as db:
            row, value = self._job(db, owner, job_id)
            if token is not None:
                require(row['lease_token'] == token, 'animation_wikiskill_lease_lost', 409)
            return deepcopy(value)

    def acquire(self, owner, job_id, *, seconds=180):
        with self._transaction() as db:
            row, value = self._job(db, owner, job_id)
            if value['state'] in TERMINAL or row['lease_token'] and row['lease_until'] > self.clock():
                return None
            token = str(uuid4())
            db.execute('UPDATE wikiskill_jobs SET lease_token=?,lease_until=? WHERE owner=? AND id=?',
                (token, self.clock() + seconds, owner, job_id))
            return token

    def release_lease(self, owner, job_id, token):
        with self._transaction() as db:
            db.execute('UPDATE wikiskill_jobs SET lease_token=NULL,lease_until=0 WHERE owner=? AND id=? AND lease_token=?',
                (owner, job_id, token))

    def change(self, owner, publication_id, token, **updates):
        require(set(updates) <= {'state', 'input_refs', 'job_id', 'error', 'remote_state',
            'remote_stop_proven', 'remote_receipt', 'submission_attempted'})
        require('state' not in updates or updates['state'] in STATES)
        with self._transaction() as db:
            row, value = self._job(db, owner, publication_id)
            require(row['lease_token'] == token, 'animation_wikiskill_lease_lost', 409)
            require(value['state'] not in TERMINAL, 'animation_wikiskill_job_terminal', 409)
            value.update(deepcopy(updates), updated_at=self.clock())
            self._save(db, value)
            return self._present(value)

    def pending(self, *, limit=20):
        require(type(limit) is int and 1 <= limit <= 1000)
        with self._transaction() as db:
            rows = db.execute("SELECT document FROM wikiskill_jobs WHERE state NOT IN ('published','failed') ORDER BY rowid LIMIT ?",
                (limit,)).fetchall()
            return [self._present(json.loads(row['document'])) for row in rows]

    def publish(self, owner, job_id, token, result):
        operation = self.operation(owner, job_id, token)
        result = validate_result(result, operation['input_sha256'], operation['input_document'])
        with self._transaction() as db:
            row, value = self._job(db, owner, job_id)
            require(row['lease_token'] == token and value['state'] == 'running'
                and value['remote_state'] == 'completed' and value['remote_stop_proven'] is True,
                'animation_wikiskill_stopped_result_required', 409)
            sequence = db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM wikiskill_releases WHERE owner=?',
                (owner,)).fetchone()[0]
            release = dict(schema=RELEASE_SCHEMA, id=str(uuid4()), owner=owner, sequence=sequence,
                job_id=job_id, snapshot_id=value['snapshot_id'], snapshot_sha256=value['snapshot_sha256'],
                input_sha256=value['input_sha256'], created_at=self.clock(),
                **{key: deepcopy(result[key]) for key in
                    ('summary', 'packages', 'index', 'exclusions', 'limitations', 'evidence_scope')})
            release['sha256'] = digest(release)
            validate_release(release)
            db.execute('INSERT INTO wikiskill_releases VALUES (?,?,?,?,?)',
                (release['id'], owner, sequence, job_id, _json(release)))
            db.execute('INSERT INTO wikiskill_active VALUES (?,?,?) ON CONFLICT(owner) DO UPDATE SET release_id=excluded.release_id,activated_at=excluded.activated_at',
                (owner, release['id'], self.clock()))
            value.update(state='published', release_id=release['id'], error=None, updated_at=self.clock())
            self._save(db, value)
            return deepcopy(release)

    @staticmethod
    def _release(db, owner, release_id, *, include_archived=False):
        row = db.execute('SELECT document FROM wikiskill_releases WHERE owner=? AND id=?',
            (owner, release_id)).fetchone()
        require(row is not None, 'animation_wikiskill_release_not_found', 404)
        if not include_archived:
            recent = {item[0] for item in db.execute(
                'SELECT id FROM wikiskill_releases WHERE owner=? ORDER BY sequence DESC LIMIT 5', (owner,))}
            require(release_id in recent, 'animation_wikiskill_release_not_found', 404)
        return validate_release(json.loads(row['document']))

    def get_release(self, owner, release_id, *, include_archived=False):
        identity(owner)
        uuid_identity(release_id)
        with self._transaction() as db:
            return self._release(db, owner, release_id, include_archived=include_archived)

    def active(self, owner):
        identity(owner)
        with self._transaction() as db:
            row = db.execute('SELECT release_id FROM wikiskill_active WHERE owner=?', (owner,)).fetchone()
            return self._release(db, owner, row[0], include_archived=True) if row else None

    def list_recent(self, owner):
        identity(owner)
        with self._transaction() as db:
            rows = db.execute('SELECT document FROM wikiskill_releases WHERE owner=? ORDER BY sequence DESC LIMIT 5',
                (owner,)).fetchall()
            return [_metadata(validate_release(json.loads(row[0]))) for row in rows]

    def catalogue(self, owner):
        identity(owner)
        with self._transaction() as db:
            pointer = db.execute('SELECT release_id FROM wikiskill_active WHERE owner=?', (owner,)).fetchone()
            active = self._release(db, owner, pointer[0], include_archived=True) if pointer else None
            releases = db.execute('SELECT document FROM wikiskill_releases WHERE owner=? ORDER BY sequence DESC LIMIT 5',
                (owner,)).fetchall()
            rows = db.execute('SELECT document FROM wikiskill_jobs WHERE owner=? ORDER BY rowid DESC LIMIT 20',
                (owner,)).fetchall()
            return dict(schema=CATALOGUE_SCHEMA, active_release_id=active['id'] if active else None,
                active_release_sha256=active['sha256'] if active else None,
                releases=[_metadata(validate_release(json.loads(row[0]))) for row in releases],
                jobs=[self._present(json.loads(row[0])) for row in rows])

    def activate(self, owner, body):
        identity(owner)
        require(type(body) is dict and set(body) == {'request_id', 'release_id'})
        uuid_identity(body['request_id'])
        uuid_identity(body['release_id'])
        fingerprint = digest(body)
        with self._transaction() as db:
            prior = db.execute('SELECT request_sha256,document FROM wikiskill_activations WHERE owner=? AND request_id=?',
                (owner, body['request_id'])).fetchone()
            if prior:
                require(prior['request_sha256'] == fingerprint, 'animation_wikiskill_request_conflict', 409)
                return json.loads(prior['document'])
            release = self._release(db, owner, body['release_id'])
            receipt = dict(schema=ACTIVATION_SCHEMA, owner=owner, request_id=body['request_id'],
                release_id=release['id'], release_sha256=release['sha256'], activated_at=self.clock())
            db.execute('INSERT INTO wikiskill_activations VALUES (?,?,?,?)',
                (owner, body['request_id'], fingerprint, _json(receipt)))
            db.execute('INSERT INTO wikiskill_active VALUES (?,?,?) ON CONFLICT(owner) DO UPDATE SET release_id=excluded.release_id,activated_at=excluded.activated_at',
                (owner, release['id'], receipt['activated_at']))
            return deepcopy(receipt)

    def activation(self, owner, request_id):
        identity(owner)
        uuid_identity(request_id)
        with self._transaction() as db:
            row = db.execute('SELECT document FROM wikiskill_activations WHERE owner=? AND request_id=?',
                (owner, request_id)).fetchone()
            require(row is not None, 'animation_wikiskill_request_not_found', 404)
            return json.loads(row[0])

    recover_activation = activation
