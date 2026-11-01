"""Canonical comparison contracts; synthetic packages are not exporter proof."""
from __future__ import annotations

import io
import copy
from pathlib import Path
import shutil
from types import SimpleNamespace
import uuid
import zipfile

import pytest

from ppt_service.control.execution import ControlError
from ppt_service.control.export_equivalence import compare_packages, semantic_package_index
from ppt_service.control import export_equivalence as equivalence
from ppt_service.control.execution import PPT_MASTER_COMMIT
from ppt_service.control.postflight_validation import PostflightEvidence, SOURCE_GATE, svg_fingerprint
from ppt_service.control.render_validation import fingerprint, hash_file, source_binding
from ppt_service.control.staging import atomic_json, read_json


def package(parts, *, date=(2020, 1, 1, 0, 0, 0), compression=zipfile.ZIP_DEFLATED):
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        for name, body in parts.items():
            item = zipfile.ZipInfo(name, date_time=date)
            item.compress_type = compression
            archive.writestr(item, body)
    return raw.getvalue()


@pytest.fixture
def pair(tmp_path):
    parts = {
        "docProps/core.xml": '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dcterms="http://purl.org/dc/terms/"><dcterms:created>2026-11-01T00:00:00Z</dcterms:created></cp:coreProperties>',
        "ppt/slides/slide1.xml": '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:sp>'
            '<a:xfrm><a:off x="10" y="20"/></a:xfrm><a:t>Actual native text</a:t></p:sp></p:spTree></p:cSld></p:sld>',
        "ppt/media/image1.png": b"Synthetic opaque image bytes",
    }
    left, right = tmp_path / "candidate.pptx", tmp_path / "rebuilt.pptx"
    left.write_bytes(package(parts))
    right.write_bytes(package(parts))
    return parts, left, right


def test_zip_container_metadata_and_core_times_do_not_change_visible_content(pair):
    parts, left, right = pair
    parts["docProps/core.xml"] = parts["docProps/core.xml"].replace("2026-11-01T00:00:00Z", "2026-11-01T12:13:14Z")
    right.write_bytes(package(parts, date=(2026, 11, 1, 1, 2, 4), compression=zipfile.ZIP_STORED))
    report = compare_packages(left, right)
    assert report["part_count"] == 3
    assert report["normalizations"] == ["docProps/core.xml:created"]
    assert report["original_export_provenance_verified"] is False


@pytest.mark.parametrize("change", ["text", "geometry", "image", "extra", "missing", "notes", "space"])
def test_any_native_content_resource_or_roster_difference_fails(pair, change):
    parts, left, right = pair
    if change == "text":
        parts["ppt/slides/slide1.xml"] = parts["ppt/slides/slide1.xml"].replace("Actual native text", "Invented text")
    elif change == "geometry":
        parts["ppt/slides/slide1.xml"] = parts["ppt/slides/slide1.xml"].replace('x="10"', 'x="11"')
    elif change == "image":
        parts["ppt/media/image1.png"] += b"different"
    elif change == "extra":
        parts["ppt/media/extra.bin"] = b"extra"
    elif change == "missing":
        del parts["ppt/media/image1.png"]
    elif change == "notes":
        parts["ppt/notesSlides/notesSlide1.xml"] = "<notes>invented notes</notes>"
    else:
        parts["ppt/slides/slide1.xml"] = parts["ppt/slides/slide1.xml"].replace("Actual native text", " Actual native text ")
    right.write_bytes(package(parts))
    with pytest.raises(ControlError, match="export_equivalence_(content|part_roster)_mismatch"):
        compare_packages(left, right)


def test_missing_core_timestamp_is_not_erased_to_equivalence(pair):
    parts, left, right = pair
    parts["docProps/core.xml"] = parts["docProps/core.xml"].replace('<dcterms:created>2026-11-01T00:00:00Z</dcterms:created>', '')
    right.write_bytes(package(parts))
    with pytest.raises(ControlError, match="export_equivalence_metadata_mismatch"):
        compare_packages(left, right)


def test_embedded_workbook_cells_are_compared_recursively(pair):
    parts, left, right = pair
    parts["ppt/embeddings/data.xlsx"] = package({"xl/worksheets/sheet1.xml": "<sheet><v>12</v></sheet>"})
    left.write_bytes(package(parts))
    parts["ppt/embeddings/data.xlsx"] = package({"xl/worksheets/sheet1.xml": "<sheet><v>13</v></sheet>"})
    right.write_bytes(package(parts))
    with pytest.raises(ControlError, match="export_equivalence_content_mismatch"):
        compare_packages(left, right)


