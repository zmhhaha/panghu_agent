"""Offline orchestration tests, not real rendering or PowerPoint acceptance.

The real source binding, OOXML inspector, evidence issuers and quality parsers
run against hand-built OOXML/PNG fixtures. Official commands and browser are
explicit test doubles. No model, subprocess, database or network is used.
"""
from __future__ import annotations

import copy
from pathlib import Path
import pickle
import sqlite3
import sys
from types import SimpleNamespace
import uuid

from PIL import Image
import pytest

from ppt_service.control import hermes_delivery as delivery
from ppt_service.control import postflight_validation as postflight
from ppt_service.control import render_validation as render
from ppt_service.control.execution import ControlError, PPT_MASTER_COMMIT
from ppt_service.control.staging import atomic_json, read_json
from ppt_service.tests.control.test_postflight_validation import FakeOfficialCommands
from ppt_service.tests.test_candidate_validation import EXPECTED, NS, presentation, zipped


class OfflineCommands(FakeOfficialCommands):
    """Synthetic official report transport; no real executable is launched."""

    def run(self, args, stdout, stderr, **kwargs):
        if Path(args[1]).name == "pptx_delivery_check.py":
            self.calls.append(list(args))
            deck = Path(args[2])
            atomic_json(stdout, {
                "schema": "ppt-master.pptx-delivery-check.v1", "status": "passed", "errors": [],
                "file": {"path": str(deck.resolve()), "bytes": deck.stat().st_size},
                "slides": {"count": 3, "hidden_count": 0},
                "package": {"zip_integrity": "passed", "parts": {"notes": 0}},
                "motion": {
                    "transitions": dict.fromkeys(("carrier_slide_count", "visual_effect_slide_count", "timed_advance_slide_count"), 0),
                    "object_animations": dict.fromkeys(("timing_slide_count", "object_animation_slide_count", "audio_timing_slide_count"), 0),
                }, "advisories": [],
            })
            stderr.write_text("")
            return 0
        if Path(args[1]).name == "visual_review.py":
            self.calls.append(list(args))
            project = Path(args[2]); previews = project / ".preview"; previews.mkdir()
            rows = []
            for ordinal in range(1, 4):
                target = previews / f"{ordinal:02}.png"
                Image.new("RGB", (1280, 720), (ordinal * 50, 100, 150)).save(target)
                rows.append({"page": f"{ordinal:02}.svg", "path": str(target), "ok": True,
                             "all_background": False, "bytes": target.stat().st_size,
                             "canvas": {"png_width": 1280, "png_height": 720}})
            atomic_json(stdout, {"project": str(project), "server_url": "http://127.0.0.1:8765",
                                 "rendered": 3, "failed": 0, "all_background": 0, "pages": rows})
            stderr.write_text("")
            return 0
        return super().run(args, stdout, stderr, **kwargs)


class OfflinePreview:
    def __init__(self, *args, **kwargs):
        self.stopped = False

    def start(self):
        pass

    def stop(self):
        self.stopped = True


