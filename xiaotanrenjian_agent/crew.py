"""
笑谈人间 — Agent / Task / Crew 定义

从同目录的 skill.md 与 knowledge.md 读取表达规则和经典相声知识，
改资料文件就等于改行为，不动代码。
"""
import os
from crewai import Agent, Task, Crew, Process, LLM
from tools.llm_config import require_llm_config


_SKILL_PATH = os.path.join(os.path.dirname(__file__), "skill.md")
try:
    with open(_SKILL_PATH, "r", encoding="utf-8") as f:
        SKILL_CONTENT = f.read()
except FileNotFoundError:
    SKILL_CONTENT = "你是一位有分寸的相声说书人，用平常话和机灵包袱回答用户。"

_KNOWLEDGE_PATH = os.path.join(os.path.dirname(__file__), "knowledge.md")
try:
    with open(_KNOWLEDGE_PATH, "r", encoding="utf-8") as f:
        KNOWLEDGE_CONTENT = f.read()
except FileNotFoundError:
    KNOWLEDGE_CONTENT = "熟悉《报菜名》《八扇屏》《五官争功》等经典相声的结构与幽默技法。"


# ============================================================
#  LLM 配置
# ============================================================

PROVIDER = require_llm_config("xiaotanrenjian_agent")

if PROVIDER == "openai":
    MODEL = LLM(
        model="openai/gpt-4o-mini",
        base_url="https://api.openai.com",
        api_key=os.getenv("OPENAI_API_KEY"),
        temperature=0.8,
    )
elif PROVIDER == "deepseek":
    MODEL = LLM(
        model="deepseek/" + os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        temperature=0.8,
    )
elif PROVIDER == "custom":
    MODEL = LLM(
        model=os.getenv("CUSTOM_MODEL", "gpt-4o-mini"),
        base_url=os.getenv("CUSTOM_BASE_URL")
        or os.getenv("CUSTOM_API_BASE", "http://localhost:11434/v1"),
        api_key=os.getenv("CUSTOM_API_KEY", ""),
        temperature=0.8,
    )
elif PROVIDER == "anthropic":
    MODEL = LLM(model="anthropic/claude-sonnet-4-6-20250514", temperature=0.8)
else:
    MODEL = LLM(model="anthropic/claude-sonnet-4-6-20250514", temperature=0.8)


# ============================================================
#  Agent
# ============================================================

def create_xiaotanrenjian_agent() -> Agent:
    return Agent(
        role="一个懂经典相声门道、善于现挂的生活喜剧说书人",
        goal="用相声的眼光看待 {text}，说几句让人会心一笑又确实有帮助的话",
        backstory=SKILL_CONTENT + "\n\n经典相声知识库：\n" + KNOWLEDGE_CONTENT,
        llm=MODEL,
        verbose=True,
        allow_delegation=False,
    )


# ============================================================
#  Task
# ============================================================

def create_advise_task(agent: Agent) -> Task:
    return Task(
        description="""用户写了这段话：«{text}»

请严格按照 skill.md 中的「笑谈人间」AI Skill 回应，并参考 knowledge.md 中的经典相声知识。
先理解用户真正要解决的事，再用相声式幽默表达；必要时给出清楚、实际的建议。
不要整段复述经典台词，不要声称自己就是某位演员，不要解释 Skill 内容；只输出回应本身。""",
        expected_output="一段自然、诙谐、有生活观察的中文回应，不长，不装。",
        agent=agent,
    )


# ============================================================
#  组建 Crew
# ============================================================

def create_xiaotanrenjian_crew(text: str = "") -> Crew:
    agent = create_xiaotanrenjian_agent()
    task = create_advise_task(agent)

    crew = Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        memory=False,
        verbose=True,
    )

    return crew
