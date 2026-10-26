import io
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ppt_service.worker.hermes_client import HermesClient, HermesError, decode_sse, safe_id


class Handler(BaseHTTPRequestHandler):
    requests = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        self.requests.append((self.path, dict(self.headers), body))
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"run_id":"run_1","status":"started"}')

    def do_GET(self):
        if self.path == "/v1/capabilities":
            self.send_response(302)
            self.send_header("Location", "http://example.com/credential-theft")
            self.end_headers()
            return
        self.send_response(500)
        self.end_headers()
        self.wfile.write(b'upstream-secret=not-for-client-errors')


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def client(self):
        return HermesClient(f"http://127.0.0.1:{self.server.server_port}", "private-token")

    def test_admission_preserves_unicode_and_stable_key(self):
        result = self.client().admit("session_1", "中文资料，不要补造数字", "job1-attempt1")
        self.assertEqual(result["run_id"], "run_1")
        path, headers, body = Handler.requests[-1]
        self.assertEqual(path, "/v1/runs")
        self.assertEqual(headers["Idempotency-Key"], "job1-attempt1")
        self.assertEqual(body["session_id"], "session_1")
        self.assertEqual(body["input"], "中文资料，不要补造数字")

    def test_redirect_is_not_followed(self):
        with self.assertRaises(HermesError) as error:
            self.client().capabilities()
        self.assertEqual(error.exception.status, 302)
        self.assertNotIn("private-token", str(error.exception))

    def test_upstream_error_body_is_not_exposed(self):
        with self.assertRaises(HermesError) as error:
            self.client().status("run_1")
        self.assertEqual(error.exception.status, 500)
        self.assertNotIn("upstream-secret", str(error.exception))

    def test_rejects_non_loopback_and_credentials(self):
        for url in ("https://127.0.0.1", "http://localhost:8642", "http://10.0.0.1", "http://user:pass@127.0.0.1", "http://127.0.0.1/base"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                HermesClient(url, "secret")

    def test_path_ids_cannot_inject_routes(self):
        for value in ("../other", "..", "a?x=1", "a%2Fb", "\r\n", "中文"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                safe_id(value)

    def test_sse_multiline_comments_and_no_trailing_blank(self):
        source = ': heartbeat\n\nevent: tool.started\nid: 4\ndata: {"tool":\ndata: "terminal"}\n\nevent: run.completed\ndata: {"status":"completed"}\n'
        events = list(decode_sse(io.BytesIO(source.encode())))
        self.assertEqual(events[0].name, "tool.started")
        self.assertEqual(events[0].data, {"tool": "terminal"})
        self.assertEqual(events[0].event_id, "4")
        self.assertEqual(events[1].name, "run.completed")

    def test_sse_bounded_event(self):
        with self.assertRaises(HermesError):
            list(decode_sse([b'data: {"large":"xxxxxxxxxxxx"}\n'], max_event_bytes=16))


if __name__ == "__main__":
    unittest.main()
