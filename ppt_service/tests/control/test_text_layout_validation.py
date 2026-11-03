"""Measured-geometry contracts; synthetic PDFs do not prove Office rendering.

Run only in the Linux server test environment. Real exporter/Office acceptance
must additionally exercise the controlled full pipeline on a fresh candidate.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from ppt_service.control.execution import ControlError
from ppt_service.control.text_layout_validation import (
    Glyph, TextFrame, analyze_text_layout, read_native_frames, read_pdf_glyphs,
    verify_text_layout,
)


def letters(text, x, baseline, line=1, size=12):
    return [Glyph(c, (x + n * size * .6, baseline - size, x + (n + 1) * size * .6, baseline + size * .2),
                  (x + n * size * .6, baseline), size, line)
            for n, c in enumerate(text)]


def frame(text, box=(40, 25, 300, 100), *, identifier=1, breaks=(), title=False, limit=None):
    return TextFrame(identifier, text, frozenset(breaks), box, box[2] - box[0], title, limit)


def check(frames, glyphs):
    return analyze_text_layout([frames], [glyphs], 960, 540)


def codes(report):
    return {issue["code"] for issue in report["issues"]}


def test_short_code_word_split_by_office_gets_page_and_native_frame_diagnostic():
    report = check([frame("tools", (40, 25, 66, 100), identifier=17)],
                   letters("too", 45, 45) + letters("ls", 45, 65, line=2))
    assert report["status"] == "failed"
    issue = next(row for row in report["issues"] if row["code"] == "short_token_unplanned_wrap")
    assert issue["page"] == 1 and issue["shape_id"] == 17
    assert issue["frame_pt"] == [40, 25, 66, 100]
    assert issue["rendered_line_count"] == 2
    assert issue["usable_frame_width_pt"] == 26
    assert issue["message"] and issue["suggestion"]
    assert "tools" not in str(report)  # arbitrary source content is not diagnostic prose
    assert report["human_visual_acceptance"] is report["publication_authorized"] is False


def test_short_cjk_one_character_orphan_rejects_but_balanced_label_does_not():
    defective = check([frame("共享基座")], letters("共享基", 45, 45) + letters("座", 45, 65, line=2))
    assert "short_label_orphan_line" in codes(defective)
    balanced = check([frame("共享基座")], letters("共享", 45, 45) + letters("基座", 45, 65, line=2))
    assert balanced["status"] == "passed"


def test_explicit_source_line_break_is_not_misreported_as_bad_short_token_wrap():
    report = check([frame("core/session", breaks=(5,))],
                   letters("core/", 45, 45) + letters("session", 45, 65, line=2))
    assert report["issues"] == []


def test_baked_in_hard_break_cannot_hide_a_single_trailing_identifier_letter():
    report = check([frame("tools", breaks=(4,))],
                   letters("tool", 45, 45) + letters("s", 45, 65, line=2))
    assert "short_token_orphan_fragment" in codes(report)


def test_a_real_single_letter_word_after_whitespace_is_not_an_identifier_fragment():
    shape = frame("columnA", breaks=(6,))
    shape.word_breaks = frozenset((6,))
    report = check([shape], letters("column", 45, 45) + letters("A", 45, 65, line=2))
    assert report["issues"] == []


def test_normal_english_word_boundary_is_allowed_even_when_pdf_matching_compacts_spaces():
    report = check([frame("Projectnotes", breaks=(7,))],
                   letters("Project", 45, 45) + letters("notes", 45, 65, line=2))
    assert report["status"] == "passed"


def test_style_runs_at_same_baseline_are_not_line_wraps():
    glyphs = letters("Con", 45, 45, line=1) + letters("sumer", 66.6, 45, line=2)
    assert check([frame("Consumer")], glyphs)["issues"] == []


def test_baseline_shifted_superscript_is_not_a_short_word_wrap():
    glyphs = letters("H", 45, 45, line=1) + letters("2", 52.2, 37, line=2, size=8) \
        + letters("O", 57, 45, line=3)
    assert check([frame("H2O")], glyphs)["issues"] == []


def test_actual_pdf_text_outside_native_frame_rejects_despite_valid_page_size():
    report = check([frame("Consumer", (40, 25, 70, 70))], letters("Consumer", 45, 45))
    assert "text_outside_frame" in codes(report)
    assert report["coverage"]["checked_text_shapes"] == 1


def test_off_canvas_text_is_not_hidden_by_a_valid_raster_canvas():
    report = check([frame("Outside", (-15, 25, 150, 80))], letters("Outside", -12, 45))
    assert "text_outside_slide" in codes(report)


def test_title_body_collision_uses_actual_glyphs_not_merely_overlapping_text_frames():
    frames = [frame("Title", (40, 25, 300, 100), title=True),
              frame("Body", (40, 25, 300, 100), identifier=2)]
    collision = check(frames, letters("Title", 45, 45) + letters("Body", 46, 48, line=2))
    issue = next(row for row in collision["issues"] if row["code"] == "text_collision")
    assert issue["title_body_collision"] is True
    assert issue["other_shape_id"] == 2
    normal = check(frames, letters("Title", 45, 45) + letters("Body", 45, 65, line=2))
    assert normal["issues"] == []  # frame intersection alone is insufficient


def test_font_box_leading_contact_is_not_a_glyph_collision():
    frames = [frame("Alpha"), frame("Beta", identifier=2)]
    report = check(frames, letters("Alpha", 45, 45) + letters("Beta", 45, 59.4, line=2))
    assert "text_collision" not in codes(report)


def test_identical_coincident_text_overlay_is_disclosed_incomplete_not_false_collision():
    frames = [frame("Shadow"), frame("Shadow", identifier=2)]
    report = check(frames, letters("Shadow", 45, 45) + letters("Shadow", 45.2, 45.2, line=2))
    assert report["status"] == "incomplete"
    assert report["issues"] == []
    assert report["advisories"]


@pytest.mark.parametrize("budget", ["_MAX_COLLISION_SHAPE_PAIRS", "_MAX_COLLISION_GLYPH_PAIRS"])
def test_collision_budget_is_bounded_and_exhaustion_is_disclosed_without_invented_pass(monkeypatch, budget):
    from ppt_service.control import text_layout_validation as layout
    monkeypatch.setattr(layout, budget, 0)
    frames = [frame("Alpha"), frame("Beta", identifier=2)]
    report = check(frames, letters("Alpha", 45, 45) + letters("Beta", 46, 48, line=2))
    assert report["status"] == "incomplete" and report["issues"] == []
    assert report["advisories"][0]["code"] == "collision_comparison_budget_exhausted"
    assert report["human_visual_acceptance"] is False


@pytest.mark.parametrize("limit", ["rotated_text_not_checked", "vertical_text_not_checked",
                                   "inherited_text_frame_not_checked"])
def test_unsupported_geometry_and_missing_text_are_coverage_limits_not_invented_passes(limit):
    report = check([frame("Unsupported", limit=limit), frame("Missing", identifier=2)], [])
    assert report["status"] == "incomplete" and report["issues"] == []
    assert report["coverage"]["geometry_limited_shapes"] == 1
    assert report["coverage"]["unmatched_text_shapes"] == 1
    assert report["human_visual_acceptance"] is False


def native_package(*, group=False, explicit_break=False, whitespace=False):
    p = "http://schemas.openxmlformats.org/presentationml/2006/main"
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    shape = '<p:sp><p:nvSpPr><p:cNvPr id="19" name="not echoed"/></p:nvSpPr><p:spPr><a:xfrm>' \
        '<a:off x="635000" y="381000"/><a:ext cx="2540000" cy="1270000"/></a:xfrm></p:spPr>' \
        '<p:txBody><a:bodyPr lIns="0" rIns="127000"/><a:p><a:r><a:rPr sz="2400"/>'
    shape += '<a:t>Project notes</a:t>' if whitespace else '<a:t>Consumer</a:t>'
    shape += '</a:r>'
    if explicit_break:
        shape += '<a:br/><a:r><a:t>tools</a:t></a:r>'
    shape += '</a:p></p:txBody></p:sp>'
    if group:
        shape = '<p:grpSp><p:grpSpPr><a:xfrm><a:off x="1270000" y="1270000"/>' \
            '<a:ext cx="5080000" cy="2540000"/><a:chOff x="0" y="0"/>' \
            '<a:chExt cx="2540000" cy="1270000"/></a:xfrm></p:grpSpPr>' + shape + '</p:grpSp>'
    parts = {
        "ppt/presentation.xml": f'<p:presentation xmlns:p="{p}" xmlns:r="{r}"><p:sldIdLst>'
            '<p:sldId id="256" r:id="s1"/></p:sldIdLst></p:presentation>',
        "ppt/_rels/presentation.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="s1" Type="{r}/slide" Target="slides/slide1.xml"/></Relationships>',
        "ppt/slides/slide1.xml": f'<p:sld xmlns:p="{p}" xmlns:a="{a}"><p:cSld><p:spTree>'
            + shape + '</p:spTree></p:cSld></p:sld>',
    }
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as package:
        for name, body in parts.items():
            package.writestr(name, body)
    return raw.getvalue()


def test_native_frame_uses_emu_geometry_insets_and_nested_group_transform(tmp_path):
    source = tmp_path / "synthetic.pptx"
    source.write_bytes(native_package(group=True, explicit_break=True))
    shape = read_native_frames(source, 1, 960, 540)[0][0]
    assert shape.shape_id == 19
    assert shape.text == "Consumertools" and shape.explicit_breaks == {8}
    assert shape.box == (200, 160, 600, 360)
    assert shape.usable_width == 380
    assert shape.title is False  # transformed frame is outside title area


def test_native_source_preserves_spaces_as_legal_word_wrap_boundaries(tmp_path):
    source = tmp_path / "synthetic.pptx"
    source.write_bytes(native_package(whitespace=True))
    shape = read_native_frames(source, 1, 960, 540)[0][0]
    assert shape.text == "Projectnotes" and shape.explicit_breaks == set()
    assert shape.word_breaks == {7}


def test_real_pinned_pdf_reader_measures_characters_and_exact_report_binding(tmp_path):
    import fitz
    source, pdf = tmp_path / "synthetic.pptx", tmp_path / "synthetic.pdf"
    source.write_bytes(native_package())
    with fitz.open() as document:
        page = document.new_page(width=960, height=540)
        page.insert_text((55, 60), "Consumer", fontsize=24)
        document.save(pdf)
    glyphs, engine = read_pdf_glyphs(pdf, 1, 960, 540)
    assert "".join(c.text for c in glyphs[0]) == "Consumer"
    assert engine["name"] == "PyMuPDF" and engine["version"]
    binding = {"controller_nonce": "nonce", "pptx_sha256": "a" * 64, "pdf_sha256": "b" * 64}
    report = verify_text_layout(source, pdf, {"width_emu": 12192000, "height_emu": 6858000, "slide_count": 1}, binding)
    assert report["binding"] == binding and report["status"] == "passed"
    assert report["coverage"]["checked_text_shapes"] == 1


def test_invalid_pdf_cannot_turn_raster_success_into_text_layout_pass(tmp_path):
    source, pdf = tmp_path / "synthetic.pptx", tmp_path / "synthetic.pdf"
    source.write_bytes(native_package())
    pdf.write_bytes(b"%PDF-1.5\nnot an actual PDF")
    with pytest.raises(ControlError, match="final_pptx_text_extraction_failed"):
        verify_text_layout(source, pdf, {"width_emu": 12192000, "height_emu": 6858000, "slide_count": 1}, {})
