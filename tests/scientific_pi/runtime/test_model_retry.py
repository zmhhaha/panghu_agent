"""Strict transient HTTP classification and one retry owner; no real provider."""
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from pi_worker.runtime import PiRuntime, RuntimeExecutionError
from scientific_agent.pi_runtime_config import runtime_from_env

ROOT = Path(__file__).resolve().parents[3]
CLI = ROOT / "pi_worker/node/node_modules/@mariozechner/pi-coding-agent/dist/cli.js"
RETRY_SETTINGS = {"retry": {"enabled": False, "provider": {"maxRetries": 0}}}


class ModelRetryUnitTests(unittest.TestCase):
    def test_only_pinned_leading_transient_http_status_is_retryable_without_body_disclosure(self):
        for status in (429, 500, 502, 503, 504):
            error = PiRuntime._model_request_error({"stopReason": "error",
                "errorMessage": f"{status} PRIVATE_FIXTURE_BODY"})
            self.assertTrue(error.retryable)
            self.assertEqual(error.code, "model_http_transient")
            self.assertIn(str(status), error.message)
            self.assertNotIn("PRIVATE_FIXTURE_BODY", error.message)

    def test_unknown_protocol_auth_numbers_inside_body_and_aborted_are_permanent(self):
        messages = [None, {}, 503, "503", "503x error", " 503 error", "Error: 503 unavailable",
                    "400 body mentions 503", "401 503 auth denied", "403 429 forbidden",
                    "501 controlled fixture", "5000 is a number", "Provider error 500",
                    "Responses stream reported a failure", "Responses stream identity changed",
                    "upstream_error", "Connection error."]
        for raw in messages:
            with self.subTest(raw=raw):
                error = PiRuntime._model_request_error({"stopReason": "error", "errorMessage": raw})
                self.assertFalse(error.retryable)
                self.assertEqual(error.code, "model_error")
        self.assertFalse(PiRuntime._model_request_error({"stopReason": "aborted", "errorMessage": "503 error"}).retryable)

    def test_cancel_interrupts_retry_backoff(self):
        cancelled = threading.Event()
        timer = threading.Timer(0.05, cancelled.set)
        timer.start()
        started = time.monotonic()
        try:
            with self.assertRaises(RuntimeExecutionError) as caught:
                PiRuntime._wait_for_model_retry(cancelled, started + 20)
            self.assertEqual(caught.exception.code, "cancelled")
            self.assertFalse(caught.exception.retryable)
            self.assertLess(time.monotonic() - started, 1)
        finally:
            timer.cancel()
            timer.join()

    def test_original_deadline_clips_backoff_and_cannot_authorize_another_attempt(self):
        started = time.monotonic()
        with self.assertRaises(RuntimeExecutionError) as caught:
            PiRuntime._wait_for_model_retry(threading.Event(), started + 0.05)
        self.assertEqual(caught.exception.code, "timed_out")
        self.assertFalse(caught.exception.retryable)
        self.assertLess(time.monotonic() - started, 1)


