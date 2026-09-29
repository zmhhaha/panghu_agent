"""周公解梦 插件。"""
from __future__ import annotations

from .persona import PersonaPlugin


class ZhouGongJieMengPlugin(PersonaPlugin):
    slug = "zhougongjiemeng"
    crew_module = "baijiazhengming.personas.zhougongjiemeng_agent.crew"
    crew_factory = "create_zhougongjiemeng_crew"
