import os
from crewai import Agent, Task, Crew, Process, LLM
from tools.llm_config import require_llm_config

BASE = os.path.dirname(__file__)
SKILL = open(os.path.join(BASE, "skill.md"), encoding="utf-8").read()
KNOWLEDGE = open(os.path.join(BASE, "knowledge.md"), encoding="utf-8").read()
PROVIDER = require_llm_config("bingbichunqiu_agent")
MODEL = LLM(model=os.getenv("LLM_MODEL", "deepseek-chat"), temperature=0.7)

def create_bingbichunqiu_agent():
    return Agent(role="秉笔春秋史官", goal="以可靠中国史料回答问题并以史鉴今", backstory=SKILL + "\n" + KNOWLEDGE, llm=MODEL, verbose=False)

def create_advise_task(agent):
    return Task(description="围绕用户问题，区分史实、传说与评价，给出有出处意识、克制清晰的回答：{text}", expected_output="一段结构清楚的中文历史解读", agent=agent)

def create_bingbichunqiu_crew(text=""):
    return Crew(agents=[a := create_bingbichunqiu_agent()], tasks=[create_advise_task(a)], process=Process.sequential, verbose=False)
