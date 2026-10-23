"""Independent, source-bound postflight checks on a stopped private snapshot.

Run in the existing network-none validator, never in the API process. This
module neither exports a deck nor publishes one. Export-time guarantees that
cannot be reconstructed stay pending; an old export report cannot supply them.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import re
import uuid

from .execution import ControlError, PINNED_WORKER, PPT_MASTER_COMMIT
from .render_validation import (
    DELIVERY_GATE, NATIVE_GATE, RASTER_GATE, OfficialCommands, ProgrammaticEvidence,
    SCRIPTS, PYTHON, fingerprint, fresh_project, hash_file, source_binding,
    unchanged,
)
from .staging import atomic_json, open_regular, read_json, relative

SOURCE_GATE = "independent_postflight_source_and_quality"
PENDING_GATES = (
    "independent_export_build_contract",
    "independent_visual_review",
    "powerpoint_open_edit_save_reopen",
)
_ISSUER = object()
_SHA = re.compile(r"[0-9a-f]{64}\Z")


class PostflightEvidence:
    """Process-local proof. JSON reports are for audit, never authorization."""

    __slots__ = ("_issuer", "_payload", "_seal", "_nonce", "_source")

    def __init__(self, *args, **kwargs):
        raise TypeError("Postflight evidence is issued only by run_postflight")

    @classmethod
    def _issue(cls, payload, nonce, source):
        result = object.__new__(cls)
        result._issuer = _ISSUER
        result._payload = copy.deepcopy(payload)
        result._seal = fingerprint(result._payload)
        result._nonce, result._source = nonce, source
        return result

    def verify(self, controller_nonce, source_manifest_sha256):
        try:
            return (
                self._issuer is _ISSUER and self._nonce == controller_nonce
                and self._source == source_manifest_sha256
                and self._seal == fingerprint(self._payload)
            )
        except (AttributeError, TypeError, ValueError):
            return False

    def report(self):
        return copy.deepcopy(self._payload)

    def __reduce__(self):
        raise TypeError("Postflight evidence cannot cross the process boundary")


def _require(condition, code):
    if not condition:
        raise ControlError(code)


def _sha(value):
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _count(value, code="postflight_report_invalid"):
    _require(type(value) is int and value >= 0, code)
    return value


def svg_fingerprint(rows):
    """Exact pinned exporter algorithm, including the complete file roster."""
    files = []
    aggregate = hashlib.sha256()
    for row in sorted(rows, key=lambda item: Path(item["path"]).name):
        name = Path(row["path"]).name
        _require(_sha(row.get("sha256")), "postflight_source_hash_invalid")
        files.append({"file": name, "sha256": row["sha256"]})
        aggregate.update(name.encode("utf-8") + b"\0" + row["sha256"].encode("ascii") + b"\n")
    _require(len({row["file"] for row in files}) == len(files), "postflight_duplicate_slide")
    return {"algorithm": "sha256", "digest": aggregate.hexdigest(), "file_count": len(files), "files": files}


def _oracle(oracle, authorized, request):
    # Both arguments are supplied by the controller from confirmed input
    # records, not loaded from candidate files or accepted from an HTTP body.
    _require(isinstance(oracle, dict) and isinstance(oracle.get("expected_charts"), list),
             "postflight_trusted_oracle_required")
    sources = oracle.get("source_sha256")
    _require(isinstance(sources, list) and all(_sha(v) for v in sources)
             and len(sources) == len(set(sources)), "postflight_oracle_source_invalid")
    _require(isinstance(authorized, (set, frozenset)) and all(_sha(v) for v in authorized),
             "postflight_authorized_sources_invalid")
    _require(set(sources).issubset(authorized), "postflight_oracle_source_mismatch")
    _require(not oracle["expected_charts"] or bool(sources), "postflight_oracle_source_required")
    _require(fingerprint(oracle["expected_charts"]) == fingerprint(request.get("expected_charts")),
             "postflight_oracle_chart_mismatch")
    return {"source_sha256": sorted(sources), "expected_charts_sha256": fingerprint(oracle["expected_charts"])}


def _prior(evidence, binding, request):
    _require(type(evidence) is ProgrammaticEvidence, "postflight_programmatic_evidence_invalid")
    report = evidence.report()
    _require(evidence.verify(report.get("validation_id"), binding["source_manifest_sha256"]),
             "postflight_programmatic_evidence_invalid")
    _require(report.get("controller_nonce") == request["validation_nonce"], "postflight_nonce_mismatch")
    _require(report.get("worker_image") == PINNED_WORKER
             and report.get("ppt_master_commit") == PPT_MASTER_COMMIT, "postflight_runtime_mismatch")
    _require(all(report.get("source", {}).get(k) == v for k, v in binding.items()),
             "postflight_prior_source_mismatch")
    gates = report.get("completed_programmatic_gates", [])
    _require(isinstance(gates, list) and all(gate in gates for gate in (RASTER_GATE, DELIVERY_GATE, NATIVE_GATE)),
             "postflight_programmatic_evidence_incomplete")
    native = report.get("native", {})
    _require(native.get("slide_count") == request["pages"]
             and type(native.get("chart_count")) is int
             and native["chart_count"] == len(request["expected_charts"])
             and (not native["chart_count"] or native.get("chart_data_verified") is True),
             "postflight_native_oracle_unverified")
    delivery = report.get("delivery", {})
    _require(delivery.get("status") in ("passed", "passed-with-advisories")
             and isinstance(delivery.get("advisories"), list)
             and _count(delivery.get("advisory_count")) == len(delivery["advisories"])
             and bool(delivery["advisories"]) == (delivery["status"] == "passed-with-advisories"),
             "postflight_delivery_unverified")
    _require(report.get("preview_group_stopped") is True and report.get("command_groups_stopped") is True,
             "postflight_previous_writers_not_stopped")
    return report


def check_quality_report(report, project, rows):
    """Reject stale fingerprints, incomplete rosters and contradictory counts."""
    _require(isinstance(report, dict) and report.get("schema") == "ppt-master.svg-quality-report.v1"
             and report.get("stage") == "final" and report.get("target") == str(project.resolve()),
             "postflight_quality_contract_mismatch")
    expected = svg_fingerprint(rows)
    _require(report.get("source_fingerprint") == expected, "postflight_quality_source_mismatch")
    files = report.get("files")
    _require(isinstance(files, list) and len(files) == len(rows), "postflight_quality_roster_mismatch")
    indexed = {str((project / row["path"]).resolve()): row for row in rows}
    seen = set()
    for record in files:
        _require(isinstance(record, dict), "postflight_quality_roster_mismatch")
        path = record.get("path")
        _require(isinstance(path, str) and path in indexed and path not in seen,
                 "postflight_quality_roster_mismatch")
        seen.add(path)
        _require(record.get("exists") is True and record.get("source_sha256") == indexed[path]["sha256"],
                 "postflight_quality_source_mismatch")
        _require(record.get("errors") == [], "postflight_quality_failed")
        _require(isinstance(record.get("warnings"), list), "postflight_report_invalid")
    summary = report.get("summary")
    _require(isinstance(summary, dict) and _count(summary.get("total")) == len(rows),
             "postflight_quality_roster_mismatch")
    _require(_count(summary.get("errors")) == 0, "postflight_quality_failed")
    warnings = _count(summary.get("warnings"))
    project_issues = report.get("project_issues")
    issue_groups = {"template", "animation", "illustration", "communication_trace", "pptx_structure"}
    _require(isinstance(project_issues, dict) and set(project_issues) == issue_groups,
             "postflight_report_invalid")
    project_warnings = 0
    for issues in project_issues.values():
        _require(isinstance(issues, list), "postflight_report_invalid")
        for issue in issues:
            _require(isinstance(issue, dict) and issue.get("severity") in ("warning", "error"),
                     "postflight_report_invalid")
            _require(issue["severity"] != "error", "postflight_quality_failed")
            project_warnings += 1
    # Upstream counts warning-bearing pages, then adds project-level issues.
    # It does not count each per-page warning in summary.warnings.
    warning_pages = sum(bool(record["warnings"]) for record in files)
    _require(warnings == warning_pages + project_warnings, "postflight_quality_count_mismatch")
    _require(_count(summary.get("passed")) == len(files) - warning_pages,
             "postflight_quality_count_mismatch")
    categories = report.get("categories")
    _require(isinstance(categories, dict), "postflight_report_invalid")
    counts = {}
    for name in ("blocking", "introduced", "inherited"):
        category = categories.get(name)
        _require(isinstance(category, dict) and isinstance(category.get("issues"), list), "postflight_report_invalid")
        count = _count(category.get("count"))
        _require(count == len(category["issues"]), "postflight_report_invalid")
        counts[name] = count
    _require(counts["blocking"] == 0, "postflight_quality_failed")
    _require(counts["introduced"] == sum(len(record["warnings"]) for record in files) + project_warnings,
             "postflight_quality_count_mismatch")
    imported = categories.get("source-import")
    _require(isinstance(imported, dict) and isinstance(imported.get("summary"), dict),
             "postflight_report_invalid")
    counts["source-import"] = _count(imported.get("count"))
    _require(counts["source-import"] == _count(imported["summary"].get("warning_count")),
             "postflight_report_invalid")
    # Warnings remain evidence. Neither inherited nor introduced warnings are
    # silently waived by a zero checker exit code.
    return {"source_fingerprint": expected, "summary": copy.deepcopy(summary),
            "category_counts": counts, "warnings_pending": warnings > 0 or any(counts.values())
            or any(row["warnings"] for row in files)}


def check_resource_report(report):
    _require(isinstance(report, dict) and isinstance(report.get("unresolved_template_tokens"), list),
             "postflight_resources_invalid")
    fonts, images = report.get("fonts"), report.get("images")
    _require(isinstance(fonts, dict) and isinstance(images, dict), "postflight_resources_invalid")
    for name in ("stacks", "generic_only_stacks", "unsafe_exported_faces"):
        _require(isinstance(fonts.get(name), list), "postflight_resources_invalid")
    for name in ("data_uri", "local", "external"):
        _count(images.get(name), "postflight_resources_invalid")
    _require(isinstance(images.get("external_references"), list)
             and images["external"] == len(images["external_references"]), "postflight_resources_invalid")
    _require(images["external"] == 0, "postflight_external_resource")
    return {"audit": copy.deepcopy(report), "warnings_pending": bool(
        report["unresolved_template_tokens"] or fonts["generic_only_stacks"] or fonts["unsafe_exported_faces"])}


class OfficialPostflightAudit:
    """Runs only image-owned tools inside the already restricted validator."""

    def run(self, project, rows, output):
        commit = Path("/opt/ppt-service/build-info/ppt-master.commit").read_text().strip()
        _require(commit == PPT_MASTER_COMMIT, "postflight_official_version_mismatch")
        names = (
            "attribution_guard.py", "project_manager.py", "svg_quality_checker.py",
            "svg_quality/checker.py", "svg_to_pptx/pptx_package/cli.py",
        )
        scripts = {name: hash_file(SCRIPTS, name)["sha256"] for name in names}
        commands = OfficialCommands()
        output.mkdir()
        for name, args in (
            ("attribution", [PYTHON, str(SCRIPTS / "attribution_guard.py")]),
            ("project", [PYTHON, str(SCRIPTS / "project_manager.py"), "validate", str(project)]),
        ):
            code = commands.run(args, output / (name + ".stdout.log"), output / (name + ".stderr.log"))
            _require(code == 0, "postflight_official_gate_failed")
        quality = project / "validation/svg_quality_report.json"
        _require(not quality.exists(), "postflight_stale_quality_report")
        code = commands.run([
            PYTHON, str(SCRIPTS / "svg_quality_checker.py"), str(project),
            "--stage", "final", "--json", "--json-output", str(quality),
        ], output / "quality.stdout.log", output / "quality.stderr.log")
        _require(code == 0, "postflight_quality_failed")
        # Audit helpers are read-only. Never invoke _write_postflight_report:
        # it would assert export-time checks without performing them.
        script = (
            "import json,sys; from pathlib import Path; "
            "sys.path.insert(0,sys.argv[1]); "
            "from svg_to_pptx.pptx_package.cli import _source_resource_audit; "
            "print(json.dumps(_source_resource_audit([Path(p) for p in sys.argv[2:]])))"
        )
        code = commands.run([PYTHON, "-c", script, str(SCRIPTS),
                             *[str(project / row["path"]) for row in rows]],
                            output / "resources.json", output / "resources.stderr.log")
        _require(code == 0, "postflight_resource_audit_failed")
        return {"ppt_master_commit": commit, "official_script_sha256": scripts,
                "quality_exit_code": 0, "resources_exit_code": 0}


def run_postflight(snapshot, completed_manifest, request, trusted_oracle,
                   authorized_source_sha256, output, *, prior_evidence=None):
    """Issue source/quality evidence, never publication or build authorization.

    The controller supplies request identity, trusted_oracle and authorized
    source hashes. prior_evidence must be the live ProgrammaticEvidence object
    from the existing independent runtime, not its transported JSON copy.
    """
    snapshot, output = Path(snapshot).absolute(), Path(output).absolute()
    nonce, source = request.get("validation_nonce"), request.get("source_manifest_sha256")
    try:
        _require(isinstance(nonce, str) and str(uuid.UUID(nonce)) == nonce, "postflight_nonce_invalid")
    except (ValueError, AttributeError):
        raise ControlError("postflight_nonce_invalid") from None
    _require(_sha(source) and _sha(request.get("input_snapshot_sha256")), "postflight_source_hash_invalid")
    _require(request.get("phase") in ("generate", "revise")
             and type(request.get("pages")) is int and 3 <= request["pages"] <= 20,
             "postflight_request_invalid")
    _require(not output.exists() and not output.is_symlink()
             and not output.resolve().is_relative_to(snapshot.resolve()), "postflight_output_not_fresh")
    actual = hash_file(snapshot / "result", "manifest.json", 4 * 1024**2)
    _require(actual["sha256"] == source, "postflight_source_manifest_mismatch")
    raw = read_json(snapshot / "result", "manifest.json")
    _require(raw.get("status") in ("candidate", "needs_input"), "postflight_candidate_status_invalid")
    payload = {
        "schema": "ppt-service.independent-postflight.v1", "controller_nonce": nonce,
        "source_manifest_sha256": source, "input_snapshot_sha256": request["input_snapshot_sha256"],
        "worker_image": PINNED_WORKER, "ppt_master_commit": PPT_MASTER_COMMIT,
        "original_candidate_status": raw["status"], "publication_authorization": False,
        "full_exporter_postflight_verified": False, "status": "pending",
        "completed_programmatic_gates": [], "required_pending_gates": list(PENDING_GATES),
    }
    if trusted_oracle is None or prior_evidence is None:
        missing = "trusted_source_oracle" if trusted_oracle is None else "independent_programmatic_evidence"
        payload["required_pending_gates"].insert(0, missing)
        output.mkdir(parents=True)
        atomic_json(output / "postflight-evidence.json", payload)
        return PostflightEvidence._issue(payload, nonce, source)
    oracle_binding = _oracle(trusted_oracle, authorized_source_sha256, request)
    binding, root, index = source_binding(snapshot, completed_manifest, request)
    previous = _prior(prior_evidence, binding, request)
    output.mkdir(parents=True)
    project = output / "source" / relative(binding["project_root"])
    original_rows = fresh_project(root, project, index)
    copied_fingerprint = fingerprint(original_rows)
    _require(previous["source"].get("copied_source_fingerprint") == copied_fingerprint,
             "postflight_prior_source_mismatch")
    binding["copied_source_fingerprint"] = copied_fingerprint
    atomic_json(output / "source-index.json", original_rows)
    atomic_json(output / "source-binding.json", binding)
    # Compare timestamps from the same filesystem. Its timestamp resolution
    # can lag time.time_ns(), incorrectly making a fresh report look stale.
    marker = output / "audit-start.marker"
    with marker.open("xb") as stream:
        stream.write(nonce.encode("ascii"))
    started_ns = marker.stat().st_mtime_ns
    runtime = OfficialPostflightAudit().run(project, binding["svg_sources"], output / "official")
    _require(runtime.get("ppt_master_commit") == PPT_MASTER_COMMIT, "postflight_official_version_mismatch")
    quality_path = project / "validation/svg_quality_report.json"
    _require(quality_path.exists() and quality_path.stat().st_mtime_ns >= started_ns,
             "postflight_stale_quality_report")
    quality = check_quality_report(read_json(quality_path.parent, quality_path.name), project, binding["svg_sources"])
    resources = check_resource_report(read_json(output / "official", "resources.json"))
    unchanged(project, original_rows)
    unchanged(root, original_rows)
    _require(hash_file(snapshot / "result", "manifest.json")["sha256"] == source,
             "postflight_source_changed")
    warnings = quality["warnings_pending"] or resources["warnings_pending"] or bool(previous["delivery"]["advisories"])
    payload.update({
        "source": binding, "oracle": oracle_binding, "official": runtime,
        "quality": quality, "resources": resources,
        "reused_programmatic_evidence": {"validation_id": previous["validation_id"],
            "report_sha256": fingerprint(previous), "gates": [DELIVERY_GATE, NATIVE_GATE],
            "delivery": previous["delivery"], "native": previous["native"]},
        "completed_programmatic_gates": [SOURCE_GATE],
        "source_and_quality_verified": True, "source_unchanged": True,
        "reports": {
            "quality": hash_file(quality_path.parent, quality_path.name),
            "resources": hash_file(output / "official", "resources.json"),
        },
    })
    if warnings:
        payload["required_pending_gates"].insert(0, "postflight_warnings_review")
    if raw["status"] == "needs_input":
        payload["required_pending_gates"].insert(0, "candidate_needs_input")
    atomic_json(output / "postflight-evidence.json", payload)
    return PostflightEvidence._issue(payload, nonce, source)