@pytest.mark.parametrize("xml", ['<!DOCTYPE a [<!ENTITY x "unsafe">]><a>&x;</a>', '<a>', '<a>\x00</a>'])
def test_unsafe_or_malformed_xml_cannot_be_canonicalized(xml):
    with pytest.raises(ControlError, match="export_equivalence_unsafe_xml"):
        semantic_package_index(package({"part.xml": xml}))


def test_duplicate_casefolded_parts_are_rejected():
    with pytest.raises(ControlError, match="export_equivalence_package_invalid"):
        semantic_package_index(package({"A.xml": "<a/>", "a.xml": "<a/>"}))


def test_relationship_declaration_order_has_no_semantic_effect(pair):
    parts, left, right = pair
    a = '<Relationship Id="a" Type="type" Target="a.xml"/>'
    b = '<Relationship Id="b" Type="type" Target="b.xml"/>'
    parts["ppt/_rels/slide.xml.rels"] = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + a + b + '</Relationships>'
    left.write_bytes(package(parts))
    parts["ppt/_rels/slide.xml.rels"] = parts["ppt/_rels/slide.xml.rels"].replace(a + b, b + a)
    right.write_bytes(package(parts))
    assert compare_packages(left, right)["part_count"] == 4


def test_random_creation_metadata_and_field_guid_preserve_field_content(pair):
    parts, left, right = pair
    slide = parts["ppt/slides/slide1.xml"]
    slide = slide.replace('<a:t>Actual native text</a:t>',
        f'<a:fld id="{{{uuid.uuid4()}}}" type="slidenum"><a:t>1</a:t></a:fld>')
    slide = slide.replace('</p:cSld>', '<p:extLst><p:ext uri="{BB962C8B-B14F-4D97-AF65-F5344CB8AC3E}">'
        '<p14:creationId xmlns:p14="http://schemas.microsoft.com/office/powerpoint/2010/main" val="123"/>'
        '</p:ext></p:extLst></p:cSld>')
    parts["ppt/slides/slide1.xml"] = slide
    left.write_bytes(package(parts))
    parts["ppt/slides/slide1.xml"] = slide.replace('val="123"', 'val="456"')
    import re
    parts["ppt/slides/slide1.xml"] = re.sub(r'id="\{[^}]+\}"', f'id="{{{uuid.uuid4()}}}"', parts["ppt/slides/slide1.xml"])
    right.write_bytes(package(parts))
    result = compare_packages(left, right)
    assert "ppt/slides/slide1.xml:a.fld[1].id" in result["normalizations"]
    assert "ppt/slides/slide1.xml:p14.creationId[1].val" in result["normalizations"]
    parts["ppt/slides/slide1.xml"] = parts["ppt/slides/slide1.xml"].replace('<a:t>1</a:t>', '<a:t>2</a:t>')
    right.write_bytes(package(parts))
    with pytest.raises(ControlError, match="export_equivalence_content_mismatch"):
        compare_packages(left, right)


class SyntheticExporter:
    """Offline command replacement; does not run the official exporter."""

    def __init__(self, case):
        self.case, self.calls = case, []
        self.exit_code = 0
        self.after = None
        self.mutate_report = None
        self.mutate_pptx = None

    def run(self, args, stdout, stderr, *, timeout):
        self.calls.append(args)
        stdout.write_text("Synthetic independent exporter fixture, not build proof\n")
        stderr.write_text("")
        target = Path(args[args.index("-o") + 1])
        target.write_bytes(self.case.candidate.read_bytes())
        if self.mutate_pptx:
            with zipfile.ZipFile(target) as archive:
                parts = {name: archive.read(name) for name in archive.namelist()}
            self.mutate_pptx(parts)
            target.write_bytes(package(parts))
        report = {
            "schema": "ppt-master.pptx-postflight-report.v1", "status": "passed",
            "output": {"path": str(target.resolve()), "bytes": target.stat().st_size},
            "source": {"svg_slide_count": 3, "fingerprint": svg_fingerprint(self.case.binding["svg_sources"]),
                       "authoring_roundtrip": None},
            "checks": {
                "zip_integrity": "passed", "slide_count": "passed",
                "internal_relationships": "enforced-at-build", "transitions": "enforced-at-build",
                "animations": "enforced-at-build", "project_svg_contract": "enforced-at-build",
                "compatibility_normalization": "not-applicable", "authoring_roundtrip": "not-applicable",
                "quality_gate": "passed", "quality_warnings": "passed", "template_tokens": "passed",
                "external_images": "passed", "font_portability": "passed", "structured_package": "not-applicable",
            },
            "export_policy": {"project_svg_contract": "strict", "dangerous_normalization_count": 0},
        }
        if self.mutate_report:
            self.mutate_report(report)
        atomic_json(self.case.project / "validation/rebuilt.report.json", report)
        if self.after:
            self.after()
        return self.exit_code


