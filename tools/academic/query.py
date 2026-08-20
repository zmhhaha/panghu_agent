"""Deterministic query variants with a small, explicit technical dictionary."""

from __future__ import annotations

import re


TERM_MAP: tuple[tuple[str, str], ...] = (
    ("电感耦合等离子体", "inductively coupled plasma"),
    ("反应离子刻蚀", "reactive ion etching"),
    ("等离子体刻蚀", "plasma etching"),
    ("干法刻蚀", "dry etching"),
    ("磷化铟", "indium phosphide"),
    ("氮化镓", "gallium nitride"),
    ("砷化镓", "gallium arsenide"),
    ("光电探测器", "photodetector"),
    ("量子点", "quantum dots"),
    ("神经形态", "neuromorphic"),
    ("红外", "infrared"),
    ("半导体", "semiconductor"),
    ("刻蚀", "etching"),
    ("专利", "patent"),
)

_GOAL_PHRASES = (
    "研究进展", "最新进展", "目前难点", "当前难点", "发展方向", "未来方向",
    "recent advances", "research progress", "current challenges",
    "future directions", "review",
)


def _clean(value: str) -> str:
    value = re.sub(r"[，。；：、（）()_\\-]+", " ", value)
    return re.sub(r"\s+", " ", value).strip(" ,;:")


def build_query_variants(topic: str, max_variants: int = 4) -> list[str]:
    """Build useful variants without silently adding unrelated domain terms."""
    raw = _clean(topic)
    if not raw:
        return []

    translated = raw
    matched = False
    for source, target in TERM_MAP:
        if source in translated:
            translated = translated.replace(source, f" {target} ")
            matched = True
    translated = _clean(translated)

    core = translated
    for phrase in _GOAL_PHRASES:
        core = re.sub(re.escape(phrase), " ", core, flags=re.I)
    core = re.sub(r"\b(?:and|with|about)\b\s*$", "", core, flags=re.I)
    core = _clean(core)

    candidates: list[str] = []

    def add(value: str) -> None:
        normalized = _clean(value)
        if normalized and normalized.lower() not in {v.lower() for v in candidates}:
            candidates.append(normalized)

    # EvidenceGate-new uses a domain-specific InP etching expansion. Preserve
    # the same high-recall variants here instead of sending punctuation and
    # Chinese goal phrases verbatim to OpenAlex/Crossref.
    inp_topic = bool(re.search(r"inp|indium phosphide|磷化铟", raw, flags=re.I))
    etching_topic = bool(re.search(r"dry etching|plasma etching|reactive ion etching|干法刻蚀|等离子体刻蚀|刻蚀", translated, flags=re.I))
    if inp_topic and etching_topic:
        inp_variants = [
            "InP plasma etching",
            "indium phosphide plasma etching",
            "InP ICP etching",
            "InP ICP-RIE etching",
            "InP RIE",
            "indium phosphide reactive ion etching",
            "InP chlorine plasma etching",
            "InP Cl2 etching",
            "InP CH4 H2 etching",
            "InP waveguide etching",
            "InP photonic crystal etching",
            "InP surface grating ICP etching",
            "InP dry etching",
        ]
        return inp_variants[:max(1, max_variants)]

    if core.lower() != raw.lower():
        add(core)
    if matched:
        add(translated)
    add(raw)

    lower = translated.lower()
    if "dry etching" in lower or "plasma etching" in lower:
        add(re.sub(r"dry etching|plasma etching", "reactive ion etching", core, flags=re.I))
        add(re.sub(r"dry etching|plasma etching", "ICP etching", core, flags=re.I))

    return candidates[:max(1, max_variants)]
