from __future__ import annotations

import os
from crewai import Agent, Crew, LLM, Process, Task
from tools.custom_tools import WebFetchTool, WebSearchTool
from tools.llm_config import require_llm_config


def build_llm() -> LLM:
    provider = require_llm_config("content_llm_service")
    if provider == "deepseek":
        return LLM(model="deepseek/" + os.getenv("DEEPSEEK_MODEL", "deepseek-chat"), base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"), api_key=os.getenv("DEEPSEEK_API_KEY"), temperature=0.2)
    if provider == "custom":
        return LLM(model=os.getenv("CUSTOM_MODEL", "gpt-4o-mini"), base_url=os.getenv("CUSTOM_BASE_URL", "http://localhost:11434/v1"), api_key=os.getenv("CUSTOM_API_KEY", ""), temperature=0.2)
    if provider == "anthropic":
        return LLM(model=os.getenv("ANTHROPIC_MODEL", "anthropic/claude-sonnet-4-20250514"), temperature=0.2)
    return LLM(model="openai/" + os.getenv("OPENAI_MODEL", "gpt-4o-mini"), base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com"), api_key=os.getenv("OPENAI_API_KEY"), temperature=0.2)


def create_meme_crew(candidate: dict) -> Crew:
    researcher = Agent(
        role="网络梗研究员",
        goal="识别可独立传播的短句梗，而不是复述新闻标题",
        backstory="你熟悉中文互联网的谐音、典故、四字成语化表达和事件型调侃。普通新闻、明星八卦和赛事标题都不能当作梗。",
        tools=[WebSearchTool(), WebFetchTool()],
        llm=build_llm(), allow_delegation=False, verbose=False,
    )
    task = Task(
        description=(
            "分析下面的 Bilibili 候选，必要时用网页工具核对背景。只有确实形成类似‘恒大空城计’、‘是关中王来了’、‘牛来’的短句梗才通过。"
            "只返回 JSON：{\"is_meme\":true,\"phrase\":\"不超过12个汉字\",\"context\":\"一句话背景\",\"joke\":\"一句话解释调侃\",\"confidence\":0.0}\n"
            f"标题：{candidate.get('title', '')}\n简介：{candidate.get('summary', '')}\n来源：{candidate.get('url', '')}"
        ),
        expected_output="严格的单个 JSON 对象", agent=researcher,
    )
    return Crew(agents=[researcher], tasks=[task], process=Process.sequential, memory=False, verbose=False)
