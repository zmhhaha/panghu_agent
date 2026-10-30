"""Temporary fixed-upstream Agent relay, exclusively for a new workbench instance.

It drops ONE accepted scene submission response, then blocks that operation's
lookup until an operator supplies a restart-bound release file. No retries,
arbitrary destinations, raw bodies, credentials, cookies, or header logging.
Use a separate persistent relay process; never restart it with the workbench.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import socket
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from agent_service.contracts import canonical_bytes, digest
from .verify_live import VerificationFailure, canonical_uuid, decode_object, origin, read_token, require


LIMIT = 8 * 1024 * 1024
SHA = re.compile(r'[a-f0-9]{64}')
ID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'


class Relay:
    """Serial, bounded relay core. Tests replace only the upstream transport."""

    def __init__(self, *, upstream, token_file, output, run_id, transport=None, clock=time.time):
        require(re.fullmatch(r'[A-Za-z0-9_-]{8,64}', run_id), 'run_id_invalid')
        self.output, self.run_id, self.clock = Path(output), run_id, clock
        require(self.output.is_absolute() and Path(token_file).is_absolute(), 'absolute_path_required')
        self.token = read_token(Path(token_file))
        self.http = httpx.Client(base_url=origin(upstream), timeout=10, follow_redirects=False,
            trust_env=False, transport=transport,
            headers={'Authorization': 'Bearer ' + self.token, 'Accept-Encoding': 'identity'})
        try:
            self.output.mkdir(parents=True, exist_ok=False)
        except OSError:
            self.http.close()
            raise VerificationFailure('new_relay_directory_required') from None
        self.events, self.post_keys, self.target = [], set(), None
        self.arm = None
        self.released = False
        self.record('relay_started')

    def record(self, event, **fields):
        previous = self.events[-1]['sha256'] if self.events else '0' * 64
        entry = dict(seq=len(self.events) + 1, event=event, observed_at=self.clock(),
                     previous_sha256=previous, **fields)
        entry['sha256'] = digest(entry)
        self.events.append(entry)
        document = dict(schema_version='video-fault-relay.v1', run_id=self.run_id, events=self.events)
        raw = canonical_bytes(document)
        require(self.token.encode() not in raw, 'credential_in_relay_evidence')
        pending = self.output / 'trace.json.tmp'
        with pending.open('wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        pending.replace(self.output / 'trace.json')

    def error(self, code, status=503):
        return status, {'Content-Type': 'application/json'}, canonical_bytes({'detail': {'code': code}})

    def load_arm(self):
        path = self.output / 'arm.json'
        if self.arm is None and path.exists():
            require(path.is_file() and path.stat().st_size <= 4096, 'relay_arm_invalid')
            value = decode_object(path.read_bytes())
            require(set(value) == {'run_id', 'input_sha256'} and value['run_id'] == self.run_id
                    and type(value['input_sha256']) is str and SHA.fullmatch(value['input_sha256']), 'relay_arm_invalid')
            self.arm = value
            self.record('relay_armed', input_sha256=value['input_sha256'])

    def release_lookup(self):
        path = self.output / 'release.json'
        if self.released:
            return True
        if not path.exists():
            return False
        require(path.is_file() and path.stat().st_size <= 4096, 'relay_release_invalid')
        value = decode_object(path.read_bytes())
        require(set(value) == {'job_id', 'operation_id', 'before_sha256', 'after_sha256'}
                and value['job_id'] == self.target['job_id'] and value['operation_id'] == self.target['operation_id']
                and all(type(value[k]) is str and SHA.fullmatch(value[k]) for k in ('before_sha256', 'after_sha256')),
                'relay_release_invalid')
        self.released = True
        self.record('lookup_released', **value)
        return True

    def handle(self, method, path, headers, body=b''):
        """None means close the downstream socket without returning any response."""
        supplied = headers.get('Authorization', headers.get('authorization', ''))
        if not hmac.compare_digest(supplied, 'Bearer ' + self.token):
            return self.error('relay_authentication_required', 401)
        parsed = urlsplit(path)
        allowed = (parsed.path == '/v1/capabilities' and method == 'GET' and not parsed.query
            or method == 'GET' and not parsed.query and re.fullmatch(
                rf'/v1/(?:jobs/{ID}(?:/artifact-manifest|/events)?|jobs/by-idempotency/video-{ID}|artifacts/{ID}/content)', path)
            or method == 'POST' and parsed.path == '/v1/inputs' and parsed.query == 'name=video-input.json'
            or method == 'POST' and parsed.path == '/v1/jobs' and not parsed.query
            or method == 'POST' and not parsed.query and re.fullmatch(rf'/v1/jobs/{ID}/cancel', path))
        if not allowed or parsed.scheme or parsed.netloc or parsed.fragment or len(body) > 1024 * 1024:
            return self.error('relay_path_rejected', 400)
        require(len(self.events) < 10000, 'relay_event_limit')
        self.load_arm()
        if self.target and method == 'GET' and path == '/v1/jobs/by-idempotency/video-' + self.target['operation_id']:
            if not self.release_lookup():
                self.record('lookup_blocked', operation_id=self.target['operation_id'], job_id=self.target['job_id'])
                return self.error('relay_restart_gate_closed')
        post = None
        upstream_headers = {'Content-Type': headers.get('Content-Type', headers.get('content-type', 'application/json'))}
        if method == 'POST' and path == '/v1/jobs':
            value = decode_object(body)
            business_ref = value.get('business_ref', '')
            require(type(business_ref) is str and business_ref.startswith('video:'), 'relay_business_ref_rejected')
            operation_id = canonical_uuid(business_ref[6:])
            idem = headers.get('Idempotency-Key', headers.get('idempotency-key', ''))
            require(idem == 'video-' + operation_id and value.get('profile') in {'video.scene.v1', 'video.storyboard.v1'}
                    and value.get('profile_revision') == '1', 'relay_job_scope_rejected')
            if idem in self.post_keys:
                self.record('duplicate_post_rejected', operation_id=operation_id)
                return self.error('relay_duplicate_submission_rejected', 409)
            checksum = value.get('task', {}).get('input_sha256')
            require(type(checksum) is str and SHA.fullmatch(checksum), 'relay_input_hash_invalid')
            targeted = bool(self.arm and self.target is None and value['profile'] == 'video.scene.v1'
                            and checksum == self.arm['input_sha256'])
            self.post_keys.add(idem)
            post = dict(operation_id=operation_id, input_sha256=checksum,
                        request_sha256=hashlib.sha256(body).hexdigest(), targeted=targeted)
            self.record('jobs_post_started', **post)  # durable before forwarding, never repeat
            upstream_headers['Idempotency-Key'] = idem
        try:
            with self.http.stream(method, path, content=body or None, headers=upstream_headers) as response:
                require(response.headers.get('content-encoding', 'identity').lower() == 'identity', 'relay_encoding_invalid')
                raw = bytearray()
                for chunk in response.iter_bytes():
                    require(len(raw) + len(chunk) <= LIMIT, 'relay_response_too_large')
                    raw.extend(chunk)
                if post:
                    if response.status_code == 202:
                        accepted = decode_object(bytes(raw))
                        job_id = canonical_uuid(accepted.get('job_id'))
                        require(accepted.get('business_ref') == 'video:' + post['operation_id'], 'relay_job_identity_invalid')
                        self.record('jobs_post_accepted', job_id=job_id, **post)
                        if post['targeted']:
                            self.target = dict(operation_id=post['operation_id'], job_id=job_id)
                            self.record('response_dropped', **self.target)
                            return None
                    else:
                        self.record('jobs_post_rejected_or_unknown', operation_id=post['operation_id'],
                                    http_status=response.status_code)
                elif method == 'GET' and '/by-idempotency/' in path and self.target and response.status_code == 200:
                    found = decode_object(bytes(raw))
                    require(found.get('job_id') == self.target['job_id']
                            and found.get('business_ref') == 'video:' + self.target['operation_id'], 'relay_lookup_identity_invalid')
                    self.record('lookup_forwarded', **self.target)
                return response.status_code, {'Content-Type': response.headers.get('content-type', 'application/octet-stream')}, bytes(raw)
        except (httpx.HTTPError, OSError):
            if post:
                self.record('upstream_submission_unknown', operation_id=post['operation_id'])
            return self.error('relay_upstream_unavailable')

    def close(self):
        self.http.close()


def handler_for(relay):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_args):
            pass

        def log_error(self, *_args):
            pass

        def do_GET(self):
            self.process()

        def do_POST(self):
            self.process()

        def process(self):
            self.connection.settimeout(10)
            try:
                require(not self.headers.get('transfer-encoding'), 'relay_request_encoding_invalid')
                length = self.headers.get('content-length', '0')
                require(re.fullmatch(r'[0-9]{1,8}', length) and int(length) <= 1024 * 1024, 'relay_request_too_large')
                body = self.rfile.read(int(length))
                require(len(body) == int(length), 'relay_request_truncated')
                result = relay.handle(self.command, self.path, self.headers, body)
                if result is None:
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
            except (VerificationFailure, ValueError, TypeError, KeyError, OSError):
                result = relay.error('relay_request_stopped')
            status, headers, body = result
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True
    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--listen-host', choices=('127.0.0.1', '0.0.0.0'), default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8096)
    parser.add_argument('--max-seconds', type=int, default=1800)
    args = parser.parse_args(argv)
    relay = None
    try:
        require(1024 <= args.port <= 65535 and 1 <= args.max_seconds <= 3600, 'relay_limits_invalid')
        relay = Relay(upstream=args.upstream, token_file=args.token_file, output=args.output, run_id=args.run_id)
        with HTTPServer((args.listen_host, args.port), handler_for(relay)) as server:
            server.timeout = .5
            deadline = time.monotonic() + args.max_seconds
            while time.monotonic() < deadline:
                server.handle_request()
        return 0
    except (VerificationFailure, ValueError, OSError, KeyboardInterrupt):
        print('Relay stopped; inspect controlled evidence. No submission is retried.')
        return 1
    finally:
        if relay:
            relay.close()


if __name__ == '__main__':
    raise SystemExit(main())
