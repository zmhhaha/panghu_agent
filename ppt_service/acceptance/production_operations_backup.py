"""Private server-only PPT snapshot and independent no-network restore rehearsal.

Capture pauses only the two PPT writer Deployments after proving the service
idle. It never changes source database rows, credentials, shared PostgreSQL,
Agent services or a user Version. Restore uses a new disposable Docker database
and a new private directory. Run only committed Git source on the Linux manager.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
import uuid

from ppt_service.deploy.k8s import consolidate_database as legacy
from ppt_service.deploy.k8s import database_fingerprint as fingerprints
from ppt_service.deploy.k8s import database_transfer as transfer

K = ['kubectl', '--kubeconfig=/etc/kubernetes/super-admin.conf']
NAMESPACE = 'ppt-service'
WRITERS = ('ppt-site', 'ppt-hermes-controller')
OWNER = 'ppt-service.panghuer.top/backup-owner'
IMAGE = re.compile(r'[^\s@]+@sha256:[0-9a-f]{64}\Z')
CAPACITY_CODE = """import json,os
v=os.statvfs('/var/lib/ppt-service')
print(json.dumps(dict(total_bytes=v.f_blocks*v.f_frsize,available_bytes=v.f_bavail*v.f_frsize,
inodes_total=v.f_files,free_inodes=v.f_favail)))
"""
# ACLs/capabilities/xattrs must never be silently omitted by this logical copy.
# Filesystems needing them require a separate native snapshot procedure.
FILE_CODE = legacy.FILE_CODE.replace('info=path.lstat()',
    "info=path.lstat()\n  if os.listxattr(path,follow_symlinks=False):raise RuntimeError('extended_attributes_require_native_snapshot')")
IDLE_SQL = """BEGIN READ ONLY;
SELECT json_build_object(
 'active_jobs',(SELECT count(*) FROM jobs WHERE status IN ('queued','starting','running','collecting','validating','cancelling')),
 'active_projects',(SELECT count(*) FROM projects WHERE active_job_id IS NOT NULL),
 'busy_parses',(SELECT count(*) FROM assets WHERE (NOT removed AND parse_status='queued') OR parse_status IN ('parsing','stopping') OR parse_lease_until IS NOT NULL),
 'occupied_slots',(SELECT count(*) FROM execution_slots WHERE job_id IS NOT NULL),
 'unproven_attached_jobs',(SELECT count(*) FROM jobs WHERE NOT stopped AND (job_uid IS NOT NULL OR run_id IS NOT NULL)),
 'unacked_outboxes',(SELECT count(*) FROM ppt_agent_outbox WHERE NOT acked),
 'schema',(SELECT max(revision) FROM api_schema_revision));
