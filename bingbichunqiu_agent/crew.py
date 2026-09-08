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

请严格按照 skill.md 回答，但默认保持短小：写 2～4 段、约 200～400 字，只选一个最相关的历史切入点。先回应用户当下的感受或问题，再用一两句可靠史实作参照，最后给一个简短落点。只有用户明确要求详细考据、展开讲或列出史料时，才补充更多年代、出处和争议。明确区分史实、传说、后世评价与推测；不要编造引文或出处，不要输出工作过程，不要写成论文或分节长文，只输出给用户看的中文解读。""",
        expected_output="2～4 段、约 200～400 字的中文短答，像一位克制而有温度的史官，史实与评价分明，读完有一个清楚的落点。",
        agent=agent,
    )

def create_bingbichunqiu_crew(text=""):
    agent=create_bingbichunqiu_agent(); return Crew(agents=[agent], tasks=[create_advise_task(agent)], process=Process.sequential, memory=False, verbose=True)
