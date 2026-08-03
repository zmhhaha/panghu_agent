"""
通用游戏试玩评价 agent — CrewAI 流水线。

三段式顺序执行：
  试玩员（TrialPlayer）用浏览器工具真实游玩 → 产出结构化试玩日志
  → 评测员（Evaluator）读日志提炼优缺点
    → 撰写者（Writer）输出 Markdown 评价报告

注意：浏览器是有状态的，一次任务一个浏览器。play_task 的 expected_output
是结构化试玩日志 JSON，作为给后续 agent 的上下文桥。
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

    # ── Agent ──────────────────────────────────────────────
    trial_player = Agent(
        role="游戏试玩员",
        goal=f"打开 {game_url}，像真人玩家一样真实游玩，体验核心玩法，并输出结构化试玩日志",
        backstory=(SKILL_TEXT or "你是一位资深游戏试玩员，擅长快速上手陌生网页游戏，并忠实记录游玩体验。"),
        llm=PRIMARY_LLM,
        tools=tools,
        verbose=True,
        allow_delegation=False,
        memory=True,
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
    play_task = Task(
        description=f"""用浏览器工具真实游玩游戏 {game_url}。

评测关注点：{comment_targets or '（无特别指定，全面体验）'}

**浏览器工具使用规则（重要）：**
1. 先调用 page_go 打开游戏地址（若页面已打开可跳过）。
2. 每次决策前必须先调用 page_scan 扫描当前页面，得到最新可交互元素列表。
3. 用 page_click 点按钮（用 idx 索引），page_type 在输入框填文本，page_select 选下拉，page_press 按 Enter。
4. 操作后页面会变化，必须重新 page_scan 再继续。
5. 不要编造页面状态——看不到的就不写。
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
        agents=[trial_player, evaluator, writer],
        tasks=[play_task, evaluate_task, report_task],
        process=Process.sequential,
        memory=False,
        verbose=True,
    )
