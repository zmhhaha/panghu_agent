"""Offline lifecycle/failure tests; these never claim actual CNI isolation."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

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


def test_idle_proxy_listener_stops_without_another_incoming_connection():
    ready, stopped = threading.Event(), threading.Event()
    thread = threading.Thread(target=preflight._serve, args=('challenge', ready, stopped),
                              kwargs={'bind': ('127.0.0.1', 0)}, daemon=True)
    thread.start()
    try:
        assert ready.wait(2)
        stopped.set()
        thread.join(2)
        assert not thread.is_alive()
    finally:
        stopped.set()
        thread.join(2)


def test_proxy_sigterm_handler_stops_listener_and_preserves_finished_probe_report(monkeypatch):
    request = {'dns_servers': [], 'challenge': 'scope-challenge'}
    report = {'passed': True, 'scope_uid': 'completed-probe'}
    written, handlers, stop_events, observed_shutdown = [], [], [], []
    listener_ready = threading.Event()
    previous = object()
    original_serve = preflight._serve
    monkeypatch.setattr(preflight, 'read_json', lambda *args: request)
    monkeypatch.setattr(preflight, 'dns_servers', lambda: [])
    monkeypatch.setattr(preflight, 'run_probes', lambda *args, **kwargs: dict(report))
    monkeypatch.setattr(preflight, 'atomic_json', lambda path, value: written.append(value))
    monkeypatch.setattr(preflight.signal, 'signal', lambda number, handler: handlers.append((number, handler)) or previous)
    def serve(challenge, ready, stopped):
        stop_events.append(stopped)
        class Ready:
            def set(self):
                ready.set()
                listener_ready.set()
        original_serve(challenge, Ready(), stopped, bind=('127.0.0.1', 0))
    monkeypatch.setattr(preflight, '_serve', serve)
    def terminate():
        if listener_ready.wait(3):
            try:
                if handlers and callable(handlers[0][1]):
                    handlers[0][1](signal.SIGTERM, None)
                observed_shutdown.append(stop_events[0].is_set())
            finally:
                # Keep a broken signal-handler regression bounded as well.
                stop_events[0].set()
    cancellation = threading.Thread(target=terminate, daemon=True)
    cancellation.start()
    assert preflight.probe_main('proxy') == 0
    cancellation.join(2)
    assert not cancellation.is_alive()
    assert written == [report]
    assert observed_shutdown == [True]
    assert handlers[0][0] == signal.SIGTERM and callable(handlers[0][1])
    assert handlers[-1] == (signal.SIGTERM, previous)


def test_main_stop_observation_outlasts_kubelet_grace_without_using_total_budget(runtime, monkeypatch, capsys):
    config, settings, _, backend, attempt = runtime
    grace = backend.pod(backend.reconcile(attempt), 'budget-probe', 'proxy', preflight.MODULE, [])['spec']['terminationGracePeriodSeconds']
    observed = []
    monkeypatch.setattr(preflight.RuntimeConfig, 'env', classmethod(lambda cls: config))
    monkeypatch.setattr(preflight.KubeSettings, 'env', classmethod(lambda cls: settings))
    def transport(config, selected):
        observed.append(selected)
        return SimpleNamespace(client=SimpleNamespace(api_server='https://192.168.137.101:6443'))
    monkeypatch.setattr(preflight, 'KubernetesBackend', transport)
    monkeypatch.setattr(preflight, 'deployment_targets', lambda *args, **kwargs: targets())
    monkeypatch.setattr(preflight, 'Preflight', lambda *args: SimpleNamespace(run=lambda: {'passed': True}))
    monkeypatch.setattr(sys, 'argv', ['preflight'])
    assert preflight.main() == 0
    assert observed[0].stop_timeout >= grace + 10
    assert preflight.SCHEDULING_SECONDS + observed[0].stop_timeout <= preflight.TOTAL_SECONDS - 5
    assert json.loads(capsys.readouterr().out)['passed'] is True


@pytest.mark.skipif(os.name != 'posix', reason='Real Unix SIGTERM delivery requires POSIX')
def test_proxy_process_handles_real_sigterm_and_keeps_probe_report(tmp_path):
    report, ready = tmp_path / 'probe.json', tmp_path / 'ready'
    code = '''
import signal
from pathlib import Path
from ppt_service.control import kubernetes_preflight as p
report, ready_file = Path(REPORT_PATH), Path(READY_PATH)
p.read_json = lambda *args: {'dns_servers': [], 'challenge': 'scope-challenge'}
p.dns_servers = lambda: []
p.run_probes = lambda *args, **kwargs: {'passed': True, 'scope_uid': 'completed-probe'}
original_write, original_serve = p.atomic_json, p._serve
p.atomic_json = lambda path, value: original_write(report, value)
def serve(challenge, ready, stopped):
    class Ready:
        def set(self):
            ready.set()
            ready_file.write_text('listening')
    original_serve(challenge, Ready(), stopped, bind=('127.0.0.1', 0))
p._serve = serve
raise SystemExit(p.probe_main('proxy'))
'''.replace('REPORT_PATH', repr(str(report))).replace('READY_PATH', repr(str(ready)))
    process = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), 'proxy failed to reach its actual listening state'
        before = report.read_bytes()
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=3) == 0
        assert report.read_bytes() == before
        assert json.loads(before)['passed'] is True
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        process.communicate(timeout=3)


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
