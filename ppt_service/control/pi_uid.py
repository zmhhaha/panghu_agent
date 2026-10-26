"""Controller-private durable UID reservations; no runtime launch authority."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


class UIDError(RuntimeError):
    """Allocation is unsafe or unavailable; callers must not launch."""


class UIDExhausted(UIDError):
    pass


def require(ok, message):
    if not ok:
        raise UIDError(message)


@dataclass(frozen=True)
class UIDScope:
    namespace: str
    deployment: str
    host_pool: str
    uid_min: int
    uid_max: int

    def __post_init__(self):
        for value in (self.namespace, self.deployment, self.host_pool):
            require(type(value) is str and re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,127}', value),
                    'scope must be canonical lowercase ASCII')
        require(type(self.uid_min) is int and type(self.uid_max) is int
                and 10000 < self.uid_min <= self.uid_max < 2**31 - 1,
                'explicit runtime-compatible UID range required')
        require(not self.uid_min <= 65534 <= self.uid_max, 'nobody UID is reserved')

    def canonical(self):
        return json.dumps(self.__dict__, sort_keys=True, separators=(',', ':'))


def identity(project_id, job_id, attempt, token, role):
    require(type(role) is str and role in ('pi', 'preview'), 'invalid UID role')
    for value in (project_id, job_id):
        try:
            require(type(value) is str and str(uuid.UUID(value)) == value,
                    'identity requires canonical UUIDs')
        except (ValueError, AttributeError):
            raise UIDError('identity requires canonical UUIDs') from None
    for value in (attempt, token):
        require(type(value) is int and 0 < value < 2**63, 'invalid attempt or fencing token')
    return json.dumps(dict(project_id=project_id, job_id=job_id, attempt=attempt, token=token, role=role),
                      sort_keys=True, separators=(',', ':'))


SCHEMA = {
    'metadata': "CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK(id=1), scope TEXT NOT NULL, next_uid INTEGER NOT NULL CHECK(next_uid>10000 AND next_uid<=2147483647)) STRICT",
    'allocations': "CREATE TABLE allocations (uid INTEGER PRIMARY KEY CHECK(uid>10000 AND uid<2147483647 AND uid!=65534), identity TEXT NOT NULL) STRICT",
}


def _safe_stat(path, *, directory=False, private=False):
    info = path.lstat()
    require(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400,
            'symlink or reparse point rejected')
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode),
            'unexpected filesystem object')
    if not directory:
        require(info.st_nlink == 1, 'hardlinked ledger rejected')
    if os.name == 'posix':
        require(sys.platform == 'linux' and os.geteuid() == 0 and info.st_uid == 0,
                'Linux root-owned controller storage required')
        mode = stat.S_IMODE(info.st_mode)
        if private:
            require(mode == (0o700 if directory else 0o600), 'private storage permissions required')
        else:
            # Root-owned sticky /tmp protects existing root-owned children from rename.
            require(not mode & 0o022 or directory and mode & stat.S_ISVTX,
                    'untrusted writable ancestor')
    return info


def _parents(path):
    for part in reversed((path, *path.parents)):
        _safe_stat(part, directory=True)


def _sync_directory(path):
    if os.name == 'posix':
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class PiUIDAllocator:
    """Every successful allocate commits a fresh UID, even for duplicate bindings."""

    def __init__(self, directory, scope: UIDScope):
        require(isinstance(scope, UIDScope), 'UIDScope required')
        self.directory = Path(os.path.abspath(directory))
        self.path = self.directory / 'uid.sqlite3'
        self.scope = scope
        with self._transaction() as db:
            self._validate(db)

    @classmethod
    def initialize(cls, directory, scope: UIDScope):
        """Create once in a NEW directory. Failed initialization leaves a tombstone."""
        require(isinstance(scope, UIDScope), 'UIDScope required')
        directory = Path(os.path.abspath(directory))
        try:
            _parents(directory.parent)
            directory.mkdir(mode=0o700)  # Exclusive marker; never remove on failure.
            _sync_directory(directory.parent)
            _safe_stat(directory, directory=True, private=True)
            path = directory / 'uid.sqlite3'
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            os.close(fd)
            db = sqlite3.connect(path, isolation_level=None)
            try:
                db.execute('PRAGMA journal_mode=DELETE')
                db.execute('PRAGMA synchronous=EXTRA')
                db.execute('BEGIN IMMEDIATE')
                for sql in SCHEMA.values():
                    db.execute(sql)
                db.execute('PRAGMA user_version=1')
                db.execute('INSERT INTO metadata VALUES(1,?,?)', (scope.canonical(), scope.uid_min))
                db.commit()
            finally:
                db.close()
            _sync_directory(directory)
            return cls(directory, scope)
        except (OSError, sqlite3.Error) as exc:
            raise UIDError('exclusive initialization failed; do not reset existing storage') from exc

    def _storage(self):
        _parents(self.directory.parent)
        _safe_stat(self.directory, directory=True, private=True)
        info = _safe_stat(self.path, private=True)
        for suffix in ('-journal', '-wal', '-shm'):
            sidecar = Path(str(self.path) + suffix)
            try:
                _safe_stat(sidecar, private=True)
            except FileNotFoundError:
                continue
            require(suffix == '-journal', 'unexpected WAL storage')
        return info.st_dev, info.st_ino

    @contextmanager
    def _transaction(self):
        db = None
        try:
            before = self._storage()
            db = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, timeout=30,
                                 isolation_level=None)
            db.execute('PRAGMA trusted_schema=OFF')
            require(db.execute('PRAGMA journal_mode').fetchone()[0] == 'delete', 'unexpected journal mode')
            db.execute('PRAGMA synchronous=EXTRA')
            db.execute('BEGIN IMMEDIATE')
            require(self._storage() == before, 'ledger replaced during open')
            yield db
            db.commit()  # Do not return an identity before this durable boundary.
        except (OSError, sqlite3.Error) as exc:
            raise UIDError('UID ledger unavailable or corrupt; launch forbidden') from exc
        finally:
            if db is not None:
                if db.in_transaction:
                    db.rollback()
                db.close()

    def _validate(self, db):
        require(db.execute('PRAGMA user_version').fetchone()[0] == 1, 'unknown ledger version')
        actual = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))
        require(actual == SCHEMA, 'unknown ledger schema')
        require(db.execute('PRAGMA quick_check').fetchall() == [('ok',)], 'corrupt ledger')
        rows = db.execute('SELECT id,scope,next_uid FROM metadata').fetchall()
        require(len(rows) == 1 and rows[0][:2] == (1, self.scope.canonical()), 'ledger scope mismatch')
        next_uid = rows[0][2]
        count, low, high = db.execute('SELECT count(*),min(uid),max(uid) FROM allocations').fetchone()
        require(self.scope.uid_min <= next_uid <= self.scope.uid_max + 1
                and count == next_uid - self.scope.uid_min
                and (count == 0 or (low == self.scope.uid_min and high == next_uid - 1)),
                'inconsistent allocation history')
        return next_uid

    def allocate(self, *, project_id, job_id, attempt, token, role):
        binding = identity(project_id, job_id, attempt, token, role)
        with self._transaction() as db:
            uid = self._validate(db)
            if uid > self.scope.uid_max:
                raise UIDExhausted('reserved UID range exhausted')
            db.execute('INSERT INTO allocations VALUES(?,?)', (uid, binding))
            db.execute('UPDATE metadata SET next_uid=? WHERE id=1', (uid + 1,))
        return uid

    def verify(self, *, project_id, job_id, attempt, token, role):
        """Return the UID durably allocated for this exact binding; never allocates.

        A caller-supplied UID is not proof of reservation. Launch code must use the
        returned value instead of trusting its own input or a stored copy.
        """
        binding = identity(project_id, job_id, attempt, token, role)
        with self._transaction() as db:
            self._validate(db)
            rows = db.execute('SELECT uid FROM allocations WHERE identity=? ORDER BY uid', (binding,)).fetchall()
            require(len(rows) == 1, 'binding must have exactly one durable allocation')
            uid = rows[0][0]
            require(type(uid) is int and self.scope.uid_min <= uid <= self.scope.uid_max,
                    'allocated UID outside the ledger scope')
        return uid

    def writers(self, *, project_id, job_id, attempt, token):
        """Every committed Pi/preview UID for the attempt, including duplicates."""
        bindings = tuple(identity(project_id, job_id, attempt, token, role)
                         for role in ('pi', 'preview'))
        with self._transaction() as db:
            self._validate(db)
            rows = db.execute('SELECT uid FROM allocations WHERE identity IN (?,?) ORDER BY uid', bindings).fetchall()
        return tuple(row[0] for row in rows)
