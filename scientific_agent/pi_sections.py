"""Deterministic Markdown section ranges over the original UTF-8 report bytes.

The index is read-only. It never normalizes line endings or rewrites headings.
Parent sections include their children; preamble and top-level ranges together
cover the report. Generated IDs are bound to the complete report and exact range.
"""
from __future__ import annotations

import hashlib
import re


MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_SECTIONS = 2000
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_LINES = re.compile(rb"[^\r\n]*(?:\r\n|\r|\n|$)")
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?)|[ \t]*)$")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_LIST = re.compile(r"^ {0,3}(?:[-+*]|\d{1,9}[.)])(?:[ \t]+|$)")
_THEMATIC = re.compile(r"^ {0,3}(?:(?:\*[ \t]*){3,}|(?:_[ \t]*){3,}|(?:-[ \t]*){3,})$")
_HTML_RAW = re.compile(r"^ {0,3}<(script|pre|style|textarea)(?:[ \t>]|$)", re.I)
_HTML_TAG = re.compile(r"^ {0,3}</?(?:address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|h[1-6]|head|header|hr|html|iframe|legend|li|link|main|menu|menuitem|nav|noframes|ol|optgroup|option|p|param|search|section|summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul)(?:[ \t/>]|$)", re.I)
_MESSAGES = {
    "invalid_report": "Report must be valid UTF-8 text.",
    "report_too_large": "Report exceeds the section indexing size limit.",
    "too_many_sections": "Report contains too many sections.",
    "invalid_base_sha256": "A valid report SHA256 is required.",
    "invalid_section_id": "A section ID is required.",
    "stale_report": "The report no longer matches the selected base.",
    "section_not_found": "The selected section does not belong to this report.",
}


class SectionError(ValueError):
    def __init__(self, code: str, message: str | None = None, status: int = 422):
        self.code = code
        self.message = message or _MESSAGES.get(code, "Section selection is unavailable.")
        self.status = self.status_code = status
        super().__init__(self.message)


def _report_bytes(report):
    if not isinstance(report, str):
        raise SectionError("invalid_report")
    if len(report) > MAX_REPORT_BYTES:
        raise SectionError("report_too_large")
    try:
        raw = report.encode("utf-8")
    except UnicodeError as exc:
        raise SectionError("invalid_report") from exc
    if len(raw) > MAX_REPORT_BYTES:
        raise SectionError("report_too_large")
    return raw


def _indented(line):
    # A tab advances to the next four-column stop, including after 1-3 spaces.
    columns = 0
    for character in line:
        if character == " ":
            columns += 1
        elif character == "\t":
            columns += 4 - columns % 4
        else:
            break
        if columns >= 4:
            return True
    return False


