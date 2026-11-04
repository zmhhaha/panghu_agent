#!/usr/bin/env python3
"""Bounded read-only TCP observations inside one explicitly authorized capsule.

The operator must supply the exact disposable business task's Pod UID, attempt
and request hash. Do not run against unrelated/valuable jobs or near collection.
There is no application payload, model request, file read or background process.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
import re
import socket
import subprocess
import sys


PREFIX = 'agent-service.panghuer.top/'
PYTHON = '/opt/agent-service/.venv/bin/python'
SERVICES = (
    ('model-proxy', 'agent-service', 'agent-model-proxy', 8080, True),
    ('agent-api', 'agent-service', 'agent-service', 8080, False),
    ('postgres', 'data', 'postgres', 5432, False),
    ('vault', 'vault', 'vault', 8200, False),
    ('kubernetes', 'default', 'kubernetes', 443, False),
    ('upstream-llm', 'llm', 'llm-service', 80, False),
)

PROBE = r'''import json, os, socket, sys, time
uid = int(sys.argv[1])
targets = json.loads(sys.argv[2])
assert 65536 <= uid <= 2147483646 and len(targets) <= 10
os.setgroups([])
os.setresgid(uid, uid, uid)
os.setresuid(uid, uid, uid)
with open('/proc/self/status') as stream:
    status = dict(line.split(':', 1) for line in stream if ':' in line)
with open('/proc/self/stat') as stream:
    starttime = stream.read().rsplit(')', 1)[1].split()[19]
safe = {key: status.get(key, '').strip() for key in
        ('Uid','Gid','CapEff','CapPrm','CapInh','CapAmb','NoNewPrivs','Seccomp')}
assert [int(v) for v in safe['Uid'].split()] == [uid] * 4
assert [int(v) for v in safe['Gid'].split()] == [uid] * 4
assert all(int(safe[k], 16) == 0 for k in ('CapEff','CapPrm','CapInh','CapAmb'))
kernel_fields_match_runtime_contract = safe['NoNewPrivs'] == '1' and safe['Seccomp'] == '2'
rows = []
for target in targets:
    started = time.monotonic()
    row = dict(name=target['name'], address=target['address'], port=target['port'],
               expected_connected=target['expected_connected'], connected=False)
    try:
        with socket.create_connection((target['address'], target['port']), timeout=1):
            row['connected'] = True
    except OSError as exc:
        row.update(error=type(exc).__name__, errno=exc.errno)
    row['seconds'] = round(time.monotonic() - started, 3)
    row['passed'] = row['connected'] == row['expected_connected']
    rows.append(row)
print(json.dumps(dict(pid=os.getpid(), starttime_ticks=starttime,
    proc_status=safe, connections=rows, application_payload_sent=False,
    exec_kernel_fields_match_runtime_contract=kernel_fields_match_runtime_contract,
    passed=all(r['passed'] for r in rows)), sort_keys=True), flush=True)
'''

VERIFY_EXIT = r'''import json, sys
pid, start = int(sys.argv[1]), sys.argv[2]
try:
    with open('/proc/' + str(pid) + '/stat') as stream:
        actual = stream.read().rsplit(')', 1)[1].split()[19]
    absent = actual != start
except FileNotFoundError:
    absent = True
print(json.dumps(dict(probe_pid=pid, original_process_absent=absent)), flush=True)
raise SystemExit(0 if absent else 1)
'''


def require(value, message):
    if not value:
        raise RuntimeError(message)


def save(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


class Probe:
    def __init__(self, args):
        self.args = args
        self.output = args.output.resolve()
        self.output.mkdir(parents=True, exist_ok=False)
        self.kube = ['kubectl', '--request-timeout=10s']
        if args.kubeconfig:
            self.kube += ['--kubeconfig', str(args.kubeconfig)]

    def run(self, args, *, check=True, timeout=15):
        result = subprocess.run(self.kube + args, text=True, capture_output=True,
                                timeout=timeout, check=False)
        if check and result.returncode:
            raise RuntimeError('kubectl_failed:' + result.stderr.strip()[:600])
        return result

    def get(self, kind, name, namespace=None):
        args = (['-n', namespace] if namespace else []) + ['get', kind, name, '-o', 'json']
        return json.loads(self.run(args).stdout)

    def capsule(self):
        pod = self.get('pod', self.args.pod, 'agent-workers')
        metadata, spec = pod['metadata'], pod['spec']
        require(metadata['uid'] == self.args.pod_uid and not metadata.get('deletionTimestamp'),
                'pod_identity_changed_or_stopping')
        require(pod['status']['phase'] == 'Running', 'pod_not_running')
        require(metadata['labels'].get('app') == 'agent-capsule'
            and metadata['labels'].get(PREFIX + 'attempt') == self.args.attempt_id
            and metadata['annotations'].get(PREFIX + 'request') == self.args.request_sha256,
            'authorized_attempt_mapping_mismatch')
        require(len(spec['containers']) == 1 and spec['containers'][0]['name'] == 'worker',
                'unexpected_capsule_shape')
        environment = {r['name']: r.get('value') for r in spec['containers'][0]['env']}
        require(environment['AGENT_ATTEMPT_ID'] == self.args.attempt_id
            and environment['AGENT_REQUEST_SHA256'] == self.args.request_sha256
            and environment['AGENT_PROFILE_NAME'].startswith('ppt.'), 'not_authorized_ppt_capsule')
        runtime_uid = int(environment['AGENT_RUNTIME_UID'])
        require(runtime_uid >= 65536 and int(environment['AGENT_PREVIEW_UID']) == runtime_uid + 1,
                'unreserved_runtime_uid')
        value = dict(pod=self.args.pod, pod_uid=metadata['uid'], attempt_id=self.args.attempt_id,
            request_sha256=self.args.request_sha256, runtime_uid=runtime_uid,
            profile=environment['AGENT_PROFILE_NAME'], image=spec['containers'][0]['image'],
            node=spec['nodeName'], pod_ip=pod['status']['podIP'],
            business_job_id_operator_supplied=self.args.business_job_id)
        return value

    def target(self, name, address, port, expected, *, control=True):
        require(ipaddress.ip_address(address).is_private, 'private_control_target_required')
        result = dict(name=name, address=address, port=port, expected_connected=expected,
                      operator_control_connected=False, operator_control_attempted=control)
        if control:
            try:
                with socket.create_connection((address, port), timeout=1):
                    result['operator_control_connected'] = True
            except OSError as exc:
                result.update(operator_control_error=type(exc).__name__, operator_control_errno=exc.errno)
        return result

    def targets(self, identity):
        rows = []
        for name, namespace, service_name, preferred, expected in SERVICES:
            service = self.get('service', service_name, namespace)
            ports = service['spec']['ports']
            require(any(p['port'] == preferred for p in ports), 'known_service_port_changed:' + name)
            port = preferred
            rows.append(self.target(name, service['spec']['clusterIP'], port, expected))
        node = self.get('node', identity['node'])
        address = next(a['address'] for a in node['status']['addresses'] if a['type'] == 'InternalIP')
        rows.append(self.target('node-ssh', address, 22, False))
        # An absent LAN metadata endpoint is not evidence of an enforced deny.
        # Observe it but keep its unavailable positive control explicit.
        rows.append(self.target('metadata', '169.254.169.254', 80, False, control=False))
        return rows

    def execute(self):
        before = self.capsule()
        save(self.output / 'authorized-capsule-before.json', before)
        targets = self.targets(before)
        save(self.output / 'operator-control-targets.json', targets)
        # Missing positive controls must be reported; do not relabel them as
        # a successful deny. Still observe the target without sending payload.
        require(targets[0]['operator_control_connected'], 'model_proxy_control_unavailable')
        require(self.capsule() == before, 'capsule_changed_before_probe')
        command = ['-n', 'agent-workers', 'exec', self.args.pod, '-c', 'worker', '--',
            PYTHON, '-c', PROBE, str(before['runtime_uid']), json.dumps(targets)]
        response = self.run(command, timeout=20)
        observed = json.loads(response.stdout)
        save(self.output / 'runtime-observation.json', observed)
        exited = json.loads(self.run(['-n', 'agent-workers', 'exec', self.args.pod, '-c', 'worker',
            '--', PYTHON, '-c', VERIFY_EXIT, str(observed['pid']), observed['starttime_ticks']],
            timeout=10).stdout)
        after = self.capsule()
        save(self.output / 'authorized-capsule-after.json', after)
        save(self.output / 'probe-exit.json', exited)
        controlled = [t['name'] for t in targets if t['operator_control_connected']]
        not_controlled = [t['name'] for t in targets if not t['operator_control_connected']]
        summary = dict(passed=observed['passed'] and exited['original_process_absent'] and before == after,
            exec_kernel_fields_match_runtime_contract=observed['exec_kernel_fields_match_runtime_contract'],
            controlled_target_names=controlled, unavailable_positive_control_names=not_controlled,
            deny_passes_with_healthy_control=[r['name'] for r in observed['connections']
                if not r['expected_connected'] and r['passed'] and r['name'] in controlled],
            scope='one explicitly authorized disposable PPT capsule; read-only TCP without payload',
            pod_uid=before['pod_uid'], attempt_id=before['attempt_id'],
            application_payload_sent=False, background_process_created=False,
            business_state_modified=False)
        save(self.output / 'summary.json', summary)
        print(json.dumps(summary, sort_keys=True))
        return 0 if summary['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pod', required=True)
    parser.add_argument('--pod-uid', required=True)
    parser.add_argument('--attempt-id', required=True)
    parser.add_argument('--request-sha256', required=True)
    parser.add_argument('--business-job-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--kubeconfig', type=Path)
    args = parser.parse_args()
    require(sys.platform.startswith('linux'), 'server_linux_required')
    require(re.fullmatch(r'agent-[0-9a-f-]{36}', args.pod) is not None, 'invalid_capsule_name')
    require(re.fullmatch(r'[0-9a-f]{64}', args.request_sha256) is not None, 'invalid_request_sha256')
    return Probe(args).execute()


if __name__ == '__main__':
    raise SystemExit(main())
