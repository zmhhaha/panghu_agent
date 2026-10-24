"""Offline lifecycle/failure tests; these never claim actual CNI isolation."""
import json
from pathlib import Path
import socket
import threading

import pytest

from ppt_service.control.execution import ControlError
from ppt_service.control.kubernetes import FINALIZER, ROLE
from ppt_service.control import kubernetes_preflight as preflight
from ppt_service.control.staging import atomic_json
from test_kubernetes import runtime


def targets():
    return [
        {'name': 'upstream', 'host': '62.234.50.20', 'port': 443, 'protocol': 'tcp'},
        {'name': 'external', 'host': '1.1.1.1', 'port': 443, 'protocol': 'tcp'},
        {'name': 'metadata', 'host': '169.254.169.254', 'port': 80, 'protocol': 'tcp'},
        {'name': 'other_service_api', 'host': '10.96.0.1', 'port': 443, 'protocol': 'tcp'},
        {'name': 'node_ssh_0', 'host': '192.168.137.101', 'port': 22, 'protocol': 'tcp'},
        {'name': 'node_api_0', 'host': '192.168.137.101', 'port': 6443, 'protocol': 'tcp'},
        {'name': 'database_0', 'host': '10.96.0.20', 'port': 5432, 'protocol': 'tcp'},
        {'name': 'dns_tcp_0', 'host': '10.96.0.10', 'port': 53, 'protocol': 'tcp'},
        {'name': 'dns_udp_0', 'host': '10.96.0.10', 'port': 53, 'protocol': 'udp'},
    ]


def fixture_transport(runtime, monkeypatch, *, leak=None, mutate=None):
    config, settings, api, backend, attempt = runtime
    monkeypatch.setenv('PPT_RUNTIME_WORKER_IMAGE', settings.worker_image)
    original_terminal = api.terminal
    def terminal(obj, exit_code=0):
        original_terminal(obj, exit_code)
        obj['status']['hostIP'] = '192.168.137.101'
    api.terminal = terminal
    def source(obj, mount):
        row = next(m for m in obj['spec']['containers'][0]['volumeMounts'] if m['mountPath'] == mount)
        return config.data_root / row['subPath'].removeprefix(settings.pvc_data_prefix + '/')
    def on_pod(obj):
        role = obj['metadata']['labels'][ROLE]
        request = json.loads((source(obj, '/request') / 'request.json').read_text())
        def network(row, challenge):
            # Deliberate fixture outcomes, independent of row.expected.
            connected = (role == 'proxy' and row['name'] == 'upstream') or (role == 'worker' and row['name'] == 'scope_proxy')
            if leak == (role, row['name']):
                connected = True
            return connected, 'fixture_observation'
        result = preflight.run_probes(role, request, local_node='192.168.137.101',
                                      local_dns=request['dns_servers'], probe=network)
        if mutate:
            mutate(role, result)
        atomic_json(source(obj, '/output') / 'probe-result.json', result)
        obj['status']['hostIP'] = '192.168.137.101'
        if role != 'proxy':
            api.terminal(obj, 0 if result['passed'] else 1)
    api.on_pod = on_pod
    return preflight.Preflight(backend, targets())


def test_no_model_lifecycle_keeps_actual_uid_stop_receipts_before_cleanup(runtime, monkeypatch):
    config, settings, api, backend, attempt = runtime
    result = fixture_transport(runtime, monkeypatch).run()
    assert result['passed'] and result['writers_stopped'] and result['cleanup_complete']
    assert result['no_model_calls'] and len(result['stopped_pod_uids']) == 3
    assert not api.objects
    assert not any(resource == 'secrets' for _, resource, *_ in api.calls)
    pods = [body for method, resource, name, body, query in api.calls if method == 'POST' and resource == 'pods']
    for pod in pods:
        assert pod['spec']['containers'][0]['args'][:2] == ['-m', preflight.MODULE]
        assert not pod['spec']['automountServiceAccountToken']
        assert not any('secretKeyRef' in json.dumps(row) for row in pod['spec']['containers'][0]['env'])
    root = config.data_root / 'kubernetes-preflight' / result['scope_uid']
    evidence = json.loads((root / 'stop-evidence.json').read_text())
    assert {row['api_uid'] for row in evidence['resources']} == set(result['stopped_pod_uids'])
    assert all(row['stop_receipt']['terminated'] and row['execution_image'] == settings.worker_image for row in evidence['resources'])


