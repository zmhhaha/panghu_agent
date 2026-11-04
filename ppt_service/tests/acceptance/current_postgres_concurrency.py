#!/usr/bin/env python3
"""Current Git HTTP/PG concurrency, replay authorization and token fencing.

Server-only, cached immutable images, isolated Unix-socket PostgreSQL. No
production URL, PVC, credentials, model, image build or dependency download.
The HTTP transport is ASGI; worker/validator boundaries are explicit fixtures.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid
import xml.etree.ElementTree as ET


LABEL = 'ppt-service.panghuer.top/pg-acceptance-owner'
IMAGE = re.compile(r'(?:[^\s@]+@)?sha256:[a-f0-9]{64}\Z')
SELECTORS = (
    'ppt_service/tests/e2e/test_postgres_concurrency.py',
    'ppt_service/tests/e2e/test_idempotency_authorization.py',
    'ppt_service/tests/e2e/test_current_multiuser.py',
    'ppt_service/tests/e2e/test_recovery.py::RecoveryTests::test_late_execution_token_cannot_mutate_next_job',
)
EXPECTED = {
    'test_duplicate_concurrent_http_requests_create_one_job_and_event',
    'test_distinct_intents_on_same_revision_have_one_winner',
    'test_concurrent_projects_enforce_per_user_queue_cap',
    'test_separate_postgres_connections_cannot_double_claim_global_slot',
    'test_plan_replay_authorizes_before_cache_and_request_hash',
    'test_confirmation_replay_authorizes_without_confirming_twice',
    'test_generation_replay_authorizes_without_reserving_quota_twice',
    'test_revision_replay_authorizes_without_creating_second_job',
    'test_cancel_replay_authorizes_without_adding_second_event',
    'test_current_review_reads_writes_and_replay_are_private_to_issuer_and_subject',
    'test_two_documents_preserve_csrf_and_conflict_then_refresh_observations',
    'test_pending_powerpoint_record_and_revocation_cannot_be_saved_by_other_owner',
    'test_stale_feedback_cannot_launch_revision_and_duplicate_cancel_stays_single',
    'test_pending_human_checks_cannot_publish_and_delete_revokes_review_records',
    'test_review_download_and_preview_reject_tampered_bytes',
    'test_quality_diagnostics_download_redacts_raw_report_and_rechecks_owner',
    'test_late_execution_token_cannot_mutate_next_job',
}


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--test-image', required=True)
    parser.add_argument('--postgres-image', required=True)
    args = parser.parse_args()
    if sys.platform != 'linux' or os.geteuid() != 0:
        parser.error('run_only_on_linux_server_as_root')
    os.umask(0o077)
    source, output = args.source.resolve(strict=True), args.output.absolute()
    if Path(__file__).resolve() != source / 'ppt_service/tests/acceptance/current_postgres_concurrency.py':
        parser.error('operator_must_use_its_own_git_checkout')
    if (output.exists() or output.is_symlink() or output.parent != Path('/root')
            or not re.fullmatch(r'ppt-current-pg-[a-z0-9-]+', output.name)
            or source == output or source in output.parents
            or any(',' in str(x) for x in (source, output))):
        parser.error('fresh_root_owned_output_outside_source_required')
    if any(path.is_symlink() for path in (source, *source.parents)):
        parser.error('source_ancestors_must_not_link')
    # Both reused operators are stdlib-only and guarded against import execution.
    ops = load(source / 'ppt_service/tests/ops/run_storage_lifecycle_linux.py', 'ppt_lifecycle_operator')
    git = load(source / 'ppt_service/tests/e2e/run_current_security_linux.py', 'ppt_security_operator')
    call, require, saved = ops.call, ops.require, ops.saved
    commit = git.clean_git(source)
    images = {}
    for image in (args.test_image, args.postgres_image):
        require(IMAGE.fullmatch(image), 'immutable_cached_image_required')
        row = json.loads(call(['docker', 'image', 'inspect', image]).stdout)[0]
        require(row['Os'] == 'linux', 'linux_image_required')
        allowed = {'/var/lib/postgresql/data'} if image == args.postgres_image else {'/opt/data'}
        require(set(row['Config'].get('Volumes') or {}) <= allowed, 'unaccounted_image_volume')
        images[image] = {'id': row['Id'], 'repo_digests': row.get('RepoDigests', []),
                         'architecture': row['Architecture'], 'declared_volumes': row['Config'].get('Volumes')}
    output.mkdir(mode=0o755)
    output.chmod(0o755)
    snapshot, evidence = output / 'source', output / 'evidence'
    evidence.mkdir(mode=0o700)
    os.chown(evidence, 10000, 10000)
    source_rows = git.snapshot_git(source, snapshot, commit)
    saved(evidence / 'source.json', {'commit': commit, 'files': source_rows,
          'application_origin': 'explicit current Git snapshot, read-only; not image-owned server modules'})
    saved(evidence / 'images.json', images)
    owner = 'ppt-pg-' + uuid.uuid4().hex
    database = 'ppt_p5_' + uuid.uuid4().hex[:20]
    volume, pg, tests = owner + '-socket', owner + '-pg', owner + '-tests'
    containers, stops = [], []
    receipt = {'schema': 'ppt-current-postgres-acceptance.v1', 'passed': False,
        'commit': commit, 'owner': owner, 'database': database, 'images': images,
        'selectors': list(SELECTORS), 'models_started': 0, 'production_resources_accessed': False,
        'production_database_accessed': False, 'transport': 'in-process ASGI HTTP; synthetic signed JWT',
        'execution_boundary': 'FakeWorker/FakeSandbox, no real model/worker execution or stop evidence',
        'application_origin': 'current committed Git read-only snapshot; cached image supplies dependencies',
        'not_proven': ['real Casdoor owner sessions/cross-owner cancellation',
            'actual Pi execution/recovery', 'PowerPoint acceptance', 'production backup/restore'],
        'started_at': time.time()}

    def inspect(kind, name):
        result = call(['docker', *(['volume'] if kind == 'volume' else []), 'inspect', name], check=False)
        if result.returncode:
            expected = ('No such volume: ' if kind == 'volume' else 'No such object: ') + name
            message = result.stderr.decode(errors='replace').strip()
            messages = {expected, 'Error: ' + expected, 'Error response from daemon: ' + expected}
            if kind == 'volume':
                messages.add('Error response from daemon: get ' + name + ': no such volume')
            require(message in messages,
                    'docker_inspect_uncertain_failure')
            call(['docker', 'info', '--format', '{{.ID}}'])
            return None
        rows = json.loads(result.stdout)
        require(len(rows) == 1, 'docker_inspect_identity_ambiguous')
        return rows[0]

    def sandbox(name, image, uid):
        containers.append(name)
        return ['docker', 'run', '--pull=never', '--name', name, '--label', LABEL + '=' + owner,
            '--network=none', '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
            '--user=' + uid, '--memory=1g', '--cpus=2', '--pids-limit=256']

    try:
        call(['docker', 'volume', 'create', '--label', LABEL + '=' + owner, volume])
        call(sandbox(owner + '-init', args.postgres_image, '0:0') + [
            '--mount', 'type=volume,source=' + volume + ',target=/pgsocket',
            '--tmpfs', '/var/lib/postgresql/data:ro,nosuid,nodev,size=1m',
            '--entrypoint', 'sh', args.postgres_image, '-c', 'chmod 1777 /pgsocket'])
        call(sandbox(pg, args.postgres_image, '70:70') + ['-d',
            '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m,mode=1777',
            '--tmpfs', '/var/lib/postgresql/data:rw,nosuid,nodev,size=768m,uid=70,gid=70,mode=700',
            '--mount', 'type=volume,source=' + volume + ',target=/pgsocket',
            '--mount', 'type=volume,source=' + volume + ',target=/var/run/postgresql',
            '-e', 'POSTGRES_USER=appuser', '-e', 'POSTGRES_DB=' + database,
            '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', args.postgres_image,
            '-c', 'listen_addresses=', '-c', 'unix_socket_directories=/pgsocket',
            '-c', 'unix_socket_permissions=0777'])
        deadline = time.monotonic() + 90
        while call(['docker', 'exec', pg, 'pg_isready', '-h', '/pgsocket', '-U', 'appuser',
                    '-d', database], check=False).returncode:
            require(time.monotonic() < deadline, 'isolated_postgres_not_ready')
            time.sleep(1)
        query = ("SELECT json_build_object('database',current_database(),'server_version_num',"
                 "current_setting('server_version_num'),'listen_addresses',current_setting('listen_addresses'),"
                 "'unix_socket_directories',current_setting('unix_socket_directories'))")
        preflight = json.loads(call(['docker', 'exec', pg, 'psql', '-h', '/pgsocket', '-U',
            'appuser', '-d', database, '-At', '-c', query]).stdout)
        require(preflight['database'] == database and int(preflight['server_version_num']) >= 160000
                and preflight['listen_addresses'] == '' and preflight['unix_socket_directories'] == '/pgsocket',
                'isolated_postgres_identity_invalid')
        receipt['postgres'] = preflight
        saved(evidence / 'postgres.json', preflight)
        command = sandbox(tests, args.test_image, '10000:10000') + [
            '--tmpfs', '/tmp:rw,nosuid,nodev,size=384m,mode=1777',
            '--tmpfs', '/opt/data:ro,nosuid,nodev,size=1m',
            '--mount', 'type=volume,source=' + volume + ',target=/pgsocket',
            '--mount', 'type=bind,source=' + str(snapshot) + ',target=/src,readonly',
            '--mount', 'type=bind,source=' + str(evidence) + ',target=/evidence',
            '-w', '/src', '-e', 'PYTHONPATH=/src', '-e', 'PYTHONDONTWRITEBYTECODE=1',
            '-e', 'HOME=/tmp', '-e', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD=1',
            '-e', 'PPT_EXECUTION_BACKEND=legacy', '-e', 'PPT_EXECUTION_ENABLED=false',
            '-e', 'PPT_E2E_TEST_ONLY=isolated-p5',
            '-e', 'PPT_E2E_DATABASE_URL=postgresql+psycopg://appuser@/' + database + '?host=/pgsocket',
            '--entrypoint', 'python', args.test_image, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
            '--junitxml=/evidence/tests.xml', *SELECTORS]
        result = call(command, timeout=600, check=False)
        (evidence / 'pytest.stdout').write_bytes(result.stdout)
        (evidence / 'pytest.stderr.private').write_bytes(result.stderr)
        cases = list(ET.parse(evidence / 'tests.xml').getroot().iter('testcase'))
        names = [case.get('name') for case in cases]
        failed = [case.get('name') for case in cases if any(case.find(k) is not None
                  for k in ('failure', 'error', 'skipped'))]
        receipt['tests'] = {'exit_code': result.returncode, 'count': len(cases),
                            'names': names, 'failed_or_skipped': failed}
        require(result.returncode == 0 and len(cases) == len(EXPECTED) and set(names) == EXPECTED
                and not failed, 'all_17_current_postgres_cases_required_without_skip')
        receipt['tests_succeeded'] = True
    except BaseException as exc:
        receipt['error'] = type(exc).__name__ + ':' + str(exc)[:240]
    finally:
        clean = []
        for name in reversed(containers):
            try:
                row = inspect('container', name)
                if row is None:
                    continue
                require(row['Config']['Labels'].get(LABEL) == owner, 'container_owner_changed')
                require(row['HostConfig']['NetworkMode'] == 'none', 'unexpected_container_network')
                require(all(m['Name'] == volume for m in row['Mounts'] if m['Type'] == 'volume'),
                        'unexpected_anonymous_volume')
                if row['State']['Running']:
                    call(['docker', 'stop', '--time=10', row['Id']], timeout=25)
                call(['docker', 'wait', row['Id']], timeout=20)
                stopped = inspect('container', row['Id'])
                require(stopped is not None and not stopped['State']['Running']
                        and stopped['State']['Pid'] == 0, 'container_stop_not_proven')
                stop = {'name': name, 'id': row['Id'], 'state': stopped['State'],
                        'network': row['HostConfig']['NetworkMode'], 'removed': False}
                stops.append(stop)
                call(['docker', 'rm', row['Id']])
                require(inspect('container', row['Id']) is None, 'container_removal_not_proven')
                stop['removed'] = True
            except Exception as exc:
                clean.append(name + ':' + type(exc).__name__ + ':' + str(exc)[:160])
        try:
            row = inspect('volume', volume)
            if row is not None:
                require(row.get('Labels', {}).get(LABEL) == owner and not clean, 'volume_cleanup_not_safe')
                call(['docker', 'volume', 'rm', volume])
                require(inspect('volume', volume) is None, 'volume_removal_not_proven')
            for command in (['docker', 'ps', '-aq'], ['docker', 'volume', 'ls', '-q']):
                result = call(command + ['--filter', 'label=' + LABEL + '=' + owner])
                require(not result.stdout.strip(), 'owned_resource_inventory_not_empty')
        except Exception as exc:
            clean.append('volume_or_inventory:' + type(exc).__name__ + ':' + str(exc)[:160])
        source_unchanged = all((snapshot / row['path']).is_file() and hashlib.sha256(
            (snapshot / row['path']).read_bytes()).hexdigest() == row['sha256'] for row in source_rows)
        receipt.update(stops=stops, cleanup_errors=clean, owned_resources_removed=not clean,
            source_unchanged=source_unchanged, finished_at=time.time(),
            passed=receipt.get('tests_succeeded') is True and not clean and source_unchanged)
        saved(evidence / 'receipt.json', receipt)
        print(json.dumps({'passed': receipt['passed'], 'receipt': str(evidence / 'receipt.json'),
                          'cleanup_errors': clean}), flush=True)
    return 0 if receipt['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
