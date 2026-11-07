"""Small, evidence-bound feedback tasks and explicit development handoff history.

This module projects existing lab records. It never edits their identity, calls
an Agent, writes Git, or treats downloading a task as processing its feedback.
Full frozen artifacts remain in the original database/report store.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import threading
from urllib.parse import quote, urlsplit
import uuid

from .animation_lab_store import AnnotationStore
from .animation_lab_triage_store import TriageStore
from .motion import canonical, sha256


SCHEMA = 'video.animation-lab-feedback-task.v1'
MODULES = ('diagram', 'process', 'continuity', 'canvas', 'three')
STATES = frozenset({'prepared', 'synced', 'running', 'pr_open', 'candidate_ready',
                    'accepted', 'rejected', 'failed', 'canceled'})
TRANSITIONS = {
    'prepared': {'synced', 'canceled'},
    'synced': {'running', 'failed', 'canceled'},
    'running': {'pr_open', 'failed', 'canceled'},
    'pr_open': {'candidate_ready', 'failed', 'canceled'},
    'candidate_ready': {'accepted', 'rejected', 'failed', 'canceled'},
    # An explicit new runner attempt can reuse the frozen task. The attempt ID
    # must change; importing/refreshing an old receipt never retries anything.
    'accepted': set(), 'rejected': {'running'}, 'failed': {'running'},
    'canceled': {'synced', 'running'},
}
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_REVISION = re.compile(r'[0-9a-f]{40}(?:[0-9a-f]{24})?\Z')
_KEY = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z')
MAX_PACKAGE_BYTES = 2 * 1024 * 1024
_RECEIPT_FIELDS = {'task_identity', 'module', 'source_revision', 'run_id', 'branch',
                   'commit', 'pr_url', 'candidate_url', 'checks', 'comment',
                   'run_slug', 'report_identity'}
_PROOF_FIELDS = {'task_identity', 'module', 'source_revision', 'commit', 'run_slug',
                 'report_identity', 'run_identity', 'artifact_sha256s',
                 'semantic_input_sha256', 'publication_identity', 'candidate_url', 'verification_scope',
                 'case_id', 'covered_feedback_keys', 'uncovered_feedback_keys'}


class IterationError(ValueError):
    def __init__(self, code, status=422):
        super().__init__(code)
        self.code, self.status = code, status


def _require(value, code, status=422):
    if not value:
        raise IterationError(code, status)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _digest(value):
    return type(value) is str and _SHA.fullmatch(value) is not None


def _text(value, maximum=4000):
    return (type(value) is str and len(value) <= maximum
            and not any((ord(c) < 32 and c not in '\n\r\t') or 0xD800 <= ord(c) <= 0xDFFF for c in value))


def _annotation_rows(db):
    rows = []
    for raw, retraction in db.execute('''SELECT a.document, r.document FROM lab_annotations a
            LEFT JOIN lab_retractions r ON r.annotation_id=a.id ORDER BY a.rowid'''):
        row = json.loads(raw)
        row['retracted'] = retraction is not None
        if retraction:
            row['retraction'] = json.loads(retraction)
        rows.append(row)
    latest = {}
    for row in rows:
        if not row['retracted'] and row['kind'] in {'preference', 'review'}:
            latest[row['case_identity'], row['variant_id'], row['kind']] = row['id']
    for row in rows:
        row['superseded'] = (not row['retracted'] and row['kind'] in {'preference', 'review'}
            and latest[row['case_identity'], row['variant_id'], row['kind']] != row['id'])
        row['effective'] = not row['retracted'] and not row['superseded']
    return rows


def _source_snapshot(db, identity):
    # SQLite projects metadata before Python sees it: large Base64 artifacts and
    # plans never become part of this task/export, nor a JSON object in memory.
    row = db.execute('''SELECT case_id, json_extract(document,'$.module'),
            json_extract(document,'$.title'), json_extract(document,'$.runtime'),
            json_extract(document,'$.comparison') FROM lab_snapshots WHERE identity=?''', (identity,)).fetchone()
    if not row:
        return None
    variants = [dict(id=v[0], compiler=v[1], artifact_sha256=v[2], input_sha256=v[3]) for v in db.execute('''
        SELECT coalesce(json_extract(v.value,'$.variant_id'),json_extract(v.value,'$.id')), json_extract(v.value,'$.compiler'),
            json_extract(v.value,'$.artifact_sha256'), json_extract(v.value,'$.input_sha256')
        FROM lab_snapshots s, json_each(s.document,'$.variants') v WHERE s.identity=?''', (identity,))]
    return dict(case_id=row[0], module=row[1], title=row[2],
                runtime=json.loads(row[3]) if row[3] else {},
                comparison=json.loads(row[4]) if row[4] else {}, variants=variants)


def _runtime_projection(runtime):
    fields = ('schema', 'gsap', 'assets', 'engine_assets', 'preview_adapter_sha256',
              'width', 'height', 'fps', 'output', 'production_export_verified')
    return {key: runtime[key] for key in fields if key in runtime}


def _stable_feedback(item):
    """Runtime navigation/availability cannot change the original opinion scope."""
    result = {key: value for key, value in item.items() if key not in {'links', 'historical_source'}}
    if 'frames' in result:
        result['frames'] = [{key: value for key, value in frame.items() if key != 'image_url'}
                            for frame in result['frames']]
    return result


class IterationStore:
    """Additive lab tables for frozen task packages and append-only status events."""

    def __init__(self, path, source_revision='unknown', base_path=''):
        _require(source_revision == 'unknown' or (type(source_revision) is str
                 and _REVISION.fullmatch(source_revision)), 'animation_iteration_revision_invalid')
        _require(type(base_path) is str and (not base_path or re.fullmatch(r'(?:/[a-z0-9][a-z0-9_-]*)+/?', base_path)),
                 'animation_iteration_base_path_invalid')
        self.path = Path(path)
        self.source_revision, self.base_path = source_revision, base_path.rstrip('/')
        self.lock = threading.RLock()
        AnnotationStore(path)
        TriageStore(path)
        with self.lock, self._connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS lab_iteration_cursors (
                    identity TEXT PRIMARY KEY, module TEXT NOT NULL, document TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lab_iteration_tasks (
                    identity TEXT PRIMARY KEY, module TEXT NOT NULL,
                    document TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS lab_iteration_events (
                    id TEXT PRIMARY KEY, task_identity TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL, document TEXT NOT NULL,
                    UNIQUE(task_identity,idempotency_key),
                    FOREIGN KEY(task_identity) REFERENCES lab_iteration_tasks(identity)
                );
                CREATE TABLE IF NOT EXISTS lab_iteration_sync (
                    module TEXT PRIMARY KEY, cursor TEXT NOT NULL, task_identity TEXT NOT NULL,
                    FOREIGN KEY(cursor) REFERENCES lab_iteration_cursors(identity),
                    FOREIGN KEY(task_identity) REFERENCES lab_iteration_tasks(identity)
                );
            ''')

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute('PRAGMA foreign_keys=ON')
            with db:
                yield db
        finally:
            db.close()

    def _feedback(self, db, module, current_cases=None, evaluation_runs=None):
        records, inactive = {}, {}
        snapshot_cache = {}
        for row in _annotation_rows(db):
            identity = row['case_identity']
            if identity not in snapshot_cache:
                snapshot_cache[identity] = _source_snapshot(db, identity)
            snapshot = snapshot_cache[identity]
            if snapshot is None or (row.get('module') or snapshot.get('module')) != module:
                continue
            key = 'annotation:' + row['id']
            if not row['effective']:
                inactive[key] = 'retracted' if row['retracted'] else 'superseded'
                continue
            fields = ('id', 'case_id', 'case_identity', 'variant_id', 'kind', 'start_seconds',
                      'end_seconds', 'start_frame', 'end_frame', 'fps', 'region', 'tags',
                      'severity', 'comment', 'preference', 'verdict', 'created_at', 'review_scope')
            item = {name: row[name] for name in fields if name in row}
            item.pop('id', None)
            item.update(key=key, source_type='annotation', record_id=row['id'], module=module,
                title=snapshot['title'], effective=True,
                historical_source=(current_cases.get(row['case_id']) != identity) if current_cases is not None else None,
                binding=dict(case_identity=identity, variants=snapshot['variants'], comparison=snapshot['comparison'],
                             runtime=_runtime_projection(snapshot['runtime'])),
                links=dict(annotations_url=self.base_path + '/api/annotations?case_id=' + quote(row['case_id'], safe='')))
            records[key] = item
        raw_decisions = [json.loads(row[0]) for row in db.execute(
            'SELECT document FROM lab_evaluation_decisions ORDER BY rowid')]
        latest = {(row['report_identity'], row['finding_id']): row for row in raw_decisions}
        for (identity, finding_id), decision in latest.items():
            raw = db.execute('SELECT document FROM lab_evaluation_snapshots WHERE identity=?', (identity,)).fetchone()
            if raw is None:
                continue
            snapshot = json.loads(raw[0])
            if snapshot.get('module', snapshot.get('source', {}).get('module')) != module:
                continue
            key = 'finding:' + identity + ':' + finding_id
            if decision['decision'] != 'needs_fix':
                inactive[key] = 'decision_' + decision['decision']
                continue
            finding = next((item for item in snapshot['findings'] if item['id'] == finding_id), None)
            if finding is None:
                continue
            # Never include arbitrary source payload or developer identity.
            finding_fields = ('source', 'variant_id', 'clip_id', 'category', 'severity',
                              'description', 'code', 'claim', 'region', 'confidence', 'location_status')
            item = {name: finding[name] for name in finding_fields if name in finding}
            run_id = (evaluation_runs or {}).get(identity)
            frame_links = []
            for frame in finding.get('frames', []):
                projected = {name: frame[name] for name in ('id', 'variant_id', 'clip_id', 'time_seconds', 'sha256', 'mime_type') if name in frame}
                if run_id:
                    projected['image_url'] = (self.base_path + '/api/evaluations/' + quote(run_id, safe='')
                        + '/frames/' + quote(frame['id'], safe='') + '?report_identity=' + identity)
                frame_links.append(projected)
            links = {}
            if run_id:
                links['report_url'] = self.base_path + '/evaluations/' + quote(run_id, safe='') + '/report.html'
            item.update(key=key, source_type='evaluation_finding', record_id=decision['id'], module=module,
                case_id=snapshot.get('case_id'), report_identity=identity, run_identity=snapshot['run_identity'],
                finding_id=finding_id, kind='needs_fix', comment=decision.get('comment', ''),
                created_at=decision['created_at'], effective=True, frames=frame_links, links=links,
                historical_source=None if evaluation_runs is None else identity not in evaluation_runs,
                binding={name: snapshot[name] for name in ('identity', 'run_identity', 'evidence_identity',
                         'review_identity', 'runtime_identity', 'semantic_input_sha256', 'variants') if name in snapshot})
            records[key] = item
        return records, inactive

    @staticmethod
    def _cursor(module, records):
        value = dict(schema='video.animation-lab-feedback-cursor.v1', module=module,
                     feedback=[_stable_feedback(records[key]) for key in sorted(records)])
        return dict(value, identity=sha256(value))

    @staticmethod
    def _module_cursor(db, module):
        row = db.execute('SELECT cursor FROM lab_iteration_sync WHERE module=?', (module,)).fetchone()
        return row[0] if row else None

    @staticmethod
    def _delta(previous, current, inactive):
        changes = []
        for key in sorted(set(previous) | set(current)):
            if key not in current:
                changes.append(dict(op='inactivate', key=key, reason=inactive.get(key, 'no_longer_effective'), feedback=previous[key]))
            elif key not in previous or _stable_feedback(previous[key]) != _stable_feedback(current[key]):
                changes.append(dict(op='upsert', key=key, reason='new' if key not in previous else 'updated', feedback=current[key]))
        return changes

    def _previous(self, db, module, cursor):
        if cursor is None:
            return {}
        _require(_digest(cursor), 'animation_iteration_cursor_invalid')
        row = db.execute('SELECT module,document FROM lab_iteration_cursors WHERE identity=?', (cursor,)).fetchone()
        _require(row is not None and row[0] == module, 'animation_iteration_cursor_not_found', 409)
        return {item['key']: item for item in json.loads(row[1])['feedback']}

    @staticmethod
    def _events(db, task_id):
        return [json.loads(row[0]) for row in db.execute(
            'SELECT document FROM lab_iteration_events WHERE task_identity=? ORDER BY rowid', (task_id,))]

    def _task(self, db, task_id, *, current_cases=None, evaluation_runs=None, current_cursor=None):
        _require(_digest(task_id), 'animation_iteration_task_invalid')
        row = db.execute('SELECT document,created_at FROM lab_iteration_tasks WHERE identity=?', (task_id,)).fetchone()
        _require(row is not None, 'animation_iteration_task_not_found', 404)
        package, created_at = json.loads(row[0]), row[1]
        events = self._events(db, task_id)
        if current_cursor is None:
            records, _ = self._feedback(db, package['module'], current_cases, evaluation_runs)
            current_cursor = self._cursor(package['module'], records)['identity']
        return dict(task_id=task_id, identity=task_id, module=package['module'],
            source_revision=package['source_revision'], package=package,
            state=events[-1]['state'] if events else 'prepared', created_at=created_at, events=events,
            current_feedback_matches=current_cursor == package['next_cursor'],
            production_adopted=False, acceptance_scope='developer_sample_only')

    def summary(self, module=None, *, current_cases=None, evaluation_runs=None):
        _require(module is None or module in MODULES, 'animation_iteration_module_invalid')
        with self.lock, self._connection() as db:
            db.execute('BEGIN')
            modules = []
            for selected in (MODULES if module is None else (module,)):
                records, inactive = self._feedback(db, selected, current_cases, evaluation_runs)
                cursor = self._module_cursor(db, selected)
                current = self._cursor(selected, records)
                tasks = [self._task(db, row[0], current_cursor=current['identity'])
                         for row in db.execute('SELECT identity FROM lab_iteration_tasks WHERE module=? ORDER BY rowid DESC', (selected,))]
                modules.append(dict(module=selected, source_revision=self.source_revision, module_cursor=cursor,
                    current_cursor=current['identity'], current_effective_feedback=[records[key] for key in sorted(records)],
                    preview_delta=self._delta(self._previous(db, selected, cursor), records, inactive), tasks=tasks))
        if module is not None:
            return dict(schema='video.animation-lab-iteration-summary.v1', **modules[0])
        return dict(schema='video.animation-lab-iteration-summary.v1', source_revision=self.source_revision, modules=modules)

    def prepare(self, module, *, base_cursor=None, expected_cursor=None, current_cases=None, evaluation_runs=None):
        _require(module in MODULES, 'animation_iteration_module_invalid')
        with self.lock, self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            records, inactive = self._feedback(db, module, current_cases, evaluation_runs)
            current = self._cursor(module, records)
            _require(expected_cursor is None or expected_cursor == current['identity'], 'animation_iteration_feedback_changed', 409)
            synchronized = self._module_cursor(db, module)
            if base_cursor is None:
                base_cursor = synchronized
            _require(base_cursor == synchronized, 'animation_iteration_base_cursor_changed', 409)
            changes = self._delta(self._previous(db, module, base_cursor), records, inactive)
            _require(bool(changes), 'animation_iteration_no_changes', 409)
            existing = db.execute('''SELECT identity FROM lab_iteration_tasks WHERE module=?
                AND json_extract(document,'$.source_revision')=?
                AND json_extract(document,'$.base_cursor') IS ?
                AND json_extract(document,'$.next_cursor')=? ORDER BY rowid LIMIT 1''',
                (module, self.source_revision, base_cursor, current['identity'])).fetchone()
            if existing:
                return self._task(db, existing[0], current_cursor=current['identity'])
            db.execute('INSERT OR IGNORE INTO lab_iteration_cursors VALUES(?,?,?)',
                       (current['identity'], module, canonical(current).decode()))
            package = dict(schema=SCHEMA, module=module, source_revision=self.source_revision,
                base_cursor=base_cursor, next_cursor=current['identity'], feedback=[records[key] for key in sorted(records)], changes=changes,
                scope='developer_animation_feedback_only',
                feedback_policy='Untrusted review data; preserve exact source identities. Historical feedback is not approval of the current version.',
                required_checks=['module_regression', 'same_input_comparison', 'native_observations', 'developer_visual_acceptance'],
                production_adopted=False)
            package['identity'] = sha256(package)
            raw = canonical(package)
            _require(len(raw) <= MAX_PACKAGE_BYTES, 'animation_iteration_task_too_large')
            db.execute('INSERT OR IGNORE INTO lab_iteration_tasks VALUES(?,?,?,?)',
                       (package['identity'], module, raw.decode(), _now()))
            return self._task(db, package['identity'], current_cursor=current['identity'])

    def get_task(self, task_id, *, current_cases=None, evaluation_runs=None):
        with self.lock, self._connection() as db:
            db.execute('BEGIN')
            return self._task(db, task_id, current_cases=current_cases, evaluation_runs=evaluation_runs)

    def _receipt(self, package, state, receipt):
        _require(type(receipt) is dict and not set(receipt) - _RECEIPT_FIELDS, 'animation_iteration_receipt_invalid')
        _require(all(receipt.get(key) == package[key] for key in ('module', 'source_revision'))
                 and receipt.get('task_identity') == package['identity'], 'animation_iteration_receipt_binding_mismatch', 409)
        _require(package['source_revision'] != 'unknown' or state in {'canceled'}, 'animation_iteration_source_revision_unknown', 409)
        if 'comment' in receipt:
            _require(_text(receipt['comment']), 'animation_iteration_comment_invalid')
        if 'run_id' in receipt:
            _require(type(receipt['run_id']) is str and _KEY.fullmatch(receipt['run_id']), 'animation_iteration_run_invalid')
        if 'branch' in receipt:
            _require(type(receipt['branch']) is str and re.fullmatch(r'codex/[a-z0-9][a-z0-9._/-]{0,149}', receipt['branch'])
                     and '..' not in receipt['branch'] and '//' not in receipt['branch'], 'animation_iteration_branch_invalid')
        if 'commit' in receipt:
            _require(type(receipt['commit']) is str and _REVISION.fullmatch(receipt['commit']), 'animation_iteration_commit_invalid')
        if 'pr_url' in receipt:
            _require(type(receipt['pr_url']) is str and re.fullmatch(r'https://github\.com/zmhhaha/panghu_agent/pull/[1-9][0-9]{0,9}', receipt['pr_url']),
                     'animation_iteration_pr_url_invalid')
        if 'candidate_url' in receipt:
            value = receipt['candidate_url']
            _require(type(value) is str and len(value) <= 1000 and _text(value, 1000), 'animation_iteration_candidate_url_invalid')
            parsed = urlsplit(value)
            _require(not parsed.scheme and not parsed.netloc and not parsed.query
                     and parsed.path == self.base_path + '/' and '\\' not in value
                     and re.fullmatch(r'evaluation-[a-z0-9][a-z0-9-]{0,63}-[0-9a-f]{16}', parsed.fragment),
                     'animation_iteration_candidate_url_invalid')
        if 'run_slug' in receipt:
            _require(type(receipt['run_slug']) is str and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', receipt['run_slug']),
                     'animation_iteration_candidate_run_invalid')
        if 'report_identity' in receipt:
            _require(_digest(receipt['report_identity']), 'animation_iteration_candidate_report_invalid')
        if 'checks' in receipt:
            checks = receipt['checks']
            _require(type(checks) is list and len(checks) <= 64 and all(type(item) is dict
                and set(item) <= {'name', 'status', 'evidence_sha256'} and _text(item.get('name'), 120)
                and item.get('status') in {'passed', 'failed', 'not_run'}
                and ('evidence_sha256' not in item or _digest(item['evidence_sha256'])) for item in checks),
                'animation_iteration_checks_invalid')
        if state == 'synced':
            _require(bool(receipt.get('run_id')), 'animation_iteration_run_required')
        elif state == 'running':
            _require(bool(receipt.get('run_id')) and bool(receipt.get('branch')), 'animation_iteration_execution_receipt_required')
        elif state == 'pr_open':
            _require(all(receipt.get(key) for key in ('run_id', 'branch', 'commit', 'pr_url')),
                     'animation_iteration_pr_receipt_required')
        elif state == 'candidate_ready':
            _require(all(receipt.get(key) for key in ('commit', 'candidate_url', 'checks', 'run_slug', 'report_identity'))
                     and all(item['status'] == 'passed' for item in receipt['checks']),
                     'animation_iteration_candidate_receipt_required')
        elif state in {'accepted', 'rejected', 'failed', 'canceled'}:
            _require(bool(receipt.get('comment', '').strip()), 'animation_iteration_comment_required')
        _require(len(canonical(receipt)) <= 32 * 1024, 'animation_iteration_receipt_too_large')

    def _verified_candidate(self, package, receipt, verifier):
        _require(callable(verifier), 'animation_iteration_candidate_verification_required', 409)
        try:
            proof = verifier(package, receipt)
        except IterationError:
            raise
        except (ValueError, TypeError, OSError, KeyError, json.JSONDecodeError):
            raise IterationError('animation_iteration_candidate_verification_failed', 409) from None
        _require(type(proof) is dict and set(proof) == _PROOF_FIELDS,
                 'animation_iteration_candidate_proof_invalid', 409)
        _require(proof['task_identity'] == package['identity']
                 and proof['module'] == package['module'] and proof['source_revision'] == package['source_revision']
                 and proof['verification_scope'] == 'frozen_report_and_developer_publication_binding'
                 and all(proof[key] == receipt[key] for key in ('commit', 'run_slug', 'report_identity', 'candidate_url'))
                 and all(_digest(proof[key]) for key in ('report_identity', 'run_identity', 'semantic_input_sha256', 'publication_identity')),
                 'animation_iteration_candidate_proof_binding_mismatch', 409)
        _require(type(proof['artifact_sha256s']) is list and 1 <= len(proof['artifact_sha256s']) <= 64
                 and all(_digest(value) for value in proof['artifact_sha256s']),
                 'animation_iteration_candidate_artifacts_invalid', 409)
        _require(_text(proof['case_id'], 200) and bool(proof['case_id']),
                 'animation_iteration_candidate_case_invalid', 409)
        covered = sorted(item['key'] for item in package['feedback'] if item.get('case_id') == proof['case_id'])
        uncovered = sorted(item['key'] for item in package['feedback'] if item.get('case_id') != proof['case_id'])
        _require(proof['covered_feedback_keys'] == covered and proof['uncovered_feedback_keys'] == uncovered,
                 'animation_iteration_candidate_coverage_invalid', 409)
        expected_url = self.base_path + '/#evaluation-' + proof['run_slug'] + '-' + proof['run_identity'][:16]
        _require(proof['candidate_url'] == expected_url, 'animation_iteration_candidate_url_binding_mismatch', 409)
        _require(len(canonical(proof)) <= 16 * 1024, 'animation_iteration_candidate_proof_invalid', 409)
        return proof

    def transition(self, task_id, body, actor=None, *, current_cases=None, evaluation_runs=None, candidate_verifier=None):
        _require(type(body) is dict and set(body) == {'state', 'expected_state', 'idempotency_key', 'receipt'},
                 'animation_iteration_event_invalid')
        state, expected, key = body['state'], body['expected_state'], body['idempotency_key']
        _require(type(state) is str and state in STATES and type(expected) is str and expected in STATES,
                 'animation_iteration_state_invalid')
        _require(type(key) is str and _KEY.fullmatch(key), 'animation_iteration_idempotency_key_invalid')
        _require(actor is None or _text(actor, 200), 'animation_iteration_actor_invalid')
        with self.lock, self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            task = self._task(db, task_id, current_cases=current_cases, evaluation_runs=evaluation_runs)
            package = task['package']
            self._receipt(package, state, body['receipt'])
            old = db.execute('SELECT document FROM lab_iteration_events WHERE task_identity=? AND idempotency_key=?', (task_id, key)).fetchone()
            if old:
                previous = json.loads(old[0])
                _require(previous['state'] == state and previous['expected_state'] == expected
                         and previous['receipt'] == body['receipt'], 'animation_iteration_idempotency_conflict', 409)
                return task
            _require(task['state'] == expected, 'animation_iteration_state_changed', 409)
            _require(state in TRANSITIONS[expected], 'animation_iteration_transition_invalid', 409)
            retry = expected in {'failed', 'canceled', 'rejected'} and state in {'synced', 'running'}
            old_attempts = {event['receipt']['run_id'] for event in task['events'] if 'run_id' in event['receipt']}
            if retry:
                _require(body['receipt'].get('run_id') not in old_attempts,
                         'animation_iteration_retry_run_required', 409)
            attempt_events = task['events']
            attempt_start = next((index for index in range(len(attempt_events) - 1, -1, -1)
                                  if attempt_events[index].get('new_attempt') is True), None)
            if attempt_start is not None:
                attempt_events = attempt_events[attempt_start:]
            for field in (() if retry else ('run_id', 'branch', 'commit', 'pr_url')):
                previous = next((event['receipt'][field] for event in reversed(attempt_events)
                                 if field in event['receipt']), None)
                if previous is not None and field in body['receipt']:
                    _require(body['receipt'][field] == previous,
                             'animation_iteration_execution_binding_changed', 409)
            if state in {'running', 'accepted'}:
                _require(task['current_feedback_matches'], 'animation_iteration_feedback_changed', 409)
            if state == 'running':
                _require(any(event['state'] == 'synced' for event in task['events']),
                         'animation_iteration_sync_required', 409)
            verified_candidate = None
            if state == 'candidate_ready':
                verified_candidate = self._verified_candidate(package, body['receipt'], candidate_verifier)
            elif state == 'accepted':
                candidate_event = next((event for event in reversed(attempt_events) if event['state'] == 'candidate_ready'), None)
                _require(candidate_event is not None and candidate_event.get('verified_candidate') is not None,
                         'animation_iteration_candidate_verification_required', 409)
                verified_candidate = self._verified_candidate(package, candidate_event['receipt'], candidate_verifier)
                _require(verified_candidate == candidate_event['verified_candidate'],
                         'animation_iteration_candidate_changed', 409)
                _require(not verified_candidate['uncovered_feedback_keys'],
                         'animation_iteration_candidate_coverage_incomplete', 409)
            cursor_applied = None
            if state == 'synced':
                cursor_applied = self._module_cursor(db, package['module']) == package['base_cursor']
                if cursor_applied:
                    db.execute('INSERT INTO lab_iteration_sync VALUES(?,?,?) ON CONFLICT(module) DO UPDATE SET cursor=excluded.cursor,task_identity=excluded.task_identity',
                               (package['module'], package['next_cursor'], task_id))
            event = dict(schema='video.animation-lab-iteration-event.v1', id=str(uuid.uuid4()),
                task_identity=task_id, source_revision=package['source_revision'], state=state,
                expected_state=expected, idempotency_key=key, receipt=body['receipt'], created_at=_now(),
                actor=actor, acceptance_scope='candidate_case_only' if verified_candidate is not None else 'developer_sample_only',
                production_adopted=False,
                new_attempt=retry, synchronization_cursor_applied=cursor_applied,
                verified_candidate=verified_candidate,
                evidence_scope='developer_imported_receipt_not_independent_execution_verification')
            db.execute('INSERT INTO lab_iteration_events VALUES(?,?,?,?)', (event['id'], task_id, key, canonical(event).decode()))
            return self._task(db, task_id, current_cases=current_cases, evaluation_runs=evaluation_runs)

    def export_jsonl(self):
        """Append only task/event metadata to a separately authorized backup export."""
        with self.lock, self._connection() as db:
            db.execute('BEGIN')
            rows = [dict(record_type='iteration_task', task=json.loads(row[0])) for row in db.execute(
                'SELECT document FROM lab_iteration_tasks ORDER BY rowid')]
            rows.extend(dict(record_type='iteration_event', event=json.loads(row[0])) for row in db.execute(
                'SELECT document FROM lab_iteration_events ORDER BY rowid'))
            rows.extend(dict(record_type='iteration_cursor', cursor=json.loads(row[0])) for row in db.execute(
                'SELECT document FROM lab_iteration_cursors ORDER BY rowid'))
            rows.extend(dict(record_type='iteration_checkpoint', module=row[0], cursor=row[1], task_identity=row[2])
                        for row in db.execute('SELECT module,cursor,task_identity FROM lab_iteration_sync ORDER BY module'))
        return b''.join(canonical(row) + b'\n' for row in rows)
