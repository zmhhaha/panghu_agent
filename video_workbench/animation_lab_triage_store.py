"""Append-only developer decisions about exact machine-review findings.

Tables share the local animation-lab database with manual annotations, but a
machine finding or disposition never becomes a human animation annotation.
The latest decision wins only for its original report and finding; all earlier
decisions and their frozen source reports remain exportable.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import threading
import uuid

from .motion import canonical, sha256


SNAPSHOT_SCHEMA = 'video.animation-lab-triage-snapshot.v1'
DECISION_SCHEMA = 'video.animation-lab-triage-decision.v1'
DECISIONS = frozenset({'needs_fix', 'dismiss', 'defer'})
MAX_REPORT_BYTES = 16 * 1024 * 1024
MAX_FINDINGS = 128
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_FINDING = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z')


def _require(value, code):
    if not value:
        raise ValueError(code)


def _digest(value):
    return type(value) is str and _SHA.fullmatch(value) is not None


def _finding(value):
    return type(value) is str and _FINDING.fullmatch(value) is not None


def _now():
    return datetime.now(timezone.utc).isoformat()


def _report_raw(snapshot):
    _require(type(snapshot) is dict and snapshot.get('schema') == SNAPSHOT_SCHEMA
             and _digest(snapshot.get('identity'))
             and _digest(snapshot.get('run_identity'))
             and _digest(snapshot.get('evidence_identity'))
             and 'review_identity' in snapshot
             and (snapshot['review_identity'] is None or _digest(snapshot['review_identity'])),
             'animation_lab_triage_report_invalid')
    findings = snapshot.get('findings')
    _require(type(findings) is list and len(findings) <= MAX_FINDINGS
             and all(type(row) is dict and _finding(row.get('id')) for row in findings),
             'animation_lab_triage_findings_invalid')
    _require(len({row['id'] for row in findings}) == len(findings),
             'animation_lab_triage_finding_duplicate')
    try:
        raw = canonical(snapshot)
        unsigned = {key: value for key, value in snapshot.items() if key != 'identity'}
        identity = sha256(unsigned)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError('animation_lab_triage_report_invalid') from None
    _require(len(raw) <= MAX_REPORT_BYTES, 'animation_lab_triage_report_too_large')
    _require(identity == snapshot['identity'], 'animation_lab_triage_report_identity_mismatch')
    return raw.decode('utf-8')


def _with_effectiveness(raw_rows):
    rows = [json.loads(raw) for raw in raw_rows]
    latest = {(row['report_identity'], row['finding_id']): row['id'] for row in rows}
    return [dict(row, effective=latest[row['report_identity'], row['finding_id']] == row['id'],
                 superseded=latest[row['report_identity'], row['finding_id']] != row['id'])
            for row in rows]


class TriageStore:
    """Persist exact source reports and bounded developer decisions, never delete."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.lock, self._connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS lab_evaluation_snapshots (
                    identity TEXT PRIMARY KEY, run_identity TEXT NOT NULL,
                    document TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lab_evaluation_decisions (
                    id TEXT PRIMARY KEY, report_identity TEXT NOT NULL,
                    finding_id TEXT NOT NULL, document TEXT NOT NULL,
                    FOREIGN KEY (report_identity) REFERENCES lab_evaluation_snapshots(identity)
                );
                CREATE INDEX IF NOT EXISTS lab_evaluation_decisions_report
                    ON lab_evaluation_decisions(report_identity);
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

    def register_report(self, snapshot):
        raw = _report_raw(snapshot)
        with self.lock, self._connection() as db:
            old = db.execute('SELECT document FROM lab_evaluation_snapshots WHERE identity = ?',
                             (snapshot['identity'],)).fetchone()
            _require(old is None or old[0] == raw, 'animation_lab_triage_report_identity_conflict')
            db.execute('INSERT OR IGNORE INTO lab_evaluation_snapshots VALUES (?, ?, ?)',
                       (snapshot['identity'], snapshot['run_identity'], raw))
        # Return a detached representation: subsequent caller edits cannot
        # relabel the registered source or later exported decision history.
        return json.loads(raw)

    def append(self, report_identity, finding_id, decision, comment=''):
        _require(_digest(report_identity), 'animation_lab_triage_report_invalid')
        _require(_finding(finding_id), 'animation_lab_triage_finding_invalid')
        _require(type(decision) is str and decision in DECISIONS,
                 'animation_lab_triage_decision_invalid')
        _require(type(comment) is str and len(comment) <= 4000
                 and not any((ord(char) < 32 and char not in '\n\r\t')
                             or 0xD800 <= ord(char) <= 0xDFFF for char in comment),
                 'animation_lab_triage_comment_invalid')
        with self.lock, self._connection() as db:
            found = db.execute('SELECT document FROM lab_evaluation_snapshots WHERE identity = ?',
                               (report_identity,)).fetchone()
            _require(found is not None, 'animation_lab_triage_report_not_found')
            snapshot = json.loads(found[0])
            _require(finding_id in {row['id'] for row in snapshot['findings']},
                     'animation_lab_triage_finding_not_found')
            row = dict(schema=DECISION_SCHEMA, id=str(uuid.uuid4()), report_identity=report_identity,
                run_identity=snapshot['run_identity'], finding_id=finding_id, decision=decision,
                comment=comment, created_at=_now(), reviewer='local-developer')
            db.execute('INSERT INTO lab_evaluation_decisions VALUES (?, ?, ?, ?)',
                       (row['id'], report_identity, finding_id, canonical(row).decode('utf-8')))
        return dict(row, effective=True, superseded=False)

    @staticmethod
    def _raw_decisions(db, report_identity=None):
        query = 'SELECT document FROM lab_evaluation_decisions'
        args = ()
        if report_identity is not None:
            query += ' WHERE report_identity = ?'
            args = (report_identity,)
        return [row[0] for row in db.execute(query + ' ORDER BY rowid', args)]

    def list(self, report_identity=None):
        _require(report_identity is None or _digest(report_identity),
                 'animation_lab_triage_report_invalid')
        with self.lock, self._connection() as db:
            return _with_effectiveness(self._raw_decisions(db, report_identity))

    def export_jsonl(self):
        """Export self-contained source reports and the complete decision history."""
        with self.lock, self._connection() as db:
            # Keep both tables in the same SQLite read snapshot if another
            # request registers a report or appends a decision concurrently.
            db.execute('BEGIN')
            snapshots = [json.loads(row[0]) for row in db.execute(
                'SELECT document FROM lab_evaluation_snapshots ORDER BY rowid')]
            decisions = _with_effectiveness(self._raw_decisions(db))
        rows = [dict(record_type='evaluation_snapshot', snapshot=row) for row in snapshots]
        rows.extend(dict(record_type='evaluation_decision', decision=row,
                         effective=row['effective'], superseded=row['superseded']) for row in decisions)
        return b''.join(canonical(row) + b'\n' for row in rows)
