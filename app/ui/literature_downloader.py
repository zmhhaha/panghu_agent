"""Gradio UI for the automatic literature collection workflow."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator

import gradio as gr
import requests


API_BASE = os.getenv("API_BASE") or os.getenv(
    "LITERATURE_API_BASE", "http://127.0.0.1:8001"
)
MAX_WAIT = 1200
POLL_SECONDS = 5


def _request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    response = requests.request(method, f"{API_BASE}{path}", timeout=30, **kwargs)
    response.raise_for_status()
    return response.json()


def _error(response: requests.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    return str(detail or response.text or response.reason)


def _render(task: dict[str, Any], elapsed: int = 0) -> str:
    if not task:
        return ""

    status = task.get("status", "")
    lines = [
        f"## {task.get('topic', '')}",
        f"状态：`{status}`",
        f"阶段：`{task.get('phase', '')}`",
        task.get("progress", "") or "等待后台开始执行",
        f"任务 ID：`{task.get('id', '')}`",
    ]
    if elapsed:
        lines.append(f"已运行/等待：{elapsed} 秒")

    search = task.get("search") or {}
    if search:
        lines.extend(
            [
                "",
                (
                    f"检索结果：{search.get('total', 0)} 篇，"
                    f"待下载 {search.get('need_download', 0)} 篇，"
                    f"本地命中 {search.get('local_hits', 0)} 篇"
                ),
            ]
        )

    collection = task.get("collection") or {}
    if collection:
        lines.extend(
            [
                f"收集轮次：{collection.get('current_round', 0)}",
                f"通过校验：{collection.get('cumulative_verified', 0)}",
                f"待处理：{collection.get('still_pending_count', 0)}",
            ]
        )

    if status == "completed":
        lines.append("任务已完成，请下载最终报告或已校验 PDF。")
    elif status == "failed":
        lines.extend(["", f"错误：{task.get('error') or '未知错误'}"])
    elif status.startswith("waiting:"):
        # This status only belongs to tasks created by an older deployment.
        lines.append("这是旧版本任务的暂停状态，请重新提交任务以使用自动流程。")

    papers = task.get("papers") or []
    if papers:
        lines.extend(["", "| 文献 | PDF 状态 | 校验 |", "|---|---|---|"])
        lines.extend(
            (
                f"| {paper.get('title', '')[:90]} | "
                f"{paper.get('pdf_status', '')} | "
                f"{paper.get('verification_status', '') or '-'} |"
            )
            for paper in papers
        )
    return "\n".join(lines)


def _updates(task: dict[str, Any] | None, running: bool = False) -> tuple[Any, ...]:
    task = task or {}
    return (
        gr.update(interactive=not running),
        gr.update(interactive=bool(task.get("id"))),
        gr.update(visible=bool((task.get("reports") or {}).get("final"))),
        gr.update(visible=bool((task.get("reports") or {}).get("pdf_zip"))),
    )


def _watch(task_id: str) -> Iterator[tuple[Any, ...]]:
    """Poll the automatic workflow for a bounded time."""
    elapsed = 0
    for _ in range(MAX_WAIT // POLL_SECONDS):
        time.sleep(POLL_SECONDS)
        elapsed += POLL_SECONDS
        try:
            task = _request("GET", f"/literature-download/{task_id}")
        except requests.RequestException as exc:
            yield (
                f"状态查询失败：{exc}\n\n任务仍在后台运行，请稍后点击刷新状态。",
                task_id,
                *_updates({"id": task_id}, running=True),
            )
            return

        status = task.get("status", "")
        if status in {"completed", "failed", "aborted"}:
            yield (_render(task, elapsed), task_id, *_updates(task, running=False))
            return
        yield (_render(task, elapsed), task_id, *_updates(task, running=True))

    try:
        task = _request("GET", f"/literature-download/{task_id}")
        message = f"{_render(task, MAX_WAIT)}\n\n任务仍在后台运行，完成后会发送邮件。"
        yield (message, task_id, *_updates(task, running=False))
    except requests.RequestException as exc:
        yield (f"状态查询失败：{exc}", task_id, *_updates({"id": task_id}, running=False))


def start(topic: str, max_rounds: int, email: str, request: gr.Request) -> Iterator[tuple[Any, ...]]:
    topic = (topic or "").strip()
    email = (email or "").strip()
    if not topic:
        yield ("请输入研究主题", "", *_updates({}, running=False))
        return
    if not email or "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        yield ("请输入有效的邮箱地址，任务完成后会发送通知", "", *_updates({}, running=False))
        return

    user_id = request.headers.get("X-Forwarded-User", "") if request else ""
    try:
        response = requests.post(
            f"{API_BASE}/literature-download",
            json={"topic": topic, "max_rounds": int(max_rounds), "email": email, "user_id": user_id},
            timeout=10,
        )
    except requests.RequestException as exc:
        yield (f"提交失败：{exc}", "", *_updates({}, running=False))
        return
    if response.status_code == 429:
        yield (f"已有文献任务正在执行：{_error(response)}", "", *_updates({}, running=False))
        return
    if not response.ok:
        yield (f"提交失败：{_error(response)}", "", *_updates({}, running=False))
        return

    task = response.json()
    task_id = task["id"]
    yield (
        f"任务已提交，后台将自动完成检索、下载和校验。\n\n任务 ID：`{task_id}`\n\n完成后会发送邮件通知。",
        task_id,
        *_updates(task, running=True),
    )
    yield from _watch(task_id)


def refresh(task_id: str) -> tuple[Any, ...]:
    if not task_id:
        return "请先开始检索", "", *_updates({}, running=False)
    try:
        task = _request("GET", f"/literature-download/{task_id}")
        return _render(task), task_id, *_updates(task, running=False)
    except requests.RequestException as exc:
        return f"状态查询失败：{exc}", task_id, *_updates({"id": task_id}, running=False)


def download_file(task_id: str, endpoint: str, suffix: str) -> str:
    if not task_id:
        raise gr.Error("请先提交任务")
    response = requests.get(f"{API_BASE}/literature-download/{task_id}/{endpoint}", timeout=60)
    if not response.ok:
        raise gr.Error(response.text or "文件暂不可用")
    path = Path(tempfile.gettempdir()) / f"literature-{task_id[:8]}{suffix}"
    path.write_bytes(response.content)
    return str(path)


def search_history(keyword: str) -> str:
    try:
        rows = _request("GET", "/literature-reports", params={"q": (keyword or "").strip(), "limit": 20})
    except requests.RequestException as exc:
        return f"历史查询失败：{exc}"
    if not rows:
        return "未找到历史任务"

    lines = ["| 时间 | 主题 | 状态 | 任务 ID | 可用文件 |", "|---|---|---|---|---|"]
    for row in rows:
        files = []
        if row.get("report_available"):
            files.append("报告")
        if row.get("pdf_available"):
            files.append("PDF")
        lines.append(
            f"| {(row.get('created_at') or '')[:16]} | {(row.get('topic') or '')[:50]} | "
            f"{row.get('status', '')} | `{row.get('id', '')}` | {', '.join(files) or '-'} |"
        )
    lines.append("\n复制任务 ID 到“新任务”页的任务 ID 输入框并点击“刷新状态”，即可显示完成任务的下载按钮。")
    return "\n".join(lines)


with gr.Blocks(title="文献下载工具") as demo:
    gr.Markdown("# 文献下载工具\n提交一次后自动完成：文献检索 -> PDF 下载 -> 文件校验 -> 最终报告。")

    with gr.Tab("新任务"):
        topic = gr.Textbox(label="研究主题", lines=2, placeholder="例如：InP 的干法刻蚀研究进展")
        rounds = gr.Slider(1, 10, value=3, step=1, label="下载失败时的最大重试轮数")
        email = gr.Textbox(label="通知邮箱（必填）", placeholder="your@email.com", type="email")
        start_button = gr.Button("开始检索", variant="primary")
        task_id = gr.Textbox(label="任务 ID")
        status = gr.Markdown(value="输入主题和邮箱后开始任务。")
        with gr.Row():
            refresh_button = gr.Button("刷新状态")
            report_button = gr.DownloadButton("下载报告", visible=False)
            pdf_button = gr.DownloadButton("下载已校验 PDF", visible=False)

    with gr.Tab("历史报告"):
        gr.Markdown("搜索已经提交的任务，查看状态和任务 ID。")
        keyword = gr.Textbox(label="关键词或任务 ID", placeholder="输入研究主题、任务 ID 或状态")
        history_search_button = gr.Button("搜索历史")
        history_results = gr.Markdown()

    controls = [start_button, refresh_button, report_button, pdf_button]
    start_button.click(start, [topic, rounds, email], [status, task_id, *controls])
    refresh_button.click(refresh, task_id, [status, task_id, *controls])
    report_button.click(lambda tid: download_file(tid, "report/download", ".md"), task_id, report_button)
    pdf_button.click(lambda tid: download_file(tid, "files/download", ".zip"), task_id, pdf_button)
    history_search_button.click(search_history, keyword, history_results)


if __name__ == "__main__":
    demo.queue(default_concurrency_limit=2).launch(
        server_name="0.0.0.0",
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7860")),
    )
