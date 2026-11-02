"""Independent acceptance bundle producer for a frozen, delivered PPT candidate.

Run only in the network-isolated validator. This module has no business database,
model or publication capability. The controller must separately observe the exact
validator Pod stop, freeze its complete output and issue the transport manifest.
Original candidates, prior receipts and human observations are never rewritten.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
from uuid import UUID, uuid4

from agent_service.contracts import canonical_bytes
from agent_service.profiles.ppt import PPTProfile

from .agent_validation import _candidate_snapshot
from .execution import ControlError
from .final_pptx_validation import FINAL_PPTX_GATE, verify_final_pptx
from .postflight_validation import PostflightEvidence, SOURCE_GATE, run_postflight
from .render_validation import (
    DELIVERY_GATE, NATIVE_GATE, RASTER_GATE, fingerprint, hash_file,
    ProgrammaticEvidence, source_binding, unchanged, verify_programmatic,
    source_binding_with_diagram_audit,
)
from .staging import atomic_json, read_json, relative

REQUEST_SCHEMA = "ppt-service.acceptance-request.v1"
RECEIPT_SCHEMA = "ppt-service.acceptance-evidence.v1"
EXPORT_GATE = "independent_export_build_contract"
COMPOSITE_DELIVERY_GATE = "independent_postflight_and_delivery"
REQUIRED = frozenset((EXPORT_GATE, COMPOSITE_DELIVERY_GATE, FINAL_PPTX_GATE))
BASE_GATES = frozenset((RASTER_GATE, DELIVERY_GATE, NATIVE_GATE))
IMAGE = re.compile(r"[^@\s]+@sha256:[a-f0-9]{64}\Z")
PARTIAL_EQUIVALENCE = frozenset((
    "export_equivalence_content_mismatch", "export_equivalence_part_roster_mismatch",
    "export_equivalence_metadata_mismatch",
    "export_equivalence_rebuild_failed", "export_equivalence_postflight_failed",
    "export_equivalence_postflight_incomplete", "export_equivalence_native_mismatch",
    "export_equivalence_source_warnings",
))


def require(value, code="acceptance_binding_invalid"):
    if not value:
        raise ControlError(code)


def _uuid(value):
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def _canonical_file(path, value):
    """The controller writes the same compact canonical request bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical_bytes(value)
    with open(path, "xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(raw).hexdigest()


def _envelope(original, request):
    # HTTP never accepts this envelope. It is constructed from the read-only
    # source resolver by the operator and mounted independently of the Actor.
    from ppt_service.server.acceptance.contracts import SourceBinding

    require(isinstance(original, dict) and isinstance(request, dict))
    require(request.get("schema") == REQUEST_SCHEMA
        and _uuid(request.get("evidence_id"))
        and request.get("evidence_id") == request.get("validation_nonce")
        and isinstance(request.get("execution_image"), str)
        and IMAGE.fullmatch(request["execution_image"])
        and request.get("original_request_sha256") == fingerprint(original)
        and type(request.get("attempt")) is int and request["attempt"] >= 1
        and type(request.get("fencing_token")) is int and request["fencing_token"] >= 1)
    try:
        binding = SourceBinding.model_validate(request["binding"])
    except (ValueError, TypeError, KeyError):
        raise ControlError("acceptance_binding_invalid") from None
    task = original.get("task")
    require(isinstance(task, dict) and task.get("phase") in ("generate", "revise"))
    PPTProfile(task["phase"]).validate_task(task)
    require(original.get("execution_contract") == "ppt-agent.v1"
        and original.get("job_id") == request.get("job_id") == task["business_job_id"]
        and binding.project_id == task["project_id"]
        and binding.input_snapshot_sha256 == original.get("input_snapshot_sha256") == task["input_snapshot_sha256"]
        and binding.manifest_sha256 == original.get("source_manifest_sha256")
        and original.get("phase") == task["phase"]
        and type(original.get("pages")) is int
        and original["pages"] == task["input_snapshot"]["settings"].get("page_count", 8)
        and isinstance(original.get("expected_charts"), list))
    # The old request/nonce remains immutable; this independent run has a fresh
    # nonce shared by all newly produced subreports.
    check = copy.deepcopy(original)
    check["validation_nonce"] = request["validation_nonce"]
    return binding, task, check


def _clean_postflight(report):
    # run_postflight validates the underlying known reports. Keep warnings and
    # delivery advisories pending instead of rewriting them as clean results.
    reused = report.get("reused_programmatic_evidence", {})
    delivery = reused.get("delivery", {})
    return (
        report.get("source_and_quality_verified") is True
        and report.get("source_unchanged") is True
        and report.get("completed_programmatic_gates") == [SOURCE_GATE]
        and report.get("quality", {}).get("warnings_pending") is False
        and report.get("resources", {}).get("warnings_pending") is False
        and delivery.get("status") == "passed"
        and delivery.get("advisories") == []
        and type(delivery.get("advisory_count")) is int
        and delivery["advisory_count"] == 0
    )


def _descriptor(output, path):
    return {"path": path, "sha256": hash_file(output, path, 8 * 1024**2)["sha256"]}


def run_acceptance(snapshot, original_request, acceptance_request, output):
    """Retain bound checks; successful production does not authorize delivery.

    ``original_request_sha256`` is the canonical-content hash of the original
    trusted validator request. The operator additionally preserves its original
    file-byte hash privately. No candidate-supplied flags enter this contract.
    The original request supplies the trusted chart values; its authorized
    input asset hashes define the source scope for the repeated source audit.
    """
    from ppt_service.worker.candidate_validation import validate_manifest
    from .export_equivalence import run_export_equivalence

    snapshot, output = Path(snapshot).absolute(), Path(output).absolute()
    binding, task, request = _envelope(original_request, acceptance_request)
    require(os.environ.get("PPT_RUNTIME_WORKER_IMAGE") == acceptance_request["execution_image"],
        "acceptance_execution_image_mismatch")
    require(not output.is_relative_to(snapshot), "acceptance_output_not_fresh")
    for ancestor in (output, *output.parents):
        require(not ancestor.is_symlink() and not ancestor.is_junction(), "acceptance_output_linked")
    require(not output.exists() or (output.is_dir() and not any(output.iterdir())),
        "acceptance_output_not_fresh")
    source = hash_file(snapshot / "result", "manifest.json", 4 * 1024**2)["sha256"]
    require(source == binding.manifest_sha256, "acceptance_source_changed")
    manifest, _ = _candidate_snapshot(snapshot, task, source)
    require(manifest.get("status") == "candidate", "acceptance_candidate_incomplete")
    root = snapshot / "project" / relative(manifest["project_root"])
    validate_manifest(snapshot / "project", manifest, expected_charts=request["expected_charts"])
    if task["phase"] == "generate":
        for name in ("design_spec.md", "spec_lock.md"):
            require(hash_file(root, name, 4 * 1024**2)["sha256"] ==
                task["checkpoint"]["approved_specs"].get(name), "approved_spec_lock_changed")
    measured, _, index = source_binding_with_diagram_audit(snapshot, manifest, request, output)
    require(measured["pptx"]["sha256"] == binding.pptx_sha256,
        "acceptance_pptx_changed")
    rows = sorted(({key: row[key] for key in ("path", "sha256", "bytes")}
        for row in index.values() if row["path"].split("/", 1)[0] not in
        (".preview", ".review", "live_preview", ".live_preview.lock", "validation")),
        key=lambda row: row["path"])
    require(fingerprint(rows) == binding.source_fingerprint_sha256,
        "acceptance_source_fingerprint_changed")

    output.mkdir(parents=True, exist_ok=True)
    request_hash = _canonical_file(output / "request.json", acceptance_request)
    _canonical_file(output / "original-validator-request.json", original_request)
    validation_id = str(uuid4())
    prior = verify_programmatic(snapshot, manifest, request, output / "programmatic", validation_id)
    require(type(prior) is ProgrammaticEvidence and prior.verify(validation_id, source),
        "acceptance_programmatic_unverified")
    initial = prior.report()
    require(set(initial.get("completed_programmatic_gates", [])) == BASE_GATES
        and initial["source"].get("copied_source_fingerprint") == binding.source_fingerprint_sha256,
        "acceptance_programmatic_binding_changed")
    authorized = frozenset(asset["sha256"] for asset in task["input_snapshot"]["assets"])
    oracle = {"expected_charts": copy.deepcopy(request["expected_charts"]),
        "source_sha256": sorted(authorized) if request["expected_charts"] else []}
    postflight = run_postflight(snapshot, manifest, request, oracle, authorized,
        output / "postflight", prior_evidence=prior)
    require(type(postflight) is PostflightEvidence and postflight.verify(request["validation_nonce"], source),
        "acceptance_postflight_unverified")
    postflight_report = postflight.report()
    final = verify_final_pptx(root / measured["pptx"]["path"], measured["pptx"], request,
        output / "final-pptx")
    require(final.get("status") == "passed" and final.get("source_unchanged") is True
        and final.get("pptx", {}).get("sha256") == binding.pptx_sha256,
        "acceptance_final_render_unverified")
    equivalence_failed = False
    try:
        equivalence = run_export_equivalence(snapshot, manifest, request, output / "equivalence",
            postflight_evidence=postflight, postflight_root=output / "postflight")
    except ControlError as exc:
        if exc.code not in PARTIAL_EQUIVALENCE:
            raise
        # A concrete semantic mismatch is reviewable evidence, never a reason to
        # change candidate content or substitute the rebuilt file as delivery.
        equivalence_failed = True
        equivalence = {
            "schema": "ppt-service.export-build-equivalence.v1", "status": "needs_changes",
            "controller_nonce": request["validation_nonce"], "source_manifest_sha256": source,
            "input_snapshot_sha256": binding.input_snapshot_sha256,
            "pptx": {key: measured["pptx"][key] for key in ("sha256", "bytes")},
            "source_fingerprint_sha256": binding.source_fingerprint_sha256,
            "export_build_equivalence_verified": False, "original_export_provenance_verified": False,
            "full_exporter_postflight_verified": False, "publication_authorized": False,
            "completed_programmatic_gates": [], "error_code": exc.code,
        }
        atomic_json(output / "equivalence/export-contract.json", equivalence)
    completed = set(BASE_GATES) | {FINAL_PPTX_GATE}
    clean = _clean_postflight(postflight_report)
    if clean:
        completed.add(SOURCE_GATE)
    if equivalence.get("status") == "passed" and equivalence.get("export_build_equivalence_verified") is True:
        require(equivalence.get("completed_programmatic_gates") == [EXPORT_GATE],
            "acceptance_export_binding_changed")
        completed.add(EXPORT_GATE)
        if clean:
            completed.add(COMPOSITE_DELIVERY_GATE)
    pending = REQUIRED - completed
    unchanged(root, rows)
    final_manifest, _ = _candidate_snapshot(snapshot, task, source)
    require(final_manifest == manifest, "acceptance_source_changed")
    require(hash_file(snapshot / "result", "manifest.json", 4 * 1024**2)["sha256"] == source,
        "acceptance_source_changed")
    receipt = {
        "schema": RECEIPT_SCHEMA, "evidence_id": acceptance_request["evidence_id"],
        "issued_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "request_sha256": request_hash, "binding_sha256": fingerprint(acceptance_request["binding"]),
        "execution_image": acceptance_request["execution_image"],
        "status": "passed" if not pending else "needs_changes" if equivalence_failed else "pending",
        "completed_programmatic_gates": sorted(completed), "required_pending_gates": sorted(pending),
        "publication_authorized": False, "human_visual_acceptance": False,
        "powerpoint_acceptance": "pending", "full_exporter_postflight_verified": False,
        "final_pptx_report": _descriptor(output, "final-pptx/final-pptx-evidence.json"),
        "postflight_report": _descriptor(output, "postflight/postflight-evidence.json"),
        "equivalence_report": _descriptor(output, "equivalence/export-contract.json"),
    }
    atomic_json(output / "receipt.json", receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=Path("/snapshot"))
    parser.add_argument("--request", type=Path, default=Path("/request/request.json"))
    parser.add_argument("--acceptance-request", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=Path("/output"))
    args = parser.parse_args(argv)
    output_safe = (not args.output.exists() or (args.output.is_dir() and not any(args.output.iterdir())))
    output_safe = output_safe and not any(path.is_symlink() or path.is_junction()
        for path in (args.output, *args.output.parents))
    try:
        wire = read_json(args.request.parent, args.request.name)
        if args.acceptance_request is None:
            require(isinstance(wire, dict) and set(wire) == {"original_request", "acceptance_request"})
            original, acceptance = wire["original_request"], wire["acceptance_request"]
        else:
            original = wire
            acceptance = read_json(args.acceptance_request.parent, args.acceptance_request.name)
        run_acceptance(args.snapshot, original, acceptance, args.output)
        return 0
    except Exception as exc:
        # Raw exception messages can contain source text or paths; private logs
        # retain only image-owned error codes and the exception class.
        failure = {
            "schema": "ppt-service.acceptance-error.v1", "publication_authorized": False,
            "error_type": type(exc).__name__,
            "error_code": exc.code if isinstance(exc, ControlError) else "acceptance_validation_failed",
        }
        if output_safe:
            atomic_json(args.output / "acceptance-error.json", failure)
        print(canonical_bytes(failure).decode(), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
