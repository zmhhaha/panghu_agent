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


def create_meme_batch_crew(candidates: list[dict]) -> Crew:
    researcher = Agent(
        role="internet meme researcher",
        goal="identify reusable short event-based internet memes and reject ordinary headlines",
        backstory="You understand Chinese internet wordplay, nicknames, idiom remixes, and event-based jokes.",
        tools=[WebSearchTool(), WebFetchTool()],
        llm=build_llm(), allow_delegation=False, verbose=False,
    )
    task = Task(
        description=(
            "Analyze every Bilibili candidate below. Only approve phrases similar to "
            "恒大空城计, 是关中王来了, 华强买瓜, 狐狸与酱板鸭. Reject ordinary news, full event headlines, "
            "celebrity/sports reposts, and generic summaries. Return ONLY a JSON array, "
            "with one object per input candidate in the same order. Each object must contain "
            "is_meme (boolean), phrase (<=12 Chinese characters), context, joke, confidence.\n"
            + __import__("json").dumps(candidates, ensure_ascii=False)
        ),
        expected_output="A JSON array of judgement objects",
        agent=researcher,
    )
    return Crew(agents=[researcher], tasks=[task], process=Process.sequential, memory=False, verbose=False)


def create_github_batch_crew(candidates: list[dict]) -> Crew:
    analyst = Agent(
        role="open source project analyst",
        goal="explain GitHub projects accurately and concisely for Chinese readers",
        backstory="You inspect project descriptions and public repository pages, distinguish facts from inference, and never invent features.",
        tools=[WebSearchTool(), WebFetchTool()],
        llm=build_llm(), allow_delegation=False, verbose=False,
    )
    task = Task(
        description=(
            "Analyze every GitHub repository candidate below. Return ONLY a JSON array in the same order. "
            "Each object must contain: overview (2-4 Chinese sentences explaining what it does and who needs it), "
            "highlights (2-4 concrete capabilities or use cases), getting_started (one concise practical starting suggestion), "
            "cautions (one sentence about maturity, license, security, or dependencies). Use only the supplied facts or public repository information; never invent numbers.\n"
            + __import__("json").dumps(candidates, ensure_ascii=False)
        ),
        expected_output="A JSON array of project analysis objects",
        agent=analyst,
    )
    return Crew(agents=[analyst], tasks=[task], process=Process.sequential, memory=False, verbose=False)
