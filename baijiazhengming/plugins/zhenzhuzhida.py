"""真主至大 插件。"""
from __future__ import annotations

from .persona import PersonaPlugin


class ZhenZhuZhiDaPlugin(PersonaPlugin):
    slug = "zhenzhuzhida"
    crew_module = "baijiazhengming.personas.zhenzhuzhida_agent.crew"
    crew_factory = "create_zhenzhuzhida_crew"
