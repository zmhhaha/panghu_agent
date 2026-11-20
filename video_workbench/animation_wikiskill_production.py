"""One immutable published WikiSkill release per creative batch.

The batch begins with joint planning. Its exact packages survive active-pointer
changes, browsing retention and process restarts; child tasks only bind those
bytes to their own input hashes. Nothing synthesizes method/human approvals.
"""
from contextlib import closing
from copy import deepcopy
import json

from agent_service.animation_skills_contract import ENGINES, freeze_library_skill_catalog
from agent_service.contracts import digest
from .animation_skill_compatibility import freeze_compatibility, requirements_compatible
from .animation_skill_production import (
    SkillProductionAdapter, SCHEMA, EVIDENCE_SCOPE, MAX_DELIVERY_BYTES, MAX_TASK_BYTES,
    _identity, _require, _canonical, validate_delivery,
)


TABLE = 'animation_wikiskill_batch_freezes'
BINDING_FIELDS = frozenset({'batch_id', 'release_id', 'release_sha256', 'publisher_owner', 'release_sequence'})


def validate_library_binding(binding):
    """Validate current policy bindings without rewriting historical identities."""
    _require(type(binding) is dict and set(binding) in (BINDING_FIELDS, BINDING_FIELDS | {'use_wikiskill'}),
             'production_skill_batch_required', 409)
    _identity(binding['batch_id'])
    enabled = binding.get('use_wikiskill', True)
    _require(type(enabled) is bool, 'production_skill_batch_required', 409)
    if binding['release_id'] is None:
        _require(all(binding[key] is None for key in ('release_sha256', 'publisher_owner', 'release_sequence')),
                 'production_skill_frozen_input_changed', 409)
    else:
        _require(enabled, 'production_skill_frozen_input_changed', 409)
        _identity(binding['release_id'], 200)
        _identity(binding['publisher_owner'], 200)
        _require(type(binding['release_sha256']) is str and len(binding['release_sha256']) == 64
            and all(c in '0123456789abcdef' for c in binding['release_sha256'])
            and type(binding['release_sequence']) is int and binding['release_sequence'] > 0,
            'production_skill_batch_required', 409)
    return deepcopy(binding)


