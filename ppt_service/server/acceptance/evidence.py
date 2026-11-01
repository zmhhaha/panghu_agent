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

from .contracts import SourceBinding
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


def require(value):
    if not value:
        raise AcceptanceError("acceptance_evidence_unavailable", 503)


def _uuid(value):
    return isinstance(value, str) and str(UUID(value)) == value


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


def _final(reader, prefix, report, request, context, pptx, fingerprint):
    _bound(report, request, context)
    require(report.get("schema") == "ppt-service.final-pptx-render.v1"
        and report.get("status") == "passed" and report.get("publication_authorized") is False
        and report.get("human_visual_acceptance") is False
        and report.get("powerpoint_acceptance") == "pending"
        and report.get("full_exporter_postflight_verified") is False
        and report.get("source_unchanged") is True and report.get("command_groups_stopped") is True
        and report.get("validator_stop_proof_requires_controller") is True
        and type(report.get("export_exit_code")) is int and report["export_exit_code"] == 0
        and type(report.get("raster_exit_code")) is int and report["raster_exit_code"] == 0
        and report.get("pptx") == pptx and _gates(report.get("completed_programmatic_gates")) == {FINAL_GATE})
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
    _, _, key, request, request_hash, receipt, receipt_hash = max(candidates, key=lambda row: row[:2])
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
        renderer=renderer, status=status, detail=detail, rendered_pages=pages)
