"""Conservative layout checks on the exact Office-rendered PDF and native deck.

The PDF supplies rendered glyph positions; source SVG metrics, PNG dimensions,
and declared text frames cannot stand in for this measurement. These bounded
checks reject clear defects, but never constitute human visual acceptance.
Unsupported layouts are reported as coverage limits instead of guessed passes.
"""
from __future__ import annotations

from dataclasses import dataclass
import bisect
import math
import posixpath
import re
import statistics
import unicodedata
import xml.etree.ElementTree as ET
import zipfile

from .execution import ControlError

SCHEMA = "ppt-service.final-text-layout.v1"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_EMU = 12700.0  # points, matching PDF coordinates
_MAX_GLYPHS = 40000
_MAX_SHAPES = 2000
_MAX_ISSUES = 100
_MAX_COLLISION_SHAPE_PAIRS = 20000
_MAX_COLLISION_GLYPH_PAIRS = 200000
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_./:+#@-]{2,31}")
_CJK = re.compile(r"[\u3400-\u9fff]")


def _require(value, code="final_pptx_text_extraction_failed"):
    if not value:
        raise ControlError(code)


def _compact(text):
    return "".join(c for c in unicodedata.normalize("NFKC", text)
                   if not c.isspace() and c not in "\u200b\u200c\u200d\ufeff\u00ad")


def _round_box(box):
    return [round(v, 2) for v in box]


def _union(boxes):
    boxes = list(boxes)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _intersection(a, b):
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])), max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _area(box):
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


@dataclass(frozen=True)
class Glyph:
    text: str
    box: tuple[float, float, float, float]
    origin: tuple[float, float]
    size: float
    line: int

    @property
    def ink_box(self):
        # PDF character boxes include line-leading and side-bearing whitespace.
        # Intersect a conservative em region to avoid declaring two normally
        # spaced baselines a collision solely because their font boxes touch.
        x0, y0, x1, y1 = self.box
        inset = min(self.size * .045, max(0.0, (x1 - x0) * .12))
        return (x0 + inset, max(y0, self.origin[1] - self.size * .88),
                x1 - inset, min(y1, self.origin[1] + self.size * .14))


@dataclass
class TextFrame:
    shape_id: int
    text: str
    explicit_breaks: frozenset[int]
    box: tuple[float, float, float, float] | None
    usable_width: float | None
    title: bool
    geometry_limit: str | None = None
    word_breaks: frozenset[int] = frozenset()


def _integer(node, key, default=0):
    try:
        return int(node.get(key, default)) if node is not None else default
    except (ValueError, TypeError):
        raise ControlError("final_pptx_text_extraction_failed") from None


def _frame_text(body):
    parts, breaks, words, offset = [], set(), set(), 0
    for paragraph in body.findall(_A + "p"):
        if parts:
            breaks.add(offset)
        for child in paragraph:
            if child.tag == _A + "br":
                breaks.add(offset)
            elif child.tag in (_A + "r", _A + "fld"):
                raw = "".join(n.text or "" for n in child.iter(_A + "t"))
                text = []
                for character in unicodedata.normalize("NFKC", raw):
                    if character.isspace():
                        # Whitespace is also a legitimate natural word-wrap
                        # boundary; stripping it for matching must not turn an
                        # ordinary English sentence into one unbreakable token.
                        words.add(offset + len(text))
                    else:
                        text.extend(_compact(character))
                parts.append("".join(text))
                offset += len(text)
    return "".join(parts), frozenset(breaks), frozenset(words)


