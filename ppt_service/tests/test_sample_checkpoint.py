"""Checkpoint contracts using only temporary files and a fake Docker client."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ppt_service.scripts import run_sample
from ppt_service.scripts.run_sample import RunError, checkpoint, continuation_inputs, sample_deadline


class SampleDeadlineTests(unittest.TestCase):
    def test_defaults_preserve_existing_phase_limits(self):
        for phase in ("plan_initial", "plan_options"):
            self.assertEqual(sample_deadline(phase), 600)
        for phase in ("prepare", "generate", "revise"):
            self.assertEqual(sample_deadline(phase), 1800)

    def test_explicit_override_is_bounded_and_does_not_expand_planning(self):
        self.assertEqual(sample_deadline("generate", 2700), 2700)
        self.assertEqual(sample_deadline("revise", 3600), 3600)
        self.assertEqual(sample_deadline("plan_options", 600), 600)
        for phase, value in (("generate", 0), ("generate", 3601),
                             ("prepare", True), ("prepare", 60.0),
                             ("plan_options", 601), ("generate", -1)):
            with self.subTest(phase=phase, value=value):
                with self.assertRaisesRegex(RunError, "sample_deadline_out_of_range"):
                    sample_deadline(phase, value)

    def test_unknown_phase_cannot_select_a_default(self):
        with self.assertRaisesRegex(RunError, "invalid_phase"):
            sample_deadline("unknown")


class FakeDocker:
    def __init__(self, source):
        self.source = source
        self.states = {}
        self.mounts = {}
        self.inspected = []

    def state(self, container):
        self.inspected.append(container)
        return self.states.get(container, {"Running": False, "Status": "exited", "ExitCode": 0})

    def call(self, *args):
        container = args[-1]
        parts = {"/audit": "audit"} if container.endswith("-proxy") else {
            "/work/" + name: name for name in ("home", "project", "result")}
        return json.dumps(self.mounts.get(container, [
            {"Type": "bind", "Source": str(self.source / name), "Destination": destination}
            for destination, name in parts.items()]))


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.attempt = self.root / "attempt"
        self.attempt.mkdir()
        for name in ("home", "project", "result"):
            (self.source / name).mkdir(parents=True)
        (self.source / "home" / "session.db").write_bytes(b"consistent stopped session")
        (self.source / "project" / "outline.txt").write_text("outline", encoding="utf-8")
        self.previous = {"job_id": "sample-job", "session_id": "sample-session", "attempt": 1,
                         "phase": "plan_initial", "input_snapshot_sha256": "a" * 64}
        self.task = dict(self.previous, phase="plan_options", attempt=2,
                         stage1_confirmation={"decision": "confirmed"})
        self.proof = {**self.previous, "status": "candidate_execution_finished",
                      "worker_stopped": True, "proxy_stopped": True,
                      "worker_container": "ppt-sample-" + "b" * 32 + "-worker"}
        self.execution = {**self.previous, "status": "completed", "gateway_stopped": True}
        self.manifest = {"phase": "plan_initial", "status": "awaiting_confirmation",
                         "input_snapshot_sha256": self.previous["input_snapshot_sha256"]}
        self.docker = FakeDocker(self.source)
        self.save()

    def write_json(self, relative, value):
        (self.source / relative).write_text(json.dumps(value), encoding="utf-8")

    def save(self):
        self.write_json("task.json", self.previous)
        self.write_json("run-state.json", self.proof)
        self.write_json("result/execution.json", self.execution)
        self.write_json("result/manifest.json", self.manifest)

    def restore(self):
        checkpoint(self.docker, self.source, self.task, self.attempt)

    def test_successful_restore_preserves_home_and_project(self):
        self.task["stage1_confirmation"]["source_manifest_sha256"] = hashlib.sha256(
            (self.source / "result" / "manifest.json").read_bytes()).hexdigest()
        self.restore()
        self.assertEqual((self.attempt / "home" / "session.db").read_bytes(), b"consistent stopped session")
        self.assertEqual((self.attempt / "project" / "outline.txt").read_text(encoding="utf-8"), "outline")

    def test_failed_or_unready_checkpoints_are_rejected_before_copy(self):
        original = (copy.deepcopy(self.proof), copy.deepcopy(self.execution), copy.deepcopy(self.manifest))
        cases = (("proof", "status", "worker_failed"), ("proof", "worker_stopped", False),
                 ("proof", "proxy_stopped", False), ("execution", "status", "failed"),
                 ("execution", "gateway_stopped", False), ("manifest", "status", "needs_input"))
        for target, field, value in cases:
            with self.subTest(target=target, field=field):
                self.proof, self.execution, self.manifest = copy.deepcopy(original)
                getattr(self, target)[field] = value
                self.save()
                with self.assertRaises(RunError):
                    self.restore()
                self.assertFalse((self.attempt / "home").exists())

    def test_cross_session_or_execution_identity_is_rejected(self):
        self.task["session_id"] = "other-session"
        with self.assertRaisesRegex(RunError, "checkpoint_task_identity_mismatch"):
            self.restore()
        self.task["session_id"] = self.previous["session_id"]
        self.execution["session_id"] = "wrong-execution-session"
        self.save()
        with self.assertRaisesRegex(RunError, "checkpoint_execution_task_mismatch"):
            self.restore()

    def test_skipped_stage_and_revision_without_base_are_rejected(self):
        self.task["phase"] = "generate"
        with self.assertRaisesRegex(RunError, "checkpoint_phase_transition_rejected"):
            self.restore()
        self.previous["phase"] = self.proof["phase"] = self.execution["phase"] = self.manifest["phase"] = "generate"
        self.manifest["status"] = "candidate"
        self.task["phase"] = "revise"
        self.save()
        with self.assertRaisesRegex(RunError, "checkpoint_base_version_required"):
            self.restore()
        self.task["base_version_id"] = "version-1"
        self.restore()  # Presence is enforced; no version mapping is claimed.

    def test_default_legal_phase_transitions_remain_available(self):
        cases = (("plan_initial", "awaiting_confirmation", "plan_options"),
                 ("plan_options", "awaiting_confirmation", "prepare"),
                 ("prepare", "prepared", "generate"), ("generate", "candidate", "revise"),
                 ("revise", "candidate", "revise"))
        for index, (previous_phase, status, next_phase) in enumerate(cases):
            with self.subTest(previous=previous_phase, next=next_phase):
                self.previous["phase"] = self.proof["phase"] = self.execution["phase"] = self.manifest["phase"] = previous_phase
                self.manifest["status"] = status
                self.task.update(phase=next_phase, base_version_id="version-1")
                self.save()
                destination = self.root / f"ordinary-{index}"
                destination.mkdir()
                self.assertIsNone(checkpoint(self.docker, self.source, self.task, destination))
                self.assertTrue((destination / "home" / "session.db").exists())

    def test_confirmation_manifest_hash_mismatch_is_rejected(self):
        self.task["stage1_confirmation"]["source_manifest_sha256"] = "0" * 64
        with self.assertRaisesRegex(RunError, "checkpoint_confirmation_manifest_mismatch"):
            self.restore()
        self.assertFalse((self.attempt / "project").exists())


class ContinuationCliTests(unittest.TestCase):
    def test_cli_flag_defaults_false_and_requires_explicit_selection(self):
        arguments = ["run_sample.py", "--root", "/unused", "--image", "local:fixture",
                     "--task", "/unused/task.json", "--upstream-env", "/unused/proxy.env"]
        for flag in ([], ["--continue-needs-input"]):
            with self.subTest(flag=flag), mock.patch("sys.argv", arguments + flag), \
                    mock.patch.object(run_sample, "run", return_value=({"status": "fixture"}, 0)) as run, \
                    mock.patch("builtins.print"):
                self.assertEqual(run_sample.main(), 0)
                self.assertIs(run.call_args.args[0].continue_needs_input, bool(flag))


class NeedsInputContinuationTests(unittest.TestCase):
    write_json = CheckpointTests.write_json
    save = CheckpointTests.save

    def setUp(self):
        CheckpointTests.setUp(self)
        self.previous.update(phase="generate", attempt=6,
                             stage1_confirmation={"decision": "confirmed", "direction": "free_design"},
                             stage2_confirmation={"decision": "confirmed", "pages": 6},
                             assets=[{"path": "/work/inputs/source.txt", "sha256": "d" * 64}])
        self.task = copy.deepcopy(self.previous)
        self.task["attempt"] = 7
        self.proof.update(self.previous, worker_exit_code=0,
                          proxy_container="ppt-sample-" + "b" * 32 + "-proxy")
        self.execution.update(self.previous)
        self.project = self.source / "project" / "acceptance-public-sample_20261021"
        self.project.mkdir()
        self.manifest = {"schema_version": 1, "phase": "generate", "status": "needs_input",
                         "input_snapshot_sha256": self.previous["input_snapshot_sha256"],
                         "project_root": self.project.name, "artifacts": [], "slides": [],
                         "questions": ["Official live-preview must be started before Step7."],
                         "quality": {}}
        self.artifact("design_spec.md", b"approved canonical spec", "design_spec")
        self.artifact("spec_lock.md", b"approved canonical lock", "spec_lock")
        self.artifact("reports/static.json", b'{"fixture":"untrusted report"}', "report")
        self.manifest["quality"] = {"static": {"path": "reports/static.json"}}
        for ordinal in range(1, 7):
            name = f"svg/slide{ordinal}.svg"
            self.artifact(name, f'<svg xmlns="http://www.w3.org/2000/svg"><text>{ordinal}</text></svg>'.encode(), "svg")
            self.manifest["slides"].append({"ordinal": ordinal, "svg_path": name})
        self.save()
        self.task["continuation"] = {
            "kind": "needs_input", "source_manifest_sha256": self.manifest_hash(),
            "approved_spec_sha256": hashlib.sha256((self.project / "design_spec.md").read_bytes()).hexdigest(),
            "approved_lock_sha256": hashlib.sha256((self.project / "spec_lock.md").read_bytes()).hexdigest(),
            "resolution": "worker_supervised_official_preview"}

    def artifact(self, name, content, role):
        path = self.project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        self.manifest["artifacts"].append({"path": name, "role": role,
                                           "media_type": "application/octet-stream",
                                           "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})

    def manifest_hash(self):
        return hashlib.sha256((self.source / "result" / "manifest.json").read_bytes()).hexdigest()

    def bind_manifest(self):
        self.save()
        self.task["continuation"]["source_manifest_sha256"] = self.manifest_hash()

    def restore(self, flag=True):
        return checkpoint(self.docker, self.source, self.task, self.attempt,
                          continue_needs_input=flag)

    def rejected(self, code=None, flag=True):
        with self.assertRaisesRegex(RunError, code or ".+"):
            self.restore(flag)
        self.assertFalse((self.attempt / "home").exists())
        self.assertFalse((self.attempt / "project").exists())

    def test_happy_private_clone_preserves_six_svgs_spec_and_source(self):
        before = {p.relative_to(self.source): p.read_bytes()
                  for p in self.source.rglob("*") if p.is_file()}
        task_before = copy.deepcopy(self.task)
        state = self.restore()
        self.assertEqual(state["scope"], "private_needs_input_continuation")
        self.assertEqual(state["source_attempt"], 6)
        self.assertEqual(state["source_manifest_sha256"], self.manifest_hash())
        self.assertFalse(state.get("published", False))
        self.assertNotIn("base_version_id", state)
        self.assertNotIn("accepted_base", state)
        self.assertEqual(self.task, task_before)
        for relative, content in before.items():
            self.assertEqual((self.source / relative).read_bytes(), content)
            if relative.parts[0] in {"home", "project"}:
                self.assertEqual((self.attempt / relative).read_bytes(), content)
        self.assertEqual(len(list((self.attempt / "project").rglob("*.svg"))), 6)
        self.assertIn(self.proof["proxy_container"], self.docker.inspected)
        (self.attempt / "project" / self.project.name / "design_spec.md").write_bytes(b"new attempt changes")
        self.assertEqual((self.project / "design_spec.md").read_bytes(), b"approved canonical spec")

    def test_metadata_without_cli_flag_is_rejected(self):
        self.rejected("continuation_cli_flag_required", flag=False)

    def test_cli_flag_without_complete_metadata_is_rejected(self):
        metadata = self.task.pop("continuation")
        self.rejected("continuation_metadata_required")
        for missing in metadata:
            with self.subTest(missing=missing):
                self.task["continuation"] = {k: v for k, v in metadata.items() if k != missing}
                self.rejected("continuation_metadata_required")

    def test_metadata_kind_resolution_hash_formats_and_unknown_keys(self):
        original = copy.deepcopy(self.task["continuation"])
        for key, value in (("kind", "failed"), ("resolution", "skip_preview"),
                           ("source_manifest_sha256", "A" * 64), ("approved_spec_sha256", None),
                           ("approved_lock_sha256", "a" * 63), ("bypass", True)):
            with self.subTest(key=key):
                self.task["continuation"] = dict(original, **{key: value})
                self.rejected()

    def test_failed_timeout_or_unstopped_proof_and_execution_rejected(self):
        original_proof, original_execution = copy.deepcopy(self.proof), copy.deepcopy(self.execution)
        for target, key, value in (("proof", "status", "worker_failed"),
                                   ("proof", "status", "failed"), ("proof", "status", "deadline_exceeded"),
                                   ("proof", "worker_exit_code", 1), ("proof", "worker_exit_code", False),
                                   ("proof", "worker_stopped", False), ("proof", "proxy_stopped", False),
                                   ("execution", "status", "failed"), ("execution", "gateway_stopped", False)):
            with self.subTest(target=target, key=key, value=value):
                self.proof, self.execution = copy.deepcopy(original_proof), copy.deepcopy(original_execution)
                getattr(self, target)[key] = value
                self.save()
                self.rejected()

    def test_actual_worker_and_proxy_must_both_be_exited_zero(self):
        for role in ("worker", "proxy"):
            container = self.proof[role + "_container"]
            for changed in ({"Running": True}, {"Status": "created"}, {"Status": "dead"},
                            {"ExitCode": 137}, {"ExitCode": False}, {"OOMKilled": True},
                            {"Restarting": True}, {"Error": "runtime failure"}):
                with self.subTest(role=role, changed=changed):
                    self.docker.states = {container: {"Running": False, "Status": "exited", "ExitCode": 0, **changed}}
                    self.rejected("continuation_container_not_successfully_exited")

    def test_worker_and_proxy_mount_identity_required(self):
        for role in ("worker", "proxy"):
            container = self.proof[role + "_container"]
            self.docker.mounts = {container: []}
            self.rejected("continuation_mount_identity_mismatch")
        self.docker.mounts = {}
        self.proof["proxy_container"] = "ppt-sample-" + "c" * 32 + "-proxy"
        self.save()
        self.rejected("continuation_proxy_identity_mismatch")

    def test_new_attempt_and_same_identity_required(self):
        original = copy.deepcopy(self.task)
        for key, value in (("attempt", 6), ("attempt", 5), ("attempt", True),
                           ("job_id", "other-job"), ("session_id", "other-session"),
                           ("input_snapshot_sha256", "f" * 64)):
            with self.subTest(key=key, value=value):
                self.task = dict(copy.deepcopy(original), **{key: value})
                self.rejected()

    def test_execution_and_proof_task_identities_required(self):
        original_proof, original_execution = copy.deepcopy(self.proof), copy.deepcopy(self.execution)
        for target, key in (("proof", "attempt"), ("proof", "phase"),
                            ("execution", "session_id"), ("execution", "job_id"),
                            ("execution", "phase"), ("execution", "attempt")):
            with self.subTest(target=target, key=key):
                self.proof, self.execution = copy.deepcopy(original_proof), copy.deepcopy(original_execution)
                getattr(self, target)[key] = "wrong"
                self.save()
                self.rejected()

    def test_confirmations_and_input_assets_cannot_change(self):
        original = copy.deepcopy(self.task)
        for key, value in (("stage1_confirmation", {}), ("stage2_confirmation", {"decision": "different"}),
                           ("assets", [])):
            with self.subTest(key=key):
                self.task = dict(copy.deepcopy(original), **{key: value})
                self.rejected()

    def test_non_needs_input_empty_questions_and_base_are_rejected(self):
        for status in ("candidate", "failed", "prepared"):
            with self.subTest(status=status):
                self.manifest["status"] = status
                self.bind_manifest()
                self.rejected("checkpoint_manifest_stage_mismatch")
        self.manifest["status"] = "needs_input"
        for questions in ([], [""], ["  "], "preview missing", [{}]):
            self.manifest["questions"] = questions
            self.bind_manifest()
            self.rejected("continuation_questions_required")
        self.manifest["questions"] = ["preview missing"]
        self.bind_manifest()
        self.task["base_version_id"] = "accepted-v1"
        self.rejected("continuation_private_generate_only")

    def test_continuation_cannot_skip_planning_or_enter_revise(self):
        self.task["phase"] = "revise"
        self.rejected("continuation_private_generate_only")
        self.task["phase"] = "generate"
        self.previous["phase"] = self.proof["phase"] = self.execution["phase"] = self.manifest["phase"] = "prepare"
        self.bind_manifest()
        self.rejected("continuation_generate_transition_required")

    def test_manifest_and_approved_spec_lock_hashes_are_bound(self):
        original = copy.deepcopy(self.task["continuation"])
        for key in ("source_manifest_sha256", "approved_spec_sha256", "approved_lock_sha256"):
            with self.subTest(key=key):
                self.task["continuation"] = dict(original, **{key: "0" * 64})
                self.rejected()

    def test_strict_validator_checks_every_artifact_and_report_reference(self):
        for name in ("design_spec.md", "spec_lock.md", "svg/slide6.svg", "reports/static.json"):
            with self.subTest(name=name):
                path = self.project / name
                original = path.read_bytes()
                path.write_bytes(b"corrupted")
                self.rejected("continuation_manifest_integrity_rejected")
                path.write_bytes(original)
        self.manifest["quality"] = {"static": {"path": "missing-report.json"}}
        self.bind_manifest()
        self.rejected("continuation_manifest_integrity_rejected")

    def test_changed_spec_cannot_be_laundered_by_rehashing_manifest(self):
        path = self.project / "design_spec.md"
        content = b"candidate has changed approved plan"
        path.write_bytes(content)
        artifact = next(a for a in self.manifest["artifacts"] if a["path"] == path.name)
        artifact.update(bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
        self.bind_manifest()
        self.rejected("continuation_approved_spec_lock_mismatch")

    def test_existing_declared_or_undeclared_pptx_rejected(self):
        path = self.project / "export" / "deck.PPTX"
        path.parent.mkdir()
        path.write_bytes(b"not parsed or executed")
        self.rejected("continuation_pptx_rejected")
        path.unlink()
        self.artifact("export/deck.bin", b"also forbidden by pptx role", "pptx")
        self.bind_manifest()
        self.rejected("continuation_pptx_rejected")

    def test_missing_canonical_artifact_and_unsafe_paths_rejected(self):
        original = copy.deepcopy(self.manifest)
        self.manifest["artifacts"] = [a for a in self.manifest["artifacts"] if a["path"] != "spec_lock.md"]
        self.bind_manifest()
        self.rejected("continuation_approved_spec_lock_mismatch")
        self.manifest = original
        self.manifest["artifacts"][0]["path"] = "../design_spec.md"
        self.bind_manifest()
        self.rejected("continuation_manifest_integrity_rejected")

    def test_actual_inputs_and_canonical_snapshot_are_reverified(self):
        inputs = self.root / "inputs"
        inputs.mkdir()
        path = inputs / "source.txt"
        content = b"original authorized input"
        path.write_bytes(content)
        task = {"assets": [{"path": "/work/inputs/source.txt", "bytes": len(content),
                             "sha256": hashlib.sha256(content).hexdigest()}]}
        task["input_snapshot_sha256"] = hashlib.sha256(json.dumps(
            task["assets"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        continuation_inputs(inputs, task)
        path.write_bytes(b"changed authorized input")
        with self.assertRaisesRegex(RunError, "continuation_input_integrity_rejected"):
            continuation_inputs(inputs, task)
        path.write_bytes(content)
        task["input_snapshot_sha256"] = "0" * 64
        with self.assertRaisesRegex(RunError, "continuation_input_integrity_rejected"):
            continuation_inputs(inputs, task)

    def test_proxy_reactivated_during_validation_is_rejected_before_copy(self):
        from ppt_service.worker.candidate_validation import validate_manifest
        def validate_then_reactivate(*args, **kwargs):
            result = validate_manifest(*args, **kwargs)
            self.docker.states[self.proof["proxy_container"]] = {
                "Running": True, "Status": "running", "ExitCode": 0}
            return result
        with mock.patch("ppt_service.worker.candidate_validation.validate_manifest", side_effect=validate_then_reactivate):
            self.rejected("continuation_container_not_successfully_exited")

    def test_no_follow_links_rejected_in_home_project_and_result(self):
        for directory in (self.source / "home", self.project, self.source / "result"):
            with self.subTest(directory=directory.name):
                link = directory / "linked-output"
                try:
                    link.symlink_to(self.source / "task.json")
                except OSError:
                    self.skipTest("Host does not permit creating symlinks")
                try:
                    self.rejected("checkpoint_or_input_link_rejected")
                finally:
                    link.unlink()

    def test_official_home_skill_link_is_omitted_without_following(self):
        skills = self.source / "home" / "skills"
        skills.mkdir()
        link = skills / "ppt-master"
        try:
            link.symlink_to(run_sample.SKILL_TARGET, target_is_directory=True)
        except OSError:
            self.skipTest("Host does not permit creating symlinks")
        self.restore()
        self.assertTrue(link.is_symlink())
        self.assertFalse((self.attempt / "home" / "skills" / "ppt-master").is_symlink())
        self.assertFalse((self.attempt / "home" / "skills" / "ppt-master").exists())

    def test_default_generate_to_generate_still_rejected(self):
        self.task.pop("continuation")
        self.rejected("checkpoint_phase_transition_rejected", flag=False)


if __name__ == "__main__":
    unittest.main()
