"""
佛法无边 — Gradio Web UI
一句话输入，一段让人心里一亮的回应。
"""
import os
import time as _time
import gradio as gr
import requests

API_BASE = os.getenv("API_BASE", "http://api.research-agent.svc.cluster.local")
THEME = gr.themes.Soft(primary_hue="purple", secondary_hue="stone")
MAX_WAIT = 600


def do_fofawubian(text: str, request: gr.Request):
    _busy = gr.update(interactive=False)
    _ready = gr.update(interactive=True)

    text = (text or "").strip()
    if not text:
        yield "⚠️ 写点什么吧", _ready
        return

    user_id = request.headers.get("X-Forwarded-User", "") if request else ""

    try:
        r = requests.post(f"{API_BASE}/fofawubian_agent",
                          json={"text": text, "user_id": user_id}, timeout=10)
        if r.status_code == 429:
            yield "⏳ 上一个还在处理，稍等", _ready
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
        # 命中缓存直接返回
        if data.get("status") == "done":
            yield data.get("report", "(空)"), _ready
            return
        task_id = data["id"]
    except Exception as e:
        yield f"❌ {e}", _ready
        return

    yield f"⏳ 在想……", _busy

    for i in range(MAX_WAIT // 5):
        try:
            r = requests.get(f"{API_BASE}/fofawubian_agent/{task_id}", timeout=10)
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
            elif status == "running":
                dots = "." * ((i % 3) + 1)
                yield f"⏳ 在想{dots}", _busy

            else:
                dots = "." * ((i % 3) + 1)
                yield f"⏳ {status}{dots}", _busy

            _time.sleep(5)

        except Exception as e:
            yield f"❌ {e}", _ready
            return

    yield "⏰ 稍后再来看看？", _ready


CSS = """
.gr-box {border-radius: 8px;}
h1 {font-family: "Noto Serif SC", "STSong", serif; font-weight: 300;}
.dark .gr-box {background: #1c1917;}
textarea {font-size: 1.1em !important; line-height: 1.8 !important;}
"""

with gr.Blocks(title="🙏 佛法无边", theme=THEME, css=CSS) as demo:
    gr.Markdown("""# 🙏 佛法无边

写一段心事、困惑、或者随便什么念头，我用佛法的眼光回你几句话。不长，不念经。

> **凡所有相，皆是虚妄。若见诸相非相，则见如来。**
""")

    text_input = gr.Textbox(
        label="",
        placeholder="随便写点什么… 比如：失恋了，走不出来。",
        lines=4,
    )
    btn = gr.Button("🙏 听听佛法怎么说", variant="primary", size="lg")
    output = gr.Markdown()

    btn.click(
        fn=do_fofawubian,
        inputs=[text_input],
        outputs=[output, btn],
    )

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=2).launch(
        server_name="0.0.0.0",
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7863")),
    )
