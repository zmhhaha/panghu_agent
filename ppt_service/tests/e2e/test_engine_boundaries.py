"""Model-free composed Pi boundaries, not production dispatch acceptance.

Real staging, prepared contexts, profile/runtime admission and candidate copying
are exercised together. Checkpoint/confirmation rows and StopProof objects are
trusted-controller test inputs: these tests do not authenticate their origin,
prove that a real Pod stopped, or exercise database lease admission. No Pi/Hermes
process or model is started. Production dispatcher/engine selection remains a
separate integration gate.
"""
from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from pi_worker.runtime import PiRuntime, RuntimeExecutionError, RuntimeSettings
from ppt_service.control.execution import Attempt, ControlError, RuntimeConfig, StopProof
from ppt_service.control.pi_collect import freeze_candidate
from ppt_service.control.pi_staging import PiStager
from ppt_service.worker.candidate_validation import ValidationError, validate_manifest
from ppt_service.worker.pi_context import PreparedPptContext
from ppt_service.worker.pi_profile import PptProfile
from ppt_service.worker.pi_runner import run_prepared_fixture


RUNTIME = "ppt-p5-fixture-runtime"
SKILLS = "ppt-p5-fixture-skills"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_inventory(root):
    """Independent byte inventory; metadata written by the worker is not trusted."""
    return {p.relative_to(root).as_posix(): (p.stat().st_size, sha(p.read_bytes()))
            for p in sorted(root.rglob("*")) if p.is_file()}


