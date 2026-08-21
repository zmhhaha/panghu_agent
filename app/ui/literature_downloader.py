"""Gradio UI for separated literature search and optional PDF download."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator

import gradio as gr
import requests


API_BASE = os.getenv("API_BASE") or os.getenv("LITERATURE_API_BASE", "http://127.0.0.1:8001")
MAX_WAIT = 1200
POLL_SECONDS = 5
SEARCH_DONE = {"ready:download", "failed"}
DOWNLOAD_DONE = {"completed", "failed"}


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
    status = str(task.get("status") or "")
    phase = str(task.get("phase") or "")
    lines = [
        f"## {task.get('topic', '')}",
        f"状态：`{status}`",
        f"阶段：`{phase}`",
        task.get("progress") or "等待后台开始执行",
        f"任务 ID：`{task.get('id', '')}`",
    ]
    if elapsed:
        lines.append(f"已等待：{elapsed} 秒")

    search = task.get("search") or {}
    if search:
        lines.extend([
            "",
            f"检索结果：{search.get('total', 0)} 篇；待下载 {search.get('need_download', 0)} 篇；"
            f"本地命中 {search.get('local_hits', 0)} 篇；检索轮数 {search.get('search_round_count', 0)}",
            f"DOI 数量：{search.get('doi_count', 0)}",
        ])
    collection = task.get("collection") or {}
    if collection:
        lines.extend([
            "下载阶段：单次收集与校验",
            f"通过校验：{collection.get('cumulative_verified', 0)}",
            f"仍待处理：{collection.get('still_pending_count', 0)}",
        ])
    if status == "ready:download":
        lines.append("检索已完成。请复制任务 ID 到“下载”标签，按需开始 PDF 下载和校验。")
    elif status == "completed":
        lines.append("下载和校验流程已完成，可以下载最终报告和已校验 PDF。")
    elif status == "failed":
        lines.extend(["", f"错误：{task.get('error') or '未知错误'}"])

    papers = task.get("papers") or []
    if papers:
        lines.extend(["", "| 文献 | PDF 状态 | 校验 |", "|---|---|---|"])
        lines.extend(
            f"| {paper.get('title', '')[:90]} | {paper.get('pdf_status', '')} | "
            f"{paper.get('verification_status', '') or '-'} |"
            for paper in papers
        )
    return "\n".join(lines)


def _search_updates(task: dict[str, Any] | None, running: bool = False) -> tuple[Any, ...]:
    task = task or {}
    reports = task.get("reports") or {}
    ready = bool(task.get("id"))
    return (
        gr.update(interactive=not running),
        gr.update(interactive=ready),
        gr.update(visible=bool(reports.get("search") or reports.get("final"))),
        gr.update(visible=bool(reports.get("doi_list"))),
    )


def _download_updates(task: dict[str, Any] | None, running: bool = False) -> tuple[Any, ...]:
    task = task or {}
    reports = task.get("reports") or {}
    return (
        gr.update(interactive=not running),
        gr.update(interactive=bool(task.get("id"))),
        gr.update(visible=bool(reports.get("final"))),
        gr.update(visible=bool(reports.get("pdf_zip"))),
    )


def _watch_search(task_id: str) -> Iterator[tuple[Any, ...]]:
    elapsed = 0
    for _ in range(MAX_WAIT // POLL_SECONDS):
        time.sleep(POLL_SECONDS)
        elapsed += POLL_SECONDS
        try:
            task = _request("GET", f"/literature-download/{task_id}")
        except requests.RequestException as exc:
            yield (f"状态查询失败：{exc}\n\n任务仍在后台运行，请稍后点击刷新状态。", task_id, *_search_updates({"id": task_id}, True))
            return
        if task.get("status") in SEARCH_DONE:
            yield (_render(task, elapsed), task_id, *_search_updates(task, False))
            return
        yield (_render(task, elapsed), task_id, *_search_updates(task, True))
    task = _request("GET", f"/literature-download/{task_id}")
    yield (f"{_render(task, MAX_WAIT)}\n\n任务仍在后台运行，完成后会发送邮件通知。", task_id, *_search_updates(task, False))


def _watch_download(task_id: str) -> Iterator[tuple[Any, ...]]:
    elapsed = 0
    for _ in range(MAX_WAIT // POLL_SECONDS):
        time.sleep(POLL_SECONDS)
        elapsed += POLL_SECONDS
        try:
            task = _request("GET", f"/literature-download/{task_id}")
        except requests.RequestException as exc:
            yield (f"状态查询失败：{exc}\n\n下载仍在后台运行，请稍后点击刷新状态。", *_download_updates({"id": task_id}, True))
            return
        if task.get("status") in DOWNLOAD_DONE:
            yield (_render(task, elapsed), *_download_updates(task, False))
            return
        yield (_render(task, elapsed), *_download_updates(task, True))
    task = _request("GET", f"/literature-download/{task_id}")
    yield (f"{_render(task, MAX_WAIT)}\n\n下载仍在后台运行，完成后会发送邮件通知。", *_download_updates(task, False))


def start_search(topic: str, search_rounds: int, email: str, request: gr.Request) -> Iterator[tuple[Any, ...]]:
    topic = (topic or "").strip()
    email = (email or "").strip()
    if not topic:
        yield ("请输入研究主题", "", *_search_updates({}, False))
        return
    if not email or "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        yield ("请输入有效的通知邮箱，检索完成后会发送任务 ID", "", *_search_updates({}, False))
        return
    user_id = request.headers.get("X-Forwarded-User", "") if request else ""
    try:
        response = requests.post(
            f"{API_BASE}/literature-download",
            json={"topic": topic, "search_rounds": int(search_rounds), "email": email, "user_id": user_id},
            timeout=10,
        )
    except requests.RequestException as exc:
        yield (f"提交失败：{exc}", "", *_search_updates({}, False))
        return
    if not response.ok:
        yield (f"提交失败：{_error(response)}", "", *_search_updates({}, False))
        return
    task = response.json()
    task_id = task["id"]
    yield (
        f"检索任务已提交，完成后可到“下载”标签输入任务 ID。\n\n任务 ID：`{task_id}`\n\n检索完成后会发送邮件通知。",
        task_id,
        *_search_updates(task, True),
    )
    yield from _watch_search(task_id)


def refresh_search(task_id: str) -> tuple[Any, ...]:
    if not task_id:
        return "请先开始检索", "", *_search_updates({}, False)
    try:
        task = _request("GET", f"/literature-download/{task_id}")
        return _render(task), task_id, *_search_updates(task, False)
    except requests.RequestException as exc:
        return f"状态查询失败：{exc}", task_id, *_search_updates({"id": task_id}, False)


def start_download(task_id: str, email: str) -> Iterator[tuple[Any, ...]]:
    task_id = (task_id or "").strip()
    email = (email or "").strip()
    if not task_id:
        yield ("请输入检索任务 ID", *_download_updates({}, False))
        return
    if not email or "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        yield ("请输入有效的通知邮箱，下载完成或失败后会发送任务通知", *_download_updates({}, False))
        return
    try:
        response = requests.post(
            f"{API_BASE}/literature-download/{task_id}/download",
            json={"email": email},
            timeout=10,
        )
    except requests.RequestException as exc:
        yield (f"提交下载失败：{exc}", *_download_updates({"id": task_id}, False))
        return
    if not response.ok:
        yield (f"提交下载失败：{_error(response)}", *_download_updates({"id": task_id}, False))
        return
    task = response.json().get("task") or {}
    yield (f"下载任务已提交。任务 ID：`{task_id}`", *_download_updates(task, True))
    yield from _watch_download(task_id)


def refresh_download(task_id: str) -> tuple[Any, ...]:
    if not task_id:
        return "请输入任务 ID", *_download_updates({}, False)
    try:
        task = _request("GET", f"/literature-download/{task_id}")
        return _render(task), *_download_updates(task, False)
    except requests.RequestException as exc:
        return f"状态查询失败：{exc}", *_download_updates({"id": task_id}, False)


def download_file(task_id: str, endpoint: str, suffix: str) -> str:
    task_id = (task_id or "").strip()
    if not task_id:
        raise gr.Error("请输入任务 ID")
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
    lines = ["| 时间 | 主题 | 状态 | 任务 ID | 报告 | DOI | PDF |", "|---|---|---|---|---|---|---|"]
    for row in rows:
        lines.append(
            f"| {(row.get('created_at') or '')[:16]} | {(row.get('topic') or '')[:50]} | "
            f"{row.get('status', '')} | `{row.get('id', '')}` | "
            f"{'可用' if row.get('report_available') else '-'} | "
            f"{'可用' if row.get('doi_available') else '-'} | "
            f"{'可用' if row.get('pdf_available') else '-'} |"
        )
    lines.append("\n将任务 ID 复制到“下载”标签，即可继续下载或查看文件。")
    return "\n".join(lines)


with gr.Blocks(title="文献检索与下载工具") as demo:
    gr.Markdown("# 文献检索与下载工具\n检索和 PDF 下载分开执行，检索完成后可按需使用任务 ID 触发下载。")

    with gr.Tab("文献检索"):
        topic = gr.Textbox(label="研究主题", lines=2, placeholder="例如：InP 的干法刻蚀研究进展、目前难点及发展方向")
        rounds = gr.Slider(1, 10, value=3, step=1, label="检索轮数")
        email = gr.Textbox(label="通知邮箱（必填）", placeholder="your@email.com", type="email")
        start_button = gr.Button("开始检索", variant="primary")
        search_task_id = gr.Textbox(label="任务 ID")
        search_status = gr.Markdown(value="输入主题和邮箱后开始检索。")
        with gr.Row():
            refresh_button = gr.Button("刷新检索状态")
            search_report_button = gr.DownloadButton("下载检索报告", visible=False)
            doi_button = gr.DownloadButton("下载 DOI 列表", visible=False)

    with gr.Tab("下载文献"):
        download_task_id = gr.Textbox(label="检索任务 ID", placeholder="粘贴检索完成后得到的任务 ID")
        download_email = gr.Textbox(
            label="通知邮箱（必填）",
            placeholder="your@email.com",
            type="email",
        )
        download_button = gr.Button("开始下载", variant="primary")
        download_status = gr.Markdown(value="请输入任务 ID 和通知邮箱后开始下载。下载完成或失败后会发送邮件通知。")
        with gr.Row():
            refresh_download_button = gr.Button("刷新下载状态")
            final_report_button = gr.DownloadButton("下载最终报告", visible=False)
            pdf_button = gr.DownloadButton("下载已校验 PDF", visible=False)

    with gr.Tab("历史任务"):
        keyword = gr.Textbox(label="主题、任务 ID 或状态", placeholder="输入检索主题、任务 ID 或状态")
        history_search_button = gr.Button("查询历史")
        history_results = gr.Markdown()

    search_controls = [start_button, refresh_button, search_report_button, doi_button]
    start_button.click(start_search, [topic, rounds, email], [search_status, search_task_id, *search_controls])
    refresh_button.click(refresh_search, search_task_id, [search_status, search_task_id, *search_controls])
    search_report_button.click(lambda tid: download_file(tid, "report/download", ".md"), search_task_id, search_report_button)
    doi_button.click(lambda tid: download_file(tid, "doi-list/download", ".md"), search_task_id, doi_button)

    download_controls = [download_button, refresh_download_button, final_report_button, pdf_button]
    download_button.click(start_download, [download_task_id, download_email], [download_status, *download_controls])
    refresh_download_button.click(refresh_download, download_task_id, [download_status, *download_controls])
    final_report_button.click(lambda tid: download_file(tid, "report/download", ".md"), download_task_id, final_report_button)
    pdf_button.click(lambda tid: download_file(tid, "files/download", ".zip"), download_task_id, pdf_button)
    history_search_button.click(search_history, keyword, history_results)


if __name__ == "__main__":
    demo.queue(default_concurrency_limit=2).launch(
        server_name="0.0.0.0",
        server_port=int(os.getenv("GRADIO_SERVER_PORT", "7860")),
    )
