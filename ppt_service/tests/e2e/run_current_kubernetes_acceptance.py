#!/usr/bin/env python3
"""Server-only, disposable Kubernetes kernel/network/process observations.

Uses synthetic files and an existing immutable validator image. Never connects
to an application, model, database or production worker namespace. Run only from
committed Git source on the cluster's Linux operator host; see the adjacent doc.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid


IMAGE_DIGEST = 'sha256:4492a878db0d575f20542379a61986500a1c5263b5ecc5460026a87937549bc9'
IMAGE = 'arm-cluster-master:5000/ppt-agent-validator:latest@' + IMAGE_DIGEST
PYTHON = '/opt/hermes/.venv/bin/python'
OWNER_LABEL = 'ppt.panghuer.top/acceptance-owner'
ROLE_LABEL = 'ppt.panghuer.top/acceptance-role'
PORT = 18080
PODS = ('target', 'client-a', 'client-b')
NAMESPACE_PATTERN = re.compile(r'ppt-isolation-[0-9a-f]{20}')
BOUNDARIES = [
    'Synthetic dedicated namespace; not a production worker configuration audit.',
    'TCP ingress/egress measured only between the recorded Pods on port 18080.',
    'No Internet, DNS, model/provider, database, production PVC or credential calls.',
    'Child SIGTERM/reap and Pod removal are not full Agent/business recovery.',
    'No OOM, node failure, restart recovery or ServiceAccount admission matrix.',
]


# This exact source is placed in a read-only ConfigMap, never fetched or pip
# installed. It intentionally imports no application/Agent/validator packages.
PROBE = r"""import errno
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

OUT = Path('/output')
NONCE = Path('/inputs/nonce.txt').read_text().strip()


def emit(data):
    print(json.dumps(data, sort_keys=True), flush=True)


def mount_at(path):
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        before, after = line.split(' - ', 1)
        fields, extra = before.split(), after.split()
        if fields[4] == path:
            return {'path': path, 'options': fields[5].split(','),
                    'filesystem': extra[0], 'super_options': extra[2].split(',')}
    raise RuntimeError('mount_missing:' + path)


def denied_write(path):
    try:
        with open(path, 'wb') as stream:
            stream.write(b'not permitted')
    except OSError as exc:
        return {'denied': exc.errno in (errno.EROFS, errno.EACCES, errno.EPERM),
                'errno': exc.errno, 'error': type(exc).__name__}
    return {'denied': False, 'errno': None}


