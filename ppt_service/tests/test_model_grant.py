"""Local-only task authority and real blocked-stream cancellation checks."""
import dataclasses
import http.client
import json
from pathlib import Path
from queue import Queue
import socket
import tempfile
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import Mock

from ppt_service.worker.model_grant import ModelGrant, ModelIdentity
from ppt_service.worker.model_proxy import ModelProxyHandler, ModelProxyServer, ProxyConfig
from ppt_service.worker.proxy_usage import UsageLedger

KEY = "task-test-key-0123456789"
AUTH = ("Bearer " + KEY).encode()


def identity():
    return ModelIdentity(str(uuid.uuid4()), str(uuid.uuid4()), 1, 1, "plan_initial")


class GrantTests(unittest.TestCase):
    def grant(self, **kwargs):
        result = ModelGrant(identity(), KEY, **kwargs)
        self.addCleanup(result.close)
        return result

    def test_identity_strict_and_immutable(self):
        value = identity()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            value.attempt = 2
        for field, bad in (("project_id", "invalid"), ("job_id", "invalid"),
                           ("attempt", True), ("attempt", 0), ("token", False),
                           ("token", -1), ("phase", "other")):
            with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                dataclasses.replace(value, **{field: bad})

    def test_ttl_and_budget_validation(self):
        for ttl in (True, False, 0, -1, 601, float("nan"), float("inf"), "90"):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                ModelGrant(identity(), KEY, ttl_seconds=ttl)
        for name in ("max_requests", "max_concurrency"):
            for bad in (True, 0, -1, float("inf"), 1.5):
                with self.subTest(name=name, bad=bad), self.assertRaises(ValueError):
                    ModelGrant(identity(), KEY, **{name: bad})
        with self.assertRaises(ValueError):
            ModelGrant(tuple(dataclasses.astuple(identity())), KEY)

    def test_exact_deadline_never_revives(self):
        clock = [10.0]
        grant = self.grant(clock=lambda: clock[0], ttl_seconds=2)
        permit = grant.acquire(AUTH)
        clock[0] = 12.0
        self.assertFalse(grant.renew(grant.identity, 600))
        self.assertFalse(permit.allowed())
        self.assertIsNone(grant.acquire(AUTH))
        clock[0] = 10.0
        self.assertFalse(grant.renew(grant.identity, 90))
        self.assertFalse(grant.snapshot()["active"])

    def test_renew_preserves_budget_and_identity(self):
        grant = self.grant(max_requests=2)
        for field, value in (("project_id", str(uuid.uuid4())),
                             ("job_id", str(uuid.uuid4())), ("attempt", 2),
                             ("token", 2), ("phase", "generate")):
            wrong = dataclasses.replace(grant.identity, **{field: value})
            self.assertFalse(grant.renew(wrong, 90))
            self.assertFalse(grant.revoke(wrong))
        for _ in range(2):
            permit = grant.acquire(AUTH)
            self.assertIsNotNone(permit)
            permit.release()
            permit.release()
            self.assertTrue(grant.renew(grant.identity, 90))
        self.assertIsNone(grant.acquire(AUTH))
        self.assertEqual(grant.snapshot()["requests_used"], 2)
        self.assertNotIn(KEY, json.dumps(grant.snapshot()))
        self.assertNotIn("Bearer", json.dumps(grant.snapshot()))
        self.assertIsNone(grant.acquire(b"Bearer wrong"))

    def test_atomic_concurrency(self):
        grant = self.grant(max_concurrency=1)
        gate = threading.Barrier(9)
        permits = []
        lock = threading.Lock()
        def acquire():
            gate.wait()
            result = grant.acquire(AUTH)
            with lock:
                permits.append(result)
        threads = [threading.Thread(target=acquire) for _ in range(8)]
        for thread in threads:
            thread.start()
        gate.wait()
        for thread in threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(sum(p is not None for p in permits), 1)
        self.assertEqual(grant.snapshot()["inflight"], 1)
        next(p for p in permits if p is not None).release()
        grant.acquire(AUTH).release()

    def test_late_socket_registration_interrupts_transport(self):
        grant = self.grant()
        permit = grant.acquire(AUTH)
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        right.settimeout(1)
        self.assertTrue(grant.revoke(grant.identity))
        self.assertFalse(permit.attach_socket(left))
        self.assertEqual(right.recv(1), b"")
        self.assertFalse(permit.allowed())

    def test_server_rejects_mismatched_configuration_before_bind(self):
        grant = self.grant()
        valid = dict(job_id=grant.identity.job_id, attempt=1, phase=grant.identity.phase)
        config = ProxyConfig(KEY, "provider-test-key")
        for attribution in ({}, {**valid, "job_id": str(uuid.uuid4())},
                            {**valid, "attempt": True}, {**valid, "phase": "generate"}):
            with self.subTest(attribution=attribution), self.assertRaisesRegex(ValueError, "invalid_proxy_configuration"):
                ModelProxyServer(("invalid-address", 0), config,
                                 usage_ledger=UsageLedger(**attribution), model_grant=grant)
        with self.assertRaisesRegex(ValueError, "invalid_proxy_configuration"):
            ModelProxyServer(("invalid-address", 0), ProxyConfig("other-task-key-0123456789", "provider-test-key"),
                             usage_ledger=UsageLedger(**valid), model_grant=grant)
        with self.assertRaisesRegex(ValueError, "invalid_proxy_configuration"):
            ModelProxyServer(("invalid-address", 0), config,
                             usage_ledger=UsageLedger(**valid), model_grant=object())


