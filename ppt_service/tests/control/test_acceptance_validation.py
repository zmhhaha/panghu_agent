"""Offline orchestration contracts; synthetic tools do not prove acceptance.

No Office, browser, model, database or Kubernetes process is started here. Real
renderer/exporter execution is a separate Linux-only integration requirement.
"""
from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service.contracts import canonical_bytes
from ppt_service.control import acceptance_validation as validation
from ppt_service.control.execution import ControlError
from ppt_service.control.postflight_validation import PostflightEvidence, SOURCE_GATE
from ppt_service.control.render_validation import ProgrammaticEvidence, fingerprint, hash_file, source_binding
from ppt_service.control.staging import atomic_json, read_json


@pytest.fixture
def case(tmp_path, monkeypatch):
    from ppt_service.control import export_equivalence
    from ppt_service.worker import candidate_validation

    project_id, version_id, job_id = (str(uuid4()) for _ in range(3))
    snapshot = tmp_path / "snapshot"
    project = snapshot / "project/deck"
    (project / "svg_output").mkdir(parents=True)
    (snapshot / "result").mkdir()
    rows, slides = [], []
    for n in range(1, 4):
        key = f"svg_output/{n}.svg"
        (project / key).write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><text>{n}</text></svg>')
        rows.append(dict(hash_file(project, key), role="svg"))
        slides.append({"ordinal": n, "svg_path": key})
    (project / "presentation.pptx").write_bytes(b"Synthetic orchestration fixture only")
    rows.append(dict(hash_file(project, "presentation.pptx"), role="pptx"))
    # A legacy preview must remain unchanged and excluded from source fingerprint.
    (project / ".preview").mkdir()
    (project / ".preview/old.png").write_bytes(b"not a real preview")
    rows.append(dict(hash_file(project, ".preview/old.png"), role="preview"))
    task = {"phase": "revise", "business_job_id": job_id, "project_id": project_id,
        "input_snapshot_sha256": "a" * 64,
        "input_snapshot": {"assets": [{"sha256": "b" * 64}], "settings": {"page_count": 3}}}
    manifest = {"phase": "revise", "status": "candidate", "input_snapshot_sha256": "a" * 64,
        "project_root": "deck", "artifacts": rows, "slides": slides}
    atomic_json(snapshot / "result/manifest.json", manifest)
    original = {"execution_contract": "ppt-agent.v1", "phase": "revise", "task": task,
        "job_id": job_id, "input_snapshot_sha256": "a" * 64, "pages": 3,
        "source_manifest_sha256": hash_file(snapshot / "result", "manifest.json")["sha256"],
        "validation_nonce": str(uuid4()), "expected_charts": []}
    source, _, _ = source_binding(snapshot, manifest, original)
    source["copied_source_fingerprint"] = fingerprint(sorted(
        ({key: row[key] for key in ("path", "sha256", "bytes")} for row in rows
         if not row["path"].startswith(".preview/")), key=lambda row: row["path"]))
    binding = {"project_id": project_id, "version_id": version_id, "source_revision": 26,
        "manifest_sha256": original["source_manifest_sha256"], "input_snapshot_sha256": "a" * 64,
        "pptx_sha256": source["pptx"]["sha256"], "source_fingerprint_sha256": source["copied_source_fingerprint"],
        "evidence_id": str(uuid4()), "evidence_sha256": "c" * 64,
        "runtime": {"engine": "pi", "runtime_revision": "fixture", "profile": "ppt.revise.v1",
            "checkpoint_format": "ppt-agent-artifacts-v1"}}
    nonce = str(uuid4())
    acceptance = {"schema": validation.REQUEST_SCHEMA, "evidence_id": nonce, "validation_nonce": nonce,
        "binding": binding, "job_id": job_id, "attempt": 1, "fencing_token": 40,
        "execution_image": "registry.fixture/validator@sha256:" + "d" * 64,
        "original_request_sha256": fingerprint(original)}
    monkeypatch.setenv("PPT_RUNTIME_WORKER_IMAGE", acceptance["execution_image"])
    monkeypatch.setattr(validation, "PPTProfile", lambda phase: SimpleNamespace(validate_task=lambda task: None))
    monkeypatch.setattr(validation, "_candidate_snapshot", lambda *args: (copy.deepcopy(manifest), {}))
    monkeypatch.setattr(candidate_validation, "validate_manifest", lambda *args, **kwargs: {})
    state = SimpleNamespace(snapshot=snapshot, project=project, original=original, acceptance=acceptance,
        manifest=manifest, output=tmp_path / "output", source=source, calls=[], warnings=False,
        equivalence_failure=None, mutate_source=False, corrupt_prior=False)

    def programmatic(snapshot, manifest, request, output, validation_id):
        state.calls.append(("programmatic", request["validation_nonce"]))
        assert request["validation_nonce"] == nonce and output.name == "programmatic"
        payload = {"source": copy.deepcopy(source), "completed_programmatic_gates": sorted(validation.BASE_GATES)}
        atomic_json(output / "programmatic-evidence.json", payload)
        if state.corrupt_prior:
            return payload  # A transported JSON report must not replace a live receipt.
        return ProgrammaticEvidence._issue(payload, validation_id, original["source_manifest_sha256"])

    def postflight(snapshot, manifest, request, oracle, authorized, output, *, prior_evidence):
        state.calls.append(("postflight", request["validation_nonce"]))
        assert type(prior_evidence) is ProgrammaticEvidence
        assert oracle == {"expected_charts": [], "source_sha256": []}
        assert authorized == frozenset(("b" * 64,))
        report = {"schema": "ppt-service.independent-postflight.v1", "source": copy.deepcopy(source),
            "source_and_quality_verified": True, "source_unchanged": True,
            "completed_programmatic_gates": [SOURCE_GATE],
            "quality": {"warnings_pending": state.warnings}, "resources": {"warnings_pending": False},
            "reused_programmatic_evidence": {"delivery": {"status": "passed", "advisories": [], "advisory_count": 0}}}
        atomic_json(output / "postflight-evidence.json", report)
        return PostflightEvidence._issue(report, nonce, original["source_manifest_sha256"])

    def final(pptx, expected, request, output):
        state.calls.append(("final", request["validation_nonce"]))
        assert pptx == project / "presentation.pptx"
        report = {"status": "passed", "source_unchanged": True, "pptx": {key: expected[key] for key in ("sha256", "bytes")}}
        atomic_json(output / "final-pptx-evidence.json", report)
        return report

    def equivalence(snapshot, manifest, request, output, *, postflight_evidence, postflight_root):
        state.calls.append(("equivalence", request["validation_nonce"]))
        assert type(postflight_evidence) is PostflightEvidence and postflight_root.name == "postflight"
        if state.equivalence_failure:
            raise ControlError(state.equivalence_failure)
        report = {"status": "passed", "export_build_equivalence_verified": True,
            "completed_programmatic_gates": [validation.EXPORT_GATE]}
        atomic_json(output / "export-contract.json", report)
        if state.mutate_source:
            (project / "svg_output/1.svg").write_text("changed after checks")
        return report

    monkeypatch.setattr(validation, "verify_programmatic", programmatic)
    monkeypatch.setattr(validation, "run_postflight", postflight)
    monkeypatch.setattr(validation, "verify_final_pptx", final)
    monkeypatch.setattr(export_equivalence, "run_export_equivalence", equivalence)
    return state


