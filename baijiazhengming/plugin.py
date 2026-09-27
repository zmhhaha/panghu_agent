"""Agent 插件协议。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class PluginResult:
    content: str


class AgentPlugin(ABC):
    """领域插件只负责生成内容，不管理 HTTP、任务或存储。"""

    slug: str

    def configuration_error(self) -> str | None:
        return None

    @abstractmethod
    def run(self, text: str, reference: str) -> PluginResult:
        raise NotImplementedError