class ErrorModel:
    """Loopback-only HTTP errors; requests are counted, request bodies discarded."""
    def __init__(self, statuses, body="controlled retry fixture"):
        self.statuses = statuses
        self.body = body
        self.requests = []
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length < 1024 * 1024:
                    self.send_error(400)
                    return
                self.rfile.read(length)
                status = owner.statuses[min(len(owner.requests), len(owner.statuses) - 1)]
                owner.requests.append((status, time.monotonic()))
                body = json.dumps({"error": {"message": owner.body, "type": "controlled_fixture"}}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    @property
    def base_url(self):
        return "http://127.0.0.1:" + str(self.server.server_port) + "/v1"
    def __enter__(self):
        self.thread.start()
        return self
    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


class ActualPiRetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not CLI.is_file() or not shutil.which("node"):
            raise unittest.SkipTest("Pinned actual Pi and Node required; no dependency installation")
        cls.node = shutil.which("node")
        integration = ROOT / "tests/scientific_pi/integration"
        sys.path.insert(0, str(integration))
        from harness import IntegrationHarness
        cls.harness_type = IntegrationHarness

    def runtime(self, directory, base_url, provider="fixture", model="fixture-model", key="loopback-dummy-only"):
        with patch.dict("os.environ", {}, clear=True):
            base = runtime_from_env(Path(directory))
        return PiRuntime(replace(base.settings, command=(self.node, str(CLI)), node_command=(self.node,),
            fixture_mode=True, provider=provider, model=model, model_base_url=base_url,
            model_env={"SCIENTIFIC_MODEL_API_KEY": key}, image_digest="sha256:" + "0" * 64,
            idle_timeout_seconds=15, term_grace_seconds=0.1, kill_grace_seconds=2))

    def test_actual_429_503_shapes_and_scheduler_caps_two_fresh_attempts(self):
        observed = []
        classify = PiRuntime._model_request_error
        def observe(message):
            observed.append(message.get("errorMessage"))
            return classify(message)
        with tempfile.TemporaryDirectory() as directory, ErrorModel([429, 503]) as model:
            runtime = self.runtime(directory, model.base_url)
            harness = self.harness_type(runtime=runtime, runtime_revision=runtime.settings.runtime_revision)
            try:
                task_id = harness.submit(key="bounded-http-retry", timeout_seconds=60).json()["id"]
                with patch.object(PiRuntime, "_model_request_error", side_effect=observe):
                    first = harness.execute_next()
                    self.assertEqual((first["state"], first["attempt"], first["error_code"]), ("queued", 1, "model_http_transient"))
                    second = harness.execute_next()
                self.assertEqual((second["state"], second["attempt"], second["error_code"]), ("failed", 2, "model_http_transient"))
                self.assertIsNone(harness.store.claim())
                self.assertEqual([status for status, _ in model.requests], [429, 503])
                self.assertGreaterEqual(model.requests[1][1] - model.requests[0][1], 2)
                self.assertEqual(observed, ["429 controlled retry fixture", "503 controlled retry fixture"])
                self.assertEqual(len(set(harness.store.execution_uids())), 2)
                for attempt in (1, 2):
                    workspace = harness.root / "tasks" / task_id / "attempts" / str(attempt) / "workspace"
                    self.assertEqual(json.loads((workspace / ".home/.pi/agent/settings.json").read_text()), RETRY_SETTINGS)
                self.assertIsNone(harness.db.get_report(task_id))
            finally:
                harness.close()

    def test_actual_auth_error_with_transient_number_in_body_never_retries(self):
        with tempfile.TemporaryDirectory() as directory, ErrorModel([401], "503 controlled auth fixture") as model:
            runtime = self.runtime(directory, model.base_url)
            harness = self.harness_type(runtime=runtime, runtime_revision=runtime.settings.runtime_revision)
            try:
                harness.submit(key="permanent-http-auth", timeout_seconds=60)
                snapshot = harness.execute_next()
                self.assertEqual((snapshot["state"], snapshot["attempt"], snapshot["error_code"]), ("failed", 1, "model_error"))
                self.assertEqual(len(model.requests), 1)
                self.assertIsNone(harness.store.claim())
            finally:
                harness.close()

    def test_actual_503_then_second_attempt_research_publishes_once(self):
        integration = ROOT / "tests/scientific_pi/integration/test_real_pi_research.py"
        spec = importlib.util.spec_from_file_location("retry_research_helper", integration)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        fixture = helper.research_fixtures.abstract_only()
        expected = fixture["report"].replace("\n", "\r\n").encode("utf-8")
        with tempfile.TemporaryDirectory() as directory, helper.ControlledResearchModel(helper.research_command(fixture)) as model:
            original = model.server.RequestHandlerClass
            failures = []
            class FirstUnavailable(original):
                def do_POST(self):
                    if failures:
                        return super().do_POST()
                    failures.append(True)
                    self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    body = b'{"error":{"message":"controlled retry fixture","type":"controlled_fixture"}}'
                    self.send_response(503)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(body)
                    self.close_connection = True
            model.server.RequestHandlerClass = FirstUnavailable
            runtime = self.runtime(directory, model.base_url, provider="controlled", model="controlled-fixture", key=helper.DUMMY_KEY)
            harness = self.harness_type(runtime=runtime, runtime_revision=runtime.settings.runtime_revision)
            try:
                task_id = harness.submit(key="retry-then-research", timeout_seconds=60).json()["id"]
                self.assertEqual(harness.execute_next()["state"], "queued")
                final = harness.execute_next()
                self.assertEqual((final["state"], final["attempt"]), ("publishing", 2))
                self.assertEqual(failures, [True])
                self.assertEqual(model.request_count, 3)
                self.assertEqual(model.errors, [])
                harness.projection.reconcile()
                snapshot = harness.public_http.get("/scientific-research/" + task_id).json()
                self.assertEqual(snapshot["state"], "succeeded")
                self.assertEqual(harness.transport.count("POST", "/publication-ack"), 1)
                self.assertEqual(harness.public_http.get("/scientific-download/" + task_id).content, expected)
                self.assertEqual(harness.db._query("SELECT * FROM scientific_notifications"), [])
            finally:
                harness.close()


if __name__ == "__main__":
    unittest.main()
