"""Small helpers for the stateless llm-service Responses contract."""
from __future__ import annotations

from typing import Any


def response_text(response: Any) -> str:
    """Read terminal assistant text; failures and partial output are not success."""
    if not isinstance(response, dict) or response.get("status") != "completed" or response.get("error"):
        raise ValueError("LLM response did not complete successfully")
    output = response.get("output")
    if not isinstance(output, list):
        raise ValueError("LLM response has no output list")
    texts = []
    for item in output:
        if not isinstance(item, dict):
            raise ValueError("LLM response has an invalid output item")
        if item.get("type") == "reasoning":
            continue
        if item.get("type") != "message" or item.get("role") != "assistant":
            raise ValueError("LLM returned a non-text output")
        parts = item.get("content")
        if not isinstance(parts, list):
            raise ValueError("LLM response has invalid message content")
        for part in parts:
            if not isinstance(part, dict) or part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                raise ValueError("LLM response contains a refusal or unsupported content")
            texts.append(part["text"])
    if not texts:
        raise ValueError("LLM response contains no assistant text")
    return "".join(texts)
