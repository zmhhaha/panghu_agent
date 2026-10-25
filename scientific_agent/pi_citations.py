"""Bounded, read-only citation traces from immutable Pi research artifacts.

This module intentionally uses only the standard library. It checks the recorded
relationships and transfer integrity; it does not independently verify scientific
claims, fetch source URLs, read full-text paths, or alter the original report.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from urllib.parse import urlsplit


MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_TRACE_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 5000
MAX_INTEGER = 2**53 - 1
TRACE_ARTIFACTS = ("papers", "evidence", "claims", "references")
_MEDIA = {"report": "text/markdown", "papers": "application/x-ndjson",
          "evidence": "application/x-ndjson", "claims": "application/x-ndjson",
          "references": "application/json"}
_ERRORS = {
    "manifest_invalid": "Citation artifact metadata is missing or invalid.",
    "report_mismatch": "Citation artifacts do not match the displayed report.",
    "trace_too_large": "Citation records exceed the supported reading limit.",
    "artifact_mismatch": "Citation artifact bytes failed their integrity check.",
    "invalid_trace": "Citation records contain invalid or inconsistent relationships.",
    "unsupported_citations": "Grouped or ranged citations cannot be traced automatically.",
}


class TraceUnavailable(ValueError):
    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or _ERRORS.get(code, "Citation trace is unavailable."))


def _require(condition, code="invalid_trace"):
    if not condition:
        raise TraceUnavailable(code)


def _report_bytes(report):
    _require(isinstance(report, str) and bool(report.strip()), "report_mismatch")
    try:
        content = report.encode("utf-8")
    except UnicodeError as exc:
        raise TraceUnavailable("report_mismatch") from exc
    _require(len(content) <= MAX_REPORT_BYTES, "trace_too_large")
    return content


def validate_manifest(report: str, items: list[dict]) -> dict:
    """Check transfer metadata before fetching the four trace artifacts.

    The returned dictionary contains only papers/evidence/claims/references and
    only public metadata fields. Unknown artifacts may coexist in the manifest,
    but duplicate IDs or malformed metadata are never silently accepted.
    """
    content = _report_bytes(report)
    _require(isinstance(items, list), "manifest_invalid")
    _require(len(items) <= MAX_RECORDS, "trace_too_large")
    indexed = {}
    for row in items:
        _require(isinstance(row, dict), "manifest_invalid")
        artifact_id = row.get("artifact_id")
        _require(isinstance(artifact_id, str) and
                 re.fullmatch(r"[A-Za-z0-9_-]{1,80}", artifact_id) is not None,
                 "manifest_invalid")
        _require(artifact_id not in indexed, "manifest_invalid")
        size, digest, media = row.get("size"), row.get("sha256"), row.get("media_type")
        _require(type(size) is int and 0 <= size <= MAX_INTEGER,
                 "manifest_invalid")
        _require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
                 is not None, "manifest_invalid")
        _require(isinstance(media, str) and bool(media.strip()), "manifest_invalid")
        if artifact_id in _MEDIA:
            _require(media == _MEDIA[artifact_id], "manifest_invalid")
        indexed[artifact_id] = {"artifact_id": artifact_id, "media_type": media,
                                "size": size, "sha256": digest}
    _require(all(key in indexed for key in ("report",) + TRACE_ARTIFACTS),
             "manifest_invalid")
    expected = indexed["report"]
    _require(expected["size"] == len(content) and
             expected["sha256"] == hashlib.sha256(content).hexdigest(),
             "report_mismatch")
    _require(sum(indexed[key]["size"] for key in TRACE_ARTIFACTS) <= MAX_TRACE_BYTES,
             "trace_too_large")
    return {key: indexed[key] for key in TRACE_ARTIFACTS}


def _json_pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _json(content):
    try:
        return json.loads(content, object_pairs_hook=_json_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(
                              TraceUnavailable("invalid_trace")))
    except (ValueError, RecursionError, UnicodeError) as exc:
        if isinstance(exc, TraceUnavailable):
            raise
        raise TraceUnavailable("invalid_trace") from exc


def _text(value, nullable=False, nonempty=True):
    if value is None and nullable:
        return
    _require(isinstance(value, str))
    _require(not nonempty or bool(value.strip()))
    _require(not any(0xD800 <= ord(char) <= 0xDFFF for char in value))


def _timestamp(value, nullable=False):
    if value is None and nullable:
        return
    _text(value)
    _require(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:"
                          r"[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})",
                          value) is not None)
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TraceUnavailable("invalid_trace") from exc


def _object(value, fields):
    _require(isinstance(value, dict) and set(value) == set(fields))


def _strings(value):
    _require(isinstance(value, list))
    for item in value:
        _text(item)


def _safe_url(value):
    if value is None:
        return None
    if not value or any(char.isspace() or ord(char) < 32 or ord(char) == 127
                        or char in '<>"\\' for char in value):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme.lower() not in ("http", "https") or not parsed.hostname
                or parsed.username is not None or parsed.password is not None):
            return None
        parsed.port  # Reject invalid ports, including otherwise parseable URLs.
    except ValueError:
        return None
    return value


def _paper(row):
    _object(row, ("paper_id", "title", "authors", "year", "doi", "pmid", "url",
                  "sources", "retrieved_at", "availability", "abstract",
                  "full_text_path", "verification"))
    _text(row["paper_id"])
    _text(row["title"])
    _strings(row["authors"])
    _strings(row["sources"])
    _require(bool(row["sources"]))
    year = row["year"]
    _require(year is None or type(year) is int and 1 <= year <= MAX_INTEGER)
    for key in ("doi", "pmid", "url", "abstract", "full_text_path"):
        _text(row[key], nullable=True, nonempty=False)
    _timestamp(row["retrieved_at"])
    availability = row["availability"]
    _object(availability, ("metadata", "abstract", "full_text"))
    for level in availability.values():
        _object(level, ("available", "obtained_at"))
        _require(type(level["available"]) is bool)
        _timestamp(level["obtained_at"], nullable=True)
        _require((level["obtained_at"] is not None) == level["available"])
    _require(availability["metadata"]["available"])
    _require(bool(row["abstract"]) == availability["abstract"]["available"])
    _require(bool(row["full_text_path"]) == availability["full_text"]["available"])
    verification = row["verification"]
    _object(verification, ("status", "checked_at", "source", "error"))
    _require(verification["status"] in ("verified", "unknown", "failed"))
    _timestamp(verification["checked_at"], nullable=True)
    _text(verification["source"], nullable=True, nonempty=False)
    _text(verification["error"], nullable=True, nonempty=False)
    if verification["status"] == "verified":
        _require(bool(verification["checked_at"] and verification["source"]))


def _evidence(row):
    _object(row, ("evidence_id", "paper_id", "finding", "method", "limitations",
                  "access_level", "locator"))
    for value in row.values():
        _text(value)
    _require(row["access_level"] in ("metadata", "abstract", "full_text"))
    if row["access_level"] == "abstract":
        _require(re.search(r"abstract|摘要", row["locator"], re.I) is not None)


def _claim(row):
    _object(row, ("claim_id", "marker", "evidence_ids", "assessment", "explanation"))
    for key in ("claim_id", "marker", "assessment", "explanation"):
        _text(row[key])
    _strings(row["evidence_ids"])
    _require(len(row["evidence_ids"]) == len(set(row["evidence_ids"])))
    _require(row["assessment"] in ("supported", "conflicting", "insufficient"))
    _require(row["marker"] == "<!-- claim:" + row["claim_id"] + " -->")
    _require(bool(row["evidence_ids"]) or row["assessment"] == "insufficient")
    if row["assessment"] == "conflicting":
        _require(len(row["evidence_ids"]) >= 2)


def _reference(row):
    _object(row, ("n", "paper_id"))
    _require(type(row["n"]) is int and 1 <= row["n"] <= MAX_INTEGER)
    _text(row["paper_id"])


def _rows(kind, content):
    try:
        text = content.decode("utf-8")
    except UnicodeError as exc:
        raise TraceUnavailable("invalid_trace") from exc
    if kind == "references":
        rows = _json(text)
        _require(isinstance(rows, list))
    else:
        rows = []
        for line in text.splitlines():
            if not line.strip():
                continue
            _require(len(rows) < MAX_RECORDS, "trace_too_large")
            rows.append(_json(line))
    _require(len(rows) <= MAX_RECORDS, "trace_too_large")
    validator = {"papers": _paper, "evidence": _evidence, "claims": _claim,
                 "references": _reference}[kind]
    for row in rows:
        validator(row)
    return rows


def _index(rows, key):
    indexed = {}
    for row in rows:
        _require(row[key] not in indexed)
        indexed[row[key]] = row
    return indexed


def _wire_size(value):
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                              allow_nan=False).encode("utf-8"))
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise TraceUnavailable("invalid_trace") from exc


def _mask(text, ranges):
    if not ranges:
        return text
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    parts, cursor = [], 0
    for start, end in merged:
        parts.append(text[cursor:start])
        parts.append("".join("\n" if char == "\n" else " "
                             for char in text[start:end]))
        cursor = end
    parts.append(text[cursor:])
    return "".join(parts)


def _escaped(text, position):
    start = position
    while start and text[start - 1] == "\\":
        start -= 1
    return (position - start) % 2 == 1


def _balanced(text, opening, closing):
    stack, pairs = [], {}
    for position, char in enumerate(text):
        if char not in (opening, closing) or _escaped(text, position):
            continue
        if char == opening:
            stack.append(position)
        elif stack:
            pairs[stack.pop()] = position
    return pairs


def _label(value):
    return " ".join(value.split()).casefold()


def _citation_numbers(report):
    """Read the supported [n] syntax without treating code or links as evidence.

    This deliberately does not infer paper matches from bibliography prose or
    interpret grouped/ranged citations. Markdown link labels/destinations,
    reference definitions, comments, inline code and fenced code are excluded.
    """
    ranges, offset, fence, fence_start = [], 0, None, None
    for line in report.splitlines(keepends=True):
        marker = re.match(r" {0,3}(`{3,}|~{3,})(.*)", line)
        if fence:
            if (marker and marker[1][0] == fence[0] and
                    len(marker[1]) >= len(fence) and not marker[2].strip()):
                ranges.append((fence_start, offset + len(line)))
                fence = None
        elif marker:
            fence, fence_start = marker[1], offset
        elif line.startswith(("    ", "\t")):
            ranges.append((offset, offset + len(line)))
        offset += len(line)
    if fence:
        ranges.append((fence_start, len(report)))
    text = _mask(report, ranges)
    ranges = [match.span() for match in re.finditer(r"<!--.*?(?:-->|\Z)", text, re.S)]
    text = _mask(text, ranges)
    # Match exact-length backtick runs, leaving unmatched literal backticks alone.
    runs = list(re.finditer(r"`+", text))
    positions = {}
    for match in reversed(runs):
        positions.setdefault(len(match[0]), []).append(match)
    ranges, used_until = [], -1
    for match in runs:
        if match.start() < used_until or _escaped(text, match.start()):
            continue
        candidates = positions[len(match[0])]
        while candidates and candidates[-1].start() <= match.start():
            candidates.pop()
        if candidates:
            end = candidates[-1].end()
            ranges.append((match.start(), end))
            used_until = end
    text = _mask(text, ranges)
    definitions, ranges = set(), []
    for match in re.finditer(r"^ {0,3}\[([^\]\n]+)\]:[^\n]*(?:\n|$)", text, re.M):
        definitions.add(_label(match[1]))
        ranges.append(match.span())
    text = _mask(text, ranges)
    brackets, parentheses = _balanced(text, "[", "]"), _balanced(text, "(", ")")
    ranges = []
    for start, end in sorted(brackets.items()):
        follow = end + 1
        while follow < len(text) and text[follow].isspace():
            follow += 1
        finish = None
        if follow in parentheses:
            finish = parentheses[follow] + 1
        elif definitions and follow in brackets:
            label_end = brackets[follow]
            label = text[follow + 1:label_end] if label_end - follow <= 1000 else None
            if label == "" and end - start <= 1000:
                label = text[start + 1:end]
            if label is not None and _label(label) in definitions:
                finish = brackets[follow] + 1
        elif definitions and end - start <= 1000:
            label = text[start + 1:end]
            if "[" not in label and "]" not in label and _label(label) in definitions:
                finish = end + 1
        if finish is not None:
            if start and text[start - 1] == "!" and not _escaped(text, start - 1):
                start -= 1
            ranges.append((start, finish))
    text = _mask(text, ranges)
    text = _mask(text, [match.span() for match in re.finditer(r"<[^<>\n]*>", text)])
    numbers = set()
    for match in re.finditer(r"\[([0-9]+(?:\s*[,;\-–]\s*[0-9]+)*)\]", text):
        if _escaped(text, match.start()):
            continue
        _require(match[1].isdigit(), "unsupported_citations")
        _require(len(match[1]) <= 16)
        number = int(match[1])
        _require(number <= MAX_INTEGER)
        numbers.add(number)
        _require(len(numbers) <= MAX_RECORDS, "trace_too_large")
    return numbers


def build_citation_trace(report: str, items: list[dict],
                         artifacts: dict[str, bytes]) -> dict:
    """Return explicit paper/evidence/claim relationships for a report.

    ``evidence`` includes the records for that paper. ``claims`` includes records
    explicitly referencing those evidence IDs. These associations do not assert
    that every appearance of a citation supports its surrounding prose.
    """
    manifest = validate_manifest(report, items)
    _require(isinstance(artifacts, dict), "artifact_mismatch")
    parsed = {}
    for kind in TRACE_ARTIFACTS:
        content = artifacts.get(kind)
        _require(isinstance(content, bytes), "artifact_mismatch")
        expected = manifest[kind]
        _require(len(content) == expected["size"] and
                 hashlib.sha256(content).hexdigest() == expected["sha256"],
                 "artifact_mismatch")
        parsed[kind] = _rows(kind, content)
    papers = _index(parsed["papers"], "paper_id")
    evidence = _index(parsed["evidence"], "evidence_id")
    claims = _index(parsed["claims"], "claim_id")
    references = _index(parsed["references"], "n")
    paper_numbers = {}
    for number, row in references.items():
        _require(row["paper_id"] in papers and row["paper_id"] not in paper_numbers)
        paper_numbers[row["paper_id"]] = number
    by_paper = {paper_id: [] for paper_id in papers}
    for row in evidence.values():
        _require(row["paper_id"] in papers)
        _require(papers[row["paper_id"]]["availability"][row["access_level"]]["available"])
        by_paper[row["paper_id"]].append(row)
    observed_claims = Counter(match[1] for match in
                              re.finditer(r"<!-- claim:([^<>]+?) -->", report))
    for row in claims.values():
        _require(observed_claims[row["claim_id"]] == 1)
        for evidence_id in row["evidence_ids"]:
            _require(evidence_id in evidence)
            _require(evidence[evidence_id]["paper_id"] in paper_numbers)
    _require(all(claim_id in claims for claim_id in observed_claims))
    numbers = _citation_numbers(report) | set(references)
    _require(len(numbers) <= MAX_RECORDS, "trace_too_large")
    warnings = ["recorded_assessment_not_independently_verified"]
    if not evidence:
        warnings.append("no_extracted_evidence")
    # Conservative incremental accounting prevents a small many-to-many input
    # from expanding into an unbounded response. A final exact check remains.
    response_budget = 2048
    public_papers, paper_claims = {}, {paper_id: [] for paper_id in paper_numbers}
    for paper_id in paper_numbers:
        paper = {key: value for key, value in papers[paper_id].items()
                 if key not in ("abstract", "full_text_path")}
        paper["url"] = _safe_url(paper["url"])
        if paper["url"] is None and papers[paper_id]["url"] is not None:
            if "unsafe_source_url_omitted" not in warnings:
                warnings.append("unsafe_source_url_omitted")
        public_papers[paper_id] = paper
        response_budget += _wire_size(paper)
        response_budget += sum(_wire_size(row) + 1 for row in by_paper[paper_id])
        _require(response_budget <= MAX_RESPONSE_BYTES, "trace_too_large")
    response_budget += 512 * len(numbers)
    _require(response_budget <= MAX_RESPONSE_BYTES, "trace_too_large")
    for row in claims.values():
        linked_papers = {evidence[evidence_id]["paper_id"]
                         for evidence_id in row["evidence_ids"]}
        row_size = _wire_size(row) + 1
        for paper_id in linked_papers:
            response_budget += row_size
            _require(response_budget <= MAX_RESPONSE_BYTES, "trace_too_large")
            paper_claims[paper_id].append(row)
    citations = []
    for number in sorted(numbers):
        reference = references.get(number)
        if reference is None:
            citations.append({"n": number, "status": "unresolved",
                              "reason": "reference_missing", "paper": None,
                              "evidence": [], "claims": []})
            if "unmapped_numbered_citation" not in warnings:
                warnings.append("unmapped_numbered_citation")
            continue
        paper_id = reference["paper_id"]
        citations.append({"n": number, "status": "resolved", "reason": None,
                          "paper": public_papers[paper_id],
                          "evidence": by_paper[paper_id],
                          "claims": paper_claims[paper_id]})
    result = {"schema_version": "1", "availability": "available", "reason": None,
              "report_sha256": hashlib.sha256(report.encode("utf-8")).hexdigest(),
              "citations": citations, "warnings": warnings}
    _require(_wire_size(result) <= MAX_RESPONSE_BYTES, "trace_too_large")
    return result