@pytest.fixture
def case(tmp_path, monkeypatch):
    execution_image = 'registry.example/ppt-hermes@sha256:' + 'e' * 64
    monkeypatch.setenv('PPT_RUNTIME_WORKER_IMAGE', execution_image)
    snapshot = tmp_path / "snapshot"; project = snapshot / "project/deck"
    (project / "svg_output").mkdir(parents=True)
    (project / ".preview").mkdir()
    (snapshot / "result").mkdir()
    parts = presentation(two_slides=True)
    parts["ppt/presentation.xml"] = parts["ppt/presentation.xml"].replace(
        "</p:sldIdLst>", '<p:sldId id="257" r:id="third"/></p:sldIdLst>')
    parts["ppt/_rels/presentation.xml.rels"] = parts["ppt/_rels/presentation.xml.rels"].replace(
        "</Relationships>", '<Relationship Id="third" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide3.xml"/></Relationships>')
    parts["ppt/slides/slide3.xml"] = f'<p:sld xmlns:p="{NS["p"]}"><p:cSld/></p:sld>'
    (project / "deck.pptx").write_bytes(zipped(parts))
    (project / "spec_lock.md").write_text("Immutable confirmed specification")
    (project / "design_spec.md").write_text("Immutable confirmed design")
    rows = []
    for ordinal in range(1, 4):
        (project / f"svg_output/{ordinal:02}.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
            f'<text x="100" y="100">Offline draft {ordinal}</text></svg>')
        Image.new("RGB", (1280, 720), "black").save(project / f".preview/{ordinal:02}.png")
    for path in sorted(project.rglob("*")):
        if path.is_file():
            key = path.relative_to(project).as_posix()
            role = "pptx" if key.endswith("pptx") else "svg" if key.endswith("svg") else "preview" if key.endswith("png") else "source"
            rows.append({**render.hash_file(project, key), "role": role, "media_type": "application/octet-stream"})
    manifest = {"schema_version": 1, "phase": "generate", "status": "candidate", "project_root": "deck",
                "input_snapshot_sha256": "a" * 64, "artifacts": rows, "slides": [
                    {"ordinal": n, "svg_path": f"svg_output/{n:02}.svg", "preview_path": f".preview/{n:02}.png"}
                    for n in range(1, 4)]}
    atomic_json(snapshot / "result/manifest.json", manifest)
    request = {"job_id": str(uuid.uuid4()), "attempt": 1, "phase": "generate", "session_id": "hermes-session-1",
               "execution_image": execution_image,
               "pages": 3, "input_snapshot_sha256": "a" * 64, "expected_charts": copy.deepcopy(EXPECTED),
               "validation_nonce": str(uuid.uuid4()),
               "source_manifest_sha256": render.hash_file(snapshot / "result", "manifest.json")["sha256"],
               "approved_design_spec_sha256": render.hash_file(project, "design_spec.md")["sha256"],
               "approved_spec_lock_sha256": render.hash_file(project, "spec_lock.md")["sha256"]}
    (snapshot / "home").mkdir()
    with sqlite3.connect(snapshot / "home/state.db") as db:
        db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO sessions VALUES (?)", (request["session_id"],))
    execution = {**{k: request[k] for k in ("job_id", "attempt", "phase", "session_id")},
                 "status": "completed", "gateway_stopped": True, "preview_stopped": True}
    atomic_json(snapshot / "result/execution.json", execution)
    binding = render.source_binding(snapshot, manifest, request)[0]
    value = SimpleNamespace(snapshot=snapshot, project=project, manifest=manifest, request=request,
                            execution=execution, binding=binding, output=tmp_path / "delivery",
                            oracle={"expected_charts": copy.deepcopy(EXPECTED), "source_sha256": ["b" * 64]},
                            authorized={"b" * 64}, official_commit=PPT_MASTER_COMMIT)
    commands = OfflineCommands(value); value.commands = commands
    scripts = tmp_path / "image-owned-scripts"
    for name in ("attribution_guard.py", "project_manager.py", "svg_quality_checker.py", "svg_quality/checker.py",
                 "svg_to_pptx/pptx_package/cli.py", "visual_review.py", "pptx_delivery_check.py", "svg_editor/server.py"):
        path = scripts / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text("# Never executed fixture " + name)
    monkeypatch.setattr(render, "SCRIPTS", scripts)
    monkeypatch.setattr(postflight, "SCRIPTS", scripts)
    monkeypatch.setattr(postflight, "OfficialCommands", lambda: commands)
    real_verify = render.verify_programmatic
    monkeypatch.setattr(delivery, "verify_programmatic", lambda *args: real_verify(*args, commands=commands, preview_factory=OfflinePreview))
    original_read = Path.read_text

    def pinned_commit(path, *args, **kwargs):
        if path.as_posix() == "/opt/ppt-service/build-info/ppt-master.commit":
            return value.official_commit
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", pinned_commit)
    yield value
    # Successful receipts freeze directories/files; restore only our temp fixture for cleanup.
    for path in [tmp_path, *tmp_path.rglob("*")]:
        if path.exists() and not path.is_symlink():
            path.chmod(0o700 if path.is_dir() else 0o600)


def run(case, **overrides):
    args = dict(snapshot=case.snapshot, request=case.request, trusted_oracle=case.oracle,
                authorized_source_sha256=case.authorized, output=case.output)
    args.update(overrides)
    return delivery.run_candidate_delivery(**args)


