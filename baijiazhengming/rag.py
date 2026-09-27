"""共享 RAG 适配器。"""
from __future__ import annotations

from tools.rag_client import fetch_reference

from .registry import AgentDefinition


def get_reference(definition: AgentDefinition, text: str) -> str:
    if not definition.rag_enabled:
        return ""
    return fetch_reference(definition.slug, text)
