"""Read-only, offline structure and traceability validation for research outputs.

This does not establish scientific truth or independently verify identifiers.
The controller must validate a stopped attempt and copy files without races.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from .schemas import RECORD_SCHEMAS, REFERENCES_SCHEMA, REQUIRED_FILES, VALIDATOR_VERSION

REPORT_LIMIT = 2 * 1024 * 1024
TOTAL_LIMIT = 50 * 1024 * 1024


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _json(text):
    return json.loads(text, object_pairs_hook=_json_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON")))


def _schema_errors(value, schema, location):
    errors = []
    types = schema.get("type", [])
    if isinstance(types, str):
        types = [types]
    matches = {"object": isinstance(value, dict), "array": isinstance(value, list),
               "string": isinstance(value, str), "integer": type(value) is int,
               "boolean": type(value) is bool, "null": value is None}
    if types and not any(matches.get(kind, False) for kind in types):
        return [f"{location}: invalid type"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{location}: invalid enum")
    if isinstance(value, str):
        if len(value.strip()) < schema.get("minLength", 0):
            errors.append(f"{location}: empty string")
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            errors.append(f"{location}: invalid Unicode surrogate")
        if schema.get("format") == "date-time":
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value) or datetime.fromisoformat(value[:-1] + "+00:00").utcoffset().total_seconds() != 0:
                    raise ValueError("not UTC")
            except (ValueError, AttributeError):
                errors.append(f"{location}: expected UTC RFC3339 timestamp")
    if type(value) is int and (value < schema.get("minimum", -(2**53 - 1)) or abs(value) > 2**53 - 1):
        errors.append(f"{location}: integer outside allowed range")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{location}.{key}: required")
        if schema.get("additionalProperties") is False:
            for key in value.keys() - properties.keys():
                errors.append(f"{location}: unknown field {key}")
        for key in value.keys() & properties.keys():
            errors.extend(_schema_errors(value[key], properties[key], f"{location}.{key}"))
    if isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(_schema_errors(item, schema["items"], f"{location}[{index}]"))
    return errors


def _safe_file(root, relative):
    """Only plain relative paths, no symlink component or encoded paths."""
    if not isinstance(relative, str) or not relative or "%" in relative or "\\" in relative:
        raise ValueError("unsafe path")
    path = Path(relative)
    if path.is_absolute() or ":" in relative or any(part in {"..", "."} for part in relative.split("/")):
        raise ValueError("unsafe path")
    candidate = root
    for part in path.parts:
        candidate = candidate / part
        if candidate.is_symlink() or candidate.is_junction():
            raise ValueError("symlink or junction forbidden")
    if not candidate.resolve().is_relative_to(root) or not candidate.is_file():
        raise ValueError("missing or unsafe file")
    if candidate.stat().st_nlink > 1:
        raise ValueError("hard-linked file forbidden")
    return candidate


def _identifier(paper):
    doi, pmid, url = paper["doi"], paper["pmid"], paper["url"]
    if doi and re.fullmatch(r"10\.\d{4,9}/\S+", doi, re.I):
        return True
    if pmid and re.fullmatch(r"[1-9]\d*", pmid):
        return True
    if url:
        try:
            parsed = urlsplit(url)
            return parsed.scheme in {"https", "http"} and bool(parsed.hostname) and not parsed.username and not parsed.password and not any(c.isspace() for c in url)
        except ValueError:
            pass
    return False


def validate_research_outputs(workspace: Path) -> dict:
    """Return explicit ``valid``, checks/errors/warnings and observed provenance.

    Required producer files are fixed by REQUIRED_FILES. validation.json and
    manifest.json are generated and hashed by the controller, never trusted here.
    No network requests, writes, recursive directory traversal or external reads.
    """
    result = {"validator_version": VALIDATOR_VERSION, "valid": False,
              "checks": [], "errors": [], "warnings": [], "provenance": {}}

    def check(name, condition, detail):
        result["checks"].append({"name": name, "status": "pass" if condition else "fail", "detail": detail})
        if not condition:
            result["errors"].append(detail)

    def unknown(name, detail):
        result["checks"].append({"name": name, "status": "unknown", "detail": detail})
        result["warnings"].append(detail)

    root_input = Path(workspace)
    # Reject ancestor links too: resolve() alone would conceal the traversal.
    if any(p.is_symlink() or p.is_junction() for p in (root_input, *root_input.absolute().parents)):
        check("workspace", False, "workspace has a symlink or junction ancestor")
        return result
    root = root_input.resolve()
    check("workspace", root.is_dir(), "workspace must be a directory")
    if result["errors"]:
        return result
    texts, total = {}, 0
    for name in REQUIRED_FILES:
        try:
            path = _safe_file(root, name)
            size = path.stat().st_size
            if size > (REPORT_LIMIT if name == "report.md" else TOTAL_LIMIT):
                raise ValueError("file exceeds size limit")
            total += size
            if total > TOTAL_LIMIT:
                raise ValueError("required outputs exceed total size limit")
            with path.open("rb") as stream:
                raw = stream.read(size + 1)
            if len(raw) != size:
                raise ValueError("file changed during bounded read")
            texts[name] = raw.decode("utf-8", errors="strict")
            if texts[name].startswith("\ufeff"):
                raise ValueError("UTF-8 BOM forbidden")
            check(f"file:{name}", True, f"{name}: readable bounded UTF-8 regular file")
        except (OSError, UnicodeError, ValueError) as exc:
            check(f"file:{name}", False, f"{name}: {type(exc).__name__}: {exc}")
    if result["errors"]:
        return result
    data = {}
    for name, schema in RECORD_SCHEMAS.items():
        rows = []
        for number, line in enumerate(texts[name + ".jsonl"].splitlines(), 1):
            if not line.strip():
                check(f"schema:{name}:{number}", False, f"{name}:{number}: blank JSONL line")
                continue
            try:
                row = _json(line)
                errors = _schema_errors(row, schema, f"{name}:{number}")
                if errors:
                    result["errors"].extend(errors)
                else:
                    rows.append(row)
            except (ValueError, RecursionError) as exc:
                result["errors"].append(f"{name}:{number}: invalid JSON ({type(exc).__name__})")
        data[name] = rows
    try:
        refs = _json(texts["references.json"])
        result["errors"].extend(_schema_errors(refs, REFERENCES_SCHEMA, "references"))
    except (ValueError, RecursionError):
        refs = []
        result["errors"].append("references.json: invalid JSON")
    check("record_schemas", not result["errors"], "all records must conform to output schema v1")
    if result["errors"]:
        return result

    def index(rows, key, label):
        indexed = {}
        for row in rows:
            check(f"unique:{label}:{row[key]}", row[key] not in indexed, f"{label}: duplicate {key} {row[key]}")
            indexed[row[key]] = row
        return indexed

    papers = index(data["papers"], "paper_id", "papers")
    evidence = index(data["evidence"], "evidence_id", "evidence")
    claims = index(data["claims"], "claim_id", "claims")
    screened = index(data["screening"], "paper_id", "screening")
    references = index(refs, "n", "references")
    seen_identifiers = set()
    report = texts["report.md"]
    check("report_nonempty", bool(report.strip()), "report.md must not be empty")
    for label, pattern in {
        "scope": r"scope|研究范围|范围", "methods": r"methods?|方法",
        "findings": r"findings?|结果|发现", "limitations": r"limitations?|局限",
        "references": r"references?|bibliography|参考文献",
    }.items():
        check(f"report_section:{label}", bool(re.search(r"^#{1,6}\s+.*(?:" + pattern + r")", report, re.I | re.M)), f"report needs a {label} heading")
    for search in data["searches"]:
        if search["status"] != "success":
            check("search_error_recorded", bool(search["error"]), "failed/partial search must retain an error")
            unknown("source_error", f"Source {search['source']} returned {search['status']}; completeness unknown")
            check("source_error_disclosure", bool(re.search(r"source|来源|检索.*失败", report, re.I)), "report must disclose source errors")
        if search["status"] == "error":
            check("failed_search_count", search["result_count"] is None, "failed search count must be null, not a fabricated zero")
    for paper_id, paper in papers.items():
        check("paper_sources", bool(paper["sources"]), f"{paper_id}: at least one source required")
        check("paper_identifier", _identifier(paper), f"{paper_id}: checkable DOI, PMID or HTTP(S) source URL required")
        if paper["doi"] is not None:
            check("doi_syntax", bool(re.fullmatch(r"10\.\d{4,9}/\S+", paper["doi"], re.I)), f"{paper_id}: malformed DOI")
        if paper["pmid"] is not None:
            check("pmid_syntax", bool(re.fullmatch(r"[1-9]\d*", paper["pmid"])), f"{paper_id}: malformed PMID")
        if paper["url"] is not None:
            check("source_url", _identifier({"doi": None, "pmid": None, "url": paper["url"]}), f"{paper_id}: source URL must be HTTP(S) without credentials")
        for key in ("doi", "pmid"):
            identifier = paper[key]
            if identifier:
                identity = (key, identifier.casefold())
                check("duplicate_paper", identity not in seen_identifiers, f"duplicate paper {key}: {identifier}")
                seen_identifiers.add(identity)
        for level, availability in paper["availability"].items():
            check("access_timestamp", (availability["obtained_at"] is not None) == availability["available"], f"{paper_id}: {level} availability/time inconsistent")
        check("metadata_access", paper["availability"]["metadata"]["available"], f"{paper_id}: metadata must be acquired")
        check("abstract_access", bool(paper["abstract"]) == paper["availability"]["abstract"]["available"], f"{paper_id}: abstract availability inconsistent")
        has_full = paper["availability"]["full_text"]["available"]
        check("full_text_path", bool(paper["full_text_path"]) == has_full, f"{paper_id}: acquired full text needs its actual workspace text file")
        if has_full:
            try:
                full_path = _safe_file(root, paper["full_text_path"])
                size = full_path.stat().st_size
                check("full_text_size", 0 < size <= TOTAL_LIMIT and total + size <= TOTAL_LIMIT, f"{paper_id}: full text must be nonempty and within quota")
                total += size
            except (OSError, ValueError) as exc:
                check("full_text_file", False, f"{paper_id}: unsafe/missing full text ({type(exc).__name__})")
        verification = paper["verification"]
        if verification["status"] == "verified":
            check("verification_provenance", bool(verification["checked_at"] and verification["source"]), f"{paper_id}: verified requires source and timestamp")
            unknown("external_verification", f"{paper_id}: recorded identifier verification is not independently repeated by offline validator")
        else:
            unknown("external_verification", f"{paper_id}: identifier verification {verification['status']}")
            check("verification_disclosure", bool(re.search(r"verif|核验|核实", report, re.I)), "report must disclose unknown/failed identifier verification")
        check("screening_complete", paper_id in screened, f"{paper_id}: screening decision required")
    for row in data["screening"]:
        check("screening_link", row["paper_id"] in papers, "screening references missing paper")
        check("screening_criteria", bool(row["criteria"]), "screening criteria must be explicit")
    for row in data["evidence"]:
        paper = papers.get(row["paper_id"])
        check("evidence_paper", paper is not None, "evidence references missing paper")
        if paper:
            check("evidence_access", paper["availability"][row["access_level"]]["available"], f"{row['evidence_id']}: access level contradicts acquired material")
            check("evidence_screening", screened.get(row["paper_id"], {}).get("decision") == "include", "evidence must reference included paper")
        if row["access_level"] == "abstract":
            check("abstract_locator", bool(re.search(r"abstract|摘要", row["locator"], re.I)), "abstract evidence locator must name the abstract")
            check("abstract_disclosure", bool(re.search(r"abstract|摘要", report, re.I)), "report must disclose abstract-only evidence")
    mapped_papers = set()
    for ref in refs:
        check("reference_paper", ref["paper_id"] in papers, "reference points to missing paper")
        check("reference_unique_paper", ref["paper_id"] not in mapped_papers, "same paper has multiple reference numbers")
        mapped_papers.add(ref["paper_id"])
        check("reference_in_report", f"[{ref['n']}]" in report, f"reference [{ref['n']}] absent from report")
    for citation in re.findall(r"\[(\d+(?:\s*[,;\-–]\s*\d+)*)\]", report):
        check("citation_format", citation.isdigit(), "use single numbered citations [n], no grouped/ranged citations")
        if citation.isdigit():
            check("citation_resolves", int(citation) in references, f"citation [{citation}] missing from references.json")
    paper_numbers = {ref["paper_id"]: ref["n"] for ref in refs}
    for report_claim in re.findall(r"<!-- claim:([^<>]+?) -->", report):
        check("report_claim_resolves", report_claim in claims, "report has unregistered claim marker")
    for row in claims.values():
        marker = f"<!-- claim:{row['claim_id']} -->"
        check("claim_marker", row["marker"] == marker and report.count(marker) == 1, f"{row['claim_id']}: exact claim marker missing or duplicated")
        check("claim_evidence", bool(row["evidence_ids"]) or row["assessment"] == "insufficient", "supported/conflicting claims require evidence")
        check("claim_evidence_unique", len(row["evidence_ids"]) == len(set(row["evidence_ids"])), "claim evidence IDs must be distinct")
        if row["assessment"] == "conflicting":
            check("conflicting_evidence", len(row["evidence_ids"]) >= 2, "conflicting assessment requires at least two evidence records")
        block = report.split(marker, 1)[1].lstrip() if marker in report else ""
        paragraph = re.split(r"\n\s*\n|(?=<!-- claim:)|(?=^#{1,6} )", block, maxsplit=1, flags=re.M)[0]
        for evidence_id in row["evidence_ids"]:
            check("claim_evidence_link", evidence_id in evidence, "claim references missing evidence")
            if evidence_id in evidence:
                paper_id = evidence[evidence_id]["paper_id"]
                check("claim_reference", paper_id in mapped_papers, "claim evidence paper must appear in references")
                number = paper_numbers.get(paper_id)
                check("claim_paragraph_citation", number is not None and f"[{number}]" in paragraph, "claim paragraph must cite each supporting evidence paper")
    if not any(paper["availability"]["full_text"]["available"] for paper in papers.values()):
        positive_access = r"(?:reviewed|read|analy[sz]ed|examined|accessed|acquired)\s+(?:the\s+)?full[- ]texts?|full[- ]texts?\s+(?:(?:were|was)\s+)?(?:reviewed|read|analy[sz]ed|examined|accessed|acquired)|已(?:获取|阅读|审阅|分析).{0,8}全文"
        for sentence in re.split(r"[.!?。！？\n]", report):
            if re.search(positive_access, sentence, re.I) and not re.search(r"\b(?:no|not|without|never)\b|未|没有", sentence, re.I):
                check("report_access_consistency", False, "report claims full-text access but no full text was acquired")
    # Check common explicit screening-count phrases against persisted records.
    # This is deliberately narrow; arbitrary prose still requires human review.
    methods = re.search(r"^#{1,6}\s+[^\n]*(?:methods?|方法)[^\n]*\n(.*?)(?=^#{1,6}\s|\Z)", report, re.I | re.M | re.S)
    if methods:
        for word, expected in (("screened", len(screened)), ("included", sum(row["decision"] == "include" for row in screened.values())), ("excluded", sum(row["decision"] == "exclude" for row in screened.values()))):
            for recorded in re.findall(r"\b" + word + r"\s+(\d+)\b", methods.group(1), re.I):
                check("screening_count", int(recorded) == expected, f"methods {word} count contradicts screening records")
    if not evidence:
        check("zero_evidence_disclosure", bool(re.search(r"insufficient evidence|no evidence|证据不足|无证据|未获得.*证据", report, re.I)), "zero evidence report must explicitly state insufficient evidence")
        check("zero_evidence_claims", all(row["assessment"] == "insufficient" for row in claims.values()), "zero evidence cannot support substantive claims")
        result["warnings"].append("No extracted evidence; report may only describe the search and evidence insufficiency")
    if re.search(r"systematic review|系统综述", report, re.I):
        unknown("systematic_review", "Systematic-review wording requires human review of the complete protocol; not certified here")
    dates = [row["searched_at"] for row in data["searches"]]
    result["provenance"] = {"validator_version": VALIDATOR_VERSION,
                            "search_started_at": min(dates) if dates else None,
                            "search_finished_at": max(dates) if dates else None,
                            "source_verification": "offline_not_repeated"}
    unknown("scientific_accuracy", "Structure and traceability checks do not establish scientific accuracy")
    result["valid"] = not result["errors"]
    return result


validate_workspace = validate_research_outputs
