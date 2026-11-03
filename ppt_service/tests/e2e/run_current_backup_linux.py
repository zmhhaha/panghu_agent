#!/usr/bin/env python3
"""Current-schema, Git-sourced, isolated PostgreSQL dump/restore rehearsal.

Only fixed synthetic databases and new owned evidence files are used. This is a
quiescent fixture restore, never production snapshot or off-site DR evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

if __package__:
    from .run_current_security_linux import IMMUTABLE_IMAGE, LABEL, Run, clean_git, command, snapshot_git, write_json
else:
    from run_current_security_linux import IMMUTABLE_IMAGE, LABEL, Run, clean_git, command, snapshot_git, write_json


MIGRATION = 'ppt_service/deploy/k8s/migration'
LEGACY_FIXTURE = '/src/' + MIGRATION + '/tests/pg_fixture.py'
CURRENT_FIXTURE = '/src/ppt_service/tests/ops/current_backup_probe.py'
SOURCE_DB, TARGET_DB = 'ppt_p1_source_fixture', 'ppt_p1_restore_fixture'


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


class BackupRun(Run):
    def __init__(self, args, evidence, snapshot):
        super().__init__(args, evidence, snapshot)
        self.id = 'ppt-backup-' + uuid.uuid4().hex
        self.volume, self.pg = self.id + '-socket', self.id + '-postgres'
        self.results = {}

    def setup(self):
        self.docker('volume', 'create', '--label', LABEL + '=' + self.id, self.volume)
        args = self.sandbox(self.id + '-socket-init', self.args.postgres_image, uid='0:0')
        args += ['--mount', f'type=volume,source={self.volume},target=/pgsocket',
                 '--entrypoint', 'sh', self.args.postgres_image, '-c', 'chmod 1777 /pgsocket']
        command(args)
        uid, gid = self.args.postgres_uid, self.args.postgres_gid
        args = self.sandbox(self.pg, self.args.postgres_image, uid=f'{uid}:{gid}')
        args += ['-d', '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m,mode=1777',
            '--tmpfs', f'{self.args.postgres_data}:rw,nosuid,nodev,size=512m,uid={uid},gid={gid},mode=700',
            '--mount', f'type=volume,source={self.volume},target=/pgsocket',
            '--mount', f'type=volume,source={self.volume},target=/var/run/postgresql',
            '--mount', f'type=bind,source={self.snapshot},target=/src,readonly',
            '--mount', f'type=bind,source={self.evidence},target=/evidence,readonly',
            '-e', 'POSTGRES_DB=ppt_p1_maintenance', '-e', 'POSTGRES_HOST_AUTH_METHOD=trust',
            '-e', 'PGHOST=/pgsocket', self.args.postgres_image, '-c', 'listen_addresses=',
            '-c', 'unix_socket_directories=/pgsocket', '-c', 'unix_socket_permissions=0777']
        command(args)
        for _ in range(45):
            ready = self.docker('exec', self.pg, 'pg_isready', '-h', '/pgsocket', '-U', 'postgres',
                                '-d', 'ppt_p1_maintenance', check=False)
            if ready.returncode == 0:
                return
            time.sleep(1)
        raise RuntimeError('isolated_postgres_not_ready')

    def record(self, name, args, *, parse_json=True, timeout=180):
        result = command(args, check=False, timeout=timeout)
        (self.evidence / (name + '.log')).write_text(result.stdout, encoding='utf-8')
        require(result.returncode == 0, name + '_failed')
        data = json.loads(result.stdout) if parse_json else {'exit_code': 0}
        self.results[name] = data
        write_json(self.evidence / (name + '.json'), data)
        write_json(self.evidence / 'stages.json', self.results)
        return data

    def python(self, name, *script_args, metadata=False, parse_json=True):
        # Only metadata fixture/copy stages need root and these narrow caps, to
        # preserve UID 10000 files alongside the synthetic root-private subtree.
        # No host data outside the new evidence directory is mounted writable.
        uid = '0:0' if metadata else '10000:10000'
        args = self.sandbox(self.id + '-' + name, self.args.runtime_image, uid=uid)
        if metadata:
            args += ['--cap-add', 'CHOWN', '--cap-add', 'FOWNER', '--cap-add', 'DAC_OVERRIDE']
        args += ['--tmpfs', '/tmp:rw,nosuid,nodev,size=128m,mode=1777',
            '--mount', f'type=bind,source={self.snapshot},target=/src,readonly',
            '--mount', f'type=bind,source={self.evidence},target=/evidence' + ('' if metadata else ',readonly'),
            '--mount', f'type=volume,source={self.volume},target=/pgsocket',
            '-w', '/src', '-e', 'PYTHONPATH=/src', '-e', 'PYTHONDONTWRITEBYTECODE=1',
            '-e', 'HOME=/tmp', '-e', 'PPT_EXECUTION_ENABLED=false',
            '--entrypoint', 'python', self.args.runtime_image, *script_args]
        return self.record(name, args, parse_json=parse_json)

    def psql(self, name, database, *args):
        require(database in (SOURCE_DB, TARGET_DB, 'ppt_p1_maintenance'), 'fixed_database_required')
        return self.record(name, ['docker', 'exec', '--user', '0:0', self.pg, 'psql', '-X', '-q',
            '-v', 'ON_ERROR_STOP=1', '-h', '/pgsocket', '-U',
            'postgres' if database == 'ppt_p1_maintenance' else 'ppt_migrator',
            '-d', database, *args], parse_json=False)

    def bootstrap(self, *, target=False):
        database = TARGET_DB if target else SOURCE_DB
        roles = '' if target else (
            'CREATE ROLE ppt_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT PASSWORD NULL;\n'
            'CREATE ROLE ppt_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT PASSWORD NULL;\n')
        # Fixed names in a new no-network PostgreSQL; no production credentials.
        sql = roles + f'''CREATE DATABASE {database} OWNER ppt_migrator;
REVOKE ALL ON DATABASE {database} FROM PUBLIC;
GRANT CONNECT ON DATABASE {database} TO ppt_runtime;
\\connect {database}
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO ppt_migrator;
GRANT USAGE ON SCHEMA public TO ppt_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE ppt_migrator IN SCHEMA public GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO ppt_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE ppt_migrator IN SCHEMA public GRANT USAGE,SELECT ON SEQUENCES TO ppt_runtime;
'''
        name = 'target-bootstrap' if target else 'source-bootstrap'
        (self.evidence / (name + '.sql')).write_text(sql, encoding='utf-8')
        self.psql(name, 'ppt_p1_maintenance', '-f', '/evidence/' + name + '.sql')

    def inventory_transport(self):
        services = f'''[ppt_migration_source]
host=/pgsocket
dbname={SOURCE_DB}
user=ppt_migrator
[ppt_migration_target]
host=/pgsocket
dbname={TARGET_DB}
user=ppt_migrator
'''
        for name, content in [('pg_service.conf', services), ('pgpass', '')]:
            path = self.evidence / name
            path.write_text(content, encoding='utf-8')
            path.chmod(0o600)
        docker_binary = shutil.which('docker')
        require(bool(docker_binary), 'docker_binary_unavailable')
        # Only this host-side bridge can invoke Docker. Test containers receive
        # neither the Docker socket nor a network connection to a daemon.
        wrapper = self.evidence / 'psql-wrapper'
        wrapper.write_text('''#!/usr/bin/env python3
import subprocess, sys
arguments = sys.argv[1:]
index = arguments.index('--file')
script = arguments[index + 1]
del arguments[index:index + 2]
with open(script, 'rb') as stream:
    result = subprocess.run(''' + repr([docker_binary, 'exec', '-i', '--user', '0:0',
            '-e', 'PGSERVICEFILE=/evidence/pg_service.conf', '-e', 'PGPASSFILE=/evidence/pgpass', self.pg, 'psql']) + ''' + arguments, stdin=stream)
raise SystemExit(result.returncode)
''', encoding='utf-8')
        wrapper.chmod(0o700)

    def inventory(self, name, action, *, target=False, output=None, expected_hash=None):
        args = [sys.executable, str(self.snapshot / MIGRATION / 'db_inventory.py'), action,
            '--schema', 'public', '--service', 'ppt_migration_target' if target else 'ppt_migration_source',
            '--service-file', str(self.evidence / 'pg_service.conf'), '--passfile', str(self.evidence / 'pgpass'),
            '--psql', str(self.evidence / 'psql-wrapper')]
        if output:
            args += ['--output', str(self.evidence / output)]
        if expected_hash:
            args += ['--expected', str(self.evidence / 'database.json'), '--expected-sha256', expected_hash]
        result = self.record(name, args)
        require(result.get('verified') is True, 'inventory_not_verified')
        if action != 'check-empty':
            require(result.get('tables') == 15, 'schema4_exact_15_tables_required')
        return result

    def binary(self, name, args, *, source=None, destination=None):
        require(bool(source) != bool(destination), 'exactly_one_binary_stream_required')
        with (self.evidence / (name + '.log')).open('xb') as errors:
            if destination:
                with destination.open('xb') as output:
                    result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=output, stderr=errors, timeout=180)
            else:
                with source.open('rb') as input_stream:
                    result = subprocess.run(args, stdin=input_stream, stdout=errors, stderr=errors, timeout=180)
        require(result.returncode == 0, name + '_failed')
        self.results[name] = {'exit_code': 0}
        write_json(self.evidence / 'stages.json', self.results)

    def rehearsal(self):
        self.python('dependencies', '-c',
            'import cryptography,fastapi,httpx,jwt,multipart,psycopg,pydantic,sqlalchemy;'
            'print(\'{"required_dependencies_present":true}\')')
        self.bootstrap()
        legacy = self.python('seed-legacy', LEGACY_FIXTURE, 'seed', metadata=True)
        require(legacy.get('fixture_only') is True and legacy.get('production_accepted') is False, 'synthetic_source_required')
        current = self.python('seed-current', CURRENT_FIXTURE, 'seed')
        require(current.get('schema_revision') == 4 and current.get('tables') == 15, 'current_schema_required')
        require(current.get('review_records') == 2 and current.get('agent_outbox_rows') == 1, 'current_rows_required')
        self.psql('grant-source', SOURCE_DB, '-v', 'runtime=ppt_runtime', '-f', '/src/ppt_service/deploy/k8s/grant-runtime.sql')
        source_before = self.python('source-quiescent-before', CURRENT_FIXTURE, 'verify-source')
        self.inventory_transport()
        captured = self.inventory('database-capture', 'capture', output='database.json')
        db_hash = captured['manifest_sha256']
        files = self.python('files-capture', '/src/' + MIGRATION + '/snapshot.py', 'inventory',
                            '--root', '/evidence/source-data', '--manifest', '/evidence/files.json', metadata=True)
        file_hash = files['manifest_sha256']
        self.python('files-backup', '/src/' + MIGRATION + '/snapshot.py', 'copy', '--source', '/evidence/source-data',
            '--destination', '/evidence/backup-data', '--manifest', '/evidence/files.json',
            '--manifest-sha256', file_hash, '--ownership', 'preserve', metadata=True)
        dump = self.evidence / 'database.dump'
        self.binary('pg-dump', ['docker', 'exec', '--user', '0:0', self.pg, 'pg_dump', '-h', '/pgsocket',
            '-U', 'ppt_migrator', '-d', SOURCE_DB, '-Fc', '--schema=public', '--no-owner', '--no-privileges'], destination=dump)
        dump_hash = sha256(dump)
        write_json(self.evidence / 'digests.json', {'database_manifest_sha256': db_hash,
            'files_manifest_sha256': file_hash, 'database_dump_sha256': dump_hash})
        self.inventory('source-after-dump', 'compare', expected_hash=db_hash)
        self.bootstrap(target=True)
        self.inventory('target-empty', 'check-empty', target=True)
        require(sha256(dump) == dump_hash, 'dump_digest_changed_before_restore')
        self.record('archive-list', ['docker', 'exec', '--user', '0:0', self.pg,
            'pg_restore', '--list', '/evidence/database.dump'], parse_json=False)
        lines = (self.evidence / 'archive-list.log').read_text(encoding='utf-8').splitlines(keepends=True)
        pattern = re.compile(r'^\d+;\s+\d+\s+\d+\s+SCHEMA\s+-\s+public\s+\S+\s*$')
        matches = [n for n, line in enumerate(lines) if pattern.fullmatch(line.rstrip('\n'))]
        require(len(matches) == 1, 'exactly_one_public_schema_toc_entry_required')
        lines[matches[0]] = '; Existing verified-empty schema: ' + lines[matches[0]]
        toc = self.evidence / 'restore.list'
        toc.write_text(''.join(lines), encoding='utf-8')
        write_json(self.evidence / 'toc-digests.json', {'original_toc_sha256': sha256(self.evidence / 'archive-list.log'),
                                                     'restore_toc_sha256': sha256(toc), 'omitted_create_schema_entries': 1})
        self.binary('pg-restore', ['docker', 'exec', '-i', '--user', '0:0', self.pg, 'pg_restore',
            '-h', '/pgsocket', '-U', 'ppt_migrator', '-d', TARGET_DB, '--use-list=/evidence/restore.list',
            '--exit-on-error', '--single-transaction', '--no-owner', '--no-privileges'], source=dump)
        self.inventory('target-restored-compare', 'compare', target=True,
                       output='restored-database.json', expected_hash=db_hash)
        self.python('files-restore', '/src/' + MIGRATION + '/snapshot.py', 'copy', '--source', '/evidence/backup-data',
            '--destination', '/evidence/restored-data', '--manifest', '/evidence/files.json',
            '--manifest-sha256', file_hash, '--ownership', 'preserve', metadata=True)
        self.psql('grant-target', TARGET_DB, '-v', 'runtime=ppt_runtime', '-f', '/src/ppt_service/deploy/k8s/grant-runtime.sql')
        # Verify restored triggers before any reinstallation could mask loss.
        # The definition comparison above already covers original trigger bytes;
        # behavior and least-privilege checks below must also pass.
        target = self.python('target-current-history-triggers-permissions', CURRENT_FIXTURE, 'verify-target')
        require(target.get('review_mutations_denied') == 2 and target.get('runtime_permissions_verified') is True,
                'restored_review_and_permissions_not_verified')
        require(target.get('current_records_sha256') == source_before.get('current_records_sha256'),
                'restored_current_history_fingerprint_changed')
        self.python('target-idempotent-migrator', LEGACY_FIXTURE, 'migrate-target')
        self.psql('grant-target-after-migration', TARGET_DB, '-v', 'runtime=ppt_runtime', '-f',
                  '/src/ppt_service/deploy/k8s/grant-runtime.sql')
        self.python('target-runtime-crud-denials', LEGACY_FIXTURE, 'runtime')
        self.inventory('target-final-compare', 'compare', target=True, expected_hash=db_hash)
        self.inventory('source-final-compare', 'compare', expected_hash=db_hash)
        source_after = self.python('source-quiescent-final', CURRENT_FIXTURE, 'verify-source')
        require(source_after.get('current_records_sha256') == source_before.get('current_records_sha256'),
                'source_current_history_changed')
        for name, root in [('source-files-unchanged', 'source-data'), ('backup-files-unchanged', 'backup-data'),
                           ('restored-files-verified', 'restored-data')]:
            self.python(name, '/src/' + MIGRATION + '/snapshot.py', 'verify', '--root', '/evidence/' + root,
                '--manifest', '/evidence/files.json', '--manifest-sha256', file_hash, '--ownership', 'preserve', metadata=True)
        require(sha256(dump) == dump_hash, 'dump_digest_changed_after_restore')
        return {'schema_revision': 4, 'tables': 15, 'review_records': 2, 'agent_outbox_rows': 1,
                'source_quiescent': True, 'source_database_unchanged': True, 'source_files_unchanged': True,
                'target_database_and_files_match': True, 'runtime_permissions_verified': True,
                'append_only_trigger_denials': 2, 'dump_sha256': dump_hash,
                'db_manifest_sha256': db_hash, 'files_manifest_sha256': file_hash}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--runtime-image', required=True)
    parser.add_argument('--postgres-image', required=True)
    parser.add_argument('--postgres-uid', required=True, type=int)
    parser.add_argument('--postgres-gid', required=True, type=int)
    parser.add_argument('--postgres-data', default='/var/lib/postgresql/data')
    args = parser.parse_args()
    if sys.platform != 'linux' or os.geteuid() != 0:
        parser.error('Linux_server_root_required_for_synthetic_ownership_preservation')
    if any(not IMMUTABLE_IMAGE.fullmatch(x) for x in (args.runtime_image, args.postgres_image)):
        parser.error('preexisting_immutable_image_sha256_required')
    if min(args.postgres_uid, args.postgres_gid) < 1:
        parser.error('nonroot_postgres_identity_required')
    if args.postgres_data not in ('/var/lib/postgresql/data', '/var/lib/postgresql/18/docker'):
        parser.error('unsupported_postgres_data_mount')
    source, output = args.source.resolve(strict=True), args.output.resolve(strict=False)
    if output.exists() or output.is_symlink() or source == output or source in output.parents:
        parser.error('output_must_be_new_and_outside_source')
    if any(',' in str(path) for path in (source, output)):
        parser.error('docker_mount_paths_may_not_contain_commas')
    commit = clean_git(source)
    images = []
    for ref in (args.runtime_image, args.postgres_image):
        info = json.loads(command(['docker', 'image', 'inspect', ref]).stdout)[0]
        images.append({'requested': ref, 'image_id': info['Id'], 'repo_digests': info.get('RepoDigests', []),
                       'architecture': info.get('Architecture'), 'os': info.get('Os')})
    output.mkdir(mode=0o755)
    snapshot, evidence = output / 'source', output / 'evidence'
    evidence.mkdir(mode=0o700)
    rows = snapshot_git(source, snapshot, commit)
    write_json(evidence / 'source.json', {'commit': commit, 'files': rows, 'source': 'clean Git checkout'})
    write_json(evidence / 'images.json', images)
    write_json(evidence / 'scope.json', {'fixture_only': True, 'models_started': 0,
        'source_database': SOURCE_DB, 'target_database': TARGET_DB,
        'transport': 'actual PostgreSQL Unix socket, actual pg_dump/pg_restore; Docker network none',
        'writers': 'no API/parser/controller/worker started; only sequential owned fixture operations',
        'not_proven': ['production writer-stop proof', 'production concurrent snapshot', 'CSI/PVC recovery',
                       'off-site DR', 'actual PPT export, visual review or PowerPoint editing']})
    run, result, error, cleaned = BackupRun(args, evidence, snapshot), None, None, False
    try:
        run.setup()
        result = run.rehearsal()
    except Exception as exc:
        error = type(exc).__name__ + ':' + str(exc)
    finally:
        try:
            owner = run.docker('inspect', '--format', '{{index .Config.Labels "' + LABEL + '"}}',
                               run.pg, check=False, timeout=30)
            if owner.returncode == 0 and owner.stdout.strip() == run.id:
                logs = run.docker('logs', run.pg, check=False, timeout=30)
                (evidence / 'postgres.log').write_text(logs.stdout, encoding='utf-8')
        except (OSError, subprocess.SubprocessError) as exc:
            error = error or 'postgres_log_capture_failed:' + type(exc).__name__
        finally:
            try:
                cleaned = run.cleanup()
            except Exception as exc:
                error = error or 'cleanup_failed:' + type(exc).__name__
                write_json(evidence / 'cleanup.json', {'run_id': run.id,
                    'owned_resources_removed': False, 'failures': ['cleanup_unavailable']})
    unchanged = all((snapshot / row['path']).is_file() and sha256(snapshot / row['path']) == row['sha256'] for row in rows)
    status = {'passed': result is not None and error is None and cleaned and unchanged,
        'commit': commit, 'fixture_only': True, 'model_requests': 0, 'checks': result,
        'error': error, 'git_source_unchanged': unchanged, 'owned_resources_removed': cleaned}
    write_json(evidence / 'status.json', status)
    print(json.dumps(status, ensure_ascii=False))
    return 0 if status['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
