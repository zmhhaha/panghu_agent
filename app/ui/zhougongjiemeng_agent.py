"""周公解梦 - Gradio Web UI。"""
import os
import time as _time

import gradio as gr
import requests


API_BASE = os.getenv("API_BASE", "http://api.zhougongjiemeng-agent.svc.cluster.local")
THEME = gr.themes.Soft(primary_hue="emerald", secondary_hue="amber")
MAX_WAIT = 600


def do_zhougongjiemeng(text: str, request: gr.Request):
    busy = gr.update(interactive=False)
    ready = gr.update(interactive=True)

    text = (text or "").strip()
    if not text:
        yield "请先写下梦里的情节。", ready
        return

    try:
        response = requests.post(
            f"{API_BASE}/zhougongjiemeng_agent",
            json={"text": text},
            timeout=10,
        )
        if response.status_code == 429:
            yield "上一个梦还在解读，请稍等。", ready
            return
        if not response.ok:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = None
            yield f"解读失败：{detail or response.text or response.reason}", ready
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

    yield "正在梳理梦中的线索...", busy

    for index in range(MAX_WAIT // 5):
        try:
            response = requests.get(
                f"{API_BASE}/zhougongjiemeng_agent/{task_id}",
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            status = data.get("status")

            if status == "done":
                yield data.get("report", "(空)"), ready
                return
            if status == "failed":
                yield f"解读失败：{data.get('error', '未知错误')}", ready
                return
            if status is None:
                yield f"API 返回异常：{data}", ready
                return

            dots = "." * ((index % 3) + 1)
            yield f"正在梳理梦中的线索{dots}", busy
            _time.sleep(5)
        except Exception as exc:
            yield f"请求失败：{exc}", ready
            return

    yield "解读用时较长，请稍后再试。", ready


CSS = """
.gr-box {border-radius: 8px;}
h1 {font-family: "Noto Serif SC", "STSong", serif; font-weight: 400;}
textarea {font-size: 1.05em !important; line-height: 1.75 !important;}
"""


with gr.Blocks(title="周公解梦", theme=THEME, css=CSS) as demo:
    gr.Markdown("""# 周公解梦

写下你记得的梦境。人物、地点、颜色、结局，以及梦里最强烈的感受，都会影响解读。

> 梦可解，命不可由一场梦来定。
""")

    text_input = gr.Textbox(
        label="梦境",
        placeholder=(
            "例如：我梦见大水一直涨，带着孩子往楼上跑，最后到了屋顶。"
            "当时很着急，但醒来后反而松了一口气。"
        ),
        lines=6,
        max_lines=12,
    )
    interpret_button = gr.Button("解读梦境", variant="primary", size="lg")
    output = gr.Markdown()

    interpret_button.click(
        fn=do_zhougongjiemeng,
        inputs=[text_input],
        outputs=[output, interpret_button],
    )


if __name__ == "__main__":
    demo.queue(default_concurrency_limit=2).launch(
        server_name="0.0.0.0",
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7867")),
    )
