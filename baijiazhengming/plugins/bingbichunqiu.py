"""秉笔春秋参考插件。"""
from __future__ import annotations

import os

from ..plugin import AgentPlugin, PluginResult


class BingBiChunQiuPlugin(AgentPlugin):
    slug = "bingbichunqiu"

    def configuration_error(self) -> str | None:
        if not os.getenv("LLM_BASE_URL", "").strip():
            return "百家争鸣配置不完整：LLM_BASE_URL 未注入。"
        if not os.getenv("LLM_SERVICE_TOKEN", "").strip():
            return "百家争鸣配置不完整：LLM_SERVICE_TOKEN 未注入。"
        return None

    def run(self, text: str, reference: str) -> PluginResult:
        # 延迟导入，单个插件配置错误不会阻止共享 API 启动。
        from bingbichunqiu_agent.crew import create_bingbichunqiu_crew

        result = create_bingbichunqiu_crew().kickoff(
            inputs={"text": text, "reference": reference}
        )
        return PluginResult(content=str(result))
