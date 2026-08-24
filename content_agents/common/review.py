from __future__ import annotations

import os


def assess(text: str, *, default_risk: str = "low") -> tuple[str, str, list[str]]:
    blocked = [word.strip().lower() for word in os.getenv("CONTENT_BLOCKLIST", "").split(",") if word.strip()]
    lowered = text.lower()
    hits = [word for word in blocked if word in lowered]
    if hits:
        return "high", "blocked", [f"blocklist:{','.join(hits)}"]
    if default_risk == "high":
        return "high", "needs_review", ["high-risk source category requires human review"]
    if default_risk == "medium":
        return "medium", "needs_review", ["medium-risk content requires human review"]
    return "low", "approved", []

