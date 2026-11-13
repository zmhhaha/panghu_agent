"""Durably freeze all compatible published Skills without choosing for an Agent.

Catalog/read functions are authenticated server adapters. Retrieval receipts
mean the host obtained bytes, never that an Agent read or applied knowledge.
Legacy max-three method delivery and its frozen history are unchanged.
"""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

from agent_service.animation_skills_contract import (
    ENGINES, bundle_files, freeze_skill_catalog, validate_skill_catalog,
)
from agent_service.contracts import ServiceError, canonical_bytes, digest


SCHEMA = 'video.production-skill-delivery.v1'
EVIDENCE_SCOPE = 'host_frozen_tool_resources_not_agent_read_application_or_quality'
MAX_DELIVERY_BYTES = 34 * 1024 * 1024
MAX_TASK_BYTES = 2 * 1024 * 1024
_TABLE = 'animation_skill_production_freezes'
_FIELDS = frozenset({'schema', 'recipient_owner', 'consumer_id', 'task_sha256', 'allowed_engines',
    'catalog', 'packages', 'retrieval_receipts', 'evidence_scope', 'sha256'})


def _require(value, code='production_skill_delivery_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def _identity(value, maximum=400):
    _require(type(value) is str and 0 < len(value) <= maximum and bool(value.strip())
             and not any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value))


def _canonical(value):
    try:
        return canonical_bytes(value)
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise ServiceError('production_skill_delivery_invalid', 422) from None


def validate_delivery(value):
    _canonical(value)
    _require(type(value) is dict and set(value) == _FIELDS and value['schema'] == SCHEMA
             and value['evidence_scope'] == EVIDENCE_SCOPE)
    _identity(value['recipient_owner'], 200)
    _identity(value['consumer_id'])
    catalog = validate_skill_catalog(value['catalog'])
    _require(all(value[key] == catalog[key] for key in (
        'recipient_owner', 'consumer_id', 'task_sha256', 'allowed_engines')),
        'production_skill_delivery_binding_mismatch', 409)
    _require(type(value['packages']) is dict
             and set(value['packages']) == {row['package_sha256'] for row in catalog['skills']})
    _require(all(key == package.get('sha256') for key, package in value['packages'].items()
                 if type(package) is dict)
             and all(type(package) is dict for package in value['packages'].values()))
    bundle_files(catalog, value['packages'])  # Exact immutable files and bounded membership.
    receipts = value['retrieval_receipts']
    _require(type(receipts) is list and len(receipts) <= 256)
    for receipt in receipts:
        _require(type(receipt) is dict and type(receipt.get('source')) is str and receipt.get('source') in {
            'authenticated_catalogue_full_records', 'authenticated_method_read'}
            and receipt.get('scope') == 'host_retrieval_not_agent_observation')
    _require(value['sha256'] == digest({key: item for key, item in value.items() if key != 'sha256'}),
             'production_skill_delivery_changed', 409)
    _require(len(_canonical(value)) <= MAX_DELIVERY_BYTES, 'production_skill_delivery_too_large', 413)
    return deepcopy(value)


