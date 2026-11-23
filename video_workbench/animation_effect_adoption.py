"""Explicit, owner-scoped effect representatives, without creative execution.

This metadata adapter stores a display decision, not an aesthetic approval or
Skill publication. The business service authenticates the owner and, under the
existing owner-scoped library mutation lock, checks current effect membership,
candidate media and trash visibility before calling ``ensure`` or ``adopt``.
The store independently serializes head and idempotency updates in SQLite.
"""
from contextlib import contextmanager
import json
import math
from pathlib import Path
import re
import sqlite3
import time

from agent_service.contracts import ServiceError, canonical_bytes, digest


SCHEMA = 'video.animation-effect-adoption.v1'
MAX_IDENTIFIER_CHARS = 200
MAX_REVISION = 2 ** 63 - 1
_IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}')
_MAX_RECORD_BYTES = 8192


def _require(value, code='animation_effect_adoption_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def _owner(value):
    _require(type(value) is str and 1 <= len(value) <= MAX_IDENTIFIER_CHARS
        and bool(value.strip()) and not any(
            ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF
            for char in value), 'animation_method_owner_invalid', 401)
    return value


def _identifier(value):
    _require(type(value) is str and _IDENTIFIER.fullmatch(value) is not None)
    return value


def _valid_result(value):
    return (type(value) is dict and set(value) == {'revision', 'commission_id', 'candidate_id'}
        and type(value['revision']) is int and 0 <= value['revision'] <= MAX_REVISION
        and all(type(value[key]) is str and _IDENTIFIER.fullmatch(value[key]) is not None
                for key in ('commission_id', 'candidate_id')))


class AnimationEffectAdoptionStore:
    """Persistent representatives and atomic compare-and-swap adoption.

    ``ensure`` freezes the existing representative once, at revision zero.
    Later calls never replace it. ``adopt`` requires this initialization and
    advances the revision, including an explicit adoption of the same sample.
    A repeated owner/request_id returns its original result even after newer
    adoptions; using the key for different input fails without changing a head.

    The path may be the existing library metadata database. No execution,
    media, knowledge or publication tables are read or changed here.
    """

    def __init__(self, path, *, clock=time.time):
        self.path = Path(path).absolute()
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._database(write=True) as db:
            db.execute('CREATE TABLE IF NOT EXISTS animation_effect_adoption_heads '
                '(owner TEXT NOT NULL,effect_id TEXT NOT NULL,revision INTEGER NOT NULL,'
                'commission_id TEXT NOT NULL,candidate_id TEXT NOT NULL,'
                'created_at REAL NOT NULL,updated_at REAL NOT NULL,PRIMARY KEY(owner,effect_id))')
            db.execute('CREATE TABLE IF NOT EXISTS animation_effect_adoption_requests '
                '(owner TEXT NOT NULL,request_id TEXT NOT NULL,request_sha256 TEXT NOT NULL,'
                'document TEXT NOT NULL,PRIMARY KEY(owner,request_id))')

    @contextmanager
    def _database(self, *, write=False):
        db = sqlite3.connect(self.path if write else self.path.as_uri() + '?mode=ro',
            uri=not write, timeout=20, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield db
            db.commit()
        except sqlite3.DatabaseError:
            db.rollback()
            raise ServiceError('animation_effect_adoption_record_changed', 409) from None
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _get(db, owner, effect_id):
        row = db.execute('SELECT revision,commission_id,candidate_id '
            'FROM animation_effect_adoption_heads WHERE owner=? AND effect_id=?',
            (owner, effect_id)).fetchone()
        if row is None:
            return None
        result = dict(row)
        _require(_valid_result(result), 'animation_effect_adoption_record_changed', 409)
        return result

    def _now(self):
        value = self.clock()
        _require(type(value) in {int, float} and math.isfinite(value) and value >= 0,
            'animation_effect_adoption_clock_invalid', 503)
        return value

    def get(self, owner, effect_id):
        """Return the stored display choice, or None; never pick a new one."""
        owner, effect_id = _owner(owner), _identifier(effect_id)
        with self._database() as db:
            return self._get(db, owner, effect_id)

    def ensure(self, owner, effect_id, commission_id, candidate_id):
        """Initialize an existing default once; fresh results cannot replace it."""
        owner, effect_id = _owner(owner), _identifier(effect_id)
        commission_id, candidate_id = _identifier(commission_id), _identifier(candidate_id)
        with self._database(write=True) as db:
            current = self._get(db, owner, effect_id)
            if current is not None:
                return current
            now = self._now()
            db.execute('INSERT INTO animation_effect_adoption_heads '
                '(owner,effect_id,revision,commission_id,candidate_id,created_at,updated_at) '
                'VALUES (?,?,0,?,?,?,?)', (owner, effect_id, commission_id, candidate_id, now, now))
            return dict(revision=0, commission_id=commission_id, candidate_id=candidate_id)

    @staticmethod
    def _replay(row, request):
        _require(row['request_sha256'] == digest(request),
            'animation_effect_adoption_idempotency_conflict', 409)
        document = row['document']
        _require(type(document) is str and len(document.encode('utf-8')) <= _MAX_RECORD_BYTES,
            'animation_effect_adoption_record_changed', 409)
        try:
            record = json.loads(document)
            checksum = record.pop('sha256')
            valid = (set(record) == {'schema', 'request', 'result', 'created_at'}
                and record['schema'] == SCHEMA and record['request'] == request
                and checksum == digest(record) and _valid_result(record['result'])
                and record['result'] == dict(revision=request['expected_revision'] + 1,
                    commission_id=request['commission_id'], candidate_id=request['candidate_id']))
        except (KeyError, TypeError, ValueError, AttributeError):
            valid = False
        _require(valid, 'animation_effect_adoption_record_changed', 409)
        return record['result']

    def adopt(self, owner, effect_id, commission_id, candidate_id, expected_revision, request_id):
        """Apply one authenticated, prevalidated display decision exactly once."""
        owner, effect_id = _owner(owner), _identifier(effect_id)
        commission_id, candidate_id = _identifier(commission_id), _identifier(candidate_id)
        request_id = _identifier(request_id)
        _require(type(expected_revision) is int and 0 <= expected_revision < MAX_REVISION)
        request = dict(owner=owner, effect_id=effect_id, commission_id=commission_id,
            candidate_id=candidate_id, expected_revision=expected_revision, request_id=request_id)
        with self._database(write=True) as db:
            prior = db.execute('SELECT request_sha256,document '
                'FROM animation_effect_adoption_requests WHERE owner=? AND request_id=?',
                (owner, request_id)).fetchone()
            if prior is not None:
                return self._replay(prior, request)
            current = self._get(db, owner, effect_id)
            _require(current is not None, 'animation_effect_adoption_uninitialized', 409)
            _require(current['revision'] == expected_revision,
                'animation_effect_adoption_revision_conflict', 409)
            result = dict(revision=expected_revision + 1,
                commission_id=commission_id, candidate_id=candidate_id)
            now = self._now()
            updated = db.execute('UPDATE animation_effect_adoption_heads '
                'SET revision=?,commission_id=?,candidate_id=?,updated_at=? '
                'WHERE owner=? AND effect_id=? AND revision=?',
                (result['revision'], commission_id, candidate_id, now, owner, effect_id, expected_revision))
            _require(updated.rowcount == 1, 'animation_effect_adoption_revision_conflict', 409)
            record = dict(schema=SCHEMA, request=request, result=result, created_at=now)
            record['sha256'] = digest(record)
            document = canonical_bytes(record)
            _require(len(document) <= _MAX_RECORD_BYTES)
            db.execute('INSERT INTO animation_effect_adoption_requests VALUES (?,?,?,?)',
                (owner, request_id, digest(request), document.decode('utf-8')))
            return result
