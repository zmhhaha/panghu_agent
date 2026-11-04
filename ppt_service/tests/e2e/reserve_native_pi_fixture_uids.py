"""Permanently append 32 fresh fixture UIDs; never free or reuse reservations.

Run only after reviewing the native fixture operator. This does not create a
Pod, launch native Pi, call a model, change an account, or enable a route.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from run_current_recovery_kubernetes import command, ordinary, read, write

ROOT = Path('/root/agent-service-deploy-20261026')
LEDGER = ROOT / 'uid-reservations.json'
COUNT = 32


def require(value, code):
    if not value:
        raise RuntimeError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def object_uid(kind, name):
    result = command(['kubectl', '--kubeconfig=/etc/kubernetes/super-admin.conf',
        '--request-timeout=20s', 'get', kind, name, '-o', 'json'])
    value = json.loads(result.stdout)
    require(value['metadata']['name'] == name and re.fullmatch('[0-9a-f-]{36}', value['metadata']['uid']),
            'cluster_object_identity_required')
    if kind == 'node':
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        require(value.get('status', {}).get('nodeInfo', {}).get('bootID') == boot,
                'reserved_node_does_not_match_local_account_and_process_scan')
    return value['metadata']['uid']


def intervals(value):
    """Read UID ranges/singletons without executing or trusting recorded checks."""
    found = []
    if type(value) is list:
        for row in value:
            found.extend(intervals(row))
    if type(value) is dict:
        if 'uid_start' in value or 'uid_count' in value:
            require(type(value.get('uid_start')) is int and type(value.get('uid_count')) is int
                    and value['uid_start'] > 0 and value['uid_count'] > 0,
                    'recorded_uid_range_invalid')
            found.append((value['uid_start'], value['uid_start'] + value['uid_count']))
        for key, row in value.items():
            if 'uid' in key.lower() and key not in {'uid_start', 'uid_count'} and type(row) is int:
                require(row >= 0, 'recorded_uid_invalid')
                found.append((row, row + 1))
            elif 'uid' in key.lower() and type(row) is list and all(type(item) is int for item in row):
                require(all(item >= 0 for item in row), 'recorded_uid_list_invalid')
                found.extend((item, item + 1) for item in row)
            elif type(row) in (dict, list):
                found.extend(intervals(row))
    return found


def account_ids():
    data = {}
    ids = set()
    for kind, field in (('passwd', 2), ('group', 2)):
        result = command(['getent', kind])
        rows = result.stdout.decode().splitlines()
        require(rows, 'account_catalog_unavailable')
        for row in rows:
            fields = row.split(':')
            require(len(fields) >= 4 and fields[field].isdecimal(), 'account_catalog_invalid')
            ids.add(int(fields[field]))
            if kind == 'passwd':
                require(fields[3].isdecimal(), 'account_primary_group_invalid')
                ids.add(int(fields[3]))
        data[kind] = {'rows': len(rows), 'sha256': sha(result.stdout)}
    return ids, data


def process_ids():
    ids, observations = set(), []
    for directory in Path('/proc').iterdir():
        if not directory.name.isdecimal():
            continue
        try:
            raw = (directory / 'status').read_bytes()
        except FileNotFoundError:
            continue
        fields = dict(line.split(':', 1) for line in raw.decode().splitlines() if ':' in line)
        values = []
        for name in ('Uid', 'Gid'):
            row = [int(value) for value in fields[name].split()]
            require(len(row) == 4 and all(value >= 0 for value in row), 'live_process_credentials_invalid')
            values.extend(row)
        groups = [int(value) for value in fields['Groups'].split()]
        require(all(value >= 0 for value in groups), 'live_process_groups_invalid')
        ids.update(values + groups)
        observations.append((directory.name, sha(raw)))
    require(observations, 'live_process_scan_empty')
    return ids, {'processes': len(observations),
                 'observations_sha256': sha(json.dumps(sorted(observations)).encode())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--node', required=True)
    parser.add_argument('--exclusive-ledger-window', action='store_true', required=True,
        help='Operator has coordinated all other ledger writers; flock only covers cooperating writers')
    args = parser.parse_args()
    require(os.name == 'posix' and os.geteuid() == 0, 'linux_root_operator_required')
    os.umask(0o077)
    source = args.source.absolute()
    own = ordinary(Path(__file__).absolute())
    require(own == source / 'ppt_service/tests/e2e/reserve_native_pi_fixture_uids.py', 'own_git_source_required')
    commit = command(['git', '-C', str(source), 'rev-parse', 'HEAD']).stdout.decode().strip()
    raw = read(own)
    require(raw == command(['git', '-C', str(source), 'show',
        commit + ':ppt_service/tests/e2e/reserve_native_pi_fixture_uids.py']).stdout,
        'uid_reserver_not_committed')
    helper = ordinary(source / 'ppt_service/tests/e2e/run_current_recovery_kubernetes.py')
    helper_raw = read(helper)
    require(helper_raw == command(['git', '-C', str(source), 'show',
        commit + ':ppt_service/tests/e2e/run_current_recovery_kubernetes.py']).stdout,
        'uid_reservation_helper_not_committed')
    output = args.output.absolute()
    require(output.parent == Path('/root') and not output.exists()
            and re.fullmatch('ppt-native-uid-[a-z0-9-]+[.]json', output.name), 'fresh_private_receipt_required')
    require(re.fullmatch('[a-z0-9][a-z0-9.-]{0,252}', args.node), 'node_required')
    root = ROOT.stat()
    require(stat.S_ISDIR(root.st_mode) and root.st_uid == 0 and not root.st_mode & 0o022,
            'root_owned_nonwritable_ledger_directory_required')
    ordinary(LEDGER, private=True)
    lock_path = ROOT / 'uid-reservations.lock'
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'r+b') as lock:
        info = os.fstat(lock.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == 0
                and not info.st_mode & 0o077, 'private_single_ledger_lock_required')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        original = read(LEDGER)
        ledger = json.loads(original)
        require(type(ledger) is list and 1 <= len(ledger) <= 10000, 'uid_ledger_shape_invalid')
        ranges = intervals(ledger)
        require(len(ranges) >= len(ledger), 'uid_ledger_ranges_incomplete')
        reviewed = []
        # Old reservations and pre-append intents also remain permanent range
        # exclusions. Their recorded booleans grant no authority to this run.
        paths = set(ROOT.glob('*uid-reservations*.json')) | set(ROOT.glob('.native-pi-uid-intent-*.json'))
        for path in sorted(paths):
            ordinary(path)
            data = read(path)
            measured = intervals(json.loads(data))
            require(measured, 'other_uid_reservation_has_no_understood_ranges')
            ranges.extend(measured)
            reviewed.append({'path': str(path), 'sha256': sha(data), 'ranges': len(measured)})
        accounts, account_evidence = account_ids()
        processes, proc_evidence = process_ids()
        used = accounts | processes
        start = max(100_000_000, max(end for _begin, end in ranges))
        start = ((start + COUNT - 1) // COUNT) * COUNT
        while any(start <= uid < start + COUNT for uid in used):
            start += COUNT
        require(65536 <= start <= 2147483616
                and all(start + COUNT <= begin or end <= start for begin, end in ranges),
                'fresh_nonoverlapping_uid_range_unavailable')
        node_uid = object_uid('node', args.node)
        cluster_uid = object_uid('namespace', 'kube-system')
        boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        run_id = os.urandom(10).hex()
        entry = dict(name='ppt-recovery-' + run_id, node=args.node, node_uid=node_uid,
            cluster_uid=cluster_uid, node_boot_id=boot_id, uid_start=start, uid_count=COUNT, reusable=False,
            purpose='ppt-native-stop-fixture-v1', source_commit=commit, created_at=time.time())
        intent = ROOT / ('.native-pi-uid-intent-' + run_id + '.json')
        write(intent, dict(entry=entry, ledger_sha256_before=sha(original), output=str(output), permanent=True))
        require(read(LEDGER) == original, 'uid_ledger_changed_under_lock')
        replacement = ROOT / ('.native-pi-uid-ledger-' + run_id + '.json')
        write(replacement, [*ledger, entry])
        require(json.loads(read(replacement))[:-1] == ledger, 'existing_uid_entries_must_not_change')
        os.replace(replacement, LEDGER)
        parent_fd = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        after = read(LEDGER)
        require(json.loads(after) == [*ledger, entry], 'appended_uid_ledger_not_durable')
        receipt = dict(schema_version=1, purpose='ppt-disposable-recovery-v1', run_id=run_id,
            ledger_path=str(LEDGER), ledger_entry_name=entry['name'], ledger_sha256_before=sha(original),
            ledger_sha256_after=sha(after), node=args.node, node_uid=node_uid, cluster_uid=cluster_uid,
            node_boot_id=boot_id,
            uid_start=start, uid_count=COUNT, reusable=False, permanent_intent=str(intent),
            source_commit=commit, source_sha256=sha(raw), fresh_scan_at=time.time(),
            helper_sha256=sha(helper_raw),
            ledger_lock_scope='cooperating flock writers only; operator coordinated exclusive ledger window',
            checks=dict(ledger_no_overlap=True, passwd_group_no_overlap=True, proc_no_overlap=True,
                        other_reservations_reviewed=True),
            account_scan=account_evidence, process_scan=proc_evidence, reviewed_reservations=reviewed)
        write(output, receipt)
        print(json.dumps({'receipt': str(output), 'uid_start': start, 'uid_count': COUNT,
            'permanent': True, 'native_started': False, 'models_called': 0}), flush=True)


if __name__ == '__main__':
    main()
