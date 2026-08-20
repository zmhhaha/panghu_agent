"""Optional LLM batch relevance ranking for API search results."""

from __future__ import annotations

import math
from typing import Any

from .config import Settings, settings
from .search_planner import LLMJsonClient


def _rule_result(record: dict[str, Any]) -> dict[str, Any]:
    item = dict(record)
    item.setdefault("relevance_method", "rules")
    item.setdefault("llm_included", None)
    item.setdefault("llm_relevance_score", None)
    item.setdefault("relevance_reason", "通过查询词词法相关性门槛")
    return item


def _parse_judgements(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get("judgements") or payload.get("results") or []
    if not isinstance(payload, list):
        raise ValueError("LLM relevance response must be a list")
    result = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
            score = float(item.get("relevance_score", item.get("score", 0)))
        except (TypeError, ValueError):
            continue
        if index < 0 or index >= 1000 or not math.isfinite(score):
            continue
        included_value = item.get("included", item.get("include", False))
        if isinstance(included_value, bool):
            included = included_value
        else:
            included = str(included_value).strip().lower() in {"1", "true", "yes", "include", "included"}
        result.append({
            "index": index,
            "score": min(max(score, 0.0), 1.0),
            "included": included,
            "reason": str(item.get("reason") or "").strip()[:500],
        })
    return result


def rank_candidates(
    records: list[dict[str, Any]],
    *,
    topic: str,
    plan: dict[str, Any],
    config: Settings = settings,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply LLM judgements when the planner successfully used an LLM.

    Records not returned by the model retain deterministic ranking and are not
    silently discarded. The caller still enforces the lexical relevance gate.
    """
    base = [_rule_result(record) for record in records]
    llm = plan.get("llm") or {}
    if not llm.get("used") or not base:
        return base, {"enabled": False, "used": False, "status": "rules", "judged": 0}
    client = LLMJsonClient.from_environment(config)
    if client is None:
        return base, {"enabled": True, "used": False, "status": "fallback", "judged": 0, "reason": "LLM 配置不可用"}

    candidate_limit = min(len(base), config.llm_max_candidates)
    candidates = []
    for index, record in enumerate(base[:candidate_limit]):
        candidates.append({
            "index": index,
            "title": str(record.get("title") or "")[:500],
            "abstract": str(record.get("abstract") or "")[:1800],
            "authors": str(record.get("authors") or "")[:300],
            "date": str(record.get("date") or "")[:20],
            "doi": str(record.get("doi") or "")[:160],
            "provider": str(record.get("provider") or "")[:80],
        })
    try:
        payload = client.complete_json(
            "你是文献筛选专家。只能根据给出的题目、摘要和元数据判断相关性，不得修改或补写 DOI、作者或 URL。只输出 JSON。",
            {
                "topic": topic,
                "scope_requirements": plan.get("scope_requirements", []),
                "inclusion_criteria": plan.get("inclusion_criteria", []),
                "exclusion_criteria": plan.get("exclusion_criteria", []),
                "candidates": candidates,
                "output_schema": {"judgements": [{"index": 0, "relevance_score": 0.0, "included": True, "reason": "..."}]},
            },
        )
        judgements = _parse_judgements(payload)
        for judgement in judgements:
            index = judgement["index"]
            if index >= len(base):
                continue
            base[index]["llm_relevance_score"] = judgement["score"]
            base[index]["llm_included"] = judgement["included"]
            base[index]["relevance_reason"] = judgement["reason"] or "LLM 完成相关性判断"
            base[index]["relevance_method"] = "llm"
        base.sort(
            key=lambda row: (
                1 if row.get("llm_included") is True else 0,
                float(row.get("llm_relevance_score") or 0),
                float(row.get("relevance_score") or 0),
            ),
            reverse=True,
        )
        return base, {"enabled": True, "used": True, "status": "used", "judged": len(judgements), "model": client.model}
    except Exception as exc:
        return base, {
            "enabled": True,
            "used": False,
            "status": "fallback",
            "judged": 0,
            "model": client.model,
            "reason": f"相关性重排失败，已保留规则排序: {type(exc).__name__}: {str(exc)[:160]}",
        }
