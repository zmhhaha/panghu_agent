"""Durable owner-scoped picture projects and immutable content-addressed media.

This store makes no paid calls. Operation claims survive process crashes and
must be reconciled by their original identity, never submitted a second time.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from uuid import uuid4

from agent_service.contracts import ServiceError, canonical_bytes, digest

TERMINAL = frozenset({'completed', 'failed', 'cancelled'})
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def require(value, code='picture_learning_request_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def identifier(value):
    require(type(value) is str and _ID.fullmatch(value) is not None)
    return value


def owner_id(value):
    require(type(value) is str and 1 <= len(value) <= 200
            and not any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value),
            'picture_learning_owner_invalid', 401)
    return value


def new_id():
    return uuid4().hex


def _plain(path):
    path = Path(path).absolute()
    require(not any(part.is_symlink() for part in (path, *path.parents)),
            'picture_learning_storage_invalid', 409)
    return path


class PictureLearningStore:
    def __init__(self, root):
        self.root = _plain(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.blobs = self.root / 'blobs'
        self.blobs.mkdir(exist_ok=True)
        (self.root / 'locks').mkdir(exist_ok=True)
        self.path = self.root / 'picture-learning.sqlite3'
        with self.db() as db:
            for table, extra in (('projects', ''), ('references', ''),
                                 ('experiments', ', project_id TEXT NOT NULL'),
                                 ('operations', ', experiment_id TEXT NOT NULL')):
                db.execute(f'CREATE TABLE IF NOT EXISTS picture_{table} ('
                           f'owner TEXT NOT NULL,id TEXT NOT NULL,document TEXT NOT NULL{extra},'
                           'PRIMARY KEY(owner,id))')
            db.execute('CREATE TABLE IF NOT EXISTS picture_requests ('
                       'owner TEXT NOT NULL,kind TEXT NOT NULL,request_id TEXT NOT NULL,'
                       'request_sha256 TEXT NOT NULL,entity_id TEXT NOT NULL,'
                       'PRIMARY KEY(owner,kind,request_id))')
            db.execute('CREATE INDEX IF NOT EXISTS picture_experiment_project '
                       'ON picture_experiments(owner,project_id)')

    @contextmanager
    def db(self):
        _plain(self.path)
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA busy_timeout=15000')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def decode(row):
        return json.loads(row['document']) if row is not None else None

    def read(self, table, owner, identity, *, db=None):
        require(table in {'projects', 'references', 'experiments', 'operations'})
        owner_id(owner)
        identifier(identity)
        if db is None:
            with self.db() as connection:
                return self.read(table, owner, identity, db=connection)
        row = db.execute(f'SELECT document FROM picture_{table} WHERE owner=? AND id=?',
                         (owner, identity)).fetchone()
        require(row is not None, 'picture_learning_not_found', 404)
        result = self.decode(row)
        require(result['id'] == identity and result['owner'] == owner,
                'picture_learning_record_changed', 409)
        if table == 'experiments':
            require(digest(result['frozen']) == result['frozen_sha256'],
                    'picture_learning_frozen_input_changed', 409)
        return result

    def put(self, table, value, *, db=None, insert=False):
        require(table in {'projects', 'references', 'experiments', 'operations'})
        if db is None:
            with self.db() as connection:
                return self.put(table, value, db=connection, insert=insert)
        if insert:
            keys = ['owner', 'id', 'document']
            values = [value['owner'], value['id'], canonical_bytes(value).decode('utf-8')]
            if table == 'experiments':
                keys.append('project_id')
                values.append(value['project_id'])
            elif table == 'operations':
                keys.append('experiment_id')
                values.append(value['experiment_id'])
            db.execute(f'INSERT INTO picture_{table} ({",".join(keys)}) VALUES ('
                       + ','.join('?' for _ in keys) + ')', values)
        else:
            prior = self.read(table, value['owner'], value['id'], db=db)
            if table == 'experiments':
                require(prior['frozen_sha256'] == value['frozen_sha256']
                        and prior['frozen'] == value['frozen'],
                        'picture_learning_frozen_input_changed', 409)
                # A stop can race a slow external operation. Preserve intent.
                value['stop_requested'] = prior['stop_requested'] or value['stop_requested']
                if value['stop_requested'] and value['state'] not in TERMINAL:
                    value['state'] = 'stopping'
            db.execute(f'UPDATE picture_{table} SET document=? WHERE owner=? AND id=?',
                       (canonical_bytes(value).decode('utf-8'), value['owner'], value['id']))
        return deepcopy(value)

    def list(self, table, owner=None, *, project_id=None):
        require(table in {'projects', 'references', 'experiments', 'operations'})
        clauses, args = [], []
        if owner is not None:
            owner_id(owner)
            clauses.append('owner=?')
            args.append(owner)
        if project_id is not None:
            require(table == 'experiments')
            clauses.append('project_id=?')
            args.append(identifier(project_id))
        query = f'SELECT document FROM picture_{table}'
        if clauses:
            query += ' WHERE ' + ' AND '.join(clauses)
        query += ' ORDER BY rowid DESC'
        with self.db() as db:
            return [self.decode(row) for row in db.execute(query, args)]

    def request(self, owner, kind, request_id, body, *, db=None):
        owner_id(owner)
        identifier(request_id)
        if db is None:
            with self.db() as connection:
                return self.request(owner, kind, request_id, body, db=connection)
        row = db.execute('SELECT * FROM picture_requests WHERE owner=? AND kind=? AND request_id=?',
                         (owner, kind, request_id)).fetchone()
        if row is not None:
            require(row['request_sha256'] == digest(body),
                    'picture_learning_idempotency_conflict', 409)
            return row['entity_id']
        return None

    def bind_request(self, owner, kind, request_id, body, entity_id, db):
        prior = self.request(owner, kind, request_id, body, db=db)
        require(prior is None or prior == entity_id, 'picture_learning_idempotency_conflict', 409)
        db.execute('INSERT OR IGNORE INTO picture_requests VALUES (?,?,?,?,?)',
                   (owner, kind, request_id, digest(body), entity_id))

    def blob(self, raw):
        require(type(raw) is bytes and 0 < len(raw) <= 32 * 1024 * 1024,
                'picture_learning_artifact_size', 413)
        sha = hashlib.sha256(raw).hexdigest()
        path = _plain(self.blobs / sha)
        if not path.exists():
            try:
                with path.open('xb') as stream:
                    stream.write(raw)
            except FileExistsError:
                pass
        self.read_blob(sha)
        return sha

    def blob_path(self, sha):
        require(type(sha) is str and _SHA.fullmatch(sha), 'picture_learning_artifact_not_found', 404)
        path = _plain(self.blobs / sha)
        require(path.is_file() and path.stat().st_nlink == 1 and path.stat().st_size <= 32 * 1024 * 1024,
                'picture_learning_artifact_changed', 409)
        return path

    def read_blob(self, sha):
        raw = self.blob_path(sha).read_bytes()
        require(hashlib.sha256(raw).hexdigest() == sha, 'picture_learning_artifact_changed', 409)
        return raw

    def claim(self, owner, operation_id, *, now=None):
        now = time.time() if now is None else now
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            op = self.read('operations', owner, operation_id, db=db)
            if op['status'] != 'reserved':
                return False
            run = self.read('experiments', owner, op['experiment_id'], db=db)
            if run['stop_requested'] or now >= run['deadline_at']:
                return False
            op.update(status='claimed', claimed_at=now)
            self.put('operations', op, db=db)
            return True

    def stop(self, owner, experiment_id):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            run = self.read('experiments', owner, experiment_id, db=db)
            if run['state'] not in TERMINAL:
                run.update(stop_requested=True, state='stopping', updated_at=time.time())
                self.put('experiments', run, db=db)
            return run
