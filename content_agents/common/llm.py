from __future__ import annotations

import json
from typing import Any

from .config import AgentConfig
from .http import HttpClientError, post_json


def generate_json(prompt: str, config: AgentConfig) -> dict[str, Any] | None:
    """Call an OpenAI-compatible endpoint when configured; otherwise return None."""
    if not config.llm_base_url or not config.llm_api_key:
        if config.llm_required:
            raise RuntimeError("LLM_REQUIRED=true but LLM_BASE_URL or LLM_API_KEY is missing")
        return None
    response = post_json(
        f"{config.llm_base_url}/chat/completions",
        {
            "model": config.llm_model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Return only valid JSON. Keep source claims conservative."},
                {"role": "user", "content": prompt},
            ],
        },
        headers={"Authorization": f"Bearer {config.llm_api_key}"},
        timeout=60,
    )
    try:
        content = response["choices"][0]["message"]["content"]
        return json.loads(content)
    except (KeyError, TypeError, IndexError, json.JSONDecodeError) as exc:
        raise HttpClientError("LLM returned an invalid structured response") from exc

