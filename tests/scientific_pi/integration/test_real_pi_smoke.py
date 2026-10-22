"""Actual pinned Pi CLI/tool protocol with a loopback-only synthetic model.

This test requires the parent's already-installed reviewed npm tree and Node.
It does not install packages, contact a real provider, or validate science.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
import uuid

from pi_worker.research_tools import BUNDLE_MANIFEST, BUNDLE_REVISION, SKILL_PATH
from pi_worker.runtime import PI_VERSION, PiRuntime, RuntimeSettings
from scientific_agent.pi_contracts import input_digest

ROOT = Path(__file__).resolve().parents[3]
CLI = ROOT / "pi_worker" / "node" / "node_modules" / "@mariozechner" / "pi-coding-agent" / "dist" / "cli.js"
MARKER = "SCIENTIFIC_PI_LOCAL_TOOL_MARKER"
CALL_ID = "call_local_smoke_marker"
DUMMY_API_KEY = "local-loopback-fixture-dummy-key"


class LoopbackModel:
    """Two deterministic OpenAI chat-completions SSE responses, IPv4 loopback."""
    def __init__(self):
        self.requests = []
        self.errors = []
        self.authorization_observed = []
        self.lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format, *args):
                # Do not print prompts, auth headers or model request bodies.
                return

            def do_POST(self):
                try:
                    if self.path != "/v1/chat/completions":
                        raise ValueError("unexpected fixture model endpoint")
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 2 * 1024 * 1024:
                        raise ValueError("fixture request exceeded bounded content limit")
                    request = json.loads(self.rfile.read(length))
                    if request.get("model") != "fixture-model" or request.get("stream") is not True:
                        raise ValueError("expected fixture model and streaming request")
                    with owner.lock:
                        index = len(owner.requests)
                        owner.requests.append(request)
                        owner.authorization_observed.append(self.headers.get("Authorization") == "Bearer " + DUMMY_API_KEY)
                    if index == 0:
                        tool_call = {"index": 0, "id": CALL_ID, "type": "function", "function": {
                            "name": "bash", "arguments": json.dumps({"command": "echo " + MARKER, "timeout": 5})}}
                        deltas = [({"role": "assistant", "content": ""}, None),
                                  ({"tool_calls": [tool_call]}, None), ({}, "tool_calls")]
                    elif index == 1:
                        deltas = [({"role": "assistant", "content": "Local CLI smoke fixture complete."}, None), ({}, "stop")]
                    else:
                        raise ValueError("actual Pi made more than the expected two model requests")
                    chunks = []
                    for delta, finish in deltas:
                        chunk = {"id": "chatcmpl-local-smoke-" + str(index + 1), "object": "chat.completion.chunk",
                                 "created": int(time.time()), "model": "fixture-model",
                                 "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                        chunks.append("data: " + json.dumps(chunk) + "\n\n")
                    body = ("".join(chunks) + "data: [DONE]\n\n").encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(body)
                    self.wfile.flush()
                    self.close_connection = True
                except Exception as exc:
                    with owner.lock:
                        owner.errors.append(type(exc).__name__ + ": " + str(exc))
                    body = b'{"error":{"message":"local fixture request failed"}}'
                    try:
                        self.send_response(500)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.wfile.write(body)
                    except OSError:
                        pass
                    self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server.timeout = 1
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    @property
    def base_url(self):
        return "http://127.0.0.1:" + str(self.server.server_port) + "/v1"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


class ActualPiLocalSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not CLI.is_file():
            raise unittest.SkipTest("Reviewed local Pi npm dependency absent; parent npm ci required, no automatic install")
        node = shutil.which("node")
        if node is None:
            raise unittest.SkipTest("Node executable absent; actual Pi CLI smoke not run")
        cls.node = str(Path(node).resolve())

    def test_pinned_actual_cli_bash_tool_result_returns_to_loopback_model(self):
        started = time.monotonic()
        with tempfile.TemporaryDirectory() as directory, LoopbackModel() as model:
            workspace = Path(directory).resolve() / "fresh-attempt"
            now = datetime.now(timezone.utc)
            # The wire contract minimum timeout is 60s; the absolute deadline is
            # deliberately shorter so the entire smoke remains bounded below45s.
            job = {"schema_version": "1", "task_id": str(uuid.uuid4()),
                   "topic": "Local CLI mechanism smoke only; synthetic model controls one harmless echo tool call",
                   "user_id": "local-smoke-user", "engine": "pi", "mode": "brief",
                   "timeout_seconds": 60, "created_at": now.isoformat().replace("+00:00", "Z"),
                   "execution_deadline_at": (now + timedelta(seconds=35)).isoformat().replace("+00:00", "Z"),
                   "skill_bundle_revision": BUNDLE_REVISION, "runtime_revision": "actual-cli-local-smoke-v1"}
            job["input_digest"] = input_digest(job)
            settings = RuntimeSettings(
                command=(self.node, str(CLI)), node_command=(self.node,),
                provider="fixture", model="fixture-model", fixture_mode=True,
                model_base_url=model.base_url, model_api="openai-completions",
                model_env={"SCIENTIFIC_MODEL_API_KEY": DUMMY_API_KEY},
                skill_paths=(SKILL_PATH,), skill_hashes=(hashlib.sha256(SKILL_PATH.read_bytes()).hexdigest(),),
                bundle_manifest=BUNDLE_MANIFEST,
                bundle_manifest_sha256=hashlib.sha256(BUNDLE_MANIFEST.read_bytes()).hexdigest(),
                skill_bundle_revision=BUNDLE_REVISION, runtime_revision=job["runtime_revision"],
                tool_pythonpath=ROOT, idle_timeout_seconds=15,
                term_grace_seconds=0.2, kill_grace_seconds=2,
            )
            runtime = PiRuntime(settings)
            events = []
            try:
                runtime.execute(job, workspace, events.append, threading.Event())
            except Exception as exc:
                # Bounded, non-sensitive diagnostics identify actual incompatibility;
                # never turn a present-but-failing CLI into a skipped/passing test.
                self.fail(f"Actual Pi CLI smoke failed: {type(exc).__name__}: {exc}; model_requests={len(model.requests)}; server_errors={model.errors}; events={[event.get('event', event.get('type')) for event in events]}")
            self.assertEqual(model.errors, [])
            self.assertEqual(len(model.requests), 2, "expected tool request then final assistant request")
            self.assertEqual(model.authorization_observed, [True, True])
            tools = model.requests[0].get("tools", [])
            self.assertIn("bash", [tool.get("function", {}).get("name") for tool in tools])
            system_prompt = "\n".join(str(message.get("content", "")) for message in model.requests[0]["messages"] if message.get("role") in {"system", "developer"})
            self.assertIn("<available_skills>", system_prompt)
            self.assertIn("<name>research-procedure</name>", system_prompt)
            self.assertIn(str(SKILL_PATH).replace("\\", "/"), system_prompt.replace("\\", "/"))
            second_messages = model.requests[1]["messages"]
            tool_results = [message for message in second_messages if message.get("role") == "tool"]
            self.assertEqual(len(tool_results), 1, "actual Pi must return the executed tool result")
            self.assertEqual(tool_results[0].get("tool_call_id"), CALL_ID)
            self.assertIn(MARKER, tool_results[0].get("content", ""))
            self.assertNotIn("error", tool_results[0].get("content", "").lower())
            self.assertTrue(any(message.get("role") == "assistant" and any(call.get("id") == CALL_ID for call in message.get("tool_calls", [])) for message in second_messages))
            observed_events = [event.get("event") for event in events if event.get("type") == "pi_event"]
            for expected in ("agent_start", "tool_execution_start", "tool_execution_end", "message_end", "agent_end"):
                self.assertIn(expected, observed_events)
            self.assertEqual(events[-1]["type"], "runtime_finished")
            provenance = json.loads((workspace / "provenance.json").read_text(encoding="utf-8"))
            self.assertEqual(provenance["pi_version"], PI_VERSION)
            self.assertEqual(provenance["model"], "fixture-model")
            self.assertEqual(provenance["provider"], "fixture")
            self.assertRegex(provenance["node_version"], r"^\d+\.\d+\.\d+$")
            self.assertIs(provenance["fixture_mode"], True)
            self.assertIsNone(provenance["image_digest"])
            self.assertIsNone(provenance["token_usage"])
            self.assertEqual(provenance["skill_bundle_revision"], BUNDLE_REVISION)
            agent = workspace / ".home" / ".pi" / "agent"
            configured = (agent / "models.json").read_text(encoding="utf-8")
            self.assertNotIn(DUMMY_API_KEY, configured)
            self.assertEqual(json.loads(configured)["providers"]["fixture"]["apiKey"], "SCIENTIFIC_MODEL_API_KEY")
            self.assertNotIn(DUMMY_API_KEY, json.dumps(events))
            self.assertNotIn(MARKER, json.dumps(events), "runtime events must not expose tool result bodies")
            self.assertFalse(list((workspace / ".home").glob("runtime-*.json")))
            self.assertLess(time.monotonic() - started, 45, "actual CLI smoke exceeded bounded runtime")


if __name__ == "__main__":
    unittest.main()