@pytest.fixture
def build_case(tmp_path, monkeypatch):
    from ppt_service.tests.control.test_final_pptx_validation import fixture_deck
    snapshot = tmp_path / "snapshot"
    original = snapshot / "project/deck"
    (original / "svg_output").mkdir(parents=True)
    (snapshot / "result").mkdir()
    slides, rows = [], []
    for number in range(1, 4):
        key = f"svg_output/{number:02}.svg"
        (original / key).write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720">'
            f'<text>Offline source {number}</text></svg>')
        rows.append({**hash_file(original, key), "role": "svg"})
        slides.append({"ordinal": number, "svg_path": key, "preview_path": f".preview/{number:02}.png"})
    (original / "deck.pptx").write_bytes(fixture_deck())
    rows.append({**hash_file(original, "deck.pptx"), "role": "pptx"})
    manifest = {"phase": "generate", "status": "candidate", "project_root": "deck",
        "input_snapshot_sha256": "a" * 64, "slides": slides, "artifacts": rows}
    atomic_json(snapshot / "result/manifest.json", manifest)
    request = {"phase": "generate", "pages": 3, "expected_charts": [],
        "validation_nonce": str(uuid.uuid4()), "input_snapshot_sha256": "a" * 64,
        "source_manifest_sha256": hash_file(snapshot / "result", "manifest.json")["sha256"]}
    binding, _, _ = source_binding(snapshot, manifest, request)
    source_rows = sorted([{key: row[key] for key in ("path", "sha256", "bytes")} for row in rows],
                         key=lambda row: row["path"])
    binding["copied_source_fingerprint"] = fingerprint(source_rows)
    bundle = tmp_path / "bundle"
    postflight = bundle / "postflight"
    project = postflight / "source/deck"
    shutil.copytree(original, project)
    atomic_json(postflight / "source-binding.json", binding)
    atomic_json(postflight / "source-index.json", source_rows)
    audit = {"source": copy.deepcopy(binding), "source_and_quality_verified": True, "source_unchanged": True,
        "completed_programmatic_gates": [SOURCE_GATE], "ppt_master_commit": PPT_MASTER_COMMIT,
        "quality": {"warnings_pending": False}, "resources": {"warnings_pending": False},
        "required_pending_gates": ["independent_export_build_contract", "independent_visual_review"]}
    evidence = PostflightEvidence._issue(audit, request["validation_nonce"], request["source_manifest_sha256"])
    case = SimpleNamespace(snapshot=snapshot, manifest=manifest, original=original, project=project,
        request=request, binding=binding, audit=audit, evidence=evidence, postflight=postflight,
        output=bundle / "equivalence", candidate=original / "deck.pptx")
    case.commands = SyntheticExporter(case)
    scripts = tmp_path / "image-owned-scripts"
    for name in ("svg_to_pptx.py", "svg_to_pptx/pptx_package/cli.py", "svg_to_pptx/pptx_package/builder.py",
                 "pptx_opc_validation.py", "pptx_transitions.py", "pptx_animations.py",
                 "svg_to_pptx/pptx_package/template_structure.py"):
        path = scripts / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Offline image script identity only; never executed\n" + name)
    monkeypatch.setattr(equivalence, "SCRIPTS", scripts)
    original_read = Path.read_text
    def commit(path, *args, **kwargs):
        if path.as_posix() == "/opt/ppt-service/build-info/ppt-master.commit":
            return PPT_MASTER_COMMIT
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", commit)
    return case


def run_build(case, **overrides):
    values = {"snapshot": case.snapshot, "completed_manifest": case.manifest,
        "request": case.request, "output": case.output, "postflight_evidence": case.evidence,
        "postflight_root": case.postflight, "commands": case.commands}
    values.update(overrides)
    return equivalence.run_export_equivalence(**values)