def run(case):
    return validation.run_acceptance(case.snapshot, case.original, case.acceptance, case.output)


def test_initial_acceptance_binding_retains_failed_diagram_details_before_commands(case):
    key = 'svg_output/1.svg'
    (case.project / key).write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
        '<rect id="peer" data-pptx-role="node" x="0" y="100" width="100" height="100" fill="blue"/>'
        '<line id="wire" data-pptx-role="connector" x1="50" y1="50" x2="50" y2="250" stroke="white"/>'
        '</svg>')
    next(row for row in case.manifest['artifacts'] if row['path'] == key).update(hash_file(case.project, key))
    atomic_json(case.snapshot / 'result/manifest.json', case.manifest)
    digest = hash_file(case.snapshot / 'result', 'manifest.json')['sha256']
    case.original['source_manifest_sha256'] = digest
    case.acceptance['binding']['manifest_sha256'] = digest
    case.acceptance['original_request_sha256'] = fingerprint(case.original)
    with pytest.raises(ControlError, match='diagram_geometry_failed'):
        run(case)
    report = read_json(case.output, 'diagram-geometry.json')
    assert report['scan_complete'] is False
    assert report['slides'][0]['report']['errors'][0]['code'] == 'svg_diagram_connector_crosses_node'
    assert case.calls == []
    assert not (case.output / 'receipt.json').exists()


def test_complete_checks_issue_bound_receipt_without_publication_or_original_changes(case):
    before = {path.relative_to(case.snapshot): path.read_bytes() for path in case.snapshot.rglob("*") if path.is_file()}
    original = copy.deepcopy(case.original)
    result = run(case)
    assert result["status"] == "passed" and not result["required_pending_gates"]
    assert result["publication_authorized"] is False and result["human_visual_acceptance"] is False
    assert result["powerpoint_acceptance"] == "pending" and result["full_exporter_postflight_verified"] is False
    assert result["binding_sha256"] == fingerprint(case.acceptance["binding"])
    assert result["request_sha256"] == hash_file(case.output, "request.json")["sha256"]
    assert (case.output / "request.json").read_bytes() == canonical_bytes(case.acceptance)
    assert (case.output / "original-validator-request.json").read_bytes() == canonical_bytes(original)
    assert [name for name, _ in case.calls] == ["programmatic", "postflight", "final", "equivalence"]
    assert case.original == original
    assert before == {path.relative_to(case.snapshot): path.read_bytes() for path in case.snapshot.rglob("*") if path.is_file()}
    assert not (case.output / "transport-manifest.json").exists()


