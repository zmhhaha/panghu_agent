"""Optional LLM-assisted literature search planning.

The planner only produces query vocabulary and screening criteria.  Provider
metadata remains the source of truth for papers, identifiers and URLs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from tools.academic.query import build_query_variants
from tools.llm_config import REQUIRED_API_KEYS, get_llm_config_error, get_provider

from .config import Settings, settings


SEARCH_PLAN_SCHEMA_VERSION = 2


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bool_env(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _model(provider: str) -> str:
    if provider == "deepseek":
        return os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()
    if provider == "openai":
        return os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
    if provider == "custom":
        return os.getenv("CUSTOM_MODEL") or os.getenv("LLM_MODEL") or "gpt-4o-mini"
    return os.getenv("LLM_MODEL", "").strip()


def _base_url(provider: str) -> str:
    if provider == "deepseek":
        value = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    elif provider == "openai":
        value = os.getenv("OPENAI_BASE_URL", "https://api.openai.com")
    elif provider == "custom":
        value = os.getenv("CUSTOM_BASE_URL") or os.getenv("CUSTOM_API_BASE") or os.getenv("LLM_BASE_URL", "")
    else:
        value = os.getenv("LLM_BASE_URL", "")
    return value.strip().rstrip("/")


def _api_key(provider: str) -> str:
    key_name = REQUIRED_API_KEYS.get(provider)
    if key_name:
        return os.getenv(key_name, "").strip()
    return (os.getenv("CUSTOM_API_KEY") or os.getenv("LLM_API_KEY") or "").strip()


class LLMJsonClient:
    """Small OpenAI-compatible JSON client used by the two search helpers."""

    def __init__(self, provider: str, model: str, base_url: str, api_key: str, timeout: int = 30):
        self.provider = provider
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout

    @classmethod
    def from_environment(cls, config: Settings = settings) -> "LLMJsonClient | None":
        if not config.llm_enabled:
            return None
        provider = get_provider()
        if provider not in {"openai", "deepseek", "custom"}:
            return None
        if get_llm_config_error("literature_search_agent"):
            return None
        base_url = _base_url(provider)
        model = _model(provider)
        api_key = _api_key(provider)
        if not base_url or not model or (provider != "custom" and not api_key):
            return None
        return cls(provider, model, base_url, api_key, config.llm_timeout)

    def complete_json(self, system_prompt: str, user_payload: dict[str, Any]) -> Any:
        endpoint = self.base_url
        if not endpoint.endswith("/chat/completions"):
            if self.provider == "openai" and not endpoint.endswith("/v1"):
                endpoint += "/v1"
            endpoint += "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
        }
        response = requests.post(endpoint, headers=headers, json=payload, timeout=self.timeout)
        # Some OpenAI-compatible gateways do not implement response_format.
        # Retry once without it while keeping strict JSON parsing locally.
        if response.status_code == 400 and "response_format" in response.text.lower():
            payload.pop("response_format", None)
            response = requests.post(endpoint, headers=headers, json=payload, timeout=self.timeout)
        response.raise_for_status()
        body = response.json()
        choices = body.get("choices") or []
        if not choices:
            raise ValueError("LLM response has no choices")
        message = choices[0].get("message") or {}
        content = message.get("content", "")
        if isinstance(content, list):
            content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        text = str(content).strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
        return json.loads(text)


def _generic_query_variants(topic: str, max_variants: int) -> list[str]:
    """Expand a topic using only the shared lexical query builder."""
    variants: list[str] = []

    def add(value: str) -> None:
        value = str(value or "").strip()
        if value and value.lower() not in {item.lower() for item in variants}:
            variants.append(value)

    for value in build_query_variants(topic, max_variants):
        add(value)
    return variants[:max(1, max_variants)]


def _fallback_plan(topic: str, max_variants: int, target_count: int, reason: str = "") -> dict[str, Any]:
    variants = _generic_query_variants(topic, max_variants) or [topic.strip()]
    return {
        "version": 1,
        "topic": topic,
        "core_concepts": [item for item in variants[:3] if item],
        "synonyms": [],
        "query_variants": variants[:max_variants],
        "inclusion_criteria": ["标题或摘要明确涉及研究主题核心概念", "来自受支持的学术 API 且保留原始元数据"],
        "exclusion_criteria": ["仅命中泛化词而未命中主题术语", "标题和摘要均无法确认与主题相关"],
        "scope_requirements": [],
        "scope_filtering": {
            "active": False,
            "reason": "LLM scope_requirements unavailable; generic lexical filtering only",
        },
        "target_count": target_count,
        "llm": {
            "enabled": False,
            "used": False,
            "status": "fallback",
            "provider": get_provider(),
            "model": _model(get_provider()),
            "reason": reason or "LLM 未启用或配置不可用",
            "generated_at": _now(),
            "cache_hit": False,
        },
    }


def _cache_path(config: Settings, topic: str, max_variants: int, target_count: int, model: str) -> Path:
    key = "|".join((str(SEARCH_PLAN_SCHEMA_VERSION), topic.strip().lower(), str(max_variants), str(target_count), model))
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return config.data_dir / "search_plans" / f"{digest}.json"


def _clean_list(value: Any, limit: int = 20) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item.get("term") if isinstance(item, dict) else item).strip()
        if text and len(text) <= 240 and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _clean_scope_requirements(value: Any, limit: int = 12) -> list[dict[str, Any]]:
    """Normalize task-level scope groups returned by the retrieval expert.

    Terms are opaque vocabulary supplied by the model. The service does not
    know which materials, processes, diseases, or methods belong to a topic.
    """
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        terms = _clean_list(item.get("terms") or item.get("keywords") or item.get("synonyms"), 30)
        if not terms:
            continue
        name = str(item.get("name") or item.get("label") or f"group_{index + 1}").strip()[:120]
        required_value = item.get("required", True)
        if isinstance(required_value, str):
            required = required_value.strip().lower() not in {"0", "false", "no", "optional"}
        else:
            required = bool(required_value)
        result.append({"name": name, "terms": terms, "required": required})
        if len(result) >= limit:
            break
    return result


def _term_matches(text: str, term: str) -> bool:
    term = str(term or "").strip()
    if not term:
        return False
    # Literal matching avoids interpreting model vocabulary as executable regex.
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+._-]*", term):
        pattern = rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])"
        return re.search(pattern, text, flags=re.IGNORECASE) is not None
    return term.casefold() in text.casefold()


def matches_plan_scope(record: dict[str, Any], plan: dict[str, Any]) -> bool:
    """Apply only required scope groups supplied by the current LLM plan."""
    scope = plan.get("scope_requirements")
    llm = plan.get("llm") or {}
    if not llm.get("used") or not isinstance(scope, list):
        return True
    text = " ".join(str(record.get(field) or "") for field in ("title", "abstract", "venue"))
    for group in scope:
        if not isinstance(group, dict) or not group.get("required", True):
            continue
        terms = group.get("terms") or []
        if not any(_term_matches(text, term) for term in terms):
            return False
    return True


def _validate_payload(payload: Any, topic: str, max_variants: int, target_count: int) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("LLM search plan must be a JSON object")
    variants = _clean_list(payload.get("query_variants"), max_variants)
    if not variants:
        raise ValueError("LLM search plan has no query_variants")
    concepts = _clean_list(payload.get("core_concepts"), 20)
    synonyms = _clean_list(payload.get("synonyms"), 40)
    inclusion = _clean_list(payload.get("inclusion_criteria"), 20)
    exclusion = _clean_list(payload.get("exclusion_criteria"), 20)
    if not inclusion or not exclusion:
        raise ValueError("LLM search plan must include inclusion and exclusion criteria")
    scope_requirements = _clean_scope_requirements(payload.get("scope_requirements"))
    if not scope_requirements or not any(group.get("required") for group in scope_requirements):
        raise ValueError("LLM search plan must include required scope_requirements")
    try:
        requested_target = int(payload.get("target_count") or target_count)
    except (TypeError, ValueError):
        requested_target = target_count
    return {
        "version": SEARCH_PLAN_SCHEMA_VERSION,
        "topic": topic,
        "core_concepts": concepts,
        "synonyms": synonyms,
        "query_variants": variants,
        "inclusion_criteria": inclusion,
        "exclusion_criteria": exclusion,
        "scope_requirements": scope_requirements,
        "scope_filtering": {"active": True, "reason": "LLM supplied required scope groups"},
        "target_count": min(max(requested_target, 1), 100),
    }


def create_search_plan(
    topic: str,
    *,
    config: Settings = settings,
    target_count: int | None = None,
    max_variants: int | None = None,
) -> dict[str, Any]:
    """Create a cached LLM plan, or a deterministic plan on any failure."""
    target = min(max(int(target_count or config.search_limit), 1), 100)
    variants_limit = min(max(int(max_variants or config.max_search_variants), 1), 13)
    provider = get_provider()
    model = _model(provider)
    fallback_reason = get_llm_config_error("literature_search_agent") or "LLM 未启用"
    fallback = _fallback_plan(topic, variants_limit, target, fallback_reason)
    if not config.llm_enabled:
        fallback["llm"]["status"] = "disabled"
    client = LLMJsonClient.from_environment(config)
    if client is None:
        return fallback

    cache = _cache_path(config, topic, variants_limit, target, client.model)
    try:
        if cache.is_file():
            cached = json.loads(cache.read_text(encoding="utf-8"))
            plan = _validate_payload(cached, topic, variants_limit, target)
            plan["llm"] = {
                "enabled": True,
                "used": True,
                "status": "cached",
                "provider": client.provider,
                "model": client.model,
                "reason": "使用已缓存的检索计划",
                "generated_at": cached.get("generated_at") or _now(),
                "cache_hit": True,
            }
            return plan
        payload = client.complete_json(
            "你是文献检索专家。只输出 JSON，不要编造 DOI、作者、论文或 URL。生成可执行的中英文语义查询计划；query_variants 只写检索短语，不要使用数据库专属的布尔语法、括号、通配符或字段前缀，服务会为每个数据库单独转换查询。",
            {
                "topic": topic,
                "target_count": target,
                "max_query_variants": variants_limit,
                "scope_requirements_instruction": "Return required concept groups. Each group must have a name, literal terms/synonyms, and required=true when every included paper must mention that concept in title, abstract, or venue.",
                "output_schema": {
                    "core_concepts": ["..."],
                    "synonyms": ["..."],
                    "query_variants": ["..."],
                    "inclusion_criteria": ["..."],
                    "exclusion_criteria": ["..."],
                    "scope_requirements": [
                        {"name": "concept group", "terms": ["literal term", "synonym"], "required": True}
                    ],
                    "target_count": target,
                },
                "required_fields": [
                    "core_concepts", "synonyms", "query_variants",
                    "inclusion_criteria", "exclusion_criteria",
                    "scope_requirements", "target_count",
                ],
            },
        )
        plan = _validate_payload(payload, topic, variants_limit, target)
        plan["generated_at"] = _now()
        plan["llm"] = {
            "enabled": True,
            "used": True,
            "status": "used",
            "provider": client.provider,
            "model": client.model,
            "reason": "LLM 生成并通过结构校验",
            "generated_at": plan["generated_at"],
            "cache_hit": False,
        }
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        return plan
    except Exception as exc:
        fallback["llm"].update({
            "enabled": True,
            "status": "fallback",
            "provider": client.provider,
            "model": client.model,
            "reason": f"LLM 调用失败，已回退规则检索: {type(exc).__name__}: {str(exc)[:160]}",
        })
        return fallback
