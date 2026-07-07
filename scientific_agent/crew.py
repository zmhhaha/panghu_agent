"""
科学综述助手 — Agent / Task / Crew 定义

5 Agent 顺序流水线，模拟系统文献综述（Systematic Literature Review）标准流程：

    文献检索员 → 文献筛选员 → 数据提取员 → 综合分析员 → 综述撰写员
    (Searcher)   (Screener)   (Extractor)  (Synthesizer)  (Writer)
"""
import os
from crewai import Agent, Task, Crew, Process, LLM

# 学术搜索工具（全部免费，无需第三方 API Key）
from tools.academic_tools import (
    ArxivSearchTool,
    ArxivFetchTool,
    PubMedSearchTool,
    PubMedFetchTool,
    SemanticScholarSearchTool,
    SemanticScholarFetchTool,
    CrossrefLookupTool,
    AcademicMultiFetchTool,
)


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
    SYNTHESIS_LLM = LLM(
        model="openai/gpt-4o-mini",
        base_url="https://api.openai.com",
        api_key=os.getenv("OPENAI_API_KEY"),
        temperature=0.3,  # 分析+综合用低 temperature 保证准确性
    )
elif PROVIDER == "deepseek":
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
    SYNTHESIS_LLM = LLM(
        model="deepseek/deepseek-chat",
        base_url="https://api.deepseek.com",
        api_key=os.getenv("OPENAI_API_KEY"),
        temperature=0.3,
    )
elif PROVIDER == "custom":
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
    SYNTHESIS_LLM = LLM(
        model=custom_model,
        base_url=custom_base,
        api_key=custom_key,
        temperature=0.3,
    )
elif PROVIDER == "anthropic":
    PRIMARY_LLM = LLM(model="anthropic/claude-sonnet-4-6-20250514", temperature=0.7)
    SECONDARY_LLM = LLM(model="anthropic/claude-haiku-4-5-20251001", temperature=0.5)
    SYNTHESIS_LLM = LLM(model="anthropic/claude-sonnet-4-6-20250514", temperature=0.3)
else:
    PRIMARY_LLM = LLM(model="anthropic/claude-sonnet-4-6-20250514", temperature=0.7)
    SECONDARY_LLM = LLM(model="anthropic/claude-haiku-4-5-20251001", temperature=0.5)
    SYNTHESIS_LLM = LLM(model="anthropic/claude-sonnet-4-6-20250514", temperature=0.3)


# ============================================================
#  Agent 定义
# ============================================================

def create_literature_searcher() -> Agent:
    """文献检索员：在学术数据库中系统检索文献"""
    return Agent(
        role="学术文献检索专家",
        goal="在 arXiv、PubMed、Semantic Scholar 等学术数据库中系统性检索关于 {topic} 的文献，记录完整的检索策略和结果",
        backstory="""你是一位经验丰富的学术文献检索专家，拥有信息科学硕士背景，在多家研究型大学图书馆和数据库公司工作过 10 年以上。你的专长包括：

- 精通 arXiv API、PubMed MeSH 术语和 Entrez 查询语法、Semantic Scholar 的高级搜索
- 能根据研究主题构建最优的中英文检索式（包括布尔运算符、字段限定、同义词扩展）
- 了解各数据库的覆盖范围和特点（arXiv 偏 CS/物理/数学，PubMed 偏生物医学，Semantic Scholar 全学科）
- 严格记录每次检索的数据库、检索式、检索时间和返回结果数
- 优先检索近 5 年的文献，但也会纳入领域内的经典早期论文
- 在构建检索式时，会同时使用中文和英文关键词以确保覆盖全面""",
        llm=PRIMARY_LLM,
        tools=[
            ArxivSearchTool(),
            PubMedSearchTool(),
            SemanticScholarSearchTool(),
        ],
        verbose=True,
        allow_delegation=False,
    )


