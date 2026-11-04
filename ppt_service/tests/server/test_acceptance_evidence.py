"""Synthetic immutable-bundle contracts; no Office, model or real acceptance."""
import copy
import hashlib
import json
import os
from uuid import uuid4

import pytest

from agent_review_fixture import seed_agent_review
from test_api import ctx
from ppt_service.server.acceptance.evidence import BASE_GATES, SOURCE_GATE, FINAL_GATE, EXPORT_GATE, DELIVERY_GATE
from ppt_service.server.acceptance.resolver import fingerprint
from ppt_service.server.auth import Identity
from ppt_service.server.models import Checkpoint, Job, Project, Version


def seed_acceptance_bundle(app, client, data, *, passed=True, issued="2026-11-01T10:00:00Z"):
    url = f"/api/v1/projects/{data['pid']}/versions/{data['vid']}/acceptance"
    snapshot = client.get(url).json()
    binding = snapshot["binding"]
    eid = str(uuid4())
    prefix = f"projects/{data['pid']}/versions/{data['vid']}/acceptance-evidence/{eid}"
    root = app.state.storage.root / prefix

    def put(name, value):
        raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}

    image = "synthetic/acceptance-validator:latest@sha256:" + "e" * 64
    request = {"schema": "ppt-service.acceptance-request.v1", "evidence_id": eid, "validation_nonce": eid,
        "binding": binding, "job_id": data["jid"], "attempt": 1, "fencing_token": 19, "execution_image": image}
    request_row = put("request.json", request)
    source = {"controller_nonce": eid, "source_manifest_sha256": binding["manifest_sha256"],
              "input_snapshot_sha256": binding["input_snapshot_sha256"]}
    pptx_data = (app.state.storage.root / data["artifact_key"] / "presentation.pptx").read_bytes()
    pptx = {"sha256": hashlib.sha256(pptx_data).hexdigest(), "bytes": len(pptx_data)}
    # The API measures the signed immutable bytes/header. The isolated renderer
    # owns actual PNG decoding. This fixture intentionally does not impersonate
    # a real image or renderer execution.
    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (1280).to_bytes(4, "big") + (720).to_bytes(4, "big") + b"synthetic"
    rows = []
    for number in range(1, 4):
        row = put(f"final-pptx/pages/{number}.png", png)
        rows.append(dict(row, path=f"pages/{number}.png", ordinal=number, width=1280, height=720))
    fonts = put("final-pptx/font-inventory.txt", b"Synthetic font inventory, no rendering occurred")
    tools = {name: {"path": "/synthetic/" + name, "resolved_path": "/synthetic/" + name,
                    "sha256": "f" * 64, "version": "Synthetic test fixture"}
             for name in ("libreoffice", "pdfinfo", "pdftoppm", "fontconfig", "soffice_binary")}
    final = {**source, "schema": "ppt-service.final-pptx-render.v1", "status": "passed",
        "publication_authorized": False, "full_exporter_postflight_verified": False,
        "human_visual_acceptance": False, "powerpoint_acceptance": "pending", "pptx": pptx,
        "source_unchanged": True, "command_groups_stopped": True, "validator_stop_proof_requires_controller": True,
        "export_exit_code": 0, "raster_exit_code": 0, "completed_programmatic_gates": [FINAL_GATE],
        "renderer": {"name": "LibreOffice Impress", "version": "Synthetic test fixture", "tools": tools,
            "font_inventory": dict(fonts, path="font-inventory.txt"), "font_inventory_sha256": fonts["sha256"],
            "runtime_image": image, "pipeline": "PPTX → LibreOffice PDF → Poppler PNG", "isolated_profile": True},
        "deck": {"slide_count": 3, "chart_count": 0, "width": 1280, "height": 720},
        "pages": rows, "page_roster_sha256": fingerprint(rows)}
    final_row = put("final-pptx/final-pptx-evidence.json", final)
    delivery = {"status": "passed", "advisories": [], "advisory_count": 0}
    summary = {"total": 3, "passed": 3, "errors": 0, "warnings": 0}
    svg_sources = [{"ordinal": page["number"], "path": f"svg_output/{page['number']:02d}.svg", "sha256": page["source_sha256"]}
                   for page in snapshot["pages"]]
    svg_files = [{"file": row["path"].rsplit("/", 1)[-1], "sha256": row["sha256"]} for row in svg_sources]
    aggregate = hashlib.sha256()
    for row in svg_files:
        aggregate.update(row["file"].encode() + b"\0" + row["sha256"].encode() + b"\n")
    svg_fingerprint = {"algorithm": "sha256", "digest": aggregate.hexdigest(), "file_count": 3, "files": svg_files}
    counts = {"blocking": 0, "introduced": 0, "inherited": 0, "source-import": 0}
    quality = {"schema": "ppt-master.svg-quality-report.v1", "stage": "final",
        "source_fingerprint": svg_fingerprint, "summary": summary,
        "files": [{"errors": [], "warnings": []} for _ in range(3)],
        "project_issues": {name: [] for name in ("template", "animation", "illustration", "communication_trace", "pptx_structure")},
        "categories": {name: {"count": count, "issues": []} for name, count in counts.items()}}
    resources = {"unresolved_template_tokens": [], "fonts": {"generic_only_stacks": [], "unsafe_exported_faces": []},
                 "images": {"external": 0, "external_references": []}}
    quality_row = put("postflight/source/deck/validation/svg_quality_report.json", quality)
    resources_row = put("postflight/official/resources.json", resources)
    postflight = {**source, "schema": "ppt-service.independent-postflight.v1", "status": "pending",
        "original_candidate_status": "candidate", "publication_authorization": False,
        "full_exporter_postflight_verified": False, "completed_programmatic_gates": [SOURCE_GATE],
        "source_and_quality_verified": True, "source_unchanged": True,
        "source": {"source_manifest_sha256": binding["manifest_sha256"],
            "input_snapshot_sha256": binding["input_snapshot_sha256"],
            "copied_source_fingerprint": binding["source_fingerprint_sha256"], "pptx": pptx, "project_root": "deck", "svg_sources": svg_sources},
        "quality": {"warnings_pending": False, "source_fingerprint": quality["source_fingerprint"],
            "summary": summary, "category_counts": counts},
        "resources": {"warnings_pending": False, "audit": resources},
        "reports": {"quality": dict(quality_row, path="svg_quality_report.json"),
                    "resources": dict(resources_row, path="resources.json")},
        "reused_programmatic_evidence": {"delivery": delivery, "native": {"slide_count": 3, "chart_count": 0}}}
    postflight_row = put("postflight/postflight-evidence.json", postflight)
    rebuilt = put("equivalence/rebuilt/presentation.pptx", pptx_data)
    parts = [{"path": "ppt/slides/slide1.xml", "kind": "xml", "semantic_sha256": "a" * 64}]
    equivalence = {**source, "schema": "ppt-service.export-build-equivalence.v1",
        "status": "passed" if passed else "pending", "publication_authorized": False,
        "original_export_provenance_verified": False, "full_exporter_postflight_verified": False,
        "export_build_equivalence_verified": passed, "pptx": pptx,
        "source_fingerprint_sha256": binding["source_fingerprint_sha256"], "rebuilt_pptx": rebuilt,
        "completed_programmatic_gates": [EXPORT_GATE] if passed else [],
        "comparison": {"parts": parts, "part_count": len(parts), "semantic_sha256": fingerprint(parts),
            "normalizations": [], "zip_container_metadata_ignored": True, "original_export_provenance_verified": False},
        "official": {"ppt_master_commit": "c350bc31e6d43b7bcf5234183d650ba8f9f4c9d7",
            "script_sha256": {"export": "a" * 64}, "exit_codes": {"export": 0}}, "delivery": delivery}
    equivalence_row = put("equivalence/export-contract.json", equivalence)
    gates = set(BASE_GATES) | {SOURCE_GATE, FINAL_GATE}
    if passed:
        gates |= {EXPORT_GATE, DELIVERY_GATE}
    receipt = {"schema": "ppt-service.acceptance-evidence.v1", "evidence_id": eid, "issued_at": issued,
        "request_sha256": request_row["sha256"], "binding_sha256": fingerprint(binding), "execution_image": image,
        "status": "passed" if passed else "pending", "publication_authorized": False,
        "human_visual_acceptance": False, "powerpoint_acceptance": "pending",
        "completed_programmatic_gates": sorted(gates),
        "required_pending_gates": [] if passed else sorted((EXPORT_GATE, DELIVERY_GATE)),
        "final_pptx_report": {key: final_row[key] for key in ("path", "sha256")},
        "postflight_report": {key: postflight_row[key] for key in ("path", "sha256")},
        "equivalence_report": {key: equivalence_row[key] for key in ("path", "sha256")}}
    put("receipt.json", receipt)
    transport = {"schema": "ppt-service.acceptance-transport.v1", "evidence_id": eid,
        "request_sha256": request_row["sha256"], "binding_sha256": fingerprint(binding), "execution_image": image,
        "validator_scope_uid": str(uuid4()), "validator_pod_uid": str(uuid4()),
        "writers_stopped": True, "transport_verified": True, "validator_exit_code": 0}

    def freeze():
        files = [{"path": target.relative_to(root).as_posix(), "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                  "bytes": target.stat().st_size} for target in sorted(root.rglob("*"))
                 if target.is_file() and target.name != "transport-manifest.json"]
        put("transport-manifest.json", transport | {"files": files, "bytes": sum(row["bytes"] for row in files)})

    freeze()
    return {"root": root, "url": url, "binding": binding, "put": put, "freeze": freeze,
            "receipt": receipt, "transport": transport, "final": final, "postflight": postflight,
            "equivalence": equivalence, "eid": eid}


@pytest.fixture
def accepted_bundle(ctx):
    app, client, owners = ctx
    data = seed_agent_review(app, owners[0])
    bundle = seed_acceptance_bundle(app, client, data)
    return app, client, owners, data, bundle


def retain_source_quality_warnings(bundle, *, page_warnings=None, project_warnings=1):
    """Synthetic fixed-report warning case; no checker or real rendering ran."""
    raw = json.loads((bundle["root"] / "postflight/source/deck/validation/svg_quality_report.json").read_bytes())
    raw["target"] = "/private/source/deck"
    if page_warnings is None:
        page_warnings = {2: ["private.invalid /private/ source excerpt", "<script>private source</script>"],
                         3: ["Different independent source label warning"]}
    sources = bundle["postflight"]["source"]["svg_sources"]
    raw["files"] = [{"path": raw["target"] + "/" + source["path"], "exists": True,
        "source_sha256": source["sha256"], "errors": [], "warnings": page_warnings.get(source["ordinal"], [])}
        for source in reversed(sources)]
    raw["project_issues"]["template"] = [{"severity": "warning", "message": "/private/project secret"}
                                           for _ in range(project_warnings)]
    warning_pages = sum(bool(row["warnings"]) for row in raw["files"])
    raw["summary"] = {"total": len(sources), "passed": len(sources) - warning_pages,
                      "errors": 0, "warnings": warning_pages + project_warnings}
    introduced = [warning for row in raw["files"] for warning in row["warnings"]] + raw["project_issues"]["template"]
    raw["categories"]["introduced"] = {"count": len(introduced), "issues": introduced}
    postflight, equivalence, receipt = bundle["postflight"], bundle["equivalence"], bundle["receipt"]
    postflight["quality"].update(warnings_pending=True, summary=raw["summary"],
        category_counts={name: category["count"] for name, category in raw["categories"].items()})
    equivalence.update(status="needs_changes", export_build_equivalence_verified=False,
        completed_programmatic_gates=[], error_code="export_equivalence_source_warnings")
    receipt.update(status="needs_changes", completed_programmatic_gates=sorted(set(BASE_GATES) | {FINAL_GATE}),
                   required_pending_gates=sorted((EXPORT_GATE, DELIVERY_GATE)))
    retain_source_quality_report(bundle, raw)
    return raw


def retain_source_quality_report(bundle, raw):
    """Re-freeze synthetic report bytes without changing the acceptance gate result."""
    row = bundle["put"]("postflight/source/deck/validation/svg_quality_report.json", raw)
    postflight = bundle["postflight"]
    postflight["quality"]["summary"] = raw["summary"]
    postflight["reports"]["quality"] = dict(row, path="svg_quality_report.json")
    for name, field, path in (("postflight", "postflight_report", "postflight/postflight-evidence.json"),
                              ("equivalence", "equivalence_report", "equivalence/export-contract.json")):
        row = bundle["put"](path, bundle[name])
        bundle["receipt"][field] = {key: row[key] for key in ("path", "sha256")}
    bundle["put"]("receipt.json", bundle["receipt"])
    bundle["freeze"]()


@pytest.mark.parametrize("project_only", [False, True])
def test_source_warning_pages_are_bound_safe_and_survive_owner_refresh_without_promoting_any_gate(accepted_bundle, project_only):
    app, client, owners, data, bundle = accepted_bundle
    retain_source_quality_warnings(bundle, page_warnings={} if project_only else None)
    original_receipt = (bundle["root"] / "receipt.json").read_bytes()
    for _ in range(2):
        response = client.get(bundle["url"])
        assert response.status_code == 200, response.text
        snapshot = response.json()
        assert snapshot["programmatic_status"] == "needs_changes"
        assert snapshot["programmatic"]["source_diagnostics"] == {
            "error_code": "source_quality_warnings", "project_warning_count": 1,
            "items": [] if project_only else [{"page": 2, "warning_count": 2}, {"page": 3, "warning_count": 1}]}
        assert snapshot["programmatic"]["diagnostics"] is None
        assert len(snapshot["programmatic"]["rendered_pages"]) == 3
        assert SOURCE_GATE not in snapshot["programmatic"]["completed_gates"]
        assert snapshot["programmatic"]["pending_gates"] == sorted((EXPORT_GATE, DELIVERY_GATE))
        assert snapshot["visual_status"] == snapshot["powerpoint_status"] == "pending"
        assert snapshot["can_publish"] is snapshot["publication_authorized"] is False
        assert snapshot["records"] == []
        assert "/private/" not in response.text and "private.invalid" not in response.text and "private source" not in response.text
    assert (bundle["root"] / "receipt.json").read_bytes() == original_receipt
    with app.state.sessions() as session:
        assert session.get(Job, data["jid"]).status == "succeeded"
        assert session.get(Version, data["vid"]).status == "review"
        assert session.get(Project, data["pid"]).revision == 8
    owners[0] = Identity("https://issuer", "different-owner")
    assert client.get(bundle["url"]).status_code == 404


@pytest.mark.parametrize("fault", ["missing_target", "relative_target", "parent_target", "wrong_path", "duplicate_path",
    "missing_page", "source_hash", "exists", "errors", "warning_object", "summary_count", "boolean_count",
    "project_error", "project_shape", "unknown_project_group"])
def test_unsafe_source_warning_mapping_is_not_guessed_and_does_not_complete_any_gate(accepted_bundle, fault):
    _, client, _, _, bundle = accepted_bundle
    raw = retain_source_quality_warnings(bundle)
    if fault == "missing_target": raw.pop("target")
    elif fault == "relative_target": raw["target"] = "source/deck"
    elif fault == "parent_target": raw["target"] = "/private/source/../deck"
    elif fault == "wrong_path": raw["files"][0]["path"] = "/private/another/svg_output/03.svg"
    elif fault == "duplicate_path": raw["files"][0] = copy.deepcopy(raw["files"][1])
    elif fault == "missing_page": raw["files"].pop()
    elif fault == "source_hash": raw["files"][0]["source_sha256"] = "0" * 64
    elif fault == "exists": raw["files"][0]["exists"] = False
    elif fault == "errors": raw["files"][0]["errors"] = ["not a warning"]
    elif fault == "warning_object": raw["files"][0]["warnings"] = [{"arbitrary": "not a known warning contract"}]
    elif fault == "summary_count": raw["summary"]["warnings"] += 1
    elif fault == "boolean_count": raw["summary"]["errors"] = False
    elif fault == "project_error": raw["project_issues"]["template"][0]["severity"] = "error"
    elif fault == "project_shape": raw["project_issues"]["template"] = "private arbitrary prose"
    else: raw["project_issues"]["unexpected"] = []
    retain_source_quality_report(bundle, raw)
    response = client.get(bundle["url"])
    assert response.status_code == 200, response.text
    snapshot = response.json()
    assert snapshot["programmatic"]["source_diagnostics"] is None
    assert snapshot["programmatic_status"] == "needs_changes"
    assert snapshot["programmatic"]["pending_gates"] == sorted((EXPORT_GATE, DELIVERY_GATE))
    assert snapshot["can_publish"] is snapshot["publication_authorized"] is False
    assert "/private/" not in response.text and "private.invalid" not in response.text


def test_pending_warning_report_does_not_claim_a_measured_source_failure(accepted_bundle):
    _, client, _, _, bundle = accepted_bundle
    raw = retain_source_quality_warnings(bundle)
    bundle["receipt"]["status"] = bundle["equivalence"]["status"] = "pending"
    retain_source_quality_report(bundle, raw)
    response = client.get(bundle["url"])
    assert response.status_code == 200, response.text
    assert response.json()["programmatic_status"] == "pending"
    assert response.json()["programmatic"]["source_diagnostics"] is None


def test_separate_bundle_preserves_old_binding_and_human_checks_remain_pending(accepted_bundle):
    app, client, _, data, bundle = accepted_bundle
    snapshot = client.get(bundle["url"]).json()
    assert snapshot["binding"] == bundle["binding"]
    assert snapshot["programmatic_status"] == "passed"
    assert snapshot["powerpoint_status"] == snapshot["visual_status"] == "pending"
    assert snapshot["acceptance_status"] == "pending" and snapshot["can_publish"] is False
    assert snapshot["programmatic"]["source_diagnostics"] is None
    assert len(snapshot["programmatic"]["rendered_pages"]) == 3
    page = snapshot["programmatic"]["rendered_pages"][0]
    response = client.get(page["preview_url"])
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store"
    assert hashlib.sha256(response.content).hexdigest() == page["preview_sha256"]
    assert (app.state.storage.root / data["evidence_key"] / "validated.json").is_file()


def test_partial_export_contract_keeps_final_render_visible_without_publication(ctx):
    app, client, owners = ctx
    data = seed_agent_review(app, owners[0])
    bundle = seed_acceptance_bundle(app, client, data, passed=False)
    snapshot = client.get(bundle["url"]).json()
    assert snapshot["programmatic_status"] == "pending" and snapshot["can_publish"] is False
    assert len(snapshot["programmatic"]["rendered_pages"]) == 3
    assert EXPORT_GATE in snapshot["pending_reasons"]


def test_full_evidence_and_human_reviews_need_separate_append_only_publication(accepted_bundle):
    app, client, _, data, bundle = accepted_bundle
    url = bundle["url"]
    snapshot = client.get(url).json()
    visual = {"kind": "visual_review", "binding": snapshot["binding"], "expected_revision": 0,
        "pages": [{"number": page["number"], "preview_sha256": page["preview_sha256"], "verdict": "pass", "issues": []}
                  for page in snapshot["pages"]], "note": "Synthetic test conclusion only"}
    response = client.post(url + "/records", json=visual, headers={"Idempotency-Key": "visual"})
    assert response.status_code == 200, response.text
    desktop = {"kind": "powerpoint_review", "binding": snapshot["binding"], "expected_revision": 1,
        "application_version": "Synthetic PowerPoint fixture", "platform": "No real application executed",
        "checks": {"opened": "pass", "content_edited": "pass", "native_chart_edited": "not_applicable",
                   "saved": "pass", "reopened": "pass"}, "note": "Contract test, not desktop acceptance evidence"}
    response = client.post(url + "/records", json=desktop, headers={"Idempotency-Key": "desktop"})
    assert response.status_code == 200, response.text
    snapshot = response.json()
    assert snapshot["can_publish"] is True and snapshot["publication_authorized"] is False
    publish = {"kind": "publish", "binding": snapshot["binding"], "expected_revision": 2, "confirm": True}
    response = client.post(url + "/publication", json=publish, headers={"Idempotency-Key": "publish"})
    assert response.status_code == 200, response.text
    assert response.json()["publication_authorized"] is True
    assert client.get(url).json()["publication"] == response.json()["publication"]
    with app.state.sessions() as session:
        # The publication journal never rewrites frozen source/evidence or
        # converts the review checkpoint into an executor baseline.
        assert session.get(Version, data["vid"]).status == "review"
        assert session.get(Checkpoint, data["cid"]).manifest["eligible_as_revision_base"] is False
        assert session.get(Project, data["pid"]).latest_version_id is None


@pytest.mark.parametrize("target", ["receipt.json", "request.json", "transport-manifest.json",
    "final-pptx/final-pptx-evidence.json", "final-pptx/pages/2.png", "postflight/postflight-evidence.json",
    "postflight/source/deck/validation/svg_quality_report.json", "postflight/official/resources.json",
    "equivalence/export-contract.json", "equivalence/rebuilt/presentation.pptx"])
def test_bundle_byte_mutations_fail_closed(accepted_bundle, target):
    _, client, _, _, bundle = accepted_bundle
    (bundle["root"] / target).write_bytes(b"tampered acceptance evidence")
    assert client.get(bundle["url"]).status_code == 503


@pytest.mark.parametrize("fault", ["unstopped", "wrong_nonce", "wrong_source", "warning", "wrong_renderer", "missing_gate"])
def test_resigned_roster_does_not_replace_known_report_semantics(accepted_bundle, fault):
    _, client, _, _, bundle = accepted_bundle
    if fault == "unstopped":
        bundle["transport"]["writers_stopped"] = False
    else:
        name = "postflight" if fault == "warning" else "equivalence" if fault == "missing_gate" else "final"
        report = copy.deepcopy(bundle[name])
        if fault == "wrong_nonce": report["controller_nonce"] = str(uuid4())
        elif fault == "wrong_source": report["pptx"]["sha256"] = "0" * 64
        elif fault == "wrong_renderer": report["renderer"]["runtime_image"] = "wrong@sha256:" + "0" * 64
        elif fault == "warning": report["quality"]["warnings_pending"] = True
        else: report["completed_programmatic_gates"] = []
        field, path = {"final": ("final_pptx_report", "final-pptx/final-pptx-evidence.json"),
            "postflight": ("postflight_report", "postflight/postflight-evidence.json"),
            "equivalence": ("equivalence_report", "equivalence/export-contract.json")}[name]
        row = bundle["put"](path, report)
        bundle["receipt"][field] = {key: row[key] for key in ("path", "sha256")}
        bundle["put"]("receipt.json", bundle["receipt"])
    bundle["freeze"]()
    assert client.get(bundle["url"]).status_code == 503


def test_newest_invalid_current_bundle_cannot_fall_back_to_old_pass(accepted_bundle):
    app, client, _, data, first = accepted_bundle
    second = seed_acceptance_bundle(app, client, data, issued="2026-11-01T11:00:00Z")
    second["transport"]["writers_stopped"] = False
    second["freeze"]()
    assert client.get(first["url"]).status_code == 503


@pytest.mark.parametrize("linked", ["symlink", "hardlink", "extra_file"])
def test_bundle_roster_rejects_links_and_unindexed_files(accepted_bundle, linked):
    _, client, _, _, bundle = accepted_bundle
    pages = bundle["root"] / "final-pptx/pages"
    if linked == "extra_file":
        (pages / "unindexed.png").write_bytes(b"not indexed by stopped transport")
    else:
        (pages / "2.png").unlink()
        if linked == "hardlink":
            os.link(pages / "1.png", pages / "2.png")
        else:
            (pages / "2.png").symlink_to(pages / "1.png")
    assert client.get(bundle["url"]).status_code == 503


def test_preview_owner_digest_and_source_checks_are_required(accepted_bundle):
    app, client, owners, data, bundle = accepted_bundle
    snapshot = client.get(bundle["url"]).json()
    url = snapshot["programmatic"]["rendered_pages"][0]["preview_url"]
    assert client.get(url.replace(snapshot["programmatic"]["receipt_sha256"], "0" * 64)).status_code == 409
    owners[0] = Identity("https://issuer", "other-owner")
    assert client.get(url).status_code == 404
    owners[0] = Identity("https://issuer", "alice")
    with app.state.sessions.begin() as session:
        project = session.get(Project, data["pid"])
        project.active_job_id = "synthetic-active-task"
    assert "active_job_exists" in client.get(bundle["url"]).json()["pending_reasons"]
    with app.state.sessions.begin() as session:
        session.get(Project, data["pid"]).revision += 1
    assert client.get(url).status_code in (404, 409)


def attach_text_layout(bundle, *, incomplete=False):
    """Synthetic new receipt without rewriting the fixture's original source."""
    final = bundle["final"]
    pdf = bundle["put"]("final-pptx/pdf/presentation.pdf", b"%PDF-1.5\nSynthetic contract-only PDF")
    final["pdf"] = dict(pdf, path="pdf/presentation.pdf")
    final["schema"] = "ppt-service.final-pptx-render.v2"
    coverage = {"native_text_shapes": 3, "matched_text_shapes": 2 if incomplete else 3,
        "checked_text_shapes": 2 if incomplete else 3, "rendered_glyphs": 30,
        "geometry_limited_shapes": 1 if incomplete else 0, "unmatched_text_shapes": 0,
        "collision_shape_pairs": 0, "collision_glyph_pairs": 0}
    layout = {"schema": "ppt-service.final-text-layout.v1", "status": "incomplete" if incomplete else "passed",
        "publication_authorized": False, "human_visual_acceptance": False, "powerpoint_acceptance": "pending",
        "method": "final-pdf-glyphs-and-native-pptx-frames", "engine": {"name": "PyMuPDF", "version": "synthetic"},
        "scope": "conservative_text_geometry_checks_only", "coverage": coverage,
        "pages": [{"ordinal": n, "native_text_shapes": 1,
                   "matched_text_shapes": 0 if incomplete and n == 1 else 1, "issue_count": 0} for n in range(1, 4)],
        "issues": [], "issue_count": 0, "issues_truncated": False,
        "advisories": [{"code": "rotated_text_not_checked", "page": 1, "shape_id": 19}] if incomplete else [],
        "binding": {"controller_nonce": final["controller_nonce"],
            "source_manifest_sha256": final["source_manifest_sha256"],
            "input_snapshot_sha256": final["input_snapshot_sha256"],
            "pptx_sha256": final["pptx"]["sha256"], "pdf_sha256": pdf["sha256"]}}
    return layout


def resign_text_layout(bundle, layout):
    row = bundle["put"]("final-pptx/text-layout.json", layout)
    bundle["final"]["text_layout"] = dict(row, path="text-layout.json", status=layout["status"],
        coverage=layout["coverage"], human_visual_acceptance=False)
    final_row = bundle["put"]("final-pptx/final-pptx-evidence.json", bundle["final"])
    bundle["receipt"]["final_pptx_report"] = {key: final_row[key] for key in ("path", "sha256")}
    bundle["put"]("receipt.json", bundle["receipt"])
    bundle["freeze"]()


@pytest.mark.parametrize("incomplete", [False, True])
def test_new_measured_text_receipt_keeps_actual_visual_and_desktop_acceptance_pending(accepted_bundle, incomplete):
    _, client, _, _, bundle = accepted_bundle
    layout = attach_text_layout(bundle, incomplete=incomplete)
    resign_text_layout(bundle, layout)
    response = client.get(bundle["url"])
    assert response.status_code == 200, response.text
    snapshot = response.json()
    assert snapshot["programmatic_status"] == "passed"
    assert snapshot["visual_status"] == snapshot["powerpoint_status"] == "pending"
    assert snapshot["can_publish"] is False and snapshot["publication_authorized"] is False


@pytest.mark.parametrize("fault", ["nonce", "pptx", "pdf", "source", "human", "finding",
    "failed", "coverage", "truncated", "missing", "path", "no_descriptor"])
def test_new_text_receipt_requires_exact_file_binding_and_safe_known_semantics_even_after_resigning(accepted_bundle, fault):
    _, client, _, _, bundle = accepted_bundle
    layout = attach_text_layout(bundle)
    if fault == "nonce": layout["binding"]["controller_nonce"] = str(uuid4())
    elif fault == "pptx": layout["binding"]["pptx_sha256"] = "0" * 64
    elif fault == "pdf": layout["binding"]["pdf_sha256"] = "0" * 64
    elif fault == "source": layout["binding"]["source_manifest_sha256"] = "0" * 64
    elif fault == "human": layout["human_visual_acceptance"] = True
    elif fault == "finding": layout["issues"] = [{"code": "text_collision", "page": 1, "shape_id": 1}]
    elif fault == "failed": layout["status"] = "failed"
    elif fault == "coverage": layout["coverage"]["checked_text_shapes"] = 0
    elif fault == "truncated": layout["issues_truncated"] = True
    resign_text_layout(bundle, layout)
    if fault == "missing":
        (bundle["root"] / "final-pptx/text-layout.json").unlink()
        bundle["freeze"]()
    elif fault in ("path", "no_descriptor"):
        if fault == "path": bundle["final"]["text_layout"]["path"] = "../text-layout.json"
        else: del bundle["final"]["text_layout"]
        row = bundle["put"]("final-pptx/final-pptx-evidence.json", bundle["final"])
        bundle["receipt"]["final_pptx_report"] = {key: row[key] for key in ("path", "sha256")}
        bundle["put"]("receipt.json", bundle["receipt"])
        bundle["freeze"]()
    assert client.get(bundle["url"]).status_code == 503


def test_text_diagnostic_bytes_are_covered_by_stopped_transport_roster(accepted_bundle):
    _, client, _, _, bundle = accepted_bundle
    resign_text_layout(bundle, attach_text_layout(bundle))
    (bundle["root"] / "final-pptx/text-layout.json").write_bytes(b"altered after stop")
    assert client.get(bundle["url"]).status_code == 503


def seed_layout_failure(app, data, bundle, *, issued="2026-11-03T10:00:00Z"):
    """Synthetic stopped failure, deliberately separate from success bundles."""
    request = json.loads((bundle["root"] / "request.json").read_text())
    root = bundle["root"].parent.parent / "acceptance-failures" / bundle["eid"]

    def put(name, value):
        raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return {"path": name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}

    request_row = put("request.json", request)
    pptx = put("final-pptx/input/presentation.pptx",
        (app.state.storage.root / data["artifact_key"] / "presentation.pptx").read_bytes())
    pdf = put("final-pptx/pdf/presentation.pdf", b"%PDF-1.5\nSynthetic failed-render contract fixture")
    source = bundle["final"]
    layout = {"schema": "ppt-service.final-text-layout.v1", "status": "failed",
        "binding": {"controller_nonce": source["controller_nonce"],
            "source_manifest_sha256": source["source_manifest_sha256"],
            "input_snapshot_sha256": source["input_snapshot_sha256"],
            "pptx_sha256": pptx["sha256"], "pdf_sha256": pdf["sha256"]},
        "publication_authorized": False, "human_visual_acceptance": False, "powerpoint_acceptance": "pending",
        "method": "final-pdf-glyphs-and-native-pptx-frames", "scope": "conservative_text_geometry_checks_only",
        "engine": {"name": "PyMuPDF", "version": "synthetic"},
        "issues": [{"code": "text_collision", "severity": "error", "page": 2, "shape_id": 17,
            "other_shape_id": 22, "message": "secret source text /private/path", "suggestion": "https://private.invalid"}],
        "issue_count": 1, "issues_truncated": False,
        "pages": [{"ordinal": n, "issue_count": int(n == 2)} for n in range(1, 4)]}
    row = put("final-pptx/text-layout.json", layout)
    error = {"schema": "ppt-service.acceptance-error.v1", "publication_authorized": False,
        "error_code": "final_pptx_text_layout_failed", "error_type": "ControlError"}
    put("acceptance-error.json", error)
    receipt = {"schema": "ppt-service.acceptance-failure.v1", "status": "failed",
        "error_code": "final_pptx_text_layout_failed", "evidence_id": request["evidence_id"],
        "request_sha256": request_row["sha256"], "binding_sha256": fingerprint(request["binding"]),
        "execution_image": request["execution_image"], "issued_at": issued,
        "accepted": False, "publication_authorized": False, "human_visual_acceptance": False,
        "powerpoint_acceptance": "pending", "pptx": pptx, "pdf": pdf, "text_layout_report": row}
    put("failure-receipt.json", receipt)
    transport = {"schema": "ppt-service.acceptance-failure-transport.v1", "evidence_id": request["evidence_id"],
        "request_sha256": request_row["sha256"], "binding_sha256": receipt["binding_sha256"],
        "execution_image": request["execution_image"], "validator_scope_uid": str(uuid4()),
        "validator_pod_uid": str(uuid4()), "validator_exit_code": 2, "writers_stopped": True, "transport_verified": True}

    def freeze():
        files = [{"path": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                  "bytes": path.stat().st_size} for path in sorted(root.rglob("*"))
                 if path.is_file() and path.name != "transport-manifest.json"]
        put("transport-manifest.json", transport | {"files": files, "bytes": sum(row["bytes"] for row in files)})

    freeze()
    return {"root": root, "put": put, "freeze": freeze, "request": request, "receipt": receipt,
            "transport": transport, "layout": layout, "error": error}


@pytest.mark.parametrize("existing_success", [False, True])
def test_measured_async_failure_is_visible_on_owner_acceptance_refresh_with_specific_safe_pages(accepted_bundle, existing_success):
    app, client, owners, data, bundle = accepted_bundle
    seed_layout_failure(app, data, bundle)
    if not existing_success:
        # Keep the synthetic bytes privately without installing a success
        # receipt: this is the real first failed asynchronous-check shape.
        bundle["root"].rename(bundle["root"].parent.parent / "private-success-fixture")
    for _ in range(2):
        response = client.get(bundle["url"])
        assert response.status_code == 200, response.text
        snapshot = response.json()
        assert snapshot["programmatic_status"] == "needs_changes"
        assert snapshot["visual_status"] == snapshot["powerpoint_status"] == "pending"
        assert snapshot["can_publish"] is snapshot["publication_authorized"] is False
        diagnostic = snapshot["programmatic"]["diagnostics"]
        assert diagnostic["error_code"] == "final_pptx_text_layout_failed"
        assert diagnostic["items"][0]["page"] == 2
        assert diagnostic["items"][0]["shape_id"] == 17
        assert diagnostic["items"][0]["other_shape_id"] == 22
        assert diagnostic["items"][0]["code"] == "text_collision"
        assert diagnostic["items"][0]["message"] == "两个文字区域出现明显重叠。"
        assert "secret source" not in response.text and "/private/" not in response.text
        assert "private.invalid" not in response.text and "acceptance-failures" not in response.text
    with app.state.sessions() as session:
        assert session.get(Job, data["jid"]).status == "succeeded"
        assert session.get(Version, data["vid"]).status == "review"
        assert session.get(Project, data["pid"]).revision == 8
    owners[0] = Identity("https://issuer", "different-owner")
    assert client.get(bundle["url"]).status_code == 404


@pytest.mark.parametrize("fault", ["nonce", "pptx", "pdf", "page", "boolean_page", "shape", "boolean_shape",
    "other_self", "code", "stopped", "exit_zero", "wrong_image", "request", "accepted", "path", "count",
    "page_count", "wrong_page_count", "conflicting_success", "tampered_bytes", "missing"])
def test_failure_projection_rejects_bad_bindings_stop_semantics_and_unbounded_items(accepted_bundle, fault):
    app, client, _, data, bundle = accepted_bundle
    failure = seed_layout_failure(app, data, bundle)
    layout, receipt, transport = failure["layout"], failure["receipt"], failure["transport"]
    if fault == "nonce": layout["binding"]["controller_nonce"] = str(uuid4())
    elif fault == "pptx": layout["binding"]["pptx_sha256"] = "0" * 64
    elif fault == "pdf": layout["binding"]["pdf_sha256"] = "0" * 64
    elif fault == "page": layout["issues"][0]["page"] = 4
    elif fault == "boolean_page": layout["issues"][0]["page"] = True
    elif fault == "shape": layout["issues"][0]["shape_id"] = "/private/path"
    elif fault == "boolean_shape": layout["issues"][0]["shape_id"] = True
    elif fault == "other_self": layout["issues"][0]["other_shape_id"] = layout["issues"][0]["shape_id"]
    elif fault == "code": layout["issues"][0]["code"] = "candidate_controlled_rule"
    elif fault == "stopped": transport["writers_stopped"] = False
    elif fault == "exit_zero": transport["validator_exit_code"] = 0
    elif fault == "wrong_image": transport["execution_image"] = "other@sha256:" + "0" * 64
    elif fault == "request": receipt["request_sha256"] = "0" * 64
    elif fault == "accepted": receipt["accepted"] = True
    elif fault == "count": layout["issue_count"] = 1000001
    elif fault == "page_count": layout["pages"][1]["issue_count"] = 0
    elif fault == "wrong_page_count":
        layout["pages"][0]["issue_count"], layout["pages"][1]["issue_count"] = 1, 0
    elif fault == "conflicting_success": failure["put"]("receipt.json", {"status": "passed"})
    row = failure["put"]("final-pptx/text-layout.json", layout)
    receipt["text_layout_report"] = row
    if fault == "path": receipt["text_layout_report"]["path"] = "../text-layout.json"
    failure["put"]("failure-receipt.json", receipt)
    failure["freeze"]()
    if fault == "tampered_bytes":
        (failure["root"] / "final-pptx/text-layout.json").write_bytes(b"changed after stop")
    elif fault == "missing":
        (failure["root"] / "final-pptx/text-layout.json").unlink()
    assert client.get(bundle["url"]).status_code == 503


def test_later_valid_acceptance_supersedes_older_failure_without_mutating_either(accepted_bundle):
    app, client, _, data, bundle = accepted_bundle
    failure = seed_layout_failure(app, data, bundle, issued="2026-11-01T09:00:00Z")
    original = (failure["root"] / "failure-receipt.json").read_bytes()
    response = client.get(bundle["url"])
    assert response.status_code == 200, response.text
    assert response.json()["programmatic_status"] == "passed"
    assert response.json()["programmatic"]["diagnostics"] is None
    assert (failure["root"] / "failure-receipt.json").read_bytes() == original
