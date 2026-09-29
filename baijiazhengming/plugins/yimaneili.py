"""以马内利 插件。"""
from __future__ import annotations

from .persona import PersonaPlugin


class YiMaNeiLiPlugin(PersonaPlugin):
    slug = "yimaneili"
    crew_module = "baijiazhengming.personas.yimaneili_agent.crew"
    crew_factory = "create_yimaneili_crew"
