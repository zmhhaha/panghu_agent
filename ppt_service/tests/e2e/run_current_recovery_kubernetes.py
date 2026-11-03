#!/usr/bin/env python3
"""Run real Agent control/WorkerHost recovery in a disposable server namespace.

No production Secret, database, route, host acceptance, PVC or business row is
read or modified. See CURRENT_RECOVERY_KUBERNETES.md for exact fixture limits.
Run only Git-committed bytes on the Linux cluster operator, never locally.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import sys
import time

CONTROL_IMAGE = 'arm-cluster-master:5000/agent-service-control:latest@sha256:7b8e4c4b8b6a620adb9842dbfa311b49de9be684e145e9892e4520e2308a3717'
WORKER_IMAGE = 'arm-cluster-master:5000/agent-service-pi:latest@sha256:8a3e77253c7f2f75c998b22adef7e0151ca7e2797ac5d341b782e8426dd75a21'
OWNER = 'ppt.panghuer.top/recovery-owner'
ROLE = 'ppt.panghuer.top/recovery-role'
FINALIZER = 'agent-service.panghuer.top/observed-stop'
SOURCE_NAMES = ('__init__.py', 'api.py', 'store.py', 'storage.py', 'dispatcher.py',
    'kubernetes_provider.py', 'kubernetes_client.py', 'transport.py', 'worker_api.py',
    'linux_stop.py', 'contracts.py', 'engines.py', 'artifacts.py', 'staging.py',
    'worker_entry.py', 'runtime/launcher.py', 'profiles/__init__.py', 'profiles/text.py')
BOUNDARIES = [
    'Real Agent API, PostgreSQL Store, Dispatcher, Kubernetes provider and WorkerHost; fixed synthetic Python workload.',
    'Unbound workspace-text fixture; no native Pi/Hermes, model calls, production acceptance or production route.',
    'Legacy synthetic manifest changes only entry/script mounts, owner labels and smaller resources; not production Worker entry acceptance.',
    'Preserved baseline is an actual synthetic Agent artifact, not a PPT business Version or an owner-reviewed deck.',
    'Bad output is a real WorkerHost schema/collection rejection; not a PPT renderer/quality-gate drill.',
    'No real node failure, OOM, input bind/AppArmor matrix, production PVC or offsite disaster recovery.',
]


class Failure(RuntimeError):
    pass


def require(value, code):
    if not value:
        raise Failure(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def read(path, limit=8 * 1024 * 1024):
    with Path(path).open('rb') as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, 'bounded_file_read_exceeded')
    return raw


def write(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(canonical(value) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())


def ordinary(path, private=False):
    path = Path(path).absolute()
    for component in (*reversed(path.parents), path):
        info = component.lstat()
        require(not stat.S_ISLNK(info.st_mode), 'linked_operator_path')
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == 0
        and not info.st_mode & (0o077 if private else 0o022), 'unsafe_operator_file')
    return path


def command(args, *, payload=None, timeout=30, check=True, limit=8 * 1024 * 1024):
    result = subprocess.run(args, input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=timeout)
    require(len(result.stdout) <= limit and len(result.stderr) <= limit, 'command_output_budget_exceeded')
    if check and result.returncode:
        # A fixed category avoids forwarding command/SQL/auth contents. The
        # administrator can inspect the retained owned-resource status instead.
        raise Failure('operator_command_failed:' + Path(args[0]).name)
    return result


def reserve(receipt, output):
    path = ordinary(receipt, private=True)
    value = json.loads(read(path, 16384))
    require(value.get('schema_version') == 1 and type(value['schema_version']) is int
        and value.get('purpose') == 'ppt-disposable-recovery-v1'
        and re.fullmatch(r'[0-9a-f]{20}', value.get('run_id', ''))
        and type(value.get('uid_start')) is int and type(value.get('uid_count')) is int
        and value['uid_count'] == 32 and 65536 <= value['uid_start'] <= 2147483616
        and value.get('ledger_entry_name') == 'ppt-recovery-' + value['run_id']
        and re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}', value.get('node', '')),
        'uid_reservation_receipt_invalid')
    checks = value.get('checks')
    require(type(checks) is dict and all(checks.get(key) is True for key in
        ('ledger_no_overlap', 'passwd_group_no_overlap', 'proc_no_overlap', 'other_reservations_reviewed')),
        'uid_reservation_checks_missing')
    ledger = ordinary(value.get('ledger_path', ''), private=True)
    require(str(ledger) == '/root/agent-service-deploy-20261026/uid-reservations.json', 'wrong_uid_ledger')
    ledger_bytes = read(ledger)
    require(sha(ledger_bytes) == value.get('ledger_sha256_after'), 'uid_ledger_changed')
    entries = json.loads(ledger_bytes)
    require(type(entries) is list and 1 <= len(entries) <= 10000, 'invalid_uid_ledger')
    matching = [entry for entry in entries if entry.get('name') == value['ledger_entry_name']]
    require(len(matching) == 1 and matching[0].get('reusable') is False
        and matching[0].get('node') == value['node'] and matching[0].get('uid_start') == value['uid_start']
        and matching[0].get('uid_count') == value['uid_count'], 'uid_reservation_not_in_ledger')
    for entry in entries:
        start, count = entry.get('uid_start'), entry.get('uid_count')
        require(type(start) is int and type(count) is int and start > 0 and count > 0, 'uid_ledger_range_invalid')
        if entry is matching[0]:
            continue
        require(start + count <= value['uid_start'] or value['uid_start'] + value['uid_count'] <= start,
            'uid_reservation_overlap')
    # Permanent, shared-ledger-adjacent O_EXCL marker prevents copying a receipt
    # to another output directory from silently reusing this reserved UID range.
    marker = ledger.parent / ('.ppt-recovery-uid-' + str(value['uid_start']) + '-32.consumed.json')
    write(marker, {'run_id': value['run_id'], 'uid_start': value['uid_start'], 'uid_count': 32,
        'receipt_sha256': sha(read(path)), 'output': str(output), 'consumed_at': time.time(),
        'reusable': False})
    write(output / 'uid-reservation.json', value)
    return value


def source(root):
    own = Path(__file__).resolve()
    payload = own.with_name('recovery_kubernetes_capsule.py')
    commit = command(['git', '-C', str(root), 'rev-parse', 'HEAD']).stdout.decode().strip()
    require(re.fullmatch(r'[0-9a-f]{40}', commit), 'git_commit_required')
    for path in (own, payload):
        name = path.relative_to(root).as_posix()
        frozen = command(['git', '-C', str(root), 'show', commit + ':' + name]).stdout
        require(frozen == read(path), 'runner_is_not_committed_git_source')
    found = {}
    for name in SOURCE_NAMES:
        path = root / 'agent_service' / name
        frozen = command(['git', '-C', str(root), 'show', commit + ':agent_service/' + name]).stdout
        require(frozen == read(path), 'selected_agent_source_is_dirty')
        found[name] = sha(frozen)
    return {'commit': commit, 'runner_sha256': sha(read(own)),
        'payload_sha256': sha(read(payload)), 'agent_sources': found}, read(payload)


def terminated(pod):
    if pod.get('status', {}).get('reason') in {'Unknown', 'NodeLost'}:
        return False
    for names, statuses in (('containers', 'containerStatuses'), ('initContainers', 'initContainerStatuses'),
                           ('ephemeralContainers', 'ephemeralContainerStatuses')):
        expected = [row['name'] for row in pod['spec'].get(names, [])]
        actual = pod.get('status', {}).get(statuses, [])
        if len(actual) != len(expected) or {row['name'] for row in actual} != set(expected):
            return False
        for row in actual:
            ended = row.get('state', {}).get('terminated')
            if (type(ended) is not dict or type(ended.get('exitCode')) is not int
                    or not ended.get('containerID') or not ended.get('finishedAt')
                    or row.get('restartCount') != 0 or ended.get('reason') in {'Unknown', 'NodeLost', 'ContainerStatusUnknown'}):
                return False
    return bool(pod['spec'].get('containers'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reservation-receipt', type=Path, required=True)
    parser.add_argument('--kubeconfig', type=Path, default=Path('/etc/kubernetes/super-admin.conf'))
    parser.add_argument('--control-image', default=CONTROL_IMAGE)
    parser.add_argument('--worker-image', default=WORKER_IMAGE)
    parser.add_argument('--postgres-image', required=True)
    parser.add_argument('--postgres-uid', type=int, required=True)
    parser.add_argument('--pod-cidr', action='append', default=[],
        help='Reviewed private cluster Pod CIDR; defaults to the selected node PodCIDRs')
    parser.add_argument('--control-python', default='/usr/local/bin/python')
    parser.add_argument('--worker-python', default='/opt/agent-service/.venv/bin/python')
    args = parser.parse_args()
    require(sys.platform == 'linux' and os.geteuid() == 0
        and os.environ.get('PPT_RUN_K8S_RECOVERY') == 'true', 'explicit_linux_server_operator_required')
    for value in (args.control_image, args.worker_image, args.postgres_image):
        require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}', value), 'fixed_registry_digest_required')
    require(1 <= args.postgres_uid <= 65535, 'reviewed_postgres_image_uid_required')
    for value in (args.control_python, args.worker_python):
        require(re.fullmatch(r'/(?:[A-Za-z0-9._-]+/)*python(?:3(?:\.[0-9]+)?)?', value), 'fixed_python_path_required')
    output = args.output.absolute()
    for parent in (*reversed(output.parents), output):
        require(not parent.is_symlink(), 'linked_output_directory')
    require(not output.exists() and output.parent.is_dir()
        and args.source.resolve() not in output.parents, 'fresh_external_output_required')
    provenance, code = source(args.source.resolve())
    output.mkdir(mode=0o700)
    reservation = reserve(args.reservation_receipt, output)
    namespace = 'ppt-recovery-' + reservation['run_id']
    labels = {OWNER: reservation['run_id']}
    kubectl = ['kubectl', '--kubeconfig=' + str(ordinary(args.kubeconfig))]
    namespace_uid = None
    namespace_create_issued = False
    final_status = None
    errors = []
    handled = set()
    created_policy = None
    started = time.monotonic()
    write(output / 'source.json', provenance)
    write(output / 'boundaries.json', BOUNDARIES)

    def kube(arguments, payload=None, *, check=True, timeout=30):
        return command(kubectl + arguments, payload=payload, check=check, timeout=timeout)

    def get(kind, name=None, *, ns=True, absent=False):
        args2 = (['-n', namespace] if ns else []) + ['get', kind] + ([name] if name else []) + ['-o', 'json']
        if absent:
            args2 += ['--ignore-not-found=true']
        result = kube(args2)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def obj(kind, name, **fields):
        return {'apiVersion': 'v1', 'kind': kind,
            'metadata': {'name': name, 'namespace': namespace, 'labels': labels.copy()}, **fields}

    def create(value):
        if value['kind'] != 'Secret':
            write(output / ('create-' + value['kind'].lower() + '-' + value['metadata']['name'] + '.json'), value)
        else:
            write(output / ('create-secret-' + value['metadata']['name'] + '.json'), {
                'kind': 'Secret', 'metadata': value['metadata'],
                'keys_sha256': {key: sha(base64.b64decode(data)) for key, data in value['data'].items()}})
        kube(['create', '-f', '-'], canonical(value))

    def own_namespace():
        current = get('namespace', namespace, ns=False, absent=True)
        require(current is not None and current['metadata']['uid'] == namespace_uid
            and current['metadata'].get('labels', {}).get(OWNER) == reservation['run_id'], 'owned_namespace_changed')
        return current

    def own_pod(name, uid=None, role=None):
        own_namespace()
        pod = get('pod', name)
        require(pod['metadata'].get('labels', {}).get(OWNER) == reservation['run_id']
            and (uid is None or pod['metadata']['uid'] == uid)
            and (role is None or pod['metadata'].get('labels', {}).get(ROLE) == role), 'owned_pod_identity_mismatch')
        expected = args.worker_image if pod['metadata'].get('labels', {}).get(ROLE) == 'worker' else (
            args.postgres_image if pod['metadata']['name'] == 'postgres' else args.control_image)
        require(len(pod['spec']['containers']) == 1 and pod['spec']['containers'][0]['image'] == expected,
            'owned_pod_image_changed')
        for row in pod.get('status', {}).get('containerStatuses', []):
            image_id = row.get('imageID', '')
            require(row.get('restartCount') == 0 and (image_id.endswith(expected.rsplit('@', 1)[1])
                if image_id else type(row.get('state', {}).get('waiting')) is dict),
                'actual_image_digest_or_restart_mismatch')
        return pod

    def wait(test, seconds=60):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            result = test()
            if result:
                return result
            time.sleep(.5)
        raise Failure('bounded_operator_observation_timeout')

    def delete_resource(kind, value, *, grace=None):
        metadata = value['metadata']
        body = {'apiVersion': 'v1', 'kind': 'DeleteOptions',
            'preconditions': {'uid': metadata['uid']}}
        if grace is not None:
            body['gracePeriodSeconds'] = grace
        group = '/apis/networking.k8s.io/v1' if kind == 'networkpolicies' else '/api/v1'
        path = group + '/namespaces/' + namespace + '/' + kind + '/' + metadata['name']
        kube(['delete', '--raw', path, '-f', '-'], canonical(body))

    def exec_control(mode, payload=None, *, check=True):
        own_pod('control', role='control')
        return kube(['-n', namespace, 'exec', '-i', 'control', '--', args.control_python,
            '-B', '/probe/recovery.py', mode], payload, check=check)

    def worker_local(action):
        pod = own_pod(action['pod_name'], action['pod_uid'], 'worker')
        require(pod.get('status', {}).get('podIP') == action['pod_ip'], 'original_worker_ip_changed')
        result = kube(['-n', namespace, 'exec', action['pod_name'], '--', args.worker_python,
            '-B', '/probe/recovery.py', 'probe-local'])
        value = json.loads(result.stdout)
        require(value['pod_uid'] == action['pod_uid'] and value['attempt_id'] == action['attempt_id'],
            'local_worker_control_identity_mismatch')
        return value

    def handle(action):
        nonlocal created_policy
        require(re.fullmatch(r'[0-9a-f]{32}', action.get('id', '')) and action['id'] not in handled,
            'action_identity_invalid_or_repeated')
        pod = own_pod(action['pod_name'], action['pod_uid'], 'worker')
        require(pod['metadata']['name'] == 'agent-' + action['attempt_id']
            and pod.get('status', {}).get('podIP') == action['pod_ip'], 'action_original_pod_mismatch')
        evidence = {'action_id': action['id'], 'kind': action['kind'], 'pod_uid': action['pod_uid'],
            'pod_ip': action['pod_ip'], 'passed': False}
        if action['kind'] == 'kill-worker':
            write(output / ('interruption-before-' + action['id'] + '.json'), pod)
            # Kubelet stops the container from its ancestor PID namespace. Do
            # not rely on kill(1,9) from an exec process in the same namespace.
            # This is an exact-UID API deletion with zero grace, not --force,
            # finalizer removal or a claim that the workload exited normally.
            delete_resource('pods', pod, grace=0)
            stopped = wait(lambda: (live if terminated(live := own_pod(
                action['pod_name'], action['pod_uid'], 'worker')) else None), 60)
            require(FINALIZER in stopped['metadata'].get('finalizers', []), 'stop_evidence_finalizer_lost')
            write(output / ('interruption-terminated-' + action['id'] + '.json'), stopped)
            evidence['terminated'] = stopped['status']['containerStatuses']
            evidence['method'] = 'Kubernetes exact-UID DeleteOptions, gracePeriodSeconds=0; retained finalizer'
        elif action['kind'] == 'deny-worker-network':
            evidence['local_positive_control'] = worker_local(action)
            require(evidence['local_positive_control']['active_uid_writers'], 'network_positive_workload_not_running')
            policy = get('networkpolicy', 'allow-control-worker')
            require(policy['metadata'].get('labels', {}).get(OWNER) == reservation['run_id']
                and policy['spec'] == created_policy['spec'], 'network_allow_policy_changed')
            delete_resource('networkpolicies', policy)
            wait(lambda: get('networkpolicy', 'allow-control-worker', absent=True) is None, 10)
            evidence['removed_policy_uid'] = policy['metadata']['uid']
        elif action['kind'] == 'restore-worker-network':
            observed = worker_local(action)
            require(not observed['active_uid_writers'] and observed.get('stopped', {}).get('all_writers_stopped') is True
                and observed['put_count'] == 1, 'worker_watchdog_stop_not_locally_observed')
            evidence['local_watchdog_stop'] = observed
            require(get('networkpolicy', 'allow-control-worker', absent=True) is None, 'network_policy_unexpectedly_present')
            # A fresh object with the original exact owned rules restores only
            # the isolated control-to-Worker channel; namespace default deny
            # and independent DB/Kubernetes permissions remain throughout.
            kube(['create', '-f', '-'], canonical(created_policy))
            actual = get('networkpolicy', 'allow-control-worker')
            require(actual['spec'] == created_policy['spec'], 'restored_network_policy_changed')
            evidence['restored_policy_uid'] = actual['metadata']['uid']
        else:
            raise Failure('unknown_operator_action')
        evidence['passed'] = True
        write(output / ('action-' + action['id'] + '.json'), evidence)
        exec_control('ack', canonical(evidence))
        handled.add(action['id'])

    def stop_signal(signum, frame):
        raise Failure('operator_interrupted')

    previous = {signum: signal.signal(signum, stop_signal) for signum in (signal.SIGINT, signal.SIGTERM)}
    try:
        # Read only public node/API endpoint metadata. No production Secret,
        # configuration, database or Pod namespace is inspected by this runner.
        node = json.loads(kube(['get', 'node', reservation['node'], '-o', 'json']).stdout)
        require(node['metadata'].get('labels', {}).get('kubernetes.io/hostname') == reservation['node'],
            'reviewed_node_selector_missing')
        cidrs = args.pod_cidr or node['spec'].get('podCIDRs') or [node['spec'].get('podCIDR')]
        cidrs = [str(ipaddress.ip_network(value, strict=True)) for value in cidrs]
        require(1 <= len(cidrs) <= 8 and all(ipaddress.ip_network(value).is_private
            and ipaddress.ip_network(value).prefixlen > 0 for value in cidrs), 'private_pod_cidrs_required')
        service = json.loads(kube(['-n', 'default', 'get', 'service', 'kubernetes', '-o', 'json']).stdout)
        endpoints = json.loads(kube(['-n', 'default', 'get', 'endpoints', 'kubernetes', '-o', 'json']).stdout)
        api_targets = {(service['spec']['clusterIP'], 443)}
        for subset in endpoints.get('subsets', []):
            for address in subset.get('addresses', []):
                for port in subset.get('ports', []):
                    api_targets.add((address['ip'], port['port']))
        require(1 <= len(api_targets) <= 10, 'finite_kubernetes_endpoints_required')
        api_egress = [{'to': [{'ipBlock': {'cidr': str(ipaddress.ip_network(
            address + ('/128' if ':' in address else '/32'), strict=True))}}],
            'ports': [{'protocol': 'TCP', 'port': port}]} for address, port in sorted(api_targets)]
        require(get('namespace', namespace, ns=False, absent=True) is None, 'reserved_namespace_already_exists')
        namespace_create_issued = True
        create({'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': namespace,
            'labels': {**labels, 'pod-security.kubernetes.io/enforce': 'baseline',
                'pod-security.kubernetes.io/audit': 'restricted', 'pod-security.kubernetes.io/warn': 'restricted'}}})
        namespace_uid = get('namespace', namespace, ns=False)['metadata']['uid']
        write(output / 'namespace-identity.json', {'name': namespace, 'uid': namespace_uid, 'labels': labels})
        create(obj('ResourceQuota', 'recovery-quota', spec={'hard': {
            'requests.cpu': '1', 'limits.cpu': '3', 'requests.memory': '768Mi', 'limits.memory': '2Gi',
            'pods': '8', 'secrets': '8', 'configmaps': '4', 'persistentvolumeclaims': '0', 'services': '0',
            'count/serviceaccounts': '2', 'count/networkpolicies.networking.k8s.io': '6'}}))
        default_deny = obj('NetworkPolicy', 'default-deny', spec={'podSelector': {},
            'policyTypes': ['Ingress', 'Egress'], 'ingress': [], 'egress': []})
        default_deny['apiVersion'] = 'networking.k8s.io/v1'
        create(default_deny)
        create(obj('ServiceAccount', 'recovery-control', automountServiceAccountToken=True))
        role = obj('Role', 'recovery-control', rules=[{'apiGroups': [''],
            'resources': ['pods', 'secrets', 'configmaps'], 'verbs': ['get', 'list', 'create', 'delete', 'patch']}])
        role['apiVersion'] = 'rbac.authorization.k8s.io/v1'
        create(role)
        binding = obj('RoleBinding', 'recovery-control', roleRef={'apiGroup': 'rbac.authorization.k8s.io',
            'kind': 'Role', 'name': 'recovery-control'}, subjects=[{'kind': 'ServiceAccount',
                'name': 'recovery-control', 'namespace': namespace}])
        binding['apiVersion'] = 'rbac.authorization.k8s.io/v1'
        create(binding)

        def policy_object(name, selected, **fields):
            result = obj('NetworkPolicy', name, spec={'podSelector': {'matchLabels': {ROLE: selected}}, **fields})
            result['apiVersion'] = 'networking.k8s.io/v1'
            return result

        control_core = policy_object('allow-control-core', 'control', policyTypes=['Egress'], egress=[
            {'to': [{'podSelector': {'matchLabels': {ROLE: 'database'}}}],
             'ports': [{'protocol': 'TCP', 'port': 5432}]},
            {'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}},
                     'podSelector': {'matchLabels': {'k8s-app': 'kube-dns'}}}],
             'ports': [{'protocol': 'UDP', 'port': 53}, {'protocol': 'TCP', 'port': 53}]}, *api_egress])
        create(control_core)
        create(policy_object('allow-database', 'database', policyTypes=['Ingress'], ingress=[{
            'from': [{'podSelector': {'matchLabels': {ROLE: 'control'}}}],
            'ports': [{'protocol': 'TCP', 'port': 5432}]}]))
        create(policy_object('allow-worker-ingress', 'worker', policyTypes=['Ingress'], ingress=[{
            'from': [{'podSelector': {'matchLabels': {ROLE: 'control'}}}],
            'ports': [{'protocol': 'TCP', 'port': 8090}]}]))
        created_policy = policy_object('allow-control-worker', 'control', policyTypes=['Egress'], egress=[{
            'to': [{'podSelector': {'matchLabels': {ROLE: 'worker'}}}],
            'ports': [{'protocol': 'TCP', 'port': 8090}]}])
        create(created_policy)
        create(obj('ConfigMap', 'recovery-code', immutable=True, data={'recovery.py': code.decode()}))
        password = secrets.token_urlsafe(36)
        settings = {**{key: reservation[key] for key in ('run_id', 'node', 'uid_start', 'uid_count')},
            'namespace': namespace, 'script_sha256': provenance['payload_sha256'],
            'worker_image': args.worker_image, 'worker_python': args.worker_python,
            'sources': provenance['agent_sources'], 'pod_cidrs': cidrs,
            'caller_token': secrets.token_urlsafe(48), 'pool_token': secrets.token_urlsafe(48),
            'receipt_key_hex': secrets.token_hex(48)}
        public = {key: settings[key] for key in ('run_id', 'node', 'uid_start', 'uid_count', 'namespace',
            'script_sha256', 'worker_image', 'worker_python', 'sources', 'pod_cidrs')}
        host = {'schema_version': 1, 'engine': 'pi', 'profile': {'name': 'workspace-task.v1',
            'revision': 'synthetic-recovery-20261103-v1', 'runtime_revision': 'synthetic-recovery-20261103-v1',
            'image_digest': args.worker_image.rsplit('@', 1)[1], 'enabled': True, 'max_timeout_seconds': 180}, 'model': None}
        create(obj('ConfigMap', 'recovery-host', immutable=True,
            data={'config.json': canonical(host).decode(), 'public.json': canonical(public).decode()}))
        # Separate Secret is not necessary for the database. Only its one
        # password key is mounted into the owned PostgreSQL Pod.
        secret_body = obj('Secret', 'recovery-settings', type='Opaque', immutable=True,
            data={'password': base64.b64encode(password.encode()).decode()})

        def pod(name, role_name, image, command_line=None, *, security=None, volumes=None, mounts=None, env=None):
            container = {'name': role_name, 'image': image, 'imagePullPolicy': 'IfNotPresent',
                'securityContext': security or {'runAsUser': 0, 'runAsGroup': 0, 'readOnlyRootFilesystem': True,
                    'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}},
                'env': env or [], 'volumeMounts': mounts or [],
                'resources': {'requests': {'cpu': '50m', 'memory': '128Mi'},
                    'limits': {'cpu': '500m', 'memory': '512Mi', 'ephemeral-storage': '128Mi'}}}
            if command_line:
                container['command'] = command_line
            result = obj('Pod', name, spec={'restartPolicy': 'Never', 'automountServiceAccountToken': False,
                'enableServiceLinks': False, 'hostPID': False, 'hostIPC': False, 'hostNetwork': False,
                'shareProcessNamespace': False, 'terminationGracePeriodSeconds': 10,
                'activeDeadlineSeconds': 900, 'nodeSelector': {'kubernetes.io/hostname': reservation['node']},
                'securityContext': {'seccompProfile': {'type': 'RuntimeDefault'}},
                'containers': [container], 'volumes': volumes or []})
            result['metadata']['labels'][ROLE] = role_name
            return result

        # DB Pod is created after the immutable combined settings Secret below;
        # use the known random Pod IP only once Kubernetes publishes its UID/IP.
        db = pod('postgres', 'database', args.postgres_image, security={
            'runAsUser': args.postgres_uid, 'runAsGroup': args.postgres_uid,
            'readOnlyRootFilesystem': True, 'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}},
            volumes=[{'name': 'db', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '128Mi'}},
                {'name': 'tmp', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '32Mi'}},
                {'name': 'socket', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '8Mi'}},
                {'name': 'password', 'secret': {'secretName': 'recovery-settings',
                    'items': [{'key': 'password', 'path': 'password'}], 'defaultMode': 288}}],
            mounts=[{'name': 'db', 'mountPath': '/var/lib/postgresql/data'},
                {'name': 'tmp', 'mountPath': '/tmp'}, {'name': 'socket', 'mountPath': '/var/run/postgresql'},
                {'name': 'password', 'mountPath': '/password', 'readOnly': True}],
            env=[{'name': 'POSTGRES_USER', 'value': 'ppt_recovery'},
                {'name': 'POSTGRES_DB', 'value': 'ppt_recovery'},
                {'name': 'POSTGRES_PASSWORD_FILE', 'value': '/password/password'},
                {'name': 'PGDATA', 'value': '/var/lib/postgresql/data/pgdata'}])
        db['spec']['securityContext']['fsGroup'] = args.postgres_uid
        # The configuration uses a fixed isolated Service DNS name only for
        # the initial Secret freeze. There is no Service: replace by the Pod IP
        # before the control Secret is frozen, using a second separate Secret.
        create(secret_body)
        create(db)
        db_live = wait(lambda: (live if (live := own_pod('postgres', role='database')).get('status', {}).get('podIP')
            and any(row.get('ready') for row in live.get('status', {}).get('containerStatuses', [])) else None), 120)
        wait(lambda: kube(['-n', namespace, 'exec', 'postgres', '--', 'pg_isready', '-U',
            'ppt_recovery', '-d', 'ppt_recovery'], check=False).returncode == 0, 60)
        db_ip = db_live['status']['podIP']
        settings['database_url'] = 'postgresql+psycopg://ppt_recovery:' + password + '@' + (
            '[' + db_ip + ']' if ':' in db_ip else db_ip) + ':5432/ppt_recovery'
        create(obj('Secret', 'recovery-control-settings', type='Opaque', immutable=True,
            data={'settings.json': base64.b64encode(canonical(settings)).decode()}))
        del password
        control = pod('control', 'control', args.control_image,
            [args.control_python, '-B', '/probe/recovery.py', 'coordinator'],
            volumes=[{'name': 'probe', 'configMap': {'name': 'recovery-code', 'defaultMode': 292}},
                {'name': 'settings', 'secret': {'secretName': 'recovery-control-settings', 'defaultMode': 256}},
                {'name': 'state', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '64Mi'}},
                {'name': 'tmp', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '16Mi'}}],
            mounts=[{'name': 'probe', 'mountPath': '/probe', 'readOnly': True},
                {'name': 'settings', 'mountPath': '/settings', 'readOnly': True},
                {'name': 'state', 'mountPath': '/state'}, {'name': 'tmp', 'mountPath': '/tmp'}],
            env=[{'name': 'PPT_RECOVERY_NAMESPACE', 'value': namespace},
                {'name': 'PPT_RECOVERY_SCRIPT_SHA256', 'value': provenance['payload_sha256']},
                {'name': 'PYTHONDONTWRITEBYTECODE', 'value': '1'}])
        control['spec']['automountServiceAccountToken'] = True
        control['spec']['serviceAccountName'] = 'recovery-control'
        create(control)
        del settings, secret_body
        wait(lambda: any(row.get('ready') for row in own_pod('control', role='control').get('status', {}).get('containerStatuses', [])), 120)
        while time.monotonic() - started < 1050:
            result = exec_control('status')
            final_status = json.loads(result.stdout)
            if final_status.get('action') and final_status['action']['id'] not in handled:
                handle(final_status['action'])
            if final_status.get('phase') in {'complete', 'failed'}:
                break
            time.sleep(1)
        require(final_status and final_status.get('phase') == 'complete' and final_status.get('passed') is True,
            'joint_recovery_did_not_pass')
        write(output / 'joint-recovery.json', final_status)
    except Exception as error:
        errors.append(str(error) if type(error) is Failure else type(error).__name__)
    finally:
        if final_status is not None and not (output / 'joint-recovery.json').exists():
            write(output / 'joint-recovery.json', final_status)
        cleanup = {'passed': False, 'namespace': namespace, 'namespace_uid': namespace_uid,
            'containers_terminated': [], 'errors': []}
        try:
            require(namespace_uid is not None or not namespace_create_issued,
                'namespace_creation_outcome_not_observed')
            if namespace_uid is not None:
                own_namespace()
                pods = get('pods')['items']
                for initial in pods:
                    name = initial['metadata']['name']
                    current = own_pod(name, initial['metadata']['uid'])
                    write(output / ('cleanup-before-' + name + '.json'), current)
                    logs = kube(['-n', namespace, 'logs', name, '--tail=200'], check=False)
                    write(output / ('cleanup-log-' + name + '.json'), {
                        'returncode': logs.returncode, 'stdout': logs.stdout.decode(errors='replace'),
                        'stderr_sha256': sha(logs.stderr)})
                    if not current['metadata'].get('deletionTimestamp'):
                        delete_resource('pods', current, grace=5)
                    # Worker finalizer retains positive termination evidence.
                    # Ordinary DB/control Pods have no finalizer and may be
                    # removed immediately after kubelet stop; they have no
                    # authority over any external/production resources.
                    if FINALIZER in current['metadata'].get('finalizers', []):
                        stopped = wait(lambda: (live if terminated(live := own_pod(
                            name, initial['metadata']['uid'])) else None), 60)
                        write(output / ('cleanup-terminated-' + name + '.json'), stopped)
                        cleanup['containers_terminated'].append({'pod': name,
                            'uid': stopped['metadata']['uid'], 'statuses': stopped['status']['containerStatuses']})
                        finalizers = stopped['metadata'].get('finalizers', [])
                        require(finalizers == [FINALIZER], 'unexpected_owned_worker_finalizers')
                        patch = [{'op': 'test', 'path': '/metadata/uid', 'value': stopped['metadata']['uid']},
                            {'op': 'test', 'path': '/metadata/resourceVersion', 'value': stopped['metadata']['resourceVersion']},
                            {'op': 'replace', 'path': '/metadata/finalizers', 'value': []}]
                        kube(['-n', namespace, 'patch', 'pod', name, '--type=json', '-p', json.dumps(patch)])
                    wait(lambda: get('pod', name, absent=True) is None, 45)
                own_namespace()
                kube(['delete', '--raw', '/api/v1/namespaces/' + namespace, '-f', '-'], canonical({
                    'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': namespace_uid}}))
                wait(lambda: get('namespace', namespace, ns=False, absent=True) is None, 60)
            cleanup['passed'] = True
        except Exception as error:
            cleanup['errors'].append(str(error) if type(error) is Failure else type(error).__name__)
        write(output / 'cleanup.json', cleanup)
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        try:
            end_source, _ = source(args.source.resolve())
            require(end_source == provenance, 'git_source_changed_during_run')
        except Exception as error:
            errors.append(str(error) if type(error) is Failure else type(error).__name__)
        passed = not errors and cleanup['passed'] and final_status is not None and final_status.get('passed') is True
        write(output / 'status.json', {'passed': passed, 'errors': errors, 'cleanup_passed': cleanup['passed'],
            'namespace': namespace, 'source': provenance, 'boundaries': BOUNDARIES,
            'images': {'control': args.control_image, 'worker': args.worker_image, 'postgres': args.postgres_image},
            'duration_seconds': round(time.monotonic() - started, 3)})
    print(json.dumps({'passed': passed, 'evidence': str(output / 'status.json'), 'errors': errors}))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
