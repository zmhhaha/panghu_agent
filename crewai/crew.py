import os
from crewai import Agent, Task, Crew, Process, LLM

# 如果需要网页搜索/爬取工具，取消下面的注释：
# from crewai_tools import SerperDevTool, ScrapeWebsiteTool


# ============================================================
#  LLM 配置 — 通过环境变量 PROVIDER 切换模型提供商
#  PROVIDER=openai | anthropic | deepseek | custom（默认 openai）
# ============================================================

PROVIDER = os.getenv("PROVIDER", "openai").lower()

if PROVIDER == "openai":
    PRIMARY_LLM = LLM(
        model="openai/gpt-4o-mini",
        base_url="https://api.openai.com",
        api_key=os.getenv("OPENAI_API_KEY"),
        temperature=0.7,
    )
    SECONDARY_LLM = LLM(
        model="openai/gpt-4o-mini",
        base_url="https://api.openai.com",
        api_key=os.getenv("OPENAI_API_KEY"),
        temperature=0.5,
    )
elif PROVIDER == "deepseek":
    # DeepSeek API 兼容 OpenAI 格式，base_url 指向 DeepSeek 端点
    PRIMARY_LLM = LLM(
        model="deepseek/deepseek-chat",
        base_url="https://api.deepseek.com",
        api_key=os.getenv("OPENAI_API_KEY"),
        temperature=0.7,
    )
    SECONDARY_LLM = LLM(
        model="deepseek/deepseek-chat",
        base_url="https://api.deepseek.com",
        api_key=os.getenv("OPENAI_API_KEY"),
        temperature=0.5,
    )
elif PROVIDER == "custom":
    # 自定义 API：兼容任何 OpenAI 格式的端点
    custom_base = os.getenv("CUSTOM_API_BASE", "http://localhost:11434/v1")
    custom_key = os.getenv("CUSTOM_API_KEY", "")
    custom_model = os.getenv("CUSTOM_MODEL", "gpt-4o-mini")

    PRIMARY_LLM = LLM(
        model=custom_model,
        base_url=custom_base,
        api_key=custom_key,
        temperature=0.7,
    )
    SECONDARY_LLM = LLM(
        model=custom_model,
        base_url=custom_base,
        api_key=custom_key,
        temperature=0.5,
    )
else:
    # Anthropic Claude 模型（默认）
    PRIMARY_LLM = LLM(model="anthropic/claude-sonnet-4-6-20250514", temperature=0.7)
    SECONDARY_LLM = LLM(model="anthropic/claude-haiku-4-5-20251001", temperature=0.5)


# ============================================================
#  Agent 定义
# ============================================================

def create_researcher() -> Agent:
    """研究员：负责搜集和整理信息"""
    return Agent(
        role="高级研究分析师",
        goal="深入调研并搜集关于 {topic} 的全面、准确的信息和数据",
        backstory="""你是一位资深的技术研究分析师，拥有10年以上的行业调研经验。你擅长：
- 从海量信息中快速提取关键洞察
- 交叉验证多个数据源的可靠性
- 识别新兴趋势和技术突破
- 整理结构化的调研笔记""",
        llm=PRIMARY_LLM,
        # tools=[SerperDevTool(), ScrapeWebsiteTool()],  # 取消注释启用搜索
        verbose=True,
        allow_delegation=False,
    )


def create_analyst() -> Agent:
    """分析师：负责深度分析和洞察"""
    return Agent(
        role="数据与趋势分析师",
        goal="对调研数据进行深度分析，提炼出关键趋势、模式和洞察",
        backstory="""你是一位经验丰富的数据分析师，善于从复杂信息中发现规律和趋势。你的分析特点是：
- 数据驱动，结论必须有依据
- 多维度思考（技术、市场、用户、竞争）
- 识别因果关系而不仅仅是相关性
- 给出可操作的建议""",
        llm=PRIMARY_LLM,
        verbose=True,
        allow_delegation=False,
    )


def create_writer() -> Agent:
    """撰写者：负责输出报告"""
    return Agent(
        role="技术报告撰写专家",
        goal="将分析结果撰写成结构清晰、逻辑严谨、易于理解的研究报告",
        backstory="""你是一位专业的技术写作专家，曾为多家顶级科技媒体和研究机构撰写报告。你的写作风格：
- 结构清晰，善用标题和小标题
- 通俗易懂，避免不必要的术语
- 图文并茂，善用表格和列表
- 结论明确，建议可操作""",
        llm=SECONDARY_LLM,  # 撰写用轻量模型，节省成本
        verbose=True,
        allow_delegation=False,
    )


# ============================================================
#  Task 定义
# ============================================================

def create_research_task(researcher: Agent) -> Task:
    """调研任务"""
    return Task(
        description="""全面调研 {topic}，涵盖以下维度：

1. **核心概念与技术**：基础原理、关键技术栈、架构设计
2. **最新进展**：最近6个月的重大更新、新版本发布、重要论文
3. **主要参与者**：主要框架/产品、背后团队、社区活跃度
4. **实际应用**：典型使用场景、成功案例、最佳实践
5. **对比分析**：各方案的优劣势对比

要求：
- 每个要点都标注信息来源
- 区分事实和观点
- 标注信息的时效性""",
        expected_output="""一份结构化的调研数据文档，包含：
- 分类整理的调研发现
- 来源引用
- 关键数据点汇总""",
        agent=researcher,
    )


def create_analysis_task(analyst: Agent, research_task: Task) -> Task:
    """分析任务"""
    return Task(
        description="""基于调研结果，进行深度分析：

1. **趋势识别**：这个领域正在向什么方向发展？
2. **技术评估**：各方案的技术成熟度和适用场景
3. **SWOT分析**：优势、劣势、机会、威胁
4. **关键洞察**：最值得关注的3-5个核心发现
5. **预测与建议**：短期和中长期的发展预判""",
        expected_output="""一份分析报告，包含：
- 核心发现总结（3-5条）
- 趋势分析（含支撑数据）
- SWOT矩阵
- 明确的建议和下一步行动""",
        agent=analyst,
        context=[research_task],
    )


def create_report_task(writer: Agent, analysis_task: Task) -> Task:
    """报告撰写任务"""
    return Task(
        description="""将分析结果撰写成一份专业的研究报告。
结构要求：
1. **执行摘要**（200字以内概括核心发现）
2. **研究背景**（为什么调研这个主题）
3. **主要发现**（分章节详述）
4. **深度分析**（趋势、对比、洞察）
5. **结论与建议**（明确可操作的建议）
6. **附录**（数据来源、术语表）

格式要求：
- Markdown 格式
- 善用表格对比
- 关键结论加粗标注
- 中文撰写""",
        expected_output="一份完整的 Markdown 格式研究报告，保存到 report.md",
        agent=writer,
        context=[analysis_task],
        output_file="report.md",
    )


# ============================================================
#  组建 Crew
# ============================================================

def create_research_crew(topic: str = "") -> Crew:
    """创建研究分析团队"""
    researcher = create_researcher()
    analyst = create_analyst()
    writer = create_writer()

    research_task = create_research_task(researcher)
    analysis_task = create_analysis_task(analyst, research_task)
    report_task = create_report_task(writer, analysis_task)

    crew = Crew(
        agents=[researcher, analyst, writer],
        tasks=[research_task, analysis_task, report_task],
        process=Process.sequential,  # 顺序执行：研究 → 分析 → 撰写
        memory=False,                 # 启用记忆
        verbose=True,
    )

    return crew