def no_receipt(case):
    assert not (case.output / "candidate-delivery.json").exists()


def test_real_evidence_composition_delivers_checked_draft_and_fresh_pngs(case):
    original = {p.relative_to(case.snapshot).as_posix(): p.read_bytes() for p in case.snapshot.rglob("*") if p.is_file()}
    result = run(case); receipt = result.report()
    assert result.verify(case.request["validation_nonce"], case.request["source_manifest_sha256"])
    assert receipt == read_json(case.output, "candidate-delivery.json")
    assert receipt["status"] == "review" and receipt["review_artifacts_ready"] is True
    assert receipt["delivery_kind"] == "review_candidate" and receipt["validation_status"] == "gate_pending"
    assert receipt["revision_base_eligible"] is False
    assert receipt['execution_image'] == case.request['execution_image']
    for name in ("accepted", "published", "publication_authorization", "transport_verified", "independent_visual_review", "full_exporter_postflight_verified"):
        assert receipt[name] is False
    assert set(postflight.PENDING_GATES).issubset(receipt["required_pending_gates"])
    assert receipt["powerpoint_acceptance"] == "pending" and receipt["native_chart_count"] == 1
    assert receipt["stop_proof_requires_controller"] is True
    assert len(receipt["artifacts"]) == 4 and len(receipt["pages"]) == 3
    assert (case.output / "review-artifacts/presentation.pptx").read_bytes() == (case.project / "deck.pptx").read_bytes()
    assert (case.output / "review-artifacts/slides/1.png").read_bytes() != (case.project / ".preview/01.png").read_bytes()
    for row in receipt["retained_files"]:
        assert render.hash_file(case.output, row["path"]) == row
    assert all((case.snapshot / key).read_bytes() == data for key, data in original.items())
    assert len(case.commands.calls) == 6  # delivery, raster, attribution, project, quality, resource audit
    assert delivery.read_candidate_delivery(case.output, case.request) == receipt


@pytest.mark.parametrize("kind", ["needs_input", "failed", "awaiting_confirmation"])
def test_non_candidates_never_run_tools_or_become_review_baselines(case, kind):
    manifest = {**case.manifest, "status": kind}; atomic_json(case.snapshot / "result/manifest.json", manifest)
    case.request["source_manifest_sha256"] = render.hash_file(case.snapshot / "result", "manifest.json")["sha256"]
    with pytest.raises(ControlError, match="draft_candidate_not_completed"):
        run(case)
    assert not case.commands.calls; no_receipt(case)


@pytest.mark.parametrize("field,value", [("job_id", "another-job"), ("session_id", "another-session"),
                                         ("attempt", 2), ("phase", "revise"), ("input_snapshot_sha256", "c" * 64),
                                         ("source_manifest_sha256", "c" * 64), ("validation_nonce", "invalid")])
def test_request_identity_or_snapshot_rebinding_rejects(case, field, value):
    with pytest.raises(ControlError):
        run(case, request={**case.request, field: value})
    assert not case.commands.calls; no_receipt(case)


@pytest.mark.parametrize("field,value", [("status", "failed"), ("gateway_stopped", False), ("preview_stopped", False)])
def test_incomplete_worker_is_not_a_candidate(case, field, value):
    atomic_json(case.snapshot / "result/execution.json", {**case.execution, field: value})
    with pytest.raises(ControlError, match="draft_worker_not_completed"):
        run(case)
    no_receipt(case)


def test_tampered_full_source_declared_hash_rejects_before_checks(case):
    (case.project / "spec_lock.md").write_text("Changed after collection")
    with pytest.raises(ControlError):
        run(case)
    assert not case.commands.calls; no_receipt(case)


@pytest.mark.parametrize("field", ["approved_design_spec_sha256", "approved_spec_lock_sha256"])
def test_generation_preserves_confirmed_design_and_specification_locks(case, field):
    with pytest.raises(ControlError, match="draft_approved_spec_changed"):
        run(case, request={**case.request, field: "f" * 64})
    assert not case.commands.calls; no_receipt(case)


