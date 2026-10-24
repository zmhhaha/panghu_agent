"""Source-bound Hermes drafts for human review, never publication authority.

The trusted controller must stop every writer and supply a private immutable
snapshot before invoking this module in the existing network-none validator.
Worker JSON is not operating-system stop proof. This module runs the existing
independent checks in one process, retains their evidence and copies only the
checked PPTX and freshly rendered PNGs into the review artifact index.

No database, queue, model request, human acceptance or publication occurs here.
Persisted JSON is an audit receipt; it cannot recreate the process-local result.

The receipt binds the original manifest SHA and input SHA to the complete source
fingerprint. ``pages`` maps each SVG SHA to a newly rendered PNG SHA; only those
PNGs and the exact independently inspected PPTX enter ``artifacts``. Remaining
export/visual/PowerPoint gates are explicit. A review draft is not an eligible
revision baseline. ``read_candidate_delivery`` verifies a bounded transport copy
but returns ordinary audit data, never reconstructed trusted evidence.
"""
from __future__ import annotations

import argparse
import copy
import os
from pathlib import Path
import re
import uuid

from .execution import ControlError, PINNED_WORKER, PPT_MASTER_COMMIT
from .postflight_validation import PENDING_GATES, SOURCE_GATE, PostflightEvidence, run_postflight
from .publisher import check_session_database, complete_index
from .render_validation import (
    CACHE_ROOTS, DELIVERY_GATE, NATIVE_GATE, RASTER_GATE, ProgrammaticEvidence,
    fingerprint, hash_file, source_binding, verify_programmatic,
)
from .staging import atomic_json, copy_file, make_readonly, open_regular, read_json, relative

SCHEMA = "ppt-service.hermes-review-delivery.v1"
MAX_ARTIFACT_BYTES = 128 * 1024**2
MAX_RETAINED_BYTES = 512 * 1024**2
MAX_RETAINED_FILES = 4096
_ISSUER = object()
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_IMAGE = re.compile(r"[^@\s]+@sha256:[a-f0-9]{64}\Z")


def _require(value, code):
    if not value:
        raise ControlError(code)


def _runtime_image(request):
    """Bind the controller's approved overlay to the immutable Pod environment."""
    image = os.environ.get("PPT_RUNTIME_WORKER_IMAGE")
    _require(isinstance(image, str) and _IMAGE.fullmatch(image)
             and request.get("execution_image") == image, "draft_execution_image_mismatch")
    return image


class CandidateDeliveryReceipt:
    """Trusted in-process draft result; deliberately has no publish method."""

    __slots__ = ("_issuer", "_payload", "_seal", "_nonce", "_source")

    def __init__(self, *args, **kwargs):
        raise TypeError("Candidate receipts are issued by run_candidate_delivery")

    @classmethod
    def _issue(cls, payload):
        result = object.__new__(cls)
        result._issuer = _ISSUER
        result._payload = copy.deepcopy(payload)
        result._seal = fingerprint(payload)
        result._nonce = payload["controller_nonce"]
        result._source = payload["source_manifest_sha256"]
        return result

    def verify(self, controller_nonce, source_manifest_sha256):
        try:
            return (self._issuer is _ISSUER and self._nonce == controller_nonce
                    and self._source == source_manifest_sha256
                    and self._seal == fingerprint(self._payload))
        except (AttributeError, TypeError, ValueError):
            return False

    def report(self):
        return copy.deepcopy(self._payload)

    def __reduce__(self):
        raise TypeError("Candidate receipt JSON is not a trusted certificate")


