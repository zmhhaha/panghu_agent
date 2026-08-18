"""
钟馗伏魔 — Gradio Web UI
一句话输入，钟馗判官给你断一断。
"""
import os
import time as _time
import gradio as gr
import requests

API_BASE = os.getenv("API_BASE", "http://api.research-agent.svc.cluster.local")
THEME = gr.themes.Soft(primary_hue="red", secondary_hue="gray")
MAX_WAIT = 600


def do_zhongkuifumo(text: str, request: gr.Request):
    _busy = gr.update(interactive=False)
    _ready = gr.update(interactive=True)

    text = (text or "").strip()
    if not text:
        yield "⚠️ 你有什么事要禀告？", _ready
        return

    user_id = request.headers.get("X-Forwarded-User", "") if request else ""

    try:
        r = requests.post(f"{API_BASE}/zhongkuifumo_agent",
                          json={"text": text, "user_id": user_id}, timeout=10)
        if r.status_code == 429:
            yield "⏳ 本官还在审上一桩案子，稍等", _ready
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

    yield f"⏳ 本官翻翻生死簿……", _busy

    for i in range(MAX_WAIT // 5):
        try:
            r = requests.get(f"{API_BASE}/zhongkuifumo_agent/{task_id}", timeout=10)
            resp = r.json()
            status = resp.get("status")

            if status is None:
                yield f"❌ API 返回异常: {resp}", _ready
                return

            if status == "done":
                yield resp.get("report", "(空案卷)"), _ready
                return
            elif status == "failed":
                yield f"❌ {resp.get('error', '未知')}", _ready
                return
            elif status == "running":
                dots = "." * ((i % 3) + 1)
                yield f"⏳ 本官正在审案{dots}", _busy

            else:
                dots = "." * ((i % 3) + 1)
                yield f"⏳ {status}{dots}", _busy

            _time.sleep(5)

        except Exception as e:
            yield f"❌ {e}", _ready
            return

    yield "⏰ 此案暂押，日后再审", _ready


CSS = """
.gr-box {border-radius: 8px;}
h1 {font-family: "Noto Serif SC", "STSong", serif; font-weight: 300;}
.dark .gr-box {background: #1c1917;}
textarea {font-size: 1.1em !important; line-height: 1.8 !important;}
"""

with gr.Blocks(title="👹 钟馗伏魔", theme=THEME, css=CSS) as demo:
    gr.Markdown("""# 👹 钟馗伏魔

有什么心事、委屈、不平——跟本官说说。该夸的夸，该骂的骂，该劝的劝。

> **阴司不讲人情，阴司讲的是公道。**
""")

    text_input = gr.Textbox(
        label="",
        placeholder="禀告判官：我有一桩心事……比如：在公司被人穿小鞋，明明不是我的错。",
        lines=4,
    )
    btn = gr.Button("👹 惊堂木一拍", variant="primary", size="lg")
    output = gr.Markdown()

    btn.click(
        fn=do_zhongkuifumo,
        inputs=[text_input],
        outputs=[output, btn],
    )

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=2).launch(
        server_name="0.0.0.0",
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7864")),
    )
