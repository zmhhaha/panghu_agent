"""Versioned creative methods and many-to-many demonstration evidence.

This store is an internal adapter, not an HTTP request validator. Demonstration
and validation evidence must come from verified server-side rendering/review
records. A browser may express feedback or explicitly accept visible results;
it must not manufacture technical, runtime, or review evidence.

Methods and demonstrations are immutable. Acceptance and publication are
append-only permissions targeting those exact versions. A method delivery
receipt records delivery to a caller, not model observation or creative success.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from uuid import uuid4

from agent_service.contracts import ServiceError, digest


SCHEMA = 'video.animation-method.v1'
MAX_RECORD_BYTES = 4 * 1024 * 1024
_SHA = re.compile(r'^[0-9a-f]{64}$')
_TABLES = frozenset({
    'animation_methods', 'animation_demonstrations', 'animation_method_validations',
    'animation_method_acceptances', 'animation_method_publications',
    'animation_method_requests', 'animation_method_deliveries',
    'animation_method_events', 'sqlite_sequence',
})
_METHOD_FIELDS = frozenset({
    'title', 'problem', 'guidance', 'applicability', 'limitations', 'criteria',
    'runtime_requirements',
})
_DEMO_FIELDS = frozenset({
    'title', 'goal', 'media', 'source', 'runtime', 'technical', 'provenance',
})
_CHANGE_FIELDS = frozenset({
    'object_count', 'relations', 'task', 'duration', 'staging', 'data_structure',
    'timing', 'explanation_problem', 'conditions',
})


def _require(condition, code='invalid_animation_method', status=422):
    if not condition:
        raise ServiceError(code, status)


def _text(value, maximum=200, *, empty=False):
    _require(type(value) is str and (empty or bool(value.strip()))
             and len(value) <= maximum
             and not any((ord(c) < 32 and c not in '\r\n\t') or ord(c) == 127
                         or 0xD800 <= ord(c) <= 0xDFFF for c in value))
    return value


def _owner(value):
    _text(value)
    _require(not any(ord(c) < 32 for c in value), 'invalid_animation_method_owner', 401)
    return value


def _sha(value):
    _require(type(value) is str and _SHA.fullmatch(value),
             'invalid_animation_method_sha256')
    return value


def _json(value):
    try:
        result = json.dumps(value, ensure_ascii=False, allow_nan=False,
                            separators=(',', ':'), sort_keys=True)
        raw = result.encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise ServiceError('invalid_animation_method_json', 422) from None
    _require(len(raw) <= MAX_RECORD_BYTES, 'animation_method_record_too_large', 413)
    return result


def _copy(value):
    return json.loads(_json(value))


def _strings(value, *, minimum=0):
    _require(type(value) is list and minimum <= len(value) <= 64)
    return [_text(item, 16000) for item in value]


def _number(value, *, positive=False):
    _require(type(value) in (int, float) and math.isfinite(value)
             and (value > 0 if positive else value >= 0))
    return value


def _criteria(value):
    _require(type(value) is list and 1 <= len(value) <= 32)
    identifiers = set()
    for row in value:
        _require(type(row) is dict and {'id', 'description'} <= set(row)
                 and set(row) <= {'id', 'description', 'observation', 'required', 'kind',
                                  'requires_motion', 'requires_audio'})
        _text(row['id'])
        _text(row['description'], 8000)
        _require(row['id'] not in identifiers, 'animation_method_duplicate_criterion')
        identifiers.add(row['id'])
        if 'observation' in row:
            _text(row['observation'], 8000)
        for key in ('required', 'requires_motion', 'requires_audio'):
            if key in row:
                _require(type(row[key]) is bool)
        if 'kind' in row:
            _text(row['kind'])
    return _copy(value)


def _method(body):
    _require(type(body) is dict and _METHOD_FIELDS <= set(body)
             and set(body) <= _METHOD_FIELDS | {'method_id', 'parent_version_id'})
    for key in ('title', 'problem', 'guidance', 'applicability'):
        _text(body[key], 64000 if key == 'guidance' else 16000)
    _strings(body['limitations'])
    _criteria(body['criteria'])
    runtime = body['runtime_requirements']
    _require(type(runtime) is dict and bool(runtime))
    if 'engine' in runtime:
        _require(type(runtime['engine']) is str and runtime['engine'] in {'html-svg', 'canvas', 'three'})
    for key in ('method_id', 'parent_version_id'):
        if key in body and body[key] is not None:
            _text(body[key])
    return _copy(body)


def _demonstration(body):
    _require(type(body) is dict and _DEMO_FIELDS <= set(body)
             and set(body) <= _DEMO_FIELDS | {'demonstration_id', 'parent_version_id'})
    for key in ('title', 'goal'):
        _text(body[key], 16000)
    media = body['media']
    _require(type(media) is dict and {'sha256', 'duration_seconds'} <= set(media))
    _sha(media['sha256'])
    _require(_number(media['duration_seconds'], positive=True) <= 3600)
    if 'url' in media:
        _text(media['url'], 4000)
    if 'audio_sha256' in media and media['audio_sha256'] is not None:
        _sha(media['audio_sha256'])
    _require(body['source'] is None or type(body['source']) is dict)
    _require(type(body['runtime']) is dict
             and type(body['runtime'].get('engine')) is str
             and body['runtime']['engine'] in {'html-svg', 'canvas', 'three'})
    technical = body['technical']
    _require(type(technical) is dict and type(technical.get('status')) is str and technical['status'] in {
        'passed', 'failed', 'unverified'})
    _sha(technical.get('evidence_sha256'))
    _require(type(body['provenance']) is dict and bool(body['provenance']))
    for key in ('demonstration_id', 'parent_version_id'):
        if key in body and body[key] is not None:
            _text(body[key])
    return _copy(body)


def _structural_changes(value):
    _require(type(value) is dict and bool(value) and set(value) <= _CHANGE_FIELDS,
             'animation_method_structural_change_required')
    for dimension, change in value.items():
        _require(type(change) is dict and set(change) == {'before', 'after'}
                 and change['before'] is not None and change['after'] is not None
                 and _json(change['before']) != _json(change['after']),
                 'animation_method_structural_change_required')
        if dimension == 'object_count':
            _require(all(type(change[side]) is int and 0 <= change[side] <= 100000
                         for side in ('before', 'after')),
                     'animation_method_structural_change_required')
        if dimension == 'duration':
            _number(change['before'], positive=True)
            _number(change['after'], positive=True)
    return _copy(value)


def _runtime_admitted(runtime):
    admission = runtime.get('admission')
    return (type(admission) is dict and admission.get('status') == 'admitted'
            and type(admission.get('id')) is str and bool(admission['id'])
            and type(admission.get('evidence_sha256')) is str
            and _SHA.fullmatch(admission['evidence_sha256']) is not None)


class AnimationMethodStore:
    """Dedicated, owner-scoped SQLite store with immutable content versions."""

    def __init__(self, path):
        target = Path(path).absolute()
        _require(not any(p.is_symlink() for p in (target, *target.parents)),
                 'animation_method_store_path_invalid')
        target.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(target)
        with self._transaction() as db:
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            _require(tables <= _TABLES, 'animation_method_database_not_isolated', 409)
            statements = (
                '''CREATE TABLE IF NOT EXISTS animation_methods (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, method_id TEXT NOT NULL,
                    version INTEGER NOT NULL, document TEXT NOT NULL,
                    UNIQUE(owner,method_id,version))''',
                '''CREATE TABLE IF NOT EXISTS animation_demonstrations (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, demonstration_id TEXT NOT NULL,
                    version INTEGER NOT NULL, document TEXT NOT NULL,
                    UNIQUE(owner,demonstration_id,version))''',
                '''CREATE TABLE IF NOT EXISTS animation_method_validations (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL,
                    method_version_id TEXT NOT NULL REFERENCES animation_methods(id),
                    demonstration_version_id TEXT NOT NULL REFERENCES animation_demonstrations(id),
                    role TEXT NOT NULL, document TEXT NOT NULL,
                    UNIQUE(method_version_id,demonstration_version_id,role))''',
                '''CREATE TABLE IF NOT EXISTS animation_method_acceptances (
                    validation_id TEXT PRIMARY KEY REFERENCES animation_method_validations(id),
                    owner TEXT NOT NULL, document TEXT NOT NULL)''',
                '''CREATE TABLE IF NOT EXISTS animation_method_publications (
                    version_id TEXT PRIMARY KEY REFERENCES animation_methods(id),
                    owner TEXT NOT NULL, document TEXT NOT NULL)''',
                '''CREATE TABLE IF NOT EXISTS animation_method_requests (
                    owner TEXT NOT NULL, request_id TEXT NOT NULL,
                    operation TEXT NOT NULL, request_sha256 TEXT NOT NULL,
                    document TEXT NOT NULL, PRIMARY KEY(owner,request_id))''',
                '''CREATE TABLE IF NOT EXISTS animation_method_deliveries (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, consumer_id TEXT NOT NULL,
                    document TEXT NOT NULL, UNIQUE(owner,consumer_id))''',
                '''CREATE TABLE IF NOT EXISTS animation_method_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL,
                    target_id TEXT NOT NULL, event TEXT NOT NULL, document TEXT NOT NULL)''',
            )
            for statement in statements:
                db.execute(statement)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    @contextmanager
    def _transaction(self):
        db = self._connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _load(db, table, owner, row_id):
        _text(row_id)
        row = db.execute(f'SELECT document FROM {table} WHERE id=? AND owner=?',
                         (row_id, owner)).fetchone()
        _require(row is not None, 'animation_method_not_found', 404)
        return json.loads(row['document'])

    @staticmethod
    def _event(db, owner, target_id, event, detail):
        document = dict(event=event, target_id=target_id, at=time.time(), **detail)
        db.execute('INSERT INTO animation_method_events '
                   '(owner,target_id,event,document) VALUES (?,?,?,?)',
                   (owner, target_id, event, _json(document)))

    def _perform(self, owner, operation, request_id, request, function):
        _owner(owner)
        _text(request_id, 500)
        request_sha256 = digest(dict(operation=operation, request=request))
        with self._transaction() as db:
            prior = db.execute('SELECT operation,request_sha256,document FROM '
                               'animation_method_requests WHERE owner=? AND request_id=?',
                               (owner, request_id)).fetchone()
            if prior:
                _require(prior['operation'] == operation
                         and prior['request_sha256'] == request_sha256,
                         'animation_method_idempotency_conflict', 409)
                return json.loads(prior['document'])
            result = function(db)
            db.execute('INSERT INTO animation_method_requests '
                       '(owner,request_id,operation,request_sha256,document) VALUES (?,?,?,?,?)',
                       (owner, request_id, operation, request_sha256, _json(result)))
            return _copy(result)

    @staticmethod
    def _next_version(db, table, owner, group_field, group_id, parent_id):
        rows = db.execute(f'SELECT id,version FROM {table} '
                          f'WHERE owner=? AND {group_field}=? ORDER BY version DESC',
                          (owner, group_id)).fetchall()
        if parent_id is None:
            _require(not rows, 'animation_method_parent_required', 409)
            return 1
        _require(bool(rows) and any(row['id'] == parent_id for row in rows),
                 'animation_method_parent_mismatch', 409)
        return rows[0]['version'] + 1

    def create_method(self, owner, body, request_id):
        body = _method(body)

        def create(db):
            parent_id = body.get('parent_version_id')
            method_id = body.get('method_id')
            if parent_id:
                parent = self._load(db, 'animation_methods', owner, parent_id)
                _require(method_id is None or method_id == parent['method_id'],
                         'animation_method_parent_mismatch', 409)
                method_id = parent['method_id']
            method_id = method_id or str(uuid4())
            version = self._next_version(db, 'animation_methods', owner,
                                         'method_id', method_id, parent_id)
            content = {key: body[key] for key in sorted(_METHOD_FIELDS)}
            version_id = str(uuid4())
            result = dict(schema=SCHEMA, id=version_id, version_id=version_id,
                owner=owner, method_id=method_id, version=version,
                parent_version_id=parent_id, content_sha256=digest(content),
                criteria_sha256=digest(content['criteria']), status='candidate',
                created_at=time.time(), **content)
            db.execute('INSERT INTO animation_methods VALUES (?,?,?,?,?)',
                       (version_id, owner, method_id, version, _json(result)))
            self._event(db, owner, version_id, 'method_created',
                        dict(content_sha256=result['content_sha256']))
            return result
        return self._perform(owner, 'create_method', request_id, body, create)

    def _get_method(self, db, owner, version_id, *, published_only=False):
        _text(version_id)
        row = db.execute('SELECT document,owner FROM animation_methods WHERE id=?',
                         (version_id,)).fetchone()
        publication = db.execute('SELECT document FROM animation_method_publications '
                                 'WHERE version_id=?', (version_id,)).fetchone()
        _require(row is not None and (publication is not None or row['owner'] == owner)
                 and (not published_only or publication is not None),
                 'animation_method_not_found', 404)
        result = json.loads(row['document'])
        if publication:
            result.update(status='published', publication=json.loads(publication['document']))
        return result

    def get_method(self, owner, version_id):
        _owner(owner)
        with closing(self._connect()) as db:
            return self._get_method(db, owner, version_id)

    def list_methods(self, owner, published_only=False, query=''):
        _owner(owner)
        _require(type(published_only) is bool)
        _text(query, 1000, empty=True)
        with closing(self._connect()) as db:
            ids = db.execute('SELECT m.id FROM animation_methods m LEFT JOIN '
                'animation_method_publications p ON p.version_id=m.id '
                'WHERE p.version_id IS NOT NULL' + ('' if published_only else ' OR m.owner=?')
                + ' ORDER BY m.rowid', () if published_only else (owner,)).fetchall()
            methods = [self._get_method(db, owner, row['id'], published_only=published_only)
                       for row in ids]
        words = query.casefold().split()
        return [row for row in methods if all(word in ' '.join(
            [row['title'], row['problem'], row['applicability']]).casefold() for word in words)]

    def add_demonstration(self, owner, body, request_id):
        """Import verified server-owned media evidence; never browser evidence."""
        body = _demonstration(body)

        def create(db):
            parent_id = body.get('parent_version_id')
            group_id = body.get('demonstration_id')
            if parent_id:
                parent = self._load(db, 'animation_demonstrations', owner, parent_id)
                _require(group_id is None or group_id == parent['demonstration_id'],
                         'animation_method_parent_mismatch', 409)
                group_id = parent['demonstration_id']
            group_id = group_id or str(uuid4())
            version = self._next_version(db, 'animation_demonstrations', owner,
                                         'demonstration_id', group_id, parent_id)
            content = {key: body[key] for key in sorted(_DEMO_FIELDS)}
            row_id = str(uuid4())
            result = dict(schema='video.animation-method-demonstration.v1',
                id=row_id, version_id=row_id, owner=owner,
                demonstration_id=group_id, version=version, parent_version_id=parent_id,
                content_sha256=digest(content), created_at=time.time(), **content)
            db.execute('INSERT INTO animation_demonstrations VALUES (?,?,?,?,?)',
                       (row_id, owner, group_id, version, _json(result)))
            self._event(db, owner, row_id, 'demonstration_created',
                        dict(content_sha256=result['content_sha256']))
            return result
        return self._perform(owner, 'add_demonstration', request_id, body, create)

    def get_demonstration(self, owner, version_id):
        _owner(owner)
        with closing(self._connect()) as db:
            return self._load(db, 'animation_demonstrations', owner, version_id)

    def list_demonstrations(self, owner):
        _owner(owner)
        with closing(self._connect()) as db:
            return [json.loads(row['document']) for row in db.execute(
                'SELECT document FROM animation_demonstrations WHERE owner=? ORDER BY rowid',
                (owner,))]

    def link_validation(self, owner, body, request_id):
        """Import a verified evaluation; this cannot approve or publish a method."""
        fields = {'method_version_id', 'demonstration_version_id', 'role',
                  'criteria_sha256', 'outcome', 'evidence'}
        _require(type(body) is dict and fields <= set(body)
                 and set(body) <= fields | {'structural_changes', 'baseline_demonstration_version_id'})
        _require(type(body['role']) is str and body['role'] in {'reference', 'transfer'}
                 and type(body['outcome']) is str and body['outcome'] in {'passed', 'failed', 'incomplete'})
        _require(type(body['evidence']) is dict and bool(body['evidence']))
        _sha(body['criteria_sha256'])
        body = _copy(body)

        def create(db):
            method = self._load(db, 'animation_methods', owner, body['method_version_id'])
            demonstration = self._load(db, 'animation_demonstrations', owner,
                                       body['demonstration_version_id'])
            _require(method['criteria_sha256'] == body['criteria_sha256'],
                     'animation_method_criteria_mismatch', 409)
            required_engine = method['runtime_requirements'].get('engine')
            _require(required_engine is None or required_engine == demonstration['runtime']['engine'],
                     'animation_method_runtime_mismatch', 409)
            if 'media_sha256' in body['evidence']:
                _require(body['evidence']['media_sha256'] == demonstration['media']['sha256'],
                         'animation_method_evidence_mismatch', 409)
            if body['role'] == 'transfer':
                _require('baseline_demonstration_version_id' in body,
                         'animation_method_transfer_baseline_required')
                base = self._load(db, 'animation_demonstrations', owner,
                                  body['baseline_demonstration_version_id'])
                _require(base['id'] != demonstration['id']
                         and base['media']['sha256'] != demonstration['media']['sha256'],
                         'animation_method_transfer_not_distinct', 409)
                baseline_validation = db.execute(
                    "SELECT id FROM animation_method_validations WHERE method_version_id=? "
                    "AND demonstration_version_id=? AND role='reference'",
                    (method['id'], base['id'])).fetchone()
                _require(baseline_validation is not None,
                         'animation_method_transfer_baseline_required', 409)
                _structural_changes(body.get('structural_changes'))
                duration = body['structural_changes'].get('duration')
                if duration:
                    _require(duration['before'] == base['media']['duration_seconds']
                             and duration['after'] == demonstration['media']['duration_seconds'],
                             'animation_method_transfer_duration_mismatch', 409)
            else:
                _require(not body.get('structural_changes')
                         and not body.get('baseline_demonstration_version_id'))
            prior = db.execute('SELECT document FROM animation_method_validations '
                'WHERE method_version_id=? AND demonstration_version_id=? AND role=?',
                (method['id'], demonstration['id'], body['role'])).fetchone()
            _require(prior is None, 'animation_method_validation_exists', 409)
            row_id = str(uuid4())
            result = dict(schema='video.animation-method-validation.v1', id=row_id,
                owner=owner, method_content_sha256=method['content_sha256'],
                demonstration_content_sha256=demonstration['content_sha256'],
                evidence_sha256=digest(body['evidence']), created_at=time.time(), **body)
            result['acceptance_target_sha256'] = digest(result)
            db.execute('INSERT INTO animation_method_validations VALUES (?,?,?,?,?,?)',
                (row_id, owner, method['id'], demonstration['id'], body['role'], _json(result)))
            self._event(db, owner, method['id'], 'validation_linked',
                        dict(validation_id=row_id, role=body['role'], outcome=body['outcome']))
            return result
        return self._perform(owner, 'link_validation', request_id, body, create)

    def list_validations(self, owner, method_version_id=None, demonstration_version_id=None):
        _owner(owner)
        clauses, args = ['owner=?'], [owner]
        for key, value in (('method_version_id', method_version_id),
                           ('demonstration_version_id', demonstration_version_id)):
            if value is not None:
                _text(value)
                clauses.append(key + '=?')
                args.append(value)
        with closing(self._connect()) as db:
            result = []
            for row in db.execute('SELECT document FROM animation_method_validations WHERE '
                                  + ' AND '.join(clauses) + ' ORDER BY rowid', args):
                value = json.loads(row['document'])
                accepted = db.execute('SELECT document FROM animation_method_acceptances '
                                       'WHERE validation_id=?', (value['id'],)).fetchone()
                value['acceptance'] = json.loads(accepted['document']) if accepted else None
                result.append(value)
            return result

    def accept(self, owner, body, request_id):
        """Record an explicit authenticated developer decision for exact evidence."""
        fields = {'validation_id', 'comment', 'method_content_sha256'}
        _require(type(body) is dict and fields <= set(body)
                 and set(body) <= fields | {'human_verification'})
        _text(body['validation_id'])
        _text(body['comment'], 16000)
        _sha(body['method_content_sha256'])
        body = _copy(body)

        def create(db):
            validation = self._load(db, 'animation_method_validations', owner, body['validation_id'])
            _require(validation['method_content_sha256'] == body['method_content_sha256'],
                     'animation_method_acceptance_target_mismatch', 409)
            _require(validation['outcome'] != 'failed',
                     'animation_method_validation_not_passed', 409)
            demonstration = self._load(db, 'animation_demonstrations', owner,
                                       validation['demonstration_version_id'])
            _require(demonstration['technical']['status'] == 'passed'
                     and _runtime_admitted(demonstration['runtime']),
                     'animation_method_demonstration_not_admitted', 409)
            human = body.get('human_verification')
            if human is not None:
                _require(type(human) is dict
                    and {'criteria_ids', 'motion_watched', 'audio_listened'} <= set(human)
                    and set(human) <= {'criteria_ids', 'motion_watched', 'audio_listened', 'comment'},
                    'animation_method_human_verification_invalid')
                criteria_ids = _strings(human['criteria_ids'], minimum=1)
                _require(len(criteria_ids) == len(set(criteria_ids))
                    and type(human['motion_watched']) is bool
                    and type(human['audio_listened']) is bool,
                    'animation_method_human_verification_invalid')
                if 'comment' in human:
                    _text(human['comment'], 16000)
                method = self._load(db, 'animation_methods', owner,
                                    validation['method_version_id'])
                _require(set(criteria_ids) == {row['id'] for row in method['criteria']},
                    'animation_method_human_criteria_incomplete', 409)
                if any(row.get('requires_motion') for row in method['criteria']):
                    _require(human['motion_watched'],
                             'animation_method_human_motion_required', 409)
                if any(row.get('requires_audio') for row in method['criteria']):
                    _require(human['audio_listened'],
                             'animation_method_human_audio_required', 409)
                    _require(type(demonstration['media'].get('audio_sha256')) is str
                             and _SHA.fullmatch(demonstration['media']['audio_sha256']),
                             'animation_method_audio_evidence_required', 409)
            _require(validation['outcome'] == 'passed' or human is not None,
                     'animation_method_validation_not_passed', 409)
            prior = db.execute('SELECT document FROM animation_method_acceptances '
                               'WHERE validation_id=?', (validation['id'],)).fetchone()
            _require(prior is None, 'animation_method_acceptance_exists', 409)
            result = dict(schema='video.animation-method-acceptance.v1', owner=owner,
                validation_id=validation['id'], method_version_id=validation['method_version_id'],
                demonstration_version_id=validation['demonstration_version_id'],
                acceptance_target_sha256=validation['acceptance_target_sha256'],
                method_content_sha256=body['method_content_sha256'], comment=body['comment'],
                source='explicit_developer_acceptance',
                acceptance_basis='explicit_human_verification' if human else 'observed_goal_review',
                review_outcome_at_acceptance=validation['outcome'],
                human_verification=human, created_at=time.time())
            db.execute('INSERT INTO animation_method_acceptances VALUES (?,?,?)',
                       (validation['id'], owner, _json(result)))
            self._event(db, owner, validation['method_version_id'], 'validation_accepted',
                        dict(validation_id=validation['id']))
            return result
        return self._perform(owner, 'accept', request_id, body, create)

    def publish(self, owner, version_id, request_id):
        _text(version_id)

        def create(db):
            method = self._load(db, 'animation_methods', owner, version_id)
            accepted = [json.loads(row['document']) for row in db.execute(
                'SELECT v.document FROM animation_method_validations v JOIN '
                'animation_method_acceptances a ON a.validation_id=v.id '
                'WHERE v.owner=? AND v.method_version_id=?',
                (owner, version_id))]
            references = {row['demonstration_version_id']: row for row in accepted
                          if row['role'] == 'reference'}
            transfers = [row for row in accepted if row['role'] == 'transfer'
                         and row.get('baseline_demonstration_version_id') in references]
            _require(bool(references) and bool(transfers),
                     'animation_method_publication_blocked', 409)
            prior = db.execute('SELECT document FROM animation_method_publications '
                               'WHERE version_id=?', (version_id,)).fetchone()
            _require(prior is None, 'animation_method_already_published', 409)
            result = dict(schema='video.animation-method-publication.v1',
                method_version_id=version_id, method_content_sha256=method['content_sha256'],
                owner=owner, reference_validation_ids=[row['id'] for row in references.values()],
                transfer_validation_ids=[row['id'] for row in transfers],
                created_at=time.time())
            db.execute('INSERT INTO animation_method_publications VALUES (?,?,?)',
                       (version_id, owner, _json(result)))
            self._event(db, owner, version_id, 'method_published', result)
            return self._get_method(db, owner, version_id, published_only=True)
        return self._perform(owner, 'publish', request_id, dict(version_id=version_id), create)

    def read_published(self, owner, version_ids, consumer_id):
        _owner(owner)
        _text(consumer_id, 400)
        _require(type(version_ids) is list and 1 <= len(version_ids) <= 16)
        for version_id in version_ids:
            _text(version_id)
        _require(len(set(version_ids)) == len(version_ids))
        version_ids = list(version_ids)

        def create(db):
            methods = [self._get_method(db, owner, version_id, published_only=True)
                       for version_id in version_ids]
            row_id = str(uuid4())
            receipt = dict(id=row_id, owner=owner, consumer_id=consumer_id,
                scope='methods_delivered_to_caller_not_model_observation',
                method_versions=[dict(version_id=row['id'], content_sha256=row['content_sha256'])
                                 for row in methods], created_at=time.time())
            result = dict(schema='video.animation-method-delivery.v1',
                          methods=methods, read_receipt=receipt)
            db.execute('INSERT INTO animation_method_deliveries VALUES (?,?,?,?)',
                       (row_id, owner, consumer_id, _json(result)))
            self._event(db, owner, row_id, 'methods_delivered',
                        dict(consumer_id=consumer_id, method_versions=receipt['method_versions']))
            return result
        return self._perform(owner, 'read_published', 'delivery:' + consumer_id,
                             dict(version_ids=version_ids, consumer_id=consumer_id), create)

    def events(self, owner, target_id=None):
        _owner(owner)
        if target_id is not None:
            _text(target_id)
        with closing(self._connect()) as db:
            query = 'SELECT document FROM animation_method_events WHERE owner=?'
            args = [owner]
            if target_id is not None:
                query += ' AND target_id=?'
                args.append(target_id)
            return [json.loads(row['document']) for row in db.execute(query + ' ORDER BY sequence', args)]