def _request(snapshot, request):
    _require(isinstance(request, dict), "draft_request_invalid")
    try:
        nonce = request.get("validation_nonce")
        _require(isinstance(nonce, str) and str(uuid.UUID(nonce)) == nonce, "draft_nonce_invalid")
    except (ValueError, AttributeError):
        raise ControlError("draft_nonce_invalid") from None
    _require(request.get("phase") in ("generate", "revise")
             and type(request.get("pages")) is int and 3 <= request["pages"] <= 20
             and type(request.get("attempt")) is int and request["attempt"] > 0
             and all(isinstance(request.get(k), str) and 0 < len(request[k]) <= 200
                     for k in ("job_id", "session_id"))
             and all(isinstance(request.get(k), str) and _SHA.fullmatch(request[k])
                     for k in ("input_snapshot_sha256", "source_manifest_sha256"))
             and isinstance(request.get("expected_charts"), list), "draft_request_invalid")
    execution = read_json(snapshot / "result", "execution.json")
    _require(all(execution.get(k) == request[k] for k in ("job_id", "attempt", "phase", "session_id")),
             "draft_execution_identity_mismatch")
    _require(execution.get("status") == "completed" and execution.get("gateway_stopped") is True
             and execution.get("preview_stopped") is True, "draft_worker_not_completed")
    # Corroboration only. The controller must separately obtain real stop proof.
    raw = read_json(snapshot / "result", "manifest.json")
    _require(raw.get("status") == "candidate" and raw.get("phase") == request["phase"]
             and raw.get("input_snapshot_sha256") == request["input_snapshot_sha256"],
             "draft_candidate_not_completed")
    _require(hash_file(snapshot / "result", "manifest.json")["sha256"]
             == request["source_manifest_sha256"], "draft_manifest_mismatch")
    completed = complete_index(snapshot, raw)[0]
    from ppt_service.worker.candidate_validation import ValidationError, validate_manifest
    try:
        validate_manifest(snapshot / "project", completed, expected_charts=request["expected_charts"])
    except ValidationError:
        raise ControlError("draft_candidate_integrity_invalid") from None
    check_session_database(snapshot, request["session_id"])
    if request["phase"] == "generate":
        project = snapshot / "project" / relative(completed["project_root"])
        for filename, field in (("design_spec.md", "approved_design_spec_sha256"),
                                ("spec_lock.md", "approved_spec_lock_sha256")):
            expected = request.get(field)
            _require(isinstance(expected, str) and _SHA.fullmatch(expected)
                     and hash_file(project, filename)["sha256"] == expected, "draft_approved_spec_changed")
    return completed


def _fresh_output(output, snapshot):
    _require(not output.exists() and not output.is_symlink()
             and not output.resolve().is_relative_to(snapshot.resolve())
             and not snapshot.resolve().is_relative_to(output.resolve()), "draft_output_not_fresh")
    for parent in output.parents:
        _require(not parent.is_symlink() and not parent.is_junction(), "draft_output_linked")
    output.mkdir(parents=True)


def _inventory(output):
    retained, total = [], 0
    for path in sorted(output.rglob("*")):
        _require(not path.is_symlink() and not path.is_junction(), "draft_retained_linked")
        if path.is_dir():
            continue
        key = path.relative_to(output).as_posix()
        if key == "candidate-delivery.json":
            continue
        row = hash_file(output, key)
        total += row["bytes"]
        _require(len(retained) < MAX_RETAINED_FILES and total <= MAX_RETAINED_BYTES, "draft_retention_limit")
        retained.append(row)
    return retained, total


