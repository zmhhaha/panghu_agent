"""Real pinned Pi reads its skill and publishes a controlled research fixture.

Only the model and academic records are synthetic. Pi's tool subprocess, research
CLI, scheduler, output validator, report projection and downloads are real. This
is a mechanism test, not live-provider or scientific-quality acceptance.
"""
from __future__ import annotations

import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shlex
import shutil
import sys
import threading
import time
import unittest

from harness import IntegrationHarness, research_fixtures
from pi_worker.research_tools import BUNDLE_MANIFEST, BUNDLE_REVISION, SKILL_PATH
from pi_worker.runtime import PI_VERSION, PiRuntime, RuntimeSettings
from scientific_agent.pi_contracts import report_sha256, result_digest


ROOT = Path(__file__).resolve().parents[3]
CLI = ROOT / "pi_worker/node/node_modules/@mariozechner/pi-coding-agent/dist/cli.js"
DUMMY_KEY = "controlled-research-loopback-dummy-key"
RUNTIME_REVISION = "actual-cli-controlled-research-v1"


def research_command(fixture):
    """A real bash/Python tool writes explicit fixture bytes and runs the CLI."""
    script = (
        "import json\nfrom pathlib import Path\n"
        "from pi_worker.research_tools.__main__ import main\n"
        "data = " + repr(fixture) + "\n"
        "for name in ('searches','papers','screening','evidence','claims'):\n"
        "    text = ''.join(json.dumps(row, ensure_ascii=False) + '\\n' for row in data[name])\n"
        "    Path(name + '.jsonl').write_bytes(text.encode('utf-8'))\n"
        "Path('references.json').write_bytes(json.dumps(data['references']).encode('utf-8'))\n"
        "Path('report.md').write_bytes(data['report'].replace('\\n','\\r\\n').encode('utf-8'))\n"
        "raise SystemExit(main(['validate','--workspace','.']))\n"
    )
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    # shlex quoting is for Pi's bash tool, including Git Bash on Windows.
    python = str(Path(sys.executable).resolve()).replace("\\", "/")
    expression = "import base64;exec(base64.b64decode(" + repr(encoded) + "))"
    return shlex.quote(python) + " -c " + shlex.quote(expression)


class ControlledResearchModel:
    """Three loopback-only SSE turns: read skill, run research CLI, finish."""
    def __init__(self, command):
        self.command = command
        self.request_count = 0
        self.skill_read = False
        self.validation_returned = False
        self.authenticated = []
        self.errors = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def do_POST(self):
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if self.path != "/v1/chat/completions" or not 0 < length <= 2 * 1024 * 1024:
                        raise ValueError("unexpected endpoint or request size")
                    request = json.loads(self.rfile.read(length))
                    if request.get("model") != "controlled-fixture" or request.get("stream") is not True:
                        raise ValueError("unexpected model or stream mode")
                    owner.authenticated.append(self.headers.get("Authorization") == "Bearer " + DUMMY_KEY)
                    index = owner.request_count
                    owner.request_count += 1
                    tools = {m.get("tool_call_id"): m.get("content") for m in request["messages"] if m.get("role") == "tool"}
                    if index == 0:
                        tool_name, call_id = "read", "call_read_reviewed_skill"
                        arguments = {"path": str(SKILL_PATH)}
                    elif index == 1:
                        content = tools.get("call_read_reviewed_skill", "")
                        owner.skill_read = isinstance(content, str) and "# Auditable research procedure" in content and "No evidence means" in content
                        if not owner.skill_read:
                            raise ValueError("actual skill read result missing")
                        tool_name, call_id = "bash", "call_write_and_validate_research"
                        arguments = {"command": owner.command, "timeout": 10}
                    elif index == 2:
                        content = tools.get("call_write_and_validate_research", "")
                        owner.validation_returned = isinstance(content, str) and json.loads(content).get("valid") is True
                        if not owner.validation_returned:
                            raise ValueError("actual research CLI validation result missing")
                    else:
                        raise ValueError("unexpected fourth model request")
                    if index < 2:
                        call = {"index": 0, "id": call_id, "type": "function", "function": {
                            "name": tool_name, "arguments": json.dumps(arguments)}}
                        deltas = [({"role": "assistant", "content": ""}, None),
                                  ({"tool_calls": [call]}, None), ({}, "tool_calls")]
                    else:
                        deltas = [({"role": "assistant", "content": "Controlled synthetic research fixture written and validated."}, None), ({}, "stop")]
                    chunks = []
                    for delta, finish in deltas:
                        chunk = {"id": "chatcmpl-controlled-" + str(index), "object": "chat.completion.chunk",
                                 "created": int(time.time()), "model": "controlled-fixture",
                                 "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                        chunks.append("data: " + json.dumps(chunk) + "\n\n")
                    body = ("".join(chunks) + "data: [DONE]\n\n").encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                except Exception as exc:
                    # Never emit model requests or tool bodies in diagnostics.
                    owner.errors.append(type(exc).__name__)
                    body = b'{"error":{"message":"controlled fixture request failed"}}'
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()
                self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)

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


class ActualPiResearchIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not CLI.is_file() or shutil.which("node") is None:
            raise unittest.SkipTest("Pinned Pi npm tree and Node are required; no automatic install")
        cls.node = str(Path(shutil.which("node")).resolve())

    def test_actual_pi_reads_skill_validates_fixture_and_publishes_exact_download(self):
        started = time.monotonic()
        fixture = research_fixtures.abstract_only()
        fixture["report"] += "\nSynthetic mechanism test: 合成科研证据，不代表真实研究。\n"
        expected_report = fixture["report"].replace("\n", "\r\n").encode("utf-8")
        with ControlledResearchModel(research_command(fixture)) as model:
            runtime = PiRuntime(RuntimeSettings(
                command=(self.node, str(CLI)), node_command=(self.node,),
                provider="controlled", model="controlled-fixture", fixture_mode=True,
                model_base_url=model.base_url, model_api="openai-completions",
                model_env={"SCIENTIFIC_MODEL_API_KEY": DUMMY_KEY},
                skill_paths=(SKILL_PATH,), skill_hashes=(hashlib.sha256(SKILL_PATH.read_bytes()).hexdigest(),),
                bundle_manifest=BUNDLE_MANIFEST,
                bundle_manifest_sha256=hashlib.sha256(BUNDLE_MANIFEST.read_bytes()).hexdigest(),
                skill_bundle_revision=BUNDLE_REVISION, runtime_revision=RUNTIME_REVISION,
                # A protocol fixture, not an image identity or production evidence.
                image_digest="sha256:" + "0" * 64, tool_pythonpath=ROOT,
                idle_timeout_seconds=15, term_grace_seconds=.2, kill_grace_seconds=2,
            ))
            h = IntegrationHarness(runtime=runtime, runtime_revision=RUNTIME_REVISION)
            try:
                response = h.submit(key="actual-cli-controlled-research", timeout_seconds=60)
                self.assertEqual(response.status_code, 200, response.text)
                task_id = response.json()["id"]
                snapshot = h.execute_next()
                self.assertEqual(snapshot["state"], "publishing", {"snapshot": snapshot, "model_requests": model.request_count, "model_errors": model.errors})
                self.assertEqual(model.errors, [])
                self.assertEqual(model.request_count, 3)
                self.assertEqual(model.authenticated, [True, True, True])
                self.assertTrue(model.skill_read)
                self.assertTrue(model.validation_returned)
                result = h.worker.result(task_id)
                self.assertTrue(result["validation"]["valid"])
                self.assertEqual(result["provenance"]["pi_version"], PI_VERSION)
                self.assertTrue(result["provenance"]["fixture_mode"])
                self.assertEqual(result_digest(result), result["result_digest"])
                self.assertEqual(report_sha256(result["report_markdown"]), result["report_sha256"])
                self.assertEqual(result["report_markdown"].encode("utf-8"), expected_report)
                self.assertIsNone(h.db.get_report(task_id))
                self.assertNotIn(DUMMY_KEY, json.dumps(result))
                self.assertNotIn(h.TOKEN, json.dumps(result))

                h.projection.reconcile()
                task = h.public_http.get("/scientific-research/" + task_id).json()
                self.assertEqual((task["state"], task["status"], task["report_id"]), ("succeeded", "done", task_id))
                self.assertIsNone(task["notification_status"])
                self.assertEqual(h.db._query("SELECT * FROM scientific_notifications"), [])
                self.assertEqual(h.transport.count("POST", "/publication-ack"), 1)
                download = h.public_http.get("/scientific-download/" + task_id)
                self.assertEqual(download.status_code, 200)
                self.assertEqual(download.content, expected_report)
                self.assertEqual(hashlib.sha256(download.content).hexdigest(), result["report_sha256"])
                for entry in result["artifacts"]:
                    artifact = h.public_http.get("/scientific-research/" + task_id + "/artifacts/" + entry["artifact_id"])
                    self.assertEqual(artifact.status_code, 200, entry["artifact_id"])
                    self.assertEqual(hashlib.sha256(artifact.content).hexdigest(), entry["sha256"])
                # Establish explicit paper -> evidence -> claim -> numbered citation
                # relationships in the actual bytes served by the public API.
                def artifact_json(name):
                    return h.public_http.get("/scientific-research/" + task_id + "/artifacts/" + name).json()
                paper, evidence, claim = [artifact_json(name) for name in ("papers", "evidence", "claims")]
                search = artifact_json("searches")
                self.assertIn(search["source"], paper["sources"])
                self.assertEqual(search["result_count"], 1)
                self.assertEqual(evidence["paper_id"], paper["paper_id"])
                self.assertEqual(claim["evidence_ids"], [evidence["evidence_id"]])
                self.assertIn(claim["marker"], download.text)
                self.assertEqual(artifact_json("references"), [{"n": 1, "paper_id": paper["paper_id"]}])
                self.assertIn("[1]", download.text)
            finally:
                h.close()
        self.assertLess(time.monotonic() - started, 75)


if __name__ == "__main__":
    unittest.main()