def create_literature_screener() -> Agent:
    """文献筛选员：按纳入/排除标准筛选文献，评估质量"""
    return Agent(
        role="文献质量评估与筛选专家",
        goal="按照严格的纳入/排除标准筛选文献，评估每篇论文的方法学质量和相关性，筛选出 10-20 篇高质量核心论文",
        backstory="""你是一位受过系统综述方法学（PRISMA、Cochrane Handbook）严格训练的文献筛选专家。你的专长包括：

- 制定清晰的纳入标准（同行评审/预印本、主题相关性、发表时间、方法学描述完整性）和排除标准（重复发表、非同行评审灰色文献、摘要不完整）
- 使用 1-5 分制从研究设计、样本量、统计方法、效度等维度评估论文的方法学质量
- 考虑引文数量作为影响力的参考指标（但不作为绝对标准）
- 按研究子主题将论文分组，为后续的专题分析奠定基础
- 对每篇被排除的论文记录排除原因，确保筛选过程透明可复现
- 熟悉各类研究设计层次（RCT > 队列研究 > 病例对照 > 病例系列/报告）""",
        llm=PRIMARY_LLM,
        tools=[
            ArxivFetchTool(),
            PubMedFetchTool(),
            SemanticScholarFetchTool(),
            CrossrefLookupTool(),
        ],
        verbose=True,
        allow_delegation=False,
    )


def create_data_extractor() -> Agent:
    """数据提取员：从筛选后的论文中系统提取关键数据"""
    return Agent(
        role="研究数据提取与整理专家",
        goal="从筛选后的论文中系统地提取关键信息：研究问题/目的、方法设计、样本/数据集、核心发现、效应量、局限性和未来工作",
        backstory="""你是一位精通系统综述数据提取方法的专家，曾参与多项 Cochrane 系统综述和 meta 分析项目。你的专长包括：

- 设计结构化的数据提取表格，确保每篇论文的信息被一致地提取和编码
- 提取研究的基本信息（作者、年份、机构、国别、基金来源）
- 提取研究设计要素（实验/观察/模拟、控制组、样本量、数据集）
- 提取定量结果（准确率、F1、效应量、置信区间、p 值等，如有）
- 提取定性结论（主要发现、作者声称的贡献、局限性）
- 标注论文之间的关联：哪些论文使用了相同的基准数据集？哪些论文的方法类似？哪些论文的结论相互矛盾？
- 对无法确定的信息明确标注"未报告"或"无法确定"
- 对引用的数据标注来源页码或章节""",
        llm=PRIMARY_LLM,
        tools=[
            ArxivFetchTool(),
            PubMedFetchTool(),
            SemanticScholarFetchTool(),
            AcademicMultiFetchTool(),
        ],
        verbose=True,
        allow_delegation=False,
    )


def create_synthesizer() -> Agent:
    """综合分析员：跨论文综合分析，识别主题、趋势和空白"""
    return Agent(
        role="研究综合与批判性分析专家",
        goal="跨论文综合分析研究发现，按主题组织讨论，识别研究一致性与分歧，评估证据强度，指出研究空白和未来方向",
        backstory="""你是一位资深的研究综合分析师，有 15 年以上的学术出版和审稿经验。你擅长：

- 将数据提取表中的离散信息合成为连贯的领域图景
- 按主题/子主题组织分析，而不是逐篇罗列——这是综述的价值所在
- 识别研究发现之间的一致性（convergent evidence）——多个独立团队使用不同方法得出相似结论时，证据强度更高
- 识别研究发现之间的分歧（divergent evidence），并分析可能的解释（方法差异、样本差异、时间因素等）
- 对每个主题的"证据强度"进行评级：强（多篇高质量研究一致支持）、中等（有支持但有争议）、弱（证据有限或质量低）、不足（缺乏研究）
- 识别研究空白（research gaps）：哪些重要问题尚未被充分研究？
- 讨论方法论趋势：领域内常用什么方法？方法上有哪些演进？
- 评估发表偏倚风险：是否可能存在负面结果未被发表的问题？
- 你的分析必须以数据提取表中的实证信息为基础，不凭空推断""",
        llm=SYNTHESIS_LLM,
        verbose=True,
        allow_delegation=False,
    )


