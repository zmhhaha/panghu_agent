"""
周公解梦 - Agent / Task / Crew 定义。

从同目录的 skill.md 读取完整 AI Skill 指令注入 Agent。
"""
import os

from crewai import Agent, Crew, LLM, Process, Task


_SKILL_PATH = os.path.join(os.path.dirname(__file__), "skill.md")
try:
    with open(_SKILL_PATH, "r", encoding="utf-8") as skill_file:
        SKILL_CONTENT = skill_file.read()
except FileNotFoundError:
    SKILL_CONTENT = (
        "你是一位熟悉周公解梦民俗的解梦先生。结合梦中细节与做梦人的现实处境，"
        "给出温和、审慎的象征性解读，不把梦说成确定预言。"
    )


PROVIDER = os.getenv("PROVIDER", "openai").lower()

if PROVIDER == "openai":
    MODEL = LLM(
        model="openai/gpt-4o-mini",
        base_url="https://api.openai.com",
        api_key=os.getenv("OPENAI_API_KEY"),
        temperature=0.7,
    )
elif PROVIDER == "deepseek":
    MODEL = LLM(
        model="deepseek/" + os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        temperature=0.7,
    )
elif PROVIDER == "custom":
    MODEL = LLM(
        model=os.getenv("CUSTOM_MODEL", "gpt-4o-mini"),
        base_url=os.getenv("CUSTOM_BASE_URL")
        or os.getenv("CUSTOM_API_BASE", "http://localhost:11434/v1"),
        api_key=os.getenv("CUSTOM_API_KEY", ""),
        temperature=0.7,
    )
elif PROVIDER == "anthropic":
    MODEL = LLM(
        model="anthropic/claude-sonnet-4-6-20250514",
        temperature=0.7,
    )
else:
    MODEL = LLM(
        model="anthropic/claude-sonnet-4-6-20250514",
        temperature=0.7,
    )


def create_zhougongjiemeng_agent() -> Agent:
    return Agent(
        role="一位熟悉周公解梦民俗与现代睡眠常识的解梦先生",
        goal=(
            "读懂 {text} 中的梦境细节，给出有传统文化味道、贴近现实且不故弄玄虚的解读"
        ),
        backstory=SKILL_CONTENT,
        llm=MODEL,
        verbose=True,
        allow_delegation=False,
    )


def create_interpret_dream_task(agent: Agent) -> Task:
    return Task(
        description="""用户讲述了这段梦境或提出了这个解梦问题：«{text}»

请严格按照 skill.md 中的「周公解梦」AI Skill 回应。
结合传统民俗象征、梦中情绪和用户现实处境进行解读。
不要把梦境说成确定预言，不要虚构古籍原文，也不要解释 Skill 内容；只输出给用户的回应。""",
        expected_output=(
            "一段清楚、有传统解梦味道又审慎的中文回应，说明主要象征、整体梦意和现实提醒。"
        ),
        agent=agent,
    )


def create_zhougongjiemeng_crew(text: str = "") -> Crew:
    agent = create_zhougongjiemeng_agent()
    task = create_interpret_dream_task(agent)
    return Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        memory=False,
        verbose=True,
    )
