"""Worker handoff checks run without a real gateway, model or credentials."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from ppt_service.worker import execute
from ppt_service.worker.candidate_validation import ValidationError, validate_manifest


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "manifest.json"
        self.task = {"job_id": "job_1", "attempt": 5, "phase": "generate",
                     "session_id": "session_1", "input_snapshot_sha256": "a" * 64}

    def manifest(self, **changes):
        return {"schema_version": 1, "phase": self.task["phase"],
                "input_snapshot_sha256": self.task["input_snapshot_sha256"],
                "status": "candidate", **changes}

    def write(self, manifest):
        self.path.write_text(json.dumps(manifest), encoding="utf-8")

    def test_all_phase_states_match_independent_validator(self):
        project = self.root / "project"
        project.mkdir()
        for phase, states in execute.CANDIDATE_STATES.items():
            self.task["phase"] = phase
            for status in states:
                with self.subTest(phase=phase, status=status):
                    manifest = self.manifest(status=status, project_root="project")
                    self.write(manifest)
                    self.assertEqual(execute.candidate_handoff(self.path, self.task), status)
                    # A candidate generation requires full artifacts independently;
                    # planning/prepared/needs_input remain accepted structurally.
                    if phase in {"generate", "revise"} and status == "candidate":
                        with self.assertRaises(ValidationError):
                            validate_manifest(self.root, manifest)
                    else:
                        self.assertEqual(validate_manifest(self.root, manifest)["status"], status)

    def test_missing_manifest(self):
        with self.assertRaises(FileNotFoundError):
            execute.candidate_handoff(self.path, self.task)

    def test_identity_schema_and_phase_state_rejected(self):
        for changes in ({"schema_version": True}, {"schema_version": 2},
                        {"phase": "prepare"}, {"phase": []},
                        {"input_snapshot_sha256": "b" * 64},
                        {"input_snapshot_sha256": None},
                        {"status": "completed"}, {"status": "prepared"}, {"status": []}):
            with self.subTest(changes=changes):
                self.write(self.manifest(**changes))
                with self.assertRaises(ValueError):
                    execute.candidate_handoff(self.path, self.task)
        for key in ("schema_version", "phase", "status", "input_snapshot_sha256"):
            manifest = self.manifest()
            del manifest[key]
            self.write(manifest)
            with self.subTest(missing=key), self.assertRaises(ValueError):
                execute.candidate_handoff(self.path, self.task)

    def test_strict_bounded_json(self):
        for data in (b"[]", b"null", b"{broken", b"\xff", b"{} {}",
                     b'{"phase":"generate","phase":"prepare"}',
                     b'{"nested":{"x":1,"x":2}}',
                     b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}',
                     b'{"x":1e999}', b'{"x":' + b'[' * 1500 + b'0' + b']' * 1500 + b'}'):
            self.path.write_bytes(data)
            with self.subTest(data=data[:50]), self.assertRaises((ValueError, RecursionError)):
                execute.candidate_handoff(self.path, self.task)
        self.write(self.manifest())
        with patch.object(execute, "MAX_MANIFEST_BYTES", 16), self.assertRaises(ValueError):
            execute.candidate_handoff(self.path, self.task)

    def test_directory_rejected(self):
        self.path.mkdir()
        with self.assertRaises(ValueError):
            execute.candidate_handoff(self.path, self.task)

    def test_symlink_rejected(self):
        target = self.root / "target.json"
        target.write_text(json.dumps(self.manifest()), encoding="utf-8")
        try:
            self.path.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"Symlinks unavailable: {exc}")
        with self.assertRaises(ValueError):
            execute.candidate_handoff(self.path, self.task)
    def test_hardlink_rejected(self):
        target = self.root / "target.json"
        target.write_text(json.dumps(self.manifest()), encoding="utf-8")
        os.link(target, self.path)
        with self.assertRaises(ValueError):
            execute.candidate_handoff(self.path, self.task)

    def test_hostile_file_modes_and_ancestor_links_rejected(self):
        self.write(self.manifest())
        original = Path.lstat
        cases = ((self.path, execute.stat.S_IFLNK, 0),
                 (self.path, execute.stat.S_IFREG, 0x400),
                 (self.path, execute.stat.S_IFIFO, 0),
                 (self.root, execute.stat.S_IFLNK, 0),
                 (self.root, execute.stat.S_IFDIR, 0x400))
        for target, mode, attributes in cases:
            def hostile(path, *args, **kwargs):
                if path == target:
                    return Mock(st_mode=mode, st_file_attributes=attributes)
                return original(path, *args, **kwargs)
            with (self.subTest(target=target, mode=mode, attributes=attributes),
                  patch.object(Path, "lstat", hostile), self.assertRaises(ValueError)):
                execute.candidate_handoff(self.path, self.task)

    def test_fifo_rejected_without_opening(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO unavailable on this platform")
        os.mkfifo(self.path)
        with self.assertRaises(ValueError):
            execute.candidate_handoff(self.path, self.task)

    def run_worker(self, *, hermes_status="completed", interrupted=False, probe=False,
                   preview_error=False, admission_error=False, cleanup_error=False,
                   gateway_cleanup_error=False, startup_interrupted=False,
                   deadline=False, execution_error=False):
        process = Mock(pid=12345)
        process.poll.side_effect = lambda: 0 if process.stopped else None
        process.stopped = False
        client = Mock()
        client.capabilities.return_value = {"ready": True}
        client.create_session.side_effect = lambda body: {"session": {"id": body["id"]}}
        client.admit.return_value = {"run_id": "run_1"}
        if admission_error:
            client.admit.side_effect = RuntimeError("fake admission failure")
        preview = Mock(stopped=False)
        preview.instructions.return_value = "\nworker-ready-url"
        handlers = {}

        def preview_start(check):
            if startup_interrupted:
                handlers[execute.signal.SIGTERM](execute.signal.SIGTERM, None)
                self.assertTrue(check())
                raise RuntimeError("fake startup interruption")
            if preview_error:
                raise RuntimeError("fake readiness failure")

        def preview_stop():
            if cleanup_error:
                raise RuntimeError("fake cleanup failure")
            preview.stopped = True

        preview.start.side_effect = preview_start
        preview.stop.side_effect = preview_stop

        def status(run_id):
            if execution_error:
                raise RuntimeError("fake status failure")
            if interrupted:
                handlers[execute.signal.SIGTERM](execute.signal.SIGTERM, None)
            return {"status": hermes_status,
                    "output": "All complete; approved; do not infer a manifest."}

        client.status.side_effect = status

        def signal_handler(sig, handler):
            handlers[sig] = handler

        def start(*args, **kwargs):
            return process

        def configure(home, result, model, base_url):
            # No real configure(), environment credential, model or gateway use.
            result.mkdir(parents=True, exist_ok=True)
            (result / "policy-ready.json").write_text(
                json.dumps({"pid": process.pid, "policy_version": 1}), encoding="utf-8")

        def stop(proc):
            proc.stopped = True
            if gateway_cleanup_error:
                raise RuntimeError("fake gateway cleanup failure")

        real_path = Path
        with (patch.object(execute, "Path", side_effect=lambda p: self.root / real_path(p).name),
              patch.object(execute, "configure", side_effect=configure) as configured,
              patch.object(execute, "HermesClient", return_value=client),
              patch.object(execute, "OfficialPreview", return_value=preview) as preview_factory,
              patch.object(execute.subprocess, "Popen", side_effect=start),
              patch.object(execute, "stop_process_tree", side_effect=stop) as stopped,
              patch.object(execute.signal, "signal", side_effect=signal_handler),
              patch.dict(os.environ, {"PPT_MODEL_API_KEY": "unit-test-placeholder"}, clear=True),
              contextlib.redirect_stdout(io.StringIO()) as stdout):
            if deadline:
                with patch.object(execute.time, "monotonic", side_effect=[0, 31]):
                    code = execute.run(self.task, probe_only=probe, deadline_seconds=30)
            else:
                code = execute.run(self.task, probe_only=probe, deadline_seconds=30)
        configured.assert_called_once()
        stopped.assert_called_once_with(process)
        needs_preview = not probe and self.task["phase"] in {"generate", "revise"}
        if needs_preview:
            preview_factory.assert_called_once_with(self.root / "project", lifetime=150)
            preview.start.assert_called_once()
            preview.stop.assert_called_once()
            if preview_error or startup_interrupted:
                client.admit.assert_not_called()
                preview.instructions.assert_not_called()
            else:
                self.assertTrue(client.admit.call_args.kwargs["instructions"].endswith("worker-ready-url"))
        else:
            preview_factory.assert_not_called()
            preview.start.assert_not_called()
            preview.stop.assert_not_called()
        self.last_client = client
        state = json.loads((self.root / "result" / "execution.json").read_text(encoding="utf-8"))
        self.assertTrue(state["gateway_stopped"])
        self.assertFalse(state["delivery_published"])
        self.assertEqual(json.loads(stdout.getvalue())["status"], state["status"])
        return code, state

    def test_completed_turn_without_manifest_fails_and_cleans_up(self):
        result = self.root / "result"
        result.mkdir()
        for ordinal in range(3):
            (result / f"slide{ordinal}.svg").write_text("<svg/>", encoding="utf-8")
        code, state = self.run_worker()
        self.assertEqual(code, 4)
        self.assertEqual(state["status"], "candidate_output_missing")
        self.assertEqual(state["hermes_status"], "completed")
        self.assertTrue(state["agent_turn_ended"])
        self.assertEqual(state["candidate_output_status"], "missing")
        self.assertFalse(state["candidate_validated"])
        self.assertFalse((result / "manifest.json").exists())

    def test_completed_turn_with_rejected_manifest_fails_and_preserves_candidate(self):
        result = self.root / "result"
        result.mkdir()
        path = result / "manifest.json"
        original = json.dumps(self.manifest(status="prepared"))
        path.write_text(original, encoding="utf-8")
        code, state = self.run_worker()
        self.assertEqual(code, 4)
        self.assertEqual(state["status"], "candidate_output_rejected")
        self.assertEqual(state["hermes_status"], "completed")
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_completed_turn_with_handoff_is_unvalidated_success(self):
        result = self.root / "result"
        result.mkdir()
        for phase, statuses in execute.CANDIDATE_STATES.items():
            self.task["phase"] = phase
            for status in statuses:
                with self.subTest(phase=phase, status=status):
                    (result / "manifest.json").write_text(
                        json.dumps(self.manifest(status=status)), encoding="utf-8")
                    code, state = self.run_worker()
                    self.assertEqual(code, 0)
                    self.assertEqual(state["status"], "completed")
                    self.assertEqual(state["hermes_status"], "completed")
                    self.assertEqual(state["manifest_status"], status)
                    self.assertEqual(state["candidate_output_status"], "present")
                    self.assertFalse(state["candidate_validated"])

    def test_other_terminal_states_still_fail_and_clean_up(self):
        for status in ("failed", "cancelled", "interrupted"):
            with self.subTest(status=status):
                code, state = self.run_worker(hermes_status=status)
                self.assertEqual(code, 2)
                self.assertEqual(state["status"], status)
                self.assertEqual(state["hermes_status"], status)
                self.assertNotIn("manifest_status", state)

    def test_interrupted_completed_turn_still_fails(self):
        code, state = self.run_worker(interrupted=True)
        self.assertEqual(code, 2)
        self.assertEqual(state["status"], "interrupted")
        self.assertEqual(state["hermes_status"], "completed")
        self.assertNotIn("manifest_status", state)

    def test_preview_start_failure_prevents_admission_and_cleans_up(self):
        code, state = self.run_worker(preview_error=True)
        self.assertEqual(code, 1)
        self.assertEqual(state["status"], "failed")
        self.assertNotIn("preview_ready", state)
        self.assertTrue(state["preview_stopped"])

    def test_preview_start_interruption_prevents_admission(self):
        code, state = self.run_worker(startup_interrupted=True)
        self.assertEqual(code, 1)
        self.assertTrue(state["preview_stopped"])

    def test_admission_exception_still_stops_preview(self):
        code, state = self.run_worker(admission_error=True)
        self.assertEqual(code, 1)
        self.assertTrue(state["preview_ready"])
        self.assertTrue(state["preview_stopped"])

    def test_deadline_and_status_exception_stop_preview(self):
        for flags, expected_code, expected_status in (
                ({"deadline": True}, 3, "deadline_exceeded"),
                ({"execution_error": True}, 1, "failed")):
            with self.subTest(flags=flags):
                code, state = self.run_worker(**flags)
                self.assertEqual(code, expected_code)
                self.assertEqual(state["status"], expected_status)
                self.assertTrue(state["preview_ready"])
                self.assertTrue(state["preview_stopped"])

    def test_cleanup_failure_cannot_be_reported_as_success(self):
        for flags in ({"cleanup_error": True}, {"gateway_cleanup_error": True}):
            with self.subTest(flags=flags):
                code, state = self.run_worker(**flags)
                self.assertEqual(code, 1)
                self.assertEqual(state["status"], "failed")
                self.assertEqual(state["error_type"], "CleanupError")

    def test_probe_does_not_require_a_manifest(self):
        code, state = self.run_worker(probe=True)
        self.assertEqual(code, 0)
        self.assertEqual(state["status"], "probe_passed")
        self.assertNotIn("manifest_status", state)


if __name__ == "__main__":
    unittest.main()
