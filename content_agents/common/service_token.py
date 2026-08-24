from __future__ import annotations

import json
import os
from typing import Any


def _token_from_entry(entry: Any) -> str:
    if isinstance(entry, str):
        return entry.strip()
    if not isinstance(entry, dict):
        return ""
    for key in ("token", "raw_token", "service_token"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def resolve_service_token(bot_name: str) -> str:
    """Resolve one bot token from a shared raw-token JSON envelope.

    The shared envelope is mounted only in the content-agent namespace. Hublog
    receives a separate hash-only envelope and cannot derive these values.
    HUBLOG_SERVICE_TOKEN remains a compatibility fallback for local runs.
    """
    raw = os.getenv("HUBLOG_SERVICE_TOKENS", "").strip()
    if raw:
        try:
            document = json.loads(raw)
        except json.JSONDecodeError:
            document = None
        if isinstance(document, dict):
            token = _token_from_entry(document.get(bot_name))
            if token:
                return token
    return os.getenv("HUBLOG_SERVICE_TOKEN", "").strip()