def test_live_audit_and_actual_rebuild_comparison_required_before_gate(build_case):
    case = build_case
    report = run_build(case)
    assert report == read_json(case.output, "export-contract.json")
    assert report["schema"] == equivalence.SCHEMA
    assert report["completed_programmatic_gates"] == [equivalence.EXPORT_GATE]
    assert report["export_build_equivalence_verified"] is True
    assert report["full_exporter_postflight_verified"] is report["original_export_provenance_verified"] is False
    assert report["publication_authorized"] is False
    assert report["rebuilt_pptx"]["path"] == "equivalence/rebuilt.pptx"
    assert report["source_fingerprint_sha256"] == case.binding["copied_source_fingerprint"]
    args = case.commands.calls[0]
    assert args[args.index("-s") + 1] == "output"
    assert "--native-charts-and-tables" in args and "--no-animations" in args
    assert not any(flag in args for flag in ("--quick-generate", "--roundtrip", "--enable-dangerous-nonconforming-svg-export"))
    assert report["official"]["exit_codes"] == {"export": 0}


@pytest.mark.parametrize("fault", ["json", "nonce", "binding", "copy"])
def test_missing_forged_or_tampered_live_audit_rejects(build_case, fault):
    case = build_case
    if fault == "json":
        case.evidence = case.evidence.report()
    elif fault == "nonce":
        case.request["validation_nonce"] = str(uuid.uuid4())
    elif fault == "binding":
        case.audit["source"]["input_snapshot_sha256"] = "f" * 64
        case.evidence = PostflightEvidence._issue(case.audit, case.request["validation_nonce"], case.request["source_manifest_sha256"])
    else:
        (case.project / "svg_output/01.svg").write_text("changed source copy")
    with pytest.raises(ControlError):
        run_build(case)
    assert case.commands.calls == []
    assert not (case.output / "export-contract.json").exists()


def test_clean_source_warnings_are_pending_not_a_build_pass(build_case):
    case = build_case
    case.audit["quality"]["warnings_pending"] = True
    case.evidence = PostflightEvidence._issue(case.audit, case.request["validation_nonce"], case.request["source_manifest_sha256"])
    with pytest.raises(ControlError, match="export_equivalence_source_warnings"):
        run_build(case)
    assert case.commands.calls == []


def test_rebuild_failure_cannot_reuse_a_created_file_or_report(build_case):
    case = build_case
    case.commands.exit_code = 1
    with pytest.raises(ControlError, match="export_equivalence_rebuild_failed"):
        run_build(case)
    assert not (case.output / "export-contract.json").exists()


def test_rebuild_content_mismatch_retains_concrete_private_part_diagnostics(build_case):
    case = build_case
    case.commands.mutate_pptx = lambda parts: parts.update({"ppt/slides/slide1.xml":
        parts["ppt/slides/slide1.xml"].replace(b"Offline fixture", b"Different text")})
    with pytest.raises(ControlError, match="export_equivalence_content_mismatch"):
        run_build(case)
    diagnostic = read_json(case.output, "comparison.private.json")
    assert [row["path"] for row in diagnostic["changed_parts"]] == ["ppt/slides/slide1.xml"]
    assert not (case.output / "export-contract.json").exists()


@pytest.mark.parametrize("fault", ["source", "output", "quality", "compatibility"])
def test_old_or_relaxed_exporter_postflight_does_not_prove_the_build(build_case, fault):
    case = build_case
    def mutate(report):
        if fault == "source":
            report["source"]["fingerprint"]["digest"] = "f" * 64
        elif fault == "output":
            report["output"]["path"] = "/other.pptx"
        elif fault == "quality":
            report["checks"]["quality_gate"] = "not-provided"
        else:
            report["export_policy"]["project_svg_contract"] = "strict-after-dangerous-normalization"
    case.commands.mutate_report = mutate
    with pytest.raises(ControlError, match="export_equivalence_postflight_"):
        run_build(case)
    assert not (case.output / "export-contract.json").exists()


def test_original_source_changed_during_export_never_gets_equivalence_receipt(build_case):
    case = build_case
    case.commands.after = lambda: (case.original / "svg_output/01.svg").write_text("changed during rebuild")
    with pytest.raises(ControlError, match="render_source_changed"):
        run_build(case)
    assert not (case.output / "export-contract.json").exists()


def test_disabling_notes_is_explicit_trusted_request_not_candidate_inference(build_case):
    case = build_case
    case.request["enable_notes"] = False
    report = run_build(case)
    assert "--no-notes" in case.commands.calls[0]
    assert report["official"]["notes_enabled"] is False
