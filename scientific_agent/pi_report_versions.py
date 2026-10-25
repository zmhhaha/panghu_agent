"""Append-only report versions, bounded evidence snapshots and revision intents.

The shared SQL HTTP adapter commits one statement per request. Every admission
condition is therefore inside its INSERT SELECT, with a stable ID and readback
for retries after an ambiguous commit. This module never edits original reports
and never starts a worker. Expired snapshot payloads leave idempotency tombstones.
"""
from __future__ import annotations

import base64
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
                 owner_intent_count=10000, owner_live_intent_count=100):
        self.db, self.clock = db, clock or (lambda: datetime.now(timezone.utc))
        values = locals().copy()
        for name in ('snapshot_ttl', 'intent_ttl', 'owner_snapshot_bytes', 'global_snapshot_bytes',
                     'owner_snapshot_count', 'owner_version_count', 'owner_version_bytes',
                     'global_version_bytes', 'owner_intent_count', 'owner_live_intent_count'):
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
                ' WHERE (SELECT COUNT(*) FROM scientific_report_versions WHERE owner=' + _sql(owner) + ')<' + str(self.owner_version_count) +
                ' AND (SELECT COALESCE(SUM(content_bytes),0) FROM scientific_report_versions WHERE owner=' + _sql(owner) + ')+' + str(len(raw)) + '<=' + str(self.owner_version_bytes) +
                ' AND (SELECT COALESCE(SUM(content_bytes),0) FROM scientific_report_versions)+' + str(len(raw)) + '<=' + str(self.global_version_bytes) +
                ' ON CONFLICT DO NOTHING')
            existing = self.db._query('SELECT version_id FROM scientific_report_versions WHERE owner=' + _sql(owner) + ' AND source_report_id=' + _sql(source_report_id))
            if existing:
                version_id = existing[0]['version_id']
        try:
            row = self._version(owner, version_id)
        except VersionError as exc:
            if exc.code == 'version_not_found':
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
            values = [version_id, owner, parent['root_report_id'], source_report_id, parent_version_id, digest,
                      base64.b64encode(raw).decode('ascii'), len(raw), topic, now]
            self.db._execute('INSERT INTO scientific_report_versions (' + _VERSION_COLUMNS.replace('content_bytes,topic', 'content_b64,content_bytes,topic') + ') SELECT ' + ','.join(map(_sql, values)) +
                ' WHERE EXISTS (SELECT 1 FROM scientific_report_versions WHERE owner=' + _sql(owner) + ' AND version_id=' + _sql(parent_version_id) + ' AND root_report_id=' + _sql(parent['root_report_id']) + ' AND report_sha256=' + _sql(base_sha256) + ')' +
                ' AND (SELECT COUNT(*) FROM scientific_report_versions WHERE owner=' + _sql(owner) + ')<' + str(self.owner_version_count) +
                ' AND (SELECT COALESCE(SUM(content_bytes),0) FROM scientific_report_versions WHERE owner=' + _sql(owner) + ')+' + str(len(raw)) + '<=' + str(self.owner_version_bytes) +
                ' AND (SELECT COALESCE(SUM(content_bytes),0) FROM scientific_report_versions)+' + str(len(raw)) + '<=' + str(self.global_version_bytes) +
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

    def cleanup_expired(self):
        now, token = self._stamp(self._time()), str(uuid.uuid4())
        self.db._execute('UPDATE scientific_report_snapshots SET payload=NULL,purged_at=' + _sql(now) + ',purge_token=' + _sql(token) +
            ' WHERE payload IS NOT NULL AND expires_at<=' + _sql(now) +
            " AND NOT EXISTS (SELECT 1 FROM scientific_report_revision_intents i WHERE i.owner=scientific_report_snapshots.owner AND i.snapshot_id=scientific_report_snapshots.snapshot_id AND i.status IN ('prepared','queued','running','validating','publishing') AND i.expires_at>" + _sql(now) + ')')
        rows = self.db._query('SELECT COUNT(*) AS n FROM scientific_report_snapshots WHERE purge_token=' + _sql(token))
        return {'cleared_snapshots': rows[0]['n'] if rows else 0}
