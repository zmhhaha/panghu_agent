"""Owner-scoped working knowledge, separate from immutable published Skills.

Raw experiences are archived verbatim; recording a render, failure or feedback
does not establish quality or human approval. Wiki pages may be revised,
corrected and deprecated using compare-and-swap. An Agent receives a frozen
snapshot, proposes evidence-bound changes, and the service may apply those
changes atomically. None of these operations publishes or loads a Skill.

This internal persistence adapter relies on its caller to authenticate owners
and import genuine run/feedback records. Hashes establish exact contents, not
the truth of a caller's claims. It never fetches reference URLs or runs code.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time

from agent_service.contracts import ServiceError, canonical_bytes, digest


from agent_service.animation_knowledge_contract import (
    EXPERIENCE_SCHEMA, PAGE_SCHEMA, SNAPSHOT_SCHEMA, MAX_EXPERIENCE_BYTES,
    MAX_PAGE_BYTES, MAX_SNAPSHOT_BYTES, MAX_PROPOSALS, MAX_PROPOSAL_BYTES, STATUSES, CHANGE_KINDS,
    RELATIONS, _require, _text, _owner, _id, _copy, _ids, _sealed, _checked,
    _proposal, validate_snapshot, validate_experience, validate_page, validate_knowledge_proposals,
)


_TABLES = frozenset({'knowledge_meta', 'knowledge_experiences', 'knowledge_heads',
                     'knowledge_revisions', 'knowledge_operations', 'knowledge_snapshots'})


class AnimationKnowledgeStore:
    """Independent SQLite database. All mutation and replay is transactional."""

    def __init__(self, path):
        self.path = Path(path).absolute()
        for item in (self.path, *self.path.parents):
            _require(not item.is_symlink(), 'animation_knowledge_path_invalid', 422)

    @contextmanager
    def _db(self, *, write=False):
        if not self.path.exists() and not write:
            yield None
            return
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        _require(not self.path.is_symlink(), 'animation_knowledge_path_invalid', 422)
        db = (sqlite3.connect(self.path, timeout=15, isolation_level=None) if write else
              sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=15, isolation_level=None))
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute('PRAGMA synchronous=FULL')
                db.execute('BEGIN IMMEDIATE')
            else:
                db.execute('BEGIN')
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            _require(tables <= _TABLES and (not tables and write or tables == _TABLES),
                     'animation_knowledge_database_not_isolated', 409)
            if write and not tables:
                db.execute('CREATE TABLE knowledge_meta (schema TEXT PRIMARY KEY)')
                db.execute('INSERT INTO knowledge_meta VALUES (?)', ('animation-knowledge-1',))
                db.execute('CREATE TABLE knowledge_experiences (owner TEXT, kind TEXT, source_id TEXT,'
                           ' id TEXT, content_sha256 TEXT, record BLOB NOT NULL,'
                           ' PRIMARY KEY(owner,kind,source_id), UNIQUE(owner,id))')
                db.execute('CREATE TABLE knowledge_heads (owner TEXT, page_id TEXT, revision INTEGER NOT NULL,'
                           ' PRIMARY KEY(owner,page_id))')
                db.execute('CREATE TABLE knowledge_revisions (owner TEXT, page_id TEXT, revision INTEGER,'
                           ' record BLOB NOT NULL, PRIMARY KEY(owner,page_id,revision))')
                db.execute('CREATE TABLE knowledge_operations (owner TEXT, kind TEXT, operation_id TEXT,'
                           ' request_sha256 TEXT NOT NULL, response BLOB NOT NULL,'
                           ' PRIMARY KEY(owner,kind,operation_id))')
                db.execute('CREATE TABLE knowledge_snapshots (owner TEXT, id TEXT, record BLOB NOT NULL,'
                           ' PRIMARY KEY(owner,id))')
            metadata = db.execute('SELECT schema FROM knowledge_meta').fetchall()
            _require(len(metadata) == 1 and metadata[0][0] == 'animation-knowledge-1',
                     'animation_knowledge_database_not_isolated', 409)
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _experience(db, owner, record_id):
        row = None if db is None else db.execute(
            'SELECT record FROM knowledge_experiences WHERE owner=? AND id=?', (owner, _id(record_id))).fetchone()
        _require(row is not None, 'animation_knowledge_not_found', 404)
        value = validate_experience(json.loads(row['record']), owner=owner)
        _require(value['id'] == record_id,
                 'animation_knowledge_record_changed', 409)
        return value

    def record_experience(self, owner, kind, source_id, document):
        owner, kind, source_id = _owner(owner), _id(kind), _text(source_id, 2000)
        _require(kind != 'knowledge_proposal', 'animation_knowledge_proposal_api_required', 422)
        return self._record_experience(owner, kind, source_id, document)

    def _record_experience(self, owner, kind, source_id, document):
        _require(type(document) is dict and bool(document))
        document = _copy(document, MAX_PROPOSAL_BYTES if kind == 'knowledge_proposal' else MAX_EXPERIENCE_BYTES)
        with self._db(write=True) as db:
            row = db.execute('SELECT id FROM knowledge_experiences WHERE owner=? AND kind=? AND source_id=?',
                             (owner, kind, source_id)).fetchone()
            if row:
                record = self._experience(db, owner, row['id'])
                _require(record['content_sha256'] == digest(document), 'animation_knowledge_idempotency_conflict', 409)
                return record
            value = _sealed(dict(schema=EXPERIENCE_SCHEMA,
                id='exp_' + digest(dict(owner=owner, kind=kind, source_id=source_id)),
                owner=owner, kind=kind, source_id=source_id, document=document,
                content_sha256=digest(document), created_at=time.time()))
            db.execute('INSERT INTO knowledge_experiences VALUES (?,?,?,?,?,?)',
                       (owner, kind, source_id, value['id'], value['content_sha256'], canonical_bytes(value)))
            return value

    def get_experience(self, owner, record_id):
        with self._db() as db:
            return self._experience(db, _owner(owner), record_id)

    def list_experiences(self, owner, *, kind=None, limit=200):
        owner = _owner(owner)
        _require(type(limit) is int and 1 <= limit <= 512)
        if kind is not None:
            _id(kind)
        with self._db() as db:
            if db is None:
                return []
            rows = db.execute('SELECT id FROM knowledge_experiences WHERE owner=?'
                + (' AND kind=?' if kind is not None else '') + ' ORDER BY rowid DESC LIMIT ?',
                (owner, kind, limit) if kind is not None else (owner, limit)).fetchall()
            return [self._experience(db, owner, row['id']) for row in rows]

    @staticmethod
    def _page(db, owner, page_id, revision=None):
        _id(page_id)
        if revision is not None:
            _require(type(revision) is int and revision >= 1)
        row = None if db is None else db.execute(
            'SELECT record FROM knowledge_revisions WHERE owner=? AND page_id=?'
            + (' AND revision=?' if revision is not None else ' ORDER BY revision DESC LIMIT 1'),
            (owner, page_id, revision) if revision is not None else (owner, page_id)).fetchone()
        _require(row is not None, 'animation_knowledge_not_found', 404)
        value = validate_page(json.loads(row['record']), owner=owner)
        _require(value['page_id'] == page_id and (revision is None or revision == value['revision']),
                 'animation_knowledge_record_changed', 409)
        return value

    def get_page(self, owner, page_id, *, revision=None):
        with self._db() as db:
            return self._page(db, _owner(owner), page_id, revision)

    def list_pages(self, owner, *, status=None, limit=200):
        owner = _owner(owner)
        _require(status is None or type(status) is str and status in STATUSES)
        _require(type(limit) is int and 1 <= limit <= 256)
        with self._db() as db:
            if db is None:
                return []
            rows = db.execute('SELECT page_id FROM knowledge_heads WHERE owner=? ORDER BY page_id', (owner,)).fetchall()
            pages = [self._page(db, owner, row['page_id']) for row in rows]
            return [page for page in pages if status is None or page['status'] == status][:limit]

    def page_history(self, owner, page_id):
        owner, page_id = _owner(owner), _id(page_id)
        with self._db() as db:
            self._page(db, owner, page_id)
            rows = db.execute('SELECT revision FROM knowledge_revisions WHERE owner=? AND page_id=? ORDER BY revision',
                              (owner, page_id)).fetchall()
            return [self._page(db, owner, page_id, row['revision']) for row in rows]

    @staticmethod
    def _replay(db, owner, kind, operation_id, request):
        row = db.execute('SELECT request_sha256,response FROM knowledge_operations'
                         ' WHERE owner=? AND kind=? AND operation_id=?', (owner, kind, operation_id)).fetchone()
        if row is None:
            return None
        _require(row['request_sha256'] == digest(request), 'animation_knowledge_idempotency_conflict', 409)
        return json.loads(row['response'])

    @staticmethod
    def _remember(db, owner, kind, operation_id, request, response):
        db.execute('INSERT INTO knowledge_operations VALUES (?,?,?,?,?)',
                   (owner, kind, operation_id, digest(request), canonical_bytes(response)))

    def _revise(self, db, owner, value):
        page_id, expected = value['page_id'], value['expected_revision']
        head = db.execute('SELECT revision FROM knowledge_heads WHERE owner=? AND page_id=?', (owner, page_id)).fetchone()
        _require((head['revision'] if head else 0) == expected, 'animation_knowledge_revision_conflict', 409)
        previous = self._page(db, owner, page_id, expected) if expected else None
        for record_id in value['evidence_ids']:
            evidence = self._experience(db, owner, record_id)
            _require(evidence['kind'] != 'knowledge_proposal', 'animation_knowledge_raw_evidence_required', 422)
        for relation in value['relations']:
            target = self._page(db, owner, relation['page_id'], relation['revision'])
            _require(target['content_sha256'] == relation['content_sha256'], 'animation_knowledge_relation_changed', 409)
        content = {key: value[key] for key in ('body', 'status', 'evidence_ids', 'relations')}
        record = _sealed(dict(schema=PAGE_SCHEMA, owner=owner, page_id=page_id,
            revision=expected + 1, previous_revision_sha256=previous['sha256'] if previous else None,
            **content, content_sha256=digest(content), change_kind=value['change_kind'],
            change_note=value['change_note'], created_at=time.time()))
        db.execute('INSERT INTO knowledge_revisions VALUES (?,?,?,?)',
                   (owner, page_id, record['revision'], canonical_bytes(record)))
        db.execute('INSERT INTO knowledge_heads VALUES (?,?,?) ON CONFLICT(owner,page_id) DO UPDATE SET revision=excluded.revision',
                   (owner, page_id, record['revision']))
        return record

    def revise_page(self, owner, page_id, body, *, expected_revision, evidence_ids,
                    change_note, status='provisional', change_kind=None, relations=(), operation_id=None):
        owner = _owner(owner)
        _require(type(evidence_ids) in (list, tuple) and type(relations) in (list, tuple))
        value = dict(page_id=page_id, body=body, expected_revision=expected_revision,
                     evidence_ids=list(evidence_ids), change_note=change_note, status=status, relations=list(relations))
        if change_kind is not None:
            value['change_kind'] = change_kind
        value = _proposal(value)
        operation_id = _id(operation_id) if operation_id is not None else 'revision_' + digest(value)
        with self._db(write=True) as db:
            prior = self._replay(db, owner, 'revision', operation_id, value)
            if prior is not None:
                return validate_page(prior, owner=owner)
            record = self._revise(db, owner, value)
            self._remember(db, owner, 'revision', operation_id, value, record)
            return record

    def snapshot(self, owner, *, page_ids=None, experience_ids=()):
        owner, requested = _owner(owner), _ids(experience_ids, maximum=512)
        if page_ids is not None:
            page_ids = _ids(page_ids)
        with self._db(write=True) as db:
            if page_ids is None:
                page_ids = [row['page_id'] for row in db.execute(
                    'SELECT page_id FROM knowledge_heads WHERE owner=? ORDER BY page_id', (owner,))]
                _require(len(page_ids) <= 256, 'animation_knowledge_snapshot_budget', 413)
                pages = [self._page(db, owner, page_id) for page_id in page_ids]
                pages = [page for page in pages if page['status'] != 'deprecated']
            else:
                pages = [self._page(db, owner, page_id) for page_id in page_ids]
            required = set(requested)
            for page in pages:
                required.update(page['evidence_ids'])
            _require(len(required) <= 512, 'animation_knowledge_snapshot_budget', 413)
            experiences = [self._experience(db, owner, record_id) for record_id in sorted(required)]
            identity = dict(owner=owner, pages=[dict(page_id=p['page_id'], revision=p['revision'], sha256=p['sha256']) for p in pages],
                            experiences=[dict(id=e['id'], sha256=e['sha256']) for e in experiences])
            snapshot_id = 'snapshot_' + digest(identity)
            row = db.execute('SELECT record FROM knowledge_snapshots WHERE owner=? AND id=?', (owner, snapshot_id)).fetchone()
            if row:
                return validate_snapshot(json.loads(row['record']), owner=owner)
            value = _sealed(dict(schema=SNAPSHOT_SCHEMA, id=snapshot_id, owner=owner,
                                 created_at=time.time(), pages=pages, experiences=experiences))
            value = validate_snapshot(value, owner=owner)
            db.execute('INSERT INTO knowledge_snapshots VALUES (?,?,?)', (owner, snapshot_id, canonical_bytes(value)))
            return value

    def get_snapshot(self, owner, snapshot_id):
        owner, snapshot_id = _owner(owner), _id(snapshot_id)
        with self._db() as db:
            row = None if db is None else db.execute('SELECT record FROM knowledge_snapshots WHERE owner=? AND id=?',
                                                     (owner, snapshot_id)).fetchone()
            _require(row is not None, 'animation_knowledge_not_found', 404)
            return validate_snapshot(json.loads(row['record']), owner=owner)

    def record_proposals(self, owner, source_id, proposals, *, snapshot_id):
        owner, source_id = _owner(owner), _text(source_id, 2000)
        snapshot = self.get_snapshot(owner, snapshot_id)
        proposals = validate_knowledge_proposals(proposals, snapshot)
        return self._record_experience(owner, 'knowledge_proposal', source_id,
            dict(snapshot_id=snapshot['id'], snapshot_sha256=snapshot['sha256'], proposals=proposals))

    def apply_proposals(self, owner, proposal_id):
        """Apply one immutable candidate group once, or fail the entire CAS.

        A conflict leaves the archived candidate intact and changes no pages.
        The service may record the actual conflict as a separate experience.
        An existing successful operation is returned after restart even if the
        page heads have since advanced. This never publishes a production Skill.
        """
        owner, proposal_id = _owner(owner), _id(proposal_id)
        with self._db(write=True) as db:
            candidate = self._experience(db, owner, proposal_id)
            _require(candidate['kind'] == 'knowledge_proposal', 'animation_knowledge_proposal_required', 422)
            document = candidate['document']
            request = dict(proposal_id=proposal_id, proposal_sha256=candidate['sha256'])
            prior = self._replay(db, owner, 'apply', proposal_id, request)
            if prior is not None:
                return [validate_page(record, owner=owner) for record in prior]
            row = db.execute('SELECT record FROM knowledge_snapshots WHERE owner=? AND id=?',
                             (owner, document['snapshot_id'])).fetchone()
            _require(row is not None, 'animation_knowledge_not_found', 404)
            snapshot = validate_snapshot(json.loads(row['record']), owner=owner)
            _require(snapshot['sha256'] == document['snapshot_sha256'], 'animation_knowledge_record_changed', 409)
            proposals = validate_knowledge_proposals(document['proposals'], snapshot)
            result = [self._revise(db, owner, value) for value in proposals]
            self._remember(db, owner, 'apply', proposal_id, request, result)
            return result
