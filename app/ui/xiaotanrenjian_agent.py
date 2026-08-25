"""
笑谈人间 — Gradio Web UI
一句话输入，一段带包袱的生活回应。
"""
import os
import time as _time

import gradio as gr
import requests


API_BASE = os.getenv("API_BASE", "http://api.xiaotanrenjian-agent.svc.cluster.local")
THEME = gr.themes.Soft(primary_hue="orange", secondary_hue="stone")
MAX_WAIT = 600


def do_xiaotanrenjian(text: str, request: gr.Request):
    _busy = gr.update(interactive=False)
    _ready = gr.update(interactive=True)

    text = (text or "").strip()
    if not text:
        yield "先说两句，别让麦克风一个人站台。", _ready
        return

    user_id = request.headers.get("X-Forwarded-User", "") if request else ""
    try:
        r = requests.post(f"{API_BASE}/xiaotanrenjian_agent",
                          json={"text": text, "user_id": user_id}, timeout=10)
        if r.status_code == 429:
            yield "上一位还没说完，后台正在捋包袱，请稍等。", _ready
            return
        if not r.ok:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = None
            yield f"配置错误：{detail or r.text or r.reason}", _ready
            return
        r.raise_for_status()
        data = r.json()
        if data.get("status") == "done":
            yield data.get("report", "(空)"), _ready
            return
        task_id = data["id"]
    except Exception as exc:
        yield f"❌ {exc}", _ready
        return

    yield "⏳ 正在找包袱，先别急着鼓掌……", _busy

    for i in range(MAX_WAIT // 5):
        try:
            r = requests.get(f"{API_BASE}/xiaotanrenjian_agent/{task_id}", timeout=10)
            resp = r.json()
            status = resp.get("status")

            if status is None:
                yield f"❌ API 返回异常: {resp}", _ready
                return

            if status == "done":
                yield resp.get("report", "(空)"), _ready
                return
            elif status == "failed":
                yield f"❌ 表演中断：{resp.get('error', '未知错误')}", _ready
                return

            dots = "." * ((i % 3) + 1)
            if status == "running":
                yield f"⏳ 正在找包袱{dots}", _busy
            else:
                yield f"⏳ {status}{dots}", _busy
            _time.sleep(5)
        except Exception as exc:
            yield f"❌ {exc}", _ready
            return

    yield "⏰ 这段包袱有点长，请稍后再来，别让演员在台上干等。", _ready


CSS = """
.gr-box {border-radius: 8px;}
h1 {font-family: "Noto Serif SC", "STSong", serif; font-weight: 300;}
.dark .gr-box {background: #1c1917;}
textarea {font-size: 1.1em !important; line-height: 1.8 !important;}
"""


with gr.Blocks(title="笑谈人间", theme=THEME, css=CSS) as demo:
    gr.Markdown("""# 笑谈人间

把你的烦恼、见闻、问题或一句牢骚说出来。这里借一点经典相声的铺垫、反差和包袱，陪你把日子说得明白一点、轻松一点。

> 生活已经够像段子了，咱们先把结论说清楚，再决定笑不笑。
""")

    text_input = gr.Textbox(
        label="想聊什么",
        placeholder="例如：我每天列一堆计划，最后最稳定完成的项目是拖延。",
        lines=6,
        max_lines=12,
    )
    talk_button = gr.Button("🎙️ 开讲", variant="primary", size="lg")
    output = gr.Markdown()

    talk_button.click(
        fn=do_xiaotanrenjian,
        inputs=[text_input],
        outputs=[output, talk_button],
    )


if __name__ == "__main__":
    demo.queue(default_concurrency_limit=2).launch(
        server_name="0.0.0.0",
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7868")),
    )
