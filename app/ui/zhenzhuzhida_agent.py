"""
真主至大 — Gradio Web UI
"""
import os, time, gradio as gr, requests
API_BASE = os.getenv("API_BASE", "http://api.research-agent.svc.cluster.local")
THEME = gr.themes.Soft(primary_hue="green", secondary_hue="stone")
MAX_WAIT = 600

def do_zhenzhuzhida(text: str, request: gr.Request):
    _busy = gr.update(interactive=False); _ready = gr.update(interactive=True)
    text = (text or "").strip()
    if not text: yield "⚠️ 写点什么吧", _ready; return
    user_id = request.headers.get("X-Forwarded-User", "") if request else ""
    try:
        r = requests.post(f"{API_BASE}/zhenzhuzhida_agent", json={"text": text, "user_id": user_id}, timeout=10)
        if r.status_code == 429: yield "⏳ 上一个还在处理，稍等", _ready; return
        if not r.ok:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = None
            yield f"配置错误：{detail or r.text or r.reason}", _ready
            return
        r.raise_for_status(); data = r.json()
        if data.get("status") == "done": yield data.get("report", "(空)"), _ready; return
        task_id = data["id"]
    except Exception as e: yield f"❌ {e}", _ready; return
    yield "⏳ 在想……", _busy
    for i in range(MAX_WAIT // 5):
        try:
            r = requests.get(f"{API_BASE}/zhenzhuzhida_agent/{task_id}", timeout=10)
            resp = r.json(); status = resp.get("status")
            if status is None: yield f"❌ API 返回异常: {resp}", _ready; return
            if status == "done": yield resp.get("report", "(空)"), _ready; return
            elif status == "failed": yield f"❌ {resp.get('error', '未知')}", _ready; return
            elif status == "running": yield f"⏳ 在想{'·'* (i%3+1)}", _busy
            else: yield f"⏳ {status}", _busy
            time.sleep(5)
        except Exception as e: yield f"❌ {e}", _ready; return
    yield "⏰ 稍后再来看看？", _ready

with gr.Blocks(title="🕌 真主至大", theme=THEME) as demo:
    gr.Markdown("# 🕌 真主至大\n\n用古兰经的眼光回你的心事。奉至仁至慈的真主之名。\n\n> **真主与坚忍者同在。**\n")
    text_input = gr.Textbox(label="", placeholder="随便写点什么…", lines=4)
    btn = gr.Button("🕌 真主怎么说", variant="primary", size="lg")
    output = gr.Markdown()
    btn.click(fn=do_zhenzhuzhida, inputs=[text_input], outputs=[output, btn])

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=2).launch(server_name="0.0.0.0", server_port=int(os.getenv("GRADIO_SERVER_PORT", "7866")))