def _shape_frame(shape, transform, ordinal, height, inherited_limit):
    body = shape.find(_P + "txBody")
    if body is None:
        return None
    text, breaks, words = _frame_text(body)
    if not text:
        return None
    _require(len(text) <= 10000)
    props = shape.find(_P + "nvSpPr/" + _P + "cNvPr")
    if props is not None and props.get("hidden") in ("1", "true"):
        return None
    raw_id = _integer(props, "id", ordinal)
    shape_id = raw_id if 0 < raw_id <= 2**31 - 1 else ordinal
    xfrm = shape.find(_P + "spPr/" + _A + "xfrm")
    off, ext = ((xfrm.find(_A + "off"), xfrm.find(_A + "ext"))
                if xfrm is not None else (None, None))
    limit = inherited_limit
    body_pr = body.find(_A + "bodyPr")
    if body_pr is not None and body_pr.get("vert", "horz") != "horz":
        limit = "vertical_text_not_checked"
    if xfrm is not None and (_integer(xfrm, "rot") % 21600000
                            or xfrm.get("flipH") in ("1", "true")
                            or xfrm.get("flipV") in ("1", "true")):
        limit = "rotated_text_not_checked"
    if off is None or ext is None:
        box, usable = None, None
        limit = limit or "inherited_text_frame_not_checked"
    else:
        sx, sy, tx, ty = transform
        x, y = _integer(off, "x") * sx + tx, _integer(off, "y") * sy + ty
        w, h = _integer(ext, "cx") * sx, _integer(ext, "cy") * sy
        if w <= 0 or h <= 0:
            box, usable = None, None
            limit = limit or "nonpositive_text_frame_not_checked"
        else:
            box = (x / _EMU, y / _EMU, (x + w) / _EMU, (y + h) / _EMU)
            usable = max(0.0, (w - (_integer(body_pr, "lIns", 91440)
                                     + _integer(body_pr, "rIns", 91440)) * sx) / _EMU)
    ph = shape.find(_P + "nvSpPr/" + _P + "nvPr/" + _P + "ph")
    sizes = [_integer(node, "sz") / 100 for node in body.iter()
             if node.tag in (_A + "rPr", _A + "defRPr") and node.get("sz")]
    title = (ph is not None and ph.get("type") in ("title", "ctrTitle")) or bool(
        box and box[1] < height * .28 and sizes and max(sizes) >= 24)
    return TextFrame(shape_id, text, breaks, box, usable, title, limit, words)


def read_native_frames(pptx, pages, width, height):
    """Read native text geometry, including unrotated nested group transforms."""
    result = []
    with zipfile.ZipFile(pptx) as package:
        presentation = ET.fromstring(package.read("ppt/presentation.xml"))
        rels = ET.fromstring(package.read("ppt/_rels/presentation.xml.rels"))
        targets = {n.get("Id"): posixpath.normpath("ppt/" + n.get("Target", ""))
                   for n in rels if n.get("Type") == _R[1:-1] + "/slide"
                   and n.get("TargetMode", "Internal") == "Internal"}
        names = [targets.get(n.get(_R + "id")) for n in
                 presentation.findall(_P + "sldIdLst/" + _P + "sldId")]
        _require(len(names) == pages and all(n and n.startswith("ppt/slides/") for n in names))
        for name in names:
            tree = ET.fromstring(package.read(name)).find(_P + "cSld/" + _P + "spTree")
            frames = []

            def walk(parent, transform=(1.0, 1.0, 0.0, 0.0), limit=None, depth=0):
                _require(depth <= 16)
                if parent is None:
                    return
                for child in parent:
                    if child.tag == _P + "sp":
                        frame = _shape_frame(child, transform, len(frames) + 1, height, limit)
                        if frame:
                            frames.append(frame)
                        _require(len(frames) <= _MAX_SHAPES)
                    elif child.tag == _P + "grpSp":
                        xf = child.find(_P + "grpSpPr/" + _A + "xfrm")
                        off, ext, choff, chext = [xf.find(_A + n) if xf is not None else None
                                                for n in ("off", "ext", "chOff", "chExt")]
                        next_limit = limit
                        if any(n is None for n in (off, ext, choff, chext)):
                            next_limit = next_limit or "group_transform_not_checked"
                            walk(child, transform, next_limit, depth + 1)
                            continue
                        if (_integer(xf, "rot") % 21600000 or xf.get("flipH") in ("1", "true")
                                or xf.get("flipV") in ("1", "true")):
                            next_limit = next_limit or "rotated_group_not_checked"
                        cw, ch = _integer(chext, "cx"), _integer(chext, "cy")
                        _require(cw > 0 and ch > 0)
                        sx, sy, tx, ty = transform
                        gsx, gsy = _integer(ext, "cx") / cw, _integer(ext, "cy") / ch
                        transformed = (sx * gsx, sy * gsy,
                            tx + sx * (_integer(off, "x") - _integer(choff, "x") * gsx),
                            ty + sy * (_integer(off, "y") - _integer(choff, "y") * gsy))
                        walk(child, transformed, next_limit, depth + 1)

            walk(tree)
            result.append(frames)
    return result


