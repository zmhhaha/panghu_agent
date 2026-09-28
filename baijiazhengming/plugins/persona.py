"""人格插件的公共部分。

八个插件除了 `slug` 与 crew 工厂的名字以外完全一样，所以都继承这里。协议本身在
`../plugin.py`：插件只负责生成内容，不碰 HTTP、任务和存储。
"""
from __future__ import annotations

import importlib
import os

from ..plugin import AgentPlugin, PluginResult


class PersonaPlugin(AgentPlugin):
    slug: str
    crew_module: str
    crew_factory: str

    def configuration_error(self) -> str | None:
        """按**本 agent 的**凭据检查，而不是进程级 env。

        共享运行时一个进程里跑多个人格，`LLM_TOKEN_<SLUG>` 是各自的身份；只有旧的按
        服务部署才会只剩下进程级的 `LLM_SERVICE_TOKEN`，所以后者作为回退。
        """
        if not os.getenv("LLM_BASE_URL", "").strip():
            return f"{self.slug}: LLM_BASE_URL 未注入。"
        own = os.getenv(f"LLM_TOKEN_{self.slug.upper()}", "").strip()
        if not (own or os.getenv("LLM_SERVICE_TOKEN", "").strip()):
            return (
                f"{self.slug}: 缺少凭据 LLM_TOKEN_{self.slug.upper()}"
                "（共享运行时按人格注入；旧的按服务部署可用 LLM_SERVICE_TOKEN）"
            )
        return None

    def run(self, text: str, reference: str) -> PluginResult:
        # 延迟导入：一个人格的配置问题不该阻止整个共享 API 启动。
        module = importlib.import_module(self.crew_module)
        crew = getattr(module, self.crew_factory)(text=text)
        return PluginResult(content=str(crew.kickoff(inputs={"text": text, "reference": reference})))
