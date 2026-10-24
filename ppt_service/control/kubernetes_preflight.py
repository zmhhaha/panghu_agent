"""No-model, real-Pod network preflight with retained API stop evidence.

Run inside the trusted controller, including while paid admission is disabled:
python -m ppt_service.control.kubernetes_preflight
Probe Pods use the actual task image, SA, mounts and generated NetworkPolicies.
An unavailable/uncertain Pod is a failure, never synthetic isolation evidence.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import ssl
import struct
import threading
import time
import urllib.parse
import uuid

from .execution import ControlError, RuntimeConfig
from .kubernetes import KubeSettings, KubernetesBackend, ROLE, require
from .staging import atomic_json, make_readonly, read_json

MODULE = 'ppt_service.control.kubernetes_preflight'
PROBE_SECONDS = 30
SCHEDULING_SECONDS = 70
STOP_OBSERVATION_SECONDS = 45
TOTAL_SECONDS = 120
SOCKET_SECONDS = 0.7
UPSTREAM_IP = '62.234.50.20'
UPSTREAM_NAME = 'custom-llm-proxy.panghuer.top'
ROLES = ('proxy', 'worker', 'sandbox')


def ipv4(value):
    require(isinstance(value, str) and ipaddress.ip_address(value).version == 4, 'preflight_target_invalid')
    return value


def resolve_v4(host):
    try:
        return sorted({row[4][0] for row in socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)})
    except OSError:
        raise ControlError('preflight_target_unavailable') from None


def dns_servers(path=Path('/etc/resolv.conf')):
    values = []
    for line in path.read_text(encoding='ascii').splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == 'nameserver':
            address = ipaddress.ip_address(parts[1])
            if address.version == 4:
                values.append(str(address))
    require(bool(values), 'preflight_dns_unavailable')
    return sorted(set(values))


def deployment_targets(config, *, db_url=None, api_url=None, dns=None, service_ip=None, node_ips=()):
    """Resolve service names in the controller; credentials never enter requests."""
    db = urllib.parse.urlsplit(db_url or os.environ['DATABASE_URL'])
    default_api = 'https://' + os.getenv('KUBERNETES_SERVICE_HOST', '') + ':' + os.getenv('KUBERNETES_SERVICE_PORT_HTTPS', '443')
    api = urllib.parse.urlsplit(api_url or os.environ.get('PPT_KUBE_API_SERVER') or default_api)
    require(db.scheme.startswith('postgresql') and db.hostname and api.scheme == 'https' and api.hostname,
            'preflight_target_invalid')
    db_ips = resolve_v4(db.hostname)
    node_ips = sorted(set(resolve_v4(api.hostname)) | {ipv4(value) for value in node_ips})
    kube_service = service_ip or os.getenv('KUBERNETES_SERVICE_HOST')
    if not kube_service:
        kube_service = resolve_v4('kubernetes.default.svc.cluster.local')[0]
    rows = []
    def add(name, host, port, protocol='tcp'):
        rows.append({'name': name, 'host': ipv4(host), 'port': port, 'protocol': protocol})
    add('upstream', UPSTREAM_IP, 443)
    add('external', '1.1.1.1', 443)
    add('metadata', '169.254.169.254', 80)
    add('other_service_api', kube_service, 443)
    for n, host in enumerate(node_ips):
        add(f'node_ssh_{n}', host, 22)
        add(f'node_api_{n}', host, api.port or 443)
        if (api.port or 443) != 6443:
            add(f'node_kube_{n}', host, 6443)
    for n, host in enumerate(db_ips):
        add(f'database_{n}', host, db.port or 5432)
    for n, host in enumerate(dns if dns is not None else dns_servers()):
        add(f'dns_tcp_{n}', host, 53)
        add(f'dns_udp_{n}', host, 53, 'udp')
    require(len(rows) <= 32 and config.approved_upstream_ip == UPSTREAM_IP, 'preflight_target_invalid')
    return rows


def _socket_probe(row, challenge):
    """Return connectivity, not text from the remote service or an exception."""
    protocol = row.get('protocol')
    require(protocol in ('tcp', 'udp') and type(row.get('port')) is int
            and 1 <= row['port'] <= 65535, 'preflight_target_invalid')
    address = (ipv4(row['host']), row['port'])
    try:
        if protocol == 'udp':
            # A real DNS query; connect() alone cannot establish UDP reachability.
            transaction = secrets.token_bytes(2)
            packet = transaction + struct.pack('!HHHHH', 0x0100, 1, 0, 0, 0)
            packet += b'\x0akubernetes\x07default\x03svc\x07cluster\x05local\x00\x00\x01\x00\x01'
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as stream:
                stream.settimeout(SOCKET_SECONDS)
                stream.sendto(packet, address)
                value, peer = stream.recvfrom(4096)
            return bool(value[:2] == transaction and peer[0] == address[0]), 'dns_response'
        with socket.create_connection(address, timeout=SOCKET_SECONDS) as stream:
            stream.settimeout(SOCKET_SECONDS)
            if row.get('tls'):
                with ssl.create_default_context().wrap_socket(stream, server_hostname=UPSTREAM_NAME):
                    return True, 'verified_tls'
            if row.get('challenge'):
                stream.sendall(challenge.encode('ascii'))
                return stream.recv(128) == challenge.encode('ascii'), 'scope_challenge'
            return True, 'tcp_connected'
    except ssl.SSLError:
        return False, 'tls_verification_failed'
    except (OSError, TimeoutError):
        return False, 'connection_unavailable'


def probe_rows(role, request, *, local_node=None, local_dns=None):
    require(role in ROLES and request.get('role') == role, 'preflight_role_invalid')
    rows = [dict(row, expected=False) for row in request['targets']]
    if local_node:
        for name, port in (('local_node_ssh', 22), ('local_node_api', 6443)):
            rows.append({'name': name, 'host': ipv4(local_node), 'port': port, 'protocol': 'tcp', 'expected': False})
    for n, host in enumerate(local_dns or []):
        for protocol in ('tcp', 'udp'):
            rows.append({'name': f'local_dns_{protocol}_{n}', 'host': ipv4(host), 'port': 53,
                         'protocol': protocol, 'expected': False})
    if role == 'proxy':
        for row in rows:
            if row['name'] == 'upstream':
                row.update(expected=True, tls=True)
        rows.append({'name': 'upstream_wrong_port', 'host': UPSTREAM_IP, 'port': 9080,
                     'protocol': 'tcp', 'expected': False})
    else:
        rows.append({'name': 'scope_proxy', 'host': ipv4(request['proxy_ip']), 'port': 8080,
                     'protocol': 'tcp', 'expected': role == 'worker', 'challenge': role == 'worker'})
    require(1 <= len(rows) <= 48, 'preflight_target_limit')
    return rows


def run_probes(role, request, *, local_node=None, local_dns=None, probe=_socket_probe, monotonic=time.monotonic):
    started = monotonic()
    result = []
    for row in probe_rows(role, request, local_node=local_node, local_dns=local_dns):
        require(monotonic() - started < PROBE_SECONDS - 1, 'preflight_probe_timeout')
        connected, detail = probe(row, request['challenge'])
        result.append({**row, 'connected': connected, 'passed': connected is row['expected'], 'detail': detail})
    return {'schema_version': 1, 'scope_uid': request['scope_uid'], 'nonce': request['nonce'],
            'role': role, 'execution_image': os.environ.get('PPT_RUNTIME_WORKER_IMAGE'),
            'passed': all(row['passed'] for row in result), 'probes': result}


def _serve(challenge, ready, stopped, *, bind=('0.0.0.0', 8080)):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(bind)
        server.listen(16)
        server.settimeout(0.2)
        ready.set()
        while not stopped.is_set():
            try:
                stream, _ = server.accept()
            except socket.timeout:
                continue
            with stream:
                stream.settimeout(1)
                try:
                    if stream.recv(128) == challenge.encode('ascii'):
                        stream.sendall(challenge.encode('ascii'))
                except OSError:
                    pass


def probe_main(role):
    request = read_json(Path('/request'), 'request.json')
    require(dns_servers() == request['dns_servers'], 'preflight_dns_mismatch')
    stopped = threading.Event()
    # Python is PID 1 in the Pod. An explicit handler makes the kubelet's
    # SIGTERM stop the listener instead of waiting for the 30-second SIGKILL.
    previous = signal.signal(signal.SIGTERM, lambda *_: stopped.set()) if role == 'proxy' else None
    try:
        report = run_probes(role, request, local_node=os.getenv('PPT_PREFLIGHT_NODE_IP'), local_dns=request['dns_servers'])
        atomic_json(Path('/output/probe-result.json'), report)
        if role == 'proxy':
            # Ready means its probes have finished, so the controller can
            # schedule the worker while this credential-free listener lives.
            ready = threading.Event()
            thread = threading.Thread(target=_serve, args=(request['challenge'], ready, stopped), daemon=True)
            thread.start()
            try:
                require(ready.wait(2), 'preflight_proxy_unavailable')
                stopped.wait(PROBE_SECONDS + 60)
            finally:
                stopped.set()
                thread.join(2)
            require(not thread.is_alive(), 'preflight_proxy_not_stopped')
        return 0 if report['passed'] else 1
    finally:
        if role == 'proxy':
            signal.signal(signal.SIGTERM, previous)


class Preflight:
    def __init__(self, backend, targets, *, monotonic=time.monotonic):
        self.backend, self.targets, self.monotonic = backend, targets, monotonic

    def run(self):
        backend = self.backend
        started = self.monotonic()
        uid, nonce = str(uuid.uuid4()), str(uuid.uuid4())
        h = backend.register_sandbox_scope(uid)
        root = backend.config.data_root.absolute() / 'kubernetes-preflight' / uid
        root.mkdir(mode=0o700, parents=True)
        challenge = secrets.token_hex(24)
        names = {role: 'ppt-preflight-' + uid.replace('-', '') + '-' + role for role in ROLES}
        public = {'schema_version': 1, 'scope_uid': uid, 'passed': False, 'no_model_calls': True,
                  'writers_stopped': False, 'cleanup_complete': False, 'roles': {}, 'error_code': None}
        failure = None
        def remaining():
            value = SCHEDULING_SECONDS - (self.monotonic() - started)
            require(value > 0, 'preflight_scheduling_timeout')
            return value
        def launch(role, proxy_ip=None):
            folder = root / role
            folder.mkdir(mode=0o755)
            request_root, output = folder / 'request', folder / 'output'
            request_root.mkdir(mode=0o755)
            output.mkdir(mode=0o777)
            output.chmod(0o777)
            request = {'scope_uid': uid, 'nonce': nonce, 'role': role, 'challenge': challenge,
                       'targets': self.targets, 'proxy_ip': proxy_ip,
                       'dns_servers': sorted({r['host'] for r in self.targets if r['protocol'] == 'udp' and r['port'] == 53})}
            atomic_json(request_root / 'request.json', request)
            make_readonly(request_root)
            body = backend.pod(h, names[role], role, MODULE,
                               [(request_root, '/request', True), (output, '/output', False)],
                               env=[{'name': 'PPT_PREFLIGHT_NODE_IP', 'valueFrom': {'fieldRef': {'fieldPath': 'status.hostIP'}}}],
                               deadline=110, args=['--probe-role', role])
            if role == 'proxy':
                body['spec']['containers'][0]['readinessProbe'] = {'tcpSocket': {'port': 8080}, 'periodSeconds': 1}
            backend.create_resource(h, 'pods', body)
        try:
            for role in ROLES:
                remaining()
                backend.create_resource(h, 'networkpolicies', backend.policy(h, names[role] + '-net', role))
            launch('proxy')
            deadline = self.monotonic() + remaining()
            while True:
                record = backend._read(h)
                entry = next(e for e in record['resources'] if e['resource'] == 'pods' and e['role'] == 'proxy')
                obj = backend._get(h, entry)
                require(obj is not None, 'preflight_proxy_unavailable')
                status = obj.get('status', {})
                require(status.get('phase') not in ('Failed', 'Succeeded'), 'preflight_proxy_unavailable')
                rows = status.get('containerStatuses', [])
                if len(rows) == 1 and rows[0].get('ready') and rows[0].get('state', {}).get('running'):
                    proxy_ip = ipv4(status.get('podIP'))
                    break
                require(self.monotonic() < deadline, 'preflight_scheduling_timeout')
                backend.sleep(0.5)
            for role in ('worker', 'sandbox'):
                remaining()
                launch(role, proxy_ip)
            for role in ('worker', 'sandbox'):
                # Nonzero means isolation failed; read the exact report only
                # after stopping every Pod below, so preserve diagnostics.
                backend.wait_pod(h, names[role], remaining())
        except Exception as exc:
            failure = exc
        finally:
            try:
                backend.stop(h)
                proof = backend.prove_stopped(h)
                require(proof.all_writers_stopped, 'preflight_stop_unproven')
                public['writers_stopped'] = True
                authority = backend._read(h)
                stop_rows = [{'name': e['name'], 'api_uid': e['api_uid'], 'execution_image': e.get('execution_image'),
                              'stop_receipt': e.get('stop_receipt')} for e in authority['resources'] if e['resource'] == 'pods']
                atomic_json(root / 'stop-evidence.json', {'scope_uid': uid, 'resources': stop_rows})
                public['stopped_pod_uids'] = [row['api_uid'] for row in stop_rows]
                try:
                    for role in ROLES if failure is None else ():
                        value = read_json(root / role / 'output', 'probe-result.json', 128 * 1024)
                        expected = read_json(root / role / 'request', 'request.json')
                        pod_stop = next(row for row in stop_rows if row['name'] == names[role])
                        actual_host = ipv4(pod_stop['stop_receipt']['status']['hostIP'])
                        planned = probe_rows(role, expected,
                                             local_node=actual_host, local_dns=expected['dns_servers'])
                        require(value.get('scope_uid') == uid and value.get('nonce') == nonce and value.get('role') == role
                                and value.get('execution_image') == backend.execution_image(h)
                                and len(value.get('probes', [])) == len(planned), 'preflight_report_invalid')
                        for actual, plan in zip(value['probes'], planned):
                            require(all(actual.get(k) == v for k, v in plan.items())
                                    and type(actual.get('connected')) is bool
                                    and actual.get('passed') is (actual['connected'] is plan['expected']), 'preflight_report_invalid')
                        passed = all(row['passed'] for row in value['probes'])
                        public['roles'][role] = {'passed': passed, 'probe_count': len(planned),
                                               'failed_probes': [r['name'] for r in value['probes'] if not r['passed']]}
                finally:
                    backend.cleanup(h)
                    public['cleanup_complete'] = True
            except Exception as exc:
                failure = failure or exc
        if self.monotonic() - started > TOTAL_SECONDS:
            failure = ControlError('preflight_total_timeout')
        public['passed'] = (failure is None and public['writers_stopped'] and public['cleanup_complete']
                            and set(public['roles']) == set(ROLES) and all(r['passed'] for r in public['roles'].values()))
        if failure:
            code = failure.code if isinstance(failure, ControlError) else 'preflight_failed'
            public['error_code'] = code if re.fullmatch(r'[a-z_]{1,80}', code) else 'preflight_failed'
        elif not public['passed']:
            public['error_code'] = 'preflight_network_isolation_failed'
        atomic_json(root / 'report.json', public)
        root.chmod(0o700)
        return public


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe-role', choices=ROLES, help=argparse.SUPPRESS)
    parser.add_argument('--node-ip', action='append', default=[], help='Additional actual node/control-plane IPv4 to test')
    args = parser.parse_args()
    if args.probe_role:
        return probe_main(args.probe_role)
    try:
        config, settings = RuntimeConfig.env(), KubeSettings.env()
        settings = replace(settings, startup_timeout=60, stop_timeout=STOP_OBSERVATION_SECONDS, poll_seconds=0.25)
        backend = KubernetesBackend(config, settings)
        result = Preflight(backend, deployment_targets(config, api_url=backend.client.api_server, node_ips=args.node_ip)).run()
    except Exception as exc:
        code = exc.code if isinstance(exc, ControlError) else 'preflight_configuration_unavailable'
        result = {'passed': False, 'no_model_calls': True, 'error_code': code}
    print(json.dumps(result, separators=(',', ':')), flush=True)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
