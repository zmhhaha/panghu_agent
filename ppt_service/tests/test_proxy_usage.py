"""Tests use synthetic provider responses only; no external model requests."""
import contextlib
import http.client
import io
import json
import os
import select
import socket
import tempfile
import time
import threading
import unittest
import uuid
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ppt_service.worker import model_proxy
from ppt_service.worker import proxy_usage as usage

FIELDS = {
    "event", "proxy_request_id", "job_id", "attempt", "phase", "started_at", "ended_at",
    "outcome", "model", "provider_response_id", "provider_request_id", "input_tokens",
    "output_tokens", "cached_tokens", "reasoning_tokens", "cost_usd", "usage_status",
}


def response_object(**updates):
    result = {
        "object": "response", "id": "resp_provider_123", "model": "gpt-6.1-sol-actual",
        "status": "completed", "output": [{"text": "PRIVATE RESPONSE NEVER SAVE"}],
        "usage": {"input_tokens": 100, "output_tokens": 40, "total_tokens": 140,
                  "input_tokens_details": {"cached_tokens": 60},
                  "output_tokens_details": {"reasoning_tokens": 15}},
    }
    result.update(updates)
    return result


def sse_response(value=None, kind="response.completed", newline=b"\n"):
    value = response_object() if value is None else value
    return (b"event: " + kind.encode() + newline + b"data: "
            + json.dumps({"type": kind, "response": value}).encode() + newline + newline)


class LedgerCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "usage.jsonl"
        self.ledger = usage.UsageLedger(str(self.path), job_id="operator-job", attempt=2,
                                       phase="slides")

    def rows(self):
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]

    def observer(self, media_type="application/json"):
        observer = self.ledger.begin("gpt-6.1-sol")
        self.assertTrue(observer.start_ok)
        headers = Message()
        headers["x-request-id"] = "trusted-provider-request-id"
        observer.response_headers(headers, media_type)
        return observer