def read_pdf_glyphs(pdf, pages, width, height):
    """Use the already pinned PyMuPDF; never install or execute project code."""
    try:
        import fitz
        result = []
        with fitz.open(pdf) as document:
            _require(not document.needs_pass and len(document) == pages)
            for page in document:
                _require(abs(page.rect.width - width) <= 1 and abs(page.rect.height - height) <= 1)
                glyphs, line_id = [], 0
                data = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE,
                                     clip=fitz.INFINITE_RECT())
                for block in data["blocks"]:
                    if block.get("type") != 0:
                        continue
                    for line in block.get("lines", []):
                        line_id += 1
                        for span in line.get("spans", []):
                            size = float(span["size"])
                            _require(math.isfinite(size) and 0 < size < 10000)
                            for char in span.get("chars", []):
                                box, origin = tuple(char["bbox"]), tuple(char["origin"])
                                _require(len(box) == 4 and len(origin) == 2
                                    and all(math.isfinite(v) and abs(v) < 100000 for v in (*box, *origin))
                                    and box[2] >= box[0] and box[3] >= box[1])
                                for letter in _compact(char["c"]):
                                    glyphs.append(Glyph(letter, box, origin, size, line_id))
                                _require(len(glyphs) <= _MAX_GLYPHS)
                result.append(glyphs)
        return result, {"name": "PyMuPDF", "version": str(fitz.VersionBind)}
    except ControlError:
        raise
    except (ImportError, OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError, OverflowError):
        raise ControlError("final_pptx_text_extraction_failed") from None


def _score(frame, chars):
    if frame.box is None:
        return None
    actual = _union(g.box for g in chars)
    x, y = (actual[0] + actual[2]) / 2, (actual[1] + actual[3]) / 2
    fx, fy = ((frame.box[0] + frame.box[2]) / 2, (frame.box[1] + frame.box[3]) / 2)
    # A rendered run may overflow its own frame; allow a bounded halo rather
    # than requiring containment, which would hide precisely that defect.
    halo = max(24.0, statistics.median(g.size for g in chars) * 2)
    dx = max(frame.box[0] - x, 0.0, x - frame.box[2])
    dy = max(frame.box[1] - y, 0.0, y - frame.box[3])
    if dx > halo or dy > halo:
        return None
    return math.hypot(x - fx, y - fy)


def _match_frames(frames, glyphs):
    sequence = "".join(g.text for g in glyphs)
    options = []
    for index, frame in enumerate(frames):
        if frame.geometry_limit or frame.box is None:
            continue
        start, occurrences = 0, []
        while len(occurrences) <= 64:
            found = sequence.find(frame.text, start)
            if found < 0:
                break
            end = found + len(frame.text)
            score = _score(frame, glyphs[found:end])
            if score is not None:
                occurrences.append((score, found, end))
            start = found + 1
        if 0 < len(occurrences) <= 64:
            occurrences.sort()
            options.append((len(occurrences), len(frame.text) * -1, index, occurrences))
    matched, used = {}, set()
    for _, _, index, occurrences in sorted(options):
        available = [row for row in occurrences if not any(n in used for n in range(row[1], row[2]))]
        # Equal-distance repeated labels cannot be safely attributed to a frame.
        if not available or (len(available) > 1 and abs(available[0][0] - available[1][0]) < .5):
            continue
        _, start, end = available[0]
        matched[index] = glyphs[start:end]
        used.update(range(start, end))
    return matched


_MESSAGES = {
    "short_token_unplanned_wrap": ("短词在最终 PPTX 中被拆成多行。", "加宽对应文本框、减小字距或显式设计换行，避免拆开短词。"),
    "short_token_orphan_fragment": ("短词末尾仅一个字母落到下一行。", "合并被拆开的短词并加宽文本框；导出成显式换行仍不能修复孤立字母。"),
    "short_label_orphan_line": ("短标签末尾仅一个字被挤到下一行。", "加宽对应文本框，或明确调整标签的行分布。"),
    "text_outside_frame": ("最终渲染的文字超出了原生文本框。", "调整文本框宽高、内边距或字号，并检查最终 PPTX 的实际排版。"),
    "text_outside_slide": ("最终渲染的文字超出了页面画布。", "移动或缩小对应文字区域，确保最终文字完整位于页面内。"),
    "text_collision": ("两个独立文本区域的实际字形发生明显重叠。", "调整对应文本框的位置、宽高或字号，给标题与正文留出间距。"),
}


def _issue(code, page, frame, **values):
    title, suggestion = _MESSAGES[code]
    return {"code": code, "severity": "error", "page": page,
            "shape_id": frame.shape_id, "message": title, "suggestion": suggestion,
            "frame_pt": _round_box(frame.box), **values}


def _outside(actual, frame, tolerance):
    return (actual[0] < frame[0] - tolerance or actual[1] < frame[1] - tolerance
            or actual[2] > frame[2] + tolerance or actual[3] > frame[3] + tolerance)