class SilentBackend(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *args):
        pass
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.flush()
        self.server.entered.set()
        try:
            self.server.release.wait(8)
        finally:
            self.server.finished.set()
        self.close_connection = True


class StreamCancellationTests(unittest.TestCase):
    def test_revoke_during_connect_never_sends_http(self):
        ident = identity()
        grant = ModelGrant(ident, KEY)
        entered, release = threading.Event(), threading.Event()
        downstream, client = socket.socketpair()
        upstream, provider = socket.socketpair()
        client.settimeout(2)
        provider.settimeout(2)
        class DeferredConnection:
            sock = None
            request = Mock()
            def connect(self):
                entered.set()
                if not release.wait(3):
                    raise TimeoutError()
                self.sock = upstream
            def close(self):
                if self.sock is not None:
                    self.sock.close()
        connection = DeferredConnection()
        ledger = UsageLedger(job_id=ident.job_id, attempt=1, phase=ident.phase)
        server = ModelProxyServer(("127.0.0.1", 0), ProxyConfig(KEY, "provider-test-key"),
                                  connector=lambda *args, **kwargs: connection,
                                  usage_ledger=ledger, model_grant=grant)
        handler = SimpleNamespace(server=server, connection=downstream,
                                  _model_authorization=AUTH, _error=Mock(), _headers=Mock())
        failures = []
        def forward():
            try:
                ModelProxyHandler._forward(handler, b'{"model":"gpt-6.1-sol"}')
            except Exception as error:
                failures.append(error)
        thread = threading.Thread(target=forward, daemon=True)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertTrue(grant.revoke(ident))
            self.assertEqual(client.recv(1), b"")
            release.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(provider.recv(1), b"")
            connection.request.assert_not_called()
            handler._error.assert_not_called()
            self.assertEqual(grant.snapshot()["inflight"], 0)
        finally:
            release.set()
            grant.close()
            for resource in (downstream, client, upstream, provider):
                resource.close()
            thread.join(2)
            server.server_close()

    def test_slow_body_connections_bounded_and_closed(self):
        ident = identity()
        grant = ModelGrant(ident, KEY)
        admitted, retired = Queue(), Queue()
        class TrackingServer(ModelProxyServer):
            def process_request(self, request, address):
                super().process_request(request, address)
                with self._connection_lock:
                    admitted.put(len(self._accepted_connections))
            def process_request_thread(self, request, address):
                try:
                    super().process_request_thread(request, address)
                finally:
                    retired.put(True)
        ledger = UsageLedger(job_id=ident.job_id, attempt=1, phase=ident.phase)
        server = TrackingServer(("127.0.0.1", 0), ProxyConfig(KEY, "provider-test-key"),
                                usage_ledger=ledger, model_grant=grant)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        thread.start()
        connections = []
        try:
            for count in range(1, 10):
                client = socket.create_connection(("127.0.0.1", server.server_port), timeout=2)
                connections.append(client)
                if count <= 8:
                    client.sendall(("POST /v1/responses HTTP/1.1\r\nHost: localhost\r\nAuthorization: "
                                    + AUTH.decode() + "\r\nContent-Length: 100\r\n\r\n{").encode())
                self.assertEqual(admitted.get(timeout=2), min(count, 8))
            self.assertEqual(connections[-1].recv(1), b"")
            self.assertEqual(grant.snapshot()["requests_used"], 0)
            server.shutdown()
            started = time.monotonic()
            server.server_close()
            for client in connections[:-1]:
                self.assertEqual(client.recv(1), b"")
            for _ in range(8):
                self.assertTrue(retired.get(timeout=2))
            self.assertLess(time.monotonic() - started, 2)
            with server._connection_lock:
                self.assertFalse(server._accepted_connections)
        finally:
            for client in connections:
                client.close()
            server.shutdown()
            server.server_close()
            thread.join(2)
            self.assertFalse(thread.is_alive())

    def run_stream(self, expire):
        with tempfile.TemporaryDirectory() as directory:
            ident = identity()
            # A deterministic clock triggers expiry while the real network read
            # remains blocked, without relying on request setup timing.
            clock = [100.0]
            grant = ModelGrant(ident, KEY, ttl_seconds=90, clock=lambda: clock[0])
            backend = ThreadingHTTPServer(("127.0.0.1", 0), SilentBackend)
            backend.daemon_threads = True
            backend.entered = threading.Event()
            backend.release = threading.Event()
            backend.finished = threading.Event()
            calls = []
            def connector(host, *, port, timeout, context):
                calls.append(timeout)
                return http.client.HTTPConnection("127.0.0.1", backend.server_port, timeout=timeout)
            path = Path(directory) / "usage.jsonl"
            ledger = UsageLedger(str(path), job_id=ident.job_id, attempt=ident.attempt, phase=ident.phase)
            server = ModelProxyServer(("127.0.0.1", 0), ProxyConfig(KEY, "provider-test-key"),
                                      connector=connector, usage_ledger=ledger, model_grant=grant)
            threads = [threading.Thread(target=s.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
                       for s in (backend, server)]
            for thread in threads:
                thread.start()
            client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            result = []
            done = threading.Event()
            reader = None
            try:
                client.request("POST", "/v1/responses", body=b'{"model":"gpt-6.1-sol"}',
                               headers={"Authorization": AUTH.decode()})
                response = client.getresponse()
                self.assertEqual(response.status, 200)
                self.assertTrue(backend.entered.wait(2))
                def read():
                    try:
                        result.append(response.read())
                    except Exception as error:
                        result.append(error)
                    finally:
                        done.set()
                reader = threading.Thread(target=read, daemon=True)
                reader.start()
                self.assertFalse(done.is_set())
                started = time.monotonic()
                if expire:
                    clock[0] = 190.0
                else:
                    self.assertTrue(grant.revoke(ident))
                self.assertTrue(done.wait(2), "grant did not interrupt silent upstream")
                self.assertLess(time.monotonic() - started, 2)
                self.assertEqual(result, [b""])
                self.assertFalse(backend.release.is_set())
                self.assertIsNone(grant.acquire(AUTH))
                # A second valid credential remains rejected by exhausted authority.
                later = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
                try:
                    later.request("POST", "/v1/responses", body=b'{"model":"gpt-6.1-sol"}',
                                  headers={"Authorization": AUTH.decode()})
                    try:
                        rejected = later.getresponse()
                    except http.client.RemoteDisconnected:
                        pass  # Expired setup timeout can reject before HTTP parsing.
                    else:
                        self.assertEqual(rejected.status, 429)
                        rejected.read()
                finally:
                    later.close()
                self.assertEqual(len(calls), 1)
                self.assertTrue(0 < calls[0] <= 90)
            finally:
                grant.close()
                client.close()
                backend.release.set()
                for target in (server, backend):
                    target.shutdown()
                    target.server_close()
                if reader is not None:
                    reader.join(2)
                    self.assertFalse(reader.is_alive())
                for thread in threads:
                    thread.join(2)
                    self.assertFalse(thread.is_alive())
                self.assertTrue(backend.finished.wait(2))
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([row["event"] for row in rows], ["request_started", "request_finished"])
            self.assertEqual(rows[-1]["outcome"], "proxy_shutdown")
            self.assertEqual(rows[-1]["usage_status"], "unknown")
            self.assertEqual(grant.snapshot()["inflight"], 0)

    def test_revoke_interrupts_silent_stream(self):
        self.run_stream(False)

    def test_expiry_interrupts_silent_stream(self):
        self.run_stream(True)


if __name__ == "__main__":
    unittest.main()