def kernel():
    status = dict(line.split(':', 1) for line in
                  Path('/proc/self/status').read_text().splitlines() if ':' in line)
    fields = {key: status.get(key, '').strip() for key in
              ('Uid', 'Gid', 'CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb',
               'NoNewPrivs', 'Seccomp', 'Seccomp_filters')}
    uid = [int(value) for value in fields['Uid'].split()]
    gid = [int(value) for value in fields['Gid'].split()]
    caps_zero = all(fields[key] and int(fields[key], 16) == 0
                    for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'))
    mounts = {path: mount_at(path) for path in ('/', '/inputs', '/output', '/tmp')}
    original = Path('/inputs/material.txt').read_bytes()
    root_write = denied_write('/ppt-acceptance-root-write')
    input_write = denied_write('/inputs/material.txt')
    input_unchanged = Path('/inputs/material.txt').read_bytes() == original
    token_absent = not Path('/var/run/secrets/kubernetes.io/serviceaccount/token').exists()
    output_write = OUT / 'writable.txt'
    output_write.write_text('synthetic-output-' + NONCE)
    writable = output_write.read_text() == 'synthetic-output-' + NONCE
    checks = {
        'uid_gid': uid == [10000] * 4 and gid == [10000] * 4,
        'zero_capabilities': bool(caps_zero),
        'no_new_privileges': fields['NoNewPrivs'] == '1',
        'seccomp_filter_active': fields['Seccomp'] == '2',
        'root_mount_read_only': 'ro' in mounts['/']['options'],
        'input_mount_read_only': 'ro' in mounts['/inputs']['options'],
        'root_write_denied': root_write['denied'],
        'input_write_denied_unchanged': input_write['denied'] and input_unchanged,
        'output_writable_memory_mount': writable and
            'rw' in mounts['/output']['options'] and mounts['/output']['filesystem'] == 'tmpfs',
        'tmp_memory_mount': 'rw' in mounts['/tmp']['options'] and
            mounts['/tmp']['filesystem'] == 'tmpfs',
        'serviceaccount_token_absent': token_absent,
    }
    emit({'passed': all(checks.values()), 'checks': checks, 'proc_status': fields,
          'mounts': mounts, 'root_write': root_write, 'input_write': input_write,
          'input_sha256': hashlib.sha256(original).hexdigest(), 'pid': os.getpid()})


def connect(address):
    started = time.monotonic()
    result = {'connected': False, 'address': address, 'port': 18080,
              'errno': None, 'timeout': False}
    try:
        with socket.create_connection((address, 18080), timeout=1.5) as connection:
            response = connection.recv(256).decode('ascii')
            result.update(connected=True, response=response, nonce_matches=response == NONCE)
    except OSError as exc:
        result.update(errno=exc.errno, timeout=isinstance(exc, TimeoutError),
                      error=type(exc).__name__)
    result['elapsed_seconds'] = round(time.monotonic() - started, 3)
    emit(result)


def process_stop():
    heartbeat = OUT / 'child-heartbeat.txt'
    child_source = '''import os, sys, time
from pathlib import Path
path = Path(sys.argv[1])
counter = 0
while True:
    path.write_text(str(os.getpid()) + ':' + str(counter))
    counter += 1
    time.sleep(0.05)
'''
    child = subprocess.Popen([sys.executable, '-c', child_source, str(heartbeat)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    result = {'pid': child.pid, 'signal': 'SIGTERM', 'passed': False}
    try:
        deadline = time.monotonic() + 5
        while not heartbeat.exists() and time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError('child_exited_before_heartbeat')
            time.sleep(0.02)
        if not heartbeat.exists():
            raise RuntimeError('child_heartbeat_missing')
        stat = Path('/proc/' + str(child.pid) + '/stat').read_text()
        # Field 22 follows the ')' terminating comm, which can contain spaces.
        result['starttime_ticks'] = stat.rsplit(')', 1)[1].split()[19]
        child_status = Path('/proc/' + str(child.pid) + '/status').read_text()
        uid_line = next(line for line in child_status.splitlines() if line.startswith('Uid:'))
        result['uid'] = [int(value) for value in uid_line.split()[1:]]
        child.terminate()
        result['returncode'] = child.wait(timeout=5)
        after = heartbeat.read_bytes()
        modified = heartbeat.stat().st_mtime_ns
        time.sleep(0.25)
        result['heartbeat_stopped'] = (heartbeat.read_bytes() == after and
                                        heartbeat.stat().st_mtime_ns == modified)
        result['pid_reaped'] = not Path('/proc/' + str(child.pid)).exists()
        result['passed'] = (result['returncode'] == -signal.SIGTERM and
                            result['uid'] == [10000] * 4 and result['pid_reaped'] and
                            result['heartbeat_stopped'])
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
            result['forced_cleanup'] = True
    emit(result)


def main():
    mode = sys.argv[1]
    if mode in ('hold', 'server'):
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        if mode == 'hold':
            while True:
                time.sleep(0.5)
        with socket.socket() as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(('0.0.0.0', 18080))
            server.listen(16)
            server.settimeout(0.5)
            while True:
                try:
                    connection, _ = server.accept()
                except TimeoutError:
                    continue
                with connection:
                    connection.settimeout(1)
                    try:
                        connection.sendall(NONCE.encode('ascii'))
                    except OSError:
                        pass
    elif mode == 'kernel':
        kernel()
    elif mode == 'network':
        connect(sys.argv[2])
    elif mode == 'write-owner':
        (OUT / 'owner.txt').write_text(sys.argv[2])
        emit({'owner': (OUT / 'owner.txt').read_text()})
    elif mode == 'read-owner':
        emit({'owner': (OUT / 'owner.txt').read_text()})
    elif mode == 'process-stop':
        process_stop()
    else:
        raise RuntimeError('unknown_probe_mode')


if __name__ == '__main__':
    main()
"""


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
                    encoding='utf-8')


def run_command(arguments, *, stdin=None, timeout=20, check=True):
    result = subprocess.run(arguments, input=stdin, text=True, capture_output=True,
                            timeout=timeout, check=False)
    if check and result.returncode:
        # All kubectl operations below concern this run's synthetic resources;
        # no kubeconfig, exec credentials, secrets or application logs are read.
        raise RuntimeError('command_failed:' + result.stderr.strip()[:1200])
    return result


class KubernetesRun:
    def __init__(self, args, evidence, source):
        self.args, self.evidence, self.source = args, evidence, source
        self.owner = uuid.uuid4().hex
        self.namespace = 'ppt-isolation-' + self.owner[:20]
        self.namespace_uid = None
        self.nonce = uuid.uuid4().hex
        self.identities = {}
        self.results = {}
        self.steps = []
        self.kubectl = [shutil.which('kubectl'), '--kubeconfig', str(args.kubeconfig),
                        '--request-timeout=10s']
        if args.context:
            self.kubectl += ['--context', args.context]

    def command(self, *arguments, namespaced=True, stdin=None, timeout=20, check=True):
        args = self.kubectl + (['--namespace', self.namespace] if namespaced else [])
        return run_command(args + list(arguments), stdin=stdin, timeout=timeout, check=check)

    def record(self, name, data):
        write_json(self.evidence / (name + '.json'), data)
        self.results[name] = data
        write_json(self.evidence / 'stages.json', self.results)
        return data

    def delete_owned_uid(self, kind, name, uid):
        require(bool(uid), 'delete_uid_required')
        if kind == 'namespace':
            require(name == self.namespace and uid == self.namespace_uid,
                    'unsafe_namespace_delete')
            path = '/api/v1/namespaces/' + self.namespace
        else:
            require(kind == 'pod' and name in PODS and
                    uid == self.identities[name]['uid'], 'unsafe_pod_delete')
            path = '/api/v1/namespaces/' + self.namespace + '/pods/' + name
        options = {'apiVersion': 'v1', 'kind': 'DeleteOptions',
                   'preconditions': {'uid': uid}, 'gracePeriodSeconds': 5}
        self.command('delete', '--raw', path, '-f', '-', namespaced=False,
                     stdin=json.dumps(options))

    def create(self, resource):
        kind = resource['kind'].lower()
        name = resource['metadata']['name']
        write_json(self.evidence / ('manifest-' + kind + '-' + name + '.json'), resource)
        self.command('create', '-f', '-', namespaced=kind != 'namespace',
                     stdin=json.dumps(resource))

    def resource(self, kind, name, *, namespaced=True):
        result = self.command('get', kind, name, '-o', 'json', '--ignore-not-found=true',
                              namespaced=namespaced)
        return json.loads(result.stdout) if result.stdout.strip() else None

    def metadata(self, name, role=None):
        labels = {OWNER_LABEL: self.owner}
        if role:
            labels[ROLE_LABEL] = role
        return {'name': name, 'namespace': self.namespace, 'labels': labels}

    def policy(self, name):
        spec = {'podSelector': {}, 'policyTypes': ['Ingress', 'Egress'],
                'ingress': [], 'egress': []}
        if name == 'client-egress':
            spec = {'podSelector': {'matchLabels': {OWNER_LABEL: self.owner, ROLE_LABEL: 'client'}},
                    'policyTypes': ['Egress'], 'egress': [{
                        'to': [{'podSelector': {'matchLabels': {
                            OWNER_LABEL: self.owner, ROLE_LABEL: 'target'}}}],
                        'ports': [{'protocol': 'TCP', 'port': PORT}]}]}
        elif name == 'target-ingress':
            spec = {'podSelector': {'matchLabels': {OWNER_LABEL: self.owner, ROLE_LABEL: 'target'}},
                    'policyTypes': ['Ingress'], 'ingress': [{
                        'from': [{'podSelector': {'matchLabels': {
                            OWNER_LABEL: self.owner, ROLE_LABEL: 'client'}}}],
                        'ports': [{'protocol': 'TCP', 'port': PORT}]}]}
        else:
            require(name == 'deny-all', 'unknown_policy')
        return {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy',
                'metadata': self.metadata(name), 'spec': spec}

    def pod(self, name):
        role = 'target' if name == 'target' else 'client'
        return {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': self.metadata(name, role),
            'spec': {
                'restartPolicy': 'Never', 'activeDeadlineSeconds': 600,
                'terminationGracePeriodSeconds': 5,
                'automountServiceAccountToken': False, 'enableServiceLinks': False,
                'hostNetwork': False, 'hostPID': False, 'hostIPC': False,
                'shareProcessNamespace': False,
                'securityContext': {'runAsNonRoot': True, 'runAsUser': 10000,
                    'runAsGroup': 10000, 'fsGroup': 10000,
                    'seccompProfile': {'type': 'RuntimeDefault'}},
                'containers': [{
                    'name': 'probe', 'image': IMAGE, 'imagePullPolicy': 'IfNotPresent',
                    'command': [PYTHON, '/inputs/probe.py', 'server' if role == 'target' else 'hold'],
                    'env': [{'name': 'PYTHONDONTWRITEBYTECODE', 'value': '1'},
                            {'name': 'HOME', 'value': '/tmp'}],
                    'resources': {'requests': {'cpu': '10m', 'memory': '32Mi'},
                                  'limits': {'cpu': '250m', 'memory': '96Mi'}},
                    'securityContext': {'runAsNonRoot': True, 'runAsUser': 10000,
                        'runAsGroup': 10000, 'privileged': False,
                        'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                        'capabilities': {'drop': ['ALL']},
                        'seccompProfile': {'type': 'RuntimeDefault'}},
                    'volumeMounts': [
                        {'name': 'inputs', 'mountPath': '/inputs', 'readOnly': True},
                        {'name': 'output', 'mountPath': '/output'},
                        {'name': 'tmp', 'mountPath': '/tmp'}],
                }],
                'volumes': [
                    {'name': 'inputs', 'configMap': {'name': 'synthetic-inputs', 'defaultMode': 292}},
                    {'name': 'output', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '16Mi'}},
                    {'name': 'tmp', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '8Mi'}},
                ],
            }}

    def verify_namespace(self):
        require(bool(NAMESPACE_PATTERN.fullmatch(self.namespace)), 'unsafe_namespace_name')
        resource = self.resource('namespace', self.namespace, namespaced=False)
        if resource is None:
            return None
        meta = resource['metadata']
        require(meta.get('labels', {}).get(OWNER_LABEL) == self.owner, 'namespace_owner_mismatch')
        require(bool(meta.get('uid')), 'namespace_uid_missing')
        if self.namespace_uid is None:
            self.namespace_uid = meta['uid']
            write_json(self.evidence / 'namespace-identity.json', {
                'name': self.namespace, 'uid': self.namespace_uid, 'owner': self.owner})
        require(meta['uid'] == self.namespace_uid, 'namespace_uid_changed')
        return resource

    def setup(self):
        namespace = {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {
            'name': self.namespace, 'labels': {OWNER_LABEL: self.owner,
                'pod-security.kubernetes.io/enforce': 'restricted',
                'pod-security.kubernetes.io/audit': 'restricted',
                'pod-security.kubernetes.io/warn': 'restricted'}}}
        self.create(namespace)
        self.verify_namespace()
        self.create({'apiVersion': 'v1', 'kind': 'ResourceQuota',
            'metadata': self.metadata('acceptance-bounds'), 'spec': {'hard': {
                'pods': '3', 'requests.cpu': '30m', 'requests.memory': '96Mi',
                'limits.cpu': '750m', 'limits.memory': '288Mi',
                # Kubernetes also injects the public kube-root-ca.crt CM.
                'count/configmaps': '2', 'count/secrets': '0',
                'persistentvolumeclaims': '0', 'services': '0',
                'count/networkpolicies.networking.k8s.io': '3'}}})
        self.create({'apiVersion': 'v1', 'kind': 'ConfigMap',
            'metadata': self.metadata('synthetic-inputs'), 'immutable': True,
            'data': {'probe.py': PROBE, 'material.txt': 'Synthetic public acceptance material.\n',
                     'nonce.txt': self.nonce + '\n'}})
        # Default deny exists before the first Pod; positive controls grant only
        # this run's same-namespace TCP flow. No DNS or external egress is added.
        for name in ('deny-all', 'client-egress', 'target-ingress'):
            self.create(self.policy(name))
        for name in PODS:
            self.create(self.pod(name))

    def validate_pod(self, pod):
        name = pod['metadata']['name']
        require(name in PODS, 'unexpected_pod_name')
        meta, spec = pod['metadata'], pod['spec']
        expected = self.pod(name)['spec']
        require(meta.get('labels', {}).get(OWNER_LABEL) == self.owner, 'pod_owner_mismatch')
        for field in ('automountServiceAccountToken', 'enableServiceLinks', 'hostNetwork',
                      'hostPID', 'hostIPC', 'shareProcessNamespace'):
            require(spec.get(field, False) is False, 'unexpected_pod_field:' + field)
        require(spec.get('automountServiceAccountToken') is False, 'automount_not_disabled')
        require(not spec.get('initContainers') and not spec.get('ephemeralContainers'),
                'unexpected_extra_containers')
        require(len(spec.get('containers', [])) == 1, 'unexpected_container_count')
        container = spec['containers'][0]
        for field in ('name', 'image', 'command', 'env', 'volumeMounts', 'resources'):
            require(container.get(field) == expected['containers'][0][field],
                    'container_spec_changed:' + field)
        require(not container.get('envFrom') and not container.get('ports'),
                'unexpected_environment_or_ports')
        require(container.get('securityContext') == expected['containers'][0]['securityContext'],
                'container_security_context_changed')
        actual_security = spec.get('securityContext', {})
        require(actual_security == expected['securityContext'],
                'pod_security_context_changed')
        require(len(spec.get('volumes', [])) == 3, 'unexpected_volume_count')
        for volume, wanted in zip(spec['volumes'], expected['volumes']):
            # The API may default ConfigMap optional:false; all mounts must
            # still be the fixed ConfigMap or new Pod-local memory emptyDirs.
            require(volume.get('name') == wanted['name'], 'volume_order_changed')
            require(set(volume) == set(wanted), 'unexpected_volume_source')
            key = 'configMap' if 'configMap' in wanted else 'emptyDir'
            require(all(volume[key].get(k) == v for k, v in wanted[key].items()),
                    'volume_spec_changed')
        require(not spec.get('imagePullSecrets'), 'unexpected_pull_credentials')
        require(not spec.get('hostAliases'), 'unexpected_host_aliases')
        statuses = pod.get('status', {}).get('containerStatuses', [])
        require(len(statuses) == 1 and statuses[0].get('restartCount') == 0,
                'probe_restarted_or_status_missing')
        image_id = statuses[0].get('imageID', '')
        require(image_id.endswith(IMAGE_DIGEST), 'runtime_image_digest_differs')
        ip = str(ipaddress.ip_address(pod['status']['podIP']))
        identity = {'name': name, 'uid': meta['uid'], 'ip': ip,
                    'node': spec['nodeName'], 'image': container['image'], 'image_id': image_id}
        if name in self.identities:
            require(identity == self.identities[name], 'pod_identity_changed:' + name)
        else:
            self.identities[name] = identity
        # Do not save unrelated admission annotations (which could contain
        # operator configuration); save only the fields actually assessed.
        return {'identity': identity, 'security_context': actual_security,
                'container_security_context': container['securityContext'],
                'volumes': spec['volumes'], 'volume_mounts': container['volumeMounts'],
                'automount_serviceaccount_token': spec['automountServiceAccountToken'],
                'namespace': self.namespace}

    def wait_pods(self):
        deadline = time.monotonic() + 120
        latest = {}
        while time.monotonic() < deadline:
            ready = []
            for name in PODS:
                pod = self.resource('pod', name)
                require(pod is not None, 'expected_pod_missing:' + name)
                latest[name] = {'phase': pod.get('status', {}).get('phase'),
                    'container_states': [entry.get('state') for entry in
                                         pod.get('status', {}).get('containerStatuses', [])]}
                require(latest[name]['phase'] not in ('Failed', 'Succeeded'),
                        'pod_exited:' + name)
                ready.append(latest[name]['phase'] == 'Running' and
                    any(c.get('type') == 'Ready' and c.get('status') == 'True'
                        for c in pod.get('status', {}).get('conditions', [])))
            if all(ready):
                observed = {name: self.validate_pod(self.resource('pod', name)) for name in PODS}
                self.record('pod-observations', observed)
                return
            write_json(self.evidence / 'startup-state.json', latest)
            time.sleep(1)
        raise RuntimeError('pods_not_ready_within_120_seconds')

    def probe(self, pod, mode, *args):
        require(pod in PODS, 'unknown_probe_pod')
        result = self.command('exec', pod, '-c', 'probe', '--', PYTHON,
                              '/inputs/probe.py', mode, *args, timeout=25)
        data = json.loads(result.stdout)
        self.steps.append({'pod': pod, 'mode': mode, 'arguments': list(args), 'result': data})
        write_json(self.evidence / 'probe-steps.json', self.steps)
        return data

    def stable_pods(self):
        for name in PODS:
            pod = self.resource('pod', name)
            require(pod is not None, 'pod_missing_during_measurement:' + name)
            self.validate_pod(pod)
            require(pod['status'].get('phase') == 'Running' and
                    not pod['metadata'].get('deletionTimestamp'), 'pod_not_live:' + name)

    def allowed(self, stage):
        deadline = time.monotonic() + 35
        observations = []
        while time.monotonic() < deadline:
            data = self.probe('client-a', 'network', self.identities['target']['ip'])
            observations.append(data)
            if data.get('connected') and data.get('nonce_matches'):
                self.stable_pods()
                return self.record(stage, {'passed': True, 'observations': observations,
                                           'identities': self.identities})
            time.sleep(0.5)
        self.record(stage, {'passed': False, 'observations': observations})
        raise RuntimeError('positive_tcp_control_failed:' + stage)

    def remove_policy(self, name):
        require(name in ('client-egress', 'target-ingress'), 'unsafe_policy_delete')
        resource = self.resource('networkpolicy', name)
        require(resource is not None and resource['metadata'].get('labels', {}).get(
            OWNER_LABEL) == self.owner, 'policy_owner_mismatch')
        self.command('delete', 'networkpolicy', name, '--wait=false')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.resource('networkpolicy', name) is None:
                return
            time.sleep(0.2)
        raise RuntimeError('policy_delete_not_observed:' + name)

    def denied(self, stage):
        observations, consecutive = [], 0
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            data = self.probe('client-a', 'network', self.identities['target']['ip'])
            # Refused/reset/EOF alone do not establish policy enforcement. A
            # timeout or explicit permission denial is required, then restored
            # positive control on the exact same live source and destination.
            exact_denial = (not data.get('connected') and
                (data.get('timeout') is True or data.get('errno') in (1, 13, 110)))
            data['accepted_denial_evidence'] = exact_denial
            observations.append(data)
            consecutive = consecutive + 1 if exact_denial else 0
            if consecutive >= 3:
                health = self.probe('target', 'network', '127.0.0.1')
                require(health.get('connected') and health.get('nonce_matches'),
                        'target_unhealthy_during_denial')
                self.stable_pods()
                return self.record(stage, {'passed': True, 'consecutive_denials': consecutive,
                    'observations': observations, 'target_loopback_control': health,
                    'identities': self.identities})
            time.sleep(0.25)
        self.record(stage, {'passed': False, 'consecutive_denials': consecutive,
                            'observations': observations})
        raise RuntimeError('exact_tcp_denial_not_observed:' + stage)

    def execute(self):
        self.setup()
        self.wait_pods()
        for name in PODS:
            result = self.record('kernel-' + name, self.probe(name, 'kernel'))
            require(result.get('passed') is True, 'kernel_observation_failed:' + name)
            expected_material = hashlib.sha256(b'Synthetic public acceptance material.\n').hexdigest()
            require(result.get('input_sha256') == expected_material, 'synthetic_input_changed')
        owners = {'client-a': 'a-' + self.owner, 'client-b': 'b-' + self.owner}
        for name, owner in owners.items():
            require(self.probe(name, 'write-owner', owner).get('owner') == owner,
                    'output_owner_write_failed:' + name)
        observed = {name: self.probe(name, 'read-owner').get('owner') for name in owners}
        self.record('independent-outputs', {'passed': observed == owners, 'expected': owners,
                                          'observed': observed})
        require(observed == owners, 'pod_output_collision')
        self.allowed('tcp-baseline')
        second = self.probe('client-b', 'network', self.identities['target']['ip'])
        require(second.get('connected') and second.get('nonce_matches'),
                'second_client_positive_control_failed')
        for policy, direction in (('client-egress', 'egress'), ('target-ingress', 'ingress')):
            self.remove_policy(policy)
            self.denied('tcp-' + direction + '-denied')
            self.create(self.policy(policy))
            self.allowed('tcp-' + direction + '-restored')
        stopped = self.record('real-child-stop', self.probe('client-a', 'process-stop'))
        require(stopped.get('passed') is True, 'real_child_sigterm_failed')
        self.stable_pods()
        self.delete_pods()

    def delete_pods(self):
        observations = {}
        for name in PODS:
            pod = self.resource('pod', name)
            require(pod is not None, 'pod_missing_before_stop:' + name)
            self.validate_pod(pod)
            self.delete_owned_uid('pod', name, self.identities[name]['uid'])
            observations[name] = {'uid': self.identities[name]['uid'], 'gone': False}
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            for name in PODS:
                pod = self.resource('pod', name)
                if pod is None:
                    observations[name]['gone'] = True
                else:
                    require(pod['metadata']['uid'] == self.identities[name]['uid'],
                            'pod_replaced_during_stop:' + name)
            if all(value['gone'] for value in observations.values()):
                self.record('pod-stop-removal', {'passed': True, 'pods': observations,
                    'boundary': 'API-observed Pod removal; not independent node runtime forensics.'})
                return
            time.sleep(0.5)
        self.record('pod-stop-removal', {'passed': False, 'pods': observations})
        raise RuntimeError('pods_not_removed_within_45_seconds')

    def cleanup(self):
        result = {'namespace': self.namespace, 'uid': self.namespace_uid,
                  'owner': self.owner, 'passed': False, 'namespace_absent': False}
        try:
            resource = self.verify_namespace()
            result['uid'] = self.namespace_uid
            if resource is not None:
                # verify_namespace checks owner and captured UID immediately
                # before delete. Never force, patch finalizers or delete a
                # resource outside this single randomly named namespace.
                self.delete_owned_uid('namespace', self.namespace, self.namespace_uid)
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    resource = self.verify_namespace()
                    if resource is None:
                        break
                    time.sleep(0.5)
            result['namespace_absent'] = resource is None
            result['passed'] = result['namespace_absent']
            if not result['passed']:
                result['error'] = 'owned_namespace_still_present_no_finalizer_override'
        except Exception as exc:
            result['error'] = str(exc)
        write_json(self.evidence / 'cleanup.json', result)
        return result


def committed_source():
    script = Path(__file__).resolve()
    source = script.parents[3]
    relative = script.relative_to(source).as_posix()
    commit = run_command(['git', '-C', str(source), 'rev-parse', 'HEAD']).stdout.strip()
    require(bool(re.fullmatch(r'[0-9a-f]{40,64}', commit)), 'invalid_git_commit')
    tracked = subprocess.run(['git', '-C', str(source), 'show', commit + ':' + relative],
                             capture_output=True, check=False, timeout=10)
    require(tracked.returncode == 0, 'runner_must_be_committed_before_server_execution')
    actual = script.read_bytes()
    # A Linux checkout uses repository LF bytes. Do not normalize source before
    # hashing, so the executed source is the exact recorded Git blob.
    require(tracked.stdout == actual, 'runner_differs_from_recorded_git_commit')
    return source, {'commit': commit, 'path': relative,
        'runner_sha256': hashlib.sha256(actual).hexdigest(),
        'probe_sha256': hashlib.sha256(PROBE.encode()).hexdigest(),
        'image': IMAGE, 'boundaries': BOUNDARIES}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kubeconfig', type=Path, required=True,
                        help='Explicit Linux server kubeconfig; never mounted in a Pod.')
    parser.add_argument('--context', help='Optional explicit context in that kubeconfig.')
    parser.add_argument('--output', type=Path, required=True,
                        help='New evidence directory outside the source tree; parent must exist.')
    args = parser.parse_args()
    require(sys.platform == 'linux', 'linux_server_only_no_local_ppt_execution')
    require(sys.version_info >= (3, 11), 'python_3_11_required')
    require(os.environ.get('PPT_RUN_K8S_ISOLATION') == 'true', 'explicit_server_opt_in_required')
    require(bool(shutil.which('kubectl')), 'kubectl_missing')
    args.kubeconfig = args.kubeconfig.expanduser().resolve(strict=True)
    require(args.kubeconfig.is_file(), 'explicit_kubeconfig_file_required')
    source, receipt = committed_source()
    evidence = args.output.expanduser().resolve()
    require(not evidence.is_relative_to(source), 'evidence_must_be_outside_source')
    require(evidence.parent.is_dir() and not evidence.exists(), 'new_evidence_directory_required')
    evidence.mkdir(mode=0o700)
    write_json(evidence / 'source.json', receipt)
    run = KubernetesRun(args, evidence, source)
    status = {'passed': False, 'namespace': run.namespace, 'owner': run.owner,
              'git_commit': receipt['commit'], 'image': IMAGE, 'boundaries': BOUNDARIES}
    errors = []
    def interrupted(signum, _frame):
        raise RuntimeError('runner_received_signal:' + str(signum))

    # Unlike SIGKILL or a host crash, SIGTERM is catchable: perform the same
    # ownership-checked cleanup and produce a failing evidence receipt.
    signal.signal(signal.SIGTERM, interrupted)
    try:
        run.execute()
    except BaseException as exc:
        errors.append(type(exc).__name__ + ':' + str(exc))
    finally:
        cleanup = run.cleanup()
        if not cleanup['passed']:
            errors.append('owned_namespace_cleanup_failed')
        script = Path(__file__).resolve()
        if hashlib.sha256(script.read_bytes()).hexdigest() != receipt['runner_sha256']:
            errors.append('runner_source_changed_during_execution')
        status.update(passed=not errors, errors=errors, cleanup=cleanup,
                      completed_stages=sorted(run.results))
        write_json(evidence / 'status.json', status)
    print(json.dumps({'passed': status['passed'], 'evidence': str(evidence),
                      'namespace': run.namespace, 'errors': errors}, ensure_ascii=False))
    return 0 if status['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
