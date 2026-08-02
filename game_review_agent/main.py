#!/usr/bin/env python3
"""通用游戏试玩评价 — 本地 CLI。

用法：
    python game_review_agent/main.py <game_url> [关注点] [--out 目录] [--steps N] [--smoke]

示例：
    python game_review_agent/main.py http://localhost:4173 "关注玩法/界面"
    python game_review_agent/main.py http://localhost:4173 --smoke   # 不调 LLM，只验证浏览器能打开

部署在 K8s 时由 app/api/game_review_agent.py 调用同一条流水线。
"""
import argparse
import json
import os
import sys
import time

# 把 panghu_agent 根目录加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.game_play.browser import GameBrowser
from tools.game_play.tools import make_game_tools


def run_smoke(url: str) -> bool:
    """冒烟：只验证浏览器能打开 URL 并扫描，不调 LLM。"""
    print(f"[smoke] 打开 {url} …")
    from tools.game_play.tools import _scan
    b = GameBrowser()
    try:
        b.start()
        page = b.page
        page.goto(url, timeout=25000, wait_until="domcontentloaded")
        time.sleep(1.5)
        snapshot = _scan(page)
        print(snapshot[:2000])
        print("\n[smoke] ✅ 页面可访问，Playwright 正常")
        return True
    except Exception as e:
        print(f"[smoke] ❌ 失败: {type(e).__name__}: {e}")
        return False
    finally:
        b.close()


def main():
    parser = argparse.ArgumentParser(description="通用游戏试玩评价 agent")
    parser.add_argument("url", nargs="?", default="", help="游戏 URL")
    parser.add_argument("targets", nargs="*", default=[], help="评测关注点")
    parser.add_argument("--out", default="trial_output", help="输出目录")
    parser.add_argument("--steps", type=int, default=0, help="最大步数（默认用环境变量/40）")
    parser.add_argument("--smoke", action="store_true", help="冒烟模式：只验证浏览器能打开，不调 LLM")
    parser.add_argument("--headless", type=int, default=None, help="0 开窗口，1 headless，默认按 GAME_HEADLESS")
    args = parser.parse_args()

    if args.smoke or not args.url:
        if not args.url:
            print("请提供游戏 URL：python game_review_agent/main.py <url> [--smoke]")
            return
        ok = run_smoke(args.url)
        sys.exit(0 if ok else 1)

    if args.headless is not None:
        os.environ["GAME_HEADLESS"] = str(args.headless)

    from game_review_agent.crew import create_game_review_crew
    from game_review_agent.llm_config import PRIMARY_LLM

    game_url = args.url
    comment_targets = " ".join(args.targets)
    out_dir = args.out
    os.makedirs(out_dir, exist_ok=True)

    print("=" * 60)
    print(f"  游戏试玩评价 agent")
    print(f"  游戏: {game_url}")
    print(f"  关注点: {comment_targets or '(全面体验)'}")
    print(f"  输出: {out_dir}")
    print("=" * 60)

    # 打开浏览器并创建工具（传给试玩员 agent）
    browser = GameBrowser()
    browser.start()
    page = browser.page
    tools = make_game_tools(page, out_dir)

    try:
        crew = create_game_review_crew(
            game_url=game_url,
            comment_targets=comment_targets,
            browser_tools=tools,
            out_dir=out_dir,
        )
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = str(pool.submit(crew.kickoff, {
                "game_url": game_url,
                "comment_targets": comment_targets,
            }).result())
        report = str(result)

        report_path = os.path.join(out_dir, "report.md")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)

        print("\n" + "=" * 60)
        print(f"  试玩评价完成！报告: {report_path}")
        print("=" * 60)
        print(report)
    finally:
        browser.close()


if __name__ == "__main__":
    main()
