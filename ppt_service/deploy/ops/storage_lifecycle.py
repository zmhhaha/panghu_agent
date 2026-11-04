"""Explicit, dry-run-first PPT scratch/file reclamation; never a DB purge.

This operator has no scheduler and never expires a user's project. An owner
must first request project deletion through the normal authenticated API.
Staging cleanup excludes originals, snapshots, checkpoints and delivered files.
Only committed source on the Linux server may be used for actual execution.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text

from ppt_service.deploy.k8s.migration import snapshot
from ppt_service.server.models import (Project, Job, Asset, AgentOutbox,
                                       ExecutionSlot, Checkpoint, Version)
from ppt_service.server.repository import ACTIVE, Repository

SCHEMA = 'ppt-storage-lifecycle-plan.v1'
TERMINAL = frozenset(('succeeded', 'failed', 'cancelled', 'interrupted'))
MODES = ('staging', 'deleted-project')


def require(value, code):
    if not value:
        raise RuntimeError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def checksum(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def identifier(value):
    require(type(value) is str and str(uuid.UUID(value)) == value, 'canonical_uuid_required')
    return value


def key(value):
    require(type(value) is str and value and not value.startswith('/')
            and all(p not in ('', '.', '..') for p in value.split('/'))
            and not any(c in value for c in ('\\', ':', '\x00')), 'unsafe_lifecycle_key')
    return value


def observe_parser_absent(namespace, scope_uid):
    """GET only; an empty LIST never replaces the original stop journal."""
    require(re.fullmatch(r'[a-z0-9](?:[-a-z0-9.]{0,61}[a-z0-9])?', namespace), 'invalid_scope_namespace')
    for prefix in ('ppt-service.panghuer.top/', 'ppt.panghuer.top/'):
        result = subprocess.run(['kubectl', '--kubeconfig=/etc/kubernetes/super-admin.conf',
            '-n', namespace, 'get', 'pods', '-l', prefix + 'attempt=' + identifier(scope_uid), '-o', 'json'],
            capture_output=True, timeout=15)
        require(result.returncode == 0 and len(result.stdout) <= 8 * 1024**2,
                'parser_scope_inventory_unavailable')
        actual = json.loads(result.stdout)
        require(type(actual.get('items')) is list and not actual.get('metadata', {}).get('continue')
                and not actual['items'], 'parser_scope_resources_remain')
    return True


def parser_stop_record(root, asset, *, observe=None):
    """Read the original Kubernetes parse authority, without repairing it."""
    from ppt_service.control.staging import read_json
    observe = observe_parser_absent if observe is None else observe
    uid = identifier(asset.parse_scope_uid)
    token = identifier(asset.parse_token)
    require(uid == str(uuid.uuid5(uuid.NAMESPACE_URL, 'ppt-material:' + asset.id + ':' + token)),
            'parser_scope_binding_mismatch')
    record = read_json(Path(root), 'kubernetes-authority/' + uid + '.json')
    require(record.get('schema') == 1 and record.get('sandbox_only') is True
            and record.get('phase') == 'parse' and record.get('project_id') is None
            and record.get('identity') == dict(uid=uid,
                job_id=str(uuid.uuid5(uuid.NAMESPACE_URL, 'ppt-material-job:' + uid)), attempt=1, token=1),
            'parser_original_authority_required')
    runtime = record.get('runtime', {})
    require(record.get('metadata_prefix') in ('ppt-service.panghuer.top/', 'ppt.panghuer.top/')
            and type(runtime.get('execution_image')) is str
            and re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}', runtime['execution_image']),
            'parser_runtime_identity_required')
    pods = []
    require(type(record.get('resources')) is list, 'parser_authority_resources_invalid')
    for entry in record['resources']:
        if entry.get('resource') != 'pods':
            continue
        receipt = entry.get('stop_receipt', {})
        statuses = receipt.get('status', {}).get('containerStatuses', [])
        require(entry.get('role') == 'sandbox' and entry.get('deleted') is True
                and entry.get('finalizer_release_requested') is True and bool(entry.get('api_uid'))
                and receipt.get('api_uid') == entry['api_uid'] and receipt.get('terminated') is True
                and entry.get('execution_image') == runtime['execution_image']
                and not receipt.get('status', {}).get('initContainerStatuses')
                and not receipt.get('status', {}).get('ephemeralContainerStatuses')
                and len(statuses) == 1 and statuses[0].get('name') == 'sandbox'
                and statuses[0].get('restartCount') == 0, 'parser_positive_stop_identity_required')
        end = statuses[0].get('state', {}).get('terminated', {})
        require(type(end.get('exitCode')) is int and bool(end.get('finishedAt'))
                and bool(end.get('containerID')) and end.get('reason') not in ('ContainerStatusUnknown', 'NodeLost'),
                'parser_container_termination_unverified')
        pods.append({'name': entry['name'], 'api_uid': entry['api_uid'], 'stop_receipt': receipt})
    if not pods:
        require(record.get('launch_not_started') is True, 'parser_launch_state_unresolved')
    namespace = record.get('runtime', {}).get('namespace')
    require(type(namespace) is str and observe(namespace, uid) is True, 'parser_scope_resources_remain')
    return {'scope_uid': uid, 'parse_token': token, 'parse_attempt': asset.parse_attempt,
            'namespace': namespace, 'execution_image': runtime['execution_image'],
            'original_journal_sha256': checksum(record),
            'pods': pods, 'original_never_launched': not pods, 'live_scope_absent': True}


def database_gate(session, project_id, mode, *, root, lock=False):
    """A stop boolean is accepted only from the trusted persisted controller.

    Inconclusive legacy/unadmitted cases are refused rather than inventing
    process-stop evidence. No business row is changed by this function.
    """
    identifier(project_id)
    require(mode in MODES, 'unsupported_lifecycle_mode')
    if lock:
        require(session.bind.dialect.name == 'postgresql', 'postgresql_execute_required')
        Repository(session).admission_lock()
    slot_query = select(ExecutionSlot).order_by(ExecutionSlot.id)
    job_query = select(Job).where(Job.project_id == project_id).order_by(Job.id)
    if lock:
        slot_query, job_query = slot_query.with_for_update(), job_query.with_for_update()
    slots = session.scalars(slot_query).all()
    jobs = session.scalars(job_query).all()
    project_query = select(Project).where(Project.id == project_id)
    if lock:
        project_query = project_query.with_for_update()
    project = session.scalar(project_query)
    require(project is not None, 'project_not_found')
    require(project.active_job_id is None, 'active_project_cannot_be_reclaimed')
    require(not any(s.job_id in {j.id for j in jobs} for s in slots if s.job_id),
            'occupied_project_slot')
    require(all(j.status in TERMINAL and j.status not in ACTIVE for j in jobs), 'nonterminal_project_job')
    # Require positive trusted stop evidence even for legacy jobs with missing
    # identity: absence of an identity is not proof that nothing was launched.
    require(all(j.stopped is True for j in jobs), 'project_writers_not_proven_stopped')
    now = datetime.now(timezone.utc)
    require(all(j.lease_until is None or (j.lease_until.replace(tzinfo=timezone.utc)
                if j.lease_until.tzinfo is None else j.lease_until) <= now for j in jobs),
            'unexpired_project_job_lease')
    asset_query = select(Asset).where(Asset.project_id == project_id).order_by(Asset.id)
    out_query = select(AgentOutbox).where(AgentOutbox.job_id.in_([j.id for j in jobs])).order_by(AgentOutbox.job_id)
    if lock:
        asset_query, out_query = asset_query.with_for_update(), out_query.with_for_update()
    assets = session.scalars(asset_query).all()
    outboxes = session.scalars(out_query).all()
    require(all(a.parse_lease_until is None and a.parse_status not in ('queued', 'parsing', 'stopping')
                for a in assets), 'parser_writers_not_proven_stopped')
    # A ready/failed status alone cannot authorize physical file deletion.
    # Successful claims retain scope/token; lost legacy identity fails closed.
    parse_receipts = {a.id: parser_stop_record(root, a) for a in assets}
    require(all(o.acked is True and o.state == 'mapped' for o in outboxes), 'unresolved_agent_outbox')
    bound = {j.id: j for j in jobs}
    require(all(o.attempt == bound[o.job_id].attempt and o.fencing_token == bound[o.job_id].fencing_token
                for o in outboxes), 'agent_outbox_execution_identity_mismatch')
    if mode == 'deleted-project':
        require(project.deletion_requested is True and project.cleanup_ready is True,
                'explicit_owner_deletion_and_cleanup_ready_required')
    else:
        require(project.deletion_requested is False, 'use_explicit_deleted_project_mode')
    return {'project_id': project.id, 'revision': project.revision, 'mode': mode,
            'deletion_requested': project.deletion_requested, 'cleanup_ready': project.cleanup_ready,
            'jobs': [{'id': j.id, 'attempt': j.attempt, 'fence': j.fencing_token,
                      'status': j.status, 'stopped': j.stopped, 'job_uid': j.job_uid,
                      'run_id': j.run_id, 'lease_until': j.lease_until.isoformat() if j.lease_until else None}
                      for j in jobs],
            'assets': [{'id': a.id, 'status': a.parse_status, 'scope_uid': a.parse_scope_uid}
                       for a in assets],
            'parser_stop_receipts': parse_receipts,
            'outboxes': [{'job_id': o.job_id, 'attempt': o.attempt, 'fence': o.fencing_token,
                          'state': o.state, 'acked': o.acked, 'remote_job_id': o.remote_job_id} for o in outboxes]}


def tree(root):
    """Content/metadata plus physical identity; never follows links or devices."""
    entries = snapshot.inventory(root)
    require(len(entries) <= 200000, 'lifecycle_inventory_limit')
    root_fd = snapshot.open_dir(root)
    identities = {}
    try:
        def walk(fd, prefix):
            info = os.fstat(fd)
            identities[prefix] = list(snapshot.signature(info))
            for name in sorted(os.listdir(fd)):
                relative = name if prefix == '.' else prefix + '/' + name
                row = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(row.st_mode):
                    child = os.open(name, snapshot.FLAGS | os.O_DIRECTORY, dir_fd=fd)
                    try:
                        require(snapshot.signature(os.fstat(child)) == snapshot.signature(row), 'tree_identity_changed')
                        walk(child, relative)
                    finally:
                        os.close(child)
                else:
                    require(stat.S_ISREG(row.st_mode) and row.st_nlink == 1, 'ordinary_single_link_file_required')
                    identities[relative] = list(snapshot.signature(row))
        walk(root_fd, '.')
    finally:
        os.close(root_fd)
    require(set(entries) == set(identities) and snapshot.inventory(root) == entries, 'tree_changed_during_plan')
    return {'entries': entries, 'identities': identities}


def protected(session, root, project_id):
    prefix = 'projects/' + project_id + '/'
    keys = {prefix + 'assets'}
    for checkpoint in session.scalars(select(Checkpoint).where(Checkpoint.project_id == project_id)):
        keys.add(checkpoint.storage_key)
        # Current checkpoints deliberately retain the downloaded source tree;
        # it is required for lineage verification before a following Pi task.
        source = checkpoint.manifest.get('agent_source', {})
        if source.get('storage_key') is not None:
            keys.add(source['storage_key'])
    for version in session.scalars(select(Version).where(Version.project_id == project_id)):
        keys.add(version.artifact_root)
        evidence = version.manifest.get('review_evidence', {})
        if evidence.get('storage_key') is not None:
            keys.add(evidence['storage_key'])
    result = {}
    for value in sorted(keys):
        key(value)
        require(value.startswith(prefix), 'invalid_protected_storage_key')
        path = root / value
        if path.exists() or path.is_symlink():
            result[value] = tree(path)
        else:
            require(value == prefix + 'assets', 'referenced_retained_tree_missing')
    return result


def plan(session, root, project_id, mode, *, retention_days=7, observed_at=None, lock=False):
    root = Path(root).absolute()
    require(type(retention_days) is int and retention_days >= 1, 'positive_retention_days_required')
    gate = database_gate(session, project_id, mode, root=root, lock=lock)
    observed_at = time.time() if observed_at is None else observed_at
    require(type(observed_at) in (float, int) and observed_at > 0, 'invalid_observation_time')
    prefix = 'projects/' + project_id
    candidates = {}
    retained = protected(session, root, project_id) if mode == 'staging' else {}
    if mode == 'deleted-project':
        values = [prefix]
    else:
        values = [value for j in gate['jobs'] for value in (
            prefix + '/attempts/' + j['id'] + '/' + str(j['attempt']) + '-' + str(j['fence']),
            prefix + '/agent-results/' + j['id'] + '/' + str(j['fence']))]
    for value in values:
        path = root / key(value)
        if not path.exists() and not path.is_symlink():
            continue
        inventory = tree(path)
        if mode == 'staging':
            newest = max(r['mtime_ns'] for r in inventory['entries'].values()) / 1e9
            if newest > observed_at - retention_days * 86400:
                continue
            if any(value == p or value.startswith(p + '/') or p.startswith(value + '/') for p in retained):
                continue
        candidates[value] = inventory
    return {'schema': SCHEMA, 'data_root': str(root), 'database_gate': gate,
            'retention_days': retention_days, 'observed_at': observed_at,
            'candidates': candidates, 'retained': retained,
            'database_rows_deleted': False, 'automatic_project_expiry': False}


def remove_exact(root, value, expected):
    """Delete only the inventory's exact ordinary nodes via directory FDs.

    Failure can leave a partial tree; it never authorizes a broader retry.
    A new reviewed plan is required after any interrupted reclamation.
    """
    path = Path(root) / key(value)
    require(tree(path) == expected, 'candidate_changed_since_plan')
    parent, name = snapshot.open_parent(path)
    rootfd = os.open(name, snapshot.FLAGS | os.O_DIRECTORY, dir_fd=parent)
    device = os.fstat(rootfd).st_dev
    removed = []
    try:
        def erase(fd, prefix):
            expected_names = sorted(p.rsplit('/', 1)[-1] for p in expected['entries'] if p != '.'
                and (p.rsplit('/', 1)[0] if '/' in p else '.') == prefix)
            require(sorted(os.listdir(fd)) == expected_names, 'candidate_members_changed')
            original = expected['identities'][prefix]
            info = os.fstat(fd)
            # This operator changes directory mtime/ctime as its children leave.
            require((info.st_dev, info.st_ino, info.st_uid, info.st_gid) ==
                    (original[0], original[1], original[3], original[4]), 'candidate_directory_replaced')
            os.fchmod(fd, 0o700)
            for child_name in expected_names:
                relative = child_name if prefix == '.' else prefix + '/' + child_name
                row = os.stat(child_name, dir_fd=fd, follow_symlinks=False)
                require(row.st_dev == device and list(snapshot.signature(row)) == expected['identities'][relative],
                        'candidate_child_changed')
                if expected['entries'][relative]['kind'] == 'directory':
                    child = os.open(child_name, snapshot.FLAGS | os.O_DIRECTORY, dir_fd=fd)
                    try:
                        erase(child, relative)
                    finally:
                        os.close(child)
                    os.rmdir(child_name, dir_fd=fd)
                else:
                    require(stat.S_ISREG(row.st_mode) and row.st_nlink == 1, 'candidate_file_type_changed')
                    os.unlink(child_name, dir_fd=fd)
                removed.append(relative)
            os.fsync(fd)
        erase(rootfd, '.')
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        require(current.st_ino == os.fstat(rootfd).st_ino and current.st_dev == device, 'candidate_root_replaced')
        os.rmdir(name, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(rootfd)
        os.close(parent)
    return len(removed) + 1


def execute(session, document, expected_sha256):
    require(document.get('schema') == SCHEMA and checksum(document) == expected_sha256,
            'reviewed_plan_digest_required')
    gate = document['database_gate']
    current = plan(session, Path(document['data_root']), gate['project_id'], gate['mode'],
                   retention_days=document['retention_days'], observed_at=document['observed_at'], lock=True)
    require(current == document, 'database_or_tree_changed_since_plan')
    # Recheck all candidates before the first unlink, not after partial work.
    for value, expected in document['candidates'].items():
        require(tree(Path(document['data_root']) / value) == expected, 'candidate_changed_since_plan')
    count = sum(remove_exact(document['data_root'], value, expected)
                for value, expected in document['candidates'].items())
    require(protected(session, Path(document['data_root']), gate['project_id']) == document['retained']
            if gate['mode'] == 'staging' else True, 'retained_data_changed')
    return {'schema': 'ppt-storage-lifecycle-result.v1', 'plan_sha256': expected_sha256,
            'mode': gate['mode'], 'removed_nodes': count, 'database_rows_deleted': False,
            'retained_data_verified': gate['mode'] == 'staging', 'passed': True}


def main():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    snapshot.require_linux()
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, required=True)
    parser.add_argument('--project-id', required=True)
    parser.add_argument('--mode', choices=MODES, required=True)
    parser.add_argument('--retention-days', type=int, default=7)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execute-plan', type=Path)
    parser.add_argument('--plan-sha256')
    args = parser.parse_args()
    require(args.data_root.is_absolute() and args.output.is_absolute()
            and not args.output.is_relative_to(args.data_root), 'external_private_output_required')
    parent, output_name = snapshot.open_parent(args.output)
    try:
        require(os.fstat(parent).st_uid == os.geteuid() and stat.S_IMODE(os.fstat(parent).st_mode) == 0o700,
                'private_output_directory_required')
        try:
            os.stat(output_name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise RuntimeError('fresh_output_required')
    finally:
        os.close(parent)
    source = Path(__file__).resolve()
    source_root = source.parents[3]
    commit = subprocess.run(['git', '-C', str(source_root), 'rev-parse', 'HEAD'],
                            check=True, capture_output=True).stdout.decode().strip()
    require(re.fullmatch('[0-9a-f]{40}', commit), 'git_source_required')
    frozen = subprocess.run(['git', '-C', str(source_root), 'show',
        commit + ':' + source.relative_to(source_root).as_posix()], check=True, capture_output=True).stdout
    require(frozen == source.read_bytes(), 'committed_lifecycle_operator_required')
    url = os.environ['DATABASE_URL']
    require(url.startswith('postgresql'), 'postgresql_required')
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with Session(engine) as session, session.begin():
            require(session.scalar(text('SELECT max(revision) FROM api_schema_revision')) == 4,
                    'current_schema_required')
            if args.execute_plan:
                document = snapshot.read_json(args.execute_plan, args.plan_sha256)
                # The document digest is canonical, while the file digest pins
                # the operator's separately retained review copy.
                require(document['data_root'] == str(args.data_root)
                        and document['database_gate']['project_id'] == args.project_id
                        and document['database_gate']['mode'] == args.mode, 'plan_cli_scope_mismatch')
                result = execute(session, document, checksum(document))
            else:
                document = plan(session, args.data_root, args.project_id, args.mode,
                                retention_days=args.retention_days)
                result = document
        result_digest = snapshot.atomic_json(args.output, result)
        print(json.dumps({'output': str(args.output), 'sha256': result_digest,
                          'executed': args.execute_plan is not None, 'operator_commit': commit,
                          'canonical_plan_sha256': checksum(document)}))
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