def _compose(snapshot, manifest, request, programmatic, postflight, programmatic_root, output, execution_image):
    """Only live objects from the preceding checks can authorize a draft copy."""
    source_sha, nonce = request["source_manifest_sha256"], request["validation_nonce"]
    _require(_runtime_image(request) == execution_image, "draft_execution_image_mismatch")
    _require(type(programmatic) is ProgrammaticEvidence, "draft_programmatic_evidence_invalid")
    prior = programmatic.report()
    _require(programmatic.verify(prior.get("validation_id"), source_sha), "draft_programmatic_evidence_invalid")
    _require(type(postflight) is PostflightEvidence and postflight.verify(nonce, source_sha),
             "draft_postflight_evidence_invalid")
    audit = postflight.report()
    binding, source, index = source_binding(snapshot, manifest, request)
    _require(complete_index(snapshot, read_json(snapshot / "result", "manifest.json"))[0] == manifest,
             "draft_source_changed")
    # Cover all source/resource bytes, including spec/lock, not just SVG/PPTX.
    source_rows = [hash_file(source, key) for key in sorted(index)
                   if key.split("/", 1)[0] not in CACHE_ROOTS]
    binding["copied_source_fingerprint"] = fingerprint(source_rows)
    for report in (prior, audit):
        _require(report.get("controller_nonce") == nonce and report.get("worker_image") == PINNED_WORKER
                 and report.get("ppt_master_commit") == PPT_MASTER_COMMIT
                 and report.get("source") == binding, "draft_evidence_binding_mismatch")
    _require(audit.get("source_manifest_sha256") == source_sha
             and audit.get("input_snapshot_sha256") == request["input_snapshot_sha256"]
             and audit.get("source_and_quality_verified") is True and audit.get("source_unchanged") is True
             and SOURCE_GATE in audit.get("completed_programmatic_gates", []), "draft_postflight_incomplete")
    _require(all(gate in prior.get("completed_programmatic_gates", [])
                 for gate in (RASTER_GATE, DELIVERY_GATE, NATIVE_GATE))
             and prior.get("preview_group_stopped") is True and prior.get("command_groups_stopped") is True,
             "draft_programmatic_incomplete")
    reused = audit.get("reused_programmatic_evidence", {})
    _require(reused.get("validation_id") == prior.get("validation_id")
             and reused.get("report_sha256") == fingerprint(prior), "draft_evidence_pair_mismatch")
    _require(audit.get("oracle", {}).get("expected_charts_sha256") == fingerprint(request["expected_charts"]),
             "draft_oracle_mismatch")
    _require(audit.get("publication_authorization") is False
             and audit.get("full_exporter_postflight_verified") is False, "draft_authority_invalid")
    pngs = prior.get("pngs")
    _require(isinstance(pngs, list) and len(pngs) == request["pages"], "draft_preview_roster_invalid")
    artifacts, pages, total = [], [], 0
    pptx = binding["pptx"]
    _require(pptx["bytes"] <= MAX_ARTIFACT_BYTES, "draft_artifact_limit")
    copied = copy_file(source, pptx["path"], output / "review-artifacts/presentation.pptx", expected_hash=pptx["sha256"])
    artifacts.append({**copied, "path": "review-artifacts/presentation.pptx", "role": "pptx",
                      "media_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation"})
    total += copied["bytes"]
    from PIL import Image
    for ordinal, (png, svg) in enumerate(zip(pngs, binding["svg_sources"]), 1):
        _require(png.get("ordinal") == ordinal == svg["ordinal"] and png.get("svg_path") == svg["path"]
                 and png.get("svg_sha256") == svg["sha256"], "draft_preview_binding_invalid")
        key = relative(png.get("relative_path"))
        # The independently regenerated directory is fixed; caller/Actor paths cannot select another file.
        _require(key == "scratch-projects/" + binding["project_root"] + "/.preview/" + relative(png.get("path"))
                 and "/" not in png["path"], "draft_preview_path_invalid")
        actual = hash_file(programmatic_root, key)
        _require(all(actual[k] == png.get(k) for k in ("sha256", "bytes")), "draft_preview_hash_mismatch")
        with open_regular(programmatic_root, key, 64 * 1024**2) as stream, Image.open(stream) as image:
            _require(image.format == "PNG" and image.size == (svg["png_width"], svg["png_height"])
                     and image.size == (png.get("width"), png.get("height")), "draft_preview_canvas_invalid")
            image.verify()
        total += actual["bytes"]
        _require(total <= MAX_ARTIFACT_BYTES, "draft_artifact_limit")
        target = f"review-artifacts/slides/{ordinal}.png"
        row = copy_file(programmatic_root, key, output / target, expected_hash=actual["sha256"])
        artifacts.append({**row, "path": target, "role": "preview", "media_type": "image/png", "ordinal": ordinal})
        pages.append({"number": ordinal, "preview_path": target, "preview_sha256": row["sha256"],
                      "source_sha256": svg["sha256"], "width": svg["png_width"], "height": svg["png_height"]})
    _require(hash_file(snapshot / "result", "manifest.json")["sha256"] == source_sha, "draft_source_changed")
    # Recheck every source after copying. Failed runs retain partial private output but no receipt.
    _require(all(hash_file(source, row["path"]) == row for row in source_rows), "draft_source_changed")
    pending = list(dict.fromkeys([*PENDING_GATES, *audit.get("required_pending_gates", [])]))
    _require(complete_index(snapshot, read_json(snapshot / "result", "manifest.json"))[0] == manifest,
             "draft_source_changed")
    retained, retained_bytes = _inventory(output)
    receipt = {
        "schema": SCHEMA, "status": "review", "engine": "hermes", "accepted": False,
        "delivery_kind": "review_candidate", "validation_status": "gate_pending",
        "original_candidate_status": "candidate", "revision_base_eligible": False,
        "published": False, "publication_authorization": False, "review_artifacts_ready": True,
        "controller_nonce": nonce, "source_manifest_sha256": source_sha,
        "input_snapshot_sha256": request["input_snapshot_sha256"],
        **{key: request[key] for key in ("job_id", "attempt", "phase", "session_id")},
        "worker_image": PINNED_WORKER, "ppt_master_commit": PPT_MASTER_COMMIT,
        "execution_image": execution_image,
        "source": binding, "source_fingerprint_sha256": binding["copied_source_fingerprint"],
        "programmatic_validation_id": prior["validation_id"],
        "programmatic_report_sha256": fingerprint(prior), "postflight_report_sha256": fingerprint(audit),
        "completed_programmatic_gates": [RASTER_GATE, DELIVERY_GATE, NATIVE_GATE, SOURCE_GATE],
        "required_pending_gates": pending, "full_exporter_postflight_verified": False,
        "independent_visual_review": False, "powerpoint_acceptance": "pending",
        "stop_proof_requires_controller": True, "transport_verified": False,
        "native_chart_count": prior["native"]["chart_count"], "pages": pages,
        "artifacts": artifacts, "artifact_bytes": total, "retained_files": retained,
        "retained_bytes": retained_bytes,
    }
    issued = CandidateDeliveryReceipt._issue(receipt)
    receipt_path = output / "candidate-delivery.json"
    try:
        atomic_json(receipt_path, receipt)
        make_readonly(output)
    except Exception:
        # A failed freeze must not leave a discoverable success receipt. Keep
        # all other private diagnostics for the controller to inspect.
        if receipt_path.exists():
            output.chmod(0o700)
            receipt_path.chmod(0o600)
            receipt_path.unlink()
        raise
    return issued