def create_review_writer() -> Agent:
    """综述撰写员：按学术综述格式撰写最终报告"""
    return Agent(
        role="学术综述撰写专家",
        goal="将综合分析的结果撰写成结构严谨、格式规范、具有学术发表标准的系统综述论文",
        backstory="""你是一位发表了 50+ 篇高水平综述论文的学术写作专家，曾担任多个 SCI 期刊的编委。你的写作特点：

- 严格遵循学术综述的结构化格式：摘要 → 引言 → 方法 → 结果 → 讨论 → 结论与展望 → 参考文献
- 摘要采用结构化形式（背景、目的、方法、主要发现、结论），适合被学术数据库直接索引
- 方法部分提供足够的细节使检索过程可复现（检索式、检索日期、数据库、筛选流程图）
- 结果部分以主题为组织框架（而非逐篇罗列），使用表格呈现关键研究特征
- 讨论部分包含一致性分析、分歧解释、方法论批判、研究空白和本综述局限性
- 每个主张/陈述/数据点后附引用编号 [n]，对应参考文献列表
- 使用正式、客观、准确的学术中文学术语言——避免口语化表达和主观判断
- 参考文献使用标准格式：作者. "标题". *期刊/会议*, 年份, 卷(期): 页码. DOI
- 尊重原始研究的完整性——不歪曲、不过度解读、正确标注不确定的结论""",
        llm=SECONDARY_LLM,  # 撰写用轻量模型降低成本
        verbose=True,
        allow_delegation=False,
    )


# ============================================================
#  Task 定义
# ============================================================

def create_search_task(searcher: Agent) -> Task:
    """文献检索任务"""
    return Task(
        description="""对研究主题「{topic}」进行系统性文献检索。

## 检索步骤（必须严格按顺序执行）

### 步骤 1：分析主题，构建检索式
- 提取主题的核心概念和关键词（中英文）
- 识别同义词、近义词、缩写和上位/下位词
- 构建布尔检索式（AND / OR / NOT）
- 为不同的数据库（arXiv / PubMed / Semantic Scholar）调整检索式语法

### 步骤 2：执行检索（每个数据库至少检索一次）
**优先级：**
- 如果主题涉及计算机科学/AI/物理/数学 → 优先 arXiv
- 如果主题涉及生物/医学/药学/公卫 → 优先 PubMed
- 无论什么主题 → Semantic Scholar 作为补充（全学科覆盖）

### 步骤 3：记录检索结果
将检索结果整理为结构化文档：

| 数据库 | 检索式 | 命中数 | 检索日期 |
|--------|--------|--------|----------|
| arXiv | ... | N | 2026-07-XX |
| PubMed | ... | M | 2026-07-XX |
| Semantic Scholar | ... | K | 2026-07-XX |

## 检索要求（非常重要）：

- **必须实际调用搜索工具**——不要仅凭训练数据回答
- 每个数据库至少用 1 个核心检索式检索 1 次
- 如果某个检索式返回 0 结果或过多结果（>200），调整检索式后重新检索
- 记录完整的检索式（包括所有布尔运算符、字段限定符）
- 对每个数据库返回的前 10-15 篇结果，保留完整的元数据（标题、作者、年份、来源、摘要片段、DOI/ID）
- 检索限定在近 5 年（2021-2026），但如有里程碑式的早期论文也应记录""",
        expected_output="""一份结构化的文献检索报告，包含：

1. **检索策略概述**：针对该主题的检索思路和关键词选择理由
2. **各数据库检索记录表**（含检索式、命中数、检索日期）
3. **检索结果详情**：每个数据库返回的论文列表（编号、完整元数据）
   - 标题（保留原始语言，中英对照如适用）
   - 第一作者 + 所有作者
   - 发表年份
   - 来源数据库 + 论文唯一 ID（arXiv ID / PMID / DOI）
   - 摘要（前 200-300 字）
4. **去重说明**：标注跨数据库重复的论文""",
        agent=searcher,
    )


