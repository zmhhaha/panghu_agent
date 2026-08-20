"""Domain-neutral deterministic query variants.

The retrieval expert supplies topic vocabulary and translations at task time.
This fallback only normalizes punctuation and removes generic review wording;
it must not embed a material, disease, process, or other subject dictionary.
"""

from __future__ import annotations

import re


_GENERIC_GOAL_PHRASES = (
    "研究进展",
    "目前难点",
    "当前难点",
    "发展方向",
    "未来方向",
    "research progress",
    "recent advances",
    "current challenges",
    "future directions",
    "state of the art",
    "review",
)


def _clean(value: str) -> str:
    value = re.sub(r"[,:;，。；：、（）()_\\-]+", " ", value)
    return re.sub(r"\s+", " ", value).strip(" ,;:")


def build_query_variants(topic: str, max_variants: int = 4) -> list[str]:
    """Build conservative variants without silently adding domain terms."""
    raw = _clean(str(topic or ""))
    if not raw:
        return []

    core = raw
    for phrase in _GENERIC_GOAL_PHRASES:
        core = re.sub(re.escape(phrase), " ", core, flags=re.IGNORECASE)
    core = _clean(core)

    candidates: list[str] = []

    def add(value: str) -> None:
        normalized = _clean(value)
        if normalized and normalized.casefold() not in {item.casefold() for item in candidates}:
            candidates.append(normalized)

    if core.casefold() != raw.casefold():
        add(core)
    add(raw)
    if core.casefold() != raw.casefold():
        add(f"{core} review")
    return candidates[:max(1, max_variants)]
