"""Immutable controller-prepared values, not wire-selectable execution authority."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from .model_grant import ModelIdentity
from ppt_service.control.execution import Attempt, Layout
from ppt_service.control.pi_staging import (PiCheckpoint, PiStager, MAX_ASSETS, MAX_ASSET_TOTAL, MAX_ASSET_BYTES, MAX_FILE_BYTES,
    MAX_MANIFEST_BYTES, checked_directory, digest, identifier, safe_path, exact_json, require, roster, verify_context, verify_file)
from ppt_service.control.staging import EXT, read_json

MAX_TASK_BYTES = 64 * 1024


def bounded_json(value, limit=MAX_TASK_BYTES):
    def check(item, depth=0):
        if depth > 32:
            raise ValueError('JSON nesting limit')
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ValueError('JSON keys must be strings')
            for child in item.values():
                check(child, depth + 1)
        elif type(item) is list:
            for child in item:
                check(child, depth + 1)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise ValueError('JSON values only')
    check(value)
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    if len(raw.encode('utf-8')) > limit:
        raise ValueError('JSON size limit')
    return raw


@dataclass(frozen=True, slots=True, init=False)
class PreparedPptContext:
    runtime_revision: str
    skill_bundle_revision: str
    _task_json: str
    _checkpoint_json: str
    _receipt_json: str

    def __init__(self):
        raise TypeError('Use PreparedPptContext.from_stager with trusted controller objects')

    @classmethod
    def from_stager(cls, stager: PiStager, attempt: Attempt, layout: Layout, *, checkpoint_manifest=None):
        require(type(stager) is PiStager and type(attempt) is Attempt and type(layout) is Layout,
                'invalid_prepared_context')
        require(layout == stager.layout(attempt) and layout.root in stager._staged, 'unstaged_attempt')
        snapshot, inventory, identity = stager._staged[layout.root]
        require(exact_json(snapshot, attempt.snapshot) and identity == (
            attempt.phase, attempt.session_id, attempt.checkpoint_key, attempt.base_version_id,
            attempt.execution_deadline), 'unstaged_attempt')
        sources = stager._sources(attempt)
        require(exact_json(inventory, {'schema_version': 1, 'input_snapshot_sha256': snapshot['hash'],
                                     'sources': sources}), 'input_inventory_changed')
        _, checkpoint = stager._checkpoint(attempt, checkpoint_manifest)
        verify_context(attempt, checkpoint)
        for path in (layout.root, layout.inputs, layout.task, layout.base, layout.project, layout.result):
            checked_directory(path)
        roster(layout.inputs, {'inventory.json', 'parsed.json'} | {r['path'] for r in sources}, set())
        roster(layout.task, {'task.json'}, set())
        require(exact_json(read_json(layout.inputs, 'inventory.json'), inventory), 'input_inventory_changed')
        for row in sources:
            verify_file(layout.inputs, row['path'], row['bytes'], row['sha256'], MAX_ASSET_BYTES)
        parsed = read_json(layout.inputs, 'parsed.json', MAX_MANIFEST_BYTES)
        parsed_json = bounded_json(parsed, MAX_MANIFEST_BYTES)
        require(type(parsed) is dict and parsed.get('input_snapshot_sha256') == snapshot['hash'], 'parse_snapshot_mismatch')
        settings = snapshot['settings'] | {'title': snapshot['title'],
            'source_inventory': '/work/inputs/inventory.json', 'parsed_materials': '/work/inputs/parsed.json',
            'revision_pages': snapshot.get('pages', [])}
        expected = {'schema_version': 1, 'job_id': attempt.job_id, 'attempt': attempt.attempt,
                    'phase': attempt.phase, 'session_id': attempt.session_id,
                    'input_snapshot_sha256': snapshot['hash'],
                    'requirements': settings | {'pages': settings.get('page_count', 8), 'external_model_acknowledged': True},
                    'assets': [row | {'path': '/work/inputs/' + row['path'],
                        'display_name': row['id'] + EXT[row['media_type']]} for row in sources],
                    'feedback': snapshot.get('feedback', ''), 'base_version_id': attempt.base_version_id}
        if attempt.execution_deadline is not None:
            expected['execution_deadline'] = attempt.execution_deadline.astimezone(timezone.utc).isoformat()
        for key in ('stage1_confirmation', 'stage2_confirmation'):
            if snapshot.get('control_context', {}).get(key):
                expected[key] = snapshot['control_context'][key]
        task = read_json(layout.task, 'task.json', MAX_TASK_BYTES)
        task_json = bounded_json(task)
        require(task_json == bounded_json(expected), 'prepared_task_mismatch')
        if checkpoint:
            files = {r.path for r in checkpoint.files}
            dirs = {'/'.join(key.split('/')[:n]) for key in files for n in range(1, len(key.split('/')))}
            roster(layout.project, files, dirs)
            roster(layout.base, {'project/' + key for key in files}, {'project'} | {'project/' + key for key in dirs})
            for row in checkpoint.files:
                verify_file(layout.project, row.path, row.bytes, row.sha256, MAX_FILE_BYTES)
                verify_file(layout.base, 'project/' + row.path, row.bytes, row.sha256, MAX_FILE_BYTES)
        else:
            roster(layout.project, set(), set())
            roster(layout.base, set(), set())
        checkpoint_json = bounded_json(checkpoint.manifest if checkpoint else None, MAX_MANIFEST_BYTES)
        receipt = {'schema_version': 1, 'project_id': attempt.project_id, 'job_id': attempt.job_id,
                   'attempt': attempt.attempt, 'token': attempt.token, 'phase': attempt.phase,
                   'session_id': attempt.session_id, 'input_snapshot_sha256': snapshot['hash'],
                   'runtime_revision': stager.runtime_revision, 'skill_bundle_revision': stager.skill_bundle_revision,
                   'checkpoint_key': attempt.checkpoint_key, 'base_version_id': attempt.base_version_id,
                   'stage_root': str(layout.root), 'task_sha256': hashlib.sha256(task_json.encode()).hexdigest(),
                   'inventory_sha256': hashlib.sha256(bounded_json(inventory).encode()).hexdigest(),
                   'parsed_sha256': hashlib.sha256(parsed_json.encode()).hexdigest(),
                   'checkpoint_sha256': hashlib.sha256(checkpoint_json.encode()).hexdigest(),
                   'paths': {'inputs': '/work/inputs', 'task': '/work/task/task.json', 'base': '/work/base',
                             'project': '/work/project', 'result': '/work/result'}}
        value = object.__new__(cls)
        for key, content in {'runtime_revision': stager.runtime_revision,
                             'skill_bundle_revision': stager.skill_bundle_revision, '_task_json': task_json,
                             '_checkpoint_json': checkpoint_json, '_receipt_json': bounded_json(receipt)}.items():
            object.__setattr__(value, key, content)
        return value

    def export_for_mounts(self):
        """Detach controller evidence for a trusted root-owned transport, never Job wire."""
        receipt = self.receipt
        receipt['stage_root'] = '/work'
        bundle = {'schema_version': 1, 'receipt': receipt, 'checkpoint': self.checkpoint}
        return json.loads(bounded_json(bundle, MAX_MANIFEST_BYTES))

    @classmethod
    def from_mounts(cls, bundle, identity: ModelIdentity, *, root=Path('/work'),
                    runtime_revision: str, skill_bundle_revision: str):
        """Measure mounted files against controller evidence and host-supplied identity.

        ``root`` is receiver configuration (customizable for filesystem fixtures),
        never a path selected by the transported receipt. The caller must supply
        the bundle and identity through its trusted root-owned launch boundary.
        """
        require(type(identity) is ModelIdentity, 'invalid_prepared_identity')
        for pin in (runtime_revision, skill_bundle_revision):
            require(type(pin) is str and bool(pin.strip()) and len(pin) <= 200, 'invalid_trusted_pin')
        bundle = json.loads(bounded_json(bundle, MAX_MANIFEST_BYTES))
        require(type(bundle) is dict and set(bundle) == {'schema_version', 'receipt', 'checkpoint'}
                and type(bundle['schema_version']) is int and bundle['schema_version'] == 1,
                'mounted_context_schema_mismatch')
        receipt = bundle['receipt']
        fields = {'schema_version', 'project_id', 'job_id', 'attempt', 'token', 'phase', 'session_id',
                  'input_snapshot_sha256', 'runtime_revision', 'skill_bundle_revision', 'checkpoint_key',
                  'base_version_id', 'stage_root', 'task_sha256', 'inventory_sha256', 'parsed_sha256',
                  'checkpoint_sha256', 'paths'}
        require(type(receipt) is dict and set(receipt) == fields
                and type(receipt['schema_version']) is int and receipt['schema_version'] == 1,
                'mounted_receipt_schema_mismatch')
        for key in ('project_id', 'job_id', 'attempt', 'token', 'phase'):
            expected = getattr(identity, key)
            require(type(receipt[key]) is type(expected) and receipt[key] == expected,
                    'mounted_identity_mismatch')
        require(receipt['runtime_revision'] == runtime_revision
                and receipt['skill_bundle_revision'] == skill_bundle_revision, 'mounted_revision_mismatch')
        require(type(receipt['session_id']) is str and 0 < len(receipt['session_id']) <= 128
                and all(c.isascii() and (c.isalnum() or c in '_-') for c in receipt['session_id']),
                'invalid_session_id')
        paths = {'inputs': '/work/inputs', 'task': '/work/task/task.json', 'base': '/work/base',
                 'project': '/work/project', 'result': '/work/result'}
        require(receipt['stage_root'] == '/work' and exact_json(receipt['paths'], paths), 'mounted_path_mismatch')
        for key in ('input_snapshot_sha256', 'task_sha256', 'inventory_sha256', 'parsed_sha256', 'checkpoint_sha256'):
            digest(receipt[key])
        checkpoint_json = bounded_json(bundle['checkpoint'], MAX_MANIFEST_BYTES)
        require(hashlib.sha256(checkpoint_json.encode()).hexdigest() == receipt['checkpoint_sha256'],
                'checkpoint_identity_mismatch')
        if receipt['phase'] == 'plan_initial':
            require(receipt['checkpoint_key'] is None and receipt['base_version_id'] is None
                    and bundle['checkpoint'] is None, 'unexpected_checkpoint')
        else:
            key = safe_path(receipt['checkpoint_key']).split('/')
            require(len(key) == 4 and key[:3] == ['projects', identity.project_id, 'checkpoints'],
                    'checkpoint_owner_mismatch')
            identifier(key[3])
            require(bundle['checkpoint'] is not None, 'explicit_checkpoint_required')
            if receipt['phase'] == 'revise':
                identifier(receipt['base_version_id'])
            else:
                require(receipt['base_version_id'] is None, 'unexpected_base_version')

        root = checked_directory(root)
        inputs, task_root, project, base = (root / name for name in ('inputs', 'task', 'project', 'base'))
        for directory in (inputs, task_root, project, base, root / 'result'):
            checked_directory(directory)
        # Optional root-owned launch metadata has controlled names and must still
        # be an ordinary unlinked file. It carries no model task authority here.
        optional = {name for name in ('mounted-context.json', 'domain-launch.json')
                    if (task_root / name).exists() or (task_root / name).is_symlink()}
        roster(task_root, {'task.json'} | optional, set())
        task = read_json(task_root, 'task.json', MAX_TASK_BYTES)
        inventory = read_json(inputs, 'inventory.json', MAX_MANIFEST_BYTES)
        parsed = read_json(inputs, 'parsed.json', MAX_MANIFEST_BYTES)
        task_json = bounded_json(task)
        for key, raw in (('task_sha256', task_json),
                         ('inventory_sha256', bounded_json(inventory, MAX_MANIFEST_BYTES)),
                         ('parsed_sha256', bounded_json(parsed, MAX_MANIFEST_BYTES))):
            require(hashlib.sha256(raw.encode()).hexdigest() == receipt[key], 'mounted_content_mismatch')
        task_fields = {'schema_version', 'job_id', 'attempt', 'phase', 'session_id', 'input_snapshot_sha256',
                       'requirements', 'assets', 'feedback', 'base_version_id'}
        confirmations = {'stage1_confirmation', 'stage2_confirmation'}
        require(type(task) is dict and task_fields <= set(task) <= task_fields | confirmations | {'execution_deadline'}
                and type(task['schema_version']) is int and task['schema_version'] == 1, 'prepared_task_mismatch')
        deadline = None
        if 'execution_deadline' in task:
            require(type(task['execution_deadline']) is str, 'invalid_execution_deadline')
            try:
                deadline = datetime.fromisoformat(task['execution_deadline'])
            except ValueError:
                require(False, 'invalid_execution_deadline')
            require(deadline.utcoffset() is not None
                    and deadline.astimezone(timezone.utc).isoformat() == task['execution_deadline'],
                    'invalid_execution_deadline')
        for key in ('job_id', 'attempt', 'phase', 'session_id', 'input_snapshot_sha256', 'base_version_id'):
            require(type(task[key]) is type(receipt[key]) and task[key] == receipt[key], 'prepared_task_mismatch')
        requirements = task['requirements']
        require(type(requirements) is dict
                and requirements.get('source_inventory') == '/work/inputs/inventory.json'
                and requirements.get('parsed_materials') == '/work/inputs/parsed.json', 'mounted_path_mismatch')
        require(type(inventory) is dict and set(inventory) == {'schema_version', 'input_snapshot_sha256', 'sources'}
                and type(inventory['schema_version']) is int and inventory['schema_version'] == 1
                and inventory['input_snapshot_sha256'] == receipt['input_snapshot_sha256'], 'input_inventory_changed')
        require(type(parsed) is dict and parsed.get('input_snapshot_sha256') == receipt['input_snapshot_sha256'],
                'parse_snapshot_mismatch')
        sources = inventory['sources']
        require(type(sources) is list and len(sources) <= MAX_ASSETS, 'asset_limit')
        seen = set(); total = 0
        for row in sources:
            require(type(row) is dict and set(row) == {'id', 'path', 'sha256', 'bytes', 'media_type'},
                    'invalid_source_mapping')
            aid = identifier(row['id'])
            require(type(row['media_type']) is str and row['media_type'] in EXT and aid not in seen,
                    'invalid_source_mapping')
            seen.add(aid)
            require(row['path'] == aid + EXT[row['media_type']], 'invalid_source_mapping')
            digest(row['sha256'])
            require(type(row['bytes']) is int and 0 < row['bytes'] <= MAX_ASSET_BYTES, 'source_size_limit')
            total += row['bytes']
        require(total <= MAX_ASSET_TOTAL, 'source_size_limit')
        assets = [row | {'path': '/work/inputs/' + row['path'],
                        'display_name': row['id'] + EXT[row['media_type']]} for row in sources]
        require(exact_json(task['assets'], assets), 'prepared_assets_mismatch')
        roster(inputs, {'inventory.json', 'parsed.json'} | {row['path'] for row in sources}, set())
        for row in sources:
            verify_file(inputs, row['path'], row['bytes'], row['sha256'], MAX_ASSET_BYTES)

        # The controller already bound the complete snapshot to the task digest.
        # Reconstruct only the measured fields needed by checkpoint/context
        # validation; never restore host asset storage keys or source roots.
        context = {key: task[key] for key in confirmations if key in task}
        snapshot = {'hash': receipt['input_snapshot_sha256'], 'control_context': context}
        if receipt['phase'] in ('plan_options', 'prepare'):
            confirmation = context.get('stage1_confirmation' if receipt['phase'] == 'plan_options' else 'stage2_confirmation')
            require(type(confirmation) is dict, 'confirmation_required')
            snapshot.update(plan_revision=confirmation.get('plan_revision'),
                            confirmation_selection=confirmation.get('selection'))
        attempt = Attempt(identity.job_id, identity.project_id, identity.attempt, identity.token,
                          identity.phase, snapshot, receipt['session_id'], receipt['checkpoint_key'], receipt['base_version_id'],
                          execution_deadline=deadline)
        checkpoint = (PiCheckpoint.validate(bundle['checkpoint'], attempt, runtime_revision,
                                           skill_bundle_revision, 512 * 1024**2)
                      if bundle['checkpoint'] is not None else None)
        if identity.phase == 'plan_initial':
            require(not context, 'unexpected_confirmation')
        verify_context(attempt, checkpoint)
        if checkpoint:
            files = {row.path for row in checkpoint.files}
            dirs = {'/'.join(key.split('/')[:n]) for key in files for n in range(1, len(key.split('/')))}
            roster(project, files, dirs)
            roster(base, {'project/' + key for key in files}, {'project'} | {'project/' + key for key in dirs})
            for row in checkpoint.files:
                verify_file(project, row.path, row.bytes, row.sha256, MAX_FILE_BYTES)
                verify_file(base, 'project/' + row.path, row.bytes, row.sha256, MAX_FILE_BYTES)
        else:
            roster(project, set(), set())
            roster(base, set(), set())
        value = object.__new__(cls)
        for key, content in {'runtime_revision': runtime_revision, 'skill_bundle_revision': skill_bundle_revision,
                             '_task_json': task_json, '_checkpoint_json': checkpoint_json,
                             '_receipt_json': bounded_json(receipt)}.items():
            object.__setattr__(value, key, content)
        return value

    @property
    def task(self):
        return json.loads(self._task_json)

    @property
    def checkpoint(self):
        return json.loads(self._checkpoint_json)

    @property
    def receipt(self):
        return json.loads(self._receipt_json)

    def matches(self, task, checkpoint):
        return (bounded_json(task) == self._task_json and
                bounded_json(checkpoint, MAX_MANIFEST_BYTES) == self._checkpoint_json)
