from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .service_token import resolve_service_token


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _csv(name: str, default: str) -> list[str]:
    value = os.getenv(name, default)
    return [item.strip().lower() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class AgentConfig:
    bot_name: str
    bot_version: str
    prompt_version: str
    data_dir: Path
    channels: list[str]
    draft_only: bool
    max_items: int
    lookback_hours: int
    hublog_base_url: str
    hublog_service_token: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_required: bool

    @classmethod
    def from_env(cls, bot_name: str) -> "AgentConfig":
        return cls(
            bot_name=bot_name,
            bot_version=os.getenv("BOT_VERSION", "0.1.0"),
            prompt_version=os.getenv("PROMPT_VERSION", "v1"),
            data_dir=Path(os.getenv("CONTENT_DATA_DIR", "/data/content-agents")),
            channels=_csv("AGENT_CHANNELS", "json"),
            draft_only=_bool("BOT_DRAFT_ONLY", True),
            max_items=max(1, int(os.getenv("BOT_MAX_ITEMS", "5"))),
            lookback_hours=max(1, int(os.getenv("BOT_LOOKBACK_HOURS", "24"))),
            hublog_base_url=os.getenv("HUBLOG_BASE_URL", "http://hublog-api.hublog.svc.cluster.local").rstrip("/"),
            hublog_service_token=resolve_service_token(bot_name),
            llm_base_url=os.getenv("LLM_BASE_URL", "").rstrip("/"),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
            llm_required=_bool("LLM_REQUIRED", False),
        )
