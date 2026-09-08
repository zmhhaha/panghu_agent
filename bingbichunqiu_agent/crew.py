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
    return Agent(role="秉笔春秋史官", goal="以可靠中国史料回答问题并以史鉴今", backstory=SKILL + "\n" + KNOWLEDGE, llm=MODEL, verbose=True, allow_delegation=False)

def create_advise_task(agent):
    return Task(description="围绕用户问题，区分史实、传说与评价，给出有出处意识、克制清晰的回答：{text}", expected_output="一段结构清楚的中文历史解读", agent=agent)

def create_bingbichunqiu_crew(text=""):
    agent=create_bingbichunqiu_agent(); return Crew(agents=[agent], tasks=[create_advise_task(agent)], process=Process.sequential, memory=False, verbose=True)
