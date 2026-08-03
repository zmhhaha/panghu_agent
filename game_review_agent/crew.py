"""
通用游戏试玩评价 agent — CrewAI 流水线。

四段式顺序执行：
  技能分析师（SkillDesigner）探索陌生游戏 → 生成本次任务专属 skill.md
  → 试玩员（TrialPlayer）依据 skill.md 用浏览器工具真实游玩 → 产出结构化试玩日志
  → 评测员（Evaluator）读日志提炼优缺点
    → 撰写者（Writer）输出 Markdown 评价报告

注意：浏览器是有状态的，一次任务一个浏览器。技能分析与试玩共享同一个浏览器，
skill_task 的 Markdown 输出作为 play_task 的操作手册，试玩日志再作为后续评测上下文。
"""
import os

from crewai import Agent, Task, Crew, Process

from game_review_agent.llm_config import PRIMARY_LLM, SECONDARY_LLM

# 试玩员行为提示词（通用，不绑定任何游戏）
_SKILL_MD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skill.md")
SKILL_TEXT = ""
try:
    with open(_SKILL_MD, "r", encoding="utf-8") as f:
        SKILL_TEXT = f.read()
except Exception:
    pass


def create_game_review_crew(
    game_url: str,
    comment_targets: str = "",
    browser_tools: list | None = None,
    out_dir: str = "trial_output",
) -> Crew:
    """创建试玩评测流水线。

    Args:
        game_url: 要玩的游戏 URL
        comment_targets: 评测关注点（可选）
        browser_tools: 试玩员可用的浏览器工具列表（由调用方用真实 page 创建）
        out_dir: 试玩日志/截图输出目录
    """
    tools = browser_tools or []
    os.makedirs(out_dir, exist_ok=True)
    generated_skill_path = os.path.abspath(os.path.join(out_dir, "skill.md"))

    # ── Agent ──────────────────────────────────────────────
    skill_designer = Agent(
        role="网页游戏技能分析师",
        goal=f"探索 {game_url} 的真实入口、控件、状态和核心循环，生成可执行且不依赖临时索引的游戏专属 skill.md",
        backstory=(
            "你擅长快速理解完全陌生的网页游戏，并把观察转成另一位 Agent 可以执行的操作手册。"
            "你只使用通用浏览器能力，不为特定游戏编写代码，不把一次扫描得到的 idx 或 CSS 选择器写进技能。\n\n"
            + (SKILL_TEXT or "所有判断必须来自真实页面，探索后输出可验证的游玩技能。")
        ),
        llm=PRIMARY_LLM,
        tools=tools,
        verbose=True,
        allow_delegation=False,
        memory=False,
        max_iter=int(os.getenv("GAME_SKILL_MAX_STEPS", "15")),
    )

    trial_player = Agent(
        role="游戏试玩员",
        goal=f"依据本次生成的 skill.md 真实游玩 {game_url}，体验核心玩法，并输出结构化试玩日志",
        backstory=(
            (SKILL_TEXT or "你是一位资深游戏试玩员，擅长快速上手陌生网页游戏，并忠实记录游玩体验。")
            + "\n\n你会把运行时生成的游戏专属 skill.md 当作操作手册，但页面最新状态永远优先于手册。"
        ),
        llm=PRIMARY_LLM,
        tools=tools,
        verbose=True,
        allow_delegation=False,
        memory=False,
        max_iter=int(os.getenv("GAME_MAX_STEPS", "40")),
    )

    evaluator = Agent(
        role="游戏评测员",
        goal="基于试玩日志，从玩法、界面、叙事、难度、技术表现等多个维度提炼游戏的优缺点",
        backstory=(
            "你是一位专业游戏评测员，能透过试玩过程看本质。你的评价必须有依据——"
            "每条优点/缺点都要引用试玩日志中的具体操作或现象。"
        ),
        llm=PRIMARY_LLM,
        tools=[],  # 只读试玩日志，无浏览器
        verbose=True,
        allow_delegation=False,
    )

    writer = Agent(
        role="评测报告撰写者",
        goal="把评测要点写成结构清晰、客观公正的 Markdown 评测报告",
        backstory=(
            "你是一位资深游戏媒体撰稿人。报告风格：客观、有细节、有观点，"
            "既说优点也讲缺点，最后给出明确结论和评分。"
        ),
        llm=SECONDARY_LLM,
        tools=[],
        verbose=True,
        allow_delegation=False,
    )

    # ── Task ───────────────────────────────────────────────
    skill_task = Task(
        description=f"""为陌生网页游戏 {game_url} 生成本次试玩专属的 skill.md。

评测关注点：{comment_targets or '（无特别指定，全面体验）'}

你必须先真实探索，而不是根据 URL 或常识猜测：
1. 用 page_go 打开目标，再用 page_scan 阅读页面与可交互元素，并保存一张入口截图。
2. 找出无需登录即可使用的开始、新建、公开案件、试玩、继续、教程等入口。游客/Guest 不等于受限。
3. 可以安全点击菜单、教程、模式、公开关卡或预览，以确认状态变化；不得购买、登录、删除数据、公开发布内容或执行其他不可逆操作。
4. 尽量探索到第一个真正需要玩家决策的核心玩法状态。若进入核心状态，就把当前状态和继续操作写清楚，试玩员会复用同一浏览器继续。
5. 识别游戏所需的通用原子操作：page_click、page_type、page_select、page_press、page_scroll、page_click_xy、page_drag、page_wait、page_back。只记录实际需要的操作。
6. 每次点击都从最新 page_scan 取 idx，并把目标文本原样放入 hint。操作后重新扫描。
7. skill.md 不得记录 idx、CSS/XPath 或依赖本次 DOM 顺序的定位信息；只写可见文本、角色、页面状态、坐标区域与预期反馈。
8. 未验证的规则放进“未知与验证方式”，不能当成事实。

skill.md 必须使用以下结构：
# <游戏名称> Play Skill
## 游戏识别
## 当前浏览器状态
## 安全入局流程
## 控件与原子操作映射
## 核心玩法循环
## 输入策略
## 状态识别（加载中/可操作/进展/胜利/失败/卡住）
## 等待与恢复策略
## 截图证据节点
## 禁止操作
## 未知与验证方式

输出纯 Markdown，不要使用代码围栏。""",
        expected_output=(
            "一份由真实页面探索产生、可供试玩员直接执行的游戏专属 Markdown skill；"
            "不含临时 idx 或硬编码选择器，包含入口、控件、核心循环、状态判断、恢复和未知项。"
        ),
        agent=skill_designer,
        output_file=generated_skill_path,
        create_directory=True,
    )

    play_task = Task(
        description=f"""依据上一个任务生成的游戏专属 skill.md，用浏览器工具真实游玩游戏 {game_url}。

评测关注点：{comment_targets or '（无特别指定，全面体验）'}

**浏览器工具使用规则（重要）：**
1. 先读取上下文中的 skill.md。若技能分析师已到达可玩的页面，就从当前状态继续；否则按“安全入局流程”操作。
2. 每次决策前必须先调用 page_scan 扫描当前页面，得到最新可交互元素列表。
3. 按 skill.md 选择通用工具：DOM 控件用 page_click/page_type/page_select，键盘用 page_press，长页面用 page_scroll，canvas/棋盘才使用 page_click_xy/page_drag。
4. 操作后页面会变化，必须重新 page_scan 再继续。
5. skill.md 是探索时的操作手册，不是永久真相。若页面与技能不一致，以最新 page_scan 为准，记录差异并采用技能中的恢复策略。
6. 像真人玩家一样：进入游戏→体验玩法→推进进度→尽量玩到结算/通关。
7. 玩的每一步都用 page_screenshot 截图存档作为证据。
8. 当你判断已通关、已充分体验、或连续多步无进展时，结束游玩。
9. “游客/Guest”身份或页面上的登录入口本身不等于权限受限。优先尝试公开案件、头条案件、试玩入口或新建入口；只有明确跳转登录页或出现拒绝提示才可判定登录受阻。
10. page_click 的 hint 必须复制 page_scan 中的目标文本。若工具报告索引与目标不符，立即重新扫描；这属于自动化定位问题，不得写成游戏交互或路由缺陷。

游玩结束后，输出结构化试玩日志（JSON）：包含每个步骤（动作、理由、页面变化、进度说明）、截图证据列表、以及最终总结（这是什么游戏、玩到什么程度、体验如何）。""",
        expected_output="""一份结构化试玩日志 JSON，包含：
- game_url, game_name
- steps: [{n, action, target, reasoning, progress_note, screenshot}]
- evidence: [{type, path, title}]
- outcome: {status(completed/partial/stuck/timeout), verdict_text, rounds_played, what_is_this_game, game_state_summary}""",
        agent=trial_player,
        context=[skill_task],
    )

    evaluate_task = Task(
        description=f"""基于试玩日志，对游戏 {game_url} 进行专业评测。

评测关注点：{comment_targets or '（全面评测）'}

请从以下维度评价，每条判断都要引用试玩日志中的具体证据（某步做了什么、页面出现什么、进度如何）：
1. **玩法设计**：核心玩法是否有趣、上手难度、机制深度、可玩性
2. **界面与交互**：UI 是否清晰、操作是否顺畅、反馈是否及时
3. **叙事与内容**：剧情/世界观/角色是否有吸引力
4. **难度与节奏**：难度曲线是否合理、是否有挫败感/成就感
5. **技术表现**：加载速度、稳定性、有无明显 bug
6. **总体评价**：适合什么类型的玩家、亮点与不足""",
        expected_output="""结构化评测要点：
- 每个维度的评分（1-10）与理由（引用试玩日志证据）
- 优点清单（每条带证据）
- 缺点清单（每条带证据）
- 目标受众与总体印象

证据边界：浏览器/工具超时、索引错位、截图失败等自动化故障不得计入游戏评分；“游客”字样不得被推断为权限受限。未实际进入或体验的维度必须标注未验证，不得用菜单文案推断玩法质量。""",
        agent=evaluator,
        context=[play_task],
    )

    report_task = Task(
        description=f"""把评测要点写成一篇完整的 Markdown 评测报告，针对游戏 {game_url}。

报告结构：
1. **标题与引言**（一句话概括这是一款什么游戏）
2. **游戏概览**（类型、玩法、目标，依据试玩日志）
3. **试玩过程**（我们实际玩到了什么程度、玩了哪些内容）
4. **分维度评测**（玩法/界面/叙事/难度/技术，各带评分与依据）
5. **优点与不足**（分点列出，客观）
6. **适合人群**（推荐给谁）
7. **总评与评分**（综合评分和一句话结论）

要求：
- Markdown 格式，中文撰写
- 客观公正，优点缺点都说
- 评分用「X/10」表示
- 结论有观点，不模棱两可
- 只评价试玩日志中真实验证的游戏行为；自动化工具故障不能包装成游戏缺陷，游客身份不能凭空解释为权限受限""",
        expected_output="一份完整的 Markdown 格式游戏评测报告",
        agent=writer,
        context=[evaluate_task],
    )

    return Crew(
        agents=[skill_designer, trial_player, evaluator, writer],
        tasks=[skill_task, play_task, evaluate_task, report_task],
        process=Process.sequential,
        memory=False,
        verbose=True,
    )
