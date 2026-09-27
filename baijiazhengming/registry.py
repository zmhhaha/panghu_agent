"""经过严格校验的 Agent 注册表。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .plugin import AgentPlugin
from .plugins.bingbichunqiu import BingBiChunQiuPlugin


_SLUG = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
_PLUGIN_TYPES: dict[str, type[AgentPlugin]] = {
    "bingbichunqiu": BingBiChunQiuPlugin,
}


@dataclass(frozen=True)
class AgentDefinition:
    slug: str
    display_name: str
    service_name: str
    host: str
    plugin_name: str
    legacy_path: str
    llm_model: str
    rag_enabled: bool
    max_input_length: int
    max_output_length: int
    concurrency: int
    enabled: bool
    icon: str
    tagline: str
    input_label: str
    input_placeholder: str
    submit_label: str
    empty_message: str
    waiting_message: str


class AgentRegistry:
    def __init__(self, definitions: list[AgentDefinition]):
        self._by_slug = {item.slug: item for item in definitions}
        self._by_host = {item.host: item for item in definitions if item.host}
        if len(self._by_slug) != len(definitions):
            raise ValueError("registry contains duplicate agent slugs")
        if len(self._by_host) != len([item for item in definitions if item.host]):
            raise ValueError("registry contains duplicate public hosts")

    @classmethod
    def load(cls, path: Path) -> "AgentRegistry":
        # registry.yaml 使用 JSON 语法（JSON 是 YAML 子集），避免增加运行时依赖。
        raw = json.loads(path.read_text(encoding="utf-8"))
        agents = raw.get("agents")
        if not isinstance(agents, dict) or not agents:
            raise ValueError("registry must contain a non-empty agents object")
        definitions = [cls._definition(slug, data) for slug, data in agents.items()]
        return cls(definitions)

    @staticmethod
    def _definition(slug: str, data: object) -> AgentDefinition:
        if not _SLUG.fullmatch(slug) or not isinstance(data, dict):
            raise ValueError(f"invalid agent definition: {slug!r}")
        plugin_name = str(data.get("plugin", ""))
        if plugin_name not in _PLUGIN_TYPES:
            raise ValueError(f"plugin is not allowlisted: {plugin_name!r}")
        host = str(data.get("host", "")).lower()
        if not _HOST.fullmatch(host):
            raise ValueError(f"invalid host for {slug}: {host!r}")
        service_name = str(data.get("service_name", ""))
        if not _SLUG.fullmatch(service_name):
            raise ValueError(f"invalid service_name for {slug}")
        legacy_path = str(data.get("legacy_path", ""))
        if legacy_path != f"/{service_name}":
            raise ValueError(f"legacy_path must match service_name for {slug}")
        max_input = int(data.get("max_input_length", 2000))
        max_output = int(data.get("max_output_length", 600))
        concurrency = int(data.get("concurrency", 2))
        if not 1 <= max_input <= 20_000 or not 1 <= max_output <= 20_000:
            raise ValueError(f"invalid text limits for {slug}")
        if not 1 <= concurrency <= 16:
            raise ValueError(f"invalid concurrency for {slug}")
        return AgentDefinition(
            slug=slug,
            display_name=str(data["display_name"]),
            service_name=service_name,
            host=host,
            plugin_name=plugin_name,
            legacy_path=legacy_path,
            llm_model=str(data.get("llm_model", "deepseek-guarded")),
            rag_enabled=bool(data.get("rag_enabled", False)),
            max_input_length=max_input,
            max_output_length=max_output,
            concurrency=concurrency,
            enabled=bool(data.get("enabled", True)),
            icon=str(data.get("icon", "")),
            tagline=str(data.get("tagline", "")),
            input_label=str(data.get("input_label", "输入")),
            input_placeholder=str(data.get("input_placeholder", "")),
            submit_label=str(data.get("submit_label", "提交")),
            empty_message=str(data.get("empty_message", "请先输入内容。")),
            waiting_message=str(data.get("waiting_message", "正在处理")),
        )

    def list_enabled(self) -> list[AgentDefinition]:
        return [item for item in self._by_slug.values() if item.enabled]

    def get(self, slug: str) -> AgentDefinition | None:
        item = self._by_slug.get(slug)
        return item if item and item.enabled else None

    def from_host(self, host: str) -> AgentDefinition | None:
        normalized = host.split(",", 1)[0].strip().lower().split(":", 1)[0]
        item = self._by_host.get(normalized)
        return item if item and item.enabled else None

    def create_plugin(self, definition: AgentDefinition) -> AgentPlugin:
        return _PLUGIN_TYPES[definition.plugin_name]()


@lru_cache(maxsize=1)
def get_registry() -> AgentRegistry:
    return AgentRegistry.load(Path(__file__).with_name("registry.yaml"))
