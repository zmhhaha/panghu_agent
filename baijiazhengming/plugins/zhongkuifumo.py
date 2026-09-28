"""钟馗伏魔 插件。"""
from __future__ import annotations

from .persona import PersonaPlugin


class ZhongKuiFuMoPlugin(PersonaPlugin):
    slug = "zhongkuifumo"
    crew_module = "zhongkuifumo_agent.crew"
    crew_factory = "create_zhongkuifumo_crew"
