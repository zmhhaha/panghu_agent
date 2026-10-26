"""Controller-only stopped Pi snapshots; no publication/checkpoint authority."""
from __future__ import annotations
import json
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from .execution import StopProof
from .pi_staging import (MAX_FILES, MAX_FILE_BYTES, MAX_MANIFEST_BYTES,
                         checked_directory, digest, identifier, json_clone,
                         mkdir_chain, positive, require, safe_path,
                         storage_usage, verify_file)
from .staging import atomic_json, copy_file, read_json

EXTENSIONS = {'.md', '.txt', '.json', '.yaml', '.yml', '.svg', '.png', '.jpg',
              '.jpeg', '.webp', '.gif', '.pptx', '.pdf', '.csv', '.tsv', '.xml',
              '.html', '.css', '.woff', '.woff2', '.ttf', '.otf'}
PRIVATE = {'runtime', 'native', 'hermes', 'pi', '.git', '.aws', '.azure',
           '.config', '.cache', 'credentials', 'secrets', 'id_rsa', 'id_ed25519'}


def _artifact(key):
    safe_path(key, artifact=True)
    for part in key.split('/'):
        name = part.casefold()
        require(name not in PRIVATE and not name.startswith(
            ('.env', 'credentials.', 'secrets.')), 'native_state_forbidden')
    return key


def _scan(root, max_files, max_file_bytes, max_bytes):
    checked_directory(root)
    files = {}; dirs = set(); seen = set(); total = 0; entries = 0
    def walk(folder, prefix=''):
        nonlocal total, entries
        with os.scandir(folder) as listing:
            for entry in listing:
                entries += 1
                require(entries <= max_files * 33 + 2, 'snapshot_file_limit')
                key = _artifact(prefix + entry.name)
                require(key.casefold() not in seen, 'duplicate_project_path')
                seen.add(key.casefold())
                info = Path(entry.path).lstat()
                require(not getattr(info, 'st_file_attributes', 0) & 0x400,
                        'linked_or_special_file')
                if stat.S_ISDIR(info.st_mode):
                    dirs.add(key); walk(Path(entry.path), key + '/')
                else:
                    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                            'linked_or_special_file')
                    require(Path(key).suffix.casefold() in EXTENSIONS, 'artifact_type_forbidden')
                    require(len(files) < max_files and info.st_size <= max_file_bytes, 'snapshot_file_limit')
                    total += info.st_size
                    require(total <= max_bytes, 'snapshot_limit')
                    files[key] = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    walk(root)
    return files, dirs, total


def _seal(root):
    for folder, dirs, files in os.walk(root):
        for name in files:
            (Path(folder) / name).chmod(0o400)
        for name in dirs:
            (Path(folder) / name).chmod(0o500)
    root.chmod(0o500)


@dataclass(frozen=True, init=False)
class FrozenCandidate:
    path: Path
    _manifest_json: bytes

    def __init__(self, path, indexes, manifest):
        value = json_clone(manifest)
        require(value.get('indexes') == indexes, 'candidate_index_mismatch')
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                             allow_nan=False).encode('utf-8')
        require(len(encoded) <= MAX_MANIFEST_BYTES, 'manifest_limit')
        object.__setattr__(self, 'path', Path(path))
        object.__setattr__(self, '_manifest_json', encoded)

    @property
    def manifest(self):
        return json.loads(self._manifest_json)

    @property
    def indexes(self):
        return self.manifest['indexes']

    def validation_manifest(self):
        """Measured rows plus untrusted worker labels; never acceptance evidence."""
        raw = read_json(self.path / 'result', 'manifest.json', MAX_MANIFEST_BYTES)
        require(type(raw) is dict, 'invalid_candidate_manifest')
        require(raw.get('phase') == self.manifest['phase'] and
                raw.get('input_snapshot_sha256') == self.manifest['input_snapshot_sha256'],
                'candidate_identity_mismatch')
        root = _artifact(raw.get('project_root'))
        declarations = raw.get('artifacts')
        require(type(declarations) is list, 'invalid_candidate_manifest')
        roles = {}; rows = []
        for row in declarations:
            require(type(row) is dict, 'invalid_candidate_manifest')
            key = _artifact(row.get('path'))
            require(key not in roles, 'duplicate_project_path')
            roles[key] = row.get('role')
        for row in self.indexes['project']['files']:
            require(row['path'].startswith(root + '/'), 'project_root_mismatch')
            key = row['path'][len(root) + 1:]
            item = dict(row, path=key)
            if key in roles:
                item['role'] = roles.pop(key)
            rows.append(item)
        require(not roles, 'candidate_roster_mismatch')
        return json_clone(dict(raw, artifacts=rows))


