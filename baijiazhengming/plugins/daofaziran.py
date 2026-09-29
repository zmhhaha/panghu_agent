"""道法自然 插件。"""
from __future__ import annotations

from .persona import PersonaPlugin


class DaoFaZiRanPlugin(PersonaPlugin):
    slug = "daofaziran"
    crew_module = "baijiazhengming.personas.daofaziran_agent.crew"
    crew_factory = "create_daofaziran_crew"