class ObservationTests(LedgerCase):
    def test_disabled_and_operator_environment(self):
        self.assertFalse(usage.UsageLedger.from_env({}).enabled)
        self.assertIsNone(usage.UsageLedger().begin("model"))
        env = {"PPT_PROXY_USAGE_PATH": str(self.path), "PPT_USAGE_JOB_ID": "job-fixed",
               "PPT_USAGE_ATTEMPT": "3", "PPT_USAGE_PHASE": "generate"}
        ledger = usage.UsageLedger.from_env(env)
        env["PPT_USAGE_JOB_ID"] = "changed-later"
        record = ledger.begin("gpt-6.1-sol").record
        self.assertEqual((record["job_id"], record["attempt"], record["phase"]),
                         ("job-fixed", 3, "generate"))
        for key, value in (("PPT_USAGE_ATTEMPT", "-1"), ("PPT_USAGE_JOB_ID", "private\ntext")):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "invalid_usage_configuration"):
                usage.UsageLedger.from_env({**env, key: value})

    def test_start_is_durable_and_finish_is_idempotent_whitelist(self):
        with patch.object(usage.os, "fsync", wraps=os.fsync) as fsync:
            observer = self.observer()
            initial = self.rows()[0]
            self.assertEqual(initial["event"], "request_started")
            self.assertEqual(initial["outcome"], "in_progress")
            self.assertEqual(str(uuid.UUID(initial["proxy_request_id"])), initial["proxy_request_id"])
            for key in ("input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens", "cost_usd"):
                self.assertIsNone(initial[key])
            self.assertIsNone(initial["ended_at"])
            observer.feed(json.dumps(response_object()).encode())
            self.assertTrue(observer.finish("completed"))
            self.assertTrue(observer.finish("completed"))
            self.assertEqual(fsync.call_count, 2)
        started, finished = self.rows()
        self.assertEqual(set(started), FIELDS)
        self.assertEqual(set(finished), FIELDS)
        self.assertEqual(finished["event"], "request_finished")
        self.assertEqual(started["proxy_request_id"], finished["proxy_request_id"])
        self.assertEqual(started["started_at"], finished["started_at"])
        self.assertGreaterEqual(finished["ended_at"], finished["started_at"])
        self.assertEqual(finished["model"], "gpt-6.1-sol-actual")
        self.assertEqual(finished["provider_response_id"], "resp_provider_123")
        self.assertEqual(finished["provider_request_id"], "trusted-provider-request-id")
        self.assertEqual((finished["input_tokens"], finished["output_tokens"],
                          finished["cached_tokens"], finished["reasoning_tokens"]), (100, 40, 60, 15))
        self.assertEqual(finished["usage_status"], "known")
        self.assertIsNone(finished["cost_usd"])
        self.assertNotIn("PRIVATE", self.path.read_text())
        self.assertNotIn("total_tokens", finished)

    def test_cost_only_when_explicitly_reported_by_provider(self):
        observer = self.observer()
        value = response_object()
        value["usage"]["cost_usd"] = 0.0123
        observer.feed(json.dumps(value).encode())
        observer.finish("completed")
        self.assertEqual(self.rows()[-1]["cost_usd"], 0.0123)

    def test_json_overflow_and_invalid_data_are_unknown_not_zero(self):
        for raw in (b"x" * (usage.MAX_OBSERVATION_BYTES + 1), b"not json",
                    b'{"usage":{"input_tokens":NaN}}'):
            observer = self.observer()
            observer.feed(raw)
            self.assertLessEqual(len(observer._json_body), usage.MAX_OBSERVATION_BYTES)
            observer.finish("completed")
            row = self.rows()[-1]
            self.assertEqual(row["usage_status"], "unknown")
            self.assertIsNone(row["input_tokens"])
            self.assertIsNone(row["output_tokens"])
            self.assertIsNone(row["cost_usd"])

    def test_zero_is_known_only_when_provider_reported_zero(self):
        observer = self.observer()
        observer.feed(json.dumps(response_object(usage={"input_tokens": 0, "output_tokens": 0})).encode())
        observer.finish("completed")
        row = self.rows()[-1]
        self.assertEqual((row["input_tokens"], row["output_tokens"], row["usage_status"]), (0, 0, "known"))
        self.assertIsNone(row["cached_tokens"])

    def test_invalid_token_types_and_inconsistent_subsets(self):
        for counts in ({"input_tokens": True, "output_tokens": -1},
                       {"input_tokens": "12", "output_tokens": 5},
                       {"input_tokens": 2, "output_tokens": 1,
                        "input_tokens_details": {"cached_tokens": 3}}):
            observer = self.observer()
            observer.feed(json.dumps(response_object(usage=counts)).encode())
            observer.finish("completed")
            self.assertEqual(self.rows()[-1]["usage_status"], "unknown")

    def test_sse_arbitrary_chunk_boundaries_crlf_and_duplicate_snapshots(self):
        for newline in (b"\n", b"\r\n", b"\r"):
            raw = sse_response(newline=newline) * 2 + b"data: [DONE]" + newline + newline
            for width in (1, 2, 17, 65536):
                observer = self.observer("text/event-stream")
                for offset in range(0, len(raw), width):
                    observer.feed(raw[offset:offset + width])
                observer.finish("completed")
                row = self.rows()[-1]
                self.assertEqual(row["usage_status"], "known")
                self.assertEqual((row["input_tokens"], row["output_tokens"]), (100, 40))

    def test_sse_overflow_recovers_but_stays_unknown(self):
        observer = self.observer("text/event-stream")
        observer.feed(b"data: ")
        chunk = b"x" * 65536
        for _ in range(20):
            observer.feed(chunk)
            self.assertLessEqual(len(observer._line) + len(observer._data), usage.MAX_OBSERVATION_BYTES)
        observer.feed(b"\n\n" + sse_response())
        observer.finish("completed")
        row = self.rows()[-1]
        self.assertEqual(row["usage_status"], "unknown")
        self.assertEqual(row["input_tokens"], 100)

    def test_failure_cancel_disconnect_preserve_seen_counts_as_unknown(self):
        for outcome in ("client_disconnected", "upstream_disconnected", "upstream_timeout"):
            observer = self.observer("text/event-stream")
            observer.feed(sse_response())
            observer.finish(outcome)
            row = self.rows()[-1]
            self.assertEqual((row["outcome"], row["usage_status"], row["input_tokens"]),
                             (outcome, "unknown", 100))
        for status in ("failed", "incomplete", "cancelled"):
            observer = self.observer("text/event-stream")
            observer.feed(sse_response(response_object(status=status), "response." + status))
            observer.finish("completed")
            row = self.rows()[-1]
            self.assertEqual(row["outcome"], "provider_" + status)
            self.assertEqual(row["usage_status"], "unknown")
            self.assertEqual(row["output_tokens"], 40)

    def test_completed_json_observation_survives_client_disconnect(self):
        observer = self.observer()
        observer.feed(json.dumps(response_object()).encode())
        observer.record["prompt"] = "PRIVATE prompt must be excluded"
        observer.finish("client_disconnected")
        row = self.rows()[-1]
        self.assertEqual(row["usage_status"], "unknown")
        self.assertEqual(row["input_tokens"], 100)
        self.assertNotIn("prompt", row)
        self.assertNotIn("PRIVATE", self.path.read_text())

    def test_unterminated_stream_missing_terminal_and_invalid_ids(self):
        observer = self.observer("text/event-stream")
        observer.feed(sse_response().rstrip(b"\n"))
        observer.finish("completed")
        self.assertEqual(self.rows()[-1]["usage_status"], "unknown")
        self.assertIsNone(self.rows()[-1]["input_tokens"])
        observer = self.observer()
        headers = Message()
        headers["x-request-id"] = "id-1"
        headers["x-request-id"] = "id-2"
        observer.response_headers(headers, "application/json")
        observer.feed(json.dumps(response_object(id="private response body\n")).encode())
        observer.finish("completed")
        row = self.rows()[-1]
        self.assertIsNone(row["provider_request_id"])
        self.assertIsNone(row["provider_response_id"])

    def test_failed_finish_keeps_started_event_and_sticky_failure(self):
        observer = self.observer()
        observer.feed(json.dumps(response_object()).encode())
        output = io.StringIO()
        with patch.object(usage.os, "fsync", side_effect=OSError("PRIVATE path credentials")):
            with contextlib.redirect_stderr(output):
                self.assertFalse(observer.finish("completed"))
        self.assertFalse(self.ledger.healthy)
        self.assertEqual(output.getvalue(), "proxy_usage_write_failed\n")
        self.assertEqual(self.rows()[0]["event"], "request_started")
        self.assertFalse(self.ledger.begin("gpt-6.1-sol").start_ok)

    def test_concurrent_two_phase_appends_are_valid_json(self):
        def request():
            observer = self.observer()
            observer.feed(json.dumps(response_object()).encode())
            observer.finish("completed")
        threads = [threading.Thread(target=request) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        rows = self.rows()
        self.assertEqual(len(rows), 20)
        ids = {row["proxy_request_id"] for row in rows}
        self.assertEqual(len(ids), 10)
        for request_id in ids:
            self.assertEqual([row["event"] for row in rows if row["proxy_request_id"] == request_id],
                             ["request_started", "request_finished"])


    def test_shutdown_finishes_pending_unknown_and_seals_admission(self):
        pending = self.ledger.begin("gpt-6.1-sol")
        observed = self.observer()
        observed.feed(json.dumps(response_object()).encode())
        self.ledger.shutdown()
        self.ledger.shutdown()
        self.assertFalse(self.ledger.begin("gpt-6.1-sol").start_ok)
        pending.finish("completed")
        observed.feed(b"late data must not mutate a finished observation")
        observed.finish("completed")
        rows = self.rows()
        self.assertEqual(len(rows), 4)
        finished = [r for r in rows if r["event"] == "request_finished"]
        self.assertEqual(len(finished), 2)
        for row in finished:
            self.assertEqual((row["outcome"], row["usage_status"], row["cost_usd"]),
                             ("proxy_shutdown", "unknown", None))
            self.assertIsNotNone(row["ended_at"])
        by_id = {row["proxy_request_id"]: row for row in finished}
        self.assertIsNone(by_id[pending.record["proxy_request_id"]]["input_tokens"])
        self.assertEqual(by_id[observed.record["proxy_request_id"]]["input_tokens"], 100)

    def test_shutdown_races_normal_finish_without_duplicate_or_deadlock(self):
        observer = self.observer()
        observer.feed(json.dumps(response_object()).encode())
        gate = threading.Barrier(3)
        def finish():
            gate.wait()
            observer.finish("completed")
        def shutdown():
            gate.wait()
            self.ledger.shutdown()
        threads = [threading.Thread(target=finish), threading.Thread(target=shutdown)]
        for thread in threads:
            thread.start()
        gate.wait()
        for thread in threads:
            thread.join(3)
            self.assertFalse(thread.is_alive())
        started, finished = self.rows()
        self.assertEqual(started["proxy_request_id"], finished["proxy_request_id"])
        self.assertIn(finished["outcome"], {"completed", "proxy_shutdown"})
        self.assertEqual(finished["usage_status"],
                         "known" if finished["outcome"] == "completed" else "unknown")


class SyntheticResponse:
    def __init__(self, chunks, status=200, content_type="application/json", length=None):
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["x-request-id"] = "provider-id-from-upstream"
        self.length = length
        self.chunks = iter(chunks)

    def read1(self, size):
        chunk = next(self.chunks, b"")
        if isinstance(chunk, Exception):
            raise chunk
        return chunk


class ProxyUsageIntegrationTests(LedgerCase):
    def setUp(self):
        super().setUp()
        self.response = SyntheticResponse([json.dumps(response_object()).encode()])
        self.calls = 0
        outer = self

        class Connector:
            def request(self, *args, **kwargs):
                outer.calls += 1
                # Durable start must exist before sending the model request.
                outer.assertEqual(outer.rows()[-1]["event"], "request_started")

            def getresponse(self):
                return outer.response

            def close(self):
                pass

        self.server = model_proxy.ModelProxyServer(
            ("127.0.0.1", 0), model_proxy.ProxyConfig("worker-key", "provider-key"),
            connector=lambda *args, **kwargs: Connector(), usage_ledger=self.ledger)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def request(self, method="POST", path="/v1/responses", body=None, auth="Bearer worker-key"):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        body = json.dumps({"model": "gpt-6.1-sol", "input": "PRIVATE PROMPT"}).encode() if body is None else body
        connection.request(method, path, body=body, headers={
            "Authorization": auth, "x-request-id": "worker-fake-request-id",
            "PPT_USAGE_JOB_ID": "worker-fake-job", "X-Job-Id": "worker-fake-job"})
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def test_only_upstream_metadata_and_operator_attribution_are_recorded(self):
        self.assertEqual(self.request()[0], 200)
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        row = rows[-1]
        self.assertEqual(row["job_id"], "operator-job")
        self.assertEqual(row["provider_request_id"], "provider-id-from-upstream")
        self.assertEqual(row["usage_status"], "known")
        raw = self.path.read_text()
        for secret in ("PRIVATE", "worker-fake", "provider-key", "worker-key"):
            self.assertNotIn(secret, raw)

    def test_auth_and_validation_rejections_do_not_create_ledger_records(self):
        self.assertEqual(self.request(body=b"", auth="Bearer wrong")[0], 401)
        self.assertEqual(self.request(body=b'{}')[0], 400)
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.calls, 0)

    def test_first_write_failure_fail_closes_and_health_is_unhealthy(self):
        output = io.StringIO()
        with patch.object(usage.os, "open", side_effect=OSError("PRIVATE secret path")):
            with contextlib.redirect_stderr(output):
                status, body = self.request()
        self.assertEqual(status, 503)
        self.assertEqual(self.calls, 0)
        self.assertNotIn(b"PRIVATE", body)
        self.assertEqual(output.getvalue(), "proxy_usage_write_failed\n")
        self.assertEqual(self.request(method="GET", path="/healthz", body=b"")[0], 503)
        self.assertEqual(self.request()[0], 503)
        self.assertEqual(self.calls, 0)

    def test_upstream_connection_errors_are_finalized_with_unknown_counts(self):
        with patch.object(self.server, "connector", side_effect=TimeoutError("PRIVATE error")):
            self.assertEqual(self.request()[0], 504)
        started, finished = self.rows()
        self.assertEqual(started["proxy_request_id"], finished["proxy_request_id"])
        self.assertEqual(finished["outcome"], "upstream_timeout")
        self.assertIsNone(finished["input_tokens"])
        self.assertIsNone(finished["cost_usd"])

    def test_provider_error_body_is_never_observed(self):
        self.response = SyntheticResponse([json.dumps(response_object()).encode()], status=500)
        self.assertEqual(self.request()[0], 502)
        row = self.rows()[-1]
        self.assertEqual(row["outcome"], "upstream_error")
        self.assertIsNone(row["input_tokens"])
        self.assertIsNone(row["provider_response_id"])
        self.assertEqual(row["provider_request_id"], "provider-id-from-upstream")

    def test_stream_disconnect_preserves_observed_terminal_tokens_as_unknown(self):
        self.response = SyntheticResponse([sse_response(), OSError("PRIVATE broken stream")],
                                          content_type="text/event-stream")
        self.assertEqual(self.request()[0], 200)
        row = self.rows()[-1]
        self.assertEqual(row["outcome"], "upstream_disconnected")
        self.assertEqual(row["usage_status"], "unknown")
        self.assertEqual(row["input_tokens"], 100)

    def test_json_and_sse_observation_overflow_does_not_interrupt_forwarding(self):
        for media_type, raw in (("application/json", b"x" * 1100),
                                ("text/event-stream", b"data: " + b"x" * 1100 + b"\n\n")):
            self.response = SyntheticResponse([raw], content_type=media_type)
            with patch.object(usage, "MAX_OBSERVATION_BYTES", 1024):
                status, body = self.request()
            self.assertEqual((status, body), (200, raw))
            self.assertEqual(self.rows()[-1]["usage_status"], "unknown")

    def test_finish_write_failure_degrades_health_and_preserves_started(self):
        original_append = self.ledger.append

        def fail_finished(record):
            if record["event"] == "request_finished":
                with patch.object(usage.os, "open", side_effect=OSError("PRIVATE path")):
                    return original_append(record)
            return original_append(record)

        output = io.StringIO()
        with patch.object(self.ledger, "append", side_effect=fail_finished):
            with contextlib.redirect_stderr(output):
                self.assertEqual(self.request()[0], 200)
        self.assertEqual([row["event"] for row in self.rows()], ["request_started"])
        self.assertEqual(output.getvalue(), "proxy_usage_write_failed\n")
        self.assertEqual(self.request(method="GET", path="/healthz", body=b"")[0], 503)

    def test_client_write_error_finalizes_observed_json_tokens(self):
        writer = Mock()
        writer.write.side_effect = BrokenPipeError("PRIVATE worker disconnected")
        handler = SimpleNamespace(server=self.server, wfile=writer, _headers=Mock(), _error=Mock())
        model_proxy.ModelProxyHandler._forward(handler, b'{"model":"gpt-6.1-sol"}')
        row = self.rows()[-1]
        self.assertEqual(row["outcome"], "client_disconnected")
        self.assertEqual(row["usage_status"], "unknown")
        self.assertEqual(row["input_tokens"], 100)

    def test_server_close_finalizes_request_blocked_before_response_headers(self):
        entered = threading.Event()
        release = threading.Event()
        outer = self
        class BlockedConnector:
            def request(self, *args, **kwargs):
                pass
            def getresponse(self):
                entered.set()
                release.wait(5)
                return outer.response
            def close(self):
                pass
        self.server.connector = lambda *args, **kwargs: BlockedConnector()
        writer = io.BytesIO()
        handler = SimpleNamespace(server=self.server, wfile=writer, _headers=Mock(), _error=Mock())
        request = threading.Thread(target=model_proxy.ModelProxyHandler._forward,
                                   args=(handler, b'{"model":"gpt-6.1-sol"}'), daemon=True)
        request.start()
        try:
            self.assertTrue(entered.wait(3))
            self.server.shutdown()
            self.server.server_close()
            self.assertTrue(request.is_alive())  # No wait for blocked upstream I/O.
            started, finished = self.rows()
            self.assertEqual(started["proxy_request_id"], finished["proxy_request_id"])
            self.assertEqual((finished["outcome"], finished["usage_status"]),
                             ("proxy_shutdown", "unknown"))
            self.assertIsNone(finished["input_tokens"])
            self.assertIsNone(finished["cost_usd"])
        finally:
            release.set()
            request.join(3)
        self.assertFalse(request.is_alive())
        self.assertEqual(len(self.rows()), 2)  # Late response/finish cannot rewrite it.

    def test_truncated_content_length_is_unknown(self):
        self.response = SyntheticResponse([json.dumps(response_object()).encode()], length=10)
        self.assertEqual(self.request()[0], 200)
        row = self.rows()[-1]
        self.assertEqual(row["outcome"], "upstream_disconnected")
        self.assertEqual(row["usage_status"], "unknown")


class ProxyShutdownTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX process signals")
    def test_real_sigterm_exits_with_pending_request_finalized(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.jsonl"
            read_fd, write_fd = os.pipe()
            pid = os.fork()
            if pid == 0:
                os.close(read_fd)
                try:
                    config = model_proxy.ProxyConfig("worker-key", "provider-key")
                    class BlockedConnector:
                        def request(self, *args, **kwargs):
                            pass
                        def getresponse(self):
                            threading.Event().wait(60)
                        def close(self):
                            pass
                    server = model_proxy.ModelProxyServer(
                        ("127.0.0.1", 0), config,
                        connector=lambda *args, **kwargs: BlockedConnector(),
                        usage_ledger=usage.UsageLedger(str(path)))
                    # Publish readiness only after main installs the SIGTERM handler.
                    real_serve = server.serve_forever
                    def serve(**kwargs):
                        os.write(write_fd, str(server.server_port).encode() + b"\n")
                        real_serve(**kwargs)
                    server.serve_forever = serve
                    with patch.object(model_proxy.ProxyConfig, "from_env", return_value=config), \
                            patch.object(model_proxy, "ModelProxyServer", return_value=server):
                        model_proxy.main()
                    os._exit(0)
                except BaseException:
                    os._exit(1)
            os.close(write_fd)
            reaped = False
            try:
                self.assertTrue(select.select([read_fd], [], [], 3)[0])
                port = int(os.read(read_fd, 64).strip())
                with socket.create_connection(("127.0.0.1", port), timeout=3) as client:
                    body = b'{"model":"gpt-6.1-sol"}'
                    client.sendall(b"POST /v1/responses HTTP/1.1\r\nHost: localhost\r\n"
                                   b"Authorization: Bearer worker-key\r\nContent-Length: "
                                   + str(len(body)).encode() + b"\r\n\r\n" + body)
                    deadline = time.monotonic() + 3
                    while not path.exists() and time.monotonic() < deadline:
                        threading.Event().wait(0.01)
                    self.assertTrue(path.exists())
                    os.kill(pid, model_proxy.signal.SIGTERM)
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        child, status = os.waitpid(pid, os.WNOHANG)
                        if child:
                            reaped = True
                            self.assertEqual(os.waitstatus_to_exitcode(status), 0)
                            break
                        threading.Event().wait(0.01)
                    self.assertTrue(reaped, "SIGTERM teardown waited for the blocked upstream")
                rows = [json.loads(line) for line in path.read_text().splitlines()]
                self.assertEqual([row["event"] for row in rows], ["request_started", "request_finished"])
                self.assertEqual(rows[0]["proxy_request_id"], rows[1]["proxy_request_id"])
                self.assertEqual((rows[1]["outcome"], rows[1]["usage_status"], rows[1]["input_tokens"],
                                  rows[1]["cost_usd"]), ("proxy_shutdown", "unknown", None, None))
            finally:
                os.close(read_fd)
                if not reaped:
                    os.kill(pid, model_proxy.signal.SIGKILL)
                    os.waitpid(pid, 0)

    def test_main_handles_sigterm_without_same_thread_shutdown(self):
        config = model_proxy.ProxyConfig("worker-key", "provider-key")
        server = Mock()
        server.shutdown_requested = threading.Event()
        handlers = {}
        previous = object()
        actions = model_proxy.ModelProxyServer.service_actions
        def register(signum, handler):
            handlers[signum] = handler
            return previous
        def serve(**kwargs):
            handlers[model_proxy.signal.SIGTERM](model_proxy.signal.SIGTERM, None)
            self.assertTrue(server.shutdown_requested.is_set())
            actions(server)
        server.serve_forever.side_effect = serve
        with patch.object(model_proxy.ProxyConfig, "from_env", return_value=config), \
                patch.object(model_proxy, "ModelProxyServer", return_value=server), \
                patch.object(model_proxy.signal, "signal", side_effect=register) as register_mock:
            model_proxy.main()
        server.server_close.assert_called_once_with()
        server.shutdown.assert_not_called()
        self.assertIs(register_mock.call_args.args[1], previous)


if __name__ == "__main__":
    unittest.main()
