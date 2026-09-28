"""佛法无边 插件。"""
from __future__ import annotations

from .persona import PersonaPlugin


class FoFaWuBianPlugin(PersonaPlugin):
    slug = "fofawubian"
    crew_module = "fofawubian_agent.crew"
    crew_factory = "create_fofawubian_crew"