def _line_transitions(glyphs):
    # A real horizontal wrap moves down and returns toward the line start.
    # Baseline-shifted math/superscript style runs can create separate PDF line
    # records while continuing to advance right; they are not broken words.
    return {i for i in range(1, len(glyphs))
            if glyphs[i].line != glyphs[i - 1].line
            and glyphs[i].origin[1] - glyphs[i - 1].origin[1] > min(glyphs[i].size, glyphs[i-1].size) * .4
            and glyphs[i].origin[0] < glyphs[i-1].origin[0] + min(glyphs[i].size, glyphs[i-1].size) * .3}


def _wrap_issue(frame, glyphs):
    transitions = _line_transitions(glyphs)
    unexpected = transitions - frame.explicit_breaks - frame.word_breaks
    for token in _TOKEN.finditer(frame.text):
        if any(token.start() < i < token.end() for i in unexpected):
            return "short_token_unplanned_wrap"
        # A source/exporter can bake a bad automatic wrap into a:br. A single
        # trailing letter in a short identifier remains a clear defect even
        # then. Genuine whitespace word boundaries and separator-delimited
        # components stay exempt; balanced intentional hard breaks also remain.
        if (token.end() - token.start() <= 24 and token.end() - 1 in transitions
                and token.end() - 1 not in frame.word_breaks
                and frame.text[token.end() - 1].isalpha()
                and frame.text[token.end() - 2].isalnum()):
            return "short_token_orphan_fragment"
    # Only short labels; ordinary prose, explicit poetic/list line breaks, and
    # punctuation-only tails do not qualify as a one-character orphan defect.
    if 2 <= len(frame.text) <= 14 and unexpected:
        boundaries = [0, *sorted(transitions), len(glyphs)]
        final = frame.text[boundaries[-2]:]
        if len(final) == 1 and _CJK.fullmatch(final) and boundaries[-2] in unexpected:
            return "short_label_orphan_line"
    return None


class _CollisionBudgetExceeded(Exception):
    pass


def _collision(a, b, budget):
    ab, bb = _union(g.ink_box for g in a), _union(g.ink_box for g in b)
    if not all(_intersection(ab, bb)):
        return None
    ordered = sorted(b, key=lambda glyph: glyph.ink_box[0])
    starts = [glyph.ink_box[0] for glyph in ordered]
    widest = max(g.ink_box[2] - g.ink_box[0] for g in ordered)
    for ga in a:
        left = bisect.bisect_left(starts, ga.ink_box[0] - widest)
        right = bisect.bisect_left(starts, ga.ink_box[2])
        for gb in ordered[left:right]:
            budget["glyph_pairs"] += 1
            if budget["glyph_pairs"] > _MAX_COLLISION_GLYPH_PAIRS:
                raise _CollisionBudgetExceeded
            # Glyph boxes are advances rather than outline masks. A sizeable
            # intersection on both axes is required, not mere line-box contact.
            w, h = _intersection(ga.ink_box, gb.ink_box)
            size = min(ga.size, gb.size)
            if (w > size * .16 and h > size * .20
                    and w * h > min(_area(ga.ink_box), _area(gb.ink_box)) * .18):
                return _union((ga.ink_box, gb.ink_box))
    return None


