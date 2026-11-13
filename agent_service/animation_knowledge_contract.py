"""Pure immutable Wiki snapshot/candidate contracts, not Skill publication.

Hashes bind exact contents; trusted adapters must authenticate custody.
"""
import json
import math
import re

from agent_service.contracts import ServiceError, canonical_bytes, digest


EXPERIENCE_SCHEMA = 'video.animation-experience.v1'
PAGE_SCHEMA = 'video.animation-knowledge-page.v1'
SNAPSHOT_SCHEMA = 'video.animation-knowledge-snapshot.v1'
MAX_EXPERIENCE_BYTES = 256 * 1024
MAX_PROPOSAL_BYTES = 512 * 1024
MAX_PAGE_BYTES = 128 * 1024
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
MAX_PROPOSALS = 8
STATUSES = frozenset({'provisional', 'active', 'deprecated'})
CHANGE_KINDS = frozenset({'create', 'revise', 'correct', 'deprecate'})
RELATIONS = frozenset({'corrects', 'supersedes', 'contradicts', 'extends'})
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def _require(value, code='invalid_animation_knowledge', status=422):
    if not value:
        raise ServiceError(code, status)


def _text(value, maximum=16000):
    _require(type(value) is str and bool(value.strip()) and len(value) <= maximum
             and not any((ord(c) < 32 and c not in '\r\n\t') or ord(c) == 127
                         or 0xD800 <= ord(c) <= 0xDFFF for c in value))
    return value


def _owner(value):
    _text(value, 500)
    _require(not any(ord(c) < 32 for c in value), 'invalid_animation_knowledge_owner', 401)
    return value


def _id(value):
    _require(type(value) is str and _ID.fullmatch(value))
    return value


def _copy(value, maximum=MAX_SNAPSHOT_BYTES):
    try:
        raw = canonical_bytes(value)
        _require(len(raw) <= maximum, 'animation_knowledge_too_large', 413)
        return json.loads(raw)
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise ServiceError('invalid_animation_knowledge_json', 422) from None


def _ids(value, *, minimum=0, maximum=256):
    _require(type(value) in (list, tuple) and minimum <= len(value) <= maximum)
    result = [_id(item) for item in value]
    _require(len(set(result)) == len(result))
    return sorted(result)


def _sealed(body):
    return dict(body, sha256=digest(body))


def _checked(value, schema, owner):
    _require(type(value) is dict and value.get('schema') == schema
             and value.get('owner') == owner and type(value.get('sha256')) is str
             and _SHA.fullmatch(value['sha256'])
             and digest({key: child for key, child in value.items() if key != 'sha256'}) == value['sha256'],
             'animation_knowledge_record_changed', 409)
    return value


def _page_body(body):
    _require(type(body) is dict and {'title', 'content'} <= set(body)
             and set(body) <= {'title', 'content', 'applicability', 'counterexamples', 'limitations'})
    body = _copy(body, MAX_PAGE_BYTES)
    _text(body['title'], 1000)
    _text(body['content'], 64000)
    for key in ('applicability', 'limitations'):
        values = body.setdefault(key, [])
        _require(type(values) is list and len(values) <= 32)
        for item in values:
            _text(item, 4000)
    rows = body.setdefault('counterexamples', [])
    _require(type(rows) is list and len(rows) <= 32)
    for row in rows:
        _require(type(row) is dict and set(row) == {'description', 'evidence_ids'})
        _text(row['description'], 8000)
        row['evidence_ids'] = _ids(row['evidence_ids'], minimum=1, maximum=32)
    _copy(body, MAX_PAGE_BYTES)
    return body


def _proposal(value):
    required = {'page_id', 'expected_revision', 'body', 'evidence_ids', 'change_note'}
    _require(type(value) is dict and required <= set(value)
             and set(value) <= required | {'status', 'change_kind', 'relations'})
    value = _copy(value, MAX_PAGE_BYTES + 64 * 1024)
    _id(value['page_id'])
    revision = value['expected_revision']
    _require(type(revision) is int and 0 <= revision < 1000000)
    value['body'] = _page_body(value['body'])
    value['evidence_ids'] = _ids(value['evidence_ids'], minimum=1)
    _text(value['change_note'], 8000)
    value.setdefault('status', 'provisional')
    value.setdefault('change_kind', 'create' if revision == 0 else 'revise')
    _require(type(value['status']) is str and value['status'] in STATUSES
             and type(value['change_kind']) is str and value['change_kind'] in CHANGE_KINDS)
    _require((revision == 0) == (value['change_kind'] == 'create'))
    _require((value['status'] == 'deprecated') == (value['change_kind'] == 'deprecate'))
    _require(all(set(row['evidence_ids']) <= set(value['evidence_ids'])
                 for row in value['body']['counterexamples']))
    relations = value.setdefault('relations', [])
    _require(type(relations) is list and len(relations) <= 32)
    for relation in relations:
        _require(type(relation) is dict and set(relation) == {
            'relation', 'page_id', 'revision', 'content_sha256'})
        _require(type(relation['relation']) is str and relation['relation'] in RELATIONS)
        _id(relation['page_id'])
        _require(type(relation['revision']) is int and relation['revision'] >= 1
                 and type(relation['content_sha256']) is str and _SHA.fullmatch(relation['content_sha256']))
    _require(len({digest(row) for row in relations}) == len(relations))
    value['relations'] = sorted(relations, key=digest)
    return value


def _timestamp(value):
    _require(type(value) in (int, float) and math.isfinite(value) and value >= 0)