@pytest.mark.parametrize('role,name', [('worker', 'local_node_ssh'), ('worker', 'dns_udp_0'),
                                     ('proxy', 'database_0'), ('sandbox', 'scope_proxy')])
def test_real_probe_leak_result_cannot_report_success(runtime, monkeypatch, role, name):
    result = fixture_transport(runtime, monkeypatch, leak=(role, name)).run()
    assert not result['passed'] and result['error_code'] == 'preflight_network_isolation_failed'
    assert result['writers_stopped'] and result['cleanup_complete']
    assert name in result['roles'][role]['failed_probes']


@pytest.mark.parametrize('fault', ['omit_local_host', 'passed_flag', 'image', 'identity'])
def test_forged_or_incomplete_probe_json_fails_and_still_cleans(runtime, monkeypatch, fault):
    def mutate(role, report):
        if role != 'worker':
            return
        if fault == 'omit_local_host':
            report['probes'] = [r for r in report['probes'] if not r['name'].startswith('local_node_')]
        elif fault == 'passed_flag':
            report['probes'][0]['connected'] = True
            report['probes'][0]['passed'] = True
        elif fault == 'image':
            report['execution_image'] = 'forged'
        else:
            report['scope_uid'] = str(attempt.uid)
    attempt = runtime[-1]
    result = fixture_transport(runtime, monkeypatch, mutate=mutate).run()
    assert not result['passed'] and result['error_code'] == 'preflight_report_invalid'
    assert result['writers_stopped'] and result['cleanup_complete']
    assert not runtime[2].objects


def test_missing_physical_stop_retains_finalizers_and_never_cleans(runtime, monkeypatch):
    runner = fixture_transport(runtime, monkeypatch)
    api = runtime[2]
    api.terminate_on_delete = False
    result = runner.run()
    assert not result['passed'] and not result['writers_stopped'] and not result['cleanup_complete']
    assert all(FINALIZER in obj['metadata']['finalizers'] for (kind, _), obj in api.objects.items() if kind == 'pods')
    assert not any(call[0] == 'PATCH' for call in api.calls)


def test_configuration_resolves_public_endpoints_without_credentials(runtime, monkeypatch):
    mapping = {'postgres.ppt-service.svc': ['10.96.0.20'], '192.168.137.101': ['192.168.137.101']}
    monkeypatch.setattr(preflight, 'resolve_v4', lambda host: mapping[host])
    value = preflight.deployment_targets(runtime[0], db_url='postgresql://user:private-db-password@postgres.ppt-service.svc/db',
                                         api_url='https://192.168.137.101:6443', dns=['10.96.0.10'], service_ip='10.96.0.1')
    assert 'private-db-password' not in json.dumps(value) and 'user' not in json.dumps(value)
    assert any(row['protocol'] == 'udp' and row['port'] == 53 for row in value)
    assert any(row['name'] == 'node_ssh_0' and row['port'] == 22 for row in value)


def test_probe_budget_expires_instead_of_self_reporting_success():
    request = {'role': 'sandbox', 'targets': targets(), 'proxy_ip': '10.42.1.2', 'challenge': 'nonce'}
    clock = iter((0, 31))
    with pytest.raises(ControlError, match='preflight_probe_timeout'):
        preflight.run_probes('sandbox', request, monotonic=lambda: next(clock))


def test_scope_proxy_probe_requires_actual_echo_and_not_just_open_port():
    challenge = 'real-scope-challenge'
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        def echo():
            stream, _ = listener.accept()
            with stream:
                stream.sendall(stream.recv(128))
        thread = threading.Thread(target=echo)
        thread.start()
        connected, detail = preflight._socket_probe({'protocol': 'tcp', 'host': '127.0.0.1',
                                                    'port': listener.getsockname()[1], 'challenge': True}, challenge)
        thread.join(2)
    assert connected is True and detail == 'scope_challenge'


def test_upstream_probe_uses_sni_and_default_verified_tls(monkeypatch):
    observed = []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def settimeout(self, value): pass
    class Context:
        def wrap_socket(self, stream, *, server_hostname):
            observed.append(server_hostname)
            return Connection()
    monkeypatch.setattr(preflight.socket, 'create_connection', lambda *args, **kwargs: Connection())
    monkeypatch.setattr(preflight.ssl, 'create_default_context', lambda: Context())
    assert preflight._socket_probe({'protocol': 'tcp', 'host': '62.234.50.20', 'port': 443, 'tls': True}, 'nonce') == (True, 'verified_tls')
    assert observed == ['custom-llm-proxy.panghuer.top']
