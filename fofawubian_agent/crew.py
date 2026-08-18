"""
佛法无边 — Agent / Task / Crew 定义

从同目录的 skill.md 读取完整的 AI Skill 指令注入 Agent，
改 skill.md 就等于改行为，不动代码。
"""
import os
from crewai import Agent, Task, Crew, Process, LLM
from tools.llm_config import require_llm_config


# ============================================================
#  加载 skill.md（与 crew.py 同目录）
# ============================================================

_SKILL_PATH = os.path.join(os.path.dirname(__file__), "skill.md")
try:
    with open(_SKILL_PATH, "r", encoding="utf-8") as f:
        SKILL_CONTENT = f.read()
except FileNotFoundError:
    SKILL_CONTENT = "你是一个懂佛法的朋友，用平常话回几句感悟。"


# ============================================================
#  LLM 配置
# ============================================================

PROVIDER = require_llm_config("fofawubian_agent")

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
        base_url=os.getenv("CUSTOM_BASE_URL") or os.getenv("CUSTOM_API_BASE", "http://localhost:11434/v1"),
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

def create_fofawubian_agent() -> Agent:
    return Agent(
        role="一个懂佛法的朋友",
        goal="用佛法的眼光看待 {text}，用平常话说几句让人心里一亮的话",
        backstory=SKILL_CONTENT,
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

请你严格按照 skill.md 中的「佛法无边」AI Skill 来回应。
不需要解释 Skill 内容，只需要输出回应本身。""",
        expected_output="""一段像人话的回应，不长，不装。""",
        agent=agent,
    )


# ============================================================
#  组建 Crew
# ============================================================

def create_fofawubian_crew(text: str = "") -> Crew:
    agent = create_fofawubian_agent()
    task = create_advise_task(agent)

    crew = Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        memory=False,
        verbose=True,
    )

    return crew
