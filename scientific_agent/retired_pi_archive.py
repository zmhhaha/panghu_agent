"""Read-only legacy Pi history, independent of a running Worker or its PVC.

Only the explicit operator helper imports data. It verifies a terminal source,
the immutable result and every available public file before marking a task
committed. Individual SQL calls may commit separately: incomplete imports are
never visible, and retries cannot overwrite a task or an already stored file.
Construction, reads and import do not discover credentials or network clients.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re

from .pi_client import WorkerError
from .pi_contracts import (ContractError, MAX_ARTIFACT_BYTES, MAX_REPORT_BYTES,
    TERMINAL_STATES, canonical_json, report_sha256, result_digest,
    validate_task_id, utc_now)
from .pi_projection import sql


TASKS = 'scientific_retired_pi_tasks'
FILES = 'scientific_retired_pi_artifacts'
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_RECORD_BYTES = 4 * 1024 * 1024
PUBLIC_IDS = frozenset(('report', 'searches', 'papers', 'screening', 'evidence',
    'claims', 'references', 'validation', 'revision_output'))
PUBLIC_FIELDS = ('artifact_id', 'media_type', 'size', 'sha256')
SNAPSHOT_FIELDS = frozenset(('schema_version', 'id', 'topic', 'engine', 'mode',
    'state', 'status', 'revision', 'attempt', 'stage', 'progress', 'created_at',
    'updated_at', 'execution_deadline_at', 'started_at', 'finished_at', 'error_code',
    'error', 'report_id', 'result_digest', 'report_sha256', 'report', 'task_kind',
    'intent_id'))


def _require(condition, code='legacy_archive_invalid', status=409):
    if not condition:
        raise WorkerError(status, code, 'Historical execution data could not be verified')


def _retired():
    raise WorkerError(410, 'legacy_execution_retired', 'The former execution service has been retired')


def _task_id(value):
    try:
        return validate_task_id(value)
    except ContractError as exc:
        raise WorkerError(exc.status_code, exc.code, 'Invalid historical task identifier') from None


def _json(value):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError('Duplicate key')
            result[key] = item
        return result
    try:
        decoded = json.loads(value, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        canonical_json(decoded)
        return decoded
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise WorkerError(409, 'legacy_archive_invalid', 'Historical execution data could not be verified') from None


def _wire(value):
    try:
        raw = canonical_json(value)
        _require(len(raw.encode('utf-8')) <= MAX_RECORD_BYTES)
        return raw
    except (ContractError, RecursionError, UnicodeError):
        raise WorkerError(409, 'legacy_archive_invalid', 'Historical execution data could not be verified') from None


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _query(database, statement):
    try:
        return database._query(statement)
    except Exception:
        raise WorkerError(503, 'legacy_archive_unavailable', 'Historical execution storage is unavailable', True) from None


def _execute(database, statement):
    try:
        return database._execute(statement)
    except Exception:
        raise WorkerError(503, 'legacy_archive_unavailable', 'Historical execution storage is unavailable', True) from None


def _index(database, task_id):
    rows = _query(database, 'SELECT id,user_id,job FROM scientific_pi_index WHERE id=' + sql(task_id))
    if not rows:
        raise WorkerError(404, 'task_not_found', 'Historical task not found')
    row = rows[0]
    job = _json(row['job'])
    _require(type(job) is dict and job.get('task_id') == task_id
        and job.get('user_id') == row['user_id'])
    # The historical table is also used by the current Agent adapter. A current
    # descriptor is never eligible for import or for this legacy read path.
    if job.get('runtime_revision') == 'agent-task-contract-v1':
        raise WorkerError(404, 'task_not_found', 'No historical execution mapping exists')
    return row, job


def _entry(value, *, manifest=False):
    _require(type(value) is dict)
    try:
        item = {key: value[key] for key in PUBLIC_FIELDS}
    except KeyError:
        _require(False)
    _require(type(item['artifact_id']) is str
        and item['artifact_id'] in (PUBLIC_IDS | {'manifest'} if manifest else PUBLIC_IDS))
    _require(type(item['media_type']) is str
        and re.fullmatch(r'[A-Za-z0-9.+_-]+/[A-Za-z0-9.+_-]+', item['media_type']) is not None)
    maximum = MAX_MANIFEST_BYTES if item['artifact_id'] == 'manifest' else MAX_ARTIFACT_BYTES
    _require(type(item['size']) is int and 0 <= item['size'] <= maximum)
    _require(type(item['sha256']) is str and re.fullmatch(r'[0-9a-f]{64}', item['sha256']) is not None)
    return item


def _entries(values, *, manifest=False):
    _require(type(values) is list and len(values) <= len(PUBLIC_IDS) + int(manifest))
    result = [_entry(item, manifest=manifest) for item in values]
    _require(len({item['artifact_id'] for item in result}) == len(result)
        and sum(item['size'] for item in result) <= MAX_ARTIFACT_BYTES + MAX_MANIFEST_BYTES)
    return result


def _snapshot(source, task_id, job):
    _require(type(source) is dict and source.get('id') == task_id)
    _require(type(source.get('state')) is str and source['state'] in TERMINAL_STATES, 'legacy_task_not_terminal')
    _require(source.get('topic') == job['topic'] and source.get('mode') == job['mode']
        and source.get('created_at') == job['created_at']
        and source.get('execution_deadline_at') == job['execution_deadline_at'])
    snapshot = {key: value for key, value in source.items() if key in SNAPSHOT_FIELDS}
    # Preserve state, timestamps and identifiers, but never archive arbitrary
    # error messages containing environment values or internal paths.
    code = snapshot.get('error_code')
    if not isinstance(code, str) or re.fullmatch(r'[a-z][a-z0-9_]{0,79}', code) is None:
        code = None
    snapshot['error_code'] = code
    snapshot['error'] = code if source.get('error') else None
    # Worker snapshots contain no report body; content comes from its hashed
    # result and the existing scientific_reports table.
    snapshot['report'] = None
    return snapshot


def _validate_result(result, snapshot):
    _require(type(result) is dict and result.get('task_id') == snapshot['id']
        and result.get('attempt') == snapshot['attempt'])
    try:
        _require(type(result.get('report_markdown')) is str
            and len(result['report_markdown'].encode('utf-8')) <= MAX_REPORT_BYTES
            and report_sha256(result['report_markdown']) == result['report_sha256']
            and result_digest(result) == result['result_digest']
            and snapshot.get('result_digest') == result['result_digest']
            and snapshot.get('report_sha256') == result['report_sha256'])
    except (KeyError, TypeError, ContractError, UnicodeError, RecursionError):
        _require(False)
    entries = _entries(result['artifacts'])
    _require(PUBLIC_IDS - {'revision_output'} <= {entry['artifact_id'] for entry in entries})
    return entries


class RetiredPiArchive:
    """PiClient-shaped, read-only view; identity checks remain in the API."""

    def __init__(self, database):
        self.db = database

    def initialize(self):
        _execute(self.db, f'''CREATE TABLE IF NOT EXISTS {TASKS} (
            task_id TEXT PRIMARY KEY, record_json TEXT NOT NULL,
            record_sha256 TEXT NOT NULL, committed INTEGER NOT NULL DEFAULT 0
                CHECK(committed IN (0,1)), archived_at TEXT NOT NULL)''')
        _execute(self.db, f'''CREATE TABLE IF NOT EXISTS {FILES} (
            task_id TEXT NOT NULL, artifact_id TEXT NOT NULL, media_type TEXT NOT NULL,
            byte_size INTEGER NOT NULL, sha256 TEXT NOT NULL, body_base64 TEXT NOT NULL,
            PRIMARY KEY(task_id,artifact_id))''')

    def _record(self, task_id):
        task_id = _task_id(task_id)
        index, job = _index(self.db, task_id)
        rows = _query(self.db, f'SELECT * FROM {TASKS} WHERE task_id=' + sql(task_id))
        if not rows or rows[0]['committed'] != 1:
            _retired()
        row = rows[0]
        _require(type(row['record_json']) is str
            and len(row['record_json'].encode('utf-8')) <= MAX_RECORD_BYTES
            and _sha(row['record_json'].encode('utf-8')) == row['record_sha256'])
        record = _json(row['record_json'])
        _require(type(record) is dict and record.get('schema_version') == 'retired-pi.v1'
            and record.get('source_job_sha256') == _sha(index['job'].encode('utf-8')))
        _require(record.get('task_id') == task_id and type(record.get('snapshot')) is dict
            and record['snapshot'].get('id') == task_id
            and record['snapshot'].get('state') in TERMINAL_STATES)
        _snapshot(record['snapshot'], task_id, job)
        _require(record.get('artifacts_state') in ('available', 'expired', 'not_ready'))
        files = _entries(record.get('files'), manifest=True)
        public = _entries(record.get('artifacts'))
        if record['snapshot']['state'] == 'succeeded':
            _require(record['artifacts_state'] != 'not_ready')
            if record.get('result') is not None:
                expected = _validate_result(record['result'], record['snapshot'])
                _require(record['artifacts_state'] == 'expired' or public == expected)
            else:
                _require(record.get('result_expired') is True)
        else:
            _require(record.get('result') is None and record['artifacts_state'] == 'not_ready')
        if record['artifacts_state'] == 'available':
            _require({item['artifact_id'] for item in files} ==
                {item['artifact_id'] for item in public} | {'manifest'})
            _require({item['artifact_id']: item for item in files if item['artifact_id'] != 'manifest'} ==
                {item['artifact_id']: item for item in public})
        else:
            _require(files == [])
        return record

    def get(self, task_id):
        return self._record(task_id)['snapshot']

    def result(self, task_id):
        record = self._record(task_id)
        if record['result'] is None:
            if record.get('result_expired'):
                raise WorkerError(410, 'artifact_expired', 'Historical result has expired')
            raise WorkerError(409, 'result_not_ready', 'This historical task has no validated result')
        return record['result']

    @staticmethod
    def _available(record):
        if record['artifacts_state'] == 'expired':
            raise WorkerError(410, 'artifact_expired', 'Historical artifacts have expired')
        if record['artifacts_state'] != 'available':
            raise WorkerError(409, 'result_not_ready', 'This historical task has no validated artifacts')

    def artifacts(self, task_id):
        record = self._record(task_id)
        self._available(record)
        return {'items': record['artifacts']}

    def artifact(self, task_id, artifact_id, max_bytes=None):
        if type(artifact_id) is not str or re.fullmatch(r'[A-Za-z0-9_-]{1,80}', artifact_id) is None:
            raise WorkerError(422, 'invalid_artifact', 'Invalid artifact identifier')
        record = self._record(task_id)
        self._available(record)
        item = next((entry for entry in record['files'] if entry['artifact_id'] == artifact_id), None)
        if item is None:
            raise WorkerError(404, 'artifact_not_found', 'Historical artifact not found')
        if max_bytes is not None:
            _require(type(max_bytes) is int and 0 <= max_bytes <= MAX_ARTIFACT_BYTES,
                'invalid_artifact_limit', 422)
            _require(item['size'] <= max_bytes, 'artifact_size_mismatch', 502)
        return self._file(task_id, item), {'Content-Type': item['media_type']}

    def _file(self, task_id, item):
        rows = _query(self.db, f'SELECT * FROM {FILES} WHERE task_id=' + sql(task_id)
            + ' AND artifact_id=' + sql(item['artifact_id']))
        _require(len(rows) == 1, 'legacy_archive_corrupt')
        row = rows[0]
        _require((row['media_type'], row['byte_size'], row['sha256']) ==
            (item['media_type'], item['size'], item['sha256']), 'legacy_archive_corrupt')
        encoded = row['body_base64']
        _require(type(encoded) is str and len(encoded) == 4 * ((item['size'] + 2) // 3),
            'legacy_archive_corrupt')
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            _require(False, 'legacy_archive_corrupt')
        _require(len(raw) == item['size'] and _sha(raw) == item['sha256'], 'legacy_archive_corrupt')
        return raw

    def pending(self, cursor=None):
        return {'items': [], 'next_cursor': None}

    def cancel(self, task_id):
        # Cancellation of an already terminal archived task is an exact no-op.
        return self.get(task_id)

    def put(self, job):
        _retired()

    def put_revision(self, job, material):
        _retired()

    def ack(self, task_id, payload):
        _retired()


def _expired(error):
    return isinstance(error, WorkerError) and error.status == 410 and error.code == 'artifact_expired'


def _source(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except WorkerError as exc:
        code = exc.code if isinstance(exc.code, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,79}', exc.code) else 'legacy_source_unavailable'
        raise WorkerError(exc.status, code, 'The historical source could not provide verified data', exc.retryable) from None
    except Exception:
        raise WorkerError(503, 'legacy_source_unavailable', 'The historical source is unavailable', True) from None


def _collect(worker, task_id, index, job, source):
    snapshot = _snapshot(source, task_id, job)
    result, result_expired, entries, files, contents = None, False, [], [], {}
    artifacts_state = 'not_ready'
    if snapshot['state'] == 'succeeded':
        try:
            result = _source(worker.result, task_id)
        except WorkerError as exc:
            if not _expired(exc):
                raise
            result_expired = True
        expected = _validate_result(result, snapshot) if result is not None else None
        try:
            page = _source(worker.artifacts, task_id)
            _require(type(page) is dict)
            entries = _entries(page.get('items'))
            _require(expected is not None and entries == expected)
            for item in entries:
                raw, _ = _source(worker.artifact, task_id, item['artifact_id'], max_bytes=item['size'])
                _require(type(raw) is bytes and len(raw) == item['size'] and _sha(raw) == item['sha256'])
                if item['artifact_id'] == 'report':
                    _require(raw == result['report_markdown'].encode('utf-8'))
                contents[item['artifact_id']] = raw
            raw, _ = _source(worker.artifact, task_id, 'manifest', max_bytes=MAX_MANIFEST_BYTES)
            _require(type(raw) is bytes and len(raw) <= MAX_MANIFEST_BYTES)
            manifest = _json(raw)
            _require(type(manifest) is dict and manifest == result.get('manifest')
                and manifest.get('schema_version') == '1' and manifest.get('task_id') == task_id
                and manifest.get('attempt') == snapshot['attempt']
                and manifest.get('result_digest') == result['result_digest']
                and _entries(manifest.get('artifacts')) == expected
                and manifest.get('provenance') == result.get('provenance'))
            contents['manifest'] = raw
            files = entries + [dict(artifact_id='manifest', media_type='application/json',
                size=len(raw), sha256=_sha(raw))]
            artifacts_state = 'available'
        except WorkerError as exc:
            if not _expired(exc):
                raise
            # A genuine expiry may race a list/download. Preserve the observed
            # 410, never manufacture an empty successful attachment listing.
            artifacts_state, files, contents = 'expired', [], {}
    else:
        for operation in (worker.result, worker.artifacts):
            try:
                _source(operation, task_id)
            except WorkerError as exc:
                _require(exc.status == 409 and exc.code == 'result_not_ready')
            else:
                _require(False)
    _require(_wire(_source(worker.get, task_id)) == _wire(source), 'legacy_source_changed')
    record = dict(schema_version='retired-pi.v1', task_id=task_id,
        source_job_sha256=_sha(index['job'].encode('utf-8')),
        source_snapshot_sha256=_sha(_wire(source).encode('utf-8')), snapshot=snapshot,
        result=result, result_expired=result_expired, artifacts_state=artifacts_state,
        artifacts=entries, files=files)
    return record, contents


def archive_from_worker(database, worker, task_ids):
    """Explicit operator import; returns receipts, never changes source records.

    All previously unarchived source tasks must first be confirmed terminal.
    Complete records are replayed from the archive without calling the source.
    A later failure can leave an uncommitted task, which readers return as 410;
    retry the same import before removing the source. Explicit source expiry is
    retained as expiry and does not require restoring or fabricating files.
    """
    ids = [_task_id(task_id) for task_id in task_ids]
    _require(1 <= len(ids) <= 1000 and len(set(ids)) == len(ids), 'invalid_archive_task_ids', 422)
    archive = RetiredPiArchive(database)
    archive.initialize()
    prepared, receipts = [], []
    for task_id in ids:
        index, job = _index(database, task_id)
        rows = _query(database, f'SELECT committed FROM {TASKS} WHERE task_id=' + sql(task_id))
        if rows and rows[0]['committed'] == 1:
            record = archive._record(task_id)
            for item in record['files']:
                archive._file(task_id, item)
            receipts.append(_receipt(record))
            continue
        source = _source(worker.get, task_id)
        _snapshot(source, task_id, job)
        prepared.append((task_id, index, job, source))
    for task_id, index, job, source in prepared:
        record, contents = _collect(worker, task_id, index, job, source)
        encoded = _wire(record)
        digest = _sha(encoded.encode('utf-8'))
        _execute(database, f'INSERT INTO {TASKS} (task_id,record_json,record_sha256,archived_at) VALUES ('
            + ','.join(map(sql, (task_id, encoded, digest, utc_now()))) + ') ON CONFLICT(task_id) DO NOTHING')
        rows = _query(database, f'SELECT record_json,record_sha256 FROM {TASKS} WHERE task_id=' + sql(task_id))
        _require(len(rows) == 1 and rows[0]['record_json'] == encoded
            and rows[0]['record_sha256'] == digest, 'legacy_archive_conflict')
        for item in record['files']:
            raw = contents[item['artifact_id']]
            values = (task_id, item['artifact_id'], item['media_type'], item['size'],
                item['sha256'], base64.b64encode(raw).decode('ascii'))
            _execute(database, f'INSERT INTO {FILES} (task_id,artifact_id,media_type,byte_size,sha256,body_base64) VALUES ('
                + ','.join(map(sql, values)) + ') ON CONFLICT(task_id,artifact_id) DO NOTHING')
            _require(archive._file(task_id, item) == raw, 'legacy_archive_conflict')
        # Last write publishes one complete task. It cannot expose partial files
        # even when the HTTP SQLite service commits each statement separately.
        _execute(database, f'UPDATE {TASKS} SET committed=1 WHERE task_id=' + sql(task_id)
            + ' AND record_sha256=' + sql(digest) + ' AND committed=0')
        committed = archive._record(task_id)
        _require(committed == record, 'legacy_archive_conflict')
        receipts.append(_receipt(committed))
    return {'schema_version': 'retired-pi-import.v1', 'task_count': len(receipts),
        'tasks': sorted(receipts, key=lambda row: row['task_id'])}


def _receipt(record):
    return dict(task_id=record['task_id'], state=record['snapshot']['state'],
        artifacts_state=record['artifacts_state'], artifact_count=len(record['files']),
        archived_bytes=sum(item['size'] for item in record['files']),
        record_sha256=_sha(_wire(record).encode('utf-8')))
