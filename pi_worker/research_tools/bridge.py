"""Bounded academic requests using the existing framework-neutral functions.

Public provider availability is not guaranteed. No keys, SaaS, CrewAI or full-text
fetch is required. Returned counts mean records received, never database totals.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import quote
from .transport import scoped_academic_transport


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _availability(available, timestamp):
    return {"available": bool(available), "obtained_at": timestamp if available else None}


def normalize_paper(record, timestamp=None):
    from tools.academic.models import normalize_doi
    timestamp = timestamp or utc_now()
    doi = normalize_doi(record.get("doi")) or None
    identifiers = record.get("identifiers") or {}
    pmid = str(identifiers.get("pmid") or "") or None
    url = record.get("url") or None
    title = str(record.get("title") or "").strip()
    if not title:
        raise ValueError("provider record missing title")
    identity = f"doi:{doi}" if doi else f"pmid:{pmid}" if pmid else "title:" + re.sub(r"\W+", "", title.casefold())
    paper_id = "paper-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    authors = record.get("authors_list") or record.get("authors") or []
    if isinstance(authors, str):
        authors = [author.strip() for author in authors.split(",") if author.strip()]
    abstract = str(record.get("abstract") or "").strip() or None
    date_match = re.match(r"(\d{4})", str(record.get("date") or ""))
    sources = record.get("providers") or [record.get("provider") or "unknown"]
    result = {
        "paper_id": paper_id, "title": title, "authors": authors,
        "year": int(date_match.group(1)) if date_match else None,
        "doi": doi, "pmid": pmid, "url": url, "sources": sources,
        "retrieved_at": timestamp,
        "availability": {"metadata": _availability(True, timestamp),
                         "abstract": _availability(abstract, timestamp),
                         "full_text": _availability(False, timestamp)},
        "abstract": abstract, "full_text_path": None,
        "verification": {"status": "unknown", "checked_at": None, "source": None,
                         "error": "Provider search metadata has not been independently verified"},
    }
    # New metadata is measured at the same bridge boundary and stays inside the
    # protected observation. Historical records without this field stay exact.
    if record.get("bibliography"):
        result["bibliography"] = record["bibliography"]
    return result


@scoped_academic_transport
def search(query, *, providers=None, limit=10):
    """One exact request per selected provider, with real failures retained.

    Empty providers means crossref/pubmed/arxiv; other public adapters are opt-in.
    No query expansion, retry loop or free-form URL is accepted by this bridge.
    Existing provider HTTP internals retain their bounded retry/timeouts.
    """
    from tools.academic.search import PROVIDER_SEARCHERS
    from tools.academic import deduplicate_papers
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        raise ValueError("query must be 1..2000 characters")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be an integer in 1..50")
    selected = ["crossref", "pubmed", "arxiv"] if providers is None else list(providers)
    if not selected or len(selected) != len(set(selected)) or any(name not in PROVIDER_SEARCHERS for name in selected):
        raise ValueError("providers must be distinct supported names")
    searches, records = [], []
    for provider in selected:
        timestamp = utc_now()
        row = {"source": provider, "query": query, "searched_at": timestamp,
               "status": "success", "result_count": None, "error": None}
        try:
            returned = PROVIDER_SEARCHERS[provider](query, limit)
            if not isinstance(returned, list):
                raise ValueError("provider returned non-list records")
            # Normalize before accepting a batch: malformed responses are source errors.
            for record in returned:
                normalize_paper(record, timestamp)
            records.extend(returned)
            row["result_count"] = len(returned)
        except Exception as exc:
            row["status"] = "error"
            # Raw request exception strings may contain credentials or internal URLs.
            row["error"] = f"{type(exc).__name__}: provider request/response failed"
        searches.append(row)
    merged = deduplicate_papers(records)
    return {"schema_version": "1", "searches": searches,
            "papers": [normalize_paper(record) for record in merged],
            "database_total": None,
            "notes": ["result_count is records returned by each adapter, not database total",
                      "A PDF URL is not acquired full text; identifier verification remains unknown"]}


@scoped_academic_transport
def verify_identifier(kind, value):
    """Fetch metadata for a DOI/PMID; never claim full text or scientific truth.

    Returned verification only attests identifier equality in a provider response.
    Timeouts, access errors and malformed responses remain unknown, not verified.
    """
    from tools.academic.http_client import request_json, request_text
    from tools.academic.models import normalize_doi
    import xml.etree.ElementTree as ET
    if kind == "doi":
        value = normalize_doi(value)
        if not re.fullmatch(r"10\.\d{4,9}/\S+", value) or len(value) > 512:
            raise ValueError("invalid DOI")
        source = "https://api.crossref.org/works/" + quote(value, safe="")
    elif kind == "pmid":
        if not isinstance(value, str) or not re.fullmatch(r"[1-9]\d{0,11}", value):
            raise ValueError("invalid PMID")
        source = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    else:
        raise ValueError("only doi and pmid verification supported")
    verification = {"status": "unknown", "checked_at": utc_now(), "source": source, "error": None}
    metadata = None
    try:
        if kind == "doi":
            metadata = request_json(source).get("message", {})
            returned = normalize_doi(metadata.get("DOI"))
        else:
            text = request_text(source, params={"db": "pubmed", "id": value, "retmode": "xml"})
            root = ET.fromstring(text)
            returned = root.findtext(".//MedlineCitation/PMID")
            metadata = {"pmid": returned, "title": " ".join(root.find(".//ArticleTitle").itertext()) if root.find(".//ArticleTitle") is not None else None}
        if returned:
            verification["status"] = "verified" if returned == value else "failed"
            if returned != value:
                verification["error"] = "Provider returned a different identifier"
        else:
            verification["error"] = "Provider response missing identifier"
    except Exception as exc:
        verification["error"] = f"{type(exc).__name__}: identifier metadata lookup unavailable"
    return {"kind": kind, "identifier": value, "verification": verification, "metadata": metadata}
