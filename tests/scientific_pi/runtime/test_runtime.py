import dataclasses
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from pi_worker.runtime import PiRuntime, RuntimeSettings, RuntimeExecutionError
from scientific_agent.pi_contracts import input_digest
from pi_worker.research_tools import BUNDLE_REVISION

ROOT = Path(__file__).resolve().parents[3]
FAKE = Path(__file__).with_name("fake_pi.py")
SKILL = ROOT / "scientific_skills" / "research-procedure" / "SKILL.md"
MANIFEST = ROOT / "scientific_skills" / "manifest.json"


class RuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        now = datetime.now(timezone.utc)
        self.job = {"schema_version": "1", "task_id": "00000000-0000-4000-8000-000000000001",
                    "topic": "fixture topic", "user_id": "opaque-user", "engine": "pi", "mode": "review",
                    "timeout_seconds": 60, "created_at": now.isoformat(),
                    "execution_deadline_at": (now + timedelta(seconds=60)).isoformat(),
                    "skill_bundle_revision": BUNDLE_REVISION, "runtime_revision": "runtime-test"}
        self.job["input_digest"] = input_digest(self.job)
        self.events = []

    def tearDown(self):
        self.temporary.cleanup()

    def runtime(self, mode="success", **kwargs):
        settings = RuntimeSettings(command=(sys.executable, str(FAKE), mode),
                    node_command=(sys.executable, str(FAKE), "node"),
                    provider="fixture", model="fixture-model", fixture_mode=True,
                    skill_paths=(SKILL,), skill_hashes=(hashlib.sha256(SKILL.read_bytes()).hexdigest(),),
                    bundle_manifest=MANIFEST, bundle_manifest_sha256=hashlib.sha256(MANIFEST.read_bytes()).hexdigest(),
                    skill_bundle_revision=BUNDLE_REVISION, runtime_revision="runtime-test",
                    term_grace_seconds=0.1, kill_grace_seconds=1, idle_timeout_seconds=1)
        return PiRuntime(dataclasses.replace(settings, **kwargs))

    def execute(self, runtime, name="attempt", cancel=None):
        runtime.execute(self.job, self.root / name, self.events.append, cancel or threading.Event())

    def test_success_minimal_env_actual_provenance_and_reviewed_skill(self):
        with patch.dict(os.environ, {"WORKER_TOKEN": "PRIVATE_CONTROL_TOKEN", "VAULT_TOKEN": "private-vault", "OPENAI_API_KEY": "private-human-key", "NODE_OPTIONS": "private-loader"}):
            self.execute(self.runtime(model_env={"FIXTURE_API_KEY": "worker-model-key"}))
        observed = json.loads((self.root / "attempt" / "observed.json").read_text())
        env = observed["env"]
        for key in ("WORKER_TOKEN", "VAULT_TOKEN", "OPENAI_API_KEY", "NODE_OPTIONS"):
            self.assertNotIn(key, env)
        self.assertEqual(env["FIXTURE_API_KEY"], "worker-model-key")
        self.assertEqual(Path(env["HOME"]), self.root / "attempt" / ".home")
        self.assertEqual(Path(observed["cwd"]), self.root / "attempt")
        self.assertIn("--no-context-files", observed["argv"])
        self.assertEqual(observed["argv"].count("--skill"), 1)
        self.assertEqual(observed["argv"][observed["argv"].index("--skill") + 1], str(SKILL))
        provenance = json.loads((self.root / "attempt" / "provenance.json").read_text())
        self.assertEqual(provenance["model"], "fixture-model")
        self.assertEqual(provenance["pi_version"], "0.73.1")
        self.assertEqual(provenance["node_version"], "24.21.0")
        self.assertIsNone(provenance["token_usage"])
        self.assertIsNone(provenance["image_digest"])
        self.assertTrue(provenance["fixture_mode"])
        self.assertNotIn("PRIVATE_CONTROL_TOKEN", json.dumps(self.events))
        self.assertNotIn("SECRET_PATH", json.dumps(self.events))
        self.assertEqual(self.events[-1]["type"], "runtime_finished")
        self.assertFalse(list((self.root / "attempt" / ".home").glob("runtime-*.json")))

    def test_second_attempt_gets_fresh_home_and_refuses_reuse(self):
        runtime = self.runtime()
        self.execute(runtime)
        (self.root / "attempt" / ".home" / "old-memory").write_text("private")
        self.execute(runtime, "second")
        self.assertFalse((self.root / "second" / ".home" / "old-memory").exists())
        with self.assertRaisesRegex(RuntimeExecutionError, "fresh empty"):
            self.execute(runtime)

    def test_protocol_exit_model_and_idle_errors(self):
        cases = {"bad_json": "runtime_protocol_error", "exit": "runtime_exit", "wrong_model": "model_mismatch",
                 "model_error": "model_error", "incomplete": "runtime_protocol_error", "idle": "runtime_idle_timeout",
                 "wrong_version": "runtime_not_ready", "large": "runtime_output_limit"}
        for mode, code in cases.items():
            with self.subTest(mode=mode):
                with self.assertRaises(RuntimeExecutionError) as caught:
                    self.execute(self.runtime(mode, max_line_bytes=10000), mode)
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("PRIVATE_CONTROL_TOKEN", caught.exception.message)
                self.assertFalse((self.root / mode / "provenance.json").exists())

    def test_cancel_stops_descendant_before_return(self):
        cancel = threading.Event()
        runtime = self.runtime("wait", idle_timeout_seconds=10)
        errors = []
        def execute():
            try:
                self.execute(runtime, cancel=cancel)
            except RuntimeExecutionError as exc:
                errors.append(exc)
        worker = threading.Thread(target=execute)
        worker.start()
        deadline = time.monotonic() + 5
        child_file = self.root / "attempt" / "child.pid"
        while not child_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(child_file.exists())
        cancel.set()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors[0].code, "cancelled")
        pid = int(child_file.read_text())
        self.assert_stopped(pid)

    def assert_stopped(self, pid):
        if os.name == "nt":
            import ctypes
            api = ctypes.WinDLL("kernel32")
            api.OpenProcess.restype = ctypes.c_void_p
            handle = api.OpenProcess(0x1000, False, pid)
            if handle:
                code = ctypes.c_ulong()
                api.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
                api.CloseHandle(ctypes.c_void_p(handle))
                self.assertNotEqual(code.value, 259)
        else:
            status = Path(f"/proc/{pid}/status")
            if status.exists():
                self.assertIn("State:\tZ", status.read_text())

    def test_normal_root_exit_stops_lingering_descendant(self):
        self.execute(self.runtime("descendant"))
        self.assert_stopped(int((self.root / "attempt" / "child.pid").read_text()))

    def test_fixture_group_scan_ignores_zombies_and_handles_complex_comm(self):
        proc = self.root / "proc"
        proc.mkdir()
        for pid, state, group in ((101, "R", 555), (102, "Z", 555), (103, "S", 999), (104, "X", 555)):
            entry = proc / str(pid)
            entry.mkdir()
            (entry / "stat").write_text(f"{pid} (fixture name with ) parentheses) {state} 1 {group} 0 0")
        (proc / "105").mkdir()  # Reaped between directory enumeration and stat read.
        (proc / "self").mkdir()
        with patch("pi_worker.runtime.adapter.Path", return_value=proc):
            self.assertEqual(self.runtime()._fixture_group_processes(555), [101])
            (proc / "101" / "stat").write_text("malformed")
            with self.assertRaises(RuntimeExecutionError) as caught:
                self.runtime()._fixture_group_processes(555)
            self.assertEqual(caught.exception.code, "process_safety_unverified")

    def test_fixture_group_stop_waits_for_two_empty_scans_after_signal(self):
        runtime = self.runtime()
        with patch.object(runtime, "_fixture_group_processes", side_effect=[[123], [], []]) as scan, \
             patch("pi_worker.runtime.adapter.os.killpg", create=True) as kill, \
             patch("pi_worker.runtime.adapter.signal.SIGKILL", 9, create=True), \
             patch("pi_worker.runtime.adapter.time.sleep"):
            runtime._stop_fixture_group(555)
        self.assertEqual(scan.call_count, 3)
        kill.assert_called_once_with(555, 9)

    def test_fixture_group_stop_fails_closed_when_descendant_stays_active(self):
        runtime = self.runtime()
        with patch.object(runtime, "_fixture_group_processes", return_value=[123]), \
             patch("pi_worker.runtime.adapter.os.killpg", create=True), \
             patch("pi_worker.runtime.adapter.signal.SIGKILL", 9, create=True), \
             patch("pi_worker.runtime.adapter.time.monotonic", side_effect=[0, 0, 2]), \
             patch("pi_worker.runtime.adapter.time.sleep"):
            with self.assertRaises(RuntimeExecutionError) as caught:
                runtime._stop_fixture_group(555)
        self.assertEqual(caught.exception.code, "process_safety_unverified")

    def test_pre_cancel_and_deadline(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(RuntimeExecutionError) as caught:
            self.execute(self.runtime(), cancel=cancel)
        self.assertEqual(caught.exception.code, "cancelled")
        now = datetime.now(timezone.utc)
        self.job["created_at"] = (now - timedelta(seconds=120)).isoformat()
        self.job["execution_deadline_at"] = (now - timedelta(seconds=60)).isoformat()
        with self.assertRaises(RuntimeExecutionError) as caught:
            self.execute(self.runtime())
        self.assertEqual(caught.exception.code, "timed_out")

    def test_production_gate_and_injected_control_secret_fail_closed(self):
        runtime = self.runtime(fixture_mode=False)
        self.assertFalse(runtime.health()["ready"])
        with self.assertRaises(RuntimeExecutionError) as caught:
            self.execute(runtime)
        self.assertEqual(caught.exception.code, "runtime_not_ready")
        self.assertFalse((self.root / "attempt").exists())
        self.assertFalse(self.runtime(model_env={"WORKER_AUTH_TOKEN": "private"}).health()["ready"])
        self.assertFalse(self.runtime(model_env={"NODE_OPTIONS": "private"}).health()["ready"])

    def test_skill_and_manifest_mismatch_fail_closed(self):
        for index, runtime in enumerate((self.runtime(skill_hashes=("0" * 64,)), self.runtime(bundle_manifest_sha256="0" * 64))):
            with self.assertRaises(RuntimeExecutionError) as caught:
                self.execute(runtime, f"mismatch-{index}")
            self.assertEqual(caught.exception.code, "runtime_not_ready")

    def test_recovery_unknown_orphan_is_not_claimed_safe(self):
        control = self.root / "control"
        control.mkdir()
        journal = control / "runtime-orphan.json"
        journal.write_text('{"kind":"fixture"}')
        with self.assertRaises(RuntimeExecutionError) as caught:
            self.runtime(control_dir=control).recover()
        self.assertEqual(caught.exception.code, "process_safety_unverified")
        self.assertTrue(journal.exists())

    def test_custom_endpoint_uses_env_reference_without_persisted_key(self):
        self.execute(self.runtime(model_base_url="https://model.invalid/v1", model_api="openai-completions",
                                  model_env={"SCIENTIFIC_MODEL_API_KEY": "worker-only-secret"}))
        path = self.root / "attempt" / ".home" / ".pi" / "agent" / "models.json"
        text = path.read_text()
        model = json.loads(text)["providers"]["fixture"]
        self.assertEqual(model["apiKey"], "SCIENTIFIC_MODEL_API_KEY")
        self.assertEqual(model["models"], [{"id": "fixture-model"}])
        self.assertNotIn("worker-only-secret", text)
        for changes in ({"model_base_url": "https://model.invalid/v1"},
                        {"model_base_url": "https://user:secret@model.invalid", "model_api": "openai-responses"},
                        {"model_base_url": "https://model.invalid", "model_api": "arbitrary-api"}):
            self.assertFalse(self.runtime(**changes).ready)

    def test_running_deadline_stops_descendant(self):
        now = datetime.now(timezone.utc)
        self.job["created_at"] = now.isoformat()
        self.job["execution_deadline_at"] = (now + timedelta(seconds=0.6)).isoformat()
        with self.assertRaises(RuntimeExecutionError) as caught:
            self.execute(self.runtime("wait", idle_timeout_seconds=10))
        self.assertEqual(caught.exception.code, "timed_out")
        child = self.root / "attempt" / "child.pid"
        if child.exists():
            self.assert_stopped(int(child.read_text()))

    def test_event_sink_failure_still_stops_descendant(self):
        def sink(event):
            if event.get("event") == "agent_start":
                raise ValueError("PRIVATE_CONTROL_TOKEN")
        with self.assertRaises(RuntimeExecutionError) as caught:
            self.runtime("wait").execute(self.job, self.root / "attempt", sink, threading.Event())
        self.assertEqual(caught.exception.code, "event_sink_error")
        self.assert_stopped(int((self.root / "attempt" / "child.pid").read_text()))
        self.assertNotIn("PRIVATE_CONTROL_TOKEN", caught.exception.message)

    def test_atomic_output_does_not_follow_existing_temporary_link(self):
        target = self.root / "must-not-overwrite"
        target.write_text("private")
        output = self.root / "provenance.json"
        old_tmp = output.with_name(output.name + ".tmp")
        try:
            old_tmp.symlink_to(target)
        except OSError:
            self.skipTest("Creating symlinks is unavailable on this host")
        PiRuntime._atomic(output, "new provenance")
        self.assertEqual(target.read_text(), "private")
        self.assertEqual(output.read_text(), "new provenance")

    def test_uncertain_cleanup_fences_runtime_and_keeps_journal(self):
        runtime = self.runtime()
        original = runtime._stop_tree
        def uncertain(*args):
            original(*args)
            raise OSError("simulated verification failure")
        with patch.object(runtime, "_stop_tree", side_effect=uncertain):
            with self.assertRaises(RuntimeExecutionError) as caught:
                self.execute(runtime)
        self.assertEqual(caught.exception.code, "process_safety_unverified")
        self.assertFalse(runtime.ready)
        self.assertTrue(list((self.root / "attempt" / ".home").glob("runtime-*.json")))
        with self.assertRaises(RuntimeExecutionError) as caught:
            self.execute(runtime, "second")
        self.assertEqual(caught.exception.code, "process_safety_unverified")

    def test_readiness_checks_hashes_executables_and_actual_version(self):
        self.assertTrue(self.runtime().ready)
        self.assertFalse(self.runtime("wrong_version").ready)
        self.assertFalse(self.runtime(command=(str(self.root / "missing-executable"),)).ready)
        self.assertFalse(self.runtime(skill_hashes=("0" * 64,)).ready)
        self.assertFalse(self.runtime(bundle_manifest_sha256="0" * 64).ready)
        runtime = self.runtime()
        with patch.object(runtime, "_probe", wraps=runtime._probe) as probe:
            self.assertTrue(runtime.ready)
            self.assertTrue(runtime.ready)
            self.assertEqual(probe.call_count, 2)

    def test_process_limit_applies_without_cgroup_and_respects_inherited_hard(self):
        from pi_worker.runtime.launcher import apply_process_limit
        from unittest.mock import Mock
        resource = Mock(RLIMIT_NPROC=6, RLIM_INFINITY=-1)
        resource.getrlimit.return_value = (100, -1)
        apply_process_limit(resource, 64)
        resource.setrlimit.assert_called_with(6, (64, 64))
        resource.getrlimit.return_value = (32, 32)
        apply_process_limit(resource, 64)
        resource.setrlimit.assert_called_with(6, (32, 32))
        with self.assertRaises(RuntimeError):
            apply_process_limit(resource, 0)
        self.assertFalse(self.runtime(max_processes=0).ready)

    def test_directory_mode_is_set_while_owned_by_root(self):
        control = self.root / "control"
        control.mkdir()
        workspace = self.root / "workspace"
        workspace.mkdir()
        runtime = self.runtime(control_dir=control, max_processes=32)
        operations = []
        with patch.object(runtime, "_stop_uid"), \
             patch("os.chown", side_effect=lambda path, uid, gid: operations.append(("chown", str(path), uid, gid)), create=True), \
             patch("os.chmod", side_effect=lambda path, mode: operations.append(("chmod", str(path), mode))):
            group, config, command = runtime._prepare_cgroup(workspace, ["fake-command"], 200001)
        self.assertIsNone(group)
        self.assertEqual(operations[:3], [("chown", str(workspace), 0, 0),
                                         ("chmod", str(workspace), 0o700),
                                         ("chown", str(workspace), 200001, 200001)])
        self.assertEqual(json.loads(config.read_text())["max_processes"], 32)

    def test_version_probe_accepts_exact_stderr_and_bounds_both_streams(self):
        self.assertTrue(self.runtime("stderr_version").ready)
        self.execute(self.runtime("stderr_version"))
        self.assertFalse(self.runtime("version_log").ready)
        started = time.monotonic()
        self.assertFalse(self.runtime("version_large").ready)
        self.assertLess(time.monotonic() - started, 3)

    def test_fixture_recovery_without_orphans(self):
        self.assertTrue(self.runtime().recover())


if __name__ == "__main__":
    unittest.main()