class LibrarySkillProductionAdapter(SkillProductionAdapter):
    def __init__(self, active, release, catalogue, ledger_path, *, import_release=None, legacy_read=None):
        self.active, self.release, self.library_catalogue = active, release, catalogue
        self.import_release = import_release
        super().__init__(None, None, ledger_path)
        # Only pre-existing durable intents may read their exact old packages.
        # New freezes never query the retired per-method catalogue.
        self.read = legacy_read
        with closing(self._connect()) as db:
            db.execute('CREATE TABLE IF NOT EXISTS ' + TABLE + ' ('
                'owner TEXT NOT NULL,batch_id TEXT NOT NULL,requested_release_id TEXT,'
                'record BLOB NOT NULL,sha256 TEXT NOT NULL,PRIMARY KEY(owner,batch_id))')

    def list_releases(self, owner):
        return self.library_catalogue(owner)

    @staticmethod
    def _record(row):
        record = json.loads(row['record'])
        _require(bytes(row['record']) == _canonical(record) and digest(record) == row['sha256'],
                 'production_skill_ledger_changed', 409)
        return record

    def pin(self, owner, batch_id, release_id=None, *, use_wikiskill=True):
        _identity(owner, 200)
        _identity(batch_id)
        _require(type(use_wikiskill) is bool, 'production_skill_loading_policy_invalid')
        _require(use_wikiskill or release_id is None, 'production_skill_loading_policy_conflict')
        if release_id is not None:
            _identity(release_id)
        with closing(self._connect()) as db:
            row = db.execute('SELECT * FROM ' + TABLE + ' WHERE owner=? AND batch_id=?',
                             (owner, batch_id)).fetchone()
            if row is not None:
                saved = self._record(row)
                _require(row['requested_release_id'] == release_id
                    and saved['binding'].get('use_wikiskill', True) is use_wikiskill,
                         'production_skill_frozen_input_changed', 409)
                return deepcopy(saved['binding'])
        release = ((self.release(owner, release_id) if release_id is not None else self.active(owner))
                   if use_wikiskill else None)
        # Reuse the shared contract to validate package bytes and release hash.
        freeze_library_skill_catalog(release, recipient_owner=owner, consumer_id=batch_id,
            task_sha256='0' * 64, allowed_engines=['html-svg', 'canvas', 'three'])
        binding = dict(batch_id=batch_id, use_wikiskill=use_wikiskill,
                       release_id=release['id'] if release else None,
                       release_sha256=release['sha256'] if release else None,
                       publisher_owner=release.get('owner') if release else None,
                       release_sequence=release.get('sequence') if release else None)
        record = dict(binding=binding, release=release)
        _require(len(_canonical(record)) <= MAX_DELIVERY_BYTES, 'production_skill_delivery_too_large', 413)
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO ' + TABLE + ' VALUES (?,?,?,?,?)',
                (owner, batch_id, release_id, _canonical(record), digest(record)))
            row = db.execute('SELECT * FROM ' + TABLE + ' WHERE owner=? AND batch_id=?',
                             (owner, batch_id)).fetchone()
            saved = self._record(row)
            _require(row['requested_release_id'] == release_id
                and saved['binding'].get('use_wikiskill', True) is use_wikiskill,
                'production_skill_frozen_input_changed', 409)
            db.commit()
        return deepcopy(saved['binding'])

    def _batch(self, owner, binding):
        validate_library_binding(binding)
        with closing(self._connect()) as db:
            row = db.execute('SELECT * FROM ' + TABLE + ' WHERE owner=? AND batch_id=?',
                             (owner, binding['batch_id'])).fetchone()
            _require(row is not None, 'production_skill_batch_not_found', 409)
            record = self._record(row)
        _require(record['binding'] == binding, 'production_skill_frozen_input_changed', 409)
        return record

    def pin_import(self, owner, binding):
        """Recover an authenticated internal batch by exact release, never active."""
        _identity(owner, 200)
        validate_library_binding(binding)
        with closing(self._connect()) as db:
            row = db.execute('SELECT * FROM ' + TABLE + ' WHERE owner=? AND batch_id=?',
                             (owner, binding['batch_id'])).fetchone()
        if row is not None:
            _require(self._record(row)['binding'] == binding, 'production_skill_frozen_input_changed', 409)
            return deepcopy(binding)
        if binding['release_id'] is None:
            _require(binding['release_sha256'] is None and binding['publisher_owner'] is None and binding['release_sequence'] is None,
                     'production_skill_frozen_input_changed', 409)
            release = None
        else:
            _identity(binding['publisher_owner'], 200)
            release = (self.import_release(owner, binding) if self.import_release else
                       self.release(owner, binding['release_id']))
            _require(type(release) is dict and release.get('id') == binding['release_id']
                and release.get('sha256') == binding['release_sha256']
                and release.get('owner') == binding['publisher_owner']
                and release.get('sequence') == binding['release_sequence'], 'production_skill_frozen_input_changed', 409)
        freeze_library_skill_catalog(release, recipient_owner=owner, consumer_id=binding['batch_id'],
            task_sha256='0' * 64, allowed_engines=['html-svg', 'canvas', 'three'])
        record = dict(binding=deepcopy(binding), release=release)
        _require(len(_canonical(record)) <= MAX_DELIVERY_BYTES, 'production_skill_delivery_too_large', 413)
        with closing(self._connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO ' + TABLE + ' VALUES (?,?,?,?,?)',
                (owner, binding['batch_id'], binding['release_id'], _canonical(record), digest(record)))
            row = db.execute('SELECT * FROM ' + TABLE + ' WHERE owner=? AND batch_id=?',
                             (owner, binding['batch_id'])).fetchone()
            _require(self._record(row)['binding'] == binding, 'production_skill_frozen_input_changed', 409)
            db.commit()
        return deepcopy(binding)

    def _complete(self, binding, intent):
        if 'library_binding' not in intent:
            # Earlier immutable task deliveries retain their original meaning.
            return super()._complete(binding, intent)
        release = intent['release']
        packages = [] if release is None else [row for row in release['packages']
            if intent.get('discovery_only') or requirements_compatible(row['runtime_requirements'], intent['compatibility'])]
        catalog = freeze_library_skill_catalog(release, **binding, packages=packages)
        wanted = {row['package_sha256'] for row in catalog['skills']}
        receipt = dict(source='authenticated_wikiskill_release',
            library_binding=deepcopy(intent['library_binding']), scope='host_retrieval_not_agent_observation')
        result = dict(schema=SCHEMA, **binding, catalog=catalog,
            packages={row['sha256']: row for row in packages if row['sha256'] in wanted},
            retrieval_receipts=[receipt], evidence_scope=EVIDENCE_SCOPE)
        result['sha256'] = digest(result)
        return self._entry(binding, intent=intent, snapshot=validate_delivery(result))['snapshot']

    def freeze(self, owner, consumer_id, task, engines, existing=None, *, resource_authoring=None,
               library_binding=None, discovery_only=False, drawing_libraries=None):
        _identity(owner, 200)
        _identity(consumer_id)
        _require(type(task) is dict and len(_canonical(task)) <= MAX_TASK_BYTES and type(discovery_only) is bool)
        _require(type(engines) is list and bool(engines)
            and all(type(value) is str and value in ENGINES for value in engines) and len(set(engines)) == len(engines))
        binding = dict(recipient_owner=owner, consumer_id=consumer_id,
                       task_sha256=digest(task), allowed_engines=list(engines))
        prior = self._entry(binding)
        if existing is not None:
            _require(prior is not None and prior['snapshot'] == validate_delivery(existing),
                     'production_skill_frozen_input_changed', 409)
        if prior:
            _require(prior['intent'].get('discovery_only', False) == discovery_only,
                     'production_skill_frozen_input_changed', 409)
            if library_binding is not None:
                _require(prior['intent'].get('library_binding') == library_binding,
                         'production_skill_frozen_input_changed', 409)
            return prior['snapshot'] or self._complete(binding, prior['intent'])
        if library_binding is None:
            library_binding = self.pin(owner, consumer_id)
        elif type(library_binding) is dict and library_binding.get('use_wikiskill', True) is False:
            # Disabled plans made without a publication adapter can be safely
            # recovered: importing no knowledge never resolves today's active.
            self.pin_import(owner, library_binding)
        batch = self._batch(owner, library_binding)
        intent = dict(library_binding=deepcopy(library_binding), release=batch['release'],
            compatibility=freeze_compatibility(task, resource_authoring=resource_authoring,
                drawing_libraries=drawing_libraries),
            discovery_only=discovery_only)
        reserved = self._entry(binding, intent=intent)
        _require(reserved['intent'].get('library_binding') == library_binding
            and reserved['intent'].get('discovery_only', False) == discovery_only,
            'production_skill_frozen_input_changed', 409)
        return reserved['snapshot'] or self._complete(binding, reserved['intent'])