def create_screening_task(screener: Agent, search_task: Task) -> Task:
    """文献筛选任务"""
    return Task(
        description="""基于文献检索结果，按照系统综述方法对检索到的论文进行筛选和质量评估。

## 筛选标准

### 纳入标准（同时满足以下条件）：
1. 与「{topic}」**直接相关**（研究问题/对象/方法与该主题有明确关联）
2. **同行评审论文**或高质量预印本（arXiv 上有引用记录的论文可纳入）
3. 发表于 **2021-2026 年**（近 5 年），或更早但有里程碑意义的论文（需标注"
4. 有完整的**标题和摘要**（信息不完整的排除）

### 排除标准（满足任一即排除）：
1. 仅标题相关但摘要显示实际内容不相关
2. 非原创研究（社论、新闻、书评、专利，除非用户明确需要）
3. 明显低质量（无方法描述、无数据支持、仅有观点）
4. 重复发表或明显抄袭

## 筛选流程

### 第一阶段：标题+摘要筛选
- 阅读每篇论文的标题和摘要（如有）
- 对不完整的摘要，使用论文详情工具获取完整摘要
- 标记为：纳入 / 排除（附原因） / 待定（需要更多信息）

### 第二阶段：方法学质量评估
对纳入的论文进行质量评分（1-5 分）：
- **5 分**：顶级期刊/会议、严格实验设计、大样本、完整统计报告、可复现
- **4 分**：好的期刊/会议、合理设计、适当样本、统计报告较完整
- **3 分**：一般期刊/会议、基本设计、样本偏小、统计简单
- **2 分**：预印本（引用 < 5）、设计有缺陷、样本量不足
- **1 分**：严重方法学缺陷

### 第三阶段：主题分组
- 将纳入的论文按子主题/研究问题分组（2-4 组）
- 每组 2-5 篇论文
- 为每组标注核心特征""",
        expected_output="""一份结构化的筛选报告，包含：

## 1. 筛选统计
- 检索到论文总数
- 去重后论文数
- 标题/摘要筛选后排除数（附排除原因分类统计）
- 最终纳入论文数

## 2. PRISMA 筛选流程图（文字版）
```
检索到的论文 (n=XX)
    ↓ 去重
去重后 (n=XX)
    ↓ 标题/摘要筛选
排除 (n=XX): 不相关(n=X), 非原创研究(n=X), 质量低(n=X), 其他(n=X)
    ↓ 全文/详情评估
排除 (n=XX): 不完整(n=X), 方法学问题(n=X)
    ↓
最终纳入 (n=XX)
```

## 3. 纳入论文列表
每篇论文标注：
- 编号（唯一，用于后续引用）
- 基本信息（标题、作者、年份、来源数据库）
- 质量评分（1-5）及简评
- 子主题分组标签
- 核心发现摘要（1-2 句）

## 4. 排除论文列表
- 论文信息 + 排除原因""",
        agent=screener,
        context=[search_task],
    )


def create_extraction_task(extractor: Agent, screening_task: Task) -> Task:
    """数据提取任务"""
    return Task(
        description="""基于筛选后的纳入论文列表，对每篇论文进行系统的数据提取。对每篇论文，必须尝试通过其 ID（arXiv ID / PMID / DOI）**使用论文详情工具获取完整信息**。

## 数据提取表结构

### A. 基本信息
| 字段 | 说明 |
|------|------|
| 论文编号 | 与筛选报告中的编号一致 |
| 引用信息 | 第一作者, 年份, "标题", 期刊/会议, DOI |
| 机构/国别 | 第一作者所属机构和/或国家 |
| 基金来源 | 如有明确标注 |

### B. 研究设计
| 字段 | 说明 |
|------|------|
| 研究类型 | 实验/观察/模拟/综述/理论/案例研究 |
| 研究问题/目标 | 论文明确陈述的研究问题或目标 |
| 方法论概述 | 使用的方法/模型/框架（2-5 句） |
| 样本/数据集 | 样本量/数据集名称和规模 |
| 对照/基线 | 对照组或比较基线（如有） |

### C. 结果与发现
| 字段 | 说明 |
|------|------|
| 核心发现 | 论文最重要的 1-3 个发现 |
| 定量结果 | 关键数值（准确率、F1、p 值、效应量等，如有） |
| 定性结论 | 作者得出的主要结论 |

### D. 批判性评估
| 字段 | 说明 |
|------|------|
| 局限性 | 作者自述的局限性 |
| 你的评估 | 你观察到的其他局限或偏见 |
| 可推广性 | 结论的适用范围和条件 |

## 提取要求（非常重要）：
- **必须使用论文详情工具获取每篇论文的完整信息**——不要仅根据标题和摘要猜测
- 对每篇论文提取至少 A+B+C 三类信息
- 如果详细信息无法获取（如只有标题），在表格中标注"仅标题可用，以下为摘要推断：[内容]"
- 记录论文间的关联：
  - 哪些论文使用方法 A？
  - 哪些论文使用数据集 B？
  - 哪些论文的结论一致？哪些矛盾？""",
        expected_output="""一份详细的数据提取报告，包含：

1. **数据提取表**（核心产出）
   - 每篇纳入论文的 A/B/C/D 四类信息
   - 如论文 ID 无法获取详情，标注"## 无法获取详情"及原因

2. **论文间关联分析**
   - 方法使用矩阵（哪些论文字用了相同方法）
   - 数据集使用矩阵（哪些论文用了相同数据集）
   - 结论一致性矩阵（一致 / 部分一致 / 冲突 / 无法比较）

3. **提取完整性报告**
   - 成功获取详情的论文数 / 总论文数
   - 有哪些信息项多数论文未报告（如基金来源、样本量等）""",
        agent=extractor,
        context=[screening_task],
    )


