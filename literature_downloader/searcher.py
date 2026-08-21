"""Local and external academic search orchestration."""

from __future__ import annotations

import re
import sys
import inspect
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
from .search_planner import create_search_plan, matches_plan_scope  # noqa: E402


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


def _split_boolean(value: str, operator: str) -> list[str]:
    """Split a simple Boolean query without splitting inside quotes/groups."""
    text = str(value or "").strip()
    if not text:
        return []
    marker = operator.upper()
    parts: list[str] = []
    start = 0
    depth = 0
    quoted = False
    index = 0
    while index < len(text):
        char = text[index]
        if char == '"':
            quoted = not quoted
            index += 1
            continue
        if not quoted:
            if char == "(":
                depth += 1
            elif char == ")":
                depth = max(0, depth - 1)
            elif depth == 0 and text[index:index + len(marker)].upper() == marker:
                before = text[index - 1] if index else " "
                after_index = index + len(marker)
                after = text[after_index] if after_index < len(text) else " "
                if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
                    parts.append(text[start:index].strip())
                    start = after_index
                    index = after_index
                    continue
        index += 1
    parts.append(text[start:].strip())
    return [part for part in parts if part]


def _unwrap_query_group(value: str) -> str:
    text = str(value or "").strip()
    while text.startswith("(") and text.endswith(")"):
        depth = 0
        quoted = False
        wraps_entire_value = True
        for index, char in enumerate(text):
            if char == '"':
                quoted = not quoted
            elif not quoted and char == "(":
                depth += 1
            elif not quoted and char == ")":
                depth -= 1
                if depth == 0 and index != len(text) - 1:
                    wraps_entire_value = False
                    break
        if not wraps_entire_value or depth != 0:
            break
        text = text[1:-1].strip()
    return text


