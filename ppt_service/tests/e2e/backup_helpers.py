"""Offline snapshot helpers for owned, quiescent P5 fixtures only.

SQLite backup is intentionally named as such: it is not a PostgreSQL deployment
backup or a proof of concurrent production backup consistency.
"""
from __future__ import annotations

from datetime import date, datetime
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import stat

from sqlalchemy import MetaData, select

from ppt_service.server.models import Asset, ExecutionSlot, Job
from ppt_service.server.repository import ACTIVE


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def files_inventory(root):
    root = Path(root)
    inventory = {}
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or path.is_symlink():
            raise RuntimeError('Only regular synthetic fixture files may be backed up')
        inventory[path.relative_to(root).as_posix()] = {
            'bytes': info.st_size, 'sha256': sha256(path),
            'mode': stat.S_IMODE(info.st_mode), 'mtime_ns': info.st_mtime_ns,
        }
    return inventory


def metadata_snapshot(engine):
    """Stable complete table contents, including histories and idempotency rows."""
    def normalize(value):
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, bytes):
            return {'hex': value.hex()}
        return value
    metadata = MetaData()
    metadata.reflect(bind=engine)
    result = {}
    with engine.connect() as connection:
        for table in sorted(metadata.tables.values(), key=lambda t: t.name):
            rows = [{str(key): normalize(value) for key, value in row.items()}
                    for row in connection.execute(select(table)).mappings()]
            result[table.name] = sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))
    return result


def assert_quiescent(sessions):
    with sessions() as session:
        slot = session.get(ExecutionSlot, 1)
        if slot is None or slot.job_id is not None:
            raise RuntimeError('Fixture execution slot must be idle before backup')
        if session.scalar(select(Job.id).where(Job.status.in_(ACTIVE)).limit(1)):
            raise RuntimeError('Fixture jobs must be stopped before backup')
        if session.scalar(select(Asset.id).where(Asset.parse_status.in_(('parsing', 'stopping'))).limit(1)):
            raise RuntimeError('Fixture material writers must be stopped before backup')


def backup_sqlite_fixture(engine, sessions, database, data_root, destination):
    """Caller closes its HTTP client and stops its synthetic writers first."""
    if engine.dialect.name != 'sqlite':
        raise RuntimeError('This fixture backup helper is explicitly SQLite only')
    destination = Path(destination)
    if destination.exists():
        raise RuntimeError('Backup destination must be new')
    assert_quiescent(sessions)
    before_metadata = metadata_snapshot(engine)
    before_files = files_inventory(data_root)
    destination.mkdir()
    shutil.copytree(data_root, destination / 'data', copy_function=shutil.copy2)
    with closing(sqlite3.connect(str(database))) as source, closing(sqlite3.connect(str(destination / 'state.sqlite3'))) as backup:
        source.backup(backup)
        if backup.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise RuntimeError('SQLite backup integrity check failed')
        if backup.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('SQLite backup foreign key check failed')
    assert_quiescent(sessions)
    if before_metadata != metadata_snapshot(engine) or before_files != files_inventory(data_root):
        raise RuntimeError('Fixture changed during quiescent backup')
    if before_files != files_inventory(destination / 'data'):
        raise RuntimeError('Backup file metadata differs from the source')
    manifest = {'schema_version': 1, 'fixture_only': True, 'database_kind': 'sqlite',
                'quiescent': True, 'database_sha256': sha256(destination / 'state.sqlite3'),
                'files': before_files, 'metadata': before_metadata}
    (destination / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2), encoding='utf-8')
    return manifest


def restore_sqlite_fixture(backup, destination):
    backup, destination = Path(backup), Path(destination)
    if destination.exists():
        raise RuntimeError('Restore destination must be new')
    manifest = json.loads((backup / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('fixture_only') is not True or manifest.get('database_kind') != 'sqlite':
        raise RuntimeError('Expected an explicitly synthetic SQLite backup')
    if manifest['database_sha256'] != sha256(backup / 'state.sqlite3'):
        raise RuntimeError('Backup database checksum mismatch')
    if manifest['files'] != files_inventory(backup / 'data'):
        raise RuntimeError('Backup files checksum or metadata mismatch')
    destination.mkdir()
    shutil.copy2(backup / 'state.sqlite3', destination / 'state.sqlite3')
    shutil.copytree(backup / 'data', destination / 'data', copy_function=shutil.copy2)
    return manifest
