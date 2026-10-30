"""Unwired controller-only Pi artifact replay; no execution/publication authority."""
from __future__ import annotations
import hashlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from .execution import Attempt, ControlError, Layout, RuntimeConfig
from .staging import EXT, Stager, atomic_json, copy_file, make_readonly, open_regular, read_json

MAX_FILES = 8192
MAX_FILE_BYTES = 64 * 1024**2
MAX_ASSET_BYTES = 25 * 1024**2
MAX_ASSETS = 10
MAX_ASSET_TOTAL = 100 * 1024**2
MAX_MANIFEST_BYTES = 4 * 1024**2
LINEAGE = {'plan_options': ('plan_initial',), 'prepare': ('plan_options',),
           'generate': ('prepare',), 'revise': ('generate', 'revise')}
FIELDS = {'schema_version', 'engine', 'profile', 'checkpoint_format', 'runtime_revision',
          'skill_bundle_revision', 'project_id', 'input_snapshot_sha256', 'source_job_id',
          'source_attempt', 'source_token', 'phase', 'project_root', 'control_context',
          'project', 'version_id'}
RESERVED = {'con', 'prn', 'aux', 'nul', 'clock$', 'conin$', 'conout$'} | {
    prefix + str(i) for prefix in ('com', 'lpt') for i in range(10)}
STATE_NAMES = {'home', '.pi', '.hermes', '.ssh', '.env', 'token', 'tokens',
               'session', 'sessions', 'auth.json', 'credentials.json'}


def require(condition, code):
    if not condition:
        raise ControlError(code)


def digest(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value), 'invalid_sha256')
    return value


def identifier(value):
    require(type(value) is str, 'invalid_uuid')
    try:
        require(str(uuid.UUID(value)) == value, 'invalid_uuid')
    except ValueError:
        raise ControlError('invalid_uuid') from None
    return value


def positive(value):
    require(type(value) is int and 0 < value < 2**63, 'invalid_positive_integer')


def safe_path(value, *, artifact=False):
    require(type(value) is str and 0 < len(value) <= 1024, 'unsafe_path')
    require(not any(ord(c) < 32 or ord(c) == 127 or c in '\\:%?#<>|\"*' for c in value), 'unsafe_path')
    parts = value.split('/')
    require(len(parts) <= 32, 'path_depth_limit')
    for part in parts:
        require(part not in ('', '.', '..') and len(part) <= 255 and part == part.rstrip(' .'), 'unsafe_path')
        require(part.split('.')[0].casefold() not in RESERVED and '~' not in part, 'windows_path_alias')
        if artifact:
            name = part.casefold()
            require(name not in STATE_NAMES and not name.endswith(('.db', '.sqlite', '.sqlite3', '.jsonl')),
                    'native_state_forbidden')
    return value


def checked_directory(path):
    """Check every ancestor, including configured root and Windows reparse points."""
    path = Path(path).absolute()
    for component in reversed((path, *path.parents)):
        info = component.lstat()
        require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                and not getattr(info, 'st_file_attributes', 0) & 0x400, 'unsafe_directory')
    return path


def mkdir_chain(path):
    path = Path(path).absolute()
    if path.parent != path:
        checked_directory(path.parent) if path.parent.exists() else mkdir_chain(path.parent)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    return checked_directory(path)