def _clean_query_atom(value: str) -> str:
    text = re.sub(r"[*?]", "", str(value or ""))
    text = re.sub(r"[()\[\]{}]", " ", text)
    text = text.replace('"', " ")
    text = re.sub(r"\b(?:AND|OR|NOT)\b", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def _query_atoms(query: str) -> list[str]:
    """Choose one useful term from each required Boolean clause.

    LLM queries are intentionally richer than individual provider syntaxes.
    Selecting the first alternative in each AND clause keeps the material and
    method anchors while avoiding unsupported OR/wildcard expressions.
    """
    clauses = _split_boolean(_unwrap_query_group(query), "AND") or [str(query or "")]
    atoms: list[str] = []
    seen: set[str] = set()
    for clause in clauses:
        alternatives = _split_boolean(_unwrap_query_group(clause), "OR") or [clause]
        atom = next((_clean_query_atom(item) for item in alternatives if _clean_query_atom(item)), "")
        if atom and atom.casefold() not in seen:
            atoms.append(atom)
            seen.add(atom.casefold())
    return atoms


def _provider_query(provider: str, query: str) -> str:
    """Translate the shared query plan to a provider-compatible expression."""
    atoms = _query_atoms(query)
    if provider == "arXiv":
        return " AND ".join(f'all:"{atom}"' for atom in atoms[:8])
    return " ".join(atoms)


def _provider_queries(provider: str, variants: list[str]) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        query = _provider_query(provider, variant)
        if query and query.casefold() not in seen:
            queries.append(query)
            seen.add(query.casefold())
    return queries


def _is_rate_limited(exc: Exception) -> bool:
    text = str(exc).lower()
    return "429" in text or "too many requests" in text or "rate limit" in text


def search_literature(
    topic: str,
    db: Database,
    *,
    search_limit: int | None = None,
    per_provider: int | None = None,
    providers: list[str] | None = None,
    search_round: int = 0,
    search_plan: dict[str, Any] | None = None,
    use_llm: bool = True,
    config: Settings = settings,
    provider_cooldowns: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Search local verified papers and external providers.

    Provider failures are isolated and returned in ``errors`` so one unavailable
    endpoint does not prevent the user from reviewing the remaining results.
    """
    topic = topic.strip()
    if not topic:
        raise ValueError("topic must not be empty")
    limit = max(int(config.search_limit if search_limit is None else search_limit), 0)
    per_limit = min(max(int(per_provider or config.per_provider), 1), 25)
    search_round = max(0, int(search_round or 0))
    search_plan = search_plan or create_search_plan(
        topic, config=config, target_count=limit, max_variants=config.max_search_variants
    )
    variants = [str(value).strip() for value in search_plan.get("query_variants") or [] if str(value).strip()]
    if not variants:
        variants = build_query_variants(topic, config.max_search_variants) or [topic]
    selected = providers or ["OpenAlex", "Crossref", "arXiv", "Semantic Scholar"]

    # Local-library hits do not change between provider pages. Query them only
    # on the first round; the aggregate step still keeps them in the report.
    local = db.search_local(_tokens(topic), limit=limit or None) if search_round == 0 else []
    local = [{**row, "provider": "LocalLibrary", "providers": ["LocalLibrary"]} for row in local]
    # The shared library stores a task-owned copy of each paper. Collapse
    # those copies before counting/reporting hits so one paper is listed once.
    local = deduplicate_papers(local)
    api_results: dict[str, list[dict[str, Any]]] = {}
    provider_queries: dict[str, list[str]] = {}
    errors: dict[str, str] = {}
    cooldowns = provider_cooldowns if provider_cooldowns is not None else {}
    offset = search_round * per_limit

    def call_provider(function: Any, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        """Keep compatibility with older test/custom adapters without offset."""
        target = getattr(function, "side_effect", None) or function
        try:
            parameters = inspect.signature(target).parameters
            accepts_offset = "offset" in parameters or any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in parameters.values()
            )
        except (TypeError, ValueError):
            accepts_offset = True
        if not accepts_offset:
            return function(query, per_limit, **kwargs)
        try:
            return function(query, per_limit, offset=offset, **kwargs)
        except TypeError as exc:
            if "offset" not in str(exc):
                raise
            return function(query, per_limit, **kwargs)

    def openalex_query(query: str) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"email": config.contact_email}
        if config.openalex_api_key:
            kwargs["api_key"] = config.openalex_api_key
        return call_provider(search_openalex, query, **kwargs)

    functions = {
        "OpenAlex": openalex_query,
        "Crossref": lambda query: call_provider(search_crossref, query, email=config.contact_email),
        "arXiv": lambda query: call_provider(search_arxiv, query),
        "Semantic Scholar": lambda query: call_provider(
            search_semantic_scholar, query, api_key=config.semantic_scholar_api_key
        ),
    }
    for provider in selected:
        if provider not in functions:
            errors[provider] = "unsupported provider"
            continue
        retry_round = int(cooldowns.get(provider, -1))
        if retry_round > search_round:
            api_results[provider] = []
            provider_queries[provider] = []
            errors[provider] = f"skipped while rate-limit cooldown is active; retry from round {retry_round + 1}"
            continue
        rows: list[dict[str, Any]] = []
        provider_errors: list[str] = []
        queries = _provider_queries(provider, variants[:config.max_search_variants])
        provider_queries[provider] = []
        for query in queries:
            provider_queries[provider].append(query)
            try:
                rows.extend(functions[provider](query))
            except Exception as exc:
                provider_errors.append(f"{type(exc).__name__}: {str(exc)[:160]}")
                if _is_rate_limited(exc):
                    # Retry this provider after one quiet round. The HTTP
                    # client already honors Retry-After within the request;
                    # this cooldown prevents a burst across search rounds.
                    cooldowns[provider] = search_round + 2
                    break
        api_results[provider] = [_with_identifiers(dict(row)) for row in deduplicate_papers(rows)]
        if provider_errors:
            errors[provider] = "; ".join(provider_errors)

    all_records: list[dict[str, Any]] = []
    for row in local:
        all_records.append(_with_identifiers(dict(row)))
    for provider_rows in api_results.values():
        all_records.extend(_with_identifiers(dict(row)) for row in provider_rows)
    ranked = rank_papers(deduplicate_papers(all_records), variants)
    if use_llm:
        ranked, relevance = rank_candidates(ranked, topic=topic, plan=search_plan, config=config)
    else:
        relevance = {
            "enabled": bool((search_plan.get("llm") or {}).get("enabled")),
            "used": False,
            "status": "rules",
            "judged": 0,
            "reason": "LLM 检索规划已复用；本轮只进行确定性相关性排序",
        }
    # Provider APIs often return newest/highly cited records even when the
    # query match is empty. Never turn those zero-score fuzzy matches into
    # download targets; an empty result is safer than downloading another
    # topic's paper.
    papers = [
        paper for paper in ranked
        if _is_relevant(paper) and matches_plan_scope(paper, search_plan)
    ]
    if limit > 0:
        papers = papers[:limit]
    need_download = [
        paper for paper in papers
        if (
            not paper.get("pdf_path")
            or paper.get("pdf_status") not in {"verified", "downloaded"}
            or not Path(str(paper.get("pdf_path"))).is_file()
        )
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
        "search_round": search_round + 1,
        "offset": offset,
        "query_variants": variants,
        "search_plan": search_plan,
        "relevance": relevance,
        "local_results": local,
        "api_results": api_results,
        "provider_queries": provider_queries,
        "papers": papers,
        "need_download": need_download,
        "provider_counts": {name: len(rows) for name, rows in api_results.items()},
        "local_hits": len(local),
        "errors": errors,
    }


def merge_search_results(
    results: list[dict[str, Any]],
    *,
    limit: int,
    topic: str,
) -> dict[str, Any]:
    """Merge provider pages from several search rounds without another LLM call."""
    if not results:
        return {
            "topic": topic,
            "query_variants": [],
            "search_plan": {},
            "relevance": {"status": "rules", "used": False, "judged": 0},
            "local_results": [],
            "api_results": {},
            "provider_queries": {},
            "papers": [],
            "need_download": [],
            "provider_counts": {},
            "local_hits": 0,
            "errors": {},
            "search_rounds": [],
        }

    first = results[0]
    variants = list(first.get("query_variants") or [])
    plan = dict(first.get("search_plan") or {})
    local = deduplicate_papers([
        dict(row)
        for result in results
        for row in result.get("local_results") or []
    ])
    api_results: dict[str, list[dict[str, Any]]] = {}
    provider_queries: dict[str, list[str]] = {}
    for provider in {
        provider
        for result in results
        for provider in (result.get("api_results") or {})
    }:
        api_results[provider] = deduplicate_papers([
            dict(row)
            for result in results
            for row in (result.get("api_results") or {}).get(provider, [])
        ])
        provider_queries[provider] = list(dict.fromkeys(
            query
            for result in results
            for query in (result.get("provider_queries") or {}).get(provider, [])
        ))

    candidates = deduplicate_papers([
        dict(row)
        for result in results
        for row in result.get("papers") or []
    ])
    ranked = rank_papers(candidates, variants)
    ranked = [
        paper for paper in ranked
        if _is_relevant(paper) and matches_plan_scope(paper, plan)
    ]
    # Preserve first-round LLM judgements while allowing later pages to fill
    # the result set without another model request.
    ranked.sort(
        key=lambda paper: (
            1 if paper.get("llm_included") is True else 0,
            float(paper.get("llm_relevance_score") or 0),
            float(paper.get("relevance_score") or 0),
            str(paper.get("date") or ""),
        ),
        reverse=True,
    )
    if limit > 0:
        ranked = ranked[:limit]

    need_download = [
        paper for paper in ranked
        if (
            not paper.get("pdf_path")
            or paper.get("pdf_status") not in {"verified", "downloaded"}
            or not Path(str(paper.get("pdf_path"))).is_file()
        )
    ]
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

    errors: dict[str, str] = {}
    for result in results:
        for provider, error in (result.get("errors") or {}).items():
            if error and provider not in errors:
                errors[provider] = str(error)
            elif error and str(error) not in errors[provider]:
                errors[provider] += f"; {error}"
    relevance = dict(first.get("relevance") or {})
    relevance["judged"] = sum(int((result.get("relevance") or {}).get("judged") or 0) for result in results)
    relevance["rounds"] = len(results)
    round_stats = []
    for result in results:
        round_stats.append({
            "round": int(result.get("search_round") or len(round_stats) + 1),
            "offset": int(result.get("offset") or 0),
            "found": len(result.get("papers") or []),
            "local_hits": int(result.get("local_hits") or 0),
            "provider_counts": dict(result.get("provider_counts") or {}),
            "errors": dict(result.get("errors") or {}),
        })

    return {
        "topic": topic,
        "query_variants": variants,
        "search_plan": plan,
        "relevance": relevance,
        "local_results": local,
        "api_results": api_results,
        "provider_queries": provider_queries,
        "papers": ranked,
        "need_download": need_download,
        "provider_counts": {provider: len(rows) for provider, rows in api_results.items()},
        "local_hits": len(local),
        "errors": errors,
        "search_rounds": round_stats,
    }
