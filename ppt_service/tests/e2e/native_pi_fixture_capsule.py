"""Owned-namespace native Pi stop probe. The model is a labelled HTTP fixture.

This is an administrator-only candidate. It does not admit production traffic,
load production credentials, call a supplier, produce a PPT, or grant acceptance.
The worker imports its image-owned Agent modules; only this harness is mounted.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import socket
import sys
import threading
import time
from uuid import uuid4


def require(value, code):
    if not value:
        raise RuntimeError(code)


def emit(kind, **values):
    print(json.dumps({'kind': kind, **values}, sort_keys=True), flush=True)


def token():
    value = Path('/run/agent-probe-bootstrap/token').read_text().strip()
    require(len(value) == 96 and all(c in '0123456789abcdef' for c in value), 'fixture_token_invalid')
    return value


def fixture():
    """Two listening ports let the worker positively measure its port deny rule."""
    key, lock, counts = token(), threading.Lock(), {'connectivity': 0, 'native': 0}
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_args: stopping.set())

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path != '/health':
                self.send_error(404)
                return
            raw = b'{"fixture":true}'
            self.send_response(200); self.send_header('Content-Length', str(len(raw)))
            self.end_headers(); self.wfile.write(raw)

        def do_POST(self):
            require(self.path == '/v1/responses', 'fixture_route_invalid')
            require(hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + key),
                    'fixture_auth_invalid')
            length = int(self.headers.get('Content-Length', '0'))
            require(1 <= length <= 4 * 1024**2, 'fixture_request_budget')
            raw = self.rfile.read(length)
            require(len(raw) == length, 'fixture_body_incomplete')
            body = json.loads(raw)
            connectivity = type(body.get('input')) is str and 'CANDIDATE_MODEL_OK' in body['input']
            with lock:
                kind = 'connectivity' if connectivity else 'native'
                counts[kind] += 1
                emit('fixture_request', category=kind, count=counts[kind], body_sha256=hashlib.sha256(raw).hexdigest(),
                     model=body.get('model'), stream=body.get('stream') is True,
                     actual_supplier_requests=0)
            if connectivity:
                value = dict(id='resp_fixture_connectivity', object='response', status='completed',
                    model=body['model'], output=[dict(type='message', role='assistant',
                    content=[dict(type='output_text', text='CANDIDATE_MODEL_OK')])])
                response = json.dumps(value).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(response)))
                self.end_headers(); self.wfile.write(response)
                return
            # Deliberately hold before any model response or tool side effect.
            # The process/UID fence must stop native Pi while this request waits.
            stopping.wait(180)

    servers = [ThreadingHTTPServer(('0.0.0.0', port), Handler) for port in (8080, 8081)]
    for server in servers:
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
    emit('fixture_ready', ports=[8080, 8081], actual_supplier_requests=0,
         boundary='deterministic connectivity response and held native request; no model synthesis')
    stopping.wait(590)
    for server in servers:
        server.shutdown()
        server.server_close()
    emit('fixture_stopped', actual_supplier_requests=0, counts=counts)


def connectivity(origin, key):
    import httpx
    host = origin.split('://', 1)[1].split(':', 1)[0]
    with httpx.Client(trust_env=False, timeout=5) as client:
        response = client.get(origin + '/health')
        require(response.status_code == 200 and response.json() == {'fixture': True}, 'fixture_not_reachable')
        body = dict(model='native-pi-fixture', store=False, max_output_tokens=32, input='CANDIDATE_MODEL_OK')
        response = client.post(origin + '/v1/responses', headers={'Authorization': 'Bearer ' + key}, json=body)
        from agent_service.deploy.acceptance_probe import response_completed
        require(response_completed(response), 'fixture_connectivity_protocol_failed')
    denied = []
    # 8081 is positively known to listen on the SAME fixture Pod. It is omitted
    # from the namespace NetworkPolicy; an unopened arbitrary port is insufficient.
    for address, port, label in ((host, 8081, 'fixture_listening_denied_port'),
                                  ('1.1.1.1', 443, 'external_https')):
        try:
            connection = socket.create_connection((address, port), timeout=2)
        except OSError:
            denied.append(label)
        else:
            connection.close()
            raise RuntimeError('fixture_egress_policy_not_enforced')
    return {'status': 200, 'api_mode': 'responses', 'fixture_only': True,
            'positive_allow_observed': True, 'negative_connection_checks': denied,
            'external_probe_has_no_positive_control': True}


def worker(args):
    from agent_service.catalog import generate
    from agent_service.contracts import ExecutionEnvelope, JobRequest, WorkerResult, canonical_bytes, verify_receipt
    from agent_service.deploy.acceptance_probe import (build_candidate_driver, candidate_config,
        fixture as task_fixture, isolation_preflight, CONTROL, WORK)
    from agent_service.linux_stop import LinuxUidStopController
    from agent_service.stop_group import StopGroup
    from agent_service.store import Store
    from agent_service.worker_api import WorkerHost
    from agent_service.worker_entry import CapsuleScope, HostSecrets

    require(os.getpid() == 1 and os.geteuid() == 0, 'private_root_controller_required')
    require(args.case in {'cancel', 'native_crash'}, 'case_invalid')
    CONTROL.mkdir(mode=0o700, parents=True, exist_ok=True); CONTROL.chmod(0o700)
    WORK.mkdir(mode=0o711, parents=True, exist_ok=True); WORK.chmod(0o711)
    key = token()
    public, hosts, summary = generate(pi_image=args.image, runtime_revision='native-fixture-20261104',
        model='native-pi-fixture', provider='fixture-only', model_proxy_url=args.origin + '/v1',
        upstream_base_url=args.origin + '/v1')
    config = next(value for value in hosts.values() if value['profile']['name'] == 'ppt.plan_initial.v1')
    config, policy, binding = candidate_config(config)
    require(summary['routes_enabled'] is False and summary['acceptance_created'] is False,
            'fixture_must_not_enable_routes')
    store = Store('sqlite:///' + str(CONTROL / 'fixture-coordination.sqlite'), allow_sqlite=True)
    store.initialize()
    task, files = task_fixture(policy.name)
    refs = []
    for name, raw in sorted(files.items()):
        item = store.put_input('fixture-only', 'operator', name, raw)
        refs.append({'input_id': item['input_id'], 'sha256': item['sha256']})
    request = JobRequest(profile=policy.name, profile_revision=policy.revision,
        business_ref='fixture-native:' + str(uuid4()), task=task, inputs=refs, timeout_seconds=90)
    store.submit('fixture-only', 'operator', 'one-native-attempt', request.model_dump(mode='json'), policy.pins())
    claimed = store.claim(lease_seconds=120, provisioning={'pi': 'kubernetes_per_attempt'})
    with store.engine.connect() as db:
        envelope = store._worker_envelope(db, claimed)
    require(isinstance(envelope, ExecutionEnvelope), 'real_envelope_required')
    scope = CapsuleScope(attempt_id=envelope.attempt_id, pod_uid=os.environ['AGENT_POD_UID'],
        request_sha256=envelope.request_sha256, agent_uid=args.uid, preview_uid=args.uid + 1,
        execution_binding_sha256=envelope.execution_binding.sha256)
    credentials = HostSecrets(worker_token=os.urandom(32).hex(), receipt_key_hex=os.urandom(32).hex(),
        model_proxy_token=key, model_proxy_attempt_id=envelope.attempt_id,
        model_proxy_request_sha256=envelope.request_sha256, model_proxy_expires_at=envelope.deadline_at + 1)
    observations = {'model_round_trip': connectivity(args.origin, key), 'isolation': isolation_preflight(scope)}
    stopper = StopGroup(*[LinuxUidStopController(CONTROL / name, scope.attempt_id, scope.pod_uid,
        scope.request_sha256, agent_uid=uid) for name, uid in
        (('engine-scope', scope.agent_uid), ('preview-scope', scope.preview_uid))])
    stopper.initialize()
    driver = build_candidate_driver(config, credentials, scope, stopper, observations)
    host = WorkerHost(replace(policy, enabled=True), driver, CONTROL, WORK,
                      bytes.fromhex(credentials.receipt_key_hex.get_secret_value()))
    host.initialize()
    sources = {}
    for name in ('agent_service.worker_api', 'agent_service.runtime.adapter', 'agent_service.engines',
                 'agent_service.domain_runtime', 'agent_service.linux_stop', 'agent_service.profiles.ppt'):
        import importlib
        module = importlib.import_module(name)
        path = Path(module.__file__)
        require(str(path).startswith('/opt/agent-service/'), 'image_owned_module_required')
        sources[name] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    try:
        first = host.start(envelope)
        second = host.start(envelope)
        require(first['request_sha256'] == second['request_sha256'] == envelope.request_sha256,
                'duplicate_start_identity_changed')
        emit('native_admitted', case=args.case, attempt_id=scope.attempt_id, pod_uid=scope.pod_uid,
             request_sha256=scope.request_sha256, agent_uid=scope.agent_uid, preview_uid=scope.preview_uid,
             binding_sha256=scope.execution_binding_sha256, observations=observations,
             image_sources=sources, duplicate_start_submitted=True, actual_supplier_requests=0)
        # The operator invokes the owned Pod's trigger mode only after seeing a
        # genuine model POST in the fixture log and verifying this exact UID.
        end = time.monotonic() + 125
        triggered = False
        while host.status(scope.attempt_id)['state'] not in {
                'completed', 'failed', 'cancelled', 'timed_out', 'quarantined', 'needs_input'}:
            if (CONTROL / 'cancel-request').exists() and not triggered:
                host.cancel(scope.attempt_id)
                triggered = True
            require(time.monotonic() < end, 'native_stop_probe_deadline')
            time.sleep(.1)
        result = host.result(scope.attempt_id)
        proof = stopper.prove_stopped(scope.attempt_id)
        require(result['state'] in {'failed', 'cancelled'} and proof.all_writers_stopped is True,
                'native_stop_not_confirmed')
        require(result.get('stop_receipt') is not None, 'signed_native_stop_receipt_required')
        require(verify_receipt(envelope, WorkerResult.model_validate(result),
                bytes.fromhex(credentials.receipt_key_hex.get_secret_value()), time.time()),
                'native_stop_signature_invalid')
        (CONTROL / 'result.private.json').write_bytes(canonical_bytes(result))
        emit('native_stopped', case=args.case, state=result['state'], error_code=result.get('error_code'),
            attempt_id=scope.attempt_id, request_sha256=scope.request_sha256,
            stop_resources=list(proof.resource_ids), stopped=True,
            result=result, stop_signature_verified=True,
            result_sha256=hashlib.sha256(canonical_bytes(result)).hexdigest(), actual_supplier_requests=0)
    finally:
        try:
            host.close()
        finally:
            stopper.stop(scope.attempt_id)


def trigger(args):
    require(args.case in {'cancel', 'native_crash'} and args.uid >= 65536, 'trigger_scope_invalid')
    # Kernel identity is observed inside the exact operator-bound Pod. Signals
    # use pidfd, and a second credential check precedes delivery.
    from agent_service.runtime.adapter import COMPACTOR_PATH
    from agent_service.worker_entry import NODE, PI_CLI

    def observation(pid):
        directory = Path('/proc') / str(pid)
        status = dict(line.split(':', 1) for line in (directory / 'status').read_text().splitlines() if ':' in line)
        ids = [int(value) for value in status['Uid'].split()]
        gids = [int(value) for value in status['Gid'].split()]
        groups = [int(value) for value in status['Groups'].split()]
        require(ids == [args.uid] * 4 and gids == [args.uid] * 4 and not groups,
                'native_credentials_not_exact')
        require(all(int(status[field].strip(), 16) == 0 for field in ('CapEff', 'CapPrm', 'CapInh', 'CapAmb'))
                and status['NoNewPrivs'].strip() == '1' and status['Seccomp'].strip() == '2',
                'native_confinement_missing')
        require(not status['State'].strip().startswith(('Z', 'X')), 'native_not_live')
        executable = os.readlink(directory / 'exe')
        command = (directory / 'cmdline').read_bytes()
        argv = command.rstrip(b'\0').split(b'\0')
        arguments = argv[1:]
        if arguments and arguments[0] == b'--import':
            require(len(arguments) >= 3 and arguments[1] == os.fsencode(COMPACTOR_PATH.resolve().as_uri()),
                    'image_owned_event_preload_required')
            arguments = arguments[2:]
        require(executable == str(Path(NODE).resolve()) and arguments
                and arguments[0] == os.fsencode(PI_CLI)
                and b'fixture-only' in arguments and b'native-pi-fixture' in arguments,
                'image_owned_native_node_required')
        # comm can contain spaces/parentheses. starttime is field 22 after its
        # final closing parenthesis, not a whitespace split of the whole stat.
        fields = (directory / 'stat').read_text().rsplit(')', 1)[1].split()
        return dict(pid=pid, uid=ids, gid=gids, groups=groups,
            starttime=int(fields[19]), executable=executable,
            command_sha256=hashlib.sha256(command).hexdigest(),
            caps_empty=True, no_new_privileges=True, seccomp_filtering=True)

    rows = []
    for directory in Path('/proc').iterdir():
        if not directory.name.isdecimal():
            continue
        try:
            status = dict(line.split(':', 1) for line in (directory / 'status').read_text().splitlines() if ':' in line)
            ids = [int(value) for value in status['Uid'].split()]
            if args.uid not in ids or status['State'].strip().startswith(('Z', 'X')):
                continue
            if Path(os.readlink(directory / 'exe')).name == 'node':
                rows.append(observation(int(directory.name)))
        except FileNotFoundError:
            continue
    require(rows, 'real_native_node_not_present')
    if args.case == 'cancel':
        fd = os.open('/run/agent-control/cancel-request', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    else:
        require(len(rows) <= 16, 'native_process_budget')
        for row in rows:
            pid = row['pid']
            require(observation(pid) == row, 'native_identity_changed_before_pidfd')
            descriptor = os.pidfd_open(pid)
            try:
                require(observation(pid) == row, 'native_identity_changed_after_pidfd')
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            finally:
                os.close(descriptor)
    emit('native_trigger', case=args.case, uid=args.uid, nodes=rows)


def cleanup_trigger(fixture_only):
    """Fixed graceful trigger; no caller-supplied signal, PID or command."""
    command = Path('/proc/1/cmdline').read_bytes().rstrip(b'\0').split(b'\0')
    require(len(command) >= 3 and command[1:3] == [os.fsencode(__file__),
            b'fixture' if fixture_only else b'worker'], 'fixed_pid1_harness_required')
    executable = os.readlink('/proc/1/exe')
    require(executable == str(Path(sys.executable).resolve()), 'fixed_controller_executable_required')
    if fixture_only:
        descriptor = os.pidfd_open(1)
        try:
            require(Path('/proc/1/cmdline').read_bytes().rstrip(b'\0').split(b'\0') == command
                    and os.readlink('/proc/1/exe') == executable, 'fixture_pid1_identity_changed')
            signal.pidfd_send_signal(descriptor, signal.SIGTERM)
        finally:
            os.close(descriptor)
    else:
        try:
            fd = os.open('/run/agent-control/cancel-request', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['fixture', 'worker', 'trigger', 'stop-fixture', 'stop-worker'])
    parser.add_argument('--case', choices=['cancel', 'native_crash'])
    parser.add_argument('--uid', type=int)
    parser.add_argument('--image')
    parser.add_argument('--origin')
    args = parser.parse_args()
    if args.mode == 'fixture':
        fixture()
    elif args.mode in {'stop-fixture', 'stop-worker'}:
        cleanup_trigger(args.mode == 'stop-fixture')
    elif args.mode == 'trigger':
        trigger(args)
    else:
        worker(args)


if __name__ == '__main__':
    main()
