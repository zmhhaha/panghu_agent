"""百家争鸣共享 Gradio UI。"""
from __future__ import annotations

import os
import time

import gradio as gr
import requests

from .registry import AgentDefinition, get_registry


API_BASE = os.getenv(
    "API_BASE", "http://baijiazhengming-api.baijiazhengming.svc.cluster.local"
).rstrip("/")
MAX_WAIT = int(os.getenv("MAX_WAIT", "600"))
REGISTRY = get_registry()
DEFAULT_SLUG = os.getenv("AGENT_SLUG", "").strip()


def _resolve(request: gr.Request | None) -> AgentDefinition | None:
    if request:
        host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host", "")
        definition = REGISTRY.from_host(host)
        if definition:
            return definition
    if DEFAULT_SLUG:
        return REGISTRY.get(DEFAULT_SLUG)
    enabled = REGISTRY.list_enabled()
    return enabled[0] if len(enabled) == 1 else None


def load_branding(request: gr.Request):
    definition = _resolve(request)
    if not definition:
        return (
            "# 百家争鸣\n\n当前域名未登记，请联系管理员。",
            gr.update(label="输入", placeholder="", interactive=False),
            gr.update(value="不可用", interactive=False),
        )
    heading = f"# {definition.icon} {definition.display_name}\n\n{definition.tagline}"
    return (
        heading,
        gr.update(
            label=definition.input_label,
            placeholder=definition.input_placeholder,
            interactive=True,
        ),
        gr.update(value=definition.submit_label, interactive=True),
    )


def do_agent(text: str, request: gr.Request):
    busy = gr.update(interactive=False)
    ready = gr.update(interactive=True)
    definition = _resolve(request)
    if not definition:
        yield "当前域名没有对应的 Agent。", ready
        return
    text = (text or "").strip()
    if not text:
        yield definition.empty_message, ready
        return
    user_id = request.headers.get("X-Forwarded-User", "") if request else ""
    try:
        response = requests.post(
            f"{API_BASE}/v1/agents/{definition.slug}/tasks",
            json={"text": text, "user_id": user_id},
            timeout=10,
        )
        if response.status_code == 429:
            yield response.json().get("detail", definition.busy_message), ready
            return
        if not response.ok:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = None
            yield definition.failed_message.replace(
                "{detail}", str(detail or response.text or response.reason)
            ), ready
            return
        data = response.json()
        if data.get("status") == "done":
            yield data.get("report", "(空)"), ready
            return
        task_id = data["id"]
    except Exception as error:
        yield f"请求失败：{error}", ready
        return

    yield f"{definition.waiting_message}……", busy
    for index in range(MAX_WAIT // 5):
        try:
            response = requests.get(
                f"{API_BASE}/v1/agents/{definition.slug}/tasks/{task_id}", timeout=10
            )
            response.raise_for_status()
            data = response.json()
            status = data.get("status")
            if status == "done":
                yield data.get("report", "(空)"), ready
                return
            if status == "failed":
                yield definition.failed_message.replace(
                    "{detail}", str(data.get("error", "未知错误"))
                ), ready
                return
            if status is None:
                yield f"API 返回异常：{data}", ready
                return
            dots = "." * ((index % 3) + 1)
            yield f"{definition.waiting_message}{dots}", busy
            time.sleep(5)
        except Exception as error:
            yield f"请求失败：{error}", ready
            return
    yield definition.timeout_message, ready


CSS = """
.gr-box {border-radius: 8px;}
h1 {font-family: "Noto Serif SC", "STSong", serif; font-weight: 400;}
textarea {font-size: 1.05em !important; line-height: 1.75 !important;}
"""

with gr.Blocks(title="百家争鸣", theme=gr.themes.Soft(), css=CSS) as demo:
    heading = gr.Markdown("# 百家争鸣")
    text_input = gr.Textbox(label="输入", lines=5, max_lines=12)
    submit_button = gr.Button("提交", variant="primary", size="lg")
    output = gr.Markdown()
    demo.load(load_branding, outputs=[heading, text_input, submit_button])
    submit_button.click(do_agent, inputs=[text_input], outputs=[output, submit_button])


if __name__ == "__main__":
    demo.queue(default_concurrency_limit=8).launch(
        server_name="0.0.0.0",
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7860")),
    )
