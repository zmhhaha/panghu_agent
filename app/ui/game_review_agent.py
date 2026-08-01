"""
通用游戏试玩评价 agent — Gradio Web UI

输入游戏 URL + 评测关注点，后台跑试玩评测，完成后展示 Markdown 报告。
"""
import os
import time as _time
import gradio as gr
import requests

API_BASE = os.getenv("API_BASE", "http://api.game-review-agent.svc.cluster.local")
THEME = gr.themes.Soft(primary_hue="indigo", secondary_hue="slate")
MAX_WAIT = 1200  # 试玩评测较慢（含真实浏览器游玩），放宽到 20 分钟


def do_review(game_url: str, comment_targets: str, request: gr.Request):
    _busy = gr.update(interactive=False)
    _ready = gr.update(interactive=True)

    game_url = (game_url or "").strip()
    comment_targets = (comment_targets or "").strip()
    if not game_url:
        yield "⚠️ 请填写游戏 URL", _ready
        return

    user_id = request.headers.get("X-Forwarded-User", "") if request else ""

    try:
        r = requests.post(
            f"{API_BASE}/game_review",
            json={"game_url": game_url, "comment_targets": comment_targets, "user_id": user_id},
            timeout=10,
        )
        if r.status_code == 429:
            yield "⏳ 上一个还在处理，稍等", _ready
            return
        r.raise_for_status()
        data = r.json()
        if data.get("status") == "done":
            yield data.get("report", "(空)"), _ready
            return
        task_id = data["id"]
    except Exception as e:
        yield f"❌ {e}", _ready
        return

    yield f"⏳ 已提交任务 {task_id[:8]}，正在启动浏览器试玩（预计几分钟）……", _busy

    # 轮询任务状态
    last_msg = ""
    for i in range(MAX_WAIT // 5):
        try:
            r = requests.get(f"{API_BASE}/game_review/{task_id}", timeout=10)
            resp = r.json()
            status = resp.get("status")

            if status is None:
                yield f"❌ API 返回异常: {resp}", _ready
                return
            if status == "done":
                yield resp.get("report", "(空)"), _ready
                return
            elif status == "failed":
                yield f"❌ {resp.get('error', '未知错误')}", _ready
                return

            # 进度提示
            if i % 12 == 0:  # 每 ~1 分钟换一条提示
                msgs = [
                    "🎮 正在进入游戏……",
                    "🕹️ 试玩员正在探索玩法……",
                    "🎯 正在推进游戏进度……",
                    "📸 正在采集游戏截图证据……",
                    "📝 正在撰写评测报告……",
                ]
                last_msg = msgs[min(i // 12, len(msgs) - 1)]
            yield f"⏳ {last_msg}（{int(i * 5 / 60)}分{int(i * 5 % 60)}秒）", _busy
            _time.sleep(5)

        except Exception as e:
            yield f"❌ {e}", _ready
            return

    yield "⏰ 超过 20 分钟仍未完成，请稍后查看任务状态", _ready


CSS = """
.gr-box {border-radius: 8px;}
h1 {font-family: "Noto Serif SC", "STSong", serif; font-weight: 600;}
.dark .gr-box {background: #1e1b4b;}
textarea {font-size: 1.05em !important; line-height: 1.7 !important;}
"""

with gr.Blocks(title="🎮 游戏试玩评价", theme=THEME, css=CSS) as demo:
    gr.Markdown("""# 🎮 游戏试玩评价

输入一个网页游戏的 **URL**，我会用浏览器真实游玩它，并从玩法、界面、叙事、难度、技术表现等维度输出一份评测报告。

支持任意网页游戏——只要你提供可访问的链接（受保护页面需已配置访问凭据）。
""")

    with gr.Row():
        with gr.Column(scale=3):
            game_url = gr.Textbox(
                label="游戏 URL",
                placeholder="https://qianfu.panghuer.top 或 https://tewu.panghuer.top …",
                lines=1,
            )
        with gr.Column(scale=2):
            comment_targets = gr.Textbox(
                label="评测关注点（可选）",
                placeholder="如：玩法深度 / 界面设计 / 难度曲线 …",
                lines=1,
            )

    btn = gr.Button("🎮 开始试玩评测", variant="primary", size="lg")
    output = gr.Markdown()

    btn.click(
        fn=do_review,
        inputs=[game_url, comment_targets],
        outputs=[output, btn],
    )

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=2).launch(
        server_name="0.0.0.0",
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7862")),
    )
