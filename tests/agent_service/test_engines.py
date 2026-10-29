"""Real loopback protocol and subprocess fixtures; never call a model provider."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from agent_service.engines import (EngineRequest, HermesDriver, NativeBinding,
                                  NativeOutcome, PiDriver, StopEvidence)
from agent_service.runtime import PiRuntime, RuntimeExecutionError, RuntimeSettings
from agent_service.runtime.profiles import CommonExecution, ExecutionProfile
from agent_service.hermes_client import HermesClient


class Clock:
    def __init__(self):
        self.seconds = 0.0
        self.base = datetime.now(timezone.utc)
        self.wait_hook = None

    def time(self):
        return self.seconds

    def now(self):
        return self.base + timedelta(seconds=self.seconds)

    def wait(self, cancel, seconds):
        self.seconds += seconds
        if self.wait_hook:
            self.wait_hook(cancel)
        return cancel.is_set()


class StopHost:
    """Explicit test host, not production OS-stop evidence."""
    def __init__(self, trace):
        self.trace = trace
        self.proof = None
        self.stop_error = False

    def stop(self, execution_id):
        self.trace.append(("host_stop", execution_id))
        if self.stop_error:
            raise RuntimeError("private-host-path")

    def prove_stopped(self, execution_id):
        self.trace.append(("host_proof", execution_id))
        return self.proof or StopEvidence(execution_id, True, ("fixture-owned-writer",))


class Bindings:
    """Only a test fixture. Production supplies the transactional durable store."""
    def __init__(self, trace):
        self.trace, self.values = trace, {}
        self.lock = threading.Lock()
        self.deny_claim = False

    def load(self, execution_id):
        return self.values.get(execution_id)

    def prepare(self, execution_id, session_id, admission_key):
        with self.lock:
            binding = self.values.get(execution_id)
            if binding is None:
                binding = NativeBinding(execution_id, session_id, admission_key)
                self.values[execution_id] = binding
                self.trace.append(("binding_prepared", execution_id))
            elif (binding.session_id, binding.admission_key) != (session_id, admission_key):
                raise ValueError("conflict")
            return binding

    def mark_admission_started(self, execution_id):
        with self.lock:
            binding = self.values[execution_id]
            if binding.admission_started or self.deny_claim:
                return False
            self.values[execution_id] = replace(binding, admission_started=True)
            self.trace.append(("admission_intent", execution_id))
            return True

    def bind_run(self, execution_id, run_id):
        with self.lock:
            binding = self.values[execution_id]
            if not binding.admission_started or binding.run_id not in (None, run_id):
                raise ValueError("conflict")
            binding = replace(binding, run_id=run_id)
            self.values[execution_id] = binding
            self.trace.append(("run_bound", run_id))
            return binding


class Gateway(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        self.server.trace.append((self.path, body))
        if self.path == "/api/sessions":
            self.send(200, {"session": {"id": body["id"]}})
        elif self.path == "/v1/runs":
            self.server.admission_keys.append(self.headers.get("Idempotency-Key"))
            if self.server.fail_admit:
                self.send(503, {"secret": "private-provider-credential"})
            else:
                self.send(202, {"run_id": "run_original", "status": "started"})
        elif self.path == "/v1/runs/run_original/stop":
            self.send(200, {"status": "cancelled"})
        else:
            self.send(404, {})

    def do_GET(self):
        self.server.trace.append((self.path, None))
        if self.path == "/v1/capabilities":
            self.send(200, {"runs": True})
        elif self.path == "/v1/runs/run_original":
            value = self.server.snapshots.pop(0) if self.server.snapshots else {"status": "running"}
            if isinstance(value, int):
                self.send(value, {"private": "discard-this-provider-body"})
            else:
                self.send(200, value)
        else:
            self.send(404, {})


class FakePi:
    def __init__(self, action=None):
        self.action, self.wires = action, []

    def health(self):
        return {"ready": True, "fixture_mode": True}

    def execute(self, wire, workspace, emit, cancel):
        self.wires.append(wire)
        emit({"type": "runtime_started", "private": "control-secret"})
        emit({"type": "pi_event", "event": "tool_execution_start", "args": "control-secret"})
        emit({"type": "unreviewed_raw_text", "message": "control-secret"})
        if self.action:
            self.action(wire, workspace, emit, cancel)
        emit({"type": "runtime_finished"})


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.trace, self.events = [], []
        self.clock = Clock()
        self.host = StopHost(self.trace)
        self.bindings = Bindings(self.trace)
        self.cancel = threading.Event()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.gateway = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
        self.gateway.daemon_threads = True
        self.gateway.trace = self.trace
        self.gateway.admission_keys = []
        self.gateway.fail_admit = False
        self.gateway.snapshots = [{"status": "completed", "output": "中文结果"}]
        self.thread = threading.Thread(target=lambda: self.gateway.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.client = HermesClient(f"http://127.0.0.1:{self.gateway.server_port}", "private-gateway-key")

    def tearDown(self):
        self.gateway.shutdown()
        self.gateway.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def request(self, **kwargs):
        values = dict(execution_id="execution-1", profile="workspace-task.v1",
                      payload={"prompt": "请整理给定资料", "instructions": "不虚构事实"},
                      timeout_seconds=5, deadline_at=self.clock.now() + timedelta(seconds=5))
        values.update(kwargs)
        return EngineRequest(**values)

    def hermes(self, enabled=True, **kwargs):
        return HermesDriver(self.client, self.bindings, self.host, enabled=enabled,
                            clock=self.clock.time, now=self.clock.now, wait=self.clock.wait,
                            poll_seconds=0.1, **kwargs)

    def execute(self, driver, request=None):
        return driver.execute(request or self.request(), self.root, self.events.append, self.cancel)

    def pi(self, runtime=None, enabled=True, **kwargs):
        def builder(request):
            return {**request.payload, "timeout_seconds": request.timeout_seconds,
                    "execution_deadline_at": request.deadline_at.isoformat()}
        return PiDriver(runtime or FakePi(), self.host, wire_builder=kwargs.pop("wire_builder", builder),
                        enabled=enabled, clock=self.clock.time, now=self.clock.now, **kwargs)

    def test_default_drivers_are_unavailable_and_outcome_does_not_claim_stop(self):
        self.assertFalse(HermesDriver(self.client, self.bindings, self.host).ready)
        self.assertFalse(PiDriver(FakePi(), self.host).ready)
        self.assertFalse(NativeOutcome("completed").stopped)
        result = self.execute(self.hermes(enabled=False))
        self.assertEqual((result.status, result.error_code), ("failed", "engine_unavailable"))
        self.assertEqual(self.gateway.admission_keys, [])

    def test_request_rejects_naive_deadline_and_strips_internal_fields(self):
        with self.assertRaises(ValueError):
            self.request(deadline_at=datetime.now())
        request = self.request(payload={"prompt": "hello", "_runtime_uid": 0,
                                        "nested": {"_command": "private", "value": 1}})
        self.assertEqual(request.payload, {"prompt": "hello", "nested": {"value": 1}})

    def test_hermes_binding_and_intent_precede_single_paid_admission(self):
        result = self.execute(self.hermes())
        names = [item[0] for item in self.trace]
        self.assertLess(names.index("binding_prepared"), names.index("/api/sessions"))
        self.assertLess(names.index("admission_intent"), names.index("/v1/runs"))
        self.assertLess(names.index("run_bound"), names.index("/v1/runs/run_original"))
        self.assertEqual(names[-2:], ["host_stop", "host_proof"])
        self.assertEqual(len(self.gateway.admission_keys), 1)
        self.assertTrue(result.stopped)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output, "中文结果")
        self.assertIsNone(result.usage)
        self.assertEqual(result.resource_ids, ("fixture-owned-writer",))
        self.assertNotIn("publication_authorization", result.to_dict())

    def test_supplier_unknown_cost_is_not_zero_or_inferred_from_tokens(self):
        self.gateway.snapshots = [{"status": "completed", "usage": {
            "input_tokens": 17, "output_tokens": 8, "cost": 0, "private": "secret"}}]
        result = self.execute(self.hermes())
        self.assertEqual(result.usage, {"input_tokens": 17, "output_tokens": 8,
                                       "reported_cost": None, "cost_status": "unknown"})

    def test_replayed_bound_run_only_queries_original_and_never_admits(self):
        self.bindings.values["execution-1"] = NativeBinding("execution-1", "prior_session", "prior-key", True, "run_original")
        result = self.execute(self.hermes())
        self.assertEqual(result.status, "completed")
        self.assertEqual(self.gateway.admission_keys, [])
        self.assertNotIn("/api/sessions", [item[0] for item in self.trace])
        self.assertEqual(result.native_ids, {"session_id": "prior_session", "run_id": "run_original"})

    def test_failed_run_retains_known_usage_without_exporting_partial_output(self):
        self.gateway.snapshots = [{"status": "failed", "output": "private partial draft",
                                   "usage": {"input_tokens": 90}}]
        result = self.execute(self.hermes())
        self.assertEqual(result.status, "failed")
        self.assertIsNone(result.output)
        self.assertEqual(result.usage["input_tokens"], 90)
        self.assertEqual(result.usage["cost_status"], "unknown")

    def test_lost_admission_response_is_never_retried_even_on_reentry(self):
        self.gateway.fail_admit = True
        first = self.execute(self.hermes())
        second = self.execute(self.hermes())
        self.assertEqual(first.error_code, "admission_uncertain")
        self.assertEqual(second.error_code, "admission_uncertain")
        self.assertEqual(len(self.gateway.admission_keys), 1)
        self.assertTrue(self.bindings.load("execution-1").admission_started)
        self.assertNotIn("private-provider-credential", json.dumps(first.to_dict()))

    def test_failed_cas_does_not_issue_paid_admission(self):
        self.bindings.deny_claim = True
        result = self.execute(self.hermes())
        self.assertEqual(result.error_code, "admission_uncertain")
        self.assertEqual(self.gateway.admission_keys, [])

    def test_bounded_transient_poll_recovery_preserves_original_run(self):
        self.gateway.snapshots = [503, {"status": "running"}, {"status": "completed", "output": "ok"}]
        result = self.execute(self.hermes())
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(self.gateway.admission_keys), 1)
        names = [item[0] for item in self.trace]
        self.assertEqual(names.count("/v1/runs/run_original"), 3)

    def test_nontransient_poll_failure_stops_original_run_without_readmit(self):
        self.gateway.snapshots = [401]
        result = self.execute(self.hermes())
        names = [item[0] for item in self.trace]
        self.assertEqual(result.error_code, "native_status_failed")
        self.assertEqual(names.count("/v1/runs/run_original"), 1)
        self.assertLess(names.index("/v1/runs/run_original/stop"), names.index("host_stop"))
        self.assertEqual(len(self.gateway.admission_keys), 1)

    def test_cancel_during_poll_stops_original_run_before_host_fence(self):
        self.gateway.snapshots = [{"status": "running"}]
        self.clock.wait_hook = lambda cancel: cancel.set()
        result = self.execute(self.hermes())
        names = [item[0] for item in self.trace]
        self.assertEqual(result.status, "cancelled")
        self.assertTrue(result.stopped)
        self.assertLess(names.index("/v1/runs/run_original/stop"), names.index("host_stop"))

    def test_timeout_budget_is_not_reset_by_poll_recovery(self):
        self.gateway.snapshots = [503, 503, {"status": "running"}]
        self.clock.wait_hook = lambda cancel: setattr(self.clock, "seconds", self.clock.seconds + 2)
        result = self.execute(self.hermes())
        self.assertEqual(result.status, "timed_out")
        self.assertTrue(result.stopped)
        self.assertEqual(len(self.gateway.admission_keys), 1)
        self.assertIn("/v1/runs/run_original/stop", [item[0] for item in self.trace])

    def test_cancel_or_expired_before_start_has_no_gateway_side_effect(self):
        for mode in ("cancel", "expired"):
            with self.subTest(mode=mode):
                self.cancel.clear()
                request = self.request()
                if mode == "cancel":
                    self.cancel.set()
                else:
                    request = replace(request, deadline_at=self.clock.now() - timedelta(seconds=1))
                result = self.execute(self.hermes(), request)
                self.assertEqual(result.status, "cancelled" if mode == "cancel" else "timed_out")
                self.assertTrue(result.stopped)
        self.assertEqual(self.gateway.admission_keys, [])

    def test_missing_or_mismatched_stop_proof_quarantines_and_discards_output(self):
        proofs = [
            {"all_writers_stopped": True},
            StopEvidence("another-execution", True, ("writer",)),
            StopEvidence("execution-1", 1, ("writer",)),
            StopEvidence("execution-1", True, ()),
            StopEvidence("execution-1", True, ("writer", "writer")),
            StopEvidence("execution-1", True, ("x" * 201,)),
            StopEvidence("execution-1", True, tuple(f"writer-{i}" for i in range(65))),
        ]
        for proof in proofs:
            with self.subTest(proof=proof):
                self.host.proof = proof
                self.gateway.snapshots = [{"status": "completed", "output": "must-be-private"}]
                result = self.execute(self.hermes())
                self.assertEqual(result.status, "quarantined")
                self.assertFalse(result.stopped)
                self.assertIsNone(result.output)
                self.assertIsNone(result.usage)
                self.assertEqual(result.resource_ids, ())

    def test_host_stop_failure_cannot_be_masked_by_successful_engine_turn(self):
        self.host.stop_error = True
        result = self.execute(self.hermes())
        self.assertEqual(result.status, "quarantined")
        self.assertIsNone(result.output)
        self.assertNotIn("private-host-path", json.dumps(result.to_dict()))

    def test_pi_reuses_runtime_but_only_emits_reviewed_metadata(self):
        runtime = FakePi()
        result = self.execute(self.pi(runtime), self.request(payload={"task_id": "fixture-task", "_runtime_uid": 0}))
        self.assertEqual(result.status, "completed")
        self.assertTrue(result.stopped)
        self.assertIsNone(result.output)
        self.assertIsNone(result.usage)
        self.assertNotIn("_runtime_uid", runtime.wires[0])
        self.assertNotIn("control-secret", json.dumps(self.events))
        self.assertEqual([event["type"] for event in self.events],
                         ["runtime_started", "engine_progress", "runtime_finished"])

    def test_pi_cancel_and_timeout_are_fenced(self):
        def cancel(wire, workspace, emit, event):
            event.set()
        result = self.execute(self.pi(FakePi(cancel)))
        self.assertEqual(result.status, "cancelled")
        self.assertTrue(result.stopped)
        self.cancel.clear()

        def expire(wire, workspace, emit, event):
            self.clock.seconds += 6
        result = self.execute(self.pi(FakePi(expire)))
        self.assertEqual(result.status, "timed_out")
        self.assertTrue(result.stopped)

    def test_pi_deadline_mismatch_fails_before_launch(self):
        runtime = FakePi()
        result = self.execute(self.pi(runtime, wire_builder=lambda request: {"timeout_seconds": 500,
                                     "execution_deadline_at": request.deadline_at.isoformat()}))
        self.assertEqual(result.error_code, "native_deadline_mismatch")
        self.assertEqual(runtime.wires, [])

    def test_pi_stop_proof_is_required_even_when_runtime_returned_normally(self):
        self.host.proof = StopEvidence("execution-1", False, ("writer",))
        result = self.execute(self.pi())
        self.assertEqual(result.status, "quarantined")
        self.assertFalse(result.stopped)

    def test_pi_runtime_errors_are_safe_and_do_not_change_readiness(self):
        def fail(*args):
            raise RuntimeExecutionError("runtime_not_ready", "private/model/credential")
        driver = self.pi(FakePi(fail))
        self.assertFalse(driver.ready)  # fixture is never advertised as production.
        result = self.execute(driver)
        self.assertEqual(result.error_code, "runtime_not_ready")
        self.assertNotIn("credential", json.dumps(result.to_dict()))

    def test_event_sink_failure_still_stops_host(self):
        def bad_sink(event):
            raise RuntimeError("private-sink-secret")
        result = self.hermes().execute(self.request(), self.root, bad_sink, self.cancel)
        self.assertEqual(result.error_code, "event_sink_failed")
        self.assertTrue(result.stopped)
        self.assertEqual(self.trace[-1][0], "host_proof")

    def test_actual_pi_runtime_executes_subprocess_fixture_with_original_guards(self):
        class Profile(ExecutionProfile):
            def validate(self, wire):
                return CommonExecution(wire["task_id"], "fixture-runtime", "fixture-skills",
                                       wire["timeout_seconds"], wire["execution_deadline_at"])

            def build_prompt(self, wire):
                return "This is a fixed offline protocol fixture; no supplier call."

            def execution_provenance(self, started, finished):
                return {"engine": "pi", "profile": "fixture-workspace"}

        fake = Path(__file__).with_name("fixtures") / "fake_pi.py"
        skill = self.root / "SKILL.md"
        skill.write_text("# Fixed fixture skill\n", encoding="utf-8")
        settings = RuntimeSettings(command=(sys.executable, str(fake), "success"),
            node_command=(sys.executable, str(fake), "node"), provider="fixture", model="fixture-model",
            runtime_revision="fixture-runtime", skill_bundle_revision="fixture-skills", fixture_mode=True,
            skill_paths=(skill,), skill_hashes=(hashlib.sha256(skill.read_bytes()).hexdigest(),),
            term_grace_seconds=0.1, kill_grace_seconds=1)
        runtime = PiRuntime(settings, profile=Profile())
        # Real subprocess timing uses real aware deadline, independent of fake polling clock.
        request = self.request(payload={"task_id": "fixture-task"}, timeout_seconds=30,
                               deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30))
        result = self.pi(runtime).execute(request, self.root / "attempt", self.events.append, self.cancel)
        self.assertEqual(result.status, "completed", result)
        self.assertTrue(result.stopped)
        self.assertFalse(Profile.production_ready)
        self.assertTrue((self.root / "attempt" / "provenance.json").is_file())
        self.assertNotIn("PRIVATE_CONTROL_TOKEN", json.dumps(self.events))
        self.assertIsNone(result.usage)

    def test_neutral_runtime_requires_explicit_server_profile(self):
        with self.assertRaises(TypeError):
            PiRuntime()
        with self.assertRaises(TypeError):
            PiRuntime(profile=None)
        from agent_service.engine_profiles import WorkspaceTaskProfile
        profile = WorkspaceTaskProfile(runtime_revision="fixed", skill_bundle_revision="fixed")
        runtime = PiRuntime(profile=profile)
        self.assertEqual(runtime.capabilities, {})
        self.assertFalse(profile.production_ready)

    def test_runtime_timestamp_helpers_remain_strict_utc(self):
        from agent_service.runtime.contracts import ContractError, parse_timestamp, utc_now
        self.assertEqual(parse_timestamp(utc_now()).utcoffset(), timedelta(0))
        for value in ("2026-10-26T01:00:00", "2026-10-26T01:00:00+08:00", "bad", None):
            with self.subTest(value=value), self.assertRaises(ContractError):
                parse_timestamp(value)

    def test_neutral_runtime_and_drivers_import_with_domain_packages_blocked(self):
        script = '''
import importlib.abc
import sys
class BlockDomains(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'pi_worker', 'scientific_agent', 'ppt_service'}:
            raise RuntimeError('domain dependency forbidden')
sys.meta_path.insert(0, BlockDomains())
from agent_service.runtime import PiRuntime, RuntimeSettings
from agent_service.runtime.profiles import ExecutionProfile
from agent_service.engine_profiles import WorkspaceTaskProfile
from agent_service.engines import PiDriver, HermesDriver
from agent_service.hermes_client import HermesClient
assert not WorkspaceTaskProfile.production_ready
'''
        process = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[2],
                                 capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr.decode(errors="replace"))

    def test_neutral_model_key_and_workspace_preparation_use_actual_runtime(self):
        from agent_service.engine_profiles import WorkspaceTaskProfile
        profile = WorkspaceTaskProfile(runtime_revision="fixture-runtime", skill_bundle_revision="fixture-skills",
                                       input_files={"source.txt": b"fixed supplied input"})
        fake = Path(__file__).with_name("fixtures") / "fake_pi.py"
        skill = self.root / "SKILL.md"
        skill.write_text("# Fixed fixture skill\n", encoding="utf-8")
        settings = RuntimeSettings(command=(sys.executable, str(fake), "success"),
            node_command=(sys.executable, str(fake), "node"), provider="fixture", model="fixture-model",
            runtime_revision="fixture-runtime", skill_bundle_revision="fixture-skills", fixture_mode=True,
            skill_paths=(skill,), skill_hashes=(hashlib.sha256(skill.read_bytes()).hexdigest(),),
            model_base_url="http://127.0.0.1:1/v1", model_api="openai-responses",
            model_env={"AGENT_MODEL_API_KEY": "task-only-fixture-key"}, term_grace_seconds=0.1, kill_grace_seconds=1)
        runtime = PiRuntime(settings, profile=profile)
        request = self.request(payload={"prompt": "summarize supplied input"}, timeout_seconds=30,
                               deadline_at=datetime.now(timezone.utc) + timedelta(seconds=30))
        result = self.pi(runtime, wire_builder=profile.make_wire).execute(
            request, self.root / "neutral-attempt", self.events.append, self.cancel)
        self.assertEqual(result.status, "completed", result)
        observed = json.loads((self.root / "neutral-attempt" / "observed.json").read_text())
        self.assertEqual(observed["env"]["AGENT_MODEL_API_KEY"], "task-only-fixture-key")
        self.assertNotIn("--skill", observed["argv"])
        self.assertEqual(observed["argv"][observed["argv"].index("--tools") + 1], "read,write")
        config = json.loads((self.root / "neutral-attempt" / ".home" / ".pi" / "agent" / "models.json").read_text())
        self.assertEqual(config["providers"]["fixture"]["apiKey"], "${AGENT_MODEL_API_KEY}")
        self.assertNotIn("task-only-fixture-key", json.dumps(config))
        self.assertEqual((self.root / "neutral-attempt" / "inputs" / "source.txt").read_bytes(), b"fixed supplied input")
        self.assertFalse(profile.production_ready)

    def test_recovery_only_accepts_neutral_cgroup_namespace(self):
        from agent_service.engine_profiles import WorkspaceTaskProfile
        profile = WorkspaceTaskProfile(runtime_revision="fixed", skill_bundle_revision="fixed")
        for prefix, accepted in (("agent-pi-", True), ("another-service-", False)):
            with self.subTest(prefix=prefix):
                control = self.root / ("control-" + prefix)
                control.mkdir()
                group = control / (prefix + "a" * 32)
                group.mkdir()
                journal = control / "runtime-fixture.json"
                journal.write_text(json.dumps({"kind": "cgroup", "uid": 20000, "cgroup": str(group)}))
                runtime = PiRuntime(RuntimeSettings(fixture_mode=True, control_dir=control, cgroup_root=control),
                                    profile=profile)
                with patch.object(runtime, "_stop_uid"), patch.object(runtime, "_kill_cgroup") as kill:
                    if accepted:
                        self.assertTrue(runtime.recover())
                        kill.assert_called_once_with(group)
                        self.assertFalse(journal.exists())
                    else:
                        with self.assertRaises(RuntimeExecutionError) as error:
                            runtime.recover()
                        self.assertEqual(error.exception.code, "process_safety_unverified")
                        kill.assert_not_called()
                        self.assertTrue(journal.exists())


if __name__ == "__main__":
    unittest.main()
