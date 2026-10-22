#!/usr/bin/env python3
"""Opt-in real Pi / scientific gateway mechanism probe; never scientific research.

No network occurs on import, --help, or --self-test. Only the isolated root
controller retains the real gateway credential; Pi receives a loopback dummy.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import stat
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
GATEWAY_HOST = 'llm-service.llm.svc.cluster.local'
GATEWAY_PATH = '/v1/chat/completions'
MODEL = 'llm-trusted'
MARKER = 'SCIENTIFIC_PI_REAL_TOOL_OK'
COMMAND = "printf 'SCIENTIFIC_PI_REAL_TOOL_OK\\n' | tee tool-roundtrip.txt"
DUMMY_KEY = 'scientific-pi-loopback-dummy-not-a-secret'
UID = 300000
LIMIT = 1024 * 1024
PROMPT = (
    'Operational tool protocol test only. Do not conduct literature research. '
    'Do not search, use network tools, read environment variables, credentials, '
    'private data, or any existing files. Do not send mail. '
    'Call the bash tool exactly once with this exact command and no extra commands:\n'
    + COMMAND + '\nThen respond briefly with done. Do not call any other tools.'
)


class ProbeFailure(Exception):
    def __init__(self, code):
        self.code = code


def require(condition, code):
    if not condition:
        raise ProbeFailure(code)


class Evidence:
    """Retain no messages, tool arguments, tool IDs, headers or response text."""
    def __init__(self):
        self.requests = 0
        self.second_tool_marker = False
        self.command_exact = False
        self.models = set()
        self.names = {}
        self.done = 0
        self.error = False
        self.lock = threading.Lock()

    def observe_request(self, request):
        require(isinstance(request, dict), 'request_invalid')
        require(request.get('model') == MODEL and request.get('stream') is True,
                'request_invalid')
        messages = request.get('messages')
        require(isinstance(messages, list), 'request_invalid')
        with self.lock:
            self.requests += 1
            index = self.requests
            require(index <= 2, 'request_limit')
            if index == 2:
                self.second_tool_marker = any(
                    isinstance(item, dict) and item.get('role') == 'tool'
                    and MARKER in json.dumps(item.get('content', ''), ensure_ascii=False)
                    for item in messages)
                calls = [call for item in messages if isinstance(item, dict)
                         and item.get('role') == 'assistant'
                         for call in item.get('tool_calls', [])]
                if len(calls) == 1:
                    function = calls[0].get('function', {})
                    arguments = json.loads(function.get('arguments', '{}'))
                    self.command_exact = (function.get('name') == 'bash'
                                          and arguments.get('command') == COMMAND
                                          and set(arguments) <= {'command', 'timeout'})
        return index

    def observe_data(self, value, request_index):
        if value == '[DONE]':
            self.done += 1
            return
        data = json.loads(value)
        require(isinstance(data, dict) and 'error' not in data, 'upstream_protocol')
        model = data.get('model')
        if model is not None:
            require(isinstance(model, str) and re.fullmatch(r'[A-Za-z0-9_.:/@+-]{1,128}', model),
                    'upstream_protocol')
            self.models.add(model)
            require(len(self.models) <= 8, 'upstream_protocol')
        choices = data.get('choices', [])
        require(isinstance(choices, list) and len(choices) <= 8, 'upstream_protocol')
        for choice in choices:
            delta = choice.get('delta', {})
            for tool in delta.get('tool_calls', []):
                name = tool.get('function', {}).get('name')
                if name is None:
                    continue
                require(isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', name),
                        'upstream_protocol')
                key = (request_index, choice.get('index', 0), tool.get('index', 0))
                require(all(type(part) is int for part in key), 'upstream_protocol')
                self.names[key] = self.names.get(key, '') + name
                require(len(self.names) <= 16 and len(self.names[key]) <= 80, 'upstream_protocol')


class SSEReader:
    """Bounded transient SSE parser; only safe metadata survives feed()."""
    def __init__(self, evidence, index):
        self.evidence, self.index = evidence, index
        self.pending = b''
        self.data = []
        self.total = 0

    def feed(self, chunk):
        self.total += len(chunk)
        require(self.total <= LIMIT, 'response_limit')
        self.pending += chunk
        while b'\n' in self.pending:
            line, self.pending = self.pending.split(b'\n', 1)
            line = line.rstrip(b'\r')
            if not line:
                if self.data:
                    self.evidence.observe_data(b'\n'.join(self.data).decode('utf-8'), self.index)
                    self.data.clear()
            elif line.startswith(b'data:'):
                self.data.append(line[5:].lstrip(b' '))

    def finish(self):
        require(not self.pending and not self.data, 'upstream_protocol')


class GatewayProxy:
    """IPv4 loopback only; one fixed upstream, no redirects or generic proxying."""
    def __init__(self, key, evidence, deadline):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def reject(self):
                body = b'{"error":{"message":"probe request unavailable"}}'
                self.send_response(502)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

            def do_GET(self):
                self.reject()

            do_CONNECT = do_GET
            do_PUT = do_GET
            do_DELETE = do_GET
            do_PATCH = do_GET
            do_HEAD = do_GET

            def do_POST(self):
                connection = None
                started = False
                self.close_connection = True
                try:
                    self.connection.settimeout(90)
                    require(self.path == GATEWAY_PATH, 'request_path')
                    require(self.headers.get('Authorization') == 'Bearer ' + DUMMY_KEY,
                            'request_auth')
                    require(not self.headers.get('Transfer-Encoding'), 'request_invalid')
                    lengths = self.headers.get_all('Content-Length', [])
                    require(len(lengths) == 1, 'request_invalid')
                    length = int(lengths[0])
                    require(0 < length <= LIMIT, 'request_limit')
                    body = self.rfile.read(length)
                    require(len(body) == length and key.encode() not in body, 'request_invalid')
                    index = evidence.observe_request(json.loads(body))
                    remaining = deadline - time.monotonic()
                    require(remaining > 0, 'deadline')
                    connection = http.client.HTTPConnection(GATEWAY_HOST, 80,
                                                            timeout=min(90, remaining))
                    # Never inherit outbound proxy variables or follow redirects.
                    connection.request('POST', GATEWAY_PATH, body=body, headers={
                        'Content-Type': 'application/json', 'Accept': 'text/event-stream',
                        'Authorization': 'Bearer ' + key})
                    response = connection.getresponse()
                    require(response.status == 200, 'upstream_status')
                    require(response.getheader('Content-Type', '').split(';')[0].strip()
                            == 'text/event-stream', 'upstream_protocol')
                    parser = SSEReader(evidence, index)
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Cache-Control', 'no-cache')
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    started = True
                    while True:
                        remaining = deadline - time.monotonic()
                        require(remaining > 0, 'deadline')
                        if connection.sock:
                            connection.sock.settimeout(min(90, remaining))
                        # read1 returns available bytes, unlike read(n) filling n.
                        chunk = response.read1(8192)
                        if not chunk:
                            break
                        parser.feed(chunk)
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    parser.finish()
                except Exception:
                    evidence.error = True
                    if not started:
                        try:
                            self.reject()
                        except OSError:
                            pass
                finally:
                    if connection:
                        connection.close()

        class QuietServer(ThreadingHTTPServer):
            daemon_threads = True
            def handle_error(self, request, client_address):
                evidence.error = True

        self.server = QuietServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': 0.05}, daemon=True)

    @property
    def base_url(self):
        return 'http://127.0.0.1:' + str(self.server.server_port) + '/v1'

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


def no_key_in_workspace(workspace, key):
    """Bounded full artifact scan; fail closed for symlinks or oversized output."""
    total = count = 0
    for directory, directories, names in os.walk(workspace, followlinks=False):
        require(not any((Path(directory) / name).is_symlink() for name in directories),
                'artifact_invalid')
        for name in names:
            path = Path(directory) / name
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                require(stat.S_ISREG(os.fstat(descriptor).st_mode), 'artifact_invalid')
                count += 1
                require(count <= 512, 'artifact_limit')
                with os.fdopen(descriptor, 'rb', closefd=False) as handle:
                    content = handle.read(8 * LIMIT + 1)
                total += len(content)
                require(len(content) <= 8 * LIMIT and total <= 16 * LIMIT, 'artifact_limit')
                require(key.encode() not in content, 'credential_leak')
            finally:
                os.close(descriptor)


def run_probe():
    require(sys.platform == 'linux' and os.geteuid() == 0, 'linux_root_required')
    cap_eff = next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines()
                   if line.startswith('CapEff:'))
    require(int(cap_eff, 16) == sum(1 << bit for bit in (0, 1, 5, 6, 7)), 'capabilities_invalid')
    key = os.environ.pop('SCIENTIFIC_PI_MODEL_API_KEY', '')
    require(len(key) >= 8, 'credential_missing')
    digest = os.environ.get('SCIENTIFIC_PI_IMAGE_DIGEST', '')
    require(re.fullmatch(r'sha256:[0-9a-f]{64}', digest), 'image_digest_missing')
    sys.path.insert(0, str(ROOT))
    from unittest.mock import patch
    from scientific_agent.pi_runtime_config import runtime_from_env
    from scientific_agent.pi_contracts import input_digest
    from pi_worker.runtime import PiRuntime

    case = Path('/data/model-probe')
    case.mkdir(mode=0o711)  # A fresh private Pod; rerunning the same case fails closed.
    case.chmod(0o711)
    base = runtime_from_env(case)
    events = []
    evidence = Evidence()
    now = datetime.now(timezone.utc)
    job = dict(schema_version='1', task_id=str(uuid.uuid4()), topic='Operational bash tool roundtrip only',
               user_id='isolated-model-probe', engine='pi', mode='brief', timeout_seconds=180,
               created_at=now.isoformat(), execution_deadline_at=(now + timedelta(seconds=180)).isoformat(),
               skill_bundle_revision=base.settings.skill_bundle_revision,
               runtime_revision=base.settings.runtime_revision)
    job['input_digest'] = input_digest(job)
    job['_runtime_uid'] = UID
    workspace = case / 'attempt'
    runtime = None
    try:
        with GatewayProxy(key, evidence, time.monotonic() + 180) as proxy:
            settings = replace(base.settings,
                command=('/usr/local/bin/node', '/opt/pi/node_modules/@mariozechner/pi-coding-agent/dist/cli.js'),
                node_command=('/usr/local/bin/node',), provider='scientific-live-probe', model=MODEL,
                model_base_url=proxy.base_url, model_api='openai-completions',
                model_env={'SCIENTIFIC_MODEL_API_KEY': DUMMY_KEY}, fixture_mode=False,
                # Test-only bypass to gather evidence; never deployment acceptance.
                capability_verified=True, model_verified=True, isolation_verified=True,
                image_digest=digest, run_uid_start=UID, run_gid=UID,
                idle_timeout_seconds=110, max_output_bytes=4 * LIMIT, max_line_bytes=LIMIT)
            runtime = PiRuntime(settings)
            with patch('pi_worker.research_tools.build_research_prompt', return_value=PROMPT):
                runtime.execute(job, workspace, events.append, threading.Event())
        require(not evidence.error, 'proxy_failed')
        require(evidence.requests == 2 and evidence.done == 2, 'roundtrip_incomplete')
        require(list(evidence.names.values()) == ['bash'], 'tool_mismatch')
        require(evidence.second_tool_marker, 'tool_result_missing')
        require(evidence.command_exact, 'command_mismatch')
        marker = workspace / 'tool-roundtrip.txt'
        require(not marker.is_symlink() and marker.stat().st_uid == UID, 'tool_uid_mismatch')
        require(marker.stat().st_size == len(MARKER) + 1, 'marker_mismatch')
        require(marker.read_bytes() == (MARKER + '\n').encode(), 'marker_mismatch')
        provenance_path = workspace / 'provenance.json'
        require(not provenance_path.is_symlink() and provenance_path.stat().st_size <= LIMIT,
                'provenance_invalid')
        provenance = json.loads(provenance_path.read_text())
        require(provenance.get('fixture_mode') is False and provenance.get('image_digest') == digest
                and provenance.get('model') == MODEL and provenance.get('provider') == 'scientific-live-probe',
                'provenance_invalid')
        event_names = [event.get('event', event.get('type')) for event in events]
        require(all(name in event_names for name in ('tool_execution_start', 'tool_execution_end',
                                                    'agent_end', 'runtime_finished')), 'events_incomplete')
        require(key not in json.dumps(events) and key not in json.dumps(provenance)
                and all(key not in model for model in evidence.models)
                and all(key not in name for name in evidence.names.values()), 'credential_leak')
        no_key_in_workspace(workspace, key)
        require(not runtime._uid_processes(UID), 'cleanup_unverified')
        require(not list(settings.control_dir.glob('runtime-*.json'))
                and not list(settings.control_dir.glob('launch-*.json')), 'cleanup_unverified')
        return dict(valid=True, code='tool_roundtrip_verified', message='Isolated operational probe passed',
                    configured_alias=MODEL, upstream_model_observed=sorted(evidence.models),
                    upstream_model_observation='SSE model field; gateway self-report, not independent identity proof',
                    tool_names=['bash'], model_requests=2, second_request_tool_marker=True,
                    command_exact=True, marker_exact=True, child_uid=UID, child_uid_cleanup=True, real_key_absent=True,
                    fixture_mode=False, pi_version=provenance['pi_version'], node_version=provenance['node_version'],
                    image_digest=digest, skill_bundle_revision=provenance['skill_bundle_revision'],
                    runtime_revision=provenance['runtime_revision'],
                    synthetic_harness_gates=True, production_acceptance=False)
    finally:
        if runtime is not None:
            try:
                runtime.recover((UID,))
            except Exception:
                raise ProbeFailure('cleanup_unverified') from None


def self_test():
    evidence = Evidence()
    request = {'model': MODEL, 'stream': True, 'messages': []}
    assert evidence.observe_request(request) == 1
    parser = SSEReader(evidence, 1)
    raw = ('data: ' + json.dumps({'model': 'actual-model', 'choices': [{'index': 0, 'delta': {
        'tool_calls': [{'index': 0, 'function': {'name': 'bash', 'arguments': 'DO_NOT_RETAIN'}}]}}]})
        + '\r\n\r\ndata: [DONE]\n\n').encode()
    for byte in raw:
        parser.feed(bytes([byte]))
    parser.finish()
    assert list(evidence.names.values()) == ['bash'] and evidence.models == {'actual-model'}
    assert 'DO_NOT_RETAIN' not in repr(vars(evidence))
    second = dict(request, messages=[{'role': 'assistant', 'tool_calls': [
        {'function': {'name': 'bash', 'arguments': json.dumps({'command': COMMAND})}}]},
        {'role': 'tool', 'content': MARKER}])
    evidence.observe_request(second)
    assert evidence.second_tool_marker and evidence.command_exact
    for action in (lambda: evidence.observe_request(request),
                   lambda: SSEReader(Evidence(), 1).feed(b'x' * (LIMIT + 1)),
                   lambda: Evidence().observe_request(dict(request, model='wrong'))):
        try:
            action()
        except ProbeFailure:
            pass
        else:
            raise AssertionError('Protocol limit did not fail closed')
    return {'valid': True, 'code': 'local_protocol_tests_passed',
            'message': 'SSE parsing and bounds tested locally; no model or Pi execution'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true', help='Run parser/bounds tests without network or Pi')
    parser.add_argument('--report', action='store_true', help='Also write sanitized JSON to /data/report.json')
    args = parser.parse_args()
    try:
        report = self_test() if args.self_test else run_probe()
    except BaseException as error:
        code = error.code if isinstance(error, ProbeFailure) else 'probe_failed'
        report = {'valid': False, 'code': code, 'message': 'Operational probe failed; raw diagnostics withheld'}
    if args.report:
        try:
            descriptor = os.open('/data/report.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w') as handle:
                json.dump(report, handle, sort_keys=True)
                handle.write('\n')
        except Exception:
            report = {'valid': False, 'code': 'report_write_failed', 'message': 'Sanitized report could not be written'}
    print(json.dumps(report, sort_keys=True))
    return 0 if report['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