def create_synthesis_task(synthesizer: Agent, extraction_task: Task) -> Task:
    """综合分析任务"""
    return Task(
        description="""基于数据提取表，对「{topic}」研究领域进行跨论文的综合分析。这是整篇综述的核心——不是逐篇罗列，而是按主题和问题组织讨论。

## 综合分析框架

### 1. 研究领域全景
- 该领域目前的研究活动有多活跃？（从论文数量、时间分布判断）
- 主要来自哪些机构/国家？
- 主要的基金来源？（如有）

### 2. 主题发现与组织
- 从纳入的论文中识别出哪几个核心研究子主题？
- 每个子主题下有哪些研究？（引用论文编号）
- 各子主题之间的关系是什么？（独立/重叠/递进/竞争）

### 3. 研究发现一致性分析
- **高度一致的发现**：多个独立研究团队使用不同方法得出相似结论——请列出并标注证据强度。
- **部分一致的发现**：多数研究支持但有一个或多个不一致——请列出并分析不一致可能的原因。
- **存在分歧的发现**：不同研究得出了矛盾的结论——请列出并解释可能的原因（方法差异？样本差异？时间因素？）。

### 4. 方法论趋势与评估
- 该领域最常用的研究方法是什么？
- 方法论上有哪些值得注意的演进？
- 现有研究在方法学上的共同弱点是什么？

### 5. 研究空白 (Research Gaps)
- 哪些重要问题尚未被充分研究？
- 哪些人群/场景/条件被现有研究忽略了？
- 哪些类型的研究缺失了？（如缺乏大规模 RCT、缺乏纵向研究等）

### 6. 证据强度评级
为中心结论列表对每条结论的总体证据强度进行评级：
- **强 (Strong)**：≥3 篇高质量研究一致支持
- **中等 (Moderate)**：≥2 篇研究支持但质量参差不齐
- **弱 (Weak)**：仅 1-2 篇研究支持或方法学质量低
- **不足 (Insufficient)**：证据不足以得出结论

## 分析要求（非常重要）：
- **以主题为中心组织，而不是逐篇罗列论文**
- **每个判断必须引用论文编号作为证据**——"[3][7][12] 均报告了..."
- **对不确定性诚实**——不要因为结论不明确就编造一个"
- **区分"原始研究怎么说"和"你如何解释"**——先陈述事实，再给出你的分析
- **关注效应量和实际意义**，不仅关注统计显著性""",
        expected_output="""一份结构化的综合分析报告，包含：

## 1. 领域全景
2-3 段概述该研究领域的活动情况、地理分布和研究热点。

## 2. 子主题分析
每个子主题一节：
- 子主题名称和定义
- 该子主题下的研究论文（[编号] 引用）
- 关键发现概述
- 子主题内的研究一致性/分歧

## 3. 跨主题发现
- 方法论趋势
- 研究空白
- 主要争议点

## 4. 证据强度总结表
| 结论 | 支持论文 | 反对/不一致论文 | 证据强度 |
|------|---------|----------------|---------|
| ... | [1][3][5] | [7] | 中等 |

每条结论都需标注证据强度（强 / 中等 / 弱 / 不足）。""",
        agent=synthesizer,
        context=[extraction_task],
    )


