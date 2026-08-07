"""Multi-provider academic search orchestration."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, TypedDict

from .models import PaperRecord, deduplicate_papers, rank_papers
from .providers import PROVIDER_SEARCHERS
from .query import build_query_variants


class AcademicSearchResult(TypedDict):
    topic: str
    query_variants: list[str]
    papers: list[PaperRecord]
    provider_counts: dict[str, int]
    errors: dict[str, str]


_ALIASES = {
    "semantic scholar": "semantic_scholar",
    "semantic_scholar": "semantic_scholar",
    "semanticscholar": "semantic_scholar",
    "s2": "semantic_scholar",
    "arxiv": "arxiv",
    "openalex": "openalex",
    "crossref": "crossref",
    "pubmed": "pubmed",
}


def _provider_kwargs(name: str) -> dict[str, str]:
    email = os.getenv("ACADEMIC_CONTACT_EMAIL") or os.getenv("EVIDENCEGATE_MAILTO", "")
    if name in {"openalex", "crossref"}:
        return {"email": email}
    if name == "semantic_scholar":
        return {"api_key": os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")}
    if name == "pubmed":
        return {
            "api_key": os.getenv("NCBI_API_KEY", ""),
            "email": email,
        }
    return {}


def _search_one_provider(
    name: str,
    variants: list[str],
    per_provider: int,
) -> tuple[list[PaperRecord], str]:
    searcher = PROVIDER_SEARCHERS[name]
    records: list[PaperRecord] = []
    errors: list[str] = []
    # PubMed uses two API calls per query and has a stricter unauthenticated limit.
    selected_variants = variants[:1] if name == "pubmed" else variants[:2]
    for variant in selected_variants:
        try:
            records.extend(searcher(variant, per_provider, **_provider_kwargs(name)))
        except Exception as exc:
            errors.append(f"{variant[:40]}: {type(exc).__name__}: {str(exc)[:120]}")
    return deduplicate_papers(records), "; ".join(errors)


def _normalize_providers(values: list[str] | None) -> list[str]:
    if not values:
        return list(PROVIDER_SEARCHERS)
    normalized: list[str] = []
    for value in values:
        key = _ALIASES.get(value.strip().lower().replace("-", " "))
        if key and key not in normalized:
            normalized.append(key)
    if not normalized:
        raise ValueError("No supported academic providers were selected")
    return normalized


def search_academic(
    topic: str,
    *,
    limit: int = 30,
    per_provider: int = 10,
    providers: list[str] | None = None,
) -> AcademicSearchResult:
    """Search providers concurrently, then merge and relevance-rank results."""
    topic = topic.strip()
    if not topic:
        raise ValueError("topic must not be empty")
    limit = min(max(1, int(limit)), 100)
    per_provider = min(max(1, int(per_provider)), 25)
    variants = build_query_variants(topic)
    selected = _normalize_providers(providers)

    records_by_provider: dict[str, list[PaperRecord]] = {}
    provider_counts: dict[str, int] = {}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(5, len(selected))) as executor:
        futures = {
            executor.submit(_search_one_provider, name, variants, per_provider): name
            for name in selected
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                records, error = future.result()
            except Exception as exc:
                records, error = [], f"{type(exc).__name__}: {str(exc)[:160]}"
            records_by_provider[name] = records
            provider_counts[name] = len(records)
            if error:
                errors[name] = error

    all_records = [
        record
        for name in selected
        for record in records_by_provider.get(name, [])
    ]
    papers = rank_papers(deduplicate_papers(all_records), variants)[:limit]
    return AcademicSearchResult(
        topic=topic,
        query_variants=variants,
        papers=papers,
        provider_counts=provider_counts,
        errors=errors,
    )
