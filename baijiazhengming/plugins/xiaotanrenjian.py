"""笑谈人间 插件。"""
from __future__ import annotations

from .persona import PersonaPlugin


class XiaoTanRenJianPlugin(PersonaPlugin):
    slug = "xiaotanrenjian"
    crew_module = "baijiazhengming.personas.xiaotanrenjian_agent.crew"
    crew_factory = "create_xiaotanrenjian_crew"
