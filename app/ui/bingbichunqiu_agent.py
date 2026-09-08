"""秉笔春秋 - Gradio Web UI。"""
import os
import time as _time

import gradio as gr
import requests

API_BASE = os.getenv("API_BASE", "http://api.bingbichunqiu-agent.svc.cluster.local")
THEME = gr.themes.Soft(primary_hue="blue", secondary_hue="stone")
MAX_WAIT = 600


def do_bingbichunqiu(text: str, request: gr.Request):
    busy = gr.update(interactive=False)
    ready = gr.update(interactive=True)
    text = (text or "").strip()
    if not text:
        yield "先落一笔，史官不能对着空白竹简起笔。", ready
        return
    user_id = request.headers.get("X-Forwarded-User", "") if request else ""
    try:
        response = requests.post(
            f"{API_BASE}/bingbichunqiu_agent",
            json={"text": text, "user_id": user_id}, timeout=10,
        )
        if response.status_code == 429:
            yield "上一次查考还未完，请稍候。", ready
            return
        if not response.ok:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = None
            yield f"查考失败：{detail or response.text or response.reason}", ready
            return
        response.raise_for_status()
        data = response.json()
        if data.get("status") == "done":
            yield data.get("report", "(空)"), ready
            return
        task_id = data["id"]
    except Exception as exc:
        yield f"请求失败：{exc}", ready
        return

    yield "正在查考旧闻，整理因果……", busy
    for index in range(MAX_WAIT // 5):
        try:
            response = requests.get(f"{API_BASE}/bingbichunqiu_agent/{task_id}", timeout=10)
            response.raise_for_status()
            data = response.json()
            status = data.get("status")
            if status == "done":
                yield data.get("report", "(空)"), ready
                return
            if status == "failed":
                yield f"查考失败：{data.get('error', '未知错误')}", ready
                return
            if status is None:
                yield f"API 返回异常：{data}", ready
                return
            dots = "." * ((index % 3) + 1)
            yield f"正在查考旧闻，整理因果{dots}", busy
            _time.sleep(5)
        except Exception as exc:
            yield f"请求失败：{exc}", ready
            return
    yield "查考用时较长，请稍后再来。", ready


CSS = """
.gr-box {border-radius: 8px;}
h1 {font-family: "Noto Serif SC", "STSong", serif; font-weight: 400;}
textarea {font-size: 1.05em !important; line-height: 1.75 !important;}
"""

with gr.Blocks(title="📜 秉笔春秋", theme=THEME, css=CSS) as demo:
    gr.Markdown("""# 📜 秉笔春秋

把人物、制度、事件或你心中的疑问写下来，请史官从史料、时代与因果中为你梳理。

> 据事直书，褒贬寓于叙事；以古鉴今，不以传说冒充信史。
""")
    text_input = gr.Textbox(
        label="史事或问题",
        placeholder="例如：为什么秦始皇能完成统一？历代史家又如何评价这件事？",
        lines=5, max_lines=12,
    )
    consult_button = gr.Button("📜 请史官落笔", variant="primary", size="lg")
    output = gr.Markdown()
    consult_button.click(fn=do_bingbichunqiu, inputs=[text_input], outputs=[output, consult_button])

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=2).launch(
        server_name="0.0.0.0", server_port=int(os.getenv("GRADIO_SERVER_PORT", "7869")),
    )