def test_another_hermes_checkpoint_session_cannot_be_delivered(case):
    with sqlite3.connect(case.snapshot / "home/state.db") as db:
        db.execute("UPDATE sessions SET id='unrelated-session'")
    with pytest.raises(ControlError, match="checkpoint_session_not_found"):
        run(case)
    assert not case.commands.calls; no_receipt(case)


@pytest.mark.parametrize("fault", ["missing", "unauthorized", "charts"])
def test_source_oracle_must_be_trusted_and_complete(case, fault):
    oracle = None if fault == "missing" else copy.deepcopy(case.oracle)
    if fault == "unauthorized":
        oracle["source_sha256"] = ["c" * 64]
    elif fault == "charts":
        oracle["expected_charts"] = []
    with pytest.raises(ControlError):
        run(case, trusted_oracle=oracle)
    no_receipt(case)


@pytest.mark.parametrize("kind", ["json", "nonce", "manifest", "source", "incomplete"])
def test_postflight_replay_json_or_incomplete_object_never_delivers(case, monkeypatch, kind):
    real = delivery.run_postflight

    def corrupt(*args, **kwargs):
        evidence = real(*args, **kwargs); payload = evidence.report()
        if kind == "json":
            return payload
        if kind == "nonce":
            return postflight.PostflightEvidence._issue(payload, str(uuid.uuid4()), case.request["source_manifest_sha256"])
        if kind == "manifest":
            return postflight.PostflightEvidence._issue(payload, case.request["validation_nonce"], "d" * 64)
        if kind == "source":
            payload["source"]["copied_source_fingerprint"] = "e" * 64
        else:
            payload["source_and_quality_verified"] = False
        return postflight.PostflightEvidence._issue(payload, case.request["validation_nonce"], case.request["source_manifest_sha256"])

    monkeypatch.setattr(delivery, "run_postflight", corrupt)
    with pytest.raises(ControlError):
        run(case)
    no_receipt(case)


def test_paired_programmatic_object_is_required_not_serialized_report(case, monkeypatch):
    real = delivery.verify_programmatic
    monkeypatch.setattr(delivery, "verify_programmatic", lambda *args: real(*args).report())
    with pytest.raises(ControlError, match="postflight_programmatic_evidence_invalid"):
        run(case)
    no_receipt(case)


def test_preview_change_after_independent_audit_is_rejected(case, monkeypatch):
    real = delivery.run_postflight

    def corrupt(*args, **kwargs):
        evidence = real(*args, **kwargs)
        (case.output / "programmatic/scratch-projects/deck/.preview/01.png").write_bytes(b"Replaced after audit")
        return evidence

    monkeypatch.setattr(delivery, "run_postflight", corrupt)
    with pytest.raises(ControlError, match="draft_preview_hash_mismatch"):
        run(case)
    no_receipt(case)


def test_size_budget_never_creates_a_success_receipt(case, monkeypatch):
    monkeypatch.setattr(delivery, "MAX_ARTIFACT_BYTES", 1)
    with pytest.raises(ControlError, match="draft_artifact_limit"):
        run(case)
    no_receipt(case)


def test_partial_output_is_private_failed_evidence_and_not_reused(case):
    case.commands.failure = "quality"
    with pytest.raises(ControlError):
        run(case)
    no_receipt(case)
    case.commands.failure = None
    with pytest.raises(ControlError, match="draft_output_not_fresh"):
        run(case)
    no_receipt(case)


def test_failed_readonly_freeze_never_leaves_a_success_receipt(case, monkeypatch):
    def broken_freeze(root):
        root.chmod(0o555)
        raise ControlError("offline_freeze_failure")
    monkeypatch.setattr(delivery, "make_readonly", broken_freeze)
    with pytest.raises(ControlError, match="offline_freeze_failure"):
        run(case)
    no_receipt(case)


def test_warnings_require_review_and_never_grant_publication(case):
    def warning(report):
        report["images"]["external_references"] = []
        report["fonts"]["generic_only_stacks"] = ["sans-serif"]
    case.commands.resource_mutation = warning
    receipt = run(case).report()
    assert "postflight_warnings_review" in receipt["required_pending_gates"]
    assert receipt["publication_authorization"] is False