def read_candidate_delivery(root, request):
    """Verify copied receipt/files without issuing acceptance or stop proof.

    ``root`` must be controller-owned, no longer mounted into any running
    validator, and owner-private. Expected request identity comes from the
    controller's fenced attempt. The controller separately proves teardown and
    atomically commits review storage; this helper checks transport bytes only.
    """
    root = Path(root).absolute()
    _require(not root.is_symlink() and not root.is_junction(), "draft_retained_linked")
    value = read_json(root, "candidate-delivery.json")
    _require(value.get("schema") == SCHEMA and value.get("engine") == "hermes"
             and value.get("status") == "review" and value.get("delivery_kind") == "review_candidate"
             and value.get("validation_status") == "gate_pending"
             and value.get("original_candidate_status") == "candidate"
             and value.get("review_artifacts_ready") is True
             and value.get("stop_proof_requires_controller") is True
             and value.get("powerpoint_acceptance") == "pending"
             and all(value.get(key) is False for key in (
                 "accepted", "published", "publication_authorization", "transport_verified",
                 "independent_visual_review", "full_exporter_postflight_verified", "revision_base_eligible")),
             "draft_receipt_invalid")
    _require(all(value.get(key) == request.get(key) for key in (
        "job_id", "attempt", "phase", "session_id", "input_snapshot_sha256", "source_manifest_sha256"))
             and value.get("controller_nonce") == request.get("validation_nonce")
             and value.get("worker_image") == PINNED_WORKER and value.get("ppt_master_commit") == PPT_MASTER_COMMIT,
             "draft_transport_identity_mismatch")
    image = request.get("execution_image")
    _require(isinstance(image, str) and _IMAGE.fullmatch(image)
             and value.get("execution_image") == image, "draft_execution_image_mismatch")
    _require(set(PENDING_GATES).issubset(value.get("required_pending_gates", []))
             and all(gate in value.get("completed_programmatic_gates", [])
                     for gate in (RASTER_GATE, DELIVERY_GATE, NATIVE_GATE, SOURCE_GATE))
             and type(value.get("native_chart_count")) is int
             and value["native_chart_count"] == len(request.get("expected_charts", [])), "draft_receipt_gates_invalid")
    rows, total = _inventory(root)
    _require(rows == value.get("retained_files") and total == value.get("retained_bytes"), "draft_transport_files_mismatch")
    index = {row["path"]: row for row in rows}
    artifacts, pages, binding = value.get("artifacts"), value.get("pages"), value.get("source")
    _require(isinstance(artifacts, list) and isinstance(pages, list) and isinstance(binding, dict)
             and len(pages) == request.get("pages") and len(artifacts) == len(pages) + 1,
             "draft_transport_roster_invalid")
    keys = ["review-artifacts/presentation.pptx", *[f"review-artifacts/slides/{n}.png" for n in range(1, len(pages) + 1)]]
    _require([row.get("path") for row in artifacts] == keys
             and sum(row.get("bytes", MAX_ARTIFACT_BYTES + 1) for row in artifacts) == value.get("artifact_bytes")
             and value["artifact_bytes"] <= MAX_ARTIFACT_BYTES, "draft_transport_roster_invalid")
    for row in artifacts:
        _require(row["path"] in index and all(row.get(k) == index[row["path"]][k] for k in ("sha256", "bytes")),
                 "draft_transport_artifact_mismatch")
    _require(artifacts[0].get("role") == "pptx"
             and artifacts[0].get("media_type") == "application/vnd.openxmlformats-officedocument.presentationml.presentation"
             and artifacts[0]["sha256"] == binding.get("pptx", {}).get("sha256")
             and binding.get("source_manifest_sha256") == request["source_manifest_sha256"]
             and binding.get("input_snapshot_sha256") == request["input_snapshot_sha256"]
             and binding.get("copied_source_fingerprint") == value.get("source_fingerprint_sha256"),
             "draft_transport_source_mismatch")
    svgs = binding.get("svg_sources")
    _require(isinstance(svgs, list) and len(svgs) == len(pages), "draft_transport_roster_invalid")
    for ordinal, (page, artifact, svg) in enumerate(zip(pages, artifacts[1:], svgs), 1):
        _require(page.get("number") == artifact.get("ordinal") == svg.get("ordinal") == ordinal
                 and artifact.get("role") == "preview" and artifact.get("media_type") == "image/png"
                 and page.get("preview_path") == artifact["path"]
                 and page.get("preview_sha256") == artifact["sha256"] and page.get("source_sha256") == svg.get("sha256")
                 and page.get("width") == svg.get("png_width") and page.get("height") == svg.get("png_height"),
                 "draft_transport_preview_mismatch")
    for folder, report_key in (("programmatic", "programmatic_report_sha256"), ("postflight", "postflight_report_sha256")):
        name = "programmatic-evidence.json" if folder == "programmatic" else "postflight-evidence.json"
        report = read_json(root / folder, name)
        _require(fingerprint(report) == value.get(report_key) and report.get("source") == binding
                 and report.get("controller_nonce") == request["validation_nonce"], "draft_transport_evidence_mismatch")
    return value


