#!/usr/bin/env python3
"""Server-only Git-sourced, offline Docker acceptance runner (stdlib only).

Never supplies a production DB URL, copies source tarballs, installs packages,
contacts a model, changes firewall rules, or mounts the Docker socket in tests.
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
import xml.etree.ElementTree as ET


LABEL = 'ppt.security-acceptance.owner'
IMMUTABLE_IMAGE = re.compile(r'(?:[A-Za-z0-9][A-Za-z0-9._:/-]*@)?sha256:[a-f0-9]{64}\Z')
SECURITY_METHODS = (
    'test_real_jwt_and_forged_identity_headers',
    'test_new_authenticated_account_needs_no_invitation_and_keeps_ownership',
    'test_valid_token_from_untrusted_peer_is_rejected',
    'test_mutations_require_both_origin_and_csrf',
    'test_other_owner_cannot_read_or_mutate_project_resources',
    'test_identical_subject_from_another_valid_issuer_has_no_access',
    'test_deletion_revokes_reads_and_retains_bytes_until_writer_stop',
    'test_deleted_project_cannot_replay_cached_write_response',
    'test_sse_reconnect_orders_events_and_strips_private_payload_fields',
    'test_terminal_sse_drains_all_event_pages_before_closing',
)
SUITES = {
    'r3-existing-api': [
        'ppt_service/tests/e2e/test_security.py::SecurityHTTPTests::' + name for name in SECURITY_METHODS
    ] + [
        'ppt_service/tests/e2e/test_idempotency_authorization.py',
        'ppt_service/tests/e2e/test_sse_pagination.py',
        'ppt_service/tests/e2e/test_postgres_concurrency.py',
    ],
    'r3-current-agent-review': ['ppt_service/tests/e2e/test_current_multiuser.py'],
    'r4-offline-agent-contracts': [
        'tests/agent_service/test_binding_host.py',
        'tests/agent_service/test_kubernetes_manifest.py',
        'tests/agent_service/test_runtime_preflight.py',
        'tests/agent_service/test_runtime_ownership.py',
        'tests/agent_service/test_runtime_boundaries.py',
        'tests/agent_service/test_controller_confinement.py',
    ],
    'r2-r5-offline-ops': [
        'ppt_service/tests/ops/test_storage_readiness.py',
        'ppt_service/tests/ops/test_upload_boundaries.py',
        'ppt_service/tests/control/test_parse_failures.py',
        'ppt_service/tests/control/test_control.py::test_docx_bomb_active_external_and_plaintext',
    ],
    'platform-catalog-capacity': [
        'tests/agent_service/test_catalog_capacity.py',
        'tests/agent_service/test_capacity_overlay.py',
        'tests/agent_service/test_config_registry.py',
        'tests/agent_service/test_deployment.py',
    ],
    'r5-offline-recovery': ['ppt_service/tests/e2e/test_recovery.py'] + [
        'ppt_service/tests/control/test_agent_connector.py::' + name for name in (
            'test_prepare_rejection_before_dispatch_has_safe_error_and_retains_confirmations',
            'test_interrupted_artifact_write_resumes_download_without_resubmitting_model_job',
            'test_terminal_remote_without_matching_stop_binding_retains_slot',
            'test_remote_failure_persists_only_fixed_business_error_without_retry',
            'test_model_failure_retains_existing_confirmed_plan_and_checkpoint',
            'test_requested_cancellation_takes_precedence_over_remote_model_failure',
            'test_collector_requires_exact_stopped_attempt_before_inspecting_output',
            'test_uncertain_submission_recovers_same_outbox_request_without_duplicate_execution',
            'test_claim_request_keeps_original_absolute_deadline_and_phase_budget',
            'test_replay_after_unreached_post_keeps_same_deadline_and_request',
            'test_cancel_during_uncertain_unreached_submission_never_reposts',
            'test_cancel_after_lost_reply_recovers_and_cancels_existing_remote',
            'test_plain_accepted_dictionary_cannot_replace_independent_validator_receipt',
        )
    ],
}


def command(args, *, cwd=None, timeout=120, check=True):
    result = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError('command_failed: ' + args[0])
    return result


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def clean_git(source):
    if command(['git', 'rev-parse', '--show-toplevel'], cwd=source).stdout.strip() != str(source):
        raise RuntimeError('source_must_be_repository_root')
    if command(['git', 'status', '--porcelain=v1', '--untracked-files=all'], cwd=source).stdout.strip():
        raise RuntimeError('source_must_be_clean_committed_git_tree')
    head = command(['git', 'rev-parse', 'HEAD'], cwd=source).stdout.strip()
    if not re.fullmatch(r'[a-f0-9]{40,64}', head):
        raise RuntimeError('invalid_source_commit')
    return head


def snapshot_git(source, target, commit):
    # This is a local Git checkout, not a source archive or uncommitted overlay.
    command(['git', 'clone', '--no-hardlinks', '--no-checkout', '--', str(source), str(target)])
    command(['git', 'checkout', '--detach', commit], cwd=target)
    if clean_git(target) != commit:
        raise RuntimeError('snapshot_commit_changed')
    entries = command(['git', 'ls-tree', '-rz', 'HEAD'], cwd=target).stdout.split('\0')
    rows = []
    for entry in filter(None, entries):
        meta, name = entry.split('\t', 1)
        mode, kind, blob = meta.split()
        if mode not in ('100644', '100755') or kind != 'blob':
            raise RuntimeError('snapshot_requires_regular_tracked_files_only')
        path = target / name
        if path.is_symlink() or not path.is_file():
            raise RuntimeError('snapshot_unexpected_file_type')
        raw = path.read_bytes()
        object_hash = hashlib.sha1 if len(blob) == 40 else hashlib.sha256
        if object_hash(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() != blob:
            raise RuntimeError('snapshot_checkout_bytes_differ_from_git_blob')
        rows.append({'path': name, 'git_blob': blob, 'sha256': hashlib.sha256(raw).hexdigest()})
        path.chmod(0o555 if mode == '100755' else 0o444)
    gitdir = target / '.git'
    if gitdir.parent != target or not gitdir.is_dir() or gitdir.is_symlink():
        raise RuntimeError('snapshot_git_metadata_not_owned_directory')
    # Prevent all Git config, remotes and credentials reaching the test process.
    shutil.rmtree(gitdir)
    for folder, _, _ in os.walk(target, topdown=False):
        Path(folder).chmod(0o555)
    return rows


def junit_summary(path, code):
    if not path.is_file():
        return {'exit_code': code, 'accepted': False, 'reason': 'junit_missing'}
    root = ET.parse(path).getroot()
    cases = list(root.iter('testcase'))
    failed = [c.get('classname', '') + '::' + c.get('name', '') for c in cases
              if c.find('failure') is not None or c.find('error') is not None]
    skipped = [c.get('classname', '') + '::' + c.get('name', '') for c in cases if c.find('skipped') is not None]
    return {'exit_code': code, 'tests': len(cases), 'failures': failed, 'skipped': skipped,
            'accepted': code == 0 and bool(cases) and not failed and not skipped}


class Run:
    def __init__(self, args, evidence, snapshot):
        self.args, self.evidence, self.snapshot = args, evidence, snapshot
        self.id = 'ppt-security-' + uuid.uuid4().hex
        self.volume = self.id + '-socket'
        self.pg = self.id + '-postgres'
        self.containers = []

    def docker(self, *args, **kwargs):
        return command(['docker', *args], **kwargs)

    def sandbox(self, name, image, *, uid='10000:10000'):
        self.containers.append(name)
        return ['docker', 'run', '--pull=never', '--name', name,
            '--label', LABEL + '=' + self.id, '--network', 'none', '--user', uid,
            '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--pids-limit', '128', '--memory', '1024m', '--cpus', '2']

    def setup(self):
        self.docker('volume', 'create', '--label', LABEL + '=' + self.id, self.volume)
        helper = self.id + '-socket-init'
        args = self.sandbox(helper, self.args.postgres_image, uid='0:0')
        args += ['--mount', f'type=volume,source={self.volume},target=/pgsocket',
                 '--entrypoint', 'sh', self.args.postgres_image, '-c', 'chmod 1777 /pgsocket']
        command(args)
        uid, gid = self.args.postgres_uid, self.args.postgres_gid
        args = self.sandbox(self.pg, self.args.postgres_image, uid=f'{uid}:{gid}')
        args += ['-d', '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m,mode=1777',
            '--tmpfs', f'{self.args.postgres_data}:rw,nosuid,nodev,size=512m,uid={uid},gid={gid},mode=700',
            '--mount', f'type=volume,source={self.volume},target=/pgsocket',
            '--mount', f'type=volume,source={self.volume},target=/var/run/postgresql',
            '-e', 'POSTGRES_DB=ppt_p5_security', '-e', 'POSTGRES_HOST_AUTH_METHOD=trust',
            '-e', 'PGHOST=/pgsocket', self.args.postgres_image,
            '-c', 'listen_addresses=', '-c', 'unix_socket_directories=/pgsocket',
            '-c', 'unix_socket_permissions=0777']
        command(args)
        for _ in range(45):
            ready = self.docker('exec', self.pg, 'pg_isready', '-h', '/pgsocket', '-U', 'postgres',
                                '-d', 'ppt_p5_security', check=False)
            if ready.returncode == 0:
                return
            time.sleep(1)
        raise RuntimeError('isolated_postgres_not_ready')

    def python_args(self, name):
        args = self.sandbox(self.id + '-' + name, self.args.runtime_image)
        args += ['--tmpfs', '/tmp:rw,nosuid,nodev,size=512m,mode=1777',
            '--mount', f'type=bind,source={self.snapshot},target=/src,readonly',
            '--mount', f'type=bind,source={self.evidence},target=/evidence',
            '--mount', f'type=volume,source={self.volume},target=/pgsocket',
            '-w', '/src', '-e', 'PYTHONPATH=/src', '-e', 'PYTHONDONTWRITEBYTECODE=1',
            '-e', 'HOME=/tmp', '-e', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD=1',
            '-e', 'PPT_EXECUTION_BACKEND=legacy', '-e', 'PPT_EXECUTION_ENABLED=false',
            '-e', 'PPT_E2E_TEST_ONLY=isolated-p5',
            '-e', 'PPT_E2E_DATABASE_URL=postgresql+psycopg://postgres@/ppt_p5_security?host=/pgsocket',
            '--entrypoint', 'python', self.args.runtime_image]
        return args

    def tests(self):
        preflight = self.python_args('dependencies') + ['-c',
            'import cryptography,fastapi,httpx,jwt,multipart,psycopg,pydantic,pytest,sqlalchemy,requests;'
            'import ppt_service,agent_service;print("required_offline_dependencies_present")']
        result = command(preflight, check=False)
        (self.evidence / 'dependencies.log').write_text(result.stdout, encoding='utf-8')
        if result.returncode:
            raise RuntimeError('runtime_image_missing_required_offline_dependencies')
        results = {}
        for name, selectors in SUITES.items():
            args = self.python_args(name) + ['-m', 'pytest', *selectors, '-q', '-p', 'no:cacheprovider',
                '-p', 'ppt_service.tests.e2e.security_acceptance_plugin',
                '--basetemp=/tmp/pytest', f'--junitxml=/evidence/{name}.xml', '--strict-markers']
            result = command(args, check=False, timeout=900)
            (self.evidence / (name + '.log')).write_text(result.stdout, encoding='utf-8')
            results[name] = junit_summary(self.evidence / (name + '.xml'), result.returncode)
            write_json(self.evidence / 'suite-results.json', results)
        return results

    def cleanup(self):
        failures = []
        def attempt(*args):
            try:
                return self.docker(*args, check=False, timeout=30)
            except (OSError, subprocess.SubprocessError):
                failures.append('docker_cleanup_command_unavailable')
                return subprocess.CompletedProcess(args, 1, '')

        for name in reversed(self.containers):
            result = attempt('inspect', '--format', '{{index .Config.Labels "' + LABEL + '"}}', name)
            if result.returncode == 0:
                if result.stdout.strip() != self.id:
                    failures.append('container_owner_mismatch')
                    continue
                if attempt('rm', '-f', name).returncode:
                    failures.append('container_remove_failed')
        result = attempt('volume', 'inspect', '--format', '{{index .Labels "' + LABEL + '"}}', self.volume)
        if result.returncode == 0:
            if result.stdout.strip() != self.id:
                failures.append('volume_owner_mismatch')
            elif attempt('volume', 'rm', self.volume).returncode:
                failures.append('volume_remove_failed')
        for operation in [('ps', '-aq'), ('volume', 'ls', '-q')]:
            result = attempt(*operation, '--filter', 'label=' + LABEL + '=' + self.id)
            if result.returncode or result.stdout.strip():
                failures.append('owned_resources_remaining_or_inventory_unavailable')
        value = {'run_id': self.id, 'owned_resources_removed': not failures, 'failures': failures}
        write_json(self.evidence / 'cleanup.json', value)
        return not failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='Clean committed Git checkout, repository root')
    parser.add_argument('--output', type=Path, required=True, help='New evidence directory outside source')
    parser.add_argument('--runtime-image', required=True, help='Already present immutable Python test image')
    parser.add_argument('--postgres-image', required=True, help='Already present immutable official PostgreSQL image')
    parser.add_argument('--postgres-uid', required=True, type=int)
    parser.add_argument('--postgres-gid', required=True, type=int)
    parser.add_argument('--postgres-data', default='/var/lib/postgresql/data')
    args = parser.parse_args()
    if sys.platform != 'linux' or os.geteuid() != 0:
        parser.error('run_on_Linux_server_as_root_for_owned_evidence_permissions')
    if any(not IMMUTABLE_IMAGE.fullmatch(x) for x in (args.runtime_image, args.postgres_image)):
        parser.error('images_require_explicit_sha256_identity_and_must_already_exist')
    if min(args.postgres_uid, args.postgres_gid) < 1:
        parser.error('postgres_uid_and_gid_must_be_nonroot')
    if args.postgres_data not in ('/var/lib/postgresql/data', '/var/lib/postgresql/18/docker'):
        parser.error('unsupported_postgres_data_mount')
    source = args.source.resolve(strict=True)
    output = args.output.resolve(strict=False)
    if output.exists() or output.is_symlink() or source == output or source in output.parents:
        parser.error('output_must_be_new_and_outside_source')
    if any(',' in str(p) for p in (source, output)):
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
    os.chown(evidence, 10000, 10000)
    rows = snapshot_git(source, snapshot, commit)
    write_json(evidence / 'source.json', {'commit': commit, 'files': rows, 'source': 'clean Git checkout'})
    write_json(evidence / 'images.json', images)
    write_json(evidence / 'scope.json', {'transport': 'in-process ASGI HTTP; no live socket or browser',
        'identities': 'temporary RSA-signed JWT, synthetic issuer/users', 'data': 'new isolated PostgreSQL/tmpfs',
        'models_started': 0, 'r4_scope': 'offline contract checks, simulated Pod/mount/proc/stop boundaries',
        'r5_scope': 'real durable rows; FakeWorker/FakeSandbox and in-memory Agent-service/validator substitutes',
        'not_proven': ['Casdoor/Ingress browser sessions', 'actual Kubernetes network/UID/mount isolation',
                       'real process stop/OOM', 'PPT rendering or PowerPoint editing', 'production backup/restore']})
    run = Run(args, evidence, snapshot)
    results, error, cleaned = {}, None, False
    try:
        run.setup()
        results = run.tests()
    except Exception as exc:
        error = type(exc).__name__ + ':' + str(exc)
    finally:
        try:
            if run.pg in run.containers:
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
    unchanged = all((snapshot / row['path']).is_file() and
        hashlib.sha256((snapshot / row['path']).read_bytes()).hexdigest() == row['sha256'] for row in rows)
    passed = bool(results) and len(results) == len(SUITES) and all(x['accepted'] for x in results.values())
    status = {'passed': passed and cleaned and unchanged and error is None,
              'commit': commit, 'suite_results': results, 'error': error,
              'source_unchanged': unchanged, 'owned_resources_removed': cleaned}
    write_json(evidence / 'status.json', status)
    print(json.dumps(status, ensure_ascii=False))
    return 0 if status['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