@pytest.mark.parametrize("change", ["oracle", "job", "nonce", "project", "manifest", "pptx", "fingerprint", "image"])
def test_hostile_binding_is_rejected_before_independent_commands(case, monkeypatch, change):
    if change == "oracle": case.original["expected_charts"] = [{"injected": True}]
    elif change == "job": case.acceptance["job_id"] = str(uuid4())
    elif change == "nonce": case.acceptance["validation_nonce"] = str(uuid4())
    elif change == "project": case.acceptance["binding"]["project_id"] = str(uuid4())
    elif change == "manifest": case.acceptance["binding"]["manifest_sha256"] = "e" * 64
    elif change == "pptx": case.acceptance["binding"]["pptx_sha256"] = "e" * 64
    elif change == "fingerprint": case.acceptance["binding"]["source_fingerprint_sha256"] = "e" * 64
    else: monkeypatch.setenv("PPT_RUNTIME_WORKER_IMAGE", "wrong")
    with pytest.raises(ControlError):
        run(case)
    assert case.calls == [] and not case.output.exists()


def test_serialized_prior_cannot_be_consumed_as_live_programmatic_proof(case):
    case.corrupt_prior = True
    with pytest.raises(ControlError, match="acceptance_programmatic_unverified"):
        run(case)
    assert [name for name, _ in case.calls] == ["programmatic"]
    assert not (case.output / "receipt.json").exists()


def test_source_quality_warnings_never_complete_composite_delivery(case):
    case.warnings = True
    result = run(case)
    assert result["status"] == "pending"
    assert result["required_pending_gates"] == [validation.COMPOSITE_DELIVERY_GATE]
    assert SOURCE_GATE not in result["completed_programmatic_gates"]
    assert validation.COMPOSITE_DELIVERY_GATE not in result["completed_programmatic_gates"]


def test_semantic_mismatch_preserves_render_but_cannot_complete_export(case):
    case.equivalence_failure = "export_equivalence_content_mismatch"
    result = run(case)
    assert result["status"] == "needs_changes"
    assert result["required_pending_gates"] == sorted((validation.EXPORT_GATE, validation.COMPOSITE_DELIVERY_GATE))
    report = read_json(case.output / "equivalence", "export-contract.json")
    assert report["completed_programmatic_gates"] == []
    assert report["export_build_equivalence_verified"] is False
    assert (case.output / "final-pptx/final-pptx-evidence.json").is_file()


def test_equivalence_source_integrity_failure_is_fatal_not_partial(case):
    case.equivalence_failure = "export_equivalence_source_copy_invalid"
    with pytest.raises(ControlError, match="source_copy_invalid"):
        run(case)
    assert not (case.output / "receipt.json").exists()


def test_source_mutation_after_commands_cannot_issue_receipt(case):
    case.mutate_source = True
    with pytest.raises(ControlError, match="render_source_changed"):
        run(case)
    assert not (case.output / "receipt.json").exists()


def test_preexisting_output_is_not_overwritten(case):
    case.output.mkdir()
    marker = case.output / "receipt.json"
    marker.write_bytes(b"retained evidence")
    with pytest.raises(ControlError, match="acceptance_output_not_fresh"):
        run(case)
    assert marker.read_bytes() == b"retained evidence" and not case.calls


def test_cli_wrapper_and_safe_error_do_not_leak_exception_message(tmp_path, monkeypatch):
    source = tmp_path / "request.json"
    output = tmp_path / "output"
    wire = {"original_request": {"id": "old"}, "acceptance_request": {"id": "new"}}
    atomic_json(source, wire)
    seen = []

    def stopped(snapshot, original, acceptance, destination):
        seen.append((original, acceptance, destination))
        raise RuntimeError("private input content must not escape")

    monkeypatch.setattr(validation, "run_acceptance", stopped)
    assert validation.main(["--request", str(source), "--output", str(output)]) == 2
    error = read_json(output, "acceptance-error.json")
    assert seen == [(wire["original_request"], wire["acceptance_request"], output)]
    assert "private" not in str(error) and error["publication_authorized"] is False


def test_cli_refuses_to_write_error_into_existing_evidence(tmp_path, monkeypatch):
    source = tmp_path / "request.json"
    output = tmp_path / "output"
    output.mkdir()
    marker = output / "receipt.json"
    marker.write_bytes(b"retained")
    atomic_json(source, {"unexpected": "shape"})
    assert validation.main(["--request", str(source), "--output", str(output)]) == 2
    assert list(output.iterdir()) == [marker] and marker.read_bytes() == b"retained"