def test_revision_candidates_use_the_same_draft_contract(case):
    case.manifest["phase"] = case.request["phase"] = case.execution["phase"] = "revise"
    atomic_json(case.snapshot / "result/manifest.json", case.manifest)
    atomic_json(case.snapshot / "result/execution.json", case.execution)
    case.request["source_manifest_sha256"] = render.hash_file(case.snapshot / "result", "manifest.json")["sha256"]
    assert run(case).report()["phase"] == "revise"


def test_receipt_copies_are_audit_only_and_cannot_be_reconstructed(case):
    evidence = run(case); report = evidence.report(); report["accepted"] = True
    assert evidence.report()["accepted"] is False
    assert not evidence.verify(str(uuid.uuid4()), case.request["source_manifest_sha256"])
    with pytest.raises(TypeError):
        delivery.CandidateDeliveryReceipt(report)
    with pytest.raises(TypeError):
        pickle.dumps(evidence)


def test_fixed_cli_runs_composition_and_keeps_authority_false(case, monkeypatch):
    envelope = {**case.request, "trusted_oracle": case.oracle, "authorized_source_sha256": sorted(case.authorized)}
    target = case.snapshot.parent / "request.json"; atomic_json(target, envelope)
    monkeypatch.setattr(sys, "argv", ["hermes_delivery", "--snapshot", str(case.snapshot),
                                     "--request", str(target), "--output", str(case.output)])
    assert delivery.main() == 0
    assert read_json(case.output, "candidate-delivery.json")["publication_authorization"] is False


def test_fixed_cli_failed_request_never_writes_a_success_receipt(case, monkeypatch):
    target = case.snapshot.parent / "request.json"; atomic_json(target, {"accepted": True, "all_writers_stopped": True})
    monkeypatch.setattr(sys, "argv", ["hermes_delivery", "--snapshot", str(case.snapshot),
                                     "--request", str(target), "--output", str(case.output)])
    assert delivery.main() == 2; no_receipt(case)


@pytest.mark.parametrize("fault", ["file", "extra", "nonce", "authority", "baseline", "preview", "report", "image"])
def test_transport_reader_rejects_changed_bytes_roster_identity_and_authority(case, fault):
    receipt = run(case).report()
    for path in [case.output, *case.output.rglob("*")]:
        path.chmod(0o700 if path.is_dir() else 0o600)
    request = case.request
    if fault == "file":
        (case.output / "review-artifacts/slides/1.png").write_bytes(b"Corrupted in transit")
    elif fault == "extra":
        (case.output / "extra-unindexed-file").write_text("Unexpected file")
    elif fault == "nonce":
        request = {**request, "validation_nonce": str(uuid.uuid4())}
    else:
        if fault == "authority":
            receipt["accepted"] = True
        elif fault == "baseline":
            receipt["revision_base_eligible"] = True
        elif fault == "preview":
            receipt["pages"][0]["source_sha256"] = "f" * 64
        elif fault == "report":
            receipt["postflight_report_sha256"] = "f" * 64
        elif fault == "image":
            receipt['execution_image'] = 'other/image@sha256:' + 'f' * 64
        atomic_json(case.output / "candidate-delivery.json", receipt)
    with pytest.raises(ControlError):
        delivery.read_candidate_delivery(case.output, request)


@pytest.mark.parametrize('fault', ['missing_env', 'missing_request', 'different', 'tag'])
def test_running_overlay_digest_must_match_controller_before_any_commands(case, monkeypatch, fault):
    request = dict(case.request)
    if fault == 'missing_env': monkeypatch.delenv('PPT_RUNTIME_WORKER_IMAGE')
    elif fault == 'missing_request': request.pop('execution_image')
    elif fault == 'different': request['execution_image'] = 'other/image@sha256:' + 'f' * 64
    elif fault == 'tag':
        request['execution_image'] = 'registry.example/ppt-hermes:latest'
        monkeypatch.setenv('PPT_RUNTIME_WORKER_IMAGE', request['execution_image'])
    with pytest.raises(ControlError, match='draft_execution_image_mismatch'):
        run(case, request=request)
    assert not case.commands.calls
    no_receipt(case)