def json_clone(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
        require(len(raw.encode()) <= MAX_MANIFEST_BYTES, 'manifest_limit')
        return json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        raise ControlError('invalid_json') from None


def exact_json(left, right):
    return json.dumps(left, sort_keys=True, ensure_ascii=False, allow_nan=False) == json.dumps(
        right, sort_keys=True, ensure_ascii=False, allow_nan=False)


@dataclass(frozen=True)
class ProjectFile:
    path: str
    bytes: int
    sha256: str


@dataclass(frozen=True)
class PiCheckpoint:
    """Validated read descriptor, not a publication or acceptance certificate."""
    manifest: dict
    files: tuple[ProjectFile, ...]

    @classmethod
    def validate(cls, value, a, runtime_revision, skill_bundle_revision, max_bytes):
        m = json_clone(value)
        require(type(m) is dict and set(m) == FIELDS, 'checkpoint_schema_mismatch')
        require(type(m['schema_version']) is int and m['schema_version'] == 1
                and m['engine'] == 'pi' and m['profile'] == 'ppt-master'
                and m['checkpoint_format'] == 'artifact-replay-v1', 'incompatible_checkpoint')
        require(m['runtime_revision'] == runtime_revision and m['skill_bundle_revision'] == skill_bundle_revision,
                'checkpoint_revision_mismatch')
        identifier(m['project_id']); identifier(m['source_job_id'])
        positive(m['source_attempt']); positive(m['source_token'])
        require(m['project_id'] == a.project_id and digest(m['input_snapshot_sha256']) == a.snapshot['hash'],
                'checkpoint_identity_mismatch')
        require(m['phase'] in LINEAGE.get(a.phase, ()), 'checkpoint_phase_mismatch')
        if m['phase'] in ('generate', 'revise'):
            identifier(m['version_id'])
        else:
            require(m['version_id'] is None, 'unexpected_version')
        if a.phase == 'revise':
            identifier(a.base_version_id)
            require(m['version_id'] == a.base_version_id, 'base_version_mismatch')
        else:
            require(a.base_version_id is None, 'unexpected_base_version')
        root = safe_path(m['project_root'], artifact=True)
        index = m['project']
        require(type(index) is dict and set(index) == {'files', 'bytes'}, 'invalid_project_index')
        require(type(index['files']) is list and 0 < len(index['files']) <= MAX_FILES, 'checkpoint_file_limit')
        require(type(index['bytes']) is int and 0 <= index['bytes'] <= max_bytes, 'checkpoint_size_limit')
        rows = []; seen = {}; names = set(); total = 0
        for row in index['files']:
            require(type(row) is dict and set(row) == {'path', 'bytes', 'sha256'}, 'invalid_project_index')
            key = safe_path(row['path'], artifact=True)
            require(key.startswith(root + '/'), 'project_root_mismatch')
            require(type(row['bytes']) is int and 0 <= row['bytes'] <= MAX_FILE_BYTES, 'checkpoint_size_limit')
            digest(row['sha256'])
            parts = key.split('/')
            for n in range(1, len(parts) + 1):
                prefix = '/'.join(parts[:n]); folded = prefix.casefold()
                require(folded not in seen or seen[folded] == prefix, 'duplicate_project_path')
                seen[folded] = prefix
            require(key not in names, 'duplicate_project_path'); names.add(key)
            rows.append(ProjectFile(key, row['bytes'], row['sha256'])); total += row['bytes']
        require(not any('/'.join(r.path.split('/')[:n]) in names
                        for r in rows for n in range(1, len(r.path.split('/')))), 'file_directory_collision')
        require(total == index['bytes'] and total <= max_bytes, 'checkpoint_size_limit')
        require(type(m['control_context']) is dict, 'invalid_control_context')
        return cls(m, tuple(rows))


def verify_confirmation(value, snapshot_hash, stage):
    required = {'channel', 'plan_revision', 'input_snapshot_sha256', 'selection', 'outline', 'design'}
    if stage == 2:
        required.add('selected_direction')
    require(type(value) is dict and set(value) == required, 'confirmation_required')
    require(value['channel'] == 'website' and value['input_snapshot_sha256'] == snapshot_hash,
            'confirmation_lineage_mismatch')
    positive(value['plan_revision'])
    require(type(value['selection']) is dict and type(value['outline']) is dict
            and type(value['design']) is dict, 'invalid_confirmation')
    if stage == 1:
        selection = value['selection']
        require(set(selection) <= {'design_mode', 'template_id'}
                and selection.get('design_mode') == 'free_design'
                and selection.get('template_id') is None, 'invalid_confirmation')
    else:
        selected = value['selected_direction']; direction = value['selection'].get('direction_id')
        options = value['design'].get('directions', value['design'].get('options'))
        require(type(direction) is str and bool(direction) and type(selected) is dict
                and selected.get('id') == direction and type(options) is list, 'selected_direction_mismatch')
        matches = [d for d in options if type(d) is dict and d.get('id') == direction]
        require(len(matches) == 1 and exact_json(matches[0], selected), 'selected_direction_mismatch')
        require(set(value['selection']) <= {'direction_id', 'page_plan', 'production_parameters'}, 'invalid_confirmation')
        for field in ('page_plan', 'production_parameters'):
            if field in value['selection']:
                require(exact_json(value['selection'][field], selected.get(field, value['design'].get(field))),
                        'selected_direction_mismatch')


def verify_context(a, checkpoint):
    context = a.snapshot.get('control_context', {})
    require(type(context) is dict and set(context) <= {'stage1_confirmation', 'stage2_confirmation'}, 'invalid_control_context')
    for stage in (1, 2):
        key = f'stage{stage}_confirmation'
        if key in context:
            verify_confirmation(context[key], a.snapshot['hash'], stage)
    needed = 'stage1_confirmation' if a.phase == 'plan_options' else 'stage2_confirmation'
    if a.phase != 'plan_initial':
        require(needed in context, 'confirmation_required')
    if a.phase in ('plan_options', 'prepare'):
        confirmation = context[needed]
        if a.phase == 'prepare' or 'plan_revision' in a.snapshot:
            require(type(a.snapshot.get('plan_revision')) is int
                    and confirmation['plan_revision'] == a.snapshot['plan_revision'], 'confirmation_lineage_mismatch')
        require(exact_json(confirmation['selection'], a.snapshot.get('confirmation_selection')),
                'confirmation_lineage_mismatch')
    if checkpoint:
        inherited = checkpoint.manifest['control_context']
        require(set(inherited) <= {'stage1_confirmation', 'stage2_confirmation'}, 'invalid_control_context')
        for key, value in inherited.items():
            require(key in context and exact_json(value, context[key]), 'checkpoint_context_mismatch')
        if a.phase in ('generate', 'revise'):
            require(exact_json(inherited, context), 'checkpoint_context_mismatch')


def roster(root, expected_files, expected_dirs):
    checked_directory(root)
    files = set(); dirs = set(); count = 0
    def walk(folder, prefix=''):
        nonlocal count
        with os.scandir(folder) as entries:
            for entry in entries:
                count += 1
                require(count <= MAX_FILES * 33 + 2, 'checkpoint_file_limit')
                key = safe_path(prefix + entry.name)
                # Windows DirEntry.stat may report st_nlink=0 from cached find data.
                info = Path(entry.path).lstat()
                require(not getattr(info, 'st_file_attributes', 0) & 0x400, 'linked_or_special_file')
                if stat.S_ISDIR(info.st_mode):
                    require(key in expected_dirs, 'checkpoint_roster_mismatch')
                    dirs.add(key); walk(Path(entry.path), key + '/')
                else:
                    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'linked_or_special_file')
                    require(key in expected_files, 'checkpoint_roster_mismatch'); files.add(key)
    walk(root)
    require(files == expected_files and dirs == expected_dirs, 'checkpoint_roster_mismatch')


