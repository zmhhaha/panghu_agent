"""秉笔春秋 插件。"""
from __future__ import annotations

from .persona import PersonaPlugin


class BingBiChunQiuPlugin(PersonaPlugin):
    slug = "bingbichunqiu"
    crew_module = "bingbichunqiu_agent.crew"
    crew_factory = "create_bingbichunqiu_crew"
