"""Stdlib-only Linux risk checks in exclusive temporary fixture directories.

Run with the storage_maintenance.py path. No production paths, Kubernetes,
network, business services, Agent, TTS or real media are used.
"""
from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile


def load(path):
    spec = importlib.util.spec_from_file_location('storage_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def typed_value(value):
    if value is None:
        return ['null', None]
    if type(value) is bytes:
        return ['blob', len(value), hashlib.sha256(value).hexdigest()]
    if type(value) is str:
        return ['text', value]
    if type(value) is int:
        return ['integer', value]
    if type(value) is float:
        return ['real', value.hex()]
    raise AssertionError('unknown SQLite value type: ' + str(type(value)))


def logical_database(path):
    """Compare exact logical state, hashing complete BLOB bytes rather than lengths."""
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as connection:
        connection.execute('BEGIN')
        schema = list(connection.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'))
        tables = []
        for kind, name, _, sql in schema:
            if kind != 'table':
                continue
            quoted = '"' + name.replace('"', '""') + '"'
            columns = list(connection.execute('PRAGMA table_info(' + quoted + ')'))
            select = '*' if 'WITHOUT ROWID' in (sql or '').upper() else 'rowid,*'
            rows = [[typed_value(value) for value in row]
                for row in connection.execute('SELECT ' + select + ' FROM ' + quoted)]
            rows.sort(key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True))
            tables.append(dict(name=name, columns=columns, select=select, rows=rows))
        result = dict(schema=schema, tables=tables,
            integrity=list(connection.execute('PRAGMA integrity_check')),
            user_version=connection.execute('PRAGMA user_version').fetchone()[0],
            application_id=connection.execute('PRAGMA application_id').fetchone()[0])
        connection.rollback()
        return result


def physical_database(path):
    result = {}
    for suffix in ('', '-wal', '-shm'):
        target = Path(str(path) + suffix)
        raw = target.read_bytes() if target.exists() else None
        result[suffix or 'main'] = (dict(exists=False) if raw is None else
            dict(exists=True, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
    return result


def blob_digests(snapshot):
    return [dict(table=table['name'], bytes=value[1], sha256=value[2])
        for table in snapshot['tables'] for row in table['rows'] for value in row if value[0] == 'blob']


def fixture(s, root):
    root.chmod(0o755)
    business, executor, execution = root / 'business.db', root / 'executor.db', root / 'executions'
    execution.mkdir()
    owner, operation = 'isolated-fixture-owner', 'isolated-operation'
    snapshot = dict(project_id='fixture-project', scenes=[dict(audio=dict(data_base64='fixture'))])
    snapshot['sha256'] = s.sha(s.canonical(snapshot))
    packed = dict(snapshot, scenes=[dict(audio={})])
    digest_owner = s.sha(owner.encode())
    key = s.sha((digest_owner + ':' + operation).encode())
    def schema(db, tables, execute=False):
        for name, columns in tables.items():
            columns = columns.split()
            primary = ('key' if execute or name == 'metadata' else
                'owner project_id scene_id' if name == 'motion_heads' else
                'owner motion_version_id audio_version_id' if name == 'motion_alignments' else
                'owner project_id' if name == 'explanation_heads' else 'id').split()
            fields = [f'{column} ' + ('BLOB' if column == 'data' else 'INTEGER' if column == 'cancel_requested'
                else 'REAL' if execute and column in {'updated', 'created'} else 'TEXT') for column in columns]
            fields.append('PRIMARY KEY (' + ','.join(primary) + ')')
            db.execute(f'CREATE TABLE {name} (' + ','.join(fields) + ')')
    with closing(sqlite3.connect(business)) as db, db:
        db.execute('PRAGMA journal_mode=WAL')
        schema(db, s.BUSINESS_COLUMNS)
        db.execute('INSERT INTO projects VALUES (?,?,?)', ('fixture-project', owner, '{"scenes":[]}'))
        job = dict(id=operation, owner=owner, status='completed', snapshot_sha256=snapshot['sha256'])
        db.execute('INSERT INTO video_renders VALUES (?,?,?,?,?,?,?,?)',
            (operation, owner, 'fixture-project', 'idempotent', 'request', json.dumps(job), json.dumps(packed), b'KEEP BLOB'))
    with closing(sqlite3.connect(executor)) as db, db:
        db.execute('PRAGMA journal_mode=WAL')
        schema(db, s.EXEC_COLUMNS, execute=True)
        db.execute('INSERT INTO motion_operations VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (key, digest_owner, operation, snapshot['sha256'], json.dumps(snapshot), 'completed', 0, '{}', None, 1.0, 1.0))
        name = 'vm-' + s.sha((key + ':0').encode())[:36]
        db.execute('INSERT INTO motion_scenes VALUES (?,?,?,?,?,?,?)',
            (key + ':0', 'b' * 64, name, 'completed', '{}', None, 1.0))
    scratch = execution / key / 'result/assets/master.wav'
    scratch.parent.mkdir(parents=True)
    scratch.write_bytes(b'rebuildable fixture bytes')
    final = execution / key / 'result/video.mp4'
    final.write_bytes(b'KEEP final file')
    for item in [execution, *execution.rglob('*')]:
        os.chown(item, 10001, 10001)
    (root / 'operator-private').mkdir(mode=0o700)
    manifest = root / 'review.json'
    plan = s.inventory(business, executor, execution, min_age_seconds=0)
    checksum = s.write_manifest(plan, manifest)
    return business, executor, execution, scratch, final, manifest, checksum, plan


def run(module_path):
    assert os.name == 'posix' and os.geteuid() == 0, 'isolated root test container required'
    s = load(module_path)
    passed, preservation = [], []
    with tempfile.TemporaryDirectory(prefix='isolated-storage-risk-') as directory:
        base = Path(directory)
        base.chmod(0o755)
        def apply(manifest, checksum, receipt):
            paths = {name: manifest.parent / (name + '.db') for name in ('business', 'executor')}
            before = {name: logical_database(path) for name, path in paths.items()}
            physical_before = {name: physical_database(path) for name, path in paths.items()}
            try:
                return s.apply_manifest(manifest, checksum, receipt,
                    quarantine_root=manifest.parent / 'operator-private')
            finally:
                physical_after = {name: physical_database(path) for name, path in paths.items()}
                after = {name: logical_database(path) for name, path in paths.items()}
                proofs = {}
                for name in paths:
                    proofs[name] = dict(logical_equal=before[name] == after[name],
                        logical_before_sha256=s.sha(s.canonical(before[name])),
                        logical_after_sha256=s.sha(s.canonical(after[name])),
                        blob_before=blob_digests(before[name]), blob_after=blob_digests(after[name]),
                        physical_before=physical_before[name], physical_after=physical_after[name],
                        physical_equal=physical_before[name] == physical_after[name])
                preservation.append(dict(case=manifest.parent.name, databases=proofs))
                assert before == after, 'schema, typed row values or complete BLOB digests changed during apply'
        def make(name):
            folder = base / name
            folder.mkdir()
            return fixture(s, folder)
        business, executor, execution, scratch, final, manifest, checksum, plan = make('success')
        result = apply(manifest, checksum, base / 'success-receipt.jsonl')
        assert result['files'] == 1 and not scratch.exists() and final.read_bytes() == b'KEEP final file'
        passed.append('exact_scratch_only_complete_logical_databases_and_media_blob_digests_preserved')

        business, executor, execution, scratch, final, manifest, checksum, plan = make('active')
        with closing(sqlite3.connect(executor)) as db, db:
            db.execute("UPDATE motion_operations SET state='running'")
        try:
            apply(manifest, checksum, base / 'active-receipt.jsonl')
            raise AssertionError('active operation was accepted')
        except s.MaintenanceError:
            assert scratch.exists() and final.exists()
        passed.append('new_activity_after_preview_refuses_all_deletion')

        business, executor, execution, scratch, final, manifest, checksum, plan = make('changed')
        scratch.write_bytes(b'new and unreviewed data')
        try:
            apply(manifest, checksum, base / 'changed-receipt.jsonl')
            raise AssertionError('changed file was accepted')
        except s.MaintenanceError:
            assert scratch.read_bytes() == b'new and unreviewed data'
        passed.append('file_digest_identity_or_tree_change_refused')

        business, executor, execution, scratch, final, manifest, checksum, plan = make('linked')
        scratch.unlink()
        outside = base / 'external-file'
        outside.write_bytes(b'KEEP external')
        scratch.symlink_to(outside)
        try:
            apply(manifest, checksum, base / 'linked-receipt.jsonl')
            raise AssertionError('symlink replacement was accepted')
        except s.MaintenanceError:
            assert outside.read_bytes() == b'KEEP external'
        passed.append('late_symlink_never_deletes_external_file')

        business, executor, execution, scratch, final, manifest, checksum, plan = make('locks')
        original_inventory = s._inventory
        def writer_probe(first, second, *args, **kwargs):
            result = original_inventory(first, second, *args, **kwargs)
            for target in (first, second):
                with closing(sqlite3.connect(target, timeout=0)) as writer, writer:
                    try:
                        writer.execute('BEGIN IMMEDIATE')
                        raise AssertionError('WAL writer not blocked')
                    except sqlite3.OperationalError:
                        pass
            return result
        s._inventory = writer_probe
        try:
            apply(manifest, checksum, base / 'locks-receipt.jsonl')
        finally:
            s._inventory = original_inventory
        passed.append('both_wal_writer_slots_reserved_through_revalidation')

        business, executor, execution, scratch, final, manifest, checksum, plan = make('rename-race')
        original_rename = s._rename_noreplace
        replaced = False
        def replacement_race(parent, source, destination_parent, destination):
            nonlocal replaced
            if not replaced:
                replaced = True
                os.rename(source, 'preserved-original', src_dir_fd=parent, dst_dir_fd=parent)
                fd = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
                os.write(fd, b'KEEP concurrent foreign replacement')
                os.close(fd)
            return original_rename(parent, source, destination_parent, destination)
        s._rename_noreplace = replacement_race
        try:
            try:
                apply(manifest, checksum, base / 'race-receipt.jsonl')
                raise AssertionError('concurrent replacement was deleted')
            except s.MaintenanceError:
                assert scratch.read_bytes() == b'KEEP concurrent foreign replacement'
                assert scratch.with_name('preserved-original').read_bytes() == b'rebuildable fixture bytes'
                assert final.exists()
        finally:
            s._rename_noreplace = original_rename
        passed.append('atomic_staging_detects_and_preserves_concurrent_foreign_replacement')

        business, executor, execution, scratch, final, manifest, checksum, plan = make('private-stage')
        original_stat, attempted = s.os.stat, False
        def stat_with_worker(path, *args, **kwargs):
            nonlocal attempted
            result = original_stat(path, *args, **kwargs)
            if str(path).startswith('.storage-maintenance-') and kwargs.get('dir_fd') is not None and not attempted:
                attempted = True
                child = os.fork()
                if child == 0:
                    os.setgid(10001)
                    os.setuid(10001)
                    try:
                        os.unlink(path, dir_fd=kwargs['dir_fd'])
                    except PermissionError:
                        os._exit(0)
                    os._exit(2)
                _, status = os.waitpid(child, 0)
                assert os.waitstatus_to_exitcode(status) == 0, 'worker could replace private staged file'
            return result
        s.os.stat = stat_with_worker
        try:
            result = apply(manifest, checksum, base / 'private-receipt.jsonl')
            assert result['files'] == 1 and attempted and final.exists()
        finally:
            s.os.stat = original_stat
        passed.append('different_worker_uid_cannot_replace_stage_between_stat_and_unlink')

        business, executor, execution, scratch, final, manifest, checksum, plan = make('held-write-fd')
        original_stat, mutated = s.os.stat, False
        writer_fd = os.open(scratch, os.O_WRONLY)
        def stat_with_held_writer(path, *args, **kwargs):
            nonlocal mutated
            result = original_stat(path, *args, **kwargs)
            if str(path).startswith('.storage-maintenance-') and kwargs.get('dir_fd') is not None and not mutated:
                mutated = True
                os.write(writer_fd, b'KEEP changed bytes')
                os.fsync(writer_fd)
            return result
        s.os.stat = stat_with_held_writer
        try:
            try:
                apply(manifest, checksum, base / 'held-writer-receipt.jsonl')
                raise AssertionError('file changed through held writer was deleted')
            except s.MaintenanceError:
                assert mutated and scratch.read_bytes().startswith(b'KEEP changed bytes') and final.exists()
        finally:
            s.os.stat = original_stat
            os.close(writer_fd)
        passed.append('held_write_descriptor_change_after_staging_is_restored_and_preserved')

        business, executor, execution, scratch, final, manifest, checksum, plan = make('restore-conflict')
        original_rename, collided = s._rename_noreplace, False
        def restoration_conflict(parent, source, destination_parent, destination):
            nonlocal collided
            if not collided:
                collided = True
                os.rename(source, 'preserved-original', src_dir_fd=parent, dst_dir_fd=parent)
                fd = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
                os.write(fd, b'KEEP first foreign file staged')
                os.close(fd)
                result = original_rename(parent, source, destination_parent, destination)
                fd = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
                os.write(fd, b'KEEP second foreign file at original name')
                os.close(fd)
                return result
            return original_rename(parent, source, destination_parent, destination)
        s._rename_noreplace = restoration_conflict
        receipt = base / 'conflict-receipt.jsonl'
        try:
            try:
                apply(manifest, checksum, receipt)
                raise AssertionError('restore conflict was accepted')
            except OSError:
                records = [json.loads(line) for line in receipt.read_text().splitlines()]
                staged = next(Path(row['stage']) for row in records if row['status'] == 'staging')
                assert staged.read_bytes() == b'KEEP first foreign file staged'
                assert scratch.read_bytes() == b'KEEP second foreign file at original name'
                assert scratch.with_name('preserved-original').read_bytes() == b'rebuildable fixture bytes'
                assert final.exists() and records[-1]['status'] == 'stopped'
        finally:
            s._rename_noreplace = original_rename
        passed.append('restore_name_conflict_preserves_all_files_private_staging_and_recovery_receipt')
    print(json.dumps(dict(status='passed', checks=passed, database_preservation=preservation,
        physical_byte_identity_required=False,
        physical_boundary='SQLite WAL checkpoint and lock sidecars can change without SQL data changes; '
            'complete schema, typed rows and full BLOB SHA256 are compared separately for every apply attempt',
        production_paths_accessed=False,
        database_content_writes=0, agent_calls=0, tts_calls=0, kubernetes_calls=0), sort_keys=True))


if __name__ == '__main__':
    run(sys.argv[1])