def verify_file(root, key, size, sha, limit):
    checked_directory(root)
    with open_regular(root, key, limit) as stream:
        before = os.fstat(stream.fileno()); h = hashlib.sha256(); total = 0
        while chunk := stream.read(65536):
            total += len(chunk); require(total <= limit, 'source_size_limit'); h.update(chunk)
        after = os.fstat(stream.fileno())
        require(before.st_size == total and before.st_mtime_ns == after.st_mtime_ns, 'source_changed')
    require(total == size and h.hexdigest() == sha, 'source_hash_mismatch')


def storage_usage(root, limit):
    """Conservative no-follow quota scan; controller must serialize admissions."""
    total = 0; count = 0
    pending = [checked_directory(root)]
    while pending:
        folder = checked_directory(pending.pop())
        with os.scandir(folder) as entries:
            for entry in entries:
                count += 1
                require(count <= 200000, 'storage_inventory_limit')
                info = Path(entry.path).lstat()
                linked = stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
                if stat.S_ISDIR(info.st_mode) and not linked:
                    pending.append(Path(entry.path))
                else:
                    # Other Hermes homes/quarantines can contain links. Count
                    # their directory entry bytes, never open/follow their target.
                    total += info.st_size
                    require(total <= limit, 'storage_full')
    return total


