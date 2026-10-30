"""Offline, bounded validation and exact-range composition of Pi revisions.

Only recorded evidence is available here. These checks establish transfer
integrity, section isolation and recorded citation relationships, not scientific
accuracy. No source URL or full_text_path is opened by this module.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
import hashlib
import json
import re

from .pi_citations import (TRACE_ARTIFACTS, MAX_TRACE_BYTES, MAX_RECORDS,
                           TraceUnavailable, _citation_numbers, _claim, _rows,
                           build_citation_trace)
from .pi_sections import (MAX_REPORT_BYTES, _HTML_RAW, _HTML_TAG,
                          _indented, index_sections, select_section)


VALIDATOR_VERSION = "recorded-evidence-revision/1.0.0"
MAX_OUTPUT_BYTES = MAX_REPORT_BYTES + MAX_TRACE_BYTES
_MARKER = re.compile(rb"<!-- claim:([^<>\r\n]+?) -->")
_LINES = re.compile(rb"[^\r\n]*(?:\r\n|\r|\n|$)")
_NL = rb"(?:\r\n|\r(?!\n)|(?<!\r)\n)"
_BLANK = re.compile(_NL + rb"[ \t]*" + _NL)
_FENCE = re.compile(rb"^ {0,3}(`{3,}|~{3,})(.*)$")
_LIST = re.compile(rb"^ {0,3}(?:[-+*]|\d{1,9}[.)])(?:[ \t]+|$)")
_REFERENCE_TITLE = re.compile(r"\breferences?\b|\bbibliography\b|参考文献", re.I)
_MESSAGES = {
    "invalid_revision_output": "Revision output does not match the supported schema.",
    "revision_too_large": "Revision exceeds the supported size limit.",
    "invalid_revision_input": "The fixed report and evidence records cannot be revised safely.",
    "invalid_claim_markers": "Claim markers must identify unique body paragraphs.",
    "claim_boundary_conflict": "A claim paragraph crosses the selected section boundary.",
    "references_not_editable": "Reference sections cannot be changed by a recorded-evidence revision.",
    "unknown_citation": "A numbered citation is absent from the fixed references.",
    "claim_citation_mismatch": "A claim paragraph must cite each recorded evidence paper.",
    "section_structure_conflict": "The revision changes structure outside the selected section.",
    "unsupported_assessment_upgrade": "Recorded uncertain evidence cannot be upgraded without new evidence.",
    "missing_evidence_disclosure": "The report must disclose recorded evidence and access limitations.",
    "candidate_mismatch": "The candidate does not match the validated section revision.",
}


class RevisionValidationError(ValueError):
    """A public error code without report content, paths or upstream diagnostics."""
    def __init__(self, code, status=422):
        self.code = code
        self.status = self.status_code = status
        self.message = _MESSAGES.get(code, "The section revision could not be validated.")
        super().__init__(self.message)


def _require(condition, code="invalid_revision_output"):
    if not condition:
        raise RevisionValidationError(code)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _json(raw):
    try:
        return json.loads(raw, object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(
                              RevisionValidationError("invalid_revision_output")))
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, RevisionValidationError):
            raise
        raise RevisionValidationError("invalid_revision_output") from exc


def _wire(value):
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise RevisionValidationError("invalid_revision_output") from exc


def parse_revision_output(raw):
    """Parse the sole model output, rejecting duplicate keys and extra fields."""
    if isinstance(raw, bytes):
        _require(len(raw) <= MAX_OUTPUT_BYTES, "revision_too_large")
        try:
            raw = raw.decode("utf-8")
        except UnicodeError as exc:
            raise RevisionValidationError("invalid_revision_output") from exc
    if isinstance(raw, str):
        _require(len(raw) <= MAX_OUTPUT_BYTES, "revision_too_large")
        try:
            _require(len(raw.encode("utf-8")) <= MAX_OUTPUT_BYTES, "revision_too_large")
        except UnicodeError as exc:
            raise RevisionValidationError("invalid_revision_output") from exc
        raw = _json(raw)
    _require(isinstance(raw, dict) and set(raw) == {
        "schema_version", "section_markdown", "claims"})
    _require(raw["schema_version"] == "1")
    section, claims = raw["section_markdown"], raw["claims"]
    _require(isinstance(section, str) and bool(section.strip()))
    _require(len(section) <= MAX_REPORT_BYTES, "revision_too_large")
    try:
        _require(len(section.encode("utf-8")) <= MAX_REPORT_BYTES,
                 "revision_too_large")
    except UnicodeError as exc:
        raise RevisionValidationError("invalid_revision_output") from exc
    _require(isinstance(claims, list) and len(claims) <= MAX_RECORDS)
    seen = set()
    for row in claims:
        try:
            _claim(row)
        except TraceUnavailable as exc:
            raise RevisionValidationError("invalid_revision_output") from exc
        _require(row["claim_id"] not in seen)
        seen.add(row["claim_id"])
    _require(len(_wire(raw)) <= MAX_OUTPUT_BYTES, "revision_too_large")
    # Copy input dictionaries so a caller cannot mutate a validated structure.
    return _json(_wire(raw))


def _opaque_ranges(raw):
    """Conservatively identify non-prose Markdown, including lazy list lines."""
    ranges, fence, html_end, html_blank, container = [], None, None, False, False
    for match in _LINES.finditer(raw):
        if match.start() == match.end():
            continue
        line = match.group().rstrip(b"\r\n")
        if match.start() == 0:
            line = line.removeprefix(b"\xef\xbb\xbf")
        text = line.decode("utf-8")
        hidden = False
        if fence:
            hidden = True
            closing = line.lstrip(b" ")
            count = len(closing) - len(closing.lstrip(fence[0]))
            if len(line) - len(closing) <= 3 and count >= fence[1] and not closing[count:].strip(b" \t"):
                fence = None
        elif html_end:
            hidden = True
            if html_end.search(text):
                html_end = None
        elif html_blank:
            hidden = True
            if not line.strip():
                html_blank = False
        else:
            opening = _FENCE.match(line)
            if opening and (opening[1][:1] != b"`" or b"`" not in opening[2]):
                fence, hidden = (opening[1][:1], len(opening[1])), True
                container = False
            elif _indented(text):
                hidden = True
            elif not line.strip():
                container = False
            elif container or line.lstrip(b" ").startswith(b">") or _LIST.match(line):
                container, hidden = True, True
            else:
                raw_html = _HTML_RAW.match(text)
                stripped = text.lstrip(" ")
                if raw_html:
                    end = re.compile(r"</" + raw_html[1] + r"[ \t]*>", re.I)
                    html_end, hidden = None if end.search(text) else end, True
                elif len(text) - len(stripped) <= 3:
                    if stripped.startswith("<!-- claim:") and _MARKER.match(line.lstrip(b" ")):
                        # A canonical marker is our sole permitted HTML block.
                        pass
                    else:
                        marker = next(((prefix, end) for prefix, end in (
                            ("<!--", r"-->"), ("<?", r"\?>"), ("<![CDATA[", r"\]\]>"))
                            if stripped.startswith(prefix)), None)
                        if marker:
                            end = re.compile(marker[1])
                            html_end, hidden = None if end.search(stripped[len(marker[0]):]) else end, True
                        elif _HTML_TAG.match(text) or re.fullmatch(r" {0,3}</?[A-Za-z][^<>]*>[ \t]*", text):
                            html_blank, hidden = True, True
        if hidden:
            ranges.append(match.span())
    # Inline code can span lines. Exclude already opaque blocks from matching.
    visible = bytearray(raw)
    for start, end in ranges:
        visible[start:end] = b" " * (end - start)
    runs = list(re.finditer(rb"`+", visible))
    by_length = {}
    for run in reversed(runs):
        by_length.setdefault(len(run[0]), []).append(run)
    used_until = -1
    for run in runs:
        if run.start() < used_until:
            continue
        preceding = run.start()
        while preceding and visible[preceding - 1] == 92:
            preceding -= 1
        if (run.start() - preceding) % 2:
            continue
        candidates = by_length[len(run[0])]
        while candidates and candidates[-1].start() <= run.start():
            candidates.pop()
        if candidates:
            used_until = candidates[-1].end()
            ranges.append((run.start(), used_until))
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def _claim_ranges(report, rows):
    raw = report.encode("utf-8")
    matches = list(_MARKER.finditer(raw))
    _require(len(matches) <= MAX_RECORDS, "invalid_claim_markers")
    _require(len(matches) == raw.count(b"<!-- claim:"), "invalid_claim_markers")
    observed = Counter(match[1].decode("utf-8") for match in matches)
    expected = {row["claim_id"]: row for row in rows}
    _require(set(observed) == set(expected) and all(count == 1 for count in observed.values()),
             "invalid_claim_markers")
    opaque = _opaque_ranges(raw)
    opaque_starts = [start for start, _ in opaque]
    headings = sorted({row["start_byte"] for row in index_sections(report)["sections"]
                       if row["kind"] == "section"})
    blank_starts = [match.start() for match in _BLANK.finditer(raw)]
    ranges = {}
    for position, match in enumerate(matches):
        claim_id = match[1].decode("utf-8")
        line_start = max(raw.rfind(b"\n", 0, match.start()), raw.rfind(b"\r", 0, match.start())) + 1
        prefix = raw[line_start:match.start()]
        if line_start == 0:
            prefix = prefix.removeprefix(b"\xef\xbb\xbf")
        _require(re.fullmatch(rb" {0,3}", prefix) is not None,
                 "invalid_claim_markers")
        opaque_position = bisect_right(opaque_starts, match.start()) - 1
        _require(opaque_position < 0 or opaque[opaque_position][1] <= match.start(),
                 "invalid_claim_markers")
        start = match.end()
        while start < len(raw) and raw[start] in b" \t\r\n":
            start += 1
        limits = [len(raw)]
        if position + 1 < len(matches):
            limits.append(matches[position + 1].start())
        heading_position = bisect_right(headings, match.start())
        if heading_position < len(headings):
            limits.append(headings[heading_position])
        blank_position = bisect_right(blank_starts, start - 1)
        if blank_position < len(blank_starts):
            limits.append(blank_starts[blank_position])
        end = min(limits)
        _require(start < end and bool(raw[start:end].strip()), "invalid_claim_markers")
        ranges[claim_id] = {"start_byte": match.start(), "end_byte": end,
                            "paragraph": raw[start:end].decode("utf-8")}
    return ranges


def _manifest(report, artifacts):
    files = dict(artifacts, report=report.encode("utf-8"))
    return [{"artifact_id": key, "size": len(files[key]),
             "sha256": hashlib.sha256(files[key]).hexdigest(),
             "media_type": "text/markdown" if key == "report" else
             "application/json" if key == "references" else "application/x-ndjson"}
            for key in ("report",) + TRACE_ARTIFACTS]


def _load(report, items, artifacts):
    try:
        _require(isinstance(artifacts, dict) and set(artifacts) == set(TRACE_ARTIFACTS),
                 "invalid_revision_input")
        trace = build_citation_trace(report, items, artifacts)
        _require(all(row["status"] == "resolved" for row in trace["citations"]),
                 "unknown_citation")
        parsed = {kind: _rows(kind, artifacts[kind]) for kind in TRACE_ARTIFACTS}
        ranges = _claim_ranges(report, parsed["claims"])
        evidence = {row["evidence_id"]: row for row in parsed["evidence"]}
        numbers = {row["paper_id"]: row["n"] for row in parsed["references"]}
        for row in parsed["claims"]:
            citations = _citation_numbers(ranges[row["claim_id"]]["paragraph"])
            _require(all(numbers[evidence[key]["paper_id"]] in citations
                         for key in row["evidence_ids"]), "claim_citation_mismatch")
    except TraceUnavailable as exc:
        raise RevisionValidationError("invalid_revision_input") from exc
    return parsed, ranges


def validate_revision_input(report, items, artifacts, section_id, base_sha256):
    """Validate a fixed snapshot and resolve its exact, non-bibliography range."""
    section = select_section(report, section_id, base_sha256)
    _require(section["editable"], "invalid_revision_input")
    start, end = section["start_byte"], section["end_byte"]
    _check_opaque_boundaries(report, start, end)
    for entry in index_sections(report)["sections"]:
        if _REFERENCE_TITLE.search(entry["title"]):
            _require(max(start, entry["start_byte"]) >= min(end, entry["end_byte"]),
                     "references_not_editable")
    parsed, ranges = _load(report, items, artifacts)
    target = []
    for row in parsed["claims"]:
        bounds = ranges[row["claim_id"]]
        a, b = bounds["start_byte"], bounds["end_byte"]
        if max(a, start) < min(b, end):
            _require(start <= a and b <= end, "claim_boundary_conflict")
            target.append(row["claim_id"])
    return {"section": section, "target_claim_ids": target,
            "material_policy": "recorded_evidence_only"}


def _check_structure(report, candidate, section):
    start, end = section["start_byte"], section["end_byte"]
    delta = len(candidate.encode("utf-8")) - len(report.encode("utf-8"))
    new_end = end + delta
    before, after = index_sections(report)["sections"], index_sections(candidate)["sections"]
    selected = [entry for entry in after if entry["start_byte"] == start and
                entry["end_byte"] == new_end and entry["kind"] == section["kind"] and
                entry["level"] == section["level"] and entry["title"] == section["title"]]
    _require(bool(selected), "section_structure_conflict")
    def identity(entry, transform=False):
        a, b = entry["start_byte"], entry["end_byte"]
        ancestor = a < start and b >= end
        if transform:
            a += delta if a >= end else 0
            b += delta if b > start else 0
        return (a, b, entry["title"], entry["level"], entry["kind"],
                None if ancestor else entry["sha256"])
    expected = [identity(entry, True) for entry in before
                if entry["start_byte"] < start or entry["start_byte"] >= end]
    observed = []
    for entry in after:
        if entry["start_byte"] < start or entry["start_byte"] >= new_end:
            ancestor = entry["start_byte"] < start and entry["end_byte"] >= new_end
            observed.append((entry["start_byte"], entry["end_byte"], entry["title"],
                             entry["level"], entry["kind"], None if ancestor else entry["sha256"]))
    _require(expected == observed, "section_structure_conflict")
    _check_opaque_boundaries(candidate, start, new_end)
    for entry in after:
        if _REFERENCE_TITLE.search(entry["title"]):
            _require(max(start, entry["start_byte"]) >= min(new_end, entry["end_byte"]),
                     "references_not_editable")


def _check_opaque_boundaries(report, start, end):
    # A multiline code span can swallow external prose without changing ATX
    # headings in our deliberately conservative section index.
    for a, b in _opaque_ranges(report.encode("utf-8")):
        _require(not (a < start < b or a < end < b), "section_structure_conflict")


def _recorded_only_text(replacement):
    """Reject narrow, explicit claims that this revision obtained new sources."""
    positive = (
        r"\b(?:newly|again)\s+(?:read|reviewed|analy[sz]ed|searched|retrieved|acquired|downloaded)\b|"
        r"\b(?:performed|conducted|ran|repeated)\s+(?:a\s+)?(?:new|additional|updated)\s+"
        r"(?:literature\s+|database\s+|web\s+)?search(?:es)?\b|"
        r"\b(?:this revision|this round)\b.{0,50}\b(?:searched|retrieved|acquired|downloaded|read|reviewed)\b|"
        r"(?:本次|本轮|此次).{0,12}(?:检索|获取|阅读|下载)|(?:重新|新近|新增)(?:检索|获取|阅读|下载)")
    for sentence in re.split(r"[.!?。！？\r\n]", replacement):
        _require(not re.search(positive, sentence, re.I) or
                 re.search(r"\b(?:no|not|without|never)\b|未|没有|不再", sentence, re.I),
                 "missing_evidence_disclosure")


def _disclosures(report, parsed):
    if any(row["access_level"] == "abstract" for row in parsed["evidence"]):
        _require(re.search(r"abstract|摘要", report, re.I), "missing_evidence_disclosure")
    if not parsed["evidence"]:
        _require(re.search(r"insufficient evidence|no evidence|证据不足|无证据|未获得.*证据",
                           report, re.I), "missing_evidence_disclosure")
        _require(all(row["assessment"] == "insufficient" for row in parsed["claims"]),
                 "missing_evidence_disclosure")
    if not any(row["availability"]["full_text"]["available"] for row in parsed["papers"]):
        positive = (r"(?:reviewed|read|analy[sz]ed|examined|accessed|acquired)\s+(?:the\s+)?full[- ]texts?|"
                    r"full[- ]texts?\s+(?:(?:were|was)\s+)?(?:reviewed|read|analy[sz]ed|examined|accessed|acquired)|"
                    r"已(?:经)?(?:获取|阅读|审阅|分析)[^，,;；:：。！？!?\r\n]{0,8}?全文")
        # The acquisition verb must govern full text in the same clause:
        # "已获取摘要，无全文" records acquired abstracts and absent full text.
        # A denial about another object/clause must not license a later actual
        # full-text assertion. Do not exempt bibliography lines or globally
        # accept the character 无. Ordinary English parenthetical commas keep
        # their negation ("not, to date, reviewed full texts").
        boundaries = (r"[.!?。！？\r\n;；，]|"
                      r",(?=\s*(?:[^\x00-\x7f]|we\b|I\b|this\b))|"
                      r"\b(?:but|however|yet|nevertheless)\b|但是|然而|不过|反而|可是|却|但")
        for clause in re.split(boundaries, report, flags=re.I):
            for assertion in re.finditer(positive, clause, re.I):
                prefix = clause[:assertion.start()]
                # "来源均无已获取全文" denies this acquisition predicate.
                # Also allow the adjacent coordination "无摘要或已获取全文";
                # no other text, subject or conjunction can bridge the denial.
                # Apply it per assertion: a later positive acquisition in the
                # same clause must not inherit an earlier absence statement.
                direct_absence = (assertion.group().startswith("已") and
                                  re.search(r"无(?:\s*摘要\s*(?:或者|或))?\s*$", prefix))
                not_equivalent = (assertion.group().startswith("已") and
                                  re.search(r"不等于\s*$", prefix))
                prohibited_claim = (assertion.group().startswith("已") and
                                    re.search(r"不能(?:据(?:链接|此))?声称\s*$", prefix))
                if direct_absence:
                    # An outer denial can reverse the absence assertion.
                    # Ambiguous nested negation is not eligible for this narrow
                    # exception, even if it could satisfy the legacy rule.
                    denied = not re.search(r"非|没有|未|不|无法", prefix)
                elif not_equivalent:
                    # A link is not acquired full text. Deliberately limit this
                    # exception to an adjacent, clause-final predicate; do not
                    # interpret arbitrary suffixes or nested Chinese negation.
                    outer = prefix[:not_equivalent.start()]
                    denied = (not re.search(r"非|没有|未|不|无法|否认|否定|"
                                             r"\b(?:no|not|without|never)\b", outer, re.I)
                              and not clause[assertion.end():].strip())
                elif prohibited_claim:
                    # "不能据链接声称已阅读全文" prohibits a claim; it does not
                    # assert access. Keep the prohibition adjacent and final,
                    # so a reversed denial or another acquisition is rejected.
                    outer = prefix[:prohibited_claim.start()]
                    denied = (not re.search(r"非|没有|未|不|无法|否认|否定|"
                                             r"\b(?:no|not|without|never)\b", outer, re.I)
                              and not clause[assertion.end():].strip())
                else:
                    denied = re.search(r"\b(?:no|not|without|never)\b|未|没有", clause, re.I)
                _require(denied, "missing_evidence_disclosure")


def compose_revision(report, items, artifacts, section_id, base_sha256, output):
    """Replace one UTF-8 range and validate the entire resulting citation graph."""
    selection = validate_revision_input(report, items, artifacts, section_id, base_sha256)
    output = parse_revision_output(output)
    _recorded_only_text(output["section_markdown"])
    section = selection["section"]
    target = set(selection["target_claim_ids"])
    parsed, _ = _load(report, items, artifacts)
    old_claims = {row["claim_id"]: row for row in parsed["claims"]}
    new_claims = output["claims"]
    _require(not target or bool(new_claims), "invalid_claim_markers")
    _require(all(row["claim_id"] not in old_claims or row["claim_id"] in target
                 for row in new_claims), "invalid_claim_markers")
    uncertain = {frozenset(row["evidence_ids"]) for row in parsed["claims"]
                 if row["assessment"] in ("conflicting", "insufficient")}
    for row in new_claims:
        previous = old_claims.get(row["claim_id"])
        evidence_ids = frozenset(row["evidence_ids"])
        # Distinct claims may assess the same evidence differently. Preserve an
        # existing supported claim only under its own ID and exact evidence set.
        preserved_support = (previous is not None and previous["assessment"] == "supported" and
                             evidence_ids == frozenset(previous["evidence_ids"]))
        _require(row["assessment"] != "supported" or
                 evidence_ids not in uncertain or preserved_support,
                 "unsupported_assessment_upgrade")
        _require(not (row["assessment"] == "supported" and previous and
                     previous["assessment"] in ("conflicting", "insufficient") and
                     set(row["evidence_ids"]).issubset(previous["evidence_ids"])),
                 "unsupported_assessment_upgrade")
    raw = report.encode("utf-8")
    replacement = output["section_markdown"].encode("utf-8")
    composed = raw[:section["start_byte"]] + replacement + raw[section["end_byte"]:]
    _require(len(composed) <= MAX_REPORT_BYTES, "revision_too_large")
    candidate = composed.decode("utf-8")
    _check_structure(report, candidate, section)
    # Preserve each retained JSONL line exactly, including its original newline.
    retained = []
    for line in artifacts["claims"].splitlines(keepends=True):
        if not line.strip() or _json(line)["claim_id"] not in target:
            retained.append(line)
    claim_bytes = b"".join(retained)
    if new_claims and claim_bytes and not claim_bytes.endswith((b"\r", b"\n")):
        claim_bytes += b"\n"
    claim_bytes += b"".join(_wire(row) + b"\n" for row in new_claims)
    candidate_artifacts = dict(artifacts, claims=claim_bytes)
    _require(sum(len(raw) for raw in candidate_artifacts.values()) <= MAX_TRACE_BYTES,
             "revision_too_large")
    candidate_items = _manifest(candidate, candidate_artifacts)
    candidate_parsed, ranges = _load(candidate, candidate_items, candidate_artifacts)
    new_end = section["start_byte"] + len(replacement)
    new_ids = {row["claim_id"] for row in new_claims}
    for claim_id, bounds in ranges.items():
        inside = section["start_byte"] <= bounds["start_byte"] < new_end
        _require(inside == (claim_id in new_ids), "invalid_claim_markers")
        if inside:
            _require(bounds["end_byte"] <= new_end, "claim_boundary_conflict")
    _disclosures(candidate, candidate_parsed)
    warnings = ["recorded_assessment_not_independently_verified",
                "structure_and_traceability_do_not_establish_scientific_accuracy"]
    if not parsed["evidence"]:
        warnings.append("no_extracted_evidence")
    validation = {
        "validator_version": VALIDATOR_VERSION, "valid": True,
        "checks": [{"name": name, "status": "pass", "detail": detail} for name, detail in (
            ("fixed_snapshot", "The input report and four evidence records match their hashes."),
            ("exact_section_range", "All report bytes outside the selected UTF-8 range are preserved."),
            ("fixed_materials", "Papers, evidence and references retain their original bytes."),
            ("section_structure", "Headings and Markdown section boundaries outside the target are preserved."),
            ("claim_relationships", "Unique prose markers and their recorded numbered citations resolve."),
            ("recorded_limitations", "Recorded uncertainty and material-access disclosures are retained."))],
        "errors": [], "warnings": warnings,
        "provenance": {"validator_version": VALIDATOR_VERSION,
                       "search_started_at": None, "search_finished_at": None,
                       "source_verification": "recorded_evidence_not_independently_repeated"},
    }
    return {"report_markdown": candidate, "report_sha256": hashlib.sha256(composed).hexdigest(),
            "artifacts": candidate_artifacts, "items": candidate_items,
            "validation": validation, "material_policy": "recorded_evidence_only",
            "target_claim_ids": sorted(new_ids), "removed_claim_ids": sorted(target - new_ids),
            "added_claim_ids": sorted(new_ids - target),
            "range": {"start_byte": section["start_byte"], "end_byte": section["end_byte"],
                      "candidate_end_byte": new_end}}


def validate_revision_candidate(report, items, artifacts, section_id, base_sha256,
                                candidate_report, candidate_items, candidate_artifacts,
                                output=None):
    """Independently rebuild a worker candidate before publication.

    Passing the original revision-output bytes is preferred. Without them the
    same strict output is recovered from the exact target and candidate claims.
    Returned artifacts are the independently rebuilt bytes, never worker paths.
    """
    section = select_section(report, section_id, base_sha256)
    _require(isinstance(candidate_report, str), "candidate_mismatch")
    _require(len(candidate_report) <= MAX_REPORT_BYTES, "revision_too_large")
    try:
        original, candidate = report.encode("utf-8"), candidate_report.encode("utf-8")
    except UnicodeError as exc:
        raise RevisionValidationError("candidate_mismatch") from exc
    _require(len(candidate) <= MAX_REPORT_BYTES, "revision_too_large")
    _require(isinstance(candidate_artifacts, dict) and set(candidate_artifacts) == set(TRACE_ARTIFACTS),
             "candidate_mismatch")
    _require(all(candidate_artifacts[key] == artifacts[key]
                 for key in ("papers", "evidence", "references")), "candidate_mismatch")
    start, end = section["start_byte"], section["end_byte"]
    new_end = len(candidate) - (len(original) - end)
    _require(start <= new_end and candidate[:start] == original[:start] and
             candidate[new_end:] == original[end:], "candidate_mismatch")
    parsed, ranges = _load(candidate_report, candidate_items, candidate_artifacts)
    if output is None:
        rows = [row for row in parsed["claims"]
                if start <= ranges[row["claim_id"]]["start_byte"] < new_end]
        try:
            text = candidate[start:new_end].decode("utf-8")
        except UnicodeError as exc:
            raise RevisionValidationError("candidate_mismatch") from exc
        output = {"schema_version": "1", "section_markdown": text, "claims": rows}
    rebuilt = compose_revision(report, items, artifacts, section_id, base_sha256, output)
    _require(rebuilt["report_markdown"] == candidate_report and
             rebuilt["artifacts"] == candidate_artifacts, "candidate_mismatch")
    return rebuilt
