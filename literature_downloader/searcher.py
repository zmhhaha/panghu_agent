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
from .relevance_ranker import rank_candidates  # noqa: E402
from .search_planner import create_search_plan  # noqa: E402


def _tokens(topic: str) -> list[str]:
    return [
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9+.-]{1,}|[\u4e00-\u9fff]{2,}", topic)
    ]


def _with_identifiers(paper: dict[str, Any]) -> dict[str, Any]:
    if not paper.get("source_record"):
        paper["source_record"] = {
            key: value for key, value in paper.items()
            if key not in {"source_record", "pdf_path", "pdf_status", "verification_status"}
        }
    identifiers = dict(paper.get("identifiers") or {})
    if identifiers.get("arxiv") and not paper.get("arxiv_id"):
        paper["arxiv_id"] = identifiers["arxiv"]
    if identifiers.get("pmid") and not paper.get("pmid"):
        paper["pmid"] = identifiers["pmid"]
    return paper


def _is_relevant(record: dict[str, Any]) -> bool:
    """Reject provider fuzzy matches with no lexical evidence for the topic."""
    if float(record.get("relevance_score") or 0) <= 0:
        return False
    if record.get("relevance_method") == "llm" and record.get("llm_included") is False:
        return False
    return True


def _matches_topic_scope(topic: str, record: dict[str, Any]) -> bool:
    """Apply a conservative domain gate for high-risk broad query terms."""
    text = " ".join(str(record.get(field) or "") for field in ("title", "abstract", "venue")).lower()
    normalized_topic = topic.lower()
    if re.search(r"(?:\binp\b|indium\s+phosphide|磷化铟)", normalized_topic):
        material = re.search(r"\binp\b|indium\s+phosphide|in(?:ga)?as?p|gainp|磷化铟", text)
        if not material:
            return False
    if re.search(r"(?:dry\s+etch|plasma\s+etch|reactive\s+ion|干法刻蚀|等离子体刻蚀|刻蚀)", normalized_topic):
        process = re.search(r"dry\s+etch|plasma\s+etch|reactive\s+ion|\bicp(?:-rie)?\b|\brie\b|etching|etch", text)
        if not process:
            return False
    return True


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
    search_plan = create_search_plan(topic, config=config, target_count=limit, max_variants=config.max_search_variants)
    variants = [str(value).strip() for value in search_plan.get("query_variants") or [] if str(value).strip()]
    if not variants:
        variants = build_query_variants(topic, config.max_search_variants) or [topic]
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
        for variant in variants[:config.max_search_variants]:
            try:
                rows.extend(functions[provider](variant))
            except Exception as exc:
                provider_errors.append(f"{type(exc).__name__}: {str(exc)[:160]}")
        api_results[provider] = [_with_identifiers(dict(row)) for row in deduplicate_papers(rows)]
        if provider_errors:
            errors[provider] = "; ".join(provider_errors)

    all_records: list[dict[str, Any]] = []
    for row in local:
        all_records.append(_with_identifiers(dict(row)))
    for provider_rows in api_results.values():
        all_records.extend(_with_identifiers(dict(row)) for row in provider_rows)
    ranked = rank_papers(deduplicate_papers(all_records), variants)
    ranked, relevance = rank_candidates(ranked, topic=topic, plan=search_plan, config=config)
    # Provider APIs often return newest/highly cited records even when the
    # query match is empty. Never turn those zero-score fuzzy matches into
    # download targets; an empty result is safer than downloading another
    # topic's paper.
    papers = [paper for paper in ranked if _is_relevant(paper) and _matches_topic_scope(topic, paper)][:limit]
    need_download = [
        paper for paper in papers
        if not paper.get("pdf_path") or paper.get("pdf_status") not in {"verified", "downloaded"}
    ]
    # Prefer records that expose a legal/open PDF route. This keeps the larger
    # candidate pool useful: direct PDF, arXiv and OA records are attempted
    # before metadata-only/paywalled records that commonly end in 403/HTML.
    need_download.sort(
        key=lambda paper: (
            bool(paper.get("pdf_url")),
            bool((paper.get("identifiers") or {}).get("arxiv") or paper.get("arxiv_id")),
            bool(paper.get("open_access")),
            bool(paper.get("doi")),
            float(paper.get("relevance_score") or 0),
        ),
        reverse=True,
    )
    return {
        "topic": topic,
        "query_variants": variants,
        "search_plan": search_plan,
        "relevance": relevance,
        "local_results": local,
        "api_results": api_results,
        "papers": papers,
        "need_download": need_download,
        "provider_counts": {name: len(rows) for name, rows in api_results.items()},
        "local_hits": len(local),
        "errors": errors,
    }
