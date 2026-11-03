"""Offline hostile-output contracts; tool fakes do not prove Office rendering."""
from __future__ import annotations

import io
from pathlib import Path
import uuid
import zipfile

from PIL import Image
import pytest

from ppt_service.control import final_pptx_validation as final
from ppt_service.control.execution import ControlError
from ppt_service.control.render_validation import hash_file
from ppt_service.control.staging import read_json

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"


def fixture_deck():
    """Minimal native package; not a real Office acceptance sample."""
    parts = {
        "[Content_Types].xml": '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/></Types>',
        "_rels/.rels": f'<Relationships xmlns="{PR}"><Relationship Id="office" Type="{R}/officeDocument" '
            'Target="ppt/presentation.xml"/></Relationships>',
        "ppt/presentation.xml": f'<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst>'
            + ''.join(f'<p:sldId id="{255+n}" r:id="s{n}"/>' for n in range(1, 4))
            + '</p:sldIdLst><p:sldSz cx="12192000" cy="6858000"/></p:presentation>',
        "ppt/_rels/presentation.xml.rels": f'<Relationships xmlns="{PR}">'
            + ''.join(f'<Relationship Id="s{n}" Type="{R}/slide" Target="slides/slide{n}.xml"/>' for n in range(1, 4))
            + '</Relationships>',
    }
    for ordinal in range(1, 4):
        parts[f"ppt/slides/slide{ordinal}.xml"] = f'<p:sld xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree>'
        parts[f"ppt/slides/slide{ordinal}.xml"] += '<p:sp><p:txBody><a:p><a:r><a:t>Offline fixture</a:t></a:r></a:p></p:txBody></p:sp>'
        parts[f"ppt/slides/slide{ordinal}.xml"] += '<p:cxnSp><p:nvCxnSpPr><p:cNvCxnSpPr><a:stCxn id="1" idx="0"/><a:endCxn id="2" idx="1"/></p:cNvCxnSpPr></p:nvCxnSpPr></p:cxnSp>'
        parts[f"ppt/slides/slide{ordinal}.xml"] += '</p:spTree></p:cSld></p:sld>'
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as package:
        for name, data in parts.items():
            package.writestr(name, data)
    return raw.getvalue()


class FakeCommands:
    def __init__(self, runtime):
        self.runtime = runtime
        self.calls = []
        self.fail = None
        self.pages = 3
        self.page_size = "960 x 540"
        self.rotation = 0
        self.png_count = 3
        self.png_size = (1280, 720)
        self.extra = False
        self.after = None

    def run(self, args, stdout, stderr, *, timeout):
        self.calls.append(args)
        stdout.write_text("")
        stderr.write_text("")
        if "--convert-to" in args:
            step = "export"
            folder = Path(args[args.index("--outdir") + 1])
            # A structurally real synthetic PDF exercises the pinned glyph
            # reader. It is still not LibreOffice or real export acceptance.
            import fitz
            with fitz.open() as document:
                for _ in range(3):
                    page = document.new_page(width=960, height=540)
                    page.insert_text((50, 50), "Offline fixture", fontsize=20)
                document.save(folder / "presentation.pdf")
        elif args[0] == final.PDFINFO and "-v" not in args:
            step = "info"
            stdout.write_text(f"Pages:          {self.pages}\n" + "".join(
                f"Page {n:4} size: {self.page_size} pts\nPage {n:4} rot: {self.rotation}\n"
                for n in range(1, self.pages + 1)))
        elif args[0] == final.PDFTOPPM and "-v" not in args:
            step = "raster"
            prefix = Path(args[-1])
            for ordinal in range(1, self.png_count + 1):
                Image.new("RGB", self.png_size, (ordinal, 100, 230)).save(
                    prefix.parent / f"{prefix.name}-{ordinal:02}.png")
            if self.extra:
                (prefix.parent / "unexpected.json").write_text("{}")
            if self.after:
                self.after()
        elif args[0] == final.FONTLIST and "--format" in args:
            step = "fonts"
            stdout.write_text("Noto Sans CJK SC\tRegular\t/usr/share/fonts/noto.ttf\n")
        else:
            step = "version"
            stderr.write_text("Offline tool identity fixture 1.0\n")
        return 13 if self.fail == step else 0


