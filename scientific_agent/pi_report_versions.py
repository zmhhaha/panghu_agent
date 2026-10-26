"""Append-only report versions, bounded evidence snapshots and revision intents.

The shared SQL HTTP adapter commits one statement per request. Every admission
condition is therefore inside its INSERT SELECT, with a stable ID and readback
for retries after an ambiguous commit. This module never edits original reports
and never starts a worker. Expired snapshot payloads leave idempotency tombstones.
"""
from __future__ import annotations

import base64
import difflib
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import uuid

from .pi_citations import build_citation_trace, validate_manifest, TraceUnavailable
from .pi_sections import select_section, SectionError

MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
NAMESPACE = uuid.UUID('8d896b64-dccd-4ad4-9501-609cac8db1df')
_VERSION_COLUMNS = 'version_id,owner,root_report_id,source_report_id,parent_version_id,report_sha256,content_bytes,topic,created_at'
_SNAPSHOT_COLUMNS = 'snapshot_id,owner,version_id,idempotency_key,request_sha256,report_sha256,payload_bytes,payload_sha256,created_at,expires_at,purged_at'
_INTENT_COLUMNS = 'intent_id,owner,parent_version_id,root_report_id,snapshot_id,base_sha256,section_id,section_sha256,instruction,timeout_seconds,idempotency_key,request_sha256,status,created_at,expires_at'
MAX_SELECTION_REVISION = 2**53 - 1
MAX_DIFF_INPUT_BYTES = 128 * 1024
MAX_DIFF_OUTPUT_BYTES = 256 * 1024
MAX_DIFF_LINES = 2000


class VersionError(ValueError):
    def __init__(self, code, message='Report version operation could not be completed.', status=422):
        self.code, self.status = code, status
        super().__init__(message)


def _require(condition, code='invalid_version_request', status=422):
    if not condition:
        raise VersionError(code, status=status)


def _sql(value):
    return 'NULL' if value is None else "'" + str(value).replace("'", "''") + "'"


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _owner(value):
    _require(isinstance(value, str) and bool(value.strip()) and len(value) <= 200
             and not any(ord(c) < 32 or ord(c) == 127 for c in value))
    return value.strip()


def _identifier(value):
    try:
        _require(isinstance(value, str) and str(uuid.UUID(value)) == value)
    except (ValueError, AttributeError):
        raise VersionError('invalid_version_request') from None
    return value


def _digest(value):
    _require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None)
    return value


def _key(value):
    _require(isinstance(value, str) and 1 <= len(value) <= 128
             and all(32 <= ord(c) <= 126 for c in value))
    return value


def _stable(kind, owner, key):
    return str(uuid.uuid5(NAMESPACE, _json([kind, owner, key])))


def _content(value):
    try:
        _require(isinstance(value, str) and bool(value.strip()))
        raw = value.encode('utf-8')
    except UnicodeError:
        raise VersionError('invalid_version_request') from None
    _require(len(raw) <= MAX_REPORT_BYTES, 'report_too_large')
    return raw


def _decode(value, code='version_integrity_error'):
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        raise VersionError(code, status=409) from None


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'snapshot_integrity_error', 409)
        result[key] = value
    return result


