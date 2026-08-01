"""
通用游戏试玩运行器 — scan → decide → act → record 循环。

不依赖 CrewAI 编排，直接驱动浏览器 + LLM 做决策。这是本地/CI/故障排障的抓手，
也是 API 后台线程执行试玩的引擎。CrewAI 的 play_task 也可以复用这里的
单步决策函数（见 crew.py），保证两种入口行为一致。

职责：
- 打开目标 URL，处理登录重定向
- 循环 N 步：page_scan → LLM 决策 → 执行 → 记录
- 每步保存截图证据
- 产出结构化试玩日志 JSON（供评测员/撰写者消费）
"""
import json
import os
import sys
import re
import time
from typing import Any

# 把 panghu_agent 根目录加入 sys.path，确保 from tools... 能导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pydantic import BaseModel, Field

from tools.game_play.browser import GameBrowser, detect_login_redirect
from tools.game_play.tools import _scan

# 默认循环参数
MAX_STEPS = int(os.getenv("GAME_MAX_STEPS", "40"))
MIN_STEPS = int(os.getenv("GAME_MIN_STEPS", "5"))
TIME_BUDGET_S = int(os.getenv("GAME_TIME_BUDGET_S", "900"))
NO_PROGRESS_LIMIT = int(os.getenv("GAME_NO_PROGRESS_LIMIT", "5"))


# ============================================================
#  决策结构化输出（LLM 每步返回）
# ============================================================

class StepDecision(BaseModel):
    """试玩员每步的决策。"""
    reasoning: str = Field(..., description="你对当前页面的理解，为什么这么做")
    action: str = Field(
        ...,
        description="要执行的动作，枚举：click/type/select/press/wait/finish",
    )
    idx: int | None = Field(None, description="click/type/select 时目标元素索引（来自 page_scan）")
    text: str = Field("", description="type 时要输入的文本，或 select 时的选项")
    key: str = Field("", description="press 时按的键")
    wait_seconds: float = Field(2.0, description="wait 时等待秒数")
    progress_note: str = Field(
        ...,
        description="这步推进了什么？现在游戏进度如何？（若你判断已通关/卡住/体验充分，说明并设 finish）",
    )


class FinishDecision(BaseModel):
    """试玩员结束时的总结。"""
    status: str = Field(..., description="completed/partial/stuck/timeout")
    verdict_text: str = Field(..., description="对这次试玩的总结（玩了什么、结果如何、体验如何）")
    what_is_this_game: str = Field(..., description="你理解的这个游戏是什么、玩法、目标")
    game_state_summary: str = Field(..., description="结束时的游戏状态概要")


# ============================================================
#  单步决策（供 CrewAI play_task 与 runner 共用）
# ============================================================

def decide_step(llm, snapshot: str, history: list[dict], comment_targets: str = "") -> StepDecision:
    """让 LLM 基于最新页面快照做一步决策。"""
    history_text = _format_history(history)
    user_msg = (
        f"## 当前页面快照\n{snapshot}\n\n"
        f"## 你已做过的动作（最近 {len(history)} 步）\n{history_text or '(还没有动作)'}\n\n"
        f"## 评测关注点\n{comment_targets or '(无特别指定，全面体验)'}\n\n"
        f"请决定下一步动作。动作必须是快照里可交互元素 idx 对应的操作。"
        f"如果已通关、已经充分体验、或连续多步没有进展，用 finish 结束。"
    )
    decision = llm.call(
        [{"role": "system", "content": _DECISION_SYSTEM_PROMPT},
         {"role": "user", "content": user_msg}],
        response_model=StepDecision,
    )
    if isinstance(decision, StepDecision):
        return decision
    # 某些 provider 返回 dict
    return StepDecision.model_validate(decision)


def summarize_trial(llm, history: list[dict], comment_targets: str = "") -> FinishDecision:
    """试玩结束后，让 LLM 总结整局。"""
    history_text = _format_history(history)
    user_msg = (
        f"## 试玩记录（共 {len(history)} 步）\n{history_text}\n\n"
        f"## 评测关注点\n{comment_targets or '(无特别指定)'}\n\n"
        f"请总结这次试玩。"
    )
    result = llm.call(
        [{"role": "system", "content": _FINISH_SYSTEM_PROMPT},
         {"role": "user", "content": user_msg}],
        response_model=FinishDecision,
    )
    if isinstance(result, FinishDecision):
        return result
    return FinishDecision.model_validate(result)


# ============================================================
#  完整试玩循环
# ============================================================

