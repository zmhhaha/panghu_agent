"""Read separately issued immutable acceptance bundles without running Office.

Only the trusted operator can install these bundles after the exact validator
Pod has stopped. Actor workspaces and original validator receipts are retained
unchanged. The API checks the complete byte roster and known report contracts;
it never accepts a certificate or filesystem path through an HTTP command.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import os
import re
import stat
from uuid import UUID

from .contracts import SourceBinding, LayoutDiagnosticItem, LayoutFailureDiagnostic
from .domain import AcceptanceError, ProgrammaticContext, RenderedPage, REQUIRED_PROGRAMMATIC_GATES

SHA = re.compile(r"[a-f0-9]{64}\Z")
IMAGE = re.compile(r"[^@\s]+@sha256:[a-f0-9]{64}\Z")
BASE_GATES = frozenset(("independent_raster_regeneration_and_binding",
    "independent_delivery_audit", "independent_native_ooxml_and_chart_data"))
SOURCE_GATE = "independent_postflight_source_and_quality"
FINAL_GATE = "independent_final_pptx_rendering"
EXPORT_GATE = "independent_export_build_contract"
DELIVERY_GATE = "independent_postflight_and_delivery"
PPT_MASTER = "c350bc31e6d43b7bcf5234183d650ba8f9f4c9d7"
LAYOUT_FAILURE = "final_pptx_text_layout_failed"
_LAYOUT_MESSAGES = {
    "short_token_unplanned_wrap": ("短词被拆成了多行。", "加宽文字框或调整字距，保持短词完整。"),
    "short_token_orphan_fragment": ("短词末尾出现了单独一行的字母。", "合并被拆开的短词，并为文字框留出足够宽度。"),
    "short_label_orphan_line": ("短标签末尾有一个字单独换行。", "加宽文字框，或重新安排标签的行分布。"),
    "text_outside_frame": ("文字超出了对应文本框。", "调整文字框的宽高、内边距或字号。"),
    "text_outside_slide": ("文字超出了页面边界。", "移动或缩小文字区域，使内容完整位于页面内。"),
    "text_collision": ("两个文字区域出现明显重叠。", "拉开两个文字区域，检查标题与正文之间的间距。"),
}


def require(value):
    if not value:
        raise AcceptanceError("acceptance_evidence_unavailable", 503)


def _uuid(value):
    return isinstance(value, str) and str(UUID(value)) == value


def layout_failure_diagnostic(layout, binding, nonce, pdf_sha256, pages):
    """Project only known rules and bounded integers from a measured failure.

    Callers separately prove exact stopped transport and all retained bytes.
    Arbitrary report messages, excerpts, names, paths and URLs never cross this
    projection even if a trusted report happens to contain them.
    """
    expected = {"controller_nonce": nonce, "source_manifest_sha256": binding.manifest_sha256,
        "input_snapshot_sha256": binding.input_snapshot_sha256, "pptx_sha256": binding.pptx_sha256,
        "pdf_sha256": pdf_sha256}
    require(isinstance(layout, dict) and layout.get("schema") == "ppt-service.final-text-layout.v1"
        and layout.get("status") == "failed" and layout.get("binding") == expected
        and layout.get("publication_authorized") is False and layout.get("human_visual_acceptance") is False
        and layout.get("powerpoint_acceptance") == "pending"
        and layout.get("method") == "final-pdf-glyphs-and-native-pptx-frames"
        and layout.get("scope") == "conservative_text_geometry_checks_only"
        and isinstance(layout.get("engine"), dict) and layout["engine"].get("name") == "PyMuPDF"
        and isinstance(layout["engine"].get("version"), str) and 0 < len(layout["engine"]["version"]) <= 100)
    rows, count = layout.get("issues"), layout.get("issue_count")
    require(isinstance(rows, list) and 1 <= len(rows) <= 100
        and type(count) is int and len(rows) <= count <= 1000000
        and type(layout.get("issues_truncated")) is bool and layout["issues_truncated"] == (count > len(rows)))
    page_rows = layout.get("pages")
    require(isinstance(page_rows, list) and len(page_rows) == pages)
    page_counts = {}
    for number, row in enumerate(page_rows, 1):
        require(isinstance(row, dict) and type(row.get("ordinal")) is int and row["ordinal"] == number
            and type(row.get("issue_count")) is int and 0 <= row["issue_count"] <= count)
        page_counts[number] = row["issue_count"]
    require(sum(page_counts.values()) == count)
    items = []
    shown_counts = {page: 0 for page in page_counts}
    for row in rows:
        require(isinstance(row, dict) and row.get("code") in _LAYOUT_MESSAGES
            and row.get("severity") == "error" and type(row.get("page")) is int and 1 <= row["page"] <= pages
            and page_counts[row["page"]] > 0 and type(row.get("shape_id")) is int and 1 <= row["shape_id"] <= 2147483647)
        other = row.get("other_shape_id")
        require(other is None or (type(other) is int and 1 <= other <= 2147483647 and other != row["shape_id"]))
        message, suggestion = _LAYOUT_MESSAGES[row["code"]]
        shown_counts[row["page"]] += 1
        items.append(LayoutDiagnosticItem(page=row["page"], code=row["code"], shape_id=row["shape_id"],
            other_shape_id=other, message=message, suggestion=suggestion))
    require(all(shown_counts[page] <= page_counts[page] for page in page_counts)
        and (layout["issues_truncated"] or shown_counts == page_counts))
    return LayoutFailureDiagnostic(error_code=LAYOUT_FAILURE, title="PPTX 文字排版需要修复",
        message="最终 PPTX 的实际文字检查发现以下页面存在排版问题。",
        suggestion="请在修改意见中说明这些页面需要调整的内容；原资料、方案与审核稿仍保留。",
        items=items, total_issue_count=count, truncated=count > len(rows))


def _gates(value):
    require(isinstance(value, list) and len(value) == len(set(value))
        and all(isinstance(item, str) and re.fullmatch(r"[a-z0-9_]{1,100}", item) for item in value))
    return set(value)


def _bound(report, request, context):
    require(report.get("controller_nonce") == request["validation_nonce"]
        and report.get("source_manifest_sha256") == context.binding.manifest_sha256
        and report.get("input_snapshot_sha256") == context.binding.input_snapshot_sha256)


def _report(reader, prefix, receipt, name, expected):
    descriptor = receipt[name]
    require(isinstance(descriptor, dict) and set(descriptor) == {"path", "sha256"}
        and descriptor["path"] == expected and SHA.fullmatch(descriptor["sha256"]))
    report, digest = reader._json(prefix + "/" + expected)
    require(digest == descriptor["sha256"])
    return report


def _text_layout(reader, prefix, final, request, context, pptx):
    """New Office receipts bind measured text evidence; old v1 stays immutable.

    Incomplete heuristic coverage is explicitly allowed as a limit of the
    rendering check. It never completes the separate human visual gate.
    """
    if final["schema"] == "ppt-service.final-pptx-render.v1" and "text_layout" not in final:
        return
    descriptor = final.get("text_layout")
    require(isinstance(descriptor, dict)
        and set(descriptor) == {"path", "sha256", "bytes", "status", "coverage", "human_visual_acceptance"}
        and descriptor.get("path") == "text-layout.json"
        and descriptor.get("status") in ("passed", "incomplete")
        and descriptor.get("human_visual_acceptance") is False
        and isinstance(descriptor.get("sha256"), str) and SHA.fullmatch(descriptor["sha256"])
        and type(descriptor.get("bytes")) is int and 0 < descriptor["bytes"] <= 8 * 1024**2)
    key = prefix + "/final-pptx/text-layout.json"
    layout, digest = reader._json(key)
    require(digest == descriptor["sha256"]
        and reader._hash(key) == {name: descriptor[name] for name in ("sha256", "bytes")})
    pdf = final.get("pdf")
    require(isinstance(pdf, dict) and set(pdf) == {"path", "sha256", "bytes"}
        and pdf.get("path") == "pdf/presentation.pdf"
        and isinstance(pdf.get("sha256"), str) and SHA.fullmatch(pdf["sha256"])
        and type(pdf.get("bytes")) is int and 0 < pdf["bytes"] <= 64 * 1024**2
        and reader._hash(prefix + "/final-pptx/pdf/presentation.pdf")
            == {name: pdf[name] for name in ("sha256", "bytes")})
    expected_binding = {"controller_nonce": request["validation_nonce"],
        "source_manifest_sha256": context.binding.manifest_sha256,
        "input_snapshot_sha256": context.binding.input_snapshot_sha256,
        "pptx_sha256": pptx["sha256"], "pdf_sha256": pdf["sha256"]}
    require(layout.get("schema") == "ppt-service.final-text-layout.v1"
        and layout.get("binding") == expected_binding
        and layout.get("status") == descriptor["status"]
        and layout.get("publication_authorized") is False
        and layout.get("human_visual_acceptance") is False
        and layout.get("powerpoint_acceptance") == "pending"
        and layout.get("method") == "final-pdf-glyphs-and-native-pptx-frames"
        and layout.get("scope") == "conservative_text_geometry_checks_only"
        and isinstance(layout.get("engine"), dict)
        and layout.get("engine", {}).get("name") == "PyMuPDF"
        and isinstance(layout.get("engine", {}).get("version"), str)
        and 0 < len(layout["engine"]["version"]) <= 100
        and layout.get("issues") == [] and type(layout.get("issue_count")) is int
        and layout["issue_count"] == 0 and layout.get("issues_truncated") is False)
    coverage = layout.get("coverage")
    keys = {"native_text_shapes", "matched_text_shapes", "checked_text_shapes", "rendered_glyphs",
        "geometry_limited_shapes", "unmatched_text_shapes", "collision_shape_pairs", "collision_glyph_pairs"}
    require(isinstance(coverage, dict) and set(coverage) == keys
        and coverage == descriptor["coverage"]
        and all(type(value) is int and 0 <= value <= 4_000_020 for value in coverage.values())
        and coverage["native_text_shapes"] == coverage["checked_text_shapes"]
            + coverage["geometry_limited_shapes"] + coverage["unmatched_text_shapes"]
        and coverage["checked_text_shapes"] == coverage["matched_text_shapes"])
    advisories, pages = layout.get("advisories"), layout.get("pages")
    require(isinstance(advisories, list) and len(advisories) <= 40000
        and (layout["status"] == "incomplete") == bool(advisories)
        and isinstance(pages, list) and len(pages) == len(context.pages))
    for ordinal, page in enumerate(pages, 1):
        require(isinstance(page, dict) and set(page) == {"ordinal", "native_text_shapes", "matched_text_shapes", "issue_count"}
            and type(page.get("ordinal")) is int and page["ordinal"] == ordinal
            and type(page.get("issue_count")) is int and page["issue_count"] == 0
            and type(page.get("native_text_shapes")) is int and 0 <= page["native_text_shapes"] <= 2000
            and type(page.get("matched_text_shapes")) is int and 0 <= page["matched_text_shapes"] <= page["native_text_shapes"])
    require(sum(row["native_text_shapes"] for row in pages) == coverage["native_text_shapes"]
        and sum(row["matched_text_shapes"] for row in pages) == coverage["matched_text_shapes"])


def _final(reader, prefix, report, request, context, pptx, fingerprint):
    _bound(report, request, context)
    require(report.get("schema") in ("ppt-service.final-pptx-render.v1", "ppt-service.final-pptx-render.v2")
        and report.get("status") == "passed" and report.get("publication_authorized") is False
        and report.get("human_visual_acceptance") is False
        and report.get("powerpoint_acceptance") == "pending"
        and report.get("full_exporter_postflight_verified") is False
        and report.get("source_unchanged") is True and report.get("command_groups_stopped") is True
        and report.get("validator_stop_proof_requires_controller") is True
        and type(report.get("export_exit_code")) is int and report["export_exit_code"] == 0
        and type(report.get("raster_exit_code")) is int and report["raster_exit_code"] == 0
        and report.get("pptx") == pptx and _gates(report.get("completed_programmatic_gates")) == {FINAL_GATE})
    _text_layout(reader, prefix, report, request, context, pptx)
    renderer = report["renderer"]
    require(isinstance(renderer, dict) and renderer.get("name") == "LibreOffice Impress"
        and isinstance(renderer.get("version"), str) and 0 < len(renderer["version"]) <= 2000
        and renderer.get("isolated_profile") is True and renderer.get("runtime_image") == request["execution_image"]
        and renderer.get("pipeline") == "PPTX → LibreOffice PDF → Poppler PNG")
    tools = renderer.get("tools")
    require(isinstance(tools, dict) and set(tools) == {"libreoffice", "pdfinfo", "pdftoppm", "fontconfig", "soffice_binary"})
    for tool in tools.values():
        require(isinstance(tool, dict) and isinstance(tool.get("sha256"), str) and SHA.fullmatch(tool["sha256"])
            and isinstance(tool.get("path"), str) and tool["path"].startswith("/")
            and isinstance(tool.get("resolved_path"), str) and tool["resolved_path"].startswith("/"))
    fonts = renderer["font_inventory"]
    require(isinstance(fonts, dict) and fonts.get("path") == "font-inventory.txt"
        and fonts.get("sha256") == renderer.get("font_inventory_sha256")
        and reader._hash(prefix + "/final-pptx/font-inventory.txt") == {key: fonts[key] for key in ("sha256", "bytes")})
    deck, rows = report["deck"], report["pages"]
    require(isinstance(deck, dict) and type(deck.get("slide_count")) is int and type(deck.get("chart_count")) is int
        and deck["slide_count"] == len(context.pages)
        and deck.get("chart_count") == context.native_chart_count
        and isinstance(rows, list) and len(rows) == len(context.pages)
        and report.get("page_roster_sha256") == fingerprint(rows))
    require(all(type(deck.get(key)) is int and 64 <= deck[key] <= 1280 for key in ("width", "height")))
    pages = []
    for number, row in enumerate(rows, 1):
        require(isinstance(row, dict) and set(row) == {"ordinal", "path", "sha256", "bytes", "width", "height"}
            and type(row["ordinal"]) is int and row["ordinal"] == number
            and row["path"] == f"pages/{number}.png" and isinstance(row["sha256"], str) and SHA.fullmatch(row["sha256"])
            and type(row["bytes"]) is int and 0 < row["bytes"] <= 8 * 1024**2
            and row["width"] == deck["width"] and row["height"] == deck["height"])
        key = prefix + "/final-pptx/" + row["path"]
        data = reader._read(key, 8 * 1024**2)
        require(len(data) == row["bytes"] and hashlib.sha256(data).hexdigest() == row["sha256"]
            and data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR"
            and int.from_bytes(data[16:20], "big") == row["width"]
            and int.from_bytes(data[20:24], "big") == row["height"])
        pages.append(RenderedPage(number=number, preview_sha256=row["sha256"], bytes=row["bytes"], storage_key=key))
    return tuple(pages), (renderer["name"] + " · " + renderer["version"].splitlines()[0])[:400]


def _postflight(reader, prefix, report, request, context):
    _bound(report, request, context)
    require(report.get("schema") == "ppt-service.independent-postflight.v1"
        and report.get("publication_authorization") is False
        and report.get("full_exporter_postflight_verified") is False
        and report.get("original_candidate_status") == "candidate")
    completed = _gates(report.get("completed_programmatic_gates"))
    require(completed.issubset({SOURCE_GATE}))
    if not completed:
        return False
    require(report.get("source_and_quality_verified") is True and report.get("source_unchanged") is True)
    source = report["source"]
    require(source.get("source_manifest_sha256") == context.binding.manifest_sha256
        and source.get("input_snapshot_sha256") == context.binding.input_snapshot_sha256
        and source.get("copied_source_fingerprint") == context.binding.source_fingerprint_sha256
        and source.get("pptx", {}).get("sha256") == context.binding.pptx_sha256)
    svg_rows = source["svg_sources"]
    require(isinstance(svg_rows, list) and len(svg_rows) == len(context.pages))
    files, aggregate = [], hashlib.sha256()
    for ordinal, row in enumerate(svg_rows, 1):
        require(isinstance(row, dict) and row.get("ordinal") == ordinal
            and row.get("sha256") == context.pages[ordinal - 1].source_sha256
            and isinstance(row.get("path"), str) and row["path"].startswith("svg_output/")
            and len(row["path"].split("/")) == 2)
    for row in sorted(svg_rows, key=lambda item: item["path"].rsplit("/", 1)[-1]):
        name = row["path"].rsplit("/", 1)[-1]
        files.append({"file": name, "sha256": row["sha256"]})
        aggregate.update(name.encode("utf-8") + b"\0" + row["sha256"].encode("ascii") + b"\n")
    require(len({row["file"] for row in files}) == len(files))
    expected_svg_fingerprint = {"algorithm": "sha256", "digest": aggregate.hexdigest(), "file_count": len(files), "files": files}
    quality, resources = report["quality"], report["resources"]
    require(isinstance(quality, dict) and isinstance(resources, dict)
        and type(quality.get("warnings_pending")) is bool and type(resources.get("warnings_pending")) is bool)
    project_root = source.get("project_root")
    require(isinstance(project_root, str) and bool(project_root))
    for label, path in (("quality", f"postflight/source/{project_root}/validation/svg_quality_report.json"),
                        ("resources", "postflight/official/resources.json")):
        raw, digest = reader._json(prefix + "/" + path)
        measured = reader._hash(prefix + "/" + path)
        declared = report["reports"][label]
        require(isinstance(declared, dict) and declared.get("sha256") == digest
            and all(declared.get(key) == measured[key] for key in ("sha256", "bytes")))
        if label == "resources":
            require(resources.get("audit") == raw)
        else:
            require(raw.get("schema") == "ppt-master.svg-quality-report.v1" and raw.get("stage") == "final"
                and raw.get("source_fingerprint") == quality.get("source_fingerprint") == expected_svg_fingerprint
                and raw.get("summary") == quality.get("summary"))
            raw_quality = raw
    reused = report["reused_programmatic_evidence"]
    delivery, native = reused["delivery"], reused["native"]
    require(type(native.get("slide_count")) is int and type(native.get("chart_count")) is int
        and native.get("slide_count") == len(context.pages) and native.get("chart_count") == context.native_chart_count
        and (not context.native_chart_count or native.get("chart_data_verified") is True))
    clean = quality["warnings_pending"] is False and resources["warnings_pending"] is False
    if clean:
        summary, counts = quality["summary"], quality["category_counts"]
        require(isinstance(summary, dict) and summary.get("total") == summary.get("passed") == len(context.pages)
            and type(summary.get("errors")) is int and summary["errors"] == 0
            and type(summary.get("warnings")) is int and summary["warnings"] == 0
            and isinstance(counts, dict) and counts and all(type(value) is int and value == 0 for value in counts.values()))
        require(isinstance(raw_quality.get("files"), list) and len(raw_quality["files"]) == len(context.pages)
            and all(isinstance(row, dict) and row.get("errors") == [] and row.get("warnings") == [] for row in raw_quality["files"])
            and isinstance(raw_quality.get("project_issues"), dict)
            and all(value == [] for value in raw_quality["project_issues"].values())
            and isinstance(raw_quality.get("categories"), dict)
            and all(raw_quality["categories"].get(name, {}).get("count") == count for name, count in counts.items()))
        audit = resources["audit"]
        require(audit.get("unresolved_template_tokens") == []
            and audit.get("fonts", {}).get("generic_only_stacks") == []
            and audit.get("fonts", {}).get("unsafe_exported_faces") == []
            and audit.get("images", {}).get("external") == 0
            and audit.get("images", {}).get("external_references") == [])
    clean = clean and delivery.get("status") == "passed" and delivery.get("advisories") == [] and type(delivery.get("advisory_count")) is int and delivery["advisory_count"] == 0
    return clean


def _equivalence(reader, prefix, report, request, context, postflight_clean, fingerprint):
    _bound(report, request, context)
    require(report.get("schema") == "ppt-service.export-build-equivalence.v1"
        and report.get("publication_authorized") is False
        and report.get("original_export_provenance_verified") is False
        and report.get("full_exporter_postflight_verified") is False)
    gates = _gates(report.get("completed_programmatic_gates"))
    require(gates.issubset({EXPORT_GATE}))
    if report.get("status") != "passed":
        require(not gates and report.get("status") in ("pending", "needs_changes"))
        return set()
    require(gates == {EXPORT_GATE}
        and report.get("export_build_equivalence_verified") is True
        and report.get("pptx", {}).get("sha256") == context.binding.pptx_sha256
        and report.get("source_fingerprint_sha256") == context.binding.source_fingerprint_sha256)
    comparison = report["comparison"]
    rows = comparison["parts"]
    require(isinstance(rows, list) and 0 < len(rows) <= 8192 and comparison.get("part_count") == len(rows)
        and comparison.get("semantic_sha256") == fingerprint(rows)
        and comparison.get("zip_container_metadata_ignored") is True
        and comparison.get("original_export_provenance_verified") is False
        and isinstance(comparison.get("normalizations"), list))
    require(len({row["path"] for row in rows}) == len(rows))
    for row in rows:
        require(isinstance(row, dict) and set(row) == {"path", "kind", "semantic_sha256"}
            and row["kind"] in ("xml", "binary", "embedded-workbook")
            and isinstance(row["semantic_sha256"], str) and SHA.fullmatch(row["semantic_sha256"]))
    rebuilt = report["rebuilt_pptx"]
    require(isinstance(rebuilt, dict) and isinstance(rebuilt.get("path"), str)
        and rebuilt["path"].startswith("equivalence/") and rebuilt["path"].endswith(".pptx")
        and reader._hash(prefix + "/" + rebuilt["path"]) == {key: rebuilt[key] for key in ("sha256", "bytes")})
    official = report["official"]
    require(official.get("ppt_master_commit") == PPT_MASTER and isinstance(official.get("script_sha256"), dict)
        and official["script_sha256"] and all(isinstance(value, str) and SHA.fullmatch(value) for value in official["script_sha256"].values())
        and isinstance(official.get("exit_codes"), dict) and official["exit_codes"].get("export") == 0
        and all(type(value) is int and value == 0 for value in official["exit_codes"].values()))
    return gates | ({DELIVERY_GATE} if postflight_clean else set())


def read_acceptance_evidence(reader, context, job, version, fingerprint):
    """Discover <=16 atomically installed UUID bundles, then verify the latest.

    There is no mutable latest pointer. A malformed current bundle fails closed;
    it cannot make the reader fall back to an older passing certificate.
    """
    prefix = f"projects/{context.binding.project_id}/versions/{context.binding.version_id}/acceptance-evidence"
    folder = reader.storage.root
    for part in prefix.split("/"):
        folder /= part
        try:
            info = folder.lstat()
        except FileNotFoundError:
            return None
        require(stat.S_ISDIR(info.st_mode) and not folder.is_symlink() and not folder.is_junction())
    bundles = list(os.scandir(folder))
    require(len(bundles) <= 16)
    candidates = []
    for entry in bundles:
        require(not entry.is_symlink() and entry.is_dir(follow_symlinks=False) and _uuid(entry.name))
        key = prefix + "/" + entry.name
        request, request_hash = reader._json(key + "/request.json")
        require(request.get("schema") == "ppt-service.acceptance-request.v1"
            and request.get("evidence_id") == request.get("validation_nonce") == entry.name
            and isinstance(request.get("execution_image"), str) and IMAGE.fullmatch(request["execution_image"]))
        binding = SourceBinding.model_validate(request["binding"])
        require(binding.project_id == context.binding.project_id and binding.version_id == context.binding.version_id)
        if binding != context.binding:
            continue
        require(request.get("job_id") == job.id and type(request.get("attempt")) is int and request["attempt"] == job.attempt
            and type(request.get("fencing_token")) is int and request["fencing_token"] == job.fencing_token)
        receipt, receipt_hash = reader._json(key + "/receipt.json")
        require(receipt.get("schema") == "ppt-service.acceptance-evidence.v1"
            and receipt.get("evidence_id") == entry.name and receipt.get("request_sha256") == request_hash
            and receipt.get("binding_sha256") == fingerprint(binding.model_dump(mode="json"))
            and receipt.get("execution_image") == request["execution_image"]
            and receipt.get("publication_authorized") is False and receipt.get("human_visual_acceptance") is False
            and receipt.get("powerpoint_acceptance") == "pending")
        issued = receipt["issued_at"]
        require(isinstance(issued, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", issued))
        datetime.strptime(issued, "%Y-%m-%dT%H:%M:%SZ")
        candidates.append((issued, entry.name, key, request, request_hash, receipt, receipt_hash))
    if not candidates:
        return None
    issued, evidence_id, key, request, request_hash, receipt, receipt_hash = max(candidates, key=lambda row: row[:2])
    transport, _ = reader._json(key + "/transport-manifest.json")
    require(transport.get("schema") == "ppt-service.acceptance-transport.v1"
        and transport.get("evidence_id") == request["evidence_id"]
        and transport.get("request_sha256") == request_hash
        and transport.get("binding_sha256") == receipt["binding_sha256"]
        and transport.get("execution_image") == request["execution_image"]
        and transport.get("writers_stopped") is True and transport.get("transport_verified") is True
        and type(transport.get("validator_exit_code")) is int and transport["validator_exit_code"] == 0
        and _uuid(transport.get("validator_scope_uid")) and _uuid(transport.get("validator_pod_uid"))
        and transport["validator_scope_uid"] != transport["validator_pod_uid"])
    retained = reader._tree(key, transport["files"], exclude=("transport-manifest.json",))
    require(type(transport.get("bytes")) is int and sum(row["bytes"] for row in retained) == transport["bytes"])
    final = _report(reader, key, receipt, "final_pptx_report", "final-pptx/final-pptx-evidence.json")
    postflight = _report(reader, key, receipt, "postflight_report", "postflight/postflight-evidence.json")
    equivalence = _report(reader, key, receipt, "equivalence_report", "equivalence/export-contract.json")
    pptx = reader._hash(version.manifest["pptx_key"])
    pages, renderer = _final(reader, key, final, request, context, pptx, fingerprint)
    clean = _postflight(reader, key, postflight, request, context)
    verified = set(BASE_GATES) | {FINAL_GATE}
    if clean:
        verified.add(SOURCE_GATE)
    verified |= _equivalence(reader, key, equivalence, request, context, clean, fingerprint)
    completed, pending = _gates(receipt.get("completed_programmatic_gates")), _gates(receipt.get("required_pending_gates"))
    require(completed == verified and pending == REQUIRED_PROGRAMMATIC_GATES - completed)
    status = receipt.get("status")
    require(status in ("passed", "pending", "needs_changes") and (status == "passed") == (not pending))
    detail = ("最终 PPTX 已独立渲染，源文件、导出内容及交付检查均通过；仍需完成实际 PowerPoint 操作验收。"
              if status == "passed" else "最终 PPTX 已独立渲染；完整导出与交付验收仍有待完成项目。")
    return ProgrammaticContext(binding=context.binding, receipt_sha256=receipt_hash,
        completed_gates=tuple(sorted(completed)), pending_gates=tuple(sorted(pending)),
        renderer=renderer, status=status, detail=detail, rendered_pages=pages,
        issued_key=(issued, evidence_id))


def read_acceptance_failure(reader, context, job, version, fingerprint):
    """Read a controller-installed failure; this can only keep gates pending.

    Failed outputs have a distinct directory, receipt and nonzero stopped
    transport. They never masquerade as a passing acceptance certificate.
    """
    prefix = f"projects/{context.binding.project_id}/versions/{context.binding.version_id}/acceptance-failures"
    folder = reader.storage.root
    for part in prefix.split("/"):
        folder /= part
        try:
            info = folder.lstat()
        except FileNotFoundError:
            return None
        require(stat.S_ISDIR(info.st_mode) and not folder.is_symlink() and not folder.is_junction())
    entries = list(os.scandir(folder))
    require(len(entries) <= 16)
    candidates = []
    for entry in entries:
        require(not entry.is_symlink() and entry.is_dir(follow_symlinks=False) and _uuid(entry.name))
        key = prefix + "/" + entry.name
        request, request_hash = reader._json(key + "/request.json")
        require(request.get("schema") == "ppt-service.acceptance-request.v1"
            and request.get("evidence_id") == request.get("validation_nonce") == entry.name
            and isinstance(request.get("execution_image"), str) and IMAGE.fullmatch(request["execution_image"]))
        binding = SourceBinding.model_validate(request["binding"])
        require(binding.project_id == context.binding.project_id and binding.version_id == context.binding.version_id)
        if binding != context.binding:
            continue
        require(request.get("job_id") == job.id
            and type(request.get("attempt")) is int and request["attempt"] == job.attempt
            and type(request.get("fencing_token")) is int and request["fencing_token"] == job.fencing_token)
        receipt, receipt_hash = reader._json(key + "/failure-receipt.json")
        require(receipt.get("schema") == "ppt-service.acceptance-failure.v1"
            and receipt.get("status") == "failed" and receipt.get("error_code") == LAYOUT_FAILURE
            and receipt.get("evidence_id") == entry.name and receipt.get("request_sha256") == request_hash
            and receipt.get("binding_sha256") == fingerprint(binding.model_dump(mode="json"))
            and receipt.get("execution_image") == request["execution_image"]
            and receipt.get("publication_authorized") is False and receipt.get("accepted") is False
            and receipt.get("human_visual_acceptance") is False and receipt.get("powerpoint_acceptance") == "pending")
        issued = receipt.get("issued_at")
        require(isinstance(issued, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", issued))
        datetime.strptime(issued, "%Y-%m-%dT%H:%M:%SZ")
        candidates.append((issued, entry.name, key, request, request_hash, receipt, receipt_hash))
    if not candidates:
        return None
    issued, evidence_id, key, request, request_hash, receipt, receipt_hash = max(candidates, key=lambda row: row[:2])
    transport, _ = reader._json(key + "/transport-manifest.json")
    require(transport.get("schema") == "ppt-service.acceptance-failure-transport.v1"
        and transport.get("evidence_id") == evidence_id and transport.get("request_sha256") == request_hash
        and transport.get("binding_sha256") == receipt["binding_sha256"]
        and transport.get("execution_image") == request["execution_image"]
        and transport.get("writers_stopped") is True and transport.get("transport_verified") is True
        and type(transport.get("validator_exit_code")) is int and transport["validator_exit_code"] != 0
        and _uuid(transport.get("validator_scope_uid")) and _uuid(transport.get("validator_pod_uid"))
        and transport["validator_scope_uid"] != transport["validator_pod_uid"])
    roster = reader._tree(key, transport["files"], exclude=("transport-manifest.json",))
    require(type(transport.get("bytes")) is int and sum(row["bytes"] for row in roster) == transport["bytes"])
    require(not {"receipt.json", "final-pptx/final-pptx-evidence.json"}.intersection(row["path"] for row in roster))
    error, _ = reader._json(key + "/acceptance-error.json")
    require(error.get("schema") == "ppt-service.acceptance-error.v1"
        and error.get("publication_authorized") is False and error.get("error_code") == LAYOUT_FAILURE)
    measured = {}
    for name, expected in (("pptx", "final-pptx/input/presentation.pptx"), ("pdf", "final-pptx/pdf/presentation.pdf"),
                           ("text_layout_report", "final-pptx/text-layout.json")):
        row = receipt.get(name)
        require(isinstance(row, dict) and set(row) == {"path", "sha256", "bytes"}
            and row.get("path") == expected and isinstance(row.get("sha256"), str) and SHA.fullmatch(row["sha256"])
            and type(row.get("bytes")) is int and 0 < row["bytes"] <= 64 * 1024**2
            and reader._hash(key + "/" + expected) == {field: row[field] for field in ("sha256", "bytes")})
        measured[name] = row
    current_pptx = reader._hash(version.manifest["pptx_key"])
    require(measured["pptx"]["sha256"] == context.binding.pptx_sha256 == current_pptx["sha256"]
        and measured["pptx"]["bytes"] == current_pptx["bytes"])
    layout, digest = reader._json(key + "/final-pptx/text-layout.json")
    require(digest == measured["text_layout_report"]["sha256"])
    diagnostic = layout_failure_diagnostic(layout, context.binding, request["validation_nonce"],
        measured["pdf"]["sha256"], len(context.pages))
    return ProgrammaticContext(binding=context.binding, receipt_sha256=receipt_hash,
        completed_gates=(), pending_gates=tuple(sorted(REQUIRED_PROGRAMMATIC_GATES)),
        renderer="LibreOffice Impress", status="needs_changes",
        detail="最终 PPTX 的文字排版检查未通过，以下页项需要修复；原审核稿仍可查看。",
        diagnostics=diagnostic, issued_key=(issued, evidence_id))