class ReportVersionStore:
    def __init__(self, db, clock=None, snapshot_ttl=30 * 86400, intent_ttl=86400,
                 owner_snapshot_bytes=64 * 1024 * 1024, global_snapshot_bytes=256 * 1024 * 1024,
                 owner_snapshot_count=10, owner_version_count=100,
                 owner_version_bytes=32 * 1024 * 1024, global_version_bytes=256 * 1024 * 1024,
                 owner_intent_count=10000, owner_live_intent_count=100,
                 owner_adoption_count=10000, global_adoption_count=100000):
        self.db, self.clock = db, clock or (lambda: datetime.now(timezone.utc))
        values = locals().copy()
        for name in ('snapshot_ttl', 'intent_ttl', 'owner_snapshot_bytes', 'global_snapshot_bytes',
                     'owner_snapshot_count', 'owner_version_count', 'owner_version_bytes',
                     'global_version_bytes', 'owner_intent_count', 'owner_live_intent_count',
                     'owner_adoption_count', 'global_adoption_count'):
            value = values[name]
            _require(type(value) is int and value > 0)
            setattr(self, name, value)

    def _time(self):
        value = self.clock()
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.replace('Z', '+00:00'))
            except ValueError:
                raise VersionError('invalid_clock') from None
        _require(isinstance(value, datetime) and value.tzinfo is not None, 'invalid_clock')
        return value.astimezone(timezone.utc)

    @staticmethod
    def _stamp(value):
        return value.strftime('%Y-%m-%dT%H:%M:%S.%fZ')

    def initialize(self):
        self.db._execute('''CREATE TABLE IF NOT EXISTS scientific_report_versions (
            version_id TEXT PRIMARY KEY, owner TEXT NOT NULL, root_report_id TEXT NOT NULL,
            source_report_id TEXT NOT NULL, parent_version_id TEXT, report_sha256 TEXT NOT NULL,
            content_b64 TEXT NOT NULL, content_bytes INTEGER NOT NULL, topic TEXT NOT NULL,
            created_at TEXT NOT NULL, UNIQUE(owner,source_report_id))''')
        self.db._execute('''CREATE TABLE IF NOT EXISTS scientific_report_snapshots (
            snapshot_id TEXT PRIMARY KEY, owner TEXT NOT NULL, version_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL, request_sha256 TEXT NOT NULL, report_sha256 TEXT NOT NULL,
            payload TEXT, payload_bytes INTEGER NOT NULL, payload_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL, expires_at TEXT NOT NULL, purged_at TEXT, purge_token TEXT,
            UNIQUE(owner,idempotency_key))''')
        self.db._execute('''CREATE TABLE IF NOT EXISTS scientific_report_revision_intents (
            intent_id TEXT PRIMARY KEY, owner TEXT NOT NULL, parent_version_id TEXT NOT NULL,
            root_report_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, base_sha256 TEXT NOT NULL,
            section_id TEXT NOT NULL, section_sha256 TEXT NOT NULL, instruction TEXT NOT NULL,
            timeout_seconds INTEGER NOT NULL, idempotency_key TEXT NOT NULL, request_sha256 TEXT NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
            UNIQUE(owner,idempotency_key))''')
        self.db._execute('CREATE INDEX IF NOT EXISTS scientific_report_versions_owner_root ON scientific_report_versions(owner,root_report_id,created_at,version_id)')
        self.db._execute('CREATE INDEX IF NOT EXISTS scientific_report_snapshots_owner ON scientific_report_snapshots(owner,expires_at)')
        self.db._execute('CREATE INDEX IF NOT EXISTS scientific_revision_intents_snapshot ON scientific_report_revision_intents(snapshot_id,owner,status,expires_at)')
        self.db._execute('''CREATE TABLE IF NOT EXISTS scientific_report_revision_jobs (
            task_id TEXT PRIMARY KEY, owner TEXT NOT NULL, intent_id TEXT NOT NULL,
            snapshot_id TEXT NOT NULL, request_sha256 TEXT NOT NULL, job TEXT NOT NULL,
            status TEXT NOT NULL, candidate_version_id TEXT, result_digest TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(owner,intent_id))''')
        self.db._execute('CREATE INDEX IF NOT EXISTS scientific_revision_jobs_active ON scientific_report_revision_jobs(status,owner,snapshot_id)')
        # The append-only event is both the selection head and idempotency
        # receipt. One INSERT SELECT commits the CAS and its retry record.
        self.db._execute('''CREATE TABLE IF NOT EXISTS scientific_report_adoptions (
            adoption_id TEXT PRIMARY KEY, owner TEXT NOT NULL, root_report_id TEXT NOT NULL,
            version_id TEXT NOT NULL, report_sha256 TEXT NOT NULL, parent_version_id TEXT NOT NULL,
            base_sha256 TEXT NOT NULL, expected_current_version_id TEXT,
            expected_selection_revision INTEGER NOT NULL, selection_revision INTEGER NOT NULL,
            idempotency_key TEXT NOT NULL, request_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(owner,idempotency_key), UNIQUE(owner,root_report_id,selection_revision))''')

    def _version(self, owner, version_id):
        rows = self.db._query('SELECT * FROM scientific_report_versions WHERE owner=' + _sql(owner) + ' AND version_id=' + _sql(version_id))
        if not rows:
            raise VersionError('version_not_found', status=404)
        row = rows[0]
        raw = _decode(row['content_b64'])
        _require(len(raw) == row['content_bytes'] and len(raw) <= MAX_REPORT_BYTES
                 and _sha(raw) == row['report_sha256'], 'version_integrity_error', 409)
        try:
            row = dict(row, content=raw.decode('utf-8'))
        except UnicodeError:
            raise VersionError('version_integrity_error', status=409) from None
        return row

    @staticmethod
    def _version_meta(row):
        return {key: row[key] for key in _VERSION_COLUMNS.split(',') if key != 'owner'}

    @staticmethod
    def _unfulfilled_reservations(owner=None, consuming_owner=None, consuming_source=None):
        """Count active promises not yet represented by an actual version.

        Source identity consumes the reservation in the version INSERT itself;
        neither a later status update nor its successful HTTP response is needed.
        """
        condition = "j.status IN ('reserved','queued','running','validating','cancel_requested','publishing')"
        condition += ' AND NOT EXISTS (SELECT 1 FROM scientific_report_versions v WHERE v.owner=j.owner AND v.source_report_id=j.task_id)'
        if owner is not None:
            condition += ' AND j.owner=' + _sql(owner)
        if consuming_source is not None:
            condition += ' AND NOT (j.owner=' + _sql(consuming_owner) + ' AND j.task_id=' + _sql(consuming_source) + ')'
        return '(SELECT COUNT(*) FROM scientific_report_revision_jobs j WHERE ' + condition + ')'

    def _version_admission(self, owner, added_bytes, consuming_source=None):
        own = self._unfulfilled_reservations(owner,owner,consuming_source)
        total = self._unfulfilled_reservations(None,owner,consuming_source)
        return ('(SELECT COUNT(*) FROM scientific_report_versions WHERE owner=' + _sql(owner) + ')+' + own + '+1<=' + str(self.owner_version_count) +
                ' AND (SELECT COALESCE(SUM(content_bytes),0) FROM scientific_report_versions WHERE owner=' + _sql(owner) + ')+' + own + '*' + str(MAX_REPORT_BYTES) + '+' + str(added_bytes) + '<=' + str(self.owner_version_bytes) +
                ' AND (SELECT COALESCE(SUM(content_bytes),0) FROM scientific_report_versions)+' + total + '*' + str(MAX_REPORT_BYTES) + '+' + str(added_bytes) + '<=' + str(self.global_version_bytes))

    def ensure_base(self, owner, source_report_id, content, topic=''):
        owner, source_report_id = _owner(owner), _identifier(source_report_id)
        raw = _content(content)
        _require(isinstance(topic, str) and len(topic) <= 4000 and '\x00' not in topic)
        digest, now = _sha(raw), self._stamp(self._time())
        version_id = _stable('base', owner, source_report_id)
        values = [version_id, owner, source_report_id, source_report_id, None, digest,
                  base64.b64encode(raw).decode('ascii'), len(raw), topic, now]
        existing = self.db._query('SELECT version_id FROM scientific_report_versions WHERE owner=' + _sql(owner) + ' AND source_report_id=' + _sql(source_report_id))
        if existing:
            # A published candidate can also be opened by its report/task ID.
            # Reuse its recorded lineage rather than re-importing it as a root.
            version_id = existing[0]['version_id']
        else:
            self.db._execute('INSERT INTO scientific_report_versions (' + _VERSION_COLUMNS.replace('content_bytes,topic', 'content_b64,content_bytes,topic') + ') SELECT ' + ','.join(map(_sql, values)) +
                ' WHERE ' + self._version_admission(owner,len(raw)) +
                ' AND NOT EXISTS (SELECT 1 FROM scientific_report_revision_jobs WHERE owner=' + _sql(owner) + ' AND task_id=' + _sql(source_report_id) + ')' +
                ' ON CONFLICT DO NOTHING')
            existing = self.db._query('SELECT version_id FROM scientific_report_versions WHERE owner=' + _sql(owner) + ' AND source_report_id=' + _sql(source_report_id))
            if existing:
                version_id = existing[0]['version_id']
        try:
            row = self._version(owner, version_id)
        except VersionError as exc:
            if exc.code == 'version_not_found':
                bound = self.db._query('SELECT task_id FROM scientific_report_revision_jobs WHERE owner=' + _sql(owner) + ' AND task_id=' + _sql(source_report_id))
                _require(not bound,'reserved_source_conflict',409)
                raise VersionError('version_quota_exceeded', status=429) from None
            raise
        _require(row['report_sha256'] == digest and row['content'] == content,
                 'base_report_conflict', 409)
        return self._version_meta(row)

    def append_candidate(self, owner, parent_version_id, base_sha256, content, key,
                         *, source_report_id, topic=None):
        """Register an internally published report without changing its parent.

        ``source_report_id`` is the actual published report/task UUID, supplied
        by the revision worker. This method neither publishes reports nor accepts
        arbitrary client-written drafts; the API must not expose it as a body
        upload endpoint. Stable owner/key IDs recover a lost commit response.
        """
        owner, parent_version_id = _owner(owner), _identifier(parent_version_id)
        source_report_id, key = _identifier(source_report_id), _key(key)
        base_sha256, raw = _digest(base_sha256), _content(content)
        parent = self._version(owner, parent_version_id)
        _require(parent['report_sha256'] == base_sha256, 'stale_report', 409)
        topic = parent['topic'] if topic is None else topic
        _require(isinstance(topic, str) and len(topic) <= 4000 and '\x00' not in topic)
        digest, now = _sha(raw), self._stamp(self._time())
        version_id = _stable('candidate', owner, key)
        existing = self.db._query('SELECT version_id FROM scientific_report_versions WHERE owner=' + _sql(owner) + ' AND version_id=' + _sql(version_id))
        if not existing:
            binding = self.get_revision_job(owner,source_report_id)
            if binding:
                fixed = json.loads(binding['job'])
                _require(binding['status'] in ('reserved','queued','running','validating','cancel_requested','publishing')
                         and fixed['parent_version_id'] == parent_version_id
                         and fixed['base_sha256'] == base_sha256
                         and fixed['root_report_id'] == parent['root_report_id'], 'revision_job_conflict',409)
            values = [version_id, owner, parent['root_report_id'], source_report_id, parent_version_id, digest,
                      base64.b64encode(raw).decode('ascii'), len(raw), topic, now]
            reservation_gate = (' AND EXISTS (SELECT 1 FROM scientific_report_revision_jobs WHERE owner=' + _sql(owner) +
                ' AND task_id=' + _sql(source_report_id) + " AND status IN ('reserved','queued','running','validating','cancel_requested','publishing'))") if binding else (
                ' AND NOT EXISTS (SELECT 1 FROM scientific_report_revision_jobs WHERE owner=' + _sql(owner) + ' AND task_id=' + _sql(source_report_id) + ')')
            self.db._execute('INSERT INTO scientific_report_versions (' + _VERSION_COLUMNS.replace('content_bytes,topic', 'content_b64,content_bytes,topic') + ') SELECT ' + ','.join(map(_sql, values)) +
                ' WHERE EXISTS (SELECT 1 FROM scientific_report_versions WHERE owner=' + _sql(owner) + ' AND version_id=' + _sql(parent_version_id) + ' AND root_report_id=' + _sql(parent['root_report_id']) + ' AND report_sha256=' + _sql(base_sha256) + ')' +
                ' AND ' + self._version_admission(owner,len(raw),source_report_id if binding else None) + reservation_gate +
                ' ON CONFLICT DO NOTHING')
        try:
            row = self._version(owner, version_id)
        except VersionError as exc:
            if exc.code != 'version_not_found':
                raise
            source = self.db._query('SELECT version_id FROM scientific_report_versions WHERE owner=' + _sql(owner) + ' AND source_report_id=' + _sql(source_report_id))
            _require(not source, 'candidate_source_conflict', 409)
            current = self._version(owner, parent_version_id)
            _require(current['report_sha256'] == base_sha256 and current['root_report_id'] == parent['root_report_id'], 'stale_report', 409)
            raise VersionError('version_quota_exceeded', status=429) from None
        _require(row['parent_version_id'] == parent_version_id and row['root_report_id'] == parent['root_report_id']
                 and row['source_report_id'] == source_report_id and row['report_sha256'] == digest
                 and row['content'] == content and row['topic'] == topic, 'candidate_key_conflict', 409)
        return self._version_meta(row)

    def list_versions(self, owner, root_report_id, limit=50, offset=0):
        owner, root_report_id = _owner(owner), _identifier(root_report_id)
        _require(type(limit) is int and 1 <= limit <= 100 and type(offset) is int and 0 <= offset <= 100000)
        sources = self.db._query('SELECT root_report_id FROM scientific_report_versions WHERE owner=' + _sql(owner) + ' AND source_report_id=' + _sql(root_report_id))
        if sources:
            root_report_id = sources[0]['root_report_id']
        rows = self.db._query('SELECT ' + _VERSION_COLUMNS + ' FROM scientific_report_versions WHERE owner=' + _sql(owner) + ' AND root_report_id=' + _sql(root_report_id) +
                              ' ORDER BY created_at,version_id LIMIT ' + str(limit) + ' OFFSET ' + str(offset))
        return [self._version_meta(row) for row in rows]

    def get_version(self, owner, version_id, include_content=False):
        row = self._version(_owner(owner), _identifier(version_id))
        result = self._version_meta(row)
        if include_content:
            result['content'] = row['content']
        return result

    @staticmethod
    def _snapshot_meta(row, pinned=False):
        result = {key: row[key] for key in _SNAPSHOT_COLUMNS.split(',') if key not in ('owner', 'idempotency_key', 'request_sha256')}
        result.update(scope='recorded_evidence_only', pinned=pinned)
        return result

    def _snapshot_row(self, owner, snapshot_id):
        rows = self.db._query('SELECT * FROM scientific_report_snapshots WHERE owner=' + _sql(owner) + ' AND snapshot_id=' + _sql(snapshot_id))
        if not rows:
            raise VersionError('snapshot_not_found', status=404)
        return rows[0]

    def _snapshot_live(self, row, now, allow_pin=True):
        pinned = False
        if row['expires_at'] <= now:
            if allow_pin:
                pinned = bool(self.db._query("SELECT intent_id FROM scientific_report_revision_intents WHERE owner=" + _sql(row['owner']) + ' AND snapshot_id=' + _sql(row['snapshot_id']) +
                                            " AND status IN ('prepared','queued','running','validating','publishing') AND expires_at>" + _sql(now) + ' LIMIT 1'))
                pinned = pinned or bool(self.db._query('SELECT task_id FROM scientific_report_revision_jobs WHERE owner=' + _sql(row['owner']) +
                    ' AND snapshot_id=' + _sql(row['snapshot_id']) + " AND status IN ('reserved','queued','running','validating','cancel_requested','publishing') LIMIT 1"))
            if not pinned:
                raise VersionError('snapshot_expired', status=410)
        _require(row['payload'] is not None, 'snapshot_integrity_error', 409)
        return pinned

    def save_snapshot(self, owner, version_id, items, artifacts, key):
        owner, version_id, key = _owner(owner), _identifier(version_id), _key(key)
        version = self._version(owner, version_id)
        try:
            manifest = validate_manifest(version['content'], items)
            build_citation_trace(version['content'], items, artifacts)
        except TraceUnavailable as exc:
            raise VersionError(exc.code, status=409) from None
        selected = []
        for item in items:
            if item['artifact_id'] in ('report',) + tuple(manifest):
                selected.append({k: item[k] for k in ('artifact_id', 'media_type', 'size', 'sha256')})
        selected.sort(key=lambda item: item['artifact_id'])
        payload = _json(dict(schema_version='1', scope='recorded_evidence_only', version_id=version_id,
                             report=version['content'], report_sha256=version['report_sha256'], items=selected,
                             artifacts={name: base64.b64encode(artifacts[name]).decode('ascii') for name in manifest}))
        raw = payload.encode('utf-8')
        _require(len(raw) <= MAX_SNAPSHOT_BYTES, 'snapshot_too_large')
        digest = _sha(raw)
        now_dt = self._time()
        now, expiry = self._stamp(now_dt), self._stamp(now_dt + timedelta(seconds=self.snapshot_ttl))
        snapshot_id = _stable('snapshot', owner, key)
        rows = self.db._query('SELECT * FROM scientific_report_snapshots WHERE owner=' + _sql(owner) + ' AND idempotency_key=' + _sql(key))
        if not rows:
            values = [snapshot_id, owner, version_id, key, digest, version['report_sha256'], payload, len(raw), digest, now, expiry]
            self.db._execute('INSERT INTO scientific_report_snapshots (snapshot_id,owner,version_id,idempotency_key,request_sha256,report_sha256,payload,payload_bytes,payload_sha256,created_at,expires_at) SELECT ' + ','.join(map(_sql, values)) +
                ' WHERE EXISTS (SELECT 1 FROM scientific_report_versions WHERE owner=' + _sql(owner) + ' AND version_id=' + _sql(version_id) + ' AND report_sha256=' + _sql(version['report_sha256']) + ')' +
                ' AND (SELECT COUNT(*) FROM scientific_report_snapshots WHERE owner=' + _sql(owner) + ' AND payload IS NOT NULL)<' + str(self.owner_snapshot_count) +
                ' AND (SELECT COALESCE(SUM(payload_bytes),0) FROM scientific_report_snapshots WHERE owner=' + _sql(owner) + ' AND payload IS NOT NULL)+' + str(len(raw)) + '<=' + str(self.owner_snapshot_bytes) +
                ' AND (SELECT COALESCE(SUM(payload_bytes),0) FROM scientific_report_snapshots WHERE payload IS NOT NULL)+' + str(len(raw)) + '<=' + str(self.global_snapshot_bytes) +
                ' ON CONFLICT DO NOTHING')
        try:
            row = self._snapshot_row(owner, snapshot_id)
        except VersionError as exc:
            if exc.code == 'snapshot_not_found':
                raise VersionError('snapshot_quota_exceeded', status=429) from None
            raise
        self._snapshot_live(row, now, allow_pin=False)
        _require(row['request_sha256'] == digest and row['version_id'] == version_id,
                 'snapshot_key_conflict', 409)
        return self._snapshot_meta(row)

    def get_snapshot(self, owner, snapshot_id):
        owner, snapshot_id = _owner(owner), _identifier(snapshot_id)
        row = self._snapshot_row(owner, snapshot_id)
        pinned = self._snapshot_live(row, self._stamp(self._time()))
        try:
            payload_bytes = row['payload'].encode('utf-8')
            _require(len(payload_bytes) == row['payload_bytes'] and len(payload_bytes) <= MAX_SNAPSHOT_BYTES
                     and _sha(payload_bytes) == row['payload_sha256'] == row['request_sha256'], 'snapshot_integrity_error', 409)
            value = json.loads(row['payload'], object_pairs_hook=_json_pairs)
            _require(isinstance(value, dict) and set(value) == {'schema_version', 'scope', 'version_id', 'report', 'report_sha256', 'items', 'artifacts'}, 'snapshot_integrity_error', 409)
            _require(value['schema_version'] == '1' and value['scope'] == 'recorded_evidence_only'
                     and value['version_id'] == row['version_id'] and value['report_sha256'] == row['report_sha256'], 'snapshot_integrity_error', 409)
            version = self._version(owner, row['version_id'])
            _require(value['report'] == version['content'] and value['report_sha256'] == version['report_sha256'], 'snapshot_integrity_error', 409)
            _require(isinstance(value['artifacts'], dict) and set(value['artifacts']) == {'papers', 'evidence', 'claims', 'references'}, 'snapshot_integrity_error', 409)
            artifacts = {name: _decode(content, 'snapshot_integrity_error') for name, content in value['artifacts'].items()}
            build_citation_trace(value['report'], value['items'], artifacts)
        except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
            if isinstance(exc, VersionError):
                raise
            raise VersionError('snapshot_integrity_error', status=409) from None
        return dict(self._snapshot_meta(row, pinned=pinned), report=value['report'], items=value['items'], artifacts=artifacts)

    def get_snapshot_by_key(self, owner, key):
        owner, key = _owner(owner), _key(key)
        rows = self.db._query('SELECT snapshot_id FROM scientific_report_snapshots WHERE owner=' + _sql(owner) + ' AND idempotency_key=' + _sql(key))
        return self.get_snapshot(owner, rows[0]['snapshot_id']) if rows else None

    @staticmethod
    def _intent_meta(row):
        return {key: row[key] for key in _INTENT_COLUMNS.split(',') if key != 'owner'}

    def get_intent(self, owner, intent_id):
        owner, intent_id = _owner(owner), _identifier(intent_id)
        rows = self.db._query('SELECT ' + _INTENT_COLUMNS + ' FROM scientific_report_revision_intents WHERE owner=' + _sql(owner) + ' AND intent_id=' + _sql(intent_id))
        if not rows:
            raise VersionError('intent_not_found', status=404)
        row = rows[0]
        if row['expires_at'] <= self._stamp(self._time()):
            raise VersionError('intent_expired', status=410)
        return self._intent_meta(row)

    def find_intent(self, owner, key):
        owner, key = _owner(owner), _key(key)
        rows = self.db._query('SELECT intent_id FROM scientific_report_revision_intents WHERE owner=' + _sql(owner) + ' AND idempotency_key=' + _sql(key))
        return self.get_intent(owner, rows[0]['intent_id']) if rows else None

    def prepare_intent(self, owner, parent_version_id, base_sha256, section_id, section_sha256,
                       instruction, key, snapshot_id, timeout_seconds=1800):
        owner, parent_version_id = _owner(owner), _identifier(parent_version_id)
        base_sha256, section_sha256 = _digest(base_sha256), _digest(section_sha256)
        snapshot_id, key = _identifier(snapshot_id), _key(key)
        _require(isinstance(section_id, str) and 1 <= len(section_id) <= 256)
        _require(isinstance(instruction, str) and bool(instruction.strip()) and '\x00' not in instruction
                 and not any(0xD800 <= ord(c) <= 0xDFFF for c in instruction))
        _require(type(timeout_seconds) is int and 60 <= timeout_seconds <= 3600)
        instruction = instruction.strip()
        _require(len(instruction.encode('utf-8')) <= 8000)
        parent = self._version(owner, parent_version_id)
        try:
            section = select_section(parent['content'], section_id, base_sha256)
        except SectionError as exc:
            raise VersionError(exc.code, status=getattr(exc, 'status', 409)) from None
        _require(section['sha256'] == section_sha256, 'section_conflict', 409)
        fingerprint = _sha(_json(dict(owner=owner, parent_version_id=parent_version_id, base_sha256=base_sha256,
                                     section_id=section_id, section_sha256=section_sha256, instruction=instruction,
                                     snapshot_id=snapshot_id, timeout_seconds=timeout_seconds)).encode('utf-8'))
        intent_id = _stable('intent', owner, key)
        now_dt = self._time()
        now, expiry = self._stamp(now_dt), self._stamp(now_dt + timedelta(seconds=self.intent_ttl))
        existing = self.find_intent(owner, key)
        if existing:
            _require(existing['request_sha256'] == fingerprint, 'intent_key_conflict', 409)
            return existing
        snapshot = self.get_snapshot(owner, snapshot_id)
        _require(snapshot['version_id'] == parent_version_id and snapshot['report_sha256'] == base_sha256,
                 'snapshot_parent_conflict', 409)
        _require(snapshot['expires_at'] > now, 'snapshot_expired', 410)
        values = [intent_id, owner, parent_version_id, parent['root_report_id'], snapshot_id, base_sha256,
                  section_id, section_sha256, instruction, timeout_seconds, key, fingerprint, 'prepared', now, expiry]
        self.db._execute('INSERT INTO scientific_report_revision_intents (' + _INTENT_COLUMNS + ') SELECT ' + ','.join(map(_sql, values)) +
            ' WHERE EXISTS (SELECT 1 FROM scientific_report_versions WHERE owner=' + _sql(owner) + ' AND version_id=' + _sql(parent_version_id) + ' AND report_sha256=' + _sql(base_sha256) + ')' +
            ' AND EXISTS (SELECT 1 FROM scientific_report_snapshots WHERE owner=' + _sql(owner) + ' AND snapshot_id=' + _sql(snapshot_id) + ' AND version_id=' + _sql(parent_version_id) + ' AND report_sha256=' + _sql(base_sha256) + ' AND payload IS NOT NULL AND expires_at>' + _sql(now) + ')' +
            ' AND (SELECT COUNT(*) FROM scientific_report_revision_intents WHERE owner=' + _sql(owner) + ')<' + str(self.owner_intent_count) +
            " AND (SELECT COUNT(*) FROM scientific_report_revision_intents WHERE owner=" + _sql(owner) + " AND status IN ('prepared','queued','running','validating','publishing') AND expires_at>" + _sql(now) + ')<' + str(self.owner_live_intent_count) +
            ' ON CONFLICT DO NOTHING')
        try:
            result = self.get_intent(owner, intent_id)
        except VersionError as exc:
            if exc.code == 'intent_not_found':
                # Recheck expiry after the conditional INSERT for a cleanup race.
                current = self._snapshot_row(owner, snapshot_id)
                self._snapshot_live(current, self._stamp(self._time()), allow_pin=False)
                raise VersionError('intent_quota_exceeded', status=429) from None
            raise
        _require(result['request_sha256'] == fingerprint, 'intent_key_conflict', 409)
        return result

    def get_revision_job(self, owner, task_id):
        owner, task_id = _owner(owner), _identifier(task_id)
        rows = self.db._query('SELECT * FROM scientific_report_revision_jobs WHERE owner=' + _sql(owner) + ' AND task_id=' + _sql(task_id))
        return self._checked_revision_job(rows[0]) if rows else None

    def find_revision_job(self, owner, intent_id):
        owner, intent_id = _owner(owner), _identifier(intent_id)
        rows = self.db._query('SELECT * FROM scientific_report_revision_jobs WHERE owner=' + _sql(owner) + ' AND intent_id=' + _sql(intent_id))
        return self._checked_revision_job(rows[0]) if rows else None

    @staticmethod
    def _checked_revision_job(row):
        from .pi_revision_contracts import RevisionJob
        try:
            job = RevisionJob.from_dict(json.loads(row['job'])).to_dict()
            _require(job['task_id'] == row['task_id'] and job['user_id'] == row['owner']
                     and job['intent_id'] == row['intent_id']
                     and job['input_digest'] == row['request_sha256']
                     and row['job'] == _json(job), 'revision_job_integrity_error', 409)
        except (ValueError, TypeError, KeyError) as exc:
            raise VersionError('revision_job_integrity_error',status=409) from exc
        return row

    def active_revision_jobs(self):
        return self.db._query("SELECT * FROM scientific_report_revision_jobs WHERE status IN ('reserved','queued','running','validating','cancel_requested','publishing')")

    def reserve_revision_job(self, owner, intent_id, job):
        """Pin immutable input before the independently committed API index.

        The binding itself is recoverable if the next SQL HTTP request fails.
        Active bindings pin input beyond preview TTL; terminal tombstones cannot
        be restarted and do not retain expired payloads.
        """
        from .pi_revision_contracts import RevisionJob, validate_revision_materials
        owner, intent_id = _owner(owner), _identifier(intent_id)
        job = RevisionJob.from_dict(job).to_dict()
        existing = self.find_revision_job(owner, intent_id)
        if existing:
            _require(existing['request_sha256'] == job['input_digest'] and existing['task_id'] == job['task_id'], 'revision_job_conflict', 409)
            return existing
        intent = self.get_intent(owner, intent_id)
        _require(job['user_id'] == owner and job['intent_id'] == intent_id
                 and all(job[k] == intent[k] for k in ('parent_version_id','root_report_id','base_sha256',
                     'section_id','section_sha256','instruction','timeout_seconds')), 'revision_job_conflict', 409)
        material = self.get_snapshot(owner, intent['snapshot_id'])
        wire = dict(report=material['report'], items=material['items'],
                    artifacts={k:base64.b64encode(v).decode('ascii') for k,v in material['artifacts'].items()})
        validate_revision_materials(job, wire)
        now = self._stamp(self._time())
        values = [job['task_id'],owner,intent_id,intent['snapshot_id'],job['input_digest'],_json(job),'reserved',now,now]
        self.db._execute('INSERT INTO scientific_report_revision_jobs (task_id,owner,intent_id,snapshot_id,request_sha256,job,status,created_at,updated_at) SELECT ' + ','.join(map(_sql,values)) +
            ' WHERE EXISTS (SELECT 1 FROM scientific_report_revision_intents WHERE owner=' + _sql(owner) + ' AND intent_id=' + _sql(intent_id) + " AND status='prepared' AND expires_at>" + _sql(now) + ')' +
            ' AND EXISTS (SELECT 1 FROM scientific_report_snapshots WHERE owner=' + _sql(owner) + ' AND snapshot_id=' + _sql(intent['snapshot_id']) + ' AND payload IS NOT NULL)' +
            ' AND ' + self._version_admission(owner,MAX_REPORT_BYTES) +
            ' AND NOT EXISTS (SELECT 1 FROM scientific_report_versions WHERE owner=' + _sql(owner) + ' AND source_report_id=' + _sql(job['task_id']) + ')' +
            ' ON CONFLICT DO NOTHING')
        row = self.find_revision_job(owner, intent_id)
        if row is None:
            self.get_intent(owner,intent_id)
            self.get_snapshot(owner,intent['snapshot_id'])
            raise VersionError('version_quota_exceeded',status=429)
        _require(row['request_sha256'] == job['input_digest'] and row['task_id'] == job['task_id'], 'revision_job_conflict', 409)
        return row

    def revision_materials(self, owner, task_id):
        from .pi_revision_contracts import validate_revision_materials
        row = self.get_revision_job(owner, task_id)
        _require(row is not None, 'revision_job_not_found', 404)
        snapshot = self.get_snapshot(owner, row['snapshot_id'])
        wire = dict(report=snapshot['report'],items=snapshot['items'],
                    artifacts={k:base64.b64encode(v).decode('ascii') for k,v in snapshot['artifacts'].items()})
        validate_revision_materials(json.loads(row['job']), wire)
        return wire

    def update_revision_job(self, owner, task_id, status, *, candidate_version_id=None, result_digest=None):
        _require(status in ('reserved','queued','running','validating','cancel_requested','publishing',
                            'succeeded','failed','timed_out','cancelled','rejected'))
        row = self.get_revision_job(owner, task_id)
        _require(row is not None, 'revision_job_not_found', 404)
        # A delayed queued/ambiguous response cannot reopen a completed binding.
        if row['status'] in ('succeeded','failed','timed_out','cancelled','rejected'):
            return row
        if row['status'] == 'cancel_requested' and status in ('reserved','queued','running','validating'):
            return row
        changes = {'status':status,'updated_at':self._stamp(self._time())}
        if candidate_version_id is not None:
            changes['candidate_version_id'] = _identifier(candidate_version_id)
        if result_digest is not None:
            changes['result_digest'] = _digest(result_digest)
        self.db._execute('UPDATE scientific_report_revision_jobs SET ' + ','.join(k+'='+_sql(v) for k,v in changes.items()) +
            ' WHERE owner=' + _sql(owner) + ' AND task_id=' + _sql(task_id) + " AND status IN ('reserved','queued','running','validating','cancel_requested','publishing')" +
            (" AND status!='cancel_requested'" if status in ('reserved','queued','running','validating') else ''))
        return self.get_revision_job(owner,task_id)

    def writing_state(self, owner, source_report_id, source_sha256=None, include_versions=True):
        """Read the effective selection; no baseline import or selection write."""
        owner, source_report_id = _owner(owner), _identifier(source_report_id)
        sources = self.db._query('SELECT root_report_id FROM scientific_report_versions WHERE owner=' + _sql(owner) + ' AND source_report_id=' + _sql(source_report_id))
        root = sources[0]['root_report_id'] if sources else source_report_id
        events = self.db._query('SELECT * FROM scientific_report_adoptions WHERE owner=' + _sql(owner) + ' AND root_report_id=' + _sql(root) + ' ORDER BY selection_revision DESC LIMIT 1')
        if events:
            head = events[0]
            current = self._version(owner,head['version_id'])
            _require(current['root_report_id'] == root and current['report_sha256'] == head['report_sha256']
                     and type(head['selection_revision']) is int and 1 <= head['selection_revision'] <= MAX_SELECTION_REVISION,
                     'selection_integrity_error',409)
            revision = head['selection_revision']
        else:
            bases = self.db._query('SELECT version_id FROM scientific_report_versions WHERE owner=' + _sql(owner) +
                ' AND root_report_id=' + _sql(root) + ' AND source_report_id=' + _sql(root) + ' AND parent_version_id IS NULL LIMIT 1')
            current = self._version(owner,bases[0]['version_id']) if bases else None
            revision = 0
        result = dict(schema_version='1',root_report_id=root,
                      current_version_id=current['version_id'] if current else None,
                      current_sha256=current['report_sha256'] if current else source_sha256,
                      selection_revision=revision)
        if include_versions:
            result['versions'] = [dict(row,is_current=row['version_id'] == result['current_version_id'])
                                  for row in self.list_versions(owner,root,limit=100)]
        return result

    def _completed_candidate(self, owner, candidate_id):
        candidate = self._version(owner,candidate_id)
        _require(candidate['parent_version_id'] is not None,'candidate_not_available',409)
        parent = self._version(owner,candidate['parent_version_id'])
        binding = self.get_revision_job(owner,candidate['source_report_id'])
        _require(binding is not None and binding['status'] == 'succeeded'
                 and binding['candidate_version_id'] == candidate_id
                 and isinstance(binding['result_digest'],str)
                 and re.fullmatch('[0-9a-f]{64}',binding['result_digest']) is not None,
                 'candidate_not_available',409)
        job = json.loads(binding['job'])
        _require(job['parent_version_id'] == parent['version_id']
                 and job['root_report_id'] == candidate['root_report_id'] == parent['root_report_id']
                 and job['base_sha256'] == parent['report_sha256'], 'candidate_integrity_error',409)
        return candidate,parent,binding,job

    @staticmethod
    def _comparison_fingerprint(owner, candidate, parent, state):
        # A consistency fingerprint, not proof that a person saw a screen.
        return _sha(_json(dict(owner=owner,root_report_id=candidate['root_report_id'],
            candidate_version_id=candidate['version_id'],parent_version_id=parent['version_id'],
            base_sha256=parent['report_sha256'],candidate_sha256=candidate['report_sha256'],
            expected_current_version_id=state['current_version_id'],
            expected_selection_revision=state['selection_revision'])).encode('utf-8'))

    @staticmethod
    def _section_diff(before, after):
        unavailable = dict(format='unified',text='',available=False,reason='diff_too_large')
        if len(before.encode('utf-8'))+len(after.encode('utf-8')) > MAX_DIFF_INPUT_BYTES:
            return unavailable
        old, new = before.splitlines(keepends=True), after.splitlines(keepends=True)
        if len(old)+len(new) > MAX_DIFF_LINES:
            return unavailable
        parts, size = [], 0
        for part in difflib.unified_diff(old,new,fromfile='parent-section.md',tofile='candidate-section.md'):
            if part.startswith(('+','-',' ')) and not part.endswith(('\n','\r')):
                part += '\n\\ No newline at end of file\n'
            size += len(part.encode('utf-8'))
            if size > MAX_DIFF_OUTPUT_BYTES:
                return unavailable
            parts.append(part)
        return dict(format='unified',text=''.join(parts),available=True,reason=None)

    def compare_candidate(self, owner, candidate_id):
        owner, candidate_id = _owner(owner), _identifier(candidate_id)
        candidate,parent,binding,job = self._completed_candidate(owner,candidate_id)
        selection = select_section(parent['content'],job['section_id'],job['base_sha256'])
        _require(selection['sha256'] == job['section_sha256'],'candidate_integrity_error',409)
        original, revised = parent['content'].encode('utf-8'),candidate['content'].encode('utf-8')
        start,end = selection['start_byte'],selection['end_byte']
        new_end = len(revised)-(len(original)-end)
        _require(start <= new_end and revised[:start] == original[:start] and revised[new_end:] == original[end:],
                 'candidate_integrity_error',409)
        try:
            before,after = original[start:end].decode('utf-8'),revised[start:new_end].decode('utf-8')
        except UnicodeError:
            raise VersionError('candidate_integrity_error',status=409) from None
        state = self.writing_state(owner,candidate['root_report_id'],include_versions=False)
        adoption = dict(expected_current_version_id=state['current_version_id'],
                        expected_selection_revision=state['selection_revision'],
                        base_sha256=parent['report_sha256'],candidate_sha256=candidate['report_sha256'],
                        comparison_token=self._comparison_fingerprint(owner,candidate,parent,state),
                        can_adopt=state['current_version_id'] == parent['version_id'],
                        reason=None if state['current_version_id'] == parent['version_id'] else 'parent_not_current')
        return dict(schema_version='1',root_report_id=candidate['root_report_id'],
                    parent=self._version_meta(parent),candidate=self._version_meta(candidate),
                    section=dict(section_id=job['section_id'],title=selection['title'],start_byte=start,end_byte=end,
                                 candidate_end_byte=new_end,before=before,after=after,
                                 before_sha256=_sha(original[start:end]),after_sha256=_sha(revised[start:new_end])),
                    diff=self._section_diff(before,after),adoption=adoption)

    def find_adoption(self, owner, key):
        rows = self.db._query('SELECT * FROM scientific_report_adoptions WHERE owner=' + _sql(_owner(owner)) + ' AND idempotency_key=' + _sql(_key(key)))
        return rows[0] if rows else None

    @staticmethod
    def _adoption_receipt(row, replayed):
        return dict(adoption_id=row['adoption_id'],root_report_id=row['root_report_id'],
                    target_version_id=row['version_id'],report_sha256=row['report_sha256'],
                    selection_revision=row['selection_revision'],replayed=replayed)

    def adopt_candidate(self, owner, candidate_id, expected_current_version_id, expected_selection_revision,
                        base_sha256, candidate_sha256, comparison_token, key):
        owner, candidate_id, key = _owner(owner), _identifier(candidate_id), _key(key)
        if expected_current_version_id is not None:
            _identifier(expected_current_version_id)
        _require(type(expected_selection_revision) is int and 0 <= expected_selection_revision < MAX_SELECTION_REVISION)
        _digest(base_sha256), _digest(candidate_sha256), _digest(comparison_token)
        request_sha = _sha(_json(dict(owner=owner,candidate_version_id=candidate_id,
            expected_current_version_id=expected_current_version_id,expected_selection_revision=expected_selection_revision,
            base_sha256=base_sha256,candidate_sha256=candidate_sha256)).encode('utf-8'))
        previous = self.find_adoption(owner,key)
        if previous:
            _require(previous['request_sha256'] == request_sha,'adoption_key_conflict',409)
            return self._adoption_receipt(previous,True)
        candidate,parent,binding,job = self._completed_candidate(owner,candidate_id)
        _require(parent['report_sha256'] == base_sha256 and candidate['report_sha256'] == candidate_sha256,
                 'stale_comparison',409)
        expected = dict(current_version_id=expected_current_version_id,selection_revision=expected_selection_revision)
        _require(comparison_token == self._comparison_fingerprint(owner,candidate,parent,expected),'stale_comparison',409)
        _require(expected_current_version_id == parent['version_id'],'parent_not_current',409)
        root = candidate['root_report_id']
        condition = 'owner=' + _sql(owner) + ' AND root_report_id=' + _sql(root)
        current = ('COALESCE((SELECT version_id FROM scientific_report_adoptions WHERE ' + condition + ' ORDER BY selection_revision DESC LIMIT 1),'
                   '(SELECT version_id FROM scientific_report_versions WHERE ' + condition + ' AND source_report_id=' + _sql(root) + ' AND parent_version_id IS NULL LIMIT 1))')
        adoption_id,now = _stable('adoption',owner,key),self._stamp(self._time())
        values = [adoption_id,owner,root,candidate_id,candidate_sha256,parent['version_id'],base_sha256,
                  expected_current_version_id,expected_selection_revision,expected_selection_revision+1,key,request_sha,now]
        self.db._execute('INSERT INTO scientific_report_adoptions (adoption_id,owner,root_report_id,version_id,report_sha256,parent_version_id,base_sha256,expected_current_version_id,expected_selection_revision,selection_revision,idempotency_key,request_sha256,created_at) SELECT ' + ','.join(map(_sql,values)) +
            ' WHERE (SELECT COALESCE(MAX(selection_revision),0) FROM scientific_report_adoptions WHERE ' + condition + ')=' + str(expected_selection_revision) +
            ' AND ' + current + ' IS ' + _sql(expected_current_version_id) +
            ' AND EXISTS (SELECT 1 FROM scientific_report_versions v JOIN scientific_report_revision_jobs j ON j.owner=v.owner AND j.task_id=v.source_report_id JOIN scientific_reports r ON r.id=v.source_report_id AND r.task_id=v.source_report_id WHERE v.owner=' + _sql(owner) +
            ' AND v.version_id=' + _sql(candidate_id) + ' AND v.parent_version_id=' + _sql(parent['version_id']) +
            ' AND v.root_report_id=' + _sql(root) + ' AND v.report_sha256=' + _sql(candidate_sha256) +
            " AND j.status='succeeded' AND j.candidate_version_id=v.version_id AND j.result_digest=" + _sql(binding['result_digest']) +
            ' AND r.content=' + _sql(candidate['content']) + ')' +
            ' AND EXISTS (SELECT 1 FROM scientific_report_versions WHERE owner=' + _sql(owner) + ' AND version_id=' + _sql(parent['version_id']) + ' AND root_report_id=' + _sql(root) + ' AND report_sha256=' + _sql(base_sha256) + ')' +
            ' AND (SELECT COUNT(*) FROM scientific_report_adoptions WHERE owner=' + _sql(owner) + ')<' + str(self.owner_adoption_count) +
            ' AND (SELECT COUNT(*) FROM scientific_report_adoptions)<' + str(self.global_adoption_count) +
            ' ON CONFLICT DO NOTHING')
        result = self.find_adoption(owner,key)
        if result:
            _require(result['request_sha256'] == request_sha,'adoption_key_conflict',409)
            return self._adoption_receipt(result,False)
        if (self.db._query('SELECT COUNT(*) AS n FROM scientific_report_adoptions WHERE owner=' + _sql(owner))[0]['n'] >= self.owner_adoption_count
                or self.db._query('SELECT COUNT(*) AS n FROM scientific_report_adoptions')[0]['n'] >= self.global_adoption_count):
            raise VersionError('adoption_quota_exceeded',status=429)
        raise VersionError('selection_conflict',status=409)

    def cleanup_expired(self):
        now, token = self._stamp(self._time()), str(uuid.uuid4())
        self.db._execute('UPDATE scientific_report_snapshots SET payload=NULL,purged_at=' + _sql(now) + ',purge_token=' + _sql(token) +
            ' WHERE payload IS NOT NULL AND expires_at<=' + _sql(now) +
            " AND NOT EXISTS (SELECT 1 FROM scientific_report_revision_intents i WHERE i.owner=scientific_report_snapshots.owner AND i.snapshot_id=scientific_report_snapshots.snapshot_id AND i.status IN ('prepared','queued','running','validating','publishing') AND i.expires_at>" + _sql(now) + ')' +
            " AND NOT EXISTS (SELECT 1 FROM scientific_report_revision_jobs j WHERE j.owner=scientific_report_snapshots.owner AND j.snapshot_id=scientific_report_snapshots.snapshot_id AND j.status IN ('reserved','queued','running','validating','cancel_requested','publishing'))")
        rows = self.db._query('SELECT COUNT(*) AS n FROM scientific_report_snapshots WHERE purge_token=' + _sql(token))
        return {'cleared_snapshots': rows[0]['n'] if rows else 0}
