"""Local and external academic search orchestration."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.academic.models import deduplicate_papers, rank_papers  # noqa: E402
from tools.academic.providers import (  # noqa: E402
    search_arxiv,
    search_crossref,
    search_openalex,
    search_semantic_scholar,
)
from tools.academic.query import build_query_variants  # noqa: E402

from .config import Settings, settings  # noqa: E402
from .db import Database  # noqa: E402


def _tokens(topic: str) -> list[str]:
    return [
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9+.-]{1,}|[\u4e00-\u9fff]{2,}", topic)
    ]


def _with_identifiers(paper: dict[str, Any]) -> dict[str, Any]:
    identifiers = dict(paper.get("identifiers") or {})
    if identifiers.get("arxiv") and not paper.get("arxiv_id"):
        paper["arxiv_id"] = identifiers["arxiv"]
    if identifiers.get("pmid") and not paper.get("pmid"):
        paper["pmid"] = identifiers["pmid"]
    return paper


def search_literature(
    topic: str,
    db: Database,
    *,
    search_limit: int | None = None,
    per_provider: int | None = None,
    providers: list[str] | None = None,
    config: Settings = settings,
) -> dict[str, Any]:
    """Search local verified papers and external providers.

    Provider failures are isolated and returned in ``errors`` so one unavailable
    endpoint does not prevent the user from reviewing the remaining results.
    """
    topic = topic.strip()
    if not topic:
        raise ValueError("topic must not be empty")
    limit = min(max(int(search_limit or config.search_limit), 1), 100)
    per_limit = min(max(int(per_provider or config.per_provider), 1), 25)
    variants = build_query_variants(topic) or [topic]
    selected = providers or ["OpenAlex", "Crossref", "arXiv", "Semantic Scholar"]

    local = db.search_local(_tokens(topic), limit=limit)
    local = [{**row, "provider": "LocalLibrary", "providers": ["LocalLibrary"]} for row in local]
    api_results: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}
    functions = {
        "OpenAlex": lambda query: search_openalex(query, per_limit, email=config.contact_email),
        "Crossref": lambda query: search_crossref(query, per_limit, email=config.contact_email),
        "arXiv": lambda query: search_arxiv(query, per_limit),
        "Semantic Scholar": lambda query: search_semantic_scholar(query, per_limit, api_key=config.semantic_scholar_api_key),
    }
    for provider in selected:
        if provider not in functions:
            errors[provider] = "unsupported provider"
            continue
        rows: list[dict[str, Any]] = []
        provider_errors: list[str] = []
        for variant in variants[:2]:
            try:
                rows.extend(functions[provider](variant))
            except Exception as exc:
                provider_errors.append(f"{type(exc).__name__}: {str(exc)[:160]}")
        api_results[provider] = [dict(row) for row in deduplicate_papers(rows)]
        if provider_errors:
            errors[provider] = "; ".join(provider_errors)

    all_records: list[dict[str, Any]] = []
    for row in local:
        all_records.append(_with_identifiers(dict(row)))
    for provider_rows in api_results.values():
        all_records.extend(_with_identifiers(dict(row)) for row in provider_rows)
    papers = rank_papers(deduplicate_papers(all_records), variants)[:limit]
    need_download = [paper for paper in papers if not paper.get("pdf_path") or paper.get("pdf_status") not in {"verified", "downloaded"}]
    return {
        "topic": topic,
        "query_variants": variants,
        "local_results": local,
        "api_results": api_results,
        "papers": papers,
        "need_download": need_download,
        "provider_counts": {name: len(rows) for name, rows in api_results.items()},
        "local_hits": len(local),
        "errors": errors,
    }
