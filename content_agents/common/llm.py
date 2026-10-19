from __future__ import annotations

import json
from typing import Any

from tools.responses_client import response_text

from .config import AgentConfig
from .http import HttpClientError, post_json


def generate_json(prompt: str, config: AgentConfig) -> dict[str, Any] | None:
    """Call llm-service Responses when configured; otherwise return None."""
    if not config.llm_base_url or not config.llm_api_key:
        if config.llm_required:
            raise RuntimeError("LLM_REQUIRED=true but LLM_BASE_URL or LLM_API_KEY is missing")
        return None
    response = post_json(
        f"{config.llm_base_url}/responses",
        {
            "model": config.llm_model,
            "temperature": 0.2,
            "store": False,
            "text": {"format": {"type": "json_object"}},
            "input": [
                {"role": "system", "content": "Return only valid JSON. Keep source claims conservative."},
                {"role": "user", "content": prompt},
            ],
        },
        headers={"Authorization": f"Bearer {config.llm_api_key}"},
        timeout=60,
    )
    try:
        return json.loads(response_text(response))
    except (KeyError, TypeError, IndexError, ValueError) as exc:
        raise HttpClientError("LLM returned an invalid structured response") from exc

