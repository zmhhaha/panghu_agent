"""Local-only integration tests for the fixed-model proxy (no provider calls)."""
import contextlib
import http.client
import io
import json
import socket
import ssl
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from ppt_service.worker import model_proxy as proxy


class Backend(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.seen.append((self.path, dict(self.headers), body))
        self.send_response(self.server.status)
        self.send_header("Content-Type", self.server.content_type)
        self.send_header("Set-Cookie", "provider-secret=do-not-forward")
        self.send_header("X-Provider-Secret", "do-not-forward")
        self.send_header("Connection", "close")
        if self.server.declared_length is not None:
            self.send_header("Content-Length", str(self.server.declared_length))
        self.end_headers()
        self.close_connection = True
        try:
            if self.server.streaming:
                self.wfile.write(b"data: first\n\n")
                self.wfile.flush()
                self.server.first_sent.set()
                self.server.release.wait(5)
                self.wfile.write(b"data: second\n\n")
            else:
                self.wfile.write(self.server.body)
            self.wfile.flush()
        except (OSError, ConnectionError):
            pass


class ProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
        cls.backend.daemon_threads = True
        cls.backend_thread = threading.Thread(target=cls.backend.serve_forever, daemon=True)
        cls.backend_thread.start()
        cls.connector_calls = []

        def connector(host, *, port, timeout, context):
            cls.connector_calls.append((host, port, timeout, context))
            return http.client.HTTPConnection("127.0.0.1", cls.backend.server_port, timeout=3)

        cls.server = proxy.ModelProxyServer(
            ("127.0.0.1", 0), proxy.ProxyConfig("worker-test-key", "provider-test-key"),
            connector=connector)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.backend.release.set()
        cls.server.shutdown()
        cls.server.server_close()
        cls.backend.shutdown()
        cls.backend.server_close()
        cls.thread.join(3)
        cls.backend_thread.join(3)

    def setUp(self):
        self.backend.seen = []
        self.backend.status = 200
        self.backend.content_type = "application/json; charset=utf-8"
        self.backend.body = b'{"ok":true}'
        self.backend.streaming = False
        self.backend.declared_length = None
        self.backend.first_sent = threading.Event()
        self.backend.release = threading.Event()
        self.connector_calls.clear()

    def request(self, method="POST", path="/v1/responses", body=None, headers=None):
        if body is None:
            body = (json.dumps({"model": proxy.DEFAULT_MODEL, "input": "test"}).encode()
                    if method == "POST" else b"")
        base = {"Authorization": "Bearer worker-test-key"}
        if headers:
            base.update(headers)
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        connection.request(method, path, body=body, headers=base)
        response = connection.getresponse()
        data = response.read()
        result = response.status, dict(response.getheaders()), data
        connection.close()
        return result

    def raw_request(self, headers):
        with socket.create_connection(("127.0.0.1", self.server.server_port), timeout=3) as sock:
            sock.sendall(b"POST /v1/responses HTTP/1.1\r\nHost: localhost\r\n" + headers + b"\r\n\r\n")
            sock.shutdown(socket.SHUT_WR)
            response = http.client.HTTPResponse(sock)
            response.begin()
            return response.status, response.read()

    def test_success_header_isolation_and_verified_connector(self):
        status, headers, body = self.request(headers={
            "Cookie": "secret-cookie", "X-API-Key": "attacker-key",
            "Host": "evil.example", "X-Forwarded-Host": "evil.example",
            "Accept": "text/html", "Content-Type": "text/plain"})
        self.assertEqual((status, body), (200, b'{"ok":true}'))
        self.assertEqual(headers["Connection"], "close")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Set-Cookie", headers)
        self.assertNotIn("X-Provider-Secret", headers)
        path, forwarded, raw = self.backend.seen[0]
        self.assertEqual(path, "/v1/responses")
        self.assertEqual(forwarded["Authorization"], "Bearer provider-test-key")
        self.assertEqual(forwarded["Content-Type"], "application/json")
        for header in ("Cookie", "X-API-Key", "X-Forwarded-Host"):
            self.assertNotIn(header, forwarded)
        self.assertNotEqual(forwarded["Host"], "evil.example")
        host, port, timeout, context = self.connector_calls[0]
        self.assertEqual((host, port, timeout), ("custom-llm-proxy.panghuer.top", 443, 600))
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_all_posts_require_exact_bearer(self):
        for path in ("/v1/responses", "/healthz", "/other"):
            for auth in ("", "Bearer wrong", "bearer worker-test-key", "Bearer provider-test-key"):
                with self.subTest(path=path, auth=auth):
                    self.assertEqual(self.request(path=path, body=b"", headers={"Authorization": auth})[0], 401)
        status, _ = self.raw_request(b"Authorization: Bearer worker-test-key\r\n"
                                     b"Authorization: Bearer worker-test-key\r\nContent-Length: 0")
        self.assertEqual(status, 401)
        self.assertFalse(self.backend.seen)

    def test_constant_time_authentication(self):
        original = proxy.hmac.compare_digest
        with patch.object(proxy.hmac, "compare_digest", wraps=original) as compare:
            self.assertEqual(self.request(body=b"", headers={"Authorization": "Bearer wrong"})[0], 401)
            compare.assert_called_once_with(b"Bearer wrong", b"Bearer worker-test-key")

    def test_routes_methods_queries_and_health(self):
        for path in ("/v1/responses?x=1", "/v1/responses?", "/v1/responses/",
                     "//v1/responses", "/responses", "https://evil.example/v1/responses", "/healthz"):
            with self.subTest(path=path):
                self.assertEqual(self.request(path=path, body=b"")[0], 404)
        for method in ("GET", "HEAD", "CONNECT", "OPTIONS", "PUT", "DELETE", "PATCH"):
            with self.subTest(method=method):
                self.assertGreaterEqual(self.request(method=method)[0], 400)
        self.assertEqual(self.request(method="GET", path="/healthz")[0], 200)
        self.assertEqual(self.request(method="GET", path="/healthz?x=1")[0], 404)
        self.assertFalse(self.backend.seen)

    def test_strict_json_model(self):
        bodies = [b'[]', b'null', b'{}', b'{"model":"evil"}', b'not-json',
                  b'{"model":"gpt-6.1-sol","model":"gpt-6.1-sol"}',
                  b'{"model":"gpt-6.1-sol","x":NaN}', b'\xff']
        for body in bodies:
            with self.subTest(body=body):
                self.assertEqual(self.request(body=body)[0], 400)
        self.assertFalse(self.backend.seen)

    def test_length_and_transfer_encoding(self):
        auth = b"Authorization: Bearer worker-test-key\r\n"
        for extra, expected in [(b"", 411), (b"Content-Length: -1", 411),
                                (b"Content-Length: 8388609", 413),
                                (b"Content-Length: 2\r\nContent-Length: 2", 411),
                                (b"Content-Length: 0\r\nTransfer-Encoding: chunked", 400),
                                (b"Transfer-Encoding: identity", 400)]:
            with self.subTest(extra=extra):
                self.assertEqual(self.raw_request(auth + extra)[0], expected)
        self.assertFalse(self.backend.seen)

    def test_stream_delivers_first_event_before_backend_finishes(self):
        self.backend.streaming = True
        self.backend.content_type = "text/event-stream; charset=utf-8"
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        connection.request("POST", "/v1/responses", body=b'{"model":"gpt-6.1-sol"}',
                           headers={"Authorization": "Bearer worker-test-key"})
        try:
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Content-Type"), "text/event-stream")
            self.assertIsNone(response.getheader("Content-Length"))
            self.assertIsNone(response.getheader("Transfer-Encoding"))
            self.assertEqual(response.readline(), b"data: first\n")
            self.assertFalse(self.backend.release.is_set())
            self.backend.release.set()
            self.assertEqual(response.read(), b"\ndata: second\n\n")
        finally:
            self.backend.release.set()
            connection.close()

    def test_provider_errors_and_redirects_are_sanitized(self):
        self.backend.body = b"provider-test-key raw private error secret-cookie"
        for status in (301, 302, 307, 400, 401, 429, 500):
            self.backend.status = status
            with self.subTest(status=status):
                actual, headers, body = self.request()
                self.assertEqual(actual, 502)
                self.assertEqual(json.loads(body), {"error": {"code": "upstream_error"}})
                self.assertNotIn(b"provider-test-key", body)
        self.assertEqual(len(self.backend.seen), 7)  # No redirect follow-up.

    def test_content_type_and_declared_size_rejected(self):
        for content_type in ("text/html", "application/octet-stream", ""):
            self.backend.content_type = content_type
            self.assertEqual(self.request()[0], 502)
        self.backend.content_type = "application/json"
        self.backend.declared_length = proxy.MAX_RESPONSE_BYTES + 1
        self.assertEqual(self.request()[0], 502)

    def test_stream_total_size_bounded(self):
        self.backend.body = b"a" * 200
        with patch.object(proxy, "MAX_RESPONSE_BYTES", 100):
            status, headers, body = self.request()
        self.assertEqual(status, 200)
        self.assertLessEqual(len(body), 100)
        self.assertEqual(headers["Connection"], "close")

    def test_exception_and_logs_do_not_leak(self):
        for error, status in ((OSError("provider-test-key private body"), 502),
                              (TimeoutError("provider-test-key private body"), 504)):
            with patch.object(self.server, "connector", side_effect=error):
                output = io.StringIO()
                with contextlib.redirect_stderr(output):
                    actual, headers, body = self.request()
                self.assertEqual(actual, status)
                self.assertNotIn(b"private", body)
                self.assertNotIn(b"provider-test-key", body)
                self.assertEqual(output.getvalue(), "")


class ConfigurationTests(unittest.TestCase):
    def test_environment_defaults_and_required_distinct_keys(self):
        env = {"PPT_PROXY_CLIENT_KEY": "worker", "PPT_UPSTREAM_API_KEY": "provider"}
        config = proxy.ProxyConfig.from_env(env)
        self.assertEqual(config.model, "gpt-6.1-sol")
        self.assertEqual(config.upstream_base_url, proxy.DEFAULT_UPSTREAM)
        for invalid in ({}, {**env, "PPT_PROXY_CLIENT_KEY": "provider"},
                        {**env, "PPT_UPSTREAM_API_KEY": "bad\r\nkey"}):
            with self.assertRaisesRegex(ValueError, "^invalid_proxy_configuration$"):
                proxy.ProxyConfig.from_env(invalid)

    def test_operator_upstream_requires_credentialfree_https(self):
        urls = ["http://example.com/v1", "https://user:pass@example.com/v1",
                "https://user@example.com/v1", "https://example.com/v1?x=1",
                "https://example.com/v1#fragment", "https://example.com/v1?",
                "https://example.com/v1#", "https:///v1", "https://example.com:0/v1",
                "https://example.com\\@evil/v1", "https://example.com/\npath"]
        for url in urls:
            with self.subTest(url=url):
                with self.assertRaisesRegex(ValueError, "^invalid_proxy_configuration$"):
                    proxy.ProxyConfig("worker", "provider", upstream_base_url=url)


if __name__ == "__main__":
    unittest.main()