class PiStager:
    """Requires controller-exclusive storage and immutable, stopped sources.

    Files retain controller ownership; this is not a worker UID handoff.
    """
    def __init__(self, config: RuntimeConfig, trustedruntime_revision: str, trustedskill_bundle_revision: str):
        self.config = config
        for pin in (trustedruntime_revision, trustedskill_bundle_revision):
            require(type(pin) is str and bool(pin.strip()) and len(pin) <= 200, 'invalid_trusted_pin')
        self.runtime_revision = trustedruntime_revision
        self.skill_bundle_revision = trustedskill_bundle_revision
        self.root = mkdir_chain(config.data_root)
        self._staged = {}

    def layout(self, a: Attempt):
        identifier(a.project_id); identifier(a.job_id); positive(a.attempt); positive(a.token)
        root = self.root / f'projects/{a.project_id}/attempts/{a.job_id}/{a.attempt}-{a.token}'
        return Layout(root, root/'inputs', root/'task', root/'home', root/'project',
                      root/'result', root/'audit', root/'base')

    def _checkpoint(self, a, expected):
        if not a.checkpoint_key:
            require(a.phase == 'plan_initial' and expected is None and a.base_version_id is None,
                    'explicit_checkpoint_required')
            return None, None
        require(a.phase != 'plan_initial' and expected is not None, 'checkpoint_identity_mismatch')
        cp = PiCheckpoint.validate(expected, a, self.runtime_revision, self.skill_bundle_revision,
                                   self.config.max_snapshot_bytes)
        parts = safe_path(a.checkpoint_key).split('/')
        require(len(parts) == 4 and parts[:3] == ['projects', a.project_id, 'checkpoints'], 'checkpoint_owner_mismatch')
        identifier(parts[3]); source = checked_directory(self.root / a.checkpoint_key)
        actual = read_json(source, 'checkpoint.json', MAX_MANIFEST_BYTES)
        require(exact_json(actual, cp.manifest), 'checkpoint_identity_mismatch')
        self._verify_checkpoint(source, cp)
        return source, cp

    def _verify_checkpoint(self, source, cp):
        files = {'checkpoint.json'} | {'project/' + r.path for r in cp.files}
        dirs = {'project'}
        for key in files:
            dirs.update('/'.join(key.split('/')[:n]) for n in range(1, len(key.split('/'))))
        roster(source, files, dirs)
        require(exact_json(read_json(source, 'checkpoint.json', MAX_MANIFEST_BYTES), cp.manifest), 'checkpoint_identity_mismatch')
        for row in cp.files:
            verify_file(source, 'project/' + row.path, row.bytes, row.sha256, MAX_FILE_BYTES)

    def _sources(self, a):
        snapshot = a.snapshot
        require(type(snapshot) is dict and snapshot.get('project_id') == a.project_id, 'snapshot_project_mismatch')
        require(type(snapshot.get('settings')) is dict and type(snapshot.get('title')) is str
                and 1 <= len(snapshot['title']) <= 200, 'invalid_snapshot')
        settings = snapshot['settings']
        limits = {'purpose': 4000, 'audience': 4000, 'language': 50, 'style': 4000, 'requirements': 12000}
        require(set(settings) <= set(limits) | {'page_count'}, 'invalid_settings')
        require(type(settings.get('page_count')) is int and 3 <= settings['page_count'] <= 20, 'invalid_settings')
        for key, limit in limits.items():
            if key in settings:
                require(type(settings[key]) is str and len(settings[key]) <= limit, 'invalid_settings')
        digest(snapshot.get('hash'))
        assets = snapshot.get('assets')
        require(type(assets) is list and len(assets) <= MAX_ASSETS, 'asset_limit')
        core = {k: snapshot[k] for k in ('project_id', 'settings', 'title', 'assets')}
        json_clone(core)
        actual_hash = hashlib.sha256(json.dumps(core, sort_keys=True, separators=(',', ':'),
                                               ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        require(actual_hash == snapshot['hash'], 'snapshot_hash_mismatch')
        rows = []; seen = set(); total = 0
        for asset in assets:
            require(type(asset) is dict and set(asset) == {'id', 'storage_key', 'sha256', 'media_type', 'bytes'}, 'invalid_source_mapping')
            aid = identifier(asset.get('id')); key = f'projects/{a.project_id}/assets/{aid}/original'
            require(aid not in seen and asset.get('storage_key') == key and asset.get('media_type') in EXT,
                    'invalid_source_mapping')
            seen.add(aid); digest(asset.get('sha256'))
            size = asset.get('bytes')
            require(type(size) is int and 0 < size <= MAX_ASSET_BYTES, 'source_size_limit')
            total += size
            require(total <= min(MAX_ASSET_TOTAL, self.config.max_snapshot_bytes), 'source_size_limit')
            checked_directory((self.root/key).parent)
            verify_file(self.root, key, size, asset['sha256'], MAX_ASSET_BYTES)
            rows.append({'id': aid, 'path': aid + EXT[asset['media_type']], 'sha256': asset['sha256'],
                         'bytes': size, 'media_type': asset['media_type']})
        return rows

    def stage(self, a: Attempt, checkpoint_manifest: dict | None = None):
        layout = self.layout(a); checked_directory(self.root)
        require(not os.path.lexists(layout.root), 'attempt_already_staged')
        sources = self._sources(a)
        source, cp = self._checkpoint(a, checkpoint_manifest)
        verify_context(a, cp)
        # Parent must serialize global storage quota accounting at integration.
        source_bytes = sum(r['bytes'] for r in sources)
        project_bytes = cp.manifest['project']['bytes'] if cp else 0
        require(source_bytes + project_bytes <= self.config.max_snapshot_bytes, 'snapshot_size_limit')
        needed = source_bytes + 2 * project_bytes
        needed += MAX_MANIFEST_BYTES * 3  # inventory, parsed result, task payload
        require(storage_usage(self.root, self.config.max_storage_bytes) + needed
                <= self.config.max_storage_bytes, 'storage_full')
        mkdir_chain(layout.root.parent)
        try:
            layout.root.mkdir(mode=0o700)
        except FileExistsError:
            raise ControlError('attempt_already_staged') from None
        try:
            for path in (layout.inputs, layout.task, layout.home, layout.project, layout.result, layout.audit, layout.base):
                path.mkdir(mode=0o700)
            for row in sources:
                key = f"projects/{a.project_id}/assets/{row['id']}/original"
                copied = copy_file(self.root, key, layout.inputs/row['path'], MAX_ASSET_BYTES, row['sha256'])
                require(copied['bytes'] == row['bytes'], 'source_size_mismatch')
            inventory = {'schema_version': 1, 'input_snapshot_sha256': a.snapshot['hash'], 'sources': sources}
            atomic_json(layout.inputs/'inventory.json', inventory)
            if cp:
                for row in cp.files:
                    for destination in (layout.base/'project'/row.path, layout.project/row.path):
                        copied = copy_file(source, 'project/' + row.path, destination, MAX_FILE_BYTES, row.sha256)
                        require(copied['bytes'] == row.bytes, 'source_size_mismatch')
                    (layout.project/row.path).chmod(0o600)
                self._verify_checkpoint(source, cp)
            self._sources(a)
            make_readonly(layout.inputs); make_readonly(layout.base)
            self._staged[layout.root] = (json_clone(a.snapshot), json_clone(inventory),
                                         (a.phase, a.session_id, a.checkpoint_key, a.base_version_id,
                                          a.execution_deadline))
            return layout, inventory
        except BaseException:
            # Retain only this freshly-created directory at a quarantine name.
            # Never recursively delete/chmod potentially attacker-controlled data.
            checked_directory(layout.root.parent)
            os.rename(layout.root, layout.root.with_name('.quarantine-' + layout.root.name + '-' + uuid.uuid4().hex))
            raise

    def finish_inputs(self, a: Attempt, layout: Layout, parsed: dict):
        require(layout == self.layout(a) and layout.root in self._staged, 'unstaged_attempt')
        snapshot, inventory, identity = self._staged[layout.root]
        require(exact_json(snapshot, a.snapshot)
                and identity == (a.phase, a.session_id, a.checkpoint_key, a.base_version_id,
                                 a.execution_deadline), 'unstaged_attempt')
        require(type(parsed) is dict and parsed.get('input_snapshot_sha256') == a.snapshot['hash'], 'parse_snapshot_mismatch')
        parsed = json_clone(parsed)
        roster(layout.inputs, {'inventory.json'} | {r['path'] for r in inventory['sources']}, set())
        roster(layout.task, set(), set())
        require(exact_json(read_json(layout.inputs, 'inventory.json'), inventory), 'input_inventory_changed')
        for row in inventory['sources']:
            verify_file(layout.inputs, row['path'], row['bytes'], row['sha256'], MAX_ASSET_BYTES)
        # Existing payload builder only; its hash guard is not parser authenticity.
        layout.inputs.chmod(0o700)
        try:
            return Stager.finish_inputs(self, a, layout, parsed)
        finally:
            make_readonly(layout.inputs)
