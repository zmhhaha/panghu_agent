"""Freeze published creative knowledge for a new production Agent input.

Retrieval is a conservative lexical shortlist, not creative selection. The
Agent may adapt or reject every offered method and create freely. A delivery
receipt proves delivery to this adapter, never model observation, application,
renderability or improved expression. This module does not modify frozen v1
creative input/result contracts; a caller must use an explicitly admitted
contract to transport the returned snapshot and collect its decision.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3

from agent_service.contracts import ServiceError, canonical_bytes, digest


SCHEMA = 'video.production-method-delivery.v1'
DECISION_SCHEMA = 'video.production-method-decision.v1'
MAX_DELIVERY_BYTES = 256 * 1024
_CONTENT = frozenset({'title', 'problem', 'guidance', 'applicability', 'limitations',
                      'criteria', 'runtime_requirements'})
_ENGINES = frozenset({'html-svg', 'canvas', 'three'})
_TABLE = 'animation_method_production_freezes'
_COMMON = frozenset({'动画', '视频', '效果', '方法', '展示', '这个', '一个', '如何',
                      '进行', '通过', '需要', '可以', '让观', '观众', '看懂', '理解',
                      'animation', 'video', 'effect', 'show', 'the', 'and', 'with'})
POLICY = ('Read each offered method completely, then decide whether to apply, adapt or reject it '
          'for this exact production task. Return one explicit decision and reason per offered '
          'version. A shortlist is optional creative knowledge, not a source template. Do not '
          'copy demonstration source or claim that delivery proves use or quality. When no '
          'method fits, create freely from the task, narration and actual audio clock.')


def _require(value, code='production_method_delivery_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def _tokens(value):
    words = set(re.findall(r'[a-z][a-z0-9_-]{1,}', value.casefold()))
    for phrase in re.findall(r'[\u3400-\u9fff]+', value):
        words.update(phrase[index:index + 2] for index in range(len(phrase) - 1))
    return words - _COMMON


def _published(row):
    """Check the immutable content/publication binding, without trusting a flag."""
    _require(type(row) is dict and _CONTENT <= set(row))
    checksum = digest({key: row[key] for key in _CONTENT})
    publication = row.get('publication')
    _require(type(row.get('id')) is str and row.get('version_id', row['id']) == row['id']
             and row.get('content_sha256') == checksum
             and row.get('status') == 'published' and type(publication) is dict
             and publication.get('method_version_id') == row['id']
             and publication.get('method_content_sha256') == checksum
             and bool(publication.get('reference_validation_ids'))
             and bool(publication.get('transfer_validation_ids')),
             'production_method_not_published', 409)
    _require(type(row['runtime_requirements']) is dict
             and row['runtime_requirements'].get('engine') in _ENGINES)
    return deepcopy(row)


def validate_delivery(snapshot):
    _require(type(snapshot) is dict and snapshot.get('schema') == SCHEMA
             and set(snapshot) == {'schema', 'recipient_owner', 'consumer_id', 'task_sha256', 'allowed_engines',
                 'selection', 'methods', 'read_receipt', 'policy', 'evidence_scope', 'sha256'})
    _require(snapshot['sha256'] == digest({key: value for key, value in snapshot.items() if key != 'sha256'})
             and snapshot['policy'] == POLICY
             and snapshot['evidence_scope'] == 'frozen_input_delivery_not_agent_application')
    _require(type(snapshot['methods']) is list and len(snapshot['methods']) <= 3
             and type(snapshot['allowed_engines']) is list and bool(snapshot['allowed_engines'])
             and set(snapshot['allowed_engines']) <= _ENGINES)
    methods = [_published(row) for row in snapshot['methods']]
    _require(len({row['id'] for row in methods}) == len(methods)
             and all(row['runtime_requirements']['engine'] in snapshot['allowed_engines'] for row in methods))
    receipt = snapshot['read_receipt']
    if methods:
        _require(type(receipt) is dict and receipt.get('consumer_id') == snapshot['consumer_id']
                 and receipt.get('owner') == snapshot['recipient_owner']
                 and receipt.get('scope') == 'methods_delivered_to_caller_not_model_observation'
                 and receipt.get('method_versions') == [dict(version_id=row['id'],
                    content_sha256=row['content_sha256']) for row in methods],
                 'production_method_receipt_mismatch', 409)
    else:
        _require(receipt is None)
    _require(len(canonical_bytes(snapshot)) <= MAX_DELIVERY_BYTES,
             'production_method_delivery_too_large', 413)
    return deepcopy(snapshot)


class MethodProductionAdapter:
    """Caller supplies authenticated catalog/read functions, with no retries.

    ``catalogue(owner)`` returns published records or ``{'methods': records}``.
    ``read(owner, body)`` follows the existing methods/read HTTP contract.
    ``ledger_path`` is a dedicated durable SQLite database. The selected intent
    is committed before requesting a delivery receipt. Recoveries use that
    intent even when no returned snapshot reached the caller before a crash.
    Persist the returned snapshot with the operation before Agent admission.
    """

    def __init__(self, catalogue, read, *, ledger_path):
        self.catalogue, self.read = catalogue, read
        self.ledger_path = Path(ledger_path).absolute()
        _require(not any(path.is_symlink() or getattr(path, 'is_junction', lambda: False)()
                         for path in (self.ledger_path, *self.ledger_path.parents)),
                 'production_method_ledger_path_invalid')
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            _require(tables <= {_TABLE}, 'production_method_ledger_not_isolated', 409)
            db.execute('CREATE TABLE IF NOT EXISTS ' + _TABLE + ' ('
                       'owner TEXT NOT NULL,consumer_id TEXT NOT NULL,binding BLOB NOT NULL,'
                       'binding_sha256 TEXT NOT NULL,intent BLOB NOT NULL,intent_sha256 TEXT NOT NULL,'
                       'snapshot BLOB,PRIMARY KEY(owner,consumer_id))')
            db.commit()

    def _connect(self):
        _require(not any(path.is_symlink() or getattr(path, 'is_junction', lambda: False)()
                         for path in (self.ledger_path, *self.ledger_path.parents)),
                 'production_method_ledger_path_invalid')
        db = sqlite3.connect(self.ledger_path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        return db

    def _entry(self, binding, *, intent=None, snapshot=None):
        """One atomic local reservation or completion, without remote calls."""
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM ' + _TABLE + ' WHERE owner=? AND consumer_id=?',
                             (binding['recipient_owner'], binding['consumer_id'])).fetchone()
            if row is not None:
                original_binding, original_intent = json.loads(row['binding']), json.loads(row['intent'])
                _require(bytes(row['binding']) == canonical_bytes(original_binding)
                         and digest(original_binding) == row['binding_sha256']
                         and bytes(row['intent']) == canonical_bytes(original_intent)
                         and digest(original_intent) == row['intent_sha256'],
                         'production_method_ledger_changed', 409)
                _require(original_binding == binding,
                         'production_method_frozen_input_changed', 409)
                original_snapshot = validate_delivery(json.loads(row['snapshot'])) if row['snapshot'] else None
                if original_snapshot:
                    _require({key: value for key, value in original_snapshot.items()
                              if key not in {'read_receipt', 'sha256'}} == original_intent,
                             'production_method_ledger_changed', 409)
                if snapshot is not None:
                    _require(original_intent == intent
                             and (original_snapshot is None or original_snapshot == snapshot),
                             'production_method_frozen_input_changed', 409)
                intent = original_intent
            else:
                if intent is None:
                    db.commit()
                    return None
                _require(len(canonical_bytes(intent)) <= MAX_DELIVERY_BYTES,
                         'production_method_delivery_too_large', 413)
                db.execute('INSERT INTO ' + _TABLE + ' VALUES (?,?,?,?,?,?,NULL)',
                    (binding['recipient_owner'], binding['consumer_id'], canonical_bytes(binding),
                     digest(binding), canonical_bytes(intent), digest(intent)))
                original_snapshot = None
            if snapshot is not None and original_snapshot is None:
                snapshot = validate_delivery(snapshot)
                db.execute('UPDATE ' + _TABLE + ' SET snapshot=? WHERE owner=? AND consumer_id=?',
                    (canonical_bytes(snapshot), binding['recipient_owner'], binding['consumer_id']))
                original_snapshot = snapshot
            db.commit()
            return dict(intent=deepcopy(intent), snapshot=deepcopy(original_snapshot))

    def _persist_snapshot(self, binding, intent, snapshot):
        return self._entry(binding, intent=intent, snapshot=snapshot)['snapshot']

    def _complete(self, binding, intent):
        selected, receipt = intent['methods'], None
        if selected:
            delivered = self.read(binding['recipient_owner'], dict(
                method_version_ids=[row['id'] for row in selected], consumer_id=binding['consumer_id']))
            _require(type(delivered) is dict and type(delivered.get('methods')) is list,
                     'production_method_read_invalid', 502)
            _require(delivered['methods'] == selected, 'production_method_catalog_changed', 409)
            receipt = delivered.get('read_receipt')
        snapshot = dict(intent, read_receipt=receipt)
        snapshot['sha256'] = digest(snapshot)
        return self._persist_snapshot(binding, intent, validate_delivery(snapshot))

    def freeze(self, owner, consumer_id, task, allowed_engines, *, existing=None):
        _require(type(owner) is str and 0 < len(owner) <= 200
                 and not any(ord(char) < 32 for char in owner))
        _require(type(consumer_id) is str and 0 < len(consumer_id) <= 400
                 and not any(ord(char) < 32 for char in consumer_id))
        _require(type(task) is dict and bool(task)
                 and len(canonical_bytes(task)) <= 128 * 1024)
        _require(type(allowed_engines) is list and bool(allowed_engines)
                 and len(set(allowed_engines)) == len(allowed_engines)
                 and set(allowed_engines) <= _ENGINES)
        task_sha256 = digest(task)
        binding = dict(recipient_owner=owner, consumer_id=consumer_id,
                       task_sha256=task_sha256, allowed_engines=list(allowed_engines))
        entry = self._entry(binding)
        if existing is not None:
            snapshot = validate_delivery(existing)
            _require(snapshot['recipient_owner'] == owner and snapshot['consumer_id'] == consumer_id and snapshot['task_sha256'] == task_sha256
                     and snapshot['allowed_engines'] == allowed_engines,
                     'production_method_frozen_input_changed', 409)
            intent = {key: value for key, value in snapshot.items() if key not in {'read_receipt', 'sha256'}}
            return self._persist_snapshot(binding, intent, snapshot)
        if entry is not None:
            return entry['snapshot'] or self._complete(binding, entry['intent'])
        document = self.catalogue(owner)
        records = document.get('methods') if type(document) is dict else document
        _require(type(records) is list, 'production_method_catalog_invalid', 502)
        query = _tokens(' '.join(value for value in task.values() if type(value) is str))
        latest, exclusions = {}, []
        for row in records:
            # A malformed published record is an integration failure, not a
            # silent "no match" that would mask an unavailable knowledge path.
            row = _published(row)
            if row['runtime_requirements']['engine'] not in allowed_engines:
                exclusions.append(dict(version_id=row['id'], reason='engine_not_admitted'))
                continue
            previous = latest.get(row['method_id'])
            if previous is None or row['version'] > previous['version']:
                latest[row['method_id']] = row
        ranked = []
        for row in latest.values():
            terms = _tokens(' '.join(row[key] for key in ('title', 'problem', 'applicability')))
            shared = sorted(query & terms)
            if shared:
                ranked.append((len(shared), row['id'], shared, row))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        selected, summaries, size = [], [], 0
        # Keep exact methods and receipt together well below the package limit;
        # leave room for selection evidence, policy and the receipt envelope.
        for score, version_id, shared, row in ranked:
            if len(selected) == 3:
                exclusions.append(dict(version_id=version_id, reason='shortlist_limit'))
                continue
            row_size = len(canonical_bytes(row))
            if row_size + size > MAX_DELIVERY_BYTES - 32 * 1024:
                exclusions.append(dict(version_id=version_id, reason='complete_method_exceeds_budget'))
                continue
            selected.append(row)
            summaries.append(dict(version_id=version_id, content_sha256=row['content_sha256'],
                                  lexical_score=score, matched_terms=shared))
            size += row_size
        intent = dict(schema=SCHEMA, recipient_owner=owner, consumer_id=consumer_id, task_sha256=task_sha256,
            allowed_engines=list(allowed_engines), selection=dict(
                algorithm='conservative-lexical-shortlist-v1', offered=summaries,
                excluded=exclusions, disposition='offered_for_agent_decision' if selected else 'free_creation_no_match'),
            methods=selected, policy=POLICY,
            evidence_scope='frozen_input_delivery_not_agent_application')
        # Another concurrent caller can win this reservation. Its immutable
        # intent is authoritative, including an empty shortlist.
        entry = self._entry(binding, intent=intent)
        return entry['snapshot'] or self._complete(binding, entry['intent'])


def collect_method_decision(snapshot, decision):
    """Bind an Agent declaration; do not turn it into an observed quality claim."""
    snapshot = validate_delivery(snapshot)
    _require(type(decision) is dict and set(decision) == {'delivery_sha256', 'decisions'}
             and decision['delivery_sha256'] == snapshot['sha256']
             and type(decision['decisions']) is list,
             'production_method_decision_binding_invalid', 409)
    offered = {row['id']: row['content_sha256'] for row in snapshot['methods']}
    ids = []
    for row in decision['decisions']:
        _require(type(row) is dict and set(row) == {'version_id', 'content_sha256', 'action', 'reason'}
                 and row.get('version_id') in offered
                 and row.get('content_sha256') == offered[row['version_id']]
                 and row.get('action') in {'apply', 'adapt', 'reject'}
                 and type(row.get('reason')) is str and bool(row['reason'].strip())
                 and len(row['reason']) <= 8000,
                 'production_method_decision_invalid', 422)
        ids.append(row['version_id'])
    _require(len(ids) == len(set(ids)) and set(ids) == set(offered),
             'production_method_decision_incomplete', 422)
    result = dict(schema=DECISION_SCHEMA, delivery_sha256=snapshot['sha256'],
        consumer_id=snapshot['consumer_id'], task_sha256=snapshot['task_sha256'],
        decisions=deepcopy(decision['decisions']), evidence_scope='agent_self_report_not_observed_application_or_quality')
    result['sha256'] = digest(result)
    return result