def run_candidate_delivery(snapshot, request, trusted_oracle, authorized_source_sha256, output):
    """Run real independent checks and issue a draft receipt in a new directory.

    ``request``/oracle/authorized hashes are controller-owned inputs. The caller
    must already have verified all writers stopped and mounted snapshot readonly.
    The result can authorize owner-scoped *review* storage only. Controller-side
    bounded copy/hash verification and fenced database commit remain required.
    """
    snapshot, output = Path(snapshot).absolute(), Path(output).absolute()
    _require(isinstance(request, dict), "draft_request_invalid")
    execution_image = _runtime_image(request)
    manifest = _request(snapshot, request)
    _fresh_output(output, snapshot)
    programmatic = verify_programmatic(snapshot, manifest, request, output / "programmatic", str(uuid.uuid4()))
    postflight = run_postflight(snapshot, manifest, request, trusted_oracle, authorized_source_sha256,
                               output / "postflight", prior_evidence=programmatic)
    return _compose(snapshot, manifest, request, programmatic, postflight, output / "programmatic", output, execution_image)


def main():
    """Fixed validator entrypoint; no caller-supplied success/stop flags accepted."""
    parser = argparse.ArgumentParser(description="Independently validate Hermes review artifacts")
    parser.add_argument("--snapshot", type=Path, default=Path("/snapshot"))
    parser.add_argument("--request", type=Path, default=Path("/request/request.json"))
    parser.add_argument("--output", type=Path, default=Path("/output/delivery"))
    args = parser.parse_args()
    try:
        envelope = read_json(args.request.parent, args.request.name)
        request = {key: value for key, value in envelope.items()
                   if key not in ("trusted_oracle", "authorized_source_sha256")}
        authorized = envelope.get("authorized_source_sha256")
        _require(isinstance(authorized, list) and all(isinstance(value, str) for value in authorized)
                 and len(authorized) == len(set(authorized)), "draft_authorized_sources_invalid")
        run_candidate_delivery(args.snapshot, request, envelope.get("trusted_oracle"), set(authorized), args.output)
        return 0
    except Exception:
        # Partial output is private failed evidence, never a candidate receipt.
        # Do not echo tool stderr, paths, input text or upstream diagnostics.
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