def create_writing_task(writer: Agent, synthesis_task: Task) -> Task:
    """综述撰写任务"""
    return Task(
        description="""基于综合分析报告，将「{topic}」的研究成果撰写成一篇完整的、符合学术发表标准的系统综述。

## 综述结构（必须严格遵循）

### 摘要 (Abstract)
结构化摘要，包含以下要素（用标签标注）：
- **背景**：1-2 句，该研究领域的整体背景
- **目的**：本综述的研究目的/问题
- **方法**：检索了哪些数据库、使用的检索策略、筛选标准和最终纳入的论文数
- **主要发现**：3-5 条核心发现（最重要的结果）
- **结论**：1 句总结句
- **关键词**：3-6 个关键词（中英文）

### 1. 引言 (Introduction)
- 1.1 研究背景：介绍该研究领域的整体背景和重要性
- 1.2 研究意义：为什么需要这个综述？该领域目前的进展和问题是什么？
- 1.3 综述范围与目的：明确本综述覆盖的范围、具体研究问题和不覆盖的内容
- 1.4 与已有综述的区别（如适用）

### 2. 方法 (Methods)
- 2.1 检索策略（数据库列表、检索式、检索日期、时间范围）
- 2.2 纳入与排除标准
- 2.3 文献筛选流程（PRISMA 流程图文字描述）
- 2.4 数据提取方法
- 2.5 分析方法

### 3. 结果 (Results)
- 3.1 文献检索结果概览（共检索多少、最终纳入多少，纳入论文的基本特征表）
- 3.2 {子主题一} — 按综合报告中识别的子主题组织
- 3.3 {子主题二}
- 3.4 {子主题三}
- 3.5 方法论分析（研究方法、数据集使用情况等）

### 4. 讨论 (Discussion)
- 4.1 主要发现总结
- 4.2 研究一致性分析（哪些发现是可靠共识？）
- 4.3 研究分歧与争议（为什么会有分歧？）
- 4.4 研究空白 (Research Gaps)
- 4.5 本综述的局限性（诚实陈述本综述方法的局限）

### 5. 结论与展望 (Conclusions & Future Directions)
- 5.1 结论（3-5 条，简洁有力）
- 5.2 未来研究方向（基于研究空白提出具体建议）

### 6. 参考文献 (References)
- 使用 [1], [2], ... 编号
- 格式：[n] 作者1, 作者2. "论文标题". *期刊/会议名称*, 年份. DOI: 10.xxxx/xxxxx
- 如果无 DOI 则用 arXiv ID 或 PMID 替代

## 写作要求（非常重要）：

1. **每条事实性陈述必须标注引用 [n]**——对应参考文献列表中的编号
2. **使用正式、客观的学术中文**——不使用"我觉得"、"很厉害"、"非常棒"等口语化表达
3. **区分原始研究的陈述和综述作者的判断**——"张三等[3] 发现..." vs "综合来看，..."
4. **善用表格**——纳入论文特征表、方法对比表、关键发现汇总表
5. **不夸大、不歪曲**——尊重原始研究的实际发现和局限性
6. **方法部分提供足够细节使检索可复现**
7. **全文用 Markdown 格式**——善用标题层级、表格、引用""",
        expected_output="""一篇完整的学术综述（Markdown 格式），包含上述所有章节。

**最低要求检查清单**：
- 有结构化摘要（含方法细节和论文数）
- 引言说明了研究背景、意义和范围
- 方法部分包含具体的检索式、筛选流程（附 PRISMA 数字）
- 结果部分按主题组织且每个主张有引用
- 讨论部分包含证据强度评估和研究空白
- 参考文献列表格式完整且编号与正文对应
- 全文使用学术中文，无口语化表达""",
        agent=writer,
        context=[synthesis_task],
    )


# ============================================================
#  组建 Crew
# ============================================================

def create_scientific_crew(topic: str = "") -> Crew:
    """创建科学综述研究团队——5 Agent 顺序流水线"""
    searcher = create_literature_searcher()
    screener = create_literature_screener()
    extractor = create_data_extractor()
    synthesizer = create_synthesizer()
    writer = create_review_writer()

    search_task = create_search_task(searcher)
    screening_task = create_screening_task(screener, search_task)
    extraction_task = create_extraction_task(extractor, screening_task)
    synthesis_task = create_synthesis_task(synthesizer, extraction_task)
    writing_task = create_writing_task(writer, synthesis_task)

    crew = Crew(
        agents=[searcher, screener, extractor, synthesizer, writer],
        tasks=[search_task, screening_task, extraction_task, synthesis_task, writing_task],
        process=Process.sequential,  # 严格顺序：检索 → 筛选 → 提取 → 综合 → 撰写
        memory=False,
        verbose=True,
    )

    return crew