class SkillProductionAdapter:
    """Reserve exact catalog intent before remote reads, never reselect on retry.

    ``catalogue(owner)`` returns full published methods or ``{'methods': rows}``.
    A metadata row can omit skill_package if it retains exact id/content hash
    and (when known) skill_package_sha256/publication_sha256. ``read(owner,
    body)`` resolves such versions through the existing authenticated read API.
    Full method records already contain the package and need no extra read.
    """

    def __init__(self, catalogue, read, ledger_path):
        self.catalogue, self.read = catalogue, read
        self.ledger_path = Path(ledger_path).absolute()
        _require(not any(path.is_symlink() or getattr(path, 'is_junction', lambda: False)()
                         for path in (self.ledger_path, *self.ledger_path.parents)),
                 'production_skill_ledger_path_invalid')
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            _require(tables <= {_TABLE}, 'production_skill_ledger_not_isolated', 409)
            db.execute('CREATE TABLE IF NOT EXISTS ' + _TABLE + ' ('
                'owner TEXT NOT NULL,consumer_id TEXT NOT NULL,binding BLOB NOT NULL,'
                'binding_sha256 TEXT NOT NULL,intent BLOB NOT NULL,intent_sha256 TEXT NOT NULL,'
                'snapshot BLOB,PRIMARY KEY(owner,consumer_id))')
            db.commit()

    def _connect(self):
        _require(not any(path.is_symlink() or getattr(path, 'is_junction', lambda: False)()
                         for path in (self.ledger_path, *self.ledger_path.parents)),
                 'production_skill_ledger_path_invalid')
        db = sqlite3.connect(self.ledger_path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        return db

    @staticmethod
    def _decoded(row):
        try:
            binding, intent = json.loads(row['binding']), json.loads(row['intent'])
        except (ValueError, TypeError, UnicodeError):
            raise ServiceError('production_skill_ledger_changed', 409) from None
        _require(bytes(row['binding']) == _canonical(binding) and digest(binding) == row['binding_sha256']
                 and bytes(row['intent']) == _canonical(intent) and digest(intent) == row['intent_sha256'],
                 'production_skill_ledger_changed', 409)
        snapshot = validate_delivery(json.loads(row['snapshot'])) if row['snapshot'] is not None else None
        _require(binding.get('recipient_owner') == row['owner'] and binding.get('consumer_id') == row['consumer_id'],
                 'production_skill_ledger_changed', 409)
        if snapshot:
            _require(all(snapshot[key] == binding[key] for key in binding),
                     'production_skill_ledger_changed', 409)
        return dict(binding=binding, intent=intent, snapshot=snapshot)

    def _entry(self, binding, *, intent=None, snapshot=None):
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM ' + _TABLE + ' WHERE owner=? AND consumer_id=?',
                (binding['recipient_owner'], binding['consumer_id'])).fetchone()
            if row is None:
                if intent is None:
                    db.commit()
                    return None
                _require(len(_canonical(intent)) <= MAX_DELIVERY_BYTES, 'production_skill_delivery_too_large', 413)
                db.execute('INSERT INTO ' + _TABLE + ' VALUES (?,?,?,?,?,?,NULL)',
                    (binding['recipient_owner'], binding['consumer_id'], _canonical(binding), digest(binding),
                     _canonical(intent), digest(intent)))
                current = dict(binding=deepcopy(binding), intent=deepcopy(intent), snapshot=None)
            else:
                current = self._decoded(row)
                _require(current['binding'] == binding, 'production_skill_frozen_input_changed', 409)
            if snapshot is not None:
                _require(current['intent'] == intent, 'production_skill_frozen_input_changed', 409)
                snapshot = validate_delivery(snapshot)
                if current['snapshot'] is not None:
                    # Independent authenticated reads can return different
                    # request receipts for the same immutable resources. The
                    # first committed snapshot owns those retrieval receipts;
                    # a concurrent completion must reuse it, not replace it.
                    resource_fields = _FIELDS - {'retrieval_receipts', 'sha256'}
                    _require(all(current['snapshot'][key] == snapshot[key] for key in resource_fields),
                             'production_skill_frozen_input_changed', 409)
                if current['snapshot'] is None:
                    db.execute('UPDATE ' + _TABLE + ' SET snapshot=? WHERE owner=? AND consumer_id=?',
                        (_canonical(snapshot), binding['recipient_owner'], binding['consumer_id']))
                    current['snapshot'] = snapshot
            db.commit()
            return deepcopy(current)

    def _complete(self, binding, intent):
        records = deepcopy(intent['methods'])
        full = [row for row in records if 'skill_package' in row]
        missing = [row for row in records if 'skill_package' not in row]
        receipts = []
        if full:
            receipts.append(dict(source='authenticated_catalogue_full_records',
                method_version_ids=[row['id'] for row in full], scope='host_retrieval_not_agent_observation'))
        for offset in range(0, len(missing), 16):
            _require(callable(self.read), 'production_skill_read_required', 503)
            group = missing[offset:offset + 16]
            body = dict(method_version_ids=[row['id'] for row in group],
                        consumer_id='skill-packages:' + digest({key: binding[key] for key in (
                            'recipient_owner', 'consumer_id')}) + ':' + str(offset // 16))
            response = self.read(binding['recipient_owner'], body)
            _require(type(response) is dict and type(response.get('methods')) is list
                     and len(response['methods']) == len(group), 'production_skill_read_invalid', 502)
            for expected, actual in zip(group, response['methods']):
                _require(type(actual) is dict and actual.get('id') == expected['id']
                         and actual.get('content_sha256') == expected['content_sha256']
                         and actual.get('schema') == 'video.animation-method.v2'
                         and actual.get('status') == 'published'
                         and type(actual.get('skill_package')) is dict,
                         'production_skill_catalog_changed', 409)
                for key, actual_value in (
                    ('skill_package_sha256', actual.get('skill_package', {}).get('sha256')),
                    ('publication_sha256', digest(actual.get('publication')))):
                    _require(key not in expected or expected[key] == actual_value,
                             'production_skill_catalog_changed', 409)
                full.append(actual)
            receipts.append(dict(source='authenticated_method_read', request=body,
                receipt=deepcopy(response.get('read_receipt')), scope='host_retrieval_not_agent_observation'))
        catalog = freeze_skill_catalog(full, recipient_owner=binding['recipient_owner'],
            consumer_id=binding['consumer_id'], task_sha256=binding['task_sha256'],
            allowed_engines=binding['allowed_engines'])
        wanted = {row['package_sha256'] for row in catalog['skills']}
        packages = {row['skill_package']['sha256']: row['skill_package'] for row in full
                    if row['skill_package']['sha256'] in wanted}
        result = dict(schema=SCHEMA, **binding, catalog=catalog, packages=packages,
                      retrieval_receipts=receipts, evidence_scope=EVIDENCE_SCOPE)
        result['sha256'] = digest(result)
        result = validate_delivery(result)
        return self._entry(binding, intent=intent, snapshot=result)['snapshot']

    def freeze(self, owner, consumer_id, task, engines, existing=None):
        _identity(owner, 200)
        _identity(consumer_id)
        _require(type(task) is dict and len(_canonical(task)) <= MAX_TASK_BYTES)
        _require(type(engines) is list and bool(engines) and all(type(v) is str and v in ENGINES for v in engines)
                 and len(set(engines)) == len(engines))
        binding = dict(recipient_owner=owner, consumer_id=consumer_id, task_sha256=digest(task),
                       allowed_engines=list(engines))
        prior = self._entry(binding)
        if existing is not None:
            existing = validate_delivery(existing)
            _require(prior is not None and prior['snapshot'] == existing,
                     'production_skill_frozen_input_changed', 409)
        if prior:
            return prior['snapshot'] or self._complete(binding, prior['intent'])
        response = self.catalogue(owner)
        rows = response.get('methods') if type(response) is dict else response
        _require(type(rows) is list, 'production_skill_catalog_invalid', 502)
        methods, ids = [], set()
        for row in rows:
            _require(type(row) is dict, 'production_skill_catalog_invalid', 502)
            if row.get('schema') == 'video.animation-method.v1' or row.get('status') == 'candidate':
                continue
            _require(row.get('schema') == 'video.animation-method.v2' and row.get('status') == 'published',
                     'production_skill_catalog_invalid', 502)
            _identity(row.get('id'))
            _require(row['id'] not in ids and type(row.get('content_sha256')) is str
                     and len(row['content_sha256']) == 64
                     and all(c in '0123456789abcdef' for c in row['content_sha256']),
                     'production_skill_catalog_invalid', 502)
            ids.add(row['id'])
            methods.append(deepcopy(row))
        # A concurrent reservation wins even if its directory was empty. No
        # catalogue refresh or later publication can alter this operation.
        reserved = self._entry(binding, intent=dict(methods=methods))
        return reserved['snapshot'] or self._complete(binding, reserved['intent'])

    def get_bundle(self, owner, consumer_id, sha):
        """Return the exact saved delivery bound to the lightweight catalog hash."""
        _identity(owner, 200)
        _identity(consumer_id)
        with closing(self._connect()) as db:
            row = db.execute('SELECT * FROM ' + _TABLE + ' WHERE owner=? AND consumer_id=?',
                             (owner, consumer_id)).fetchone()
            _require(row is not None, 'production_skill_bundle_not_found', 404)
            current = self._decoded(row)
        snapshot = current['snapshot']
        _require(snapshot is not None and snapshot['catalog']['sha256'] == sha,
                 'production_skill_bundle_not_found', 404)
        return deepcopy(snapshot)