COMMIT;"""


def require(value, code):
    if not value:
        raise RuntimeError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def private_directory(path, *, new=True):
    path = Path(path).absolute()
    require(path.parent == Path('/root') and re.fullmatch(r'ppt-production-(backup|restore)-[a-z0-9-]+', path.name),
            'private_root_output_name_required')
    require(not path.is_symlink(), 'linked_output_forbidden')
    if new:
        path.mkdir(mode=0o700)
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700,
            'root_private_output_required')
    return path


def save(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(canonical(value) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def command(args, *, data=None, output=None, timeout=120, check=True):
    result = subprocess.run(args, input=data, stdout=output or subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout)
    require(len(result.stderr) <= 8 * 1024 * 1024 and
            (output is not None or len(result.stdout) <= 64 * 1024 * 1024), 'command_output_budget')
    if check:
        # Never forward SQL, raw PostgreSQL errors or user file contents.
        require(result.returncode == 0, 'operator_command_failed:' + Path(args[0]).name)
    return result


def kube(*args, data=None, namespace=NAMESPACE, timeout=120, check=True):
    return command(K + ['-n', namespace, *args], data=data, timeout=timeout, check=check)


def get(kind, name, namespace=NAMESPACE):
    return json.loads(kube('get', kind, name, '-o', 'json', namespace=namespace).stdout)


def source_psql(sql, database='ppt_service'):
    require(database in ('ppt_service', 'postgres'), 'fixed_source_database_required')
    return kube('exec', '-i', 'postgres-0', '--', 'psql', '-X', '--no-password', '-qAt',
                '-v', 'ON_ERROR_STOP=1', '-U', 'appuser', '-d', database,
                data=sql.encode(), namespace='data').stdout.decode()


def validate_capacity(value):
    """Keep unreported inode accounting distinct from measured headroom."""
    require(type(value) is dict and set(value) == {'total_bytes', 'available_bytes', 'inodes_total', 'free_inodes'}
            and all(type(value[k]) is int and value[k] >= 0 for k in ('total_bytes', 'available_bytes'))
            and all(type(value[k]) is int and value[k] >= -1 for k in ('inodes_total', 'free_inodes')),
            'production_pvc_capacity_measurement_invalid')
    require(value['total_bytes'] > 0 and value['available_bytes'] <= value['total_bytes']
            and value['available_bytes'] > 128 * 1024**2, 'production_pvc_capacity_below_capture_floor')
    # CephFS reports the number of existing objects as f_files, and -1 for
    # f_ffree/f_favail when no finite inode ceiling is available. Preserve the
    # raw sentinel; treating it as zero falsely turns "unknown" into "full".
    reported = value['inodes_total'] > 0 and value['free_inodes'] >= 0
    require(not reported or (100 < value['free_inodes'] <= value['inodes_total']),
            'production_pvc_inodes_below_capture_floor')
    return dict(value, inode_capacity_status='measured' if reported else 'not_reported',
                inode_headroom_verified=reported)


def capacity_preflight():
    # Discover byte/inode measurement failures before touching writer replicas.
    raw = kube('exec', 'deployment/ppt-site', '-c', 'site', '--', '/usr/local/bin/python', '-c', CAPACITY_CODE)
    return validate_capacity(json.loads(raw.stdout))


def idle():
    actual = json.loads(source_psql(IDLE_SQL))
    require(actual == dict(active_jobs=0, active_projects=0, busy_parses=0, occupied_slots=0,
                           unproven_attached_jobs=0, unacked_outboxes=0, schema=4), 'production_ppt_not_idle')
    return actual


def source_proof():
    root = Path(__file__).resolve().parents[2]
    commit = command(['git', '-C', str(root), 'rev-parse', 'HEAD']).stdout.decode().strip()
    require(re.fullmatch('[0-9a-f]{40}', commit), 'git_commit_required')
    files = (Path(__file__).resolve(), Path(legacy.__file__).resolve(),
             Path(fingerprints.__file__).resolve(), Path(transfer.__file__).resolve())
    for path in files:
        frozen = command(['git', '-C', str(root), 'show', commit + ':' + path.relative_to(root).as_posix()]).stdout
        require(frozen == path.read_bytes(), 'operator_source_not_committed')
    return {'commit': commit, 'files': {p.relative_to(root).as_posix(): digest(p) for p in files}}


def cas_spec(kind, expected, desired):
    name = expected['metadata']['name']
    current = get(kind, name)
    require(current['metadata']['uid'] == expected['metadata']['uid'] and current['spec'] == expected['spec'],
            'concurrent_resource_spec_change')
    patch = [{'op': 'test', 'path': '/metadata/uid', 'value': current['metadata']['uid']},
             {'op': 'test', 'path': '/metadata/resourceVersion', 'value': current['metadata']['resourceVersion']},
             {'op': 'replace', 'path': '/spec', 'value': desired}]
    kube('patch', kind, name, '--type=json', '-p', json.dumps(patch))
    result = get(kind, name)
    require(result['spec'] == desired, 'unexpected_resource_defaulting')
    return result


def delete_owned(kind, name, uid, run_id):
    raw = kube('get', kind, name, '--ignore-not-found', '-o', 'json').stdout
    if not raw.strip():
        return False
    value = json.loads(raw)
    # A create response may be lost after the apiserver accepted the resource.
    # Its random run label and reserved name recover that exact creation only.
    require((uid is None or value['metadata']['uid'] == uid) and value['metadata'].get('labels', {}).get(OWNER) == run_id,
            'cleanup_resource_owner_changed')
    uid = value['metadata']['uid']
    group = 'networking.k8s.io/v1' if kind == 'networkpolicy' else 'v1'
    resource = 'networkpolicies' if kind == 'networkpolicy' else ('configmaps' if kind == 'configmap' else 'pods')
    prefix = '/apis/' if kind == 'networkpolicy' else '/api/'
    url = prefix + group + '/namespaces/' + NAMESPACE + '/' + resource + '/' + name
    kube('delete', '--raw', url, '-f', '-', data=canonical({'apiVersion': 'v1', 'kind': 'DeleteOptions',
         'preconditions': {'uid': uid}}))
    return True


def preserve():
    return {'shared_postgres': {k: get('statefulset', 'postgres', 'data')[k] for k in ('spec',)},
            'claims': {name: {'uid': get('pvc', name)['metadata']['uid'], 'spec': get('pvc', name)['spec']}
                       for name in ('ppt-data', 'ppt-postgres-data')}}


def capture(output):
    output = private_directory(output)
    run_id = uuid.uuid4().hex
    receipt = {'format': 'ppt-production-backup-v1', 'passed': False, 'run_id': run_id,
               'source': source_proof(), 'source_rows_modified': False, 'model_requests': 0,
               'started_at': time.time(), 'writer_specs_restored': False, 'lock_released': False}
    stopped, original, owned = {}, {}, []
    captured = False
    try:
        idle()
        receipt['capacity_before_writer_drain'] = capacity_preflight()
        save(output / 'capacity-preflight.json', receipt['capacity_before_writer_drain'])
        before_preserved = preserve()
        save(output / 'preserved-before.json', before_preserved)
        lock = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'ppt-deploy-lock',
                'namespace': NAMESPACE, 'labels': {OWNER: run_id}}, 'data': {'token': run_id}}
        owned.append(('configmap', 'ppt-deploy-lock', None))
        locked = json.loads(kube('create', '-f', '-', '-o', 'json', data=canonical(lock)).stdout)
        owned[-1] = ('configmap', 'ppt-deploy-lock', locked['metadata']['uid'])
        for name in WRITERS:
            idle()
            before = get('deployment', name)
            original[name] = before
            save(output / (name + '-before.private.json'), before)
            require(before['spec'].get('replicas') == 1 and all(IMAGE.fullmatch(c['image'])
                    for c in before['spec']['template']['spec']['containers']), 'expected_single_immutable_writer')
            desired = copy.deepcopy(before['spec'])
            desired['replicas'] = 0
            # Record the intended stopped spec before sending a mutation: even
            # a failed response after an accepted patch must enter restoration.
            stopped[name] = copy.deepcopy(before)
            stopped[name]['spec'] = desired
            stopped[name] = cas_spec('deployment', before, desired)
            selector = ','.join(k + '=' + v for k, v in before['spec']['selector']['matchLabels'].items())
            deadline = time.monotonic() + 150
            while json.loads(kube('get', 'pods', '-l', selector, '-o', 'json').stdout)['items']:
                require(time.monotonic() < deadline, 'writer_pods_did_not_stop')
                time.sleep(1)
        save(output / 'quiescent.json', idle())
        require(int(source_psql("SELECT count(*) FROM pg_stat_activity WHERE datname='ppt_service' AND pid<>pg_backend_pid();")) == 0,
                'unexpected_source_database_client')
        running = json.loads(kube('get', 'pods', '--field-selector=status.phase=Running', '-o', 'json').stdout)['items']
        require(not running, 'unexpected_running_ppt_scope')
        receipt['writers_stopped_at'] = time.time()
        site_image = next(c['image'] for c in original['ppt-site']['spec']['template']['spec']['containers'] if c['name'] == 'site')
        name = 'ppt-backup-' + run_id[:12]
        policy = {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy', 'metadata': {
            'name': name, 'namespace': NAMESPACE, 'labels': {OWNER: run_id}}, 'spec': {
            'podSelector': {'matchLabels': {OWNER: run_id}}, 'policyTypes': ['Ingress', 'Egress'], 'ingress': [], 'egress': []}}
        owned.append(('networkpolicy', name, None))
        resource = json.loads(kube('create', '-f', '-', '-o', 'json', data=canonical(policy)).stdout)
        owned[-1] = ('networkpolicy', name, resource['metadata']['uid'])
        pod = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': name, 'namespace': NAMESPACE,
            'labels': {OWNER: run_id}}, 'spec': {'restartPolicy': 'Never', 'activeDeadlineSeconds': 900,
            'automountServiceAccountToken': False, 'securityContext': {'runAsNonRoot': True, 'runAsUser': 10000,
            'runAsGroup': 10000, 'seccompProfile': {'type': 'RuntimeDefault'}}, 'containers': [{
                'name': 'backup', 'image': site_image, 'imagePullPolicy': 'IfNotPresent',
                'command': ['/usr/local/bin/python', '-c', 'import time; time.sleep(850)'],
                'securityContext': {'readOnlyRootFilesystem': True, 'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}},
                'resources': {'requests': {'cpu': '50m', 'memory': '64Mi'}, 'limits': {'cpu': '1', 'memory': '256Mi'}},
                'volumeMounts': [{'name': 'data', 'mountPath': '/var/lib/ppt-service', 'readOnly': True}]}],
            'volumes': [{'name': 'data', 'persistentVolumeClaim': {'claimName': 'ppt-data', 'readOnly': True}}]}}
        owned.append(('pod', name, None))
        resource = json.loads(kube('create', '-f', '-', '-o', 'json', data=canonical(pod)).stdout)
        owned[-1] = ('pod', name, resource['metadata']['uid'])
        kube('wait', '--for=condition=Ready', 'pod/' + name, '--timeout=150s', timeout=170)
        def index():
            return json.loads(kube('exec', name, '--', '/usr/local/bin/python', '-c', FILE_CODE, 'index', timeout=300).stdout)
        capacity = validate_capacity(json.loads(kube('exec', name, '--', '/usr/local/bin/python', '-c', CAPACITY_CODE).stdout))
        save(output / 'capacity.json', capacity)
        receipt['production_pvc_capacity'] = capacity
        files = index()
        save(output / 'files.private.json', files)
        file_bytes = sum(r.get('bytes', 0) for r in files.values())
        require(shutil.disk_usage(output).free > 3 * file_bytes + 1024**3, 'insufficient_private_backup_capacity')
        metadata = json.loads(source_psql(transfer.METADATA_SQL, 'postgres'))
        transfer.Transfer.validate_source(metadata)
        save(output / 'database-metadata.private.json', metadata)
        before = fingerprints.fingerprint(source_psql)
        save(output / 'database-fingerprint.private.json', before)
        for filename, args in [('database.dump', K + ['-n', 'data', 'exec', 'postgres-0', '--', 'pg_dump',
                '--format=custom', '--create', '--no-password', '-U', 'appuser', '--dbname=ppt_service']),
                ('files.tar', K + ['-n', NAMESPACE, 'exec', name, '--', '/usr/local/bin/python', '-c', FILE_CODE, 'archive'])]:
            fd = os.open(output / filename, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                command(args, output=stream, timeout=600)
                stream.flush()
                os.fsync(stream.fileno())
        restorable = {k: v for k, v in files.items() if v['kind'] != 'socket'}
        require(legacy.archive_index(output / 'files.tar') == restorable, 'archive_not_equal_to_frozen_pvc')
        require(index() == files and fingerprints.fingerprint(source_psql) == before and
                json.loads(source_psql(transfer.METADATA_SQL, 'postgres')) == metadata, 'quiesced_source_changed')
        require(preserve() == before_preserved, 'shared_resources_or_claims_changed')
        receipt.update(captured=True, source_unchanged=True, tables=len(before['tables']),
            file_entries=len(restorable), file_bytes=file_bytes, sockets_metadata_only=len(files) - len(restorable),
            backup_files={n: {'bytes': (output / n).stat().st_size, 'sha256': digest(output / n)}
                          for n in ('files.tar', 'database.dump', 'files.private.json', 'database-metadata.private.json', 'database-fingerprint.private.json')})
        captured = True
    except BaseException as error:
        receipt['error_type'] = type(error).__name__
        receipt['diagnostic'] = str(error) if type(error) is RuntimeError else 'inspect_private_evidence'
        raise
    finally:
        cleanup = []
        for kind, name, uid in reversed(owned):
            if kind == 'configmap':
                continue
            try:
                deleted = delete_owned(kind, name, uid, run_id)
                if kind == 'pod' and deleted:
                    kube('wait', '--for=delete', 'pod/' + name, '--timeout=90s', timeout=100)
            except Exception as error:
                cleanup.append(kind + ':' + type(error).__name__)
        for name in reversed(WRITERS):
            if name not in stopped:
                continue
            try:
                current = get('deployment', name)
                if current['metadata']['uid'] != original[name]['metadata']['uid'] or current['spec'] != original[name]['spec']:
                    cas_spec('deployment', stopped[name], original[name]['spec'])
                kube('rollout', 'status', 'deployment/' + name, '--timeout=240s', timeout=260)
            except Exception as error:
                cleanup.append(name + ':' + type(error).__name__)
        try:
            receipt['writer_specs_restored'] = all(get('deployment', n)['metadata']['uid'] == original[n]['metadata']['uid']
                and get('deployment', n)['spec'] == original[n]['spec'] for n in stopped) if stopped else True
        except Exception as error:
            cleanup.append('writer_final_check:' + type(error).__name__)
            receipt['writer_specs_restored'] = False
        receipt['writers_resumed_at'] = time.time()
        for kind, name, uid in owned:
            if kind == 'configmap':
                try:
                    delete_owned(kind, name, uid, run_id)
                    receipt['lock_released'] = True
                except Exception as error:
                    cleanup.append(kind + ':' + type(error).__name__)
        receipt.update(cleanup_errors=cleanup, passed=captured and not cleanup and receipt['writer_specs_restored']
                       and receipt['lock_released'], finished_at=time.time())
        save(output / 'receipt.json', receipt)
        print(json.dumps({'receipt': str(output / 'receipt.json'), 'passed': receipt['passed'],
                          'writer_specs_restored': receipt['writer_specs_restored']}), flush=True)
    require(receipt['passed'], 'production_capture_not_complete')


def safe_extract(archive_path, destination):
    """Restore only verified members; never traverse an archive-created symlink."""
    require(not destination.exists() and not destination.is_symlink(), 'fresh_restore_directory_required')
    rows = legacy.archive_index(archive_path)
    destination.mkdir(mode=0o700)
    directories = []
    with tarfile.open(archive_path, 'r:') as archive:
        for member in archive:
            target = destination / member.name
            require(not member.name.startswith('/') and target.relative_to(destination).as_posix() == member.name
                    and target.is_relative_to(destination) and all(not p.is_symlink() for p in target.parents),
                    'archive_symlink_parent_forbidden')
            row = rows[member.name]
            if member.isdir():
                target.mkdir(mode=0o700)
                directories.append((target, row))
            elif member.issym():
                os.symlink(member.linkname, target)
                os.chown(target, row['uid'], row['gid'], follow_symlinks=False)
            else:
                require(member.isfile() or member.islnk(), 'unsupported_restore_member')
                source = archive.extractfile(member)
                fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                with source, os.fdopen(fd, 'wb') as stream:
                    shutil.copyfileobj(source, stream, 1024 * 1024)
                require(digest(target) == row['sha256'] and target.stat().st_size == row['bytes'], 'restored_file_hash_mismatch')
                os.chown(target, row['uid'], row['gid'])
                os.chmod(target, row['mode'])
        for target, row in reversed(directories):
            os.chown(target, row['uid'], row['gid'])
            os.chmod(target, row['mode'])
    return rows


def restore(backup, output, image):
    backup, output = private_directory(backup, new=False), private_directory(output)
    require(IMAGE.fullmatch(image), 'immutable_postgres_image_required')
    captured = json.loads((backup / 'receipt.json').read_text())
    require(captured.get('format') == 'ppt-production-backup-v1' and captured.get('passed') is True,
            'successful_capture_receipt_required')
    for name, expected in captured['backup_files'].items():
        require(Path(name).name == name and not (backup / name).is_symlink() and
                digest(backup / name) == expected['sha256'], 'backup_integrity_mismatch')
    receipt = {'format': 'ppt-production-restore-v1', 'passed': False, 'source': source_proof(),
               'backup': str(backup), 'backup_receipt_sha256': digest(backup / 'receipt.json'),
               'production_database_or_files_modified': False, 'network': 'none', 'model_requests': 0}
    run_id = 'ppt-restore-' + uuid.uuid4().hex
    container = None
    try:
        inspected = json.loads(command(['docker', 'image', 'inspect', image]).stdout)[0]
        require(inspected['Os'] == 'linux', 'linux_database_image_required')
        receipt['postgres_image'] = {'requested': image, 'image_id': inspected['Id']}
        launched = command(['docker', 'run', '-d', '--name', run_id, '--label', OWNER + '=' + run_id,
            '--network', 'none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
            '--user', '70:70', '--memory=2g', '--cpus=1', '--pids-limit=256',
            '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m,mode=1777',
            '--tmpfs', '/var/run/postgresql:rw,nosuid,nodev,size=64m,uid=70,gid=70,mode=700',
            '--tmpfs', '/var/lib/postgresql/data:rw,nosuid,nodev,size=2g,uid=70,gid=70,mode=700',
            '-e', 'POSTGRES_USER=appuser', '-e', 'POSTGRES_DB=postgres', '-e', 'POSTGRES_HOST_AUTH_METHOD=trust',
            image, '-c', 'listen_addresses=', '-c', 'unix_socket_directories=/var/run/postgresql'])
        container = launched.stdout.decode().strip()
        deadline = time.monotonic() + 90
        while command(['docker', 'exec', run_id, 'pg_isready', '-U', 'appuser', '-d', 'postgres'], check=False).returncode:
            require(time.monotonic() < deadline, 'disposable_postgres_not_ready')
            time.sleep(1)
        def psql(sql, database='ppt_service', user='appuser'):
            return command(['docker', 'exec', '-i', '--user', '70:70', run_id, 'psql', '-X', '-qAt', '--no-password',
                '-v', 'ON_ERROR_STOP=1', '-U', user, '-d', database], data=sql.encode()).stdout.decode()
        metadata = json.loads((backup / 'database-metadata.private.json').read_text())
        require(json.loads(psql(transfer.METADATA_SQL, 'postgres'))['database'] is None, 'new_target_database_required')
        statements = ['BEGIN;', 'SET LOCAL standard_conforming_strings=on;']
        for role in metadata['roles']:
            require(role['name'] in transfer.ROLES and role['config'] == [] and role['valid_until'] is None,
                    'unreviewed_role_settings')
            statements.append('CREATE ROLE "' + role['name'] + '" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
                'NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT ' + str(role['connection_limit']) + ' PASSWORD NULL;')
        statements.append('COMMIT;')
        psql('\n'.join(statements), 'postgres')
        with (backup / 'database.dump').open('rb') as stream:
            restored = subprocess.run(['docker', 'exec', '-i', '--user', '70:70', run_id, 'pg_restore', '--create',
                '--exit-on-error', '--no-password', '-U', 'appuser', '--dbname=postgres'], stdin=stream,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
        require(restored.returncode == 0, 'disposable_database_restore_failed')
        expected = json.loads((backup / 'database-fingerprint.private.json').read_text())
        observed = fingerprints.fingerprint(psql)
        save(output / 'restored-database-fingerprint.private.json', observed)
        require(expected == observed, 'restored_database_data_schema_acl_history_mismatch')
        restored_metadata = json.loads(psql(transfer.METADATA_SQL, 'postgres'))
        require({k: v for k, v in metadata.items() if k != 'server_version_num'} ==
                {k: v for k, v in restored_metadata.items() if k != 'server_version_num'}, 'restored_database_role_metadata_mismatch')
        runtime = json.loads(psql(transfer.RUNTIME_SQL, user='ppt_runtime'))
        require(runtime == dict(user='ppt_runtime', schema=4, schema_create=False, revision_write=False,
            entries_read_append=True, entries_mutable=False, streams_cas=True, streams_destructive=False),
            'restored_runtime_privilege_mismatch')
        save(output / 'runtime-privileges.json', runtime)
        expected_files = safe_extract(backup / 'files.tar', output / 'restored-files')
        root = output / 'restored-files' / 'data'
        code = FILE_CODE.replace("Path('/var/lib/ppt-service')", 'Path(' + repr(str(root)) + ')')
        observed_files = json.loads(command([sys.executable, '-c', code, 'index'], timeout=300).stdout)
        require(observed_files == expected_files, 'restored_file_bytes_metadata_mismatch')
        save(output / 'restored-files.private.json', observed_files)
        receipt.update(database_equal=True, metadata_equal=True, runtime_privileges_verified=True,
            files_equal=True, tables=len(observed['tables']), file_entries=len(observed_files),
            database_fingerprint_sha256=hashlib.sha256(canonical(observed)).hexdigest(),
            source_postgres_version=metadata['server_version_num'], target_postgres_version=restored_metadata['server_version_num'])
    except BaseException as error:
        receipt.update(error_type=type(error).__name__, diagnostic=str(error) if type(error) is RuntimeError else 'inspect_private_evidence')
        raise
    finally:
        cleaned = False
        try:
            # Resolve by the reserved random name even if `docker run` created
            # the container but its response/ID was lost to an interruption.
            inspection = command(['docker', 'inspect', run_id], check=False)
            if inspection.returncode != 0:
                require(container is None, 'database_disappeared_before_cleanup')
                cleaned = True
            else:
                actual = json.loads(inspection.stdout)[0]
                require((container is None or actual['Id'] == container) and
                        actual['Config']['Labels'].get(OWNER) == run_id, 'database_cleanup_owner_changed')
                command(['docker', 'rm', '-f', run_id])
                cleaned = command(['docker', 'inspect', run_id], check=False).returncode != 0
        except Exception as error:
            receipt['cleanup_error_type'] = type(error).__name__
        receipt.update(disposable_database_removed=cleaned,
            passed=cleaned and receipt.get('database_equal') is True and receipt.get('files_equal') is True)
        save(output / 'receipt.json', receipt)
        print(json.dumps({'receipt': str(output / 'receipt.json'), 'passed': receipt['passed']}), flush=True)
    require(receipt['passed'], 'independent_restore_not_complete')


def main():
    os.umask(0o077)
    def terminate(signum, frame):
        # A repeated termination request must not interrupt writer restoration.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        raise InterruptedError('operator_termination_requested')
    signal.signal(signal.SIGTERM, terminate)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('capture', 'restore'))
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--postgres-image')
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    require(sys.platform.startswith('linux') and os.geteuid() == 0, 'linux_server_root_required')
    require(args.execute, 'explicit_execute_required')
    if args.action == 'capture':
        require(args.backup is None and args.postgres_image is None, 'capture_takes_no_restore_arguments')
        capture(args.output)
    else:
        require(args.backup is not None and args.postgres_image is not None, 'restore_arguments_required')
        restore(args.backup, args.output, args.postgres_image)


if __name__ == '__main__':
    main()
