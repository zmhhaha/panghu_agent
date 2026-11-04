"""Run current lifecycle checks in fresh network-none PostgreSQL on the server.

The source must already exist through Git. No production URL, Secret, PVC,
project ID, model, local application test, image build or dependency download.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

LABEL = 'ppt-service.panghuer.top/lifecycle-owner'
SOURCE_FILES = ('ppt_service/deploy/ops/storage_lifecycle.py',
    'ppt_service/deploy/k8s/migration/snapshot.py', 'ppt_service/server/repository.py',
    'ppt_service/server/models.py', 'ppt_service/server/migrate.py',
    'ppt_service/tests/ops/test_storage_lifecycle.py',
    'ppt_service/tests/ops/run_storage_lifecycle_linux.py')


def require(value, code):
    if not value:
        raise RuntimeError(code)


def call(args, *, timeout=120, check=True):
    result = subprocess.run(args, capture_output=True, timeout=timeout)
    require(len(result.stdout) + len(result.stderr) < 16 * 1024**2, 'operator_output_budget')
    if check:
        require(result.returncode == 0, 'operator_command_failed:' + Path(args[0]).name)
    return result


def saved(path, value):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(json.dumps(value, sort_keys=True).encode() + b'\n')
        stream.flush()
        os.fsync(stream.fileno())


def absent(kind, name, result):
    """Only exact Docker absence plus a healthy daemon proves no resource."""
    noun = 'object' if kind == 'container' else 'volume'
    message = 'No such ' + noun + ': ' + name
    allowed = {message, 'Error: ' + message, 'Error response from daemon: ' + message,
               'error: no such ' + noun + ': ' + name}
    if kind == 'volume':
        allowed.add('Error response from daemon: get ' + name + ': no such volume')
    if (result.returncode != 1 or result.stdout.strip() not in (b'', b'[]')
            or result.stderr.decode(errors='replace').strip() not in allowed):
        return False
    healthy = call(['docker', 'info', '--format', '{{.ID}}'], check=False)
    return healthy.returncode == 0 and bool(healthy.stdout.strip()) and not healthy.stderr.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--test-image', required=True)
    parser.add_argument('--postgres-image', required=True)
    args = parser.parse_args()
    require(sys.platform == 'linux' and os.geteuid() == 0, 'linux_server_operator_required')
    os.umask(0o077)
    source, output = args.source.absolute(), args.output.absolute()
    require(Path(__file__).resolve() == source / 'ppt_service/tests/ops/run_storage_lifecycle_linux.py',
            'runner_must_use_its_own_git_checkout')
    require(not output.exists() and output.parent == Path('/root')
            and re.fullmatch('ppt-lifecycle-acceptance-[a-z0-9-]+', output.name), 'new_private_output_required')
    for path in (source, *source.parents):
        require(not path.is_symlink(), 'unlinked_git_source_required')
    commit = call(['git', '-C', str(source), 'rev-parse', 'HEAD']).stdout.decode().strip()
    require(re.fullmatch('[0-9a-f]{40}', commit), 'git_commit_required')
    hashes = {}
    for name in SOURCE_FILES:
        raw = (source / name).read_bytes()
        require(raw == call(['git', '-C', str(source), 'show', commit + ':' + name]).stdout,
                'lifecycle_source_not_committed')
        hashes[name] = hashlib.sha256(raw).hexdigest()
    images = {}
    for image, allowed_volumes in ((args.test_image, {'/opt/data'}),
                                  (args.postgres_image, {'/var/lib/postgresql/data'})):
        require(re.fullmatch(r'(?:[^\s@]+@)?sha256:[0-9a-f]{64}', image), 'immutable_cached_image_required')
        value = json.loads(call(['docker', 'image', 'inspect', image]).stdout)[0]
        require(value['Os'] == 'linux', 'linux_cached_image_required')
        require(set(value['Config'].get('Volumes') or {}) <= allowed_volumes,
                'unaccounted_image_volume')
        images[image] = {'id': value['Id'],
                         'declared_volumes': value['Config'].get('Volumes')}
    output.mkdir(mode=0o700)
    owner = 'ppt-life-' + uuid.uuid4().hex
    volume, pg, tests = owner + '-socket', owner + '-pg', owner + '-tests'
    containers = []
    receipt = dict(schema='ppt-lifecycle-acceptance.v1', passed=False, owner=owner,
        source_commit=commit, source_sha256=hashes, image_ids=images,
        production_resources_accessed=False, model_requests=0, network='none',
        boundary='actual current schema4 PostgreSQL and temporary file trees; no production reclamation',
        started_at=time.time())
    try:
        call(['docker', 'volume', 'create', '--label', LABEL + '=' + owner, volume])
        init = owner + '-init'
        containers.append(init)
        call(['docker', 'run', '--name', init, '--label', LABEL + '=' + owner,
            '--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
            '--memory=64m', '--cpus=0.25', '--pids-limit=32',
            '--tmpfs', '/var/lib/postgresql/data:ro,nosuid,nodev,size=1m',
            '--mount', 'type=volume,source=' + volume + ',target=/socket',
            '--entrypoint', 'sh', args.postgres_image, '-c', 'chmod 1777 /socket'])
        containers.append(pg)
        call(['docker', 'run', '-d', '--name', pg, '--label', LABEL + '=' + owner,
            '--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
            '--user=70:70', '--memory=1g', '--cpus=1', '--pids-limit=256',
            '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m,mode=1777',
            '--tmpfs', '/var/lib/postgresql/data:rw,nosuid,nodev,size=768m,uid=70,gid=70,mode=700',
            '--mount', 'type=volume,source=' + volume + ',target=/ppt-lifecycle-socket',
            '--mount', 'type=volume,source=' + volume + ',target=/var/run/postgresql',
            '-e', 'POSTGRES_USER=appuser', '-e', 'POSTGRES_DB=postgres', '-e', 'POSTGRES_HOST_AUTH_METHOD=trust',
            args.postgres_image, '-c', 'listen_addresses=', '-c', 'unix_socket_directories=/ppt-lifecycle-socket',
            '-c', 'unix_socket_permissions=0777'])
        end = time.monotonic() + 90
        while call(['docker', 'exec', pg, 'pg_isready', '-h', '/ppt-lifecycle-socket', '-U', 'appuser',
                    '-d', 'postgres'], check=False).returncode:
            require(time.monotonic() < end, 'isolated_postgres_not_ready')
            time.sleep(1)
        containers.append(tests)
        result = call(['docker', 'run', '--name', tests, '--label', LABEL + '=' + owner,
            '--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
            '--user=10000:10000', '--memory=768m', '--cpus=1', '--pids-limit=256',
            '--tmpfs', '/tmp:rw,nosuid,nodev,size=192m,mode=1777',
            '--tmpfs', '/opt/data:ro,nosuid,nodev,size=1m',
            '--mount', 'type=volume,source=' + volume + ',target=/ppt-lifecycle-socket',
            '--mount', 'type=bind,source=' + str(source) + ',target=/src,readonly',
            '-w', '/src', '-e', 'PYTHONPATH=/src', '-e', 'PYTHONDONTWRITEBYTECODE=1',
            '-e', 'PPT_LIFECYCLE_TEST_ADMIN_URL=postgresql+psycopg://appuser@/postgres?host=/ppt-lifecycle-socket',
            '--entrypoint', 'python', args.test_image, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
            '--junitxml=/tmp/lifecycle.xml', 'ppt_service/tests/ops/test_storage_lifecycle.py'], timeout=300, check=False)
        (output / 'pytest.stdout').write_bytes(result.stdout)
        (output / 'pytest.stderr.private').write_bytes(result.stderr)
        require(result.returncode == 0, 'lifecycle_tests_failed')
        xml = call(['docker', 'cp', tests + ':/tmp/lifecycle.xml', '-']).stdout
        # docker cp emits a tar; extract its one ordinary XML member only.
        import io, tarfile
        with tarfile.open(fileobj=io.BytesIO(xml), mode='r:') as archive:
            members = archive.getmembers()
            require(len(members) == 1 and members[0].isfile() and members[0].size < 1024**2,
                    'bounded_junit_member_required')
            junit = archive.extractfile(members[0]).read()
        (output / 'lifecycle.xml').write_bytes(junit)
        suites = list(ET.fromstring(junit).iter('testsuite'))
        count = sum(int(s.attrib.get('tests', 0)) for s in suites)
        require(count >= 19 and not any(int(s.attrib.get(k, 0)) for s in suites
            for k in ('failures', 'errors', 'skipped')), 'all_lifecycle_tests_required_without_skip')
        receipt['tests_passed'] = count
        receipt['tests_succeeded'] = True
    except BaseException as error:
        receipt['error_type'] = type(error).__name__
        receipt['diagnostic'] = str(error) if type(error) is RuntimeError else 'inspect_private_evidence'
        raise
    finally:
        clean = []
        for name in reversed(containers):
            found = call(['docker', 'inspect', name], check=False)
            if found.returncode:
                if not absent('container', name, found):
                    clean.append(name + ':inspect_failure_not_absence')
                continue
            value = json.loads(found.stdout)[0]
            try:
                require(value['Config']['Labels'].get(LABEL) == owner, 'owned_container_identity_changed')
                require(value['HostConfig']['NetworkMode'] == 'none', 'unexpected_container_network')
                require(all(mount['Name'] == volume for mount in value['Mounts']
                            if mount['Type'] == 'volume'), 'unexpected_anonymous_volume')
                if value['State']['Running']:
                    call(['docker', 'stop', '--time=10', value['Id']], timeout=25)
                call(['docker', 'wait', value['Id']], timeout=20)
                stopped = json.loads(call(['docker', 'inspect', value['Id']]).stdout)[0]
                require(not stopped['State']['Running'] and stopped['State']['Pid'] == 0, 'container_stop_unproven')
                call(['docker', 'rm', value['Id']])
                require(absent('container', value['Id'], call(['docker', 'inspect', value['Id']], check=False)),
                        'exact_container_removal_not_proven')
            except Exception as error:
                clean.append(name + ':' + type(error).__name__ + ':' + str(error)[:120])
        found = call(['docker', 'volume', 'inspect', volume], check=False)
        if found.returncode and not absent('volume', volume, found):
            clean.append('volume_inspect_failure_not_absence')
        if not found.returncode:
            value = json.loads(found.stdout)[0]
            if value.get('Labels', {}).get(LABEL) == owner and not clean:
                if call(['docker', 'volume', 'rm', volume], check=False).returncode:
                    clean.append('volume_remove_failed')
                elif not absent('volume', volume, call(['docker', 'volume', 'inspect', volume], check=False)):
                    clean.append('exact_volume_removal_not_proven')
            else:
                clean.append('volume_retained_for_uncertain_cleanup')
        remaining = call(['docker', 'ps', '-aq', '--filter', 'label=' + LABEL + '=' + owner], check=False)
        volumes = call(['docker', 'volume', 'ls', '-q', '--filter', 'label=' + LABEL + '=' + owner], check=False)
        if remaining.returncode or remaining.stdout.strip() or volumes.returncode or volumes.stdout.strip():
            clean.append('owned_resource_absence_unproven')
        receipt.update(cleanup_errors=clean, owned_resources_removed=not clean,
            passed=receipt.get('tests_succeeded') is True and not clean, finished_at=time.time())
        saved(output / 'receipt.json', receipt)
        print(json.dumps({'receipt': str(output / 'receipt.json'), 'passed': receipt['passed'],
                          'cleanup_errors': clean}), flush=True)
    require(receipt['passed'], 'lifecycle_acceptance_failed')


if __name__ == '__main__':
    main()
