"""Exact-content equivalence to a fresh, pinned native exporter build.

ZIP metadata and core-property creation/modification times do not affect the
presentation. All other XML, relationships, text, shapes, notes, resources and
embedded workbook content must match the independent build. This module never
asserts that the original Actor followed a particular export procedure.
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path
import re
import stat
import uuid
import xml.etree.ElementTree as ET
import zipfile

from .execution import ControlError, PPT_MASTER_COMMIT
from .render_validation import (
    CACHE_ROOTS, OfficialCommands, PYTHON, SCRIPTS, fingerprint, hash_file, source_binding, unchanged,
)
from .staging import atomic_json, open_regular, read_json, relative

SCHEMA = "ppt-service.export-build-equivalence.v1"
EXPORT_GATE = "independent_export_build_contract"
POSTFLIGHT_GATE = "independent_postflight_and_delivery"
_DCTERMS = "{http://purl.org/dc/terms/}"
_CORE = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
_DATETIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z\Z")
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_P14 = "{http://schemas.microsoft.com/office/powerpoint/2010/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _require(value, code):
    if not value:
        raise ControlError(code)


def _canonical_xml(data, part, normalizations):
    _require(len(data) <= 16 * 1024**2 and b"<!DOCTYPE" not in data.upper()
             and b"<!ENTITY" not in data.upper(), "export_equivalence_unsafe_xml")
    try:
        text = data.decode("utf-8-sig")
        _require("\x00" not in text, "export_equivalence_unsafe_xml")
        root = ET.fromstring(text)
    except (UnicodeError, ET.ParseError):
        raise ControlError("export_equivalence_unsafe_xml") from None
    pending, count = [(root, 1)], 0
    while pending:
        node, depth = pending.pop()
        count += 1
        _require(count <= 200000 and depth <= 128, "export_equivalence_xml_limit")
        pending.extend((child, depth + 1) for child in node)
    if part == "docProps/core.xml":
        _require(root.tag == _CORE + "coreProperties", "export_equivalence_core_invalid")
        for tag in ("created", "modified"):
            matches = list(root.iter(_DCTERMS + tag))
            _require(len(matches) <= 1, "export_equivalence_core_invalid")
            for node in matches:
                _require(not list(node) and isinstance(node.text, str) and _DATETIME.fullmatch(node.text),
                         "export_equivalence_core_invalid")
                node.text = "2000-01-01T00:00:00Z"
                normalizations.append(part + ":" + tag)
    if re.fullmatch(r"ppt/(?:slides|slideLayouts|slideMasters)/[^/]+\.xml", part):
        # Pinned builder._reseed_p14_creation_id assigns a new uint32 to
        # cloned parts. This is creation metadata, never a shape reference.
        for ordinal, node in enumerate(root.findall(_P + "cSld/" + _P + "extLst/" + _P + "ext/" + _P14 + "creationId"), 1):
            _require(set(node.attrib) == {"val"} and node.get("val", "").isdigit()
                     and 0 < int(node.get("val")) <= 0xffffffff and not list(node),
                     "export_equivalence_metadata_invalid")
            node.set("val", "1")
            normalizations.append(f"{part}:p14.creationId[{ordinal}].val")
    # The pinned builder's placeholder and page-number field constructors
    # issue random a:fld GUIDs. Keep every other attribute, field type, text
    # and geometry. An invalid or duplicate field identifier is not waived.
    field_ids = set()
    for ordinal, node in enumerate(root.iter(_A + "fld"), 1):
        value = node.get("id")
        try:
            _require(isinstance(value, str) and value.startswith("{") and value.endswith("}"),
                     "export_equivalence_metadata_invalid")
            identifier = uuid.UUID(value[1:-1])
        except (ValueError, AttributeError):
            raise ControlError("export_equivalence_metadata_invalid") from None
        _require(identifier not in field_ids and isinstance(node.get("type"), str) and bool(node.get("type")),
                 "export_equivalence_metadata_invalid")
        field_ids.add(identifier)
        node.set("id", "{" + str(uuid.UUID(int=ordinal)).upper() + "}")
        normalizations.append(f"{part}:a.fld[{ordinal}].id")
    # Formatting whitespace between OOXML elements is not document text. Text
    # leaves, including empty/space-only a:t and xml:space values, are preserved.
    pending = [(root, False)]
    while pending:
        node, inherited_preserve = pending.pop()
        preserve = inherited_preserve or node.get("{http://www.w3.org/XML/1998/namespace}space") == "preserve"
        known_namespace = node.tag.startswith(("{http://schemas.openxmlformats.org/", "{http://purl.org/dc/"))
        if known_namespace and not preserve:
            if list(node) and node.text and not node.text.strip():
                node.text = None
            for child in node:
                if child.tail and not child.tail.strip():
                    child.tail = None
        pending.extend((child, preserve) for child in node)
    if part.endswith(".rels"):
        _require(root.tag == "{http://schemas.openxmlformats.org/package/2006/relationships}Relationships",
                 "export_equivalence_relationships_invalid")
        root[:] = sorted(root, key=lambda node: (node.get("Id", ""), node.get("Type", ""), node.get("Target", "")))
    elif part == "[Content_Types].xml":
        _require(root.tag == "{http://schemas.openxmlformats.org/package/2006/content-types}Types",
                 "export_equivalence_content_types_invalid")
        root[:] = sorted(root, key=lambda node: (node.tag, sorted(node.attrib.items())))
    return ET.canonicalize(ET.tostring(root, encoding="unicode"), rewrite_prefixes=True).encode("utf-8")


def semantic_package_index(data, *, max_bytes=256 * 1024**2, depth=0):
    """Measure a bounded OOXML package, recursively including embedded XLSX.

    This comparison helper is not the structural/active-content validator. The
    caller must independently inspect both PPTX packages before accepting it.
    """
    _require(depth <= 1 and isinstance(data, bytes) and len(data) <= 64 * 1024**2,
             "export_equivalence_package_limit")
    try:
        package = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ControlError("export_equivalence_package_invalid") from None
    rows, normalized, seen, total = [], [], set(), 0
    with package:
        entries = package.infolist()
        _require(len(entries) <= 8192, "export_equivalence_package_limit")
        for item in entries:
            key = item.orig_filename
            if item.is_dir():
                relative(key[:-1])
                continue
            relative(key)
            mode = stat.S_IFMT(item.external_attr >> 16)
            _require(mode in (0, stat.S_IFREG) and key.casefold() not in seen and not item.flag_bits & 1
                     and item.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
                     "export_equivalence_package_invalid")
            seen.add(key.casefold())
            total += item.file_size
            _require(item.file_size <= 64 * 1024**2 and total <= max_bytes
                     and item.file_size <= max(1, item.compress_size) * 200,
                     "export_equivalence_package_limit")
            try:
                payload = package.read(item)
            except (zipfile.BadZipFile, RuntimeError, OSError):
                raise ControlError("export_equivalence_package_invalid") from None
            _require(len(payload) == item.file_size, "export_equivalence_package_invalid")
            if key.lower().endswith(".xlsx"):
                child = semantic_package_index(payload, max_bytes=max_bytes - total, depth=depth + 1)
                total += child["expanded_bytes"]
                _require(total <= max_bytes, "export_equivalence_package_limit")
                canonical = fingerprint(child["parts"]).encode("ascii")
                normalized.extend(key + "!" + value for value in child["normalizations"])
                kind = "embedded-workbook"
            elif key.endswith((".xml", ".rels")):
                canonical = _canonical_xml(payload, key, normalized)
                kind = "xml"
            else:
                canonical, kind = payload, "binary"
            rows.append({"path": key, "kind": kind, "semantic_sha256": hashlib.sha256(canonical).hexdigest()})
    rows.sort(key=lambda row: row["path"])
    _require(bool(rows), "export_equivalence_package_invalid")
    return {"parts": rows, "semantic_sha256": fingerprint(rows),
            "normalizations": sorted(normalized), "part_count": len(rows), "expanded_bytes": total}


def compare_packages(candidate, independent, *, diagnostics_path=None):
    """Compare all content; report mismatched part paths only, never contents."""
    def measured(path):
        path = Path(path).absolute()
        with open_regular(path.parent, path.name, 64 * 1024**2) as stream:
            return semantic_package_index(stream.read(64 * 1024**2 + 1))
    candidate_index = measured(candidate)
    independent_index = measured(independent)
    left = {row["path"]: row for row in candidate_index["parts"]}
    right = {row["path"]: row for row in independent_index["parts"]}
    if diagnostics_path is not None:
        atomic_json(Path(diagnostics_path), {
            "schema": "ppt-service.export-build-comparison.v1",
            "candidate_only": sorted(set(left) - set(right)),
            "rebuilt_only": sorted(set(right) - set(left)),
            "changed_parts": [{"path": key, "candidate_sha256": left[key]["semantic_sha256"],
                               "rebuilt_sha256": right[key]["semantic_sha256"]}
                              for key in sorted(set(left) & set(right)) if left[key] != right[key]],
            "candidate_normalizations": candidate_index["normalizations"],
            "rebuilt_normalizations": independent_index["normalizations"],
        })
    _require(set(left) == set(right), "export_equivalence_part_roster_mismatch")
    _require(candidate_index["normalizations"] == independent_index["normalizations"],
             "export_equivalence_metadata_mismatch")
    _require(all(left[key] == right[key] for key in left), "export_equivalence_content_mismatch")
    return {"semantic_sha256": candidate_index["semantic_sha256"],
            "part_count": candidate_index["part_count"], "parts": candidate_index["parts"],
            "normalizations": candidate_index["normalizations"],
            "zip_container_metadata_ignored": True,
            "original_export_provenance_verified": False}


def _fresh_output(output, snapshot, postflight_root):
    _require(output.name == "equivalence" and postflight_root.parent == output.parent
             and not output.exists() and not output.is_symlink()
             and not output.resolve().is_relative_to(snapshot.resolve()),
             "export_equivalence_output_not_fresh")
    for path in (output, postflight_root):
        for parent in path.parents:
            _require(not parent.is_symlink() and not parent.is_junction(), "export_equivalence_output_linked")
    output.mkdir(parents=True)


def _exporter_postflight(value, rebuilt, source_rows):
    from .postflight_validation import svg_fingerprint
    _require(isinstance(value, dict) and value.get("schema") == "ppt-master.pptx-postflight-report.v1"
             and value.get("status") == "passed", "export_equivalence_postflight_failed")
    _require(value.get("output") == {"path": str(rebuilt.resolve()), "bytes": rebuilt.stat().st_size}
             and value.get("source", {}).get("svg_slide_count") == len(source_rows)
             and value["source"].get("fingerprint") == svg_fingerprint(source_rows)
             and value["source"].get("authoring_roundtrip") is None,
             "export_equivalence_postflight_binding_invalid")
    checks = value.get("checks", {})
    expected = {
        "zip_integrity": "passed", "slide_count": "passed",
        "internal_relationships": "enforced-at-build", "transitions": "enforced-at-build",
        "animations": "enforced-at-build", "project_svg_contract": "enforced-at-build",
        "compatibility_normalization": "not-applicable", "authoring_roundtrip": "not-applicable",
        "quality_gate": "passed", "quality_warnings": "passed", "template_tokens": "passed",
        "external_images": "passed", "font_portability": "passed",
    }
    _require(all(checks.get(key) == result for key, result in expected.items())
             and checks.get("structured_package") in ("enforced-at-build", "not-applicable")
             and value.get("export_policy") == {"project_svg_contract": "strict", "dangerous_normalization_count": 0},
             "export_equivalence_postflight_incomplete")
    return {"status": "passed", "checks": checks,
            "structured_package_applicable": checks["structured_package"] == "enforced-at-build"}


def run_export_equivalence(snapshot, completed_manifest, request, output, *,
                           postflight_evidence, postflight_root, commands=None):
    """Rebuild through actual pinned release CLI, then compare exact content.

    The live source audit and its fresh working project are required, never an
    Actor's saved report. The release CLI reads bound svg_output explicitly and
    derives strict source/theme/structure contracts from the unchanged lock.
    Passing equivalent output proves the named current build guarantees; it
    does not attest how the original candidate was produced or permit release.
    """
    from .postflight_validation import PostflightEvidence, SOURCE_GATE
    from ppt_service.worker.candidate_validation import inspect_pptx
    snapshot, output, postflight_root = (Path(path).absolute() for path in (snapshot, output, postflight_root))
    source_sha, nonce = request.get("source_manifest_sha256"), request.get("validation_nonce")
    _require(type(postflight_evidence) is PostflightEvidence
             and postflight_evidence.verify(nonce, source_sha), "export_equivalence_live_postflight_required")
    audit = postflight_evidence.report()
    binding, original, index = source_binding(snapshot, completed_manifest, request)
    source_rows = [hash_file(original, key) for key in sorted(index)
                   if key.split("/", 1)[0] not in CACHE_ROOTS]
    binding["copied_source_fingerprint"] = fingerprint(source_rows)
    _require(binding["source_manifest_sha256"] == source_sha
             and audit.get("source") == binding
             and audit.get("source_and_quality_verified") is True and audit.get("source_unchanged") is True
             and SOURCE_GATE in audit.get("completed_programmatic_gates", [])
             and audit.get("ppt_master_commit") == PPT_MASTER_COMMIT,
             "export_equivalence_source_audit_incomplete")
    _require(audit.get("quality", {}).get("warnings_pending") is False
             and audit.get("resources", {}).get("warnings_pending") is False
             and "postflight_warnings_review" not in audit.get("required_pending_gates", []),
             "export_equivalence_source_warnings")
    _require(request.get("phase") in ("generate", "revise")
             and type(request.get("pages")) is int and 3 <= request["pages"] <= 20
             and isinstance(request.get("expected_charts"), list)
             and type(request.get("enable_notes", True)) is bool,
             "export_equivalence_request_invalid")
    project = postflight_root / "source" / relative(binding["project_root"])
    _require(read_json(postflight_root, "source-index.json") == source_rows
             and read_json(postflight_root, "source-binding.json") == binding,
             "export_equivalence_source_copy_invalid")
    unchanged(project, source_rows)
    _fresh_output(output, snapshot, postflight_root)
    # Hash the actual fixed entrypoint and build implementation. The outer
    # stopped-Pod transport additionally binds the entire immutable image.
    scripts = {name: hash_file(SCRIPTS, name)["sha256"] for name in (
        "svg_to_pptx.py", "svg_to_pptx/pptx_package/cli.py", "svg_to_pptx/pptx_package/builder.py",
        "pptx_opc_validation.py", "pptx_transitions.py", "pptx_animations.py",
        "svg_to_pptx/pptx_package/template_structure.py",
    )}
    commit = Path("/opt/ppt-service/build-info/ppt-master.commit").read_text().strip()
    _require(commit == PPT_MASTER_COMMIT, "export_equivalence_official_version_mismatch")
    rebuilt = output / "rebuilt.pptx"
    official_report_path = project / "validation/rebuilt.report.json"
    _require(not official_report_path.exists() and not rebuilt.exists(), "export_equivalence_output_not_fresh")
    args = [PYTHON, str(SCRIPTS / "svg_to_pptx.py"), str(project), "-s", "output",
            "--native-charts-and-tables", "--no-animations", "-o", str(rebuilt)]
    if request.get("enable_notes", True) is False:
        args.append("--no-notes")
    commands = commands or OfficialCommands()
    code = commands.run(args, output / "export.stdout.log", output / "export.stderr.log", timeout=150)
    _require(code == 0 and rebuilt.exists(), "export_equivalence_rebuild_failed")
    rebuilt_row = hash_file(output, rebuilt.name)
    candidate = original / binding["pptx"]["path"]
    native_candidate = inspect_pptx(candidate, request["expected_charts"])
    native_rebuilt = inspect_pptx(rebuilt, request["expected_charts"])
    _require(native_candidate["slide_count"] == native_rebuilt["slide_count"] == request["pages"]
             and native_candidate["chart_count"] == native_rebuilt["chart_count"] == len(request["expected_charts"])
             and (not native_candidate["chart_count"]
                  or native_candidate["chart_data_verified"] is native_rebuilt["chart_data_verified"] is True),
             "export_equivalence_native_mismatch")
    official = read_json(official_report_path.parent, official_report_path.name)
    checked = _exporter_postflight(official, rebuilt, binding["svg_sources"])
    comparison = compare_packages(candidate, rebuilt, diagnostics_path=output / "comparison.private.json")
    unchanged(project, source_rows)
    unchanged(original, source_rows)
    _require(hash_file(snapshot / "result", "manifest.json")["sha256"] == source_sha
             and hash_file(output, rebuilt.name) == rebuilt_row,
             "export_equivalence_source_changed")
    report_row = hash_file(official_report_path.parent, official_report_path.name)
    payload = {
        "schema": SCHEMA, "status": "passed", "controller_nonce": nonce,
        "source_manifest_sha256": source_sha, "input_snapshot_sha256": request["input_snapshot_sha256"],
        "pptx": {key: binding["pptx"][key] for key in ("sha256", "bytes")},
        "source_fingerprint_sha256": binding["copied_source_fingerprint"], "source": binding,
        "publication_authorized": False, "full_exporter_postflight_verified": False,
        "original_export_provenance_verified": False, "export_build_equivalence_verified": True,
        "completed_programmatic_gates": [EXPORT_GATE],
        "rebuilt_pptx": {**rebuilt_row, "path": "equivalence/rebuilt.pptx"},
        "comparison": comparison, "native_candidate": native_candidate, "native_rebuilt": native_rebuilt,
        "source_quality": audit["quality"], "source_resources": audit["resources"],
        "independent_export_postflight": checked,
        "exporter_postflight_report": {**report_row,
            "path": official_report_path.relative_to(output.parent).as_posix()},
        "official": {"ppt_master_commit": commit, "script_sha256": scripts, "exit_codes": {"export": 0},
            "source_selection": "svg_output", "release_mode": "strict", "native_charts_and_tables": True,
            "animations_enabled": False, "notes_enabled": request.get("enable_notes", True)},
        "source_unchanged": True, "command_groups_stopped": True,
        "validator_stop_proof_requires_controller": True,
    }
    atomic_json(output / "export-contract.json", payload)
    return payload
