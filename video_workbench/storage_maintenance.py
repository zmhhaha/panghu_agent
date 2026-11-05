"""Bounded operator inventory and reviewed cleanup of known executor scratch.

Never instantiate Store/ExecutionJournal here: their constructors write schema.
Business media, database rows, final MP4s and Kubernetes objects are never deleted.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import uuid

SCHEMA = 'video.storage-maintenance.v1'
TERMINAL = {'completed', 'failed', 'cancelled', 'timed_out', 'needs_input', 'partial_failed'}
EXEC_TERMINAL = {'completed', 'failed', 'cancelled', 'timed_out'}
BUSINESS_COLUMNS = {
    'projects': 'id owner document', 'versions': 'id project_id scene_id operation_id source format',
    'operations': 'id owner project_id idem request_hash document', 'metadata': 'key value',
    'audio_versions': 'id project_id scene_id operation_id document data',
    'audio_operations': 'id owner project_id scene_id idem request_hash document',
    'video_renders': 'id owner project_id idem request_hash document snapshot data',
    'motion_operations': 'id owner project_id scene_id idem request_hash document',
    'motion_versions': 'id owner project_id scene_id operation_id document',
    'motion_heads': 'owner project_id scene_id version_id mode',
    'motion_alignments': 'owner project_id scene_id motion_version_id audio_version_id document',
    'explanation_operations': 'id owner project_id idem request_hash document',
    'explanation_versions': 'id owner project_id operation_id document',
    'explanation_heads': 'owner project_id version_id applied_binding_sha256',
    'video_batches': 'id owner project_id idem request_hash document',
}
EXEC_COLUMNS = {
    'motion_operations': 'key owner operation_id snapshot_sha256 snapshot state cancel_requested result error updated created',
    'motion_scenes': 'key binding name state result error updated',
}
INPUT_FILES = {'index.html', 'plan.json', 'beats.json', 'alignment.json', 'timeline.json', 'manifest.json', 'audio.wav'}
DEFAULT_LIMITS = dict(max_files=20000, max_rows=10000, max_text_bytes=64 * 1024 * 1024,
                      max_hash_bytes=512 * 1024 * 1024, max_candidates=256)


class MaintenanceError(RuntimeError):
    pass


def require(condition, code):
    if not condition:
        raise MaintenanceError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def ordinary_path(value, *, directory=False):
    path = Path(os.path.abspath(value))
    for part in (path, *path.parents):
        require(not part.is_symlink() and not getattr(part, 'is_junction', lambda: False)(), 'storage_link_refused')
    require(path.is_dir() if directory else path.is_file(), 'storage_path_unavailable')
    require(directory or stat.S_ISREG(path.stat().st_mode), 'storage_nonregular_refused')
    return path


def identity(info):
    return dict(device=info.st_dev, inode=info.st_ino, uid=info.st_uid, gid=info.st_gid,
                mode=stat.S_IMODE(info.st_mode), bytes=info.st_size, mtime_ns=info.st_mtime_ns,
                links=info.st_nlink)


@contextmanager
def database(path, *, reserve=False):
    target = ordinary_path(path)
    require(target.stat().st_nlink == 1, 'storage_database_hardlink_refused')
    before = identity(target.stat())
    # In WAL mode mode=ro BEGIN IMMEDIATE does not reserve a writer slot.
    connection = sqlite3.connect(target.as_uri() + ('?mode=rw' if reserve else '?mode=ro'), uri=True, timeout=0)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute('BEGIN IMMEDIATE' if reserve else 'BEGIN')
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_TRANSACTION}
        connection.set_authorizer(lambda action, name, *_: sqlite3.SQLITE_OK
            if action in allowed or action == sqlite3.SQLITE_PRAGMA and name in {'quick_check', 'table_info'}
            else sqlite3.SQLITE_DENY)
        deadline, steps = time.monotonic() + 15, 0
        def budget():
            nonlocal steps
            steps += 1
            return int(steps > 20000 or time.monotonic() > deadline)
        connection.set_progress_handler(budget, 1000)
        require(connection.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'storage_database_invalid')
        require(identity(target.stat()) == before, 'storage_database_identity_changed')
        yield connection
    finally:
        connection.set_authorizer(None)
        connection.rollback()
        connection.close()


def capture(connection, expected, limits):
    schema = [tuple(row) for row in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT GLOB 'sqlite_*' ORDER BY type,name")]
    tables = {row[1] for row in schema if row[0] == 'table'}
    require(tables <= set(expected) and all(row[0] in {'table', 'index'} and row[2] in tables for row in schema),
            'storage_unknown_schema')
    required = {'motion_operations', 'motion_scenes'} if expected is EXEC_COLUMNS else {'projects', 'video_renders'}
    require(required <= tables, 'storage_incomplete_schema')
    rows, texts, summary, fingerprints = {}, [], {}, []
    row_count = text_bytes = 0
    for table in sorted(tables):
        columns = expected[table].split()
        # PRAGMA is intentionally outside the authorizer's allowlist. Read the
        # same metadata using its table-valued read-only equivalent.
        metadata = list(connection.execute('SELECT name,type,pk FROM pragma_table_info(?)', (table,)))
        require([r['name'] for r in metadata] == columns, 'storage_unknown_columns')
        keys = ('key' if expected is EXEC_COLUMNS or table == 'metadata' else
            'owner project_id scene_id' if table == 'motion_heads' else
            'owner motion_version_id audio_version_id' if table == 'motion_alignments' else
            'owner project_id' if table == 'explanation_heads' else 'id').split()
        require([r['name'] for r in sorted(metadata, key=lambda row: row['pk']) if r['pk']] == keys,
                'storage_primary_key_mismatch')
        expected_type = lambda name: 'BLOB' if name == 'data' else 'INTEGER' if name == 'cancel_requested' else (
            'REAL' if expected is EXEC_COLUMNS and name in {'updated', 'created'} else 'TEXT')
        require(all(r['type'].upper() == expected_type(r['name']) for r in metadata), 'storage_column_type_mismatch')
        text_sizes = '+'.join(f'coalesce(length(CAST("{col}" AS BLOB)),0)' for col in columns if col != 'data')
        count, byte_count = connection.execute(f'SELECT count(*),coalesce(sum({text_sizes}),0) FROM "{table}"').fetchone()
        require(row_count + count <= limits['max_rows'], 'storage_row_budget_exceeded')
        require(text_bytes + byte_count <= limits['max_text_bytes'], 'storage_text_budget_exceeded')
        selected = [f'length("{col}") AS "{col}"' if col == 'data' else f'"{col}"' for col in columns]
        result = connection.execute(f'SELECT {",".join(selected)} FROM "{table}"')
        values = []
        for raw in result:
            row_count += 1
            require(row_count <= limits['max_rows'], 'storage_row_budget_exceeded')
            row = dict(raw)
            for col, value in row.items():
                require(value is None or type(value) in {str, int, float}, 'storage_unknown_value_type')
                if type(value) is str:
                    text_bytes += len(value.encode('utf-8'))
                    require(text_bytes <= limits['max_text_bytes'], 'storage_text_budget_exceeded')
                    texts.append(value)
            values.append(row)
        values.sort(key=canonical)
        rows[table] = values
        fingerprints.append([table, values])
        summary[table] = dict(rows=len(values), blob_bytes=sum(row.get('data') or 0 for row in values))
    return dict(schema_sha256=sha(canonical(schema)), state_sha256=sha(canonical(fingerprints)), tables=summary), rows, texts


def document(raw):
    value = json.loads(raw)
    require(type(value) is dict, 'storage_invalid_document')
    return value


def database_files(path):
    rows = []
    for suffix in ('', '-wal', '-shm', '-journal'):
        target = Path(str(path) + suffix)
        if target.exists() or target.is_symlink():
            ordinary_path(target)
            require(target.stat().st_nlink == 1, 'storage_database_hardlink_refused')
            rows.append(dict(suffix=suffix or 'main', bytes=target.stat().st_size))
    return rows


def file_hash(path, expected):
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    descriptor = os.open(path, flags)
    try:
        require(identity(os.fstat(descriptor)) == expected, 'storage_file_identity_changed')
        digest, count = hashlib.sha256(), 0
        while chunk := os.read(descriptor, 1024 * 1024):
            count += len(chunk)
            require(count <= expected['bytes'], 'storage_file_identity_changed')
            digest.update(chunk)
        require(identity(os.fstat(descriptor)) == expected, 'storage_file_identity_changed')
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _inventory(business, executor, root, *, min_age_seconds, limits, now, connections):
    business_info, business_rows, texts = capture(connections[0], BUSINESS_COLUMNS, limits)
    executor_info, execution_rows, _ = capture(connections[1], EXEC_COLUMNS, limits)
    blockers = []
    for table, rows in business_rows.items():
        if table.endswith('_operations') or table in {'operations', 'video_renders', 'video_batches'}:
            if any(document(row['document']).get('status') not in TERMINAL for row in rows):
                blockers.append('active_or_unknown_business_operation')
    operations = {row['key']: row for row in execution_rows['motion_operations']}
    scenes = {row['key']: row for row in execution_rows['motion_scenes']}
    if any(row['state'] not in EXEC_TERMINAL or 'cleanup_pending' in (row['result'] or '') for row in operations.values()):
        blockers.append('active_or_pending_executor_operation')
    if any(row['state'] not in EXEC_TERMINAL or 'cleanup_pending' in (row['result'] or '') for row in scenes.values()):
        blockers.append('active_or_pending_executor_scene')
    renders = {row['id']: row for row in business_rows['video_renders']}
    projects = {row['id']: row for row in business_rows['projects']}
    eligible = set()
    for key, op in operations.items():
        business_row = renders.get(op['operation_id'])
        if not business_row:
            continue  # An unproven orphan is report-only.
        job = document(business_row['document'])
        snapshot = document(op['snapshot'])
        packed = dict(snapshot, scenes=[dict(scene, audio={k: v for k, v in scene['audio'].items()
            if k != 'data_base64'}) for scene in snapshot.get('scenes', [])])
        project = projects.get(business_row['project_id'])
        expected_owner = sha(business_row['owner'].encode())
        if (re.fullmatch('[0-9a-f]{64}', key) and op['owner'] == expected_owner
                and key == sha((expected_owner + ':' + op['operation_id']).encode())
                and project and project['owner'] == business_row['owner']
                and job.get('id') == business_row['id'] and job.get('owner') == business_row['owner']
                and snapshot.get('project_id') == business_row['project_id']
                and snapshot.get('sha256') == op['snapshot_sha256']
                and sha(canonical({k: v for k, v in snapshot.items() if k != 'sha256'})) == op['snapshot_sha256']
                and document(business_row['snapshot']) == packed
                and job.get('status') in TERMINAL and job.get('snapshot_sha256') == op['snapshot_sha256']
                and op['state'] in EXEC_TERMINAL and op['updated'] <= now - min_age_seconds):
            eligible.add(key)
    files, directories, counts = [], [], dict(files=0, directories=0, bytes=0, retained_bytes=0)
    # scandir never follows directory links. Every entry, including retained
    # ones, consumes the traversal budget; a partial scan never permits apply.
    pending = [root]
    while pending:
        directory = pending.pop()
        directories.append([directory.relative_to(root).as_posix(), identity(directory.stat())])
        with os.scandir(directory) as entries:
            for entry in entries:
                counts['files' if not entry.is_dir(follow_symlinks=False) else 'directories'] += 1
                require(counts['files'] + counts['directories'] <= limits['max_files'], 'storage_file_budget_exceeded')
                # Windows DirEntry.stat can omit inode/device/link identity;
                # lstat provides the identity that an opened file reports.
                info = Path(entry.path).lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(Path(entry.path), 'is_junction', lambda: False)():
                    blockers.append('storage_link_refused')
                elif stat.S_ISDIR(info.st_mode):
                    if info.st_dev != root.stat().st_dev or os.path.ismount(entry.path):
                        blockers.append('storage_mount_boundary_refused')
                    elif info.st_uid != root.stat().st_uid:
                        blockers.append('storage_uid_conflict')
                    else:
                        pending.append(Path(entry.path))
                elif stat.S_ISREG(info.st_mode):
                    files.append((Path(entry.path), identity(info)))
                    counts['bytes'] += info.st_size
                    if info.st_nlink != 1:
                        blockers.append('storage_hardlink_refused')
                else:
                    blockers.append('storage_nonregular_refused')
    candidates, retained, hashed = [], [], 0
    tree = []
    for path, info in sorted(files):
        relative = path.relative_to(root).as_posix()
        tree.append([relative, info])
        parts = Path(relative).parts
        reason = 'historical_or_unknown_file_preserved'
        candidate = False
        if parts[0] in eligible and len(parts) >= 3:
            if parts[1:3] == ('result', 'assets'):
                candidate = True
            elif re.fullmatch(r'scene-[0-9]{3}', parts[1]):
                index = int(parts[1][6:])
                record = scenes.get(parts[0] + ':' + str(index))
                expected_name = 'vm-' + sha((parts[0] + ':' + str(index)).encode())[:36]
                snapshot = document(operations[parts[0]]['snapshot'])
                if (record and record['name'] == expected_name and re.fullmatch('[0-9a-f]{64}', record['binding'])
                        and index < len(snapshot.get('scenes', [])) and record['state'] in EXEC_TERMINAL
                        and record['updated'] <= now - min_age_seconds):
                    candidate = (len(parts) == 4 and parts[2] == 'input' and parts[3] in INPUT_FILES
                                 or len(parts) == 3 and parts[2] == 'output.pending')
            if candidate:
                reason = 'known_rebuildable_scratch'
                fragments = {str(path), path.as_posix(), relative, path.name}
                if any(fragment in text for text in texts for fragment in fragments):
                    candidate, reason = False, 'possible_business_reference_preserved'
                elif info['mtime_ns'] > (now - min_age_seconds) * 10 ** 9:
                    candidate, reason = False, 'retention_age_not_met'
                elif info['uid'] != root.stat().st_uid:
                    blockers.append('storage_uid_conflict')
                    candidate = False
        if candidate:
            hashed += info['bytes']
            require(hashed <= limits['max_hash_bytes'], 'storage_hash_budget_exceeded')
            require(len(candidates) < limits['max_candidates'], 'storage_candidate_budget_exceeded')
            candidates.append(dict(path=relative, identity=info, sha256=file_hash(path, info), reason=reason))
        else:
            retained.append(dict(path=relative, bytes=info['bytes'], reason=reason))
            counts['retained_bytes'] += info['bytes']
    adopted_audio, adopted_static = set(), set()
    for row in business_rows['projects']:
        for scene in document(row['document']).get('scenes', []):
            adopted_audio.add(scene.get('head_audio_version_id'))
            adopted_static.add(scene.get('head_version_id'))
    retention = {}
    for table, adopted in (('audio_versions', adopted_audio), ('versions', adopted_static),
            ('motion_versions', {row['version_id'] for row in business_rows.get('motion_heads', [])}),
            ('explanation_versions', {row['version_id'] for row in business_rows.get('explanation_heads', [])})):
        rows = business_rows.get(table, [])
        selected = sum(row['id'] in adopted for row in rows)
        retention[table] = dict(total=len(rows), adopted=selected, unadopted_preserved=len(rows) - selected)
    return dict(schema=SCHEMA, created_at=int(now), expires_at=int(now) + 3600,
        config=dict(business_db=str(business), executor_db=str(executor), execution_root=str(root),
                    min_age_seconds=min_age_seconds, limits=limits),
        root_identity=identity(root.stat()), business=business_info, executor=executor_info,
        database_identities=dict(business=identity(business.stat()), executor=identity(executor.stat())),
        database_files=dict(business=database_files(business), executor=database_files(executor)),
        retention=retention,
        tree_sha256=sha(canonical([tree, sorted(directories)])), totals=counts,
        blockers=sorted(set(blockers)), apply_allowed=not blockers, candidates=candidates, retained=retained,
        policy=dict(business_rows='preserved', audio_and_video_blobs='preserved', final_media='preserved',
                    unproven_orphans='report_only', kubernetes='never_accessed', min_age_seconds=min_age_seconds))


def inventory(business_db, executor_db, execution_root, *, min_age_seconds=86400, limits=None, now=None):
    configured = DEFAULT_LIMITS | (limits or {})
    require(set(configured) == set(DEFAULT_LIMITS) and all(type(v) is int and v > 0 for v in configured.values()),
            'storage_invalid_limits')
    require(type(min_age_seconds) is int and 0 <= min_age_seconds <= 3650 * 86400, 'storage_invalid_retention')
    business, executor = ordinary_path(business_db), ordinary_path(executor_db)
    root = ordinary_path(execution_root, directory=True)
    require(business != executor and not business.is_relative_to(root) and not executor.is_relative_to(root),
            'storage_root_overlap')
    with database(business) as first, database(executor) as second:
        return _inventory(business, executor, root, min_age_seconds=min_age_seconds, limits=configured,
                          now=time.time() if now is None else now, connections=(first, second))


def write_manifest(plan, destination):
    target = Path(os.path.abspath(destination))
    root = Path(plan['config']['execution_root'])
    protected = {Path(plan['config'][name] + suffix) for name in ('business_db', 'executor_db')
                 for suffix in ('', '-wal', '-shm', '-journal')}
    require(not target.is_relative_to(root) and not target.is_symlink() and target not in protected,
            'storage_manifest_location_invalid')
    ordinary_path(target.parent, directory=True)
    raw = canonical(plan) + b'\n'
    require(len(raw) <= 8 * 1024 * 1024, 'storage_manifest_budget_exceeded')
    with target.open('xb') as output:
        output.write(raw)
    return sha(raw)


def _rename_noreplace(parent, source, destination_parent, destination):
    # Atomic staging prevents a replaced pathname from being unlinked on the
    # strength of a previously opened descriptor. No unsafe rename fallback.
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    require(rename is not None, 'storage_atomic_staging_unavailable')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(parent, os.fsencode(source), destination_parent, os.fsencode(destination), 1) != 0:
        raise OSError(ctypes.get_errno(), 'storage_atomic_staging_failed')


def _descriptor_hash(descriptor, expected):
    require(identity(os.fstat(descriptor)) == expected, 'storage_file_identity_changed')
    os.lseek(descriptor, 0, os.SEEK_SET)
    checksum, count = hashlib.sha256(), 0
    while chunk := os.read(descriptor, 1024 * 1024):
        count += len(chunk)
        require(count <= expected['bytes'], 'storage_file_identity_changed')
        checksum.update(chunk)
    require(identity(os.fstat(descriptor)) == expected, 'storage_file_identity_changed')
    return checksum.hexdigest()


def inspect_manifest(manifest, expected_sha256, *, now=None):
    path = ordinary_path(manifest)
    require(path.stat().st_size <= 8 * 1024 * 1024, 'storage_manifest_budget_exceeded')
    with path.open('rb') as source:
        raw = source.read(8 * 1024 * 1024 + 1)
    require(len(raw) <= 8 * 1024 * 1024, 'storage_manifest_budget_exceeded')
    require(re.fullmatch('[0-9a-f]{64}', expected_sha256 or '') and sha(raw) == expected_sha256,
            'storage_manifest_digest_mismatch')
    plan = document(raw)
    require(plan.get('schema') == SCHEMA and plan.get('apply_allowed') is True and not plan.get('blockers'),
            'storage_manifest_not_applicable')
    current_time = time.time() if now is None else now
    require(plan['created_at'] <= current_time <= plan['expires_at'], 'storage_manifest_expired')
    return path, plan, current_time


def apply_manifest(manifest, expected_sha256, receipt, *, quarantine_root=None, now=None):
    require(os.name == 'posix' and hasattr(os, 'O_NOFOLLOW') and hasattr(os, 'O_DIRECTORY')
            and os.open in os.supports_dir_fd and os.unlink in os.supports_dir_fd,
            'storage_apply_linux_required')
    path, plan, current_time = inspect_manifest(manifest, expected_sha256, now=now)
    config = plan['config']
    business, executor = ordinary_path(config['business_db']), ordinary_path(config['executor_db'])
    root = ordinary_path(config['execution_root'], directory=True)
    require(business != executor and not business.is_relative_to(root) and not executor.is_relative_to(root),
            'storage_root_overlap')
    require(config['limits'] == DEFAULT_LIMITS, 'storage_apply_custom_limits_refused')
    require(type(config['min_age_seconds']) is int and 0 <= config['min_age_seconds'] <= 3650 * 86400,
            'storage_invalid_retention')
    require(quarantine_root is not None, 'storage_private_quarantine_required')
    quarantine = ordinary_path(quarantine_root, directory=True)
    private = quarantine.stat()
    require(private.st_uid == os.geteuid() and stat.S_IMODE(private.st_mode) == 0o700
            and private.st_dev == root.stat().st_dev and root.stat().st_uid != os.geteuid(),
            'storage_operator_identity_or_quarantine_invalid')
    require(not quarantine.is_relative_to(root) and not root.is_relative_to(quarantine)
            and not business.is_relative_to(quarantine) and not executor.is_relative_to(quarantine),
            'storage_quarantine_overlap')
    output = Path(os.path.abspath(receipt))
    require(output != path and not output.is_relative_to(root), 'storage_receipt_location_invalid')
    protected = {Path(str(db) + suffix) for db in (business, executor) for suffix in ('', '-wal', '-shm', '-journal')}
    require(output not in protected, 'storage_receipt_location_invalid')
    ordinary_path(output.parent, directory=True)
    deleted = []
    with ExitStack() as stack:
        first = stack.enter_context(database(business, reserve=True))
        second = stack.enter_context(database(executor, reserve=True))
        refreshed = _inventory(business, executor, root, min_age_seconds=config['min_age_seconds'],
                               limits=config['limits'], now=current_time, connections=(first, second))
        comparison = ('config', 'root_identity', 'database_identities', 'business', 'executor',
                      'tree_sha256', 'candidates', 'blockers', 'apply_allowed')
        require(all(plan.get(key) == refreshed.get(key) for key in comparison), 'storage_preview_changed')
        require(refreshed['apply_allowed'], 'storage_activity_or_reference_changed')
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, root_fd)
        require(identity(os.fstat(root_fd)) == plan['root_identity'], 'storage_root_identity_changed')
        private_fd = os.open(quarantine, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, private_fd)
        require(identity(os.fstat(private_fd)) == identity(private), 'storage_quarantine_identity_changed')
        handles = []
        for item in refreshed['candidates']:
            parts = item['path'].split('/')
            require(all(part not in {'', '.', '..'} and '\\' not in part for part in parts), 'storage_path_escape')
            parent = root_fd
            for part in parts[:-1]:
                parent = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                stack.callback(os.close, parent)
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            stack.callback(os.close, descriptor)
            require(_descriptor_hash(descriptor, item['identity']) == item['sha256'], 'storage_file_digest_changed')
            handles.append((parent, parts[-1], descriptor, item))
        # All candidates are opened and verified before the first mutation.
        audit = output.open('xb')
        stack.callback(audit.close)
        def record(value):
            audit.write(canonical(value) + b'\n')
            audit.flush()
            os.fsync(audit.fileno())
        record(dict(status='started', manifest_sha256=expected_sha256, database_content_writes=0))
        quarantine_name = 'storage-' + uuid.uuid4().hex
        os.mkdir(quarantine_name, 0o700, dir_fd=private_fd)
        quarantine_fd = os.open(quarantine_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=private_fd)
        stack.callback(os.close, quarantine_fd)
        try:
            for parent, name, descriptor, item in handles:
                require(_descriptor_hash(descriptor, item['identity']) == item['sha256'], 'storage_file_digest_changed')
                stage = '.storage-maintenance-' + uuid.uuid4().hex
                stage_path = str(quarantine / quarantine_name / stage)
                record(dict(status='staging', path=item['path'], stage=stage_path, sha256=item['sha256']))
                _rename_noreplace(parent, name, quarantine_fd, stage)
                try:
                    staged = os.stat(stage, dir_fd=quarantine_fd, follow_symlinks=False)
                    require(identity(staged) == item['identity'], 'storage_file_identity_changed')
                    require(_descriptor_hash(descriptor, item['identity']) == item['sha256'],
                            'storage_file_digest_changed')
                except (MaintenanceError, OSError):
                    # A foreign replacement is preserved, never deleted. If a
                    # concurrent actor also filled its old name, leave staging
                    # intact and the receipt identifies the recovery location.
                    _rename_noreplace(quarantine_fd, stage, parent, name)
                    raise
                os.unlink(stage, dir_fd=quarantine_fd)
                deleted.append(dict(path=item['path'], bytes=item['identity']['bytes'], sha256=item['sha256']))
                record(dict(status='deleted', **deleted[-1]))
            os.rmdir(quarantine_name, dir_fd=private_fd)
            record(dict(status='completed', files=len(deleted), bytes=sum(item['bytes'] for item in deleted)))
        except BaseException:
            try:
                os.rmdir(quarantine_name, dir_fd=private_fd)
            except OSError:
                pass  # Preserve nonempty staging for receipt-based recovery.
            record(dict(status='stopped', files_deleted=len(deleted), recovery='inspect_recorded_staging_paths'))
            raise
    return dict(status='completed', files=len(deleted), bytes=sum(item['bytes'] for item in deleted),
                database_content_writes=0, kubernetes_calls=0, receipt=str(output))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', choices=['inventory', 'dry-run', 'apply'], default='inventory')
    parser.add_argument('--business-db')
    parser.add_argument('--executor-db')
    parser.add_argument('--execution-root')
    parser.add_argument('--min-age-seconds', type=int, default=86400)
    parser.add_argument('--manifest')
    parser.add_argument('--confirm-sha256')
    parser.add_argument('--receipt')
    parser.add_argument('--quarantine-root')
    args = parser.parse_args(argv)
    try:
        if args.action == 'apply':
            require(args.manifest and args.confirm_sha256 and args.receipt and args.quarantine_root
                    and not any((args.business_db, args.executor_db, args.execution_root)), 'storage_apply_arguments_invalid')
            print(json.dumps(apply_manifest(args.manifest, args.confirm_sha256, args.receipt,
                quarantine_root=args.quarantine_root), sort_keys=True))
            return 0
        require(args.business_db and args.executor_db and args.execution_root
                and not args.confirm_sha256 and not args.receipt and not args.quarantine_root,
                'storage_inventory_arguments_invalid')
        plan = inventory(args.business_db, args.executor_db, args.execution_root,
                         min_age_seconds=args.min_age_seconds)
        receipt = dict(manifest_sha256=write_manifest(plan, args.manifest)) if args.manifest else {}
        print(json.dumps(plan | receipt, ensure_ascii=False, sort_keys=True))
        return 0 if plan['apply_allowed'] else 2
    except Exception:
        print(json.dumps(dict(status='blocked', error='storage_maintenance_unavailable', apply_allowed=False)))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