def analyze_text_layout(frames_by_page, glyphs_by_page, width, height, *, engine=None):
    """Pure measured-geometry policy, separated for meaningful hostile fixtures."""
    _require(len(frames_by_page) == len(glyphs_by_page))
    issues, advisories, pages = [], [], []
    issue_count = 0
    totals = {"native_text_shapes": 0, "matched_text_shapes": 0, "checked_text_shapes": 0,
              "rendered_glyphs": 0, "geometry_limited_shapes": 0, "unmatched_text_shapes": 0,
              "collision_shape_pairs": 0, "collision_glyph_pairs": 0}

    def add_issue(value):
        nonlocal issue_count
        issue_count += 1
        if len(issues) < _MAX_ISSUES:
            issues.append(value)

    for ordinal, (frames, glyphs) in enumerate(zip(frames_by_page, glyphs_by_page), 1):
        matched = _match_frames(frames, glyphs)
        start_count = issue_count
        totals["native_text_shapes"] += len(frames)
        totals["matched_text_shapes"] += len(matched)
        totals["rendered_glyphs"] += len(glyphs)
        for index, frame in enumerate(frames):
            if frame.geometry_limit:
                totals["geometry_limited_shapes"] += 1
                advisories.append({"code": frame.geometry_limit, "page": ordinal, "shape_id": frame.shape_id})
                continue
            chars = matched.get(index)
            if not chars:
                totals["unmatched_text_shapes"] += 1
                advisories.append({"code": "rendered_text_not_uniquely_matched", "page": ordinal, "shape_id": frame.shape_id})
                continue
            totals["checked_text_shapes"] += 1
            actual = _union(g.ink_box for g in chars)
            size = statistics.median(g.size for g in chars)
            details = {"rendered_bounds_pt": _round_box(actual), "font_size_pt": round(size, 2),
                       "usable_frame_width_pt": round(frame.usable_width, 2),
                       "rendered_line_count": 1 + len(_line_transitions(chars))}
            wrap_code = _wrap_issue(frame, chars)
            if wrap_code:
                add_issue(_issue(wrap_code, ordinal, frame, **details))
            # Accommodate small glyph overshoots and PDF/font rounding; a
            # complete extra line or a visibly overlong label still rejects.
            if _outside(actual, frame.box, max(2.0, size * .16)):
                add_issue(_issue("text_outside_frame", ordinal, frame, **details))
            if _outside(actual, (0, 0, width, height), 1.5):
                add_issue(_issue("text_outside_slide", ordinal, frame, **details))
        actual_bounds = {index: _union(g.ink_box for g in chars) for index, chars in matched.items()}
        indices = sorted(matched, key=lambda index: actual_bounds[index][0])
        budget = {"shape_pairs": 0, "glyph_pairs": 0}
        try:
            for n, left in enumerate(indices):
                for right in indices[n + 1:]:
                    if actual_bounds[right][0] >= actual_bounds[left][2]:
                        break
                    budget["shape_pairs"] += 1
                    if budget["shape_pairs"] > _MAX_COLLISION_SHAPE_PAIRS:
                        raise _CollisionBudgetExceeded
                    if not all(_intersection(actual_bounds[left], actual_bounds[right])):
                        continue
                    a, b = frames[left], frames[right]
                    # Identical near-coincident duplicate text can be an intentional
                    # shadow/outline treatment. It is a disclosed coverage limit.
                    if (a.text == b.text and max(abs(x - y) for x, y in zip(a.box, b.box)) <= 1.5):
                        advisories.append({"code": "identical_text_overlay_not_checked", "page": ordinal,
                                           "shape_id": a.shape_id, "other_shape_id": b.shape_id})
                        continue
                    contact = _collision(matched[left], matched[right], budget)
                    if contact:
                        add_issue(_issue("text_collision", ordinal, a,
                            other_shape_id=b.shape_id, other_frame_pt=_round_box(b.box),
                            contact_bounds_pt=_round_box(contact),
                            title_body_collision=a.title != b.title))
        except _CollisionBudgetExceeded:
            advisories.append({"code": "collision_comparison_budget_exhausted", "page": ordinal,
                               "shape_pair_limit": _MAX_COLLISION_SHAPE_PAIRS,
                               "glyph_pair_limit": _MAX_COLLISION_GLYPH_PAIRS})
        totals["collision_shape_pairs"] += budget["shape_pairs"]
        totals["collision_glyph_pairs"] += budget["glyph_pairs"]
        pages.append({"ordinal": ordinal, "native_text_shapes": len(frames),
                      "matched_text_shapes": len(matched), "issue_count": issue_count - start_count})
        _require(len(advisories) <= 40000,
                 "final_pptx_text_layout_limit_exceeded")
    status = "failed" if issues else ("incomplete" if advisories else "passed")
    return {"schema": SCHEMA, "status": status, "publication_authorized": False,
            "human_visual_acceptance": False, "powerpoint_acceptance": "pending",
            "method": "final-pdf-glyphs-and-native-pptx-frames", "engine": engine or {},
            "scope": "conservative_text_geometry_checks_only", "coverage": totals,
            "pages": pages, "issues": issues, "issue_count": issue_count,
            "issues_truncated": issue_count > len(issues), "advisories": advisories}


def verify_text_layout(pptx, pdf, deck, binding):
    width, height = deck["width_emu"] / _EMU, deck["height_emu"] / _EMU
    try:
        frames = read_native_frames(pptx, deck["slide_count"], width, height)
        glyphs, engine = read_pdf_glyphs(pdf, deck["slide_count"], width, height)
        report = analyze_text_layout(frames, glyphs, width, height, engine=engine)
    except ControlError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ET.ParseError, zipfile.BadZipFile):
        raise ControlError("final_pptx_text_extraction_failed") from None
    report["binding"] = dict(binding)
    return report
