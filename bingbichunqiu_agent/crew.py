import os
from crewai import Agent, Task, Crew, Process, LLM
from tools.llm_config import require_llm_config

BASE = os.path.dirname(__file__)
with open(os.path.join(BASE, "skill.md"), encoding="utf-8") as f: SKILL = f.read()
with open(os.path.join(BASE, "knowledge.md"), encoding="utf-8") as f: KNOWLEDGE = f.read()
PROVIDER = require_llm_config("bingbichunqiu_agent")
if PROVIDER == "openai":
    MODEL = LLM(model="openai/gpt-4o-mini", base_url="https://api.openai.com", api_key=os.getenv("OPENAI_API_KEY"), temperature=0.8)
elif PROVIDER == "deepseek":
    MODEL = LLM(model="deepseek/" + os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"), base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"), api_key=os.getenv("DEEPSEEK_API_KEY"), temperature=0.8)
elif PROVIDER == "custom":
    MODEL = LLM(model=os.getenv("CUSTOM_MODEL", "gpt-4o-mini"), base_url=os.getenv("CUSTOM_BASE_URL") or os.getenv("CUSTOM_API_BASE", "http://localhost:11434/v1"), api_key=os.getenv("CUSTOM_API_KEY", ""), temperature=0.8)
else:
    MODEL = LLM(model="anthropic/claude-sonnet-4-6-20250514", temperature=0.8)

def create_bingbichunqiu_agent():
    return Agent(
        role="秉笔春秋史官",
        goal="依据可靠史料回答 {text}，辨析史实、传说与后世评价，并以史鉴今",
        backstory=SKILL + "\n\n" + KNOWLEDGE,
        llm=MODEL,
        verbose=True,
        allow_delegation=False,
    )

def create_advise_task(agent):
    return Task(
        description="""用户要查考这件事：{text}

请严格按照 skill.md 的史官准则回答。先给出清楚结论，再梳理相关史实和时代背景；明确区分史料记载、后世传说、学者解释与推测。若存在争议，说明不同观点及其依据；结尾可用一两句说明今天能从中借鉴什么，但不要把历史类比写成事实。不要编造引文或出处，不要输出工作过程，只输出给用户看的中文解读。""",
        expected_output="一段像史官写给后人的中文解读，结构清楚，事实与评价分明，语气克制而有温度。",
        agent=agent,
    )

def create_bingbichunqiu_crew(text=""):
    agent=create_bingbichunqiu_agent(); return Crew(agents=[agent], tasks=[create_advise_task(agent)], process=Process.sequential, memory=False, verbose=True)
