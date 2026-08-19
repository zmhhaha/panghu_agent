"""Minimal Gradio UI with checkpoints and download buttons."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import gradio as gr
import requests


API_BASE = os.getenv("API_BASE") or os.getenv("LITERATURE_API_BASE", "http://127.0.0.1:8001")


def _request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    response = requests.request(method, f"{API_BASE}{path}", timeout=30, **kwargs)
    response.raise_for_status()
    return response.json()


def _render(task: dict[str, Any]) -> str:
    if not task:
        return ""
    lines = [f"## {task.get('topic', '')}", f"状态: `{task.get('status')}`", f"阶段: `{task.get('phase')}`", task.get("progress", "")]
    search = task.get("search") or {}
    if search:
        lines.extend(["", f"检索结果: {search.get('total', 0)} 篇，待下载 {search.get('need_download', 0)} 篇，本地命中 {search.get('local_hits', 0)} 篇"])
    collection = task.get("collection") or {}
    if collection:
        lines.extend([f"收集轮次: {collection.get('current_round', 0)}", f"通过校验: {collection.get('cumulative_verified', 0)}", f"待处理: {collection.get('still_pending_count', 0)}"])
    if task.get("error"):
        lines.extend(["", f"错误: {task['error']}"])
    papers = task.get("papers") or []
    if papers:
        lines.extend(["", "| 文献 | PDF 状态 | 校验 |", "|---|---|---|"])
        lines.extend(f"| {paper.get('title', '')[:90]} | {paper.get('pdf_status', '')} | {paper.get('verification_status', '') or '-'} |" for paper in papers)
    return "\n".join(lines)


def start(topic: str, max_rounds: int) -> tuple[str, str, str]:
    topic = (topic or "").strip()
    if not topic:
        return "请输入研究主题", "", ""
    try:
        data = _request("POST", "/literature-download", json={"topic": topic, "max_rounds": max_rounds})
        return _render(data), data["id"], ""
    except requests.RequestException as exc:
        return f"启动失败: {exc}", "", ""


def refresh(task_id: str) -> str:
    if not task_id:
        return ""
    try:
        return _render(_request("GET", f"/literature-download/{task_id}"))
    except requests.RequestException as exc:
        return f"状态查询失败: {exc}"


def action(task_id: str, endpoint: str) -> str:
    if not task_id:
        return "请先启动任务"
    try:
        data = _request("POST", f"/literature-download/{task_id}/{endpoint}")
        return _render(data.get("task") or data)[0:20_000]
    except requests.RequestException as exc:
        return f"操作失败: {exc}"


def download_file(task_id: str, endpoint: str, suffix: str) -> str:
    if not task_id:
        raise gr.Error("请先启动任务")
    response = requests.get(f"{API_BASE}/literature-download/{task_id}/{endpoint}", timeout=60)
    if not response.ok:
        raise gr.Error(response.text or "文件暂不可用")
    path = Path(tempfile.gettempdir()) / f"literature-{task_id[:8]}{suffix}"
    path.write_bytes(response.content)
    return str(path)


with gr.Blocks(title="文献下载工具") as demo:
    gr.Markdown("# 文献下载工具\n三阶段流程：检索 → PDF 收集 → 文件校验")
    topic = gr.Textbox(label="研究主题", lines=2, placeholder="例如：InP 的干法刻蚀研究进展")
    rounds = gr.Slider(1, 10, value=3, step=1, label="最大收集轮数")
    start_button = gr.Button("开始检索", variant="primary")
    task_id = gr.Textbox(label="任务 ID", interactive=False)
    status = gr.Markdown()
    with gr.Row():
        approve_button = gr.Button("确认清单并开始下载")
        retry_button = gr.Button("重试失败文献")
        finish_button = gr.Button("结束收集")
        refresh_button = gr.Button("刷新状态")
    with gr.Row():
        report_button = gr.DownloadButton("下载报告")
        pdf_button = gr.DownloadButton("下载已校验 PDF")

    start_button.click(start, [topic, rounds], [status, task_id, report_button])
    approve_button.click(lambda tid: action(tid, "approve"), task_id, status)
    retry_button.click(lambda tid: action(tid, "retry"), task_id, status)
    finish_button.click(lambda tid: action(tid, "finish"), task_id, status)
    refresh_button.click(refresh, task_id, status)
    timer = gr.Timer(value=2.0, active=True)
    timer.tick(refresh, task_id, status)
    report_button.click(lambda tid: download_file(tid, "report/download", ".md"), task_id, report_button)
    pdf_button.click(lambda tid: download_file(tid, "files/download", ".zip"), task_id, pdf_button)


if __name__ == "__main__":
    port = int(os.getenv("GRADIO_SERVER_PORT", os.getenv("LITERATURE_UI_PORT", "7860")))
    demo.queue().launch(server_name="0.0.0.0", server_port=port)
