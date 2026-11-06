"""Developer review history, kept entirely outside the production workbench DB."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
import uuid


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(',', ':'))


def _now():
    return datetime.now(timezone.utc).isoformat()


class AnnotationStore:
    """Append annotations and explicit retractions; preserve their frozen inputs."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.lock, self._connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS lab_snapshots (
                    identity TEXT PRIMARY KEY, case_id TEXT NOT NULL, document TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lab_annotations (
                    id TEXT PRIMARY KEY, case_identity TEXT NOT NULL,
                    document TEXT NOT NULL,
                    FOREIGN KEY (case_identity) REFERENCES lab_snapshots(identity)
                );
                CREATE TABLE IF NOT EXISTS lab_retractions (
                    annotation_id TEXT PRIMARY KEY, document TEXT NOT NULL,
                    FOREIGN KEY (annotation_id) REFERENCES lab_annotations(id)
                );
            ''')

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute('PRAGMA foreign_keys = ON')
            with db:
                yield db
        finally:
            db.close()

    def register_snapshot(self, snapshot):
        raw = _json(snapshot)
        with self.lock, self._connection() as db:
            old = db.execute('SELECT document FROM lab_snapshots WHERE identity = ?',
                             (snapshot['identity'],)).fetchone()
            if old and old[0] != raw:
                raise ValueError('animation_lab_snapshot_identity_conflict')
            db.execute('INSERT OR IGNORE INTO lab_snapshots VALUES (?, ?, ?)',
                       (snapshot['identity'], snapshot['case_id'], raw))

    def append(self, annotation):
        row = dict(annotation, schema='video.animation-lab-annotation.v1',
                   id=str(uuid.uuid4()), created_at=_now(), reviewer='local-developer')
        with self.lock, self._connection() as db:
            db.execute('INSERT INTO lab_annotations VALUES (?, ?, ?)',
                       (row['id'], row['case_identity'], _json(row)))
        return dict(row, retracted=False)

    def list(self, case_id=None):
        with self.lock, self._connection() as db:
            query = '''SELECT a.document, r.document FROM lab_annotations a
                       JOIN lab_snapshots s ON s.identity = a.case_identity
                       LEFT JOIN lab_retractions r ON r.annotation_id = a.id'''
            args = ()
            if case_id is not None:
                query += ' WHERE s.case_id = ?'
                args = (case_id,)
            rows = db.execute(query + ' ORDER BY a.rowid', args).fetchall()
        result = []
        for raw, retraction in rows:
            row = json.loads(raw)
            row['retracted'] = retraction is not None
            if retraction:
                row['retraction'] = json.loads(retraction)
            result.append(row)
        return result

    def retract(self, annotation_id, reason):
        with self.lock, self._connection() as db:
            row = db.execute('SELECT document FROM lab_annotations WHERE id = ?',
                             (annotation_id,)).fetchone()
            if row is None:
                raise KeyError(annotation_id)
            event = dict(annotation_id=annotation_id, reason=reason, created_at=_now())
            # Repeated withdrawal is idempotent, without erasing the first event.
            db.execute('INSERT OR IGNORE INTO lab_retractions VALUES (?, ?)',
                       (annotation_id, _json(event)))
        return next(row for row in self.list() if row['id'] == annotation_id)

    def export_jsonl(self):
        """Self-contained plans/artifacts plus asset hashes, with raw review history.

        Asset binaries stay in the explicitly supplied runtime directory. Their
        hashes are exported so a future replay must use the same files. Rows are
        engineering/evaluation data; they never train a model or publish a style.
        """
        with self.lock, self._connection() as db:
            snapshots = [json.loads(row[0]) for row in db.execute(
                'SELECT document FROM lab_snapshots ORDER BY case_id, identity')]
            annotations = self.list()
        rows = [dict(record_type='case_snapshot', snapshot=row) for row in snapshots]
        latest = {}
        for row in annotations:
            if not row['retracted'] and row['kind'] in {'preference', 'review'}:
                latest[row['case_identity'], row['variant_id'], row['kind']] = row['id']
        for row in annotations:
            superseded = (not row['retracted'] and row['kind'] in {'preference', 'review'}
                and latest[row['case_identity'], row['variant_id'], row['kind']] != row['id'])
            rows.append(dict(record_type='annotation', annotation=row, superseded=superseded,
                             effective=not row['retracted'] and not superseded))
        return ('\n'.join(_json(row) for row in rows) + '\n').encode('utf-8')