def _headings(raw):
    headings = []
    fence = None
    html_end = None
    html_until_blank = False
    paragraph_start = None
    paragraph = []
    for match in _LINES.finditer(raw):
        if match.start() == match.end():
            continue
        line = match.group().rstrip(b"\r\n").decode("utf-8")
        if match.start() == 0:
            line = line.removeprefix("\ufeff")
        if fence is not None:
            token, minimum = fence
            closing = line.lstrip(" ")
            indent = len(line) - len(closing)
            run = len(closing) - len(closing.lstrip(token))
            if indent <= 3 and run >= minimum and not closing[run:].strip(" \t"):
                fence = None
            continue
        if html_end is not None:
            if html_end.search(line):
                html_end = None
            continue
        if html_until_blank:
            if not line.strip():
                html_until_blank = False
            continue
        opening = _FENCE.match(line)
        if opening and (opening[1][0] != "`" or "`" not in opening[2]):
            fence = (opening[1][0], len(opening[1]))
            paragraph_start, paragraph = None, []
            continue
        raw_html = _HTML_RAW.match(line)
        if raw_html:
            end = re.compile(r"</" + raw_html[1] + r"[ \t]*>", re.I)
            html_end = None if end.search(line) else end
            paragraph_start, paragraph = None, []
            continue
        stripped = line.lstrip(" ")
        if len(line) - len(stripped) <= 3:
            html_markers = (("<!--", r"-->"), ("<?", r"\?>"), ("<![CDATA[", r"\]\]>"))
            marker = next(((prefix, end) for prefix, end in html_markers if stripped.startswith(prefix)), None)
            if marker:
                end = re.compile(marker[1])
                html_end = None if end.search(stripped[len(marker[0]):]) else end
                paragraph_start, paragraph = None, []
                continue
            if _HTML_TAG.match(line):
                html_until_blank = True
                paragraph_start, paragraph = None, []
                continue
        if not line.strip() or _indented(line) or stripped.startswith(">"):
            paragraph_start, paragraph = None, []
            continue
        atx = _ATX.match(line)
        if atx:
            title = atx[2] or ""
            # The whitespace after the opening sequence was consumed by the
            # matcher, so an all-hash body is also an empty closing sequence.
            title = "" if re.fullmatch(r"#+[ \t]*", title) else re.sub(r"[ \t]+#+[ \t]*$", "", title).strip()
            headings.append((match.start(), " ".join(title.split()) or "未命名章节", len(atx[1])))
            paragraph_start, paragraph = None, []
        else:
            underline = _SETEXT.match(line)
            if underline and paragraph_start is not None:
                headings.append((paragraph_start, " ".join(" ".join(paragraph).split()),
                                 1 if underline[1][0] == "=" else 2))
                paragraph_start, paragraph = None, []
            elif underline or _THEMATIC.match(line) or _LIST.match(line):
                paragraph_start, paragraph = None, []
            else:
                if paragraph_start is None:
                    paragraph_start = match.start()
                paragraph.append(line.strip())
        if len(headings) > MAX_SECTIONS:
            raise SectionError("too_many_sections")
    return headings


def _entry(raw, report_digest, start, end, title, level, kind):
    content = raw[start:end]
    digest = hashlib.sha256(content).hexdigest()
    identity = f"scientific-section/1\n{report_digest}\n{start}\n{end}\n{digest}".encode("ascii")
    return {
        "section_id": "section_" + hashlib.sha256(identity).hexdigest(),
        "title": title, "level": level, "kind": kind,
        "start_byte": start, "end_byte": end, "sha256": digest,
        "editable": bool(content.decode("utf-8").strip()),
    }


def index_sections(report: str) -> dict:
    """Index ATX/Setext sections without changing any report bytes.

    Blank lines, fenced/indented code, block quotes, list markers and HTML
    blocks do not create headings. Setext titles may span multiple paragraph
    lines. Parent sections end before the next heading of the same/higher level.
    """
    raw = _report_bytes(report)
    report_digest = hashlib.sha256(raw).hexdigest()
    headings = _headings(raw)
    if not headings:
        entries = [_entry(raw, report_digest, 0, len(raw), "正文", 0, "preamble")]
    else:
        if len(headings) + (headings[0][0] > 0) > MAX_SECTIONS:
            raise SectionError("too_many_sections")
        ends = [len(raw)] * len(headings)
        stack = []
        for position, (start, _, level) in enumerate(headings):
            while stack and headings[stack[-1]][2] >= level:
                ends[stack.pop()] = start
            stack.append(position)
        entries = []
        if headings[0][0] > 0:
            entries.append(_entry(raw, report_digest, 0, headings[0][0], "前言", 0, "preamble"))
        entries.extend(_entry(raw, report_digest, start, ends[position], title, level, "section")
                       for position, (start, title, level) in enumerate(headings))
    return {"schema_version": "1", "report_sha256": report_digest, "sections": entries}


def select_section(report: str, section_id: str, base_sha256: str) -> dict:
    """Select the exact original range, rejecting stale or foreign selections."""
    if not isinstance(base_sha256, str) or not _SHA.fullmatch(base_sha256):
        raise SectionError("invalid_base_sha256")
    if not isinstance(section_id, str) or not section_id:
        raise SectionError("invalid_section_id")
    raw = _report_bytes(report)
    if hashlib.sha256(raw).hexdigest() != base_sha256:
        raise SectionError("stale_report", status=409)
    index = index_sections(report)
    entry = next((item for item in index["sections"] if item["section_id"] == section_id), None)
    if entry is None:
        raise SectionError("section_not_found", status=409)
    return dict(entry, text=raw[entry["start_byte"]:entry["end_byte"]].decode("utf-8"))