@pytest.fixture
def case(tmp_path, monkeypatch):
    source_dir = tmp_path / "immutable-source"
    source_dir.mkdir()
    pptx = source_dir / "deck.pptx"
    pptx.write_bytes(fixture_deck())
    output = tmp_path / "retained-final"
    request = {"validation_nonce": str(uuid.uuid4()), "source_manifest_sha256": "a" * 64,
               "input_snapshot_sha256": "b" * 64, "pages": 3}
    fake = FakeCommands(output / "runtime")
    monkeypatch.setattr(final, "_tool_identity", lambda path:
        {"path": path, "resolved_path": path, "sha256": "c" * 64})
    return {"pptx": pptx, "expected_pptx": hash_file(source_dir, pptx.name),
            "request": request, "output": output, "commands_factory": lambda _: fake}, fake


def test_final_pptx_evidence_is_source_bound_and_separate_from_human_acceptance(case):
    values, fake = case
    source = values["pptx"].read_bytes()
    report = final.verify_final_pptx(**values)
    assert report == read_json(values["output"], "final-pptx-evidence.json")
    assert report["status"] == "passed"
    assert report["completed_programmatic_gates"] == [final.FINAL_PPTX_GATE]
    assert report["pptx"] == {key: values["expected_pptx"][key] for key in ("bytes", "sha256")}
    assert report["publication_authorized"] is False
    assert report["full_exporter_postflight_verified"] is False
    assert report["human_visual_acceptance"] is False
    assert report["powerpoint_acceptance"] == "pending"
    assert report["deck"]["semantic_connectors"] == 3
    assert report["deck"]["connected_connectors"] == 3
    assert report["deck"]["native_shapes"] == report["deck"]["text_shapes"] == 3
    assert report["renderer"]["name"] == "LibreOffice Impress"
    assert set(report["renderer"]["tools"]) == {"libreoffice", "pdfinfo", "pdftoppm", "fontconfig", "soffice_binary"}
    assert values["pptx"].read_bytes() == source
    assert len(report["pages"]) == 3
    assert [page["path"] for page in report["pages"]] == [f"pages/{n}.png" for n in range(1, 4)]
    assert any(arg.startswith("-env:UserInstallation=file:") for args in fake.calls for arg in args)
    assert all(args[0] in {final.LIBREOFFICE, final.PDFINFO, final.PDFTOPPM, final.FONTLIST} for args in fake.calls)
    assert report["validator_stop_proof_requires_controller"] is True
    assert report["text_layout"]["path"] == "text-layout.json"
    layout = read_json(values["output"], "text-layout.json")
    assert layout["binding"]["pptx_sha256"] == report["pptx"]["sha256"]
    assert layout["binding"]["pdf_sha256"] == report["pdf"]["sha256"]
    assert layout["human_visual_acceptance"] is False
    assert layout["status"] == "incomplete"  # fixture intentionally has no native frames


@pytest.mark.parametrize("key,value", [("validation_nonce", "bad"), ("pages", True), ("pages", 2),
    ("pages", 21), ("source_manifest_sha256", "a" * 63), ("input_snapshot_sha256", None)])
def test_bad_controller_binding_is_rejected_before_commands(case, key, value):
    values, fake = case
    values["request"][key] = value
    with pytest.raises(ControlError, match="final_pptx_binding_invalid"):
        final.verify_final_pptx(**values)
    assert fake.calls == []


@pytest.mark.parametrize("problem", ["hash", "bytes", "changed"])
def test_wrong_or_changed_pptx_is_rejected_before_commands(case, problem):
    values, fake = case
    if problem == "hash":
        values["expected_pptx"]["sha256"] = "f" * 64
    elif problem == "bytes":
        values["expected_pptx"]["bytes"] += 1
    else:
        values["pptx"].write_bytes(b"changed source")
    with pytest.raises(ControlError, match="final_pptx_source_mismatch"):
        final.verify_final_pptx(**values)
    assert fake.calls == []