def freeze_candidate(config, attempt, layout, stop_proof, *, candidate_id=None,
                     max_files=MAX_FILES, max_file_bytes=MAX_FILE_BYTES):
    """Requires backend-derived StopProof and exclusive controller storage.

    StopProof is a trusted caller contract, not an authenticated wire certificate.
    """
    require(type(stop_proof) is StopProof and stop_proof.uid == attempt.uid
            and stop_proof.all_writers_stopped is True, 'writers_not_stopped')
    identifier(attempt.project_id); identifier(attempt.job_id)
    positive(attempt.attempt); positive(attempt.token); digest(attempt.snapshot['hash'])
    require(type(max_files) is int and 0 < max_files <= MAX_FILES and
            type(max_file_bytes) is int and 0 < max_file_bytes <= MAX_FILE_BYTES,
            'invalid_snapshot_bounds')
    root = checked_directory(config.data_root)
    expected = root / f'projects/{attempt.project_id}/attempts/{attempt.job_id}/{attempt.attempt}-{attempt.token}'
    require(layout.root == expected and all(getattr(layout, name) == expected / name
            for name in ('inputs', 'task', 'home', 'project', 'result', 'audit', 'base')),
            'attempt_layout_mismatch')
    checked_directory(expected)
    before = {name: _scan(getattr(layout, name), max_files, max_file_bytes,
                          config.max_snapshot_bytes) for name in ('project', 'result')}
    count = sum(len(value[0]) for value in before.values())
    total = sum(value[2] for value in before.values())
    require(count <= max_files and total <= config.max_snapshot_bytes, 'snapshot_limit')
    require(storage_usage(root, config.max_storage_bytes) + total + MAX_MANIFEST_BYTES
            <= config.max_storage_bytes, 'storage_full')
    cid = identifier(candidate_id) if candidate_id is not None else str(uuid.uuid4())
    parent = root / f'projects/{attempt.project_id}/candidates'
    mkdir_chain(parent)
    target = parent / cid
    target.mkdir(mode=0o700)
    try:
        indexes = {}
        for name in ('project', 'result'):
            source = getattr(layout, name); destination = target / name
            destination.mkdir(mode=0o700)
            files, dirs, size = before[name]
            for key in sorted(dirs, key=lambda key: (key.count('/'), key)):
                (destination / key).mkdir(mode=0o700)
            rows = [copy_file(source, key, destination / key, max_file_bytes) for key in sorted(files)]
            require(sum(row['bytes'] for row in rows) == size, 'source_changed')
            indexes[name] = {'files': rows, 'bytes': size}
        for name in ('project', 'result'):
            source = getattr(layout, name)
            require(_scan(source, max_files, max_file_bytes, config.max_snapshot_bytes)
                    == before[name], 'source_changed')
            for row in indexes[name]['files']:
                verify_file(source, row['path'], row['bytes'], row['sha256'], max_file_bytes)
        manifest = json_clone({'schema_version': 1, 'kind': 'pi-candidate-snapshot',
                              'candidate_id': cid, 'project_id': attempt.project_id,
                              'job_id': attempt.job_id, 'attempt': attempt.attempt,
                              'token': attempt.token, 'uid': attempt.uid, 'phase': attempt.phase,
                              'input_snapshot_sha256': attempt.snapshot['hash'], 'indexes': indexes})
        frozen = FrozenCandidate(target, indexes, manifest)
        atomic_json(target / 'snapshot.json', frozen.manifest)
        _seal(target)
        return frozen
    except BaseException:
        quarantine = parent / ('.quarantine-' + cid + '-' + uuid.uuid4().hex)
        os.rename(target, quarantine)
        raise