def run_trial(
    llm,
    game_url: str,
    comment_targets: str = "",
    out_dir: str = "trial_output",
    max_steps: int = MAX_STEPS,
    min_steps: int = MIN_STEPS,
    time_budget_s: int = TIME_BUDGET_S,
    no_progress_limit: int = NO_PROGRESS_LIMIT,
) -> dict:
    """跑一次完整试玩，返回试玩日志 dict。

    Args:
        llm: CrewAI LLM 实例（决策用）
        game_url: 要玩的游戏 URL
        comment_targets: 评测关注点（可选）
        out_dir: 截图/日志输出目录
    """
    os.makedirs(out_dir, exist_ok=True)
    start_time = time.time()
    steps: list[dict] = []
    last_snapshot_text = ""
    last_progress_note = ""
    no_progress_count = 0

    browser = GameBrowser()
    status = "timeout"
    verdict = ""

    try:
        browser.start()
        page = browser.page
        page.goto(game_url, timeout=25000, wait_until="domcontentloaded")
        time.sleep(1.5)

        # 登录检测
        login = detect_login_redirect(page)
        if login:
            status = "stuck"
            verdict = (
                f"无法访问：{login}\n\n"
                f"该页面受 oauth2-proxy 保护，当前环境未配置有效登录 cookie。"
                f"请参考 docs/game-auth-setup.md 配置 GAME_AUTH_COOKIE 后重试。"
            )
            return _build_log(game_url, out_dir, steps, status, verdict, what="", state="")

        # 开局快照
        first_scan = _scan(page)
        last_snapshot_text = first_scan
        screenshot = _shot(page, out_dir, "start")
        steps.append(_mk_step(0, "scan", "", first_scan, "打开页面，观察游戏界面", "刚进入页面，先了解这是什么游戏", screenshot))

        history = [s.copy() for s in steps]  # 供决策参考

        # 主循环
        for n in range(1, max_steps + 1):
            if time.time() - start_time > time_budget_s:
                status = "timeout"
                verdict = f"达到时间预算 {time_budget_s}s，提前结束试玩"
                break

            # 决策
            try:
                decision = decide_step(llm, last_snapshot_text, history, comment_targets)
            except Exception as e:
                verdict = f"LLM 决策失败: {e}"
                status = "stuck"
                break

            action = decision.action
            result = ""

            # 执行
            if action == "click":
                result = _click(page, decision.idx)
            elif action == "type":
                result = _type(page, decision.idx, decision.text)
            elif action == "select":
                result = _select(page, decision.idx, decision.text)
            elif action == "press":
                result = _press(page, decision.key)
            elif action == "wait":
                result = _wait(page, decision.wait_seconds)
            elif action == "finish":
                status = "completed"
                verdict = decision.progress_note
                break
            else:
                result = f"未知动作 {action}，仅扫描"

            time.sleep(0.5)
            new_scan = _scan(page)
            last_snapshot_text = new_scan
            screenshot = _shot(page, out_dir, f"step{n}")

            # 进度判定（LLM 的 progress_note 驱动，工具层只统计无进展步数）
            if decision.progress_note and decision.progress_note != last_progress_note:
                no_progress_count = 0
                last_progress_note = decision.progress_note
            else:
                no_progress_count += 1

            step_rec = _mk_step(
                n, action, f"{decision.text or decision.idx or decision.key}",
                result, decision.reasoning, decision.progress_note, screenshot,
            )
            steps.append(step_rec)
            history.append(step_rec)

            # 无进展保护
            if no_progress_count >= no_progress_limit:
                status = "stuck"
                verdict = f"连续 {no_progress_limit} 步无进展，提前结束"
                break

        # 收尾：总结整局
        try:
            fin = summarize_trial(llm, history, comment_targets)
            if status == "timeout":
                fin_status = "timeout"
            elif status == "stuck":
                fin_status = "stuck"
            else:
                fin_status = "completed" if len(steps) >= min_steps else "partial"
            if len(steps) < min_steps:
                fin_status = "partial"
            status = fin_status
            verdict = f"{verdict}\n\n## 试玩员总结\n{fin.verdict_text}"
            what = fin.what_is_this_game
            state = fin.game_state_summary
        except Exception as e:
            what = ""
            state = ""
            if not verdict:
                verdict = f"试玩结束但总结失败: {e}"

    except Exception as e:
        status = "stuck"
        verdict = f"试玩异常终止: {type(e).__name__}: {e}"
        what = ""
        state = ""

    finally:
        browser.close()

    return _build_log(game_url, out_dir, steps, status, verdict, what, state)


# ============================================================
#  执行辅助
# ============================================================

def _mk_step(n: int, action: str, target: str, result: str, reasoning: str, progress_note: str, screenshot: str) -> dict:
    return {
        "n": n,
        "action": action,
        "target": target,
        "result": result,
        "reasoning": reasoning,
        "progress_note": progress_note,
        "screenshot": os.path.basename(screenshot) if screenshot else "",
    }


def _click(page, idx):
    from tools.game_play.tools import _get_element_by_idx
    el = _get_element_by_idx(page, idx)
    if el is None:
        return f"找不到索引 {idx} 的元素，请重新 page_scan"
    try:
        el.scroll_into_view_if_needed(timeout=5000)
        el.click(timeout=8000)
        return f"已点击 [{idx}]"
    except Exception as e:
        return f"点击失败: {e}"


