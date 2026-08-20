"""Shared paper schema, deduplication, and relevance ranking."""

from __future__ import annotations

import math
import re
from typing import Any, TypedDict


class PaperRecord(TypedDict, total=False):
    provider: str
    providers: list[str]
    type: str
    title: str
    date: str
    url: str
    doi: str
    authors: str
    venue: str
    cited_by_count: int
    abstract: str
    pdf_url: str
    open_access: bool
    identifiers: dict[str, str]
    relevance_score: float


def normalize_doi(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text, flags=re.I)
    text = re.sub(r"^doi:\s*", "", text, flags=re.I)
    return text.strip().rstrip(".").lower()


def make_paper(provider: str, **values: Any) -> PaperRecord:
    """Create a normalized record with a stable set of fields."""
    doi = normalize_doi(values.get("doi"))
    cited_by_count = values.get("cited_by_count", 0)
    try:
        cited_by_count = max(0, int(cited_by_count or 0))
    except (TypeError, ValueError):
        cited_by_count = 0

    return PaperRecord(
        provider=provider,
        providers=[provider],
        type=str(values.get("type") or "paper"),
        title=str(values.get("title") or "").strip(),
        date=str(values.get("date") or "")[:10],
        url=str(values.get("url") or "").strip(),
        doi=doi,
        authors=str(values.get("authors") or "").strip(),
        venue=str(values.get("venue") or "").strip(),
        cited_by_count=cited_by_count,
        abstract=str(values.get("abstract") or "").strip(),
        pdf_url=str(values.get("pdf_url") or "").strip(),
        open_access=bool(values.get("open_access")),
        identifiers=dict(values.get("identifiers") or {}),
    )


def _title_key(value: Any) -> str:
    text = str(value or "").lower()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text)


def _identities(record: PaperRecord) -> list[str]:
    keys: list[str] = []
    doi = normalize_doi(record.get("doi"))
    if doi:
        keys.append(f"doi:{doi}")
    title = _title_key(record.get("title"))
    if title:
        keys.append(f"title:{title}")
    url = str(record.get("url") or "").strip().lower().rstrip("/")
    if url:
        keys.append(f"url:{url}")
    return keys


def _merge(existing: PaperRecord, incoming: PaperRecord) -> PaperRecord:
    providers = list(existing.get("providers") or [existing.get("provider", "")])
    for provider in incoming.get("providers") or [incoming.get("provider", "")]:
        if provider and provider not in providers:
            providers.append(provider)
    existing["providers"] = providers

    for field in ("title", "date", "url", "doi", "authors", "venue", "pdf_url"):
        if not existing.get(field) and incoming.get(field):
            existing[field] = incoming[field]  # type: ignore[literal-required]

    if len(str(incoming.get("abstract") or "")) > len(str(existing.get("abstract") or "")):
        existing["abstract"] = str(incoming.get("abstract") or "")
    existing["cited_by_count"] = max(
        int(existing.get("cited_by_count") or 0),
        int(incoming.get("cited_by_count") or 0),
    )
    existing["open_access"] = bool(
        existing.get("open_access") or incoming.get("open_access")
    )
    identifiers = dict(existing.get("identifiers") or {})
    identifiers.update(incoming.get("identifiers") or {})
    existing["identifiers"] = identifiers
    return existing


def deduplicate_papers(records: list[PaperRecord]) -> list[PaperRecord]:
    """Deduplicate by DOI first and normalized title second, merging metadata."""
    merged: list[PaperRecord | None] = []
    key_to_index: dict[str, int] = {}
    for record in records:
        if not str(record.get("title") or "").strip():
            continue
        keys = _identities(record)
        matched_indexes = {key_to_index[key] for key in keys if key in key_to_index}
        if matched_indexes:
            target_index = min(matched_indexes)
            target = merged[target_index]
            if target is None:
                continue
            for duplicate_index in sorted(matched_indexes - {target_index}, reverse=True):
                duplicate = merged[duplicate_index]
                if duplicate is not None:
                    _merge(target, duplicate)
                    merged[duplicate_index] = None
                    for key, index in list(key_to_index.items()):
                        if index == duplicate_index:
                            key_to_index[key] = target_index
            _merge(target, record)
        else:
            target_index = len(merged)
            target = dict(record)  # type: ignore[assignment]
            merged.append(target)
        for key in _identities(target):
            key_to_index[key] = target_index
    return [record for record in merged if record is not None]


_STOPWORDS = {
    "about", "and", "current", "for", "from", "into", "latest", "of",
    "progress", "recent", "research", "review", "study", "the", "this",
    "with", "以及", "关于", "当前", "目前", "研究", "进展", "综述",
    "研究进展", "最新进展", "目前难点", "当前难点", "发展方向", "未来方向",
}


def _query_tokens(queries: list[str]) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for query in queries:
        for token in re.findall(r"[A-Za-z][A-Za-z0-9+.-]{1,}|[\u4e00-\u9fff]{2,}", query):
            normalized = token.lower()
            if normalized in _STOPWORDS or normalized in seen:
                continue
            seen.add(normalized)
            tokens.append(normalized)
    return tokens


def _score(record: PaperRecord, tokens: list[str], phrases: list[str]) -> float:
    title = str(record.get("title") or "").lower()
    abstract = str(record.get("abstract") or "").lower()
    venue = str(record.get("venue") or "").lower()
    score = 0.0
    for token in tokens:
        if token in title:
            score += 3.0
        elif token in abstract:
            score += 1.0
        elif token in venue:
            score += 0.5
    for phrase in phrases:
        normalized = phrase.lower().strip()
        if len(normalized) >= 5 and normalized in title:
            score += 3.0

    # Citation and recency are tie-breakers only after a lexical match.
    if score > 0:
        score += min(2.5, math.log10(int(record.get("cited_by_count") or 0) + 1))
        year_match = re.match(r"(19|20)\d{2}", str(record.get("date") or ""))
        if year_match:
            year = int(year_match.group(0))
            if year >= 2020:
                score += min(1.0, (year - 2019) * 0.12)
    return round(score, 3)


def rank_papers(records: list[PaperRecord], queries: list[str]) -> list[PaperRecord]:
    """Rank records without allowing citation count to overwhelm relevance."""
    tokens = _query_tokens(queries)
    phrases = [q for q in queries if q.strip()]
    ranked: list[PaperRecord] = []
    for record in records:
        item: PaperRecord = dict(record)  # type: ignore[assignment]
        item["relevance_score"] = _score(item, tokens, phrases)
        ranked.append(item)
    ranked.sort(
        key=lambda item: (
            float(item.get("relevance_score") or 0),
            str(item.get("date") or ""),
            int(item.get("cited_by_count") or 0),
        ),
        reverse=True,
    )
    return ranked