class EngineBoundaryScenarios(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ppt-p5-engine-")
        self.root = Path(self.tmp.name).absolute()
        self.data = self.root / "data"
        self.config = RuntimeConfig(self.data)
        self.stager = PiStager(self.config, RUNTIME, SKILLS)
        self.project_id = str(uuid.uuid4())
        core = {"project_id": self.project_id, "title": "Synthetic recovery fixture",
                "settings": {"page_count": 3}, "assets": []}
        self.snapshot = dict(core, hash=sha(json.dumps(
            core, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()))
        self.attempt = Attempt(str(uuid.uuid4()), self.project_id, 1, 7,
                               "plan_initial", self.snapshot, "unused-native-session")
        self.skill = self.root / "SKILL.md"
        self.skill.write_text("Synthetic fixture. No real generation acceptance.", encoding="utf-8")
        self.settings = RuntimeSettings(
            command=(str(self.root / "pi-is-deliberately-absent"),),
            node_command=(sys.executable,), provider="synthetic", model="no-model",
            fixture_mode=True, runtime_revision=RUNTIME, skill_bundle_revision=SKILLS,
            skill_paths=(self.skill,), skill_hashes=(sha(self.skill.read_bytes()),))

    def tearDown(self):
        # Only this test-owned temporary tree; the stager intentionally seals it.
        for folder, dirs, files in os.walk(self.root):
            Path(folder).chmod(0o700)
            for name in files:
                (Path(folder) / name).chmod(0o600)
        self.tmp.cleanup()

    @property
    def project_root(self):
        return self.data / "projects" / self.project_id

    def checkpoint(self):
        """A controller-supplied initial-plan checkpoint plus website confirmation."""
        confirmation = {"channel": "website", "plan_revision": 1,
                        "input_snapshot_sha256": self.snapshot["hash"],
                        "selection": {"design_mode": "free_design", "template_id": None},
                        "outline": {"pages": [{"title": "Synthetic"}]}, "design": {}}
        snap = copy.deepcopy(self.snapshot)
        snap.update(control_context={"stage1_confirmation": confirmation}, plan_revision=1,
                    confirmation_selection=copy.deepcopy(confirmation["selection"]))
        key = f"projects/{self.project_id}/checkpoints/{uuid.uuid4()}"
        source = self.data / key
        (source / "project/deck").mkdir(parents=True)
        raw = b"# Synthetic public outline\n"
        (source / "project/deck/outline.md").write_bytes(raw)
        manifest = {"schema_version": 1, "engine": "pi", "profile": "ppt-master",
                    "checkpoint_format": "artifact-replay-v1", "runtime_revision": RUNTIME,
                    "skill_bundle_revision": SKILLS, "project_id": self.project_id,
                    "input_snapshot_sha256": snap["hash"], "source_job_id": str(uuid.uuid4()),
                    "source_attempt": 1, "source_token": 3, "phase": "plan_initial",
                    "project_root": "deck", "control_context": {}, "version_id": None,
                    "project": {"files": [{"path": "deck/outline.md", "bytes": len(raw),
                                            "sha256": sha(raw)}], "bytes": len(raw)}}
        (source / "checkpoint.json").write_text(json.dumps(manifest), encoding="utf-8")
        attempt = replace(self.attempt, phase="plan_options", snapshot=snap, checkpoint_key=key)
        return attempt, source, manifest

    def prepare(self, attempt=None, manifest=None):
        attempt = attempt or self.attempt
        layout, inventory = self.stager.stage(attempt, manifest)
        # No materials exist in this fixture; this does not claim parser authenticity.
        self.assertEqual(inventory["sources"], [])
        self.stager.finish_inputs(attempt, layout, {
            "input_snapshot_sha256": attempt.snapshot["hash"], "sources": []})
        context = PreparedPptContext.from_stager(
            self.stager, attempt, layout, checkpoint_manifest=manifest)
        now = datetime.now(timezone.utc)
        wire = {"schema_version": 1, "domain": "ppt", "engine": "pi", "profile": "ppt-master",
                "phase": attempt.phase, "job_id": attempt.job_id,
                "input_snapshot_sha256": attempt.snapshot["hash"],
                "runtime_revision": RUNTIME, "skill_bundle_revision": SKILLS,
                "timeout_seconds": 60, "created_at": now.isoformat(),
                "execution_deadline_at": (now + timedelta(seconds=60)).isoformat(),
                "task": context.task, "checkpoint": context.checkpoint}
        profile = PptProfile(runtime_revision=RUNTIME, skill_bundle_revision=SKILLS,
                             prepared_context=context)
        return layout, context, wire, profile

    def assert_no_publication(self):
        self.assertFalse((self.project_root / "versions").exists())
        self.assertFalse((self.project_root / "downloads").exists())

    def reject_runtime(self, runtime, wire, code, name="scratch"):
        events = []
        workspace = self.root / name
        with patch("subprocess.Popen") as spawn, \
                patch("ppt_service.worker.hermes_client.HermesClient.__init__") as hermes:
            with self.assertRaises(RuntimeExecutionError) as caught:
                runtime.execute(wire, workspace, events.append, threading.Event())
            self.assertEqual(caught.exception.code, code)
            spawn.assert_not_called()
            hermes.assert_not_called()
        self.assertEqual(events, [])
        self.assertFalse(workspace.exists())
        self.assert_no_publication()

    def test_replay_prepared_context_binds_confirmation_and_independent_baseline(self):
        attempt, source, manifest = self.checkpoint()
        original = file_inventory(source)
        layout, context, wire, profile = self.prepare(attempt, manifest)
        self.assertEqual(profile.validate(wire).task_id, attempt.job_id)
        prompt = profile.build_prompt(wire)
        self.assertIn("checkpoint was restored", prompt)
        self.assertEqual(context.task["stage1_confirmation"],
                         attempt.snapshot["control_context"]["stage1_confirmation"])
        before = (layout.base / "project/deck/outline.md").read_bytes()
        (layout.project / "deck/outline.md").write_bytes(b"new candidate only")
        self.assertEqual((layout.base / "project/deck/outline.md").read_bytes(), before)
        self.assertEqual(file_inventory(source), original)
        self.assertEqual(list(layout.home.iterdir()), [])
        self.assertFalse(profile.production_ready)
        self.assert_no_publication()

    def test_cross_engine_profile_format_and_pin_replay_rejected_before_staging(self):
        attempt, source, manifest = self.checkpoint()
        changes = {"engine": ("hermes", "incompatible_checkpoint"),
                   "profile": ("scientific-research", "incompatible_checkpoint"),
                   "checkpoint_format": ("native-sqlite", "incompatible_checkpoint"),
                   "runtime_revision": ("old-runtime", "checkpoint_revision_mismatch"),
                   "skill_bundle_revision": ("local-procedures-v1", "checkpoint_revision_mismatch")}
        for key, (value, code) in changes.items():
            with self.subTest(field=key):
                changed = dict(manifest, **{key: value})
                (source / "checkpoint.json").write_text(json.dumps(changed), encoding="utf-8")
                before = file_inventory(source)
                with self.assertRaises(ControlError) as caught:
                    self.stager.stage(attempt, changed)
                self.assertEqual(caught.exception.code, code)
                self.assertFalse(self.stager.layout(attempt).root.exists())
                self.assertEqual(file_inventory(source), before)
                self.assert_no_publication()

    def test_checkpoint_disk_swap_after_stage_blocks_runner_before_spawn(self):
        attempt, source, manifest = self.checkpoint()
        layout, _, _, _ = self.prepare(attempt, manifest)
        # A worker replacing its source cannot convert a valid prepared stage
        # into permission to restore native state from a different engine.
        (source / "checkpoint.json").write_text(
            json.dumps(dict(manifest, engine="hermes")), encoding="utf-8")
        baseline = file_inventory(layout.base)
        with patch("subprocess.Popen") as spawn:
            with self.assertRaisesRegex(ControlError, "checkpoint_identity_mismatch"):
                run_prepared_fixture(self.stager, attempt, layout, self.settings,
                    self.root / "scratch", checkpoint_manifest=manifest)
            spawn.assert_not_called()
        self.assertEqual(file_inventory(layout.base), baseline)
        self.assertFalse((self.root / "scratch").exists())
        self.assert_no_publication()

    def test_prepared_envelope_cannot_switch_engine_profile_runtime_or_checkpoint(self):
        attempt, source, manifest = self.checkpoint()
        layout, _, wire, profile = self.prepare(attempt, manifest)
        protected = file_inventory(layout.root)
        changes = [("engine", "hermes", "invalid_ppt_job"),
                   ("profile", "scientific", "invalid_ppt_job"),
                   ("runtime_revision", "other", "invalid_ppt_pins"),
                   ("checkpoint", dict(manifest, engine="hermes"), "invalid_ppt_context")]
        for field, value, code in changes:
            with self.subTest(field=field):
                self.reject_runtime(PiRuntime(self.settings, profile=profile),
                                    dict(wire, **{field: value}), code)
                self.assertEqual(file_inventory(layout.root), protected)

    def test_matching_prepared_profile_cannot_override_runtime_settings_pins(self):
        _, _, wire, profile = self.prepare()
        for field in ("runtime_revision", "skill_bundle_revision"):
            with self.subTest(field=field):
                runtime = PiRuntime(replace(self.settings, **{field: "other"}), profile=profile)
                self.reject_runtime(runtime, wire, "runtime_not_ready")

    def test_prepared_state_and_scientific_readiness_flags_do_not_admit_production(self):
        layout, _, wire, profile = self.prepare()
        settings = replace(self.settings, fixture_mode=False, capability_verified=True,
                           model_verified=True, isolation_verified=True,
                           image_digest="sha256:" + "a" * 64)
        runtime = PiRuntime(settings, profile=profile)
        before = file_inventory(layout.root)
        with patch("subprocess.Popen") as spawn:
            self.assertFalse(runtime.health()["ready"])
            spawn.assert_not_called()
        self.reject_runtime(runtime, wire, "runtime_not_ready")
        with patch("subprocess.Popen") as spawn:
            with self.assertRaises(RuntimeExecutionError) as caught:
                run_prepared_fixture(self.stager, self.attempt, layout, settings, self.root / "runner")
            self.assertEqual(caught.exception.code, "runtime_not_ready")
            spawn.assert_not_called()
        self.assertEqual(file_inventory(layout.root), before)
        self.assertFalse((self.root / "runner").exists())

    def test_missing_pi_reports_unready_and_fixture_failure_has_no_hermes_fallback(self):
        layout, _, _, profile = self.prepare()
        runtime = PiRuntime(self.settings, profile=profile)
        self.assertEqual(runtime.health()["code"], "runtime_not_ready")
        before = file_inventory(layout.root)
        # Intercept OS process creation, preserving the real runtime failure path.
        with patch("subprocess.Popen", side_effect=FileNotFoundError("synthetic missing executable")) as spawn, \
                patch("ppt_service.worker.hermes_client.HermesClient.__init__") as hermes:
            with self.assertRaises(RuntimeExecutionError) as caught:
                run_prepared_fixture(self.stager, self.attempt, layout, self.settings, self.root / "scratch")
            self.assertEqual(caught.exception.code, "runtime_not_ready")
            self.assertEqual(spawn.call_count, 1)
            hermes.assert_not_called()
        self.assertEqual(file_inventory(layout.root), before)
        self.assertFalse((self.project_root / "candidates").exists())
        self.assert_no_publication()

    def test_cancelled_prepared_replay_never_starts_or_collects(self):
        attempt, source, manifest = self.checkpoint()
        layout, _, _, _ = self.prepare(attempt, manifest)
        before = file_inventory(source)
        cancel = threading.Event()
        cancel.set()
        with patch("subprocess.Popen") as spawn:
            with self.assertRaises(RuntimeExecutionError) as caught:
                run_prepared_fixture(self.stager, attempt, layout, self.settings,
                    self.root / "scratch", checkpoint_manifest=manifest, cancel=cancel)
            self.assertEqual(caught.exception.code, "cancelled")
            spawn.assert_not_called()
        self.assertFalse((self.root / "scratch").exists())
        self.assertFalse((self.project_root / "candidates").exists())
        self.assertEqual(file_inventory(source), before)

    def test_fenced_attempt_stop_proof_cannot_collect_replacement_attempt(self):
        old_proof = StopProof(self.attempt.uid, True, ("synthetic-old-writer",))
        fresh = replace(self.attempt, attempt=2, token=self.attempt.token + 1)
        layout, context, _, _ = self.prepare(fresh)
        before = file_inventory(layout.root)
        proofs = (old_proof, StopProof(fresh.uid, False), context.receipt,
                  {"uid": fresh.uid, "all_writers_stopped": True})
        for proof in proofs:
            with self.subTest(proof_type=type(proof).__name__):
                with self.assertRaisesRegex(ControlError, "writers_not_stopped"):
                    freeze_candidate(self.config, fresh, layout, proof)
                self.assertFalse((self.project_root / "candidates").exists())
                self.assertEqual(file_inventory(layout.root), before)
        self.assert_no_publication()

    def test_collected_worker_acceptance_claim_is_only_candidate_data_not_publication(self):
        attempt, source, manifest = self.checkpoint()
        layout, _, _, _ = self.prepare(attempt, manifest)
        checkpoints_before = file_inventory(self.project_root / "checkpoints")
        worker = {"schema_version": 1, "phase": attempt.phase,
                  "input_snapshot_sha256": attempt.snapshot["hash"], "project_root": "deck",
                  "status": "accepted", "accepted": True,
                  "artifacts": [{"path": "outline.md", "bytes": 999, "sha256": "0" * 64}]}
        (layout.result / "manifest.json").write_text(json.dumps(worker), encoding="utf-8")
        frozen = freeze_candidate(self.config, attempt, layout, StopProof(attempt.uid, True))
        mapped = frozen.validation_manifest()
        self.assertEqual(mapped["artifacts"][0]["sha256"],
                         sha((layout.project / "deck/outline.md").read_bytes()))
        self.assertNotEqual(mapped["artifacts"][0]["bytes"], 999)
        self.assertNotIn("accepted", frozen.manifest)
        self.assertNotIn("version_id", frozen.manifest)
        with self.assertRaisesRegex(ValidationError, "Invalid phase/status pair"):
            validate_manifest(frozen.path / "project", mapped)
        self.assertFalse((frozen.path / "checkpoint.json").exists())
        self.assertEqual(file_inventory(self.project_root / "checkpoints"), checkpoints_before)
        self.assert_no_publication()

    def test_native_state_added_after_replay_cannot_enter_frozen_candidate(self):
        attempt, source, manifest = self.checkpoint()
        layout, _, _, _ = self.prepare(attempt, manifest)
        before = file_inventory(source)
        native = layout.project / "deck/.hermes"
        native.mkdir()
        (native / "state.sqlite").write_bytes(b"synthetic private state")
        with self.assertRaisesRegex(ControlError, "native_state_forbidden"):
            freeze_candidate(self.config, attempt, layout, StopProof(attempt.uid, True))
        self.assertFalse((self.project_root / "candidates").exists())
        self.assertEqual(file_inventory(source), before)
        self.assert_no_publication()


if __name__ == "__main__":
    unittest.main()