def test_reused_output_cannot_reuse_old_render(case):
    values, _ = case
    values["output"].mkdir()
    with pytest.raises(ControlError, match="final_pptx_output_not_fresh"):
        final.verify_final_pptx(**values)


@pytest.mark.parametrize("step,code", [("version", "renderer_unavailable"), ("fonts", "font_inventory_failed"),
    ("export", "export_failed"), ("info", "pdf_invalid"), ("raster", "raster_failed")])
def test_nonzero_tool_exit_never_creates_a_success_receipt(case, step, code):
    values, fake = case
    fake.fail = step
    with pytest.raises(ControlError, match="final_pptx_" + code):
        final.verify_final_pptx(**values)
    assert not (values["output"] / "final-pptx-evidence.json").exists()


@pytest.mark.parametrize("field,value,code", [("pages", 2, "page_count_mismatch"),
    ("page_size", "600 x 600", "canvas_invalid"), ("png_count", 0, "raster_roster_invalid"),
    ("rotation", 90, "canvas_invalid"),
    ("png_count", 4, "raster_roster_invalid"), ("png_size", (100, 100), "raster_canvas_invalid"),
    ("extra", True, "raster_roster_invalid")])
def test_exit_zero_incomplete_or_wrong_render_never_passes(case, field, value, code):
    values, fake = case
    setattr(fake, field, value)
    with pytest.raises(ControlError, match="final_pptx_" + code):
        final.verify_final_pptx(**values)
    assert not (values["output"] / "final-pptx-evidence.json").exists()


def test_source_mutated_during_render_has_no_receipt(case):
    values, fake = case
    fake.after = lambda: values["pptx"].write_bytes(b"changed during render")
    with pytest.raises(ControlError, match="final_pptx_source_changed"):
        final.verify_final_pptx(**values)
    assert not (values["output"] / "final-pptx-evidence.json").exists()


def test_absent_tool_is_not_optional_success(case, monkeypatch):
    values, _ = case
    def absent(_):
        raise FileNotFoundError("offline missing-tool fixture")
    monkeypatch.setattr(final, "_tool_identity", absent)
    with pytest.raises(ControlError, match="final_pptx_renderer_unavailable"):
        final.verify_final_pptx(**values)
    assert not (values["output"] / "final-pptx-evidence.json").exists()


def test_detected_final_text_defect_retains_bound_actionable_failure_without_success_receipt(case, monkeypatch):
    values, _ = case
    def failed_layout(pptx, pdf, deck, binding):
        return {"schema": "ppt-service.final-text-layout.v1", "status": "failed",
            "binding": binding, "human_visual_acceptance": False, "publication_authorized": False,
            "coverage": {"checked_text_shapes": 1}, "issues": [
                {"code": "short_token_unplanned_wrap", "page": 2, "shape_id": 17}]}
    monkeypatch.setattr(final, "verify_text_layout", failed_layout)
    with pytest.raises(ControlError, match="final_pptx_text_layout_failed"):
        final.verify_final_pptx(**values)
    report = read_json(values["output"], "text-layout.json")
    assert report["binding"]["controller_nonce"] == values["request"]["validation_nonce"]
    assert report["binding"]["pptx_sha256"] == values["expected_pptx"]["sha256"]
    assert report["issues"] == [{"code": "short_token_unplanned_wrap", "page": 2, "shape_id": 17}]
    assert not (values["output"] / "final-pptx-evidence.json").exists()


def test_unreadable_actual_pdf_cannot_receive_final_rendering_success(case, monkeypatch):
    values, _ = case
    def broken_reader(*args):
        raise ControlError("final_pptx_text_extraction_failed")
    monkeypatch.setattr(final, "verify_text_layout", broken_reader)
    with pytest.raises(ControlError, match="final_pptx_text_extraction_failed"):
        final.verify_final_pptx(**values)
    assert not (values["output"] / "final-pptx-evidence.json").exists()