def _type(page, idx, text):
    from tools.game_play.tools import _get_element_by_idx
    el = _get_element_by_idx(page, idx)
    if el is None:
        return f"找不到索引 {idx} 的输入框，请重新 page_scan"
    try:
        el.fill("", timeout=5000)
        el.fill(text, timeout=8000)
        el.press("Enter", timeout=3000)
        return f"已输入并提交: {text[:50]}"
    except Exception as e:
        return f"输入失败: {e}"


def _select(page, idx, value):
    from tools.game_play.tools import _get_element_by_idx
    el = _get_element_by_idx(page, idx)
    if el is None:
        return f"找不到索引 {idx} 的下拉框，请重新 page_scan"
    try:
        el.select_option(value, timeout=5000)
        return f"已选择: {value}"
    except Exception as e:
        try:
            el.select_option(label=value, timeout=5000)
            return f"已选择: {value}"
        except Exception:
            return f"下拉选择失败: {e}"


def _press(page, key):
    try:
        page.keyboard.press(key)
        return f"已按键 {key}"
    except Exception as e:
        return f"按键失败: {e}"


def _wait(page, seconds):
    import time as _t
    _t.sleep(seconds)
    return f"已等待 {seconds}s"


def _shot(page, out_dir: str, name: str) -> str:
    import re as _re
    safe = _re.sub(r"[^\w\-]", "_", name)[:40] or "step"
    path = os.path.join(out_dir, f"{safe}.png")
    try:
        page.screenshot(path=path, full_page=False)
        return path
    except Exception:
        return ""


def _format_history(history: list[dict]) -> str:
    lines = []
    for h in history[-10:]:  # 只带最近 10 步，控制上下文
        n = h.get("n", "?")
        act = h.get("action", "")
        target = h.get("target", "")
        note = h.get("progress_note", "")
        lines.append(f"step{n} [{act} {target}]: {note}")
    return "\n".join(lines)


def _build_log(game_url, out_dir, steps, status, verdict, what, state) -> dict:
    return {
        "game_url": game_url,
        "game_name": "",  # 由 LLM 总结填充
        "out_dir": out_dir,
        "steps": steps,
        "evidence": _list_evidence(out_dir),
        "outcome": {
            "status": status,
            "verdict_text": verdict,
            "rounds_played": len(steps),
            "what_is_this_game": what,
            "game_state_summary": state,
        },
        "comment_targets": "",
    }


def _list_evidence(out_dir: str) -> list[dict]:
    import glob
    ev = []
    for p in sorted(glob.glob(os.path.join(out_dir, "*.png"))):
        ev.append({"type": "screenshot", "path": p, "title": os.path.basename(p)})
    return ev


def save_log(log: dict, out_dir: str = "trial_output") -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "trial_log.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    return path


# ============================================================
#  提示词
# ============================================================

_DECISION_SYSTEM_PROMPT = """你是「游戏试玩员」，负责用浏览器真实游玩一个网页游戏，为后续评测采集第一手体验。

行为准则：
1. 每次只基于最新的 page_scan 快照行动——页面是动态的，任何操作后都要重新 page_scan。
2. 先理解这是什么游戏：读界面文本、标题、按钮，判断玩法、目标和当前状态。
3. 像真人玩家一样行动：进入游戏、完成新手引导、体验核心玩法、推进进度、尽量玩到结算/通关。
4. 用 click 操作按钮/选项，用 type 在输入框填文本，用 select 选下拉，用 press 按 Enter 提交。
5. 每步用 progress_note 记录你理解的游戏进展；若你已经通关、充分体验、或连续多步毫无进展，用 finish 结束。
6. 不要编造页面状态。看不到的不要猜。如果快照里没有合适元素，wait 一下再 scan。
7. 你是一个认真的玩家，不是破坏者——不要故意点明显危险的按钮，遇到需要登录的页面就 finish 并说明。

输出是一个决策 JSON：reasoning（你的理解）、action（click/type/select/press/wait/finish）、
idx（目标元素索引）、text/key（输入内容或按键）、wait_seconds、progress_note（这步推进了什么）。"""

_FINISH_SYSTEM_PROMPT = """你是「游戏试玩员」。试玩已结束，请总结整局体验。

输出：
- status: completed（通关/正常体验）/ partial（玩了一部分）/ stuck（卡住或登录受阻）/ timeout（超时）
- verdict_text: 对这次试玩的总结——玩了什么、玩到什么程度、体验如何
- what_is_this_game: 你理解的这个游戏的玩法、目标、类型
- game_state_summary: 结束时的游戏状态概要"""


if __name__ == "__main__":
    # 冒烟测试：不调 LLM，只验证浏览器能打开目标 URL 并扫描
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:4173"
    print(f"smoke: 打开 {url} 并扫描……")
    b = GameBrowser()
    try:
        b.start()
        page = b.page
        page.goto(url, timeout=20000, wait_until="domcontentloaded")
        time.sleep(1.5)
        print(_scan(page)[:1500])
        print("\n[smoke] 页面可访问")
    finally:
        b.close()