def validate_experience(record, *, owner=None):
    record = _copy(record)
    _require(type(record) is dict)
    actual_owner = _owner(record.get('owner'))
    _require(owner is None or actual_owner == _owner(owner), 'animation_knowledge_not_found', 404)
    _checked(record, EXPERIENCE_SCHEMA, actual_owner)
    _require(set(record) == {'schema', 'id', 'owner', 'kind', 'source_id', 'document',
                            'content_sha256', 'created_at', 'sha256'})
    _id(record['kind'])
    _text(record['source_id'], 2000)
    _timestamp(record['created_at'])
    _require(type(record['document']) is dict and bool(record['document'])
             and record['content_sha256'] == digest(record['document'])
             and record['id'] == 'exp_' + digest(dict(owner=actual_owner, kind=record['kind'], source_id=record['source_id'])),
             'animation_knowledge_record_changed', 409)
    _copy(record['document'], MAX_PROPOSAL_BYTES if record['kind'] == 'knowledge_proposal' else MAX_EXPERIENCE_BYTES)
    return record


def validate_page(record, *, owner=None):
    record = _copy(record, MAX_PAGE_BYTES + 64 * 1024)
    _require(type(record) is dict)
    actual_owner = _owner(record.get('owner'))
    _require(owner is None or actual_owner == _owner(owner), 'animation_knowledge_not_found', 404)
    _checked(record, PAGE_SCHEMA, actual_owner)
    _require(set(record) == {'schema', 'owner', 'page_id', 'revision', 'previous_revision_sha256',
        'body', 'status', 'evidence_ids', 'relations', 'content_sha256', 'change_kind', 'change_note', 'created_at', 'sha256'})
    _require(type(record['revision']) is int and 1 <= record['revision'] <= 1000000)
    _timestamp(record['created_at'])
    previous = record['previous_revision_sha256']
    _require(previous is None if record['revision'] == 1 else type(previous) is str and _SHA.fullmatch(previous))
    candidate = _proposal({key: record[key] for key in
        ('page_id', 'body', 'evidence_ids', 'status', 'change_kind', 'change_note', 'relations')}
        | dict(expected_revision=record['revision'] - 1))
    _require(all(candidate[key] == record[key] for key in
                 ('body', 'evidence_ids', 'status', 'change_kind', 'change_note', 'relations'))
             and record['content_sha256'] == digest({key: record[key] for key in
                 ('body', 'status', 'evidence_ids', 'relations')}),
             'animation_knowledge_record_changed', 409)
    return record


def validate_snapshot(snapshot, *, owner=None):
    """Check immutable wire contents; this does not authenticate their source."""
    snapshot = _copy(snapshot)
    _require(type(snapshot) is dict)
    actual_owner = _owner(snapshot.get('owner'))
    _require(owner is None or actual_owner == _owner(owner), 'animation_knowledge_not_found', 404)
    _checked(snapshot, SNAPSHOT_SCHEMA, actual_owner)
    _require(set(snapshot) == {'schema', 'id', 'owner', 'created_at', 'pages', 'experiences', 'sha256'})
    _id(snapshot['id'])
    _timestamp(snapshot['created_at'])
    _require(type(snapshot['pages']) is list and len(snapshot['pages']) <= 256
             and type(snapshot['experiences']) is list and len(snapshot['experiences']) <= 512)
    evidence = {}
    for record in snapshot['experiences']:
        validate_experience(record, owner=actual_owner)
        _require(record['id'] not in evidence, 'animation_knowledge_record_changed', 409)
        evidence[record['id']] = record
    pages = set()
    for page in snapshot['pages']:
        validate_page(page, owner=actual_owner)
        _require(page['page_id'] not in pages and set(page['evidence_ids']) <= set(evidence)
                 and all(evidence[record_id]['kind'] != 'knowledge_proposal' for record_id in page['evidence_ids']),
                 'animation_knowledge_record_changed', 409)
        pages.add(page['page_id'])
    _require(snapshot['pages'] == sorted(snapshot['pages'], key=lambda row: row['page_id'])
             and snapshot['experiences'] == sorted(snapshot['experiences'], key=lambda row: row['id']),
             'animation_knowledge_record_changed', 409)
    identity = dict(owner=actual_owner,
        pages=[dict(page_id=p['page_id'], revision=p['revision'], sha256=p['sha256']) for p in snapshot['pages']],
        experiences=[dict(id=e['id'], sha256=e['sha256']) for e in snapshot['experiences']])
    _require(snapshot['id'] == 'snapshot_' + digest(identity), 'animation_knowledge_record_changed', 409)
    return snapshot


def validate_knowledge_proposals(proposals, snapshot):
    """Normalize candidates against the exact knowledge the Agent received."""
    snapshot = validate_snapshot(snapshot)
    _require(type(proposals) is list and len(proposals) <= MAX_PROPOSALS)
    result = [_proposal(value) for value in proposals]
    _copy(result, MAX_PROPOSAL_BYTES - 1024)
    _require(len({value['page_id'] for value in result}) == len(result))
    pages = {page['page_id']: page for page in snapshot['pages']}
    evidence = {record['id'] for record in snapshot['experiences'] if record['kind'] != 'knowledge_proposal'}
    for value in result:
        page = pages.get(value['page_id'])
        _require(value['expected_revision'] == (page['revision'] if page else 0)
                 and set(value['evidence_ids']) <= evidence,
                 'animation_knowledge_proposal_snapshot_mismatch', 409)
        for relation in value['relations']:
            target = pages.get(relation['page_id'])
            _require(target is not None and target['revision'] == relation['revision']
                     and target['content_sha256'] == relation['content_sha256'],
                     'animation_knowledge_proposal_snapshot_mismatch', 409)
    return result


validate_proposals = validate_knowledge_proposals
