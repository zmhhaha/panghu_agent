"""FastAPI service for the literature downloader."""

from __future__ import annotations

import threading
from html import escape
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .config import settings
from .pipeline import LiteraturePipeline


pipeline = LiteraturePipeline()
pipeline.db.interrupt_running_tasks()
app = FastAPI(title="Literature Downloader API", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
_submission_lock = threading.Lock()


class DownloadRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=500)
    search_rounds: int | None = Field(default=None, ge=1, le=10)
    email: str = Field(default="", max_length=200)
    user_id: str = Field(default="", max_length=200)
    providers: list[str] | None = None


class DownloadTriggerRequest(BaseModel):
    """Parameters for the separately-triggered PDF collection stage."""

    email: str = Field(..., min_length=1, max_length=200)


def _run(task_id: str, stage: str) -> None:
    try:
        if stage == "search":
            pipeline.search(task_id)
        elif stage == "collect":
            pipeline.download(task_id)
        _notify_task(pipeline.status(task_id), stage)
    except Exception:
        # Pipeline persists the error; polling clients receive it from status.
        try:
            _notify_task(pipeline.status(task_id), stage)
        except Exception:
            pass


def _is_email(value: str) -> bool:
    local, separator, domain = value.strip().partition("@")
    return bool(separator and local and domain and "." in domain)


def _notify_task(task: dict[str, Any], stage: str) -> None:
    """Send a best-effort notification after a background stage changes state."""
    email = str(task.get("email") or "").strip()
    if not email:
        return

    status = task.get("status")
    task_id = str(task.get("id") or "")
    topic = str(task.get("topic") or "")
    if stage == "search" and status == "ready:download":
        subject = f"文献检索完成：{topic[:40]}"
        message = (
            "<h2>文献检索任务已完成</h2>"
            f"<p><b>主题：</b>{escape(topic)}</p>"
            f"<p><b>任务 ID：</b><code>{escape(task_id)}</code></p>"
            f"<p><a href=\"https://literature-downloader.panghuer.top/literature-download/{escape(task_id)}/report/download\">下载检索报告</a>　"
            f"<a href=\"https://literature-downloader.panghuer.top/literature-download/{escape(task_id)}/doi-list/download\">下载 DOI 列表</a></p>"
            "<p>请在文献下载工具的“下载”标签输入任务 ID，按需触发 PDF 下载。</p>"
        )
    elif stage == "collect" and status == "completed":
        subject = f"文献下载完成：{topic[:40]}"
        message = (
            "<h2>文献下载任务已完成</h2>"
            f"<p><b>主题：</b>{escape(topic)}</p>"
            f"<p><b>任务 ID：</b><code>{escape(task_id)}</code></p>"
            f"<p>请访问 <a href=\"https://literature-downloader.panghuer.top\">文献下载工具</a>，"
            "在“历史报告”中搜索任务 ID，将 ID 填回“新任务”页并点击“刷新状态”，"
            "即可显示最终报告和已校验 PDF 的下载按钮。</p>"
        )
    elif status == "failed":
        subject = f"文献{('检索' if stage == 'search' else '下载')}失败：{topic[:40]}"
        message = (
            f"<h2>文献{('检索' if stage == 'search' else '下载')}任务失败</h2>"
            f"<p><b>主题：</b>{escape(topic)}</p>"
            f"<p><b>任务 ID：</b><code>{escape(task_id)}</code></p>"
            f"<p><b>错误：</b>{escape(str(task.get('error') or '未知错误'))}</p>"
        )
    else:
        return

    try:
        from tools.email_client import send_email

        send_email(to=email, subject=subject, body=message)
    except Exception:
        # Email delivery must never turn a completed download into a failure.
        pass


def _start_thread(task_id: str, stage: str) -> bool:
    """Start a stage once; repeated clicks return the already-running stage."""
    if not pipeline.claim_stage(task_id, stage):
        current = _status_or_404(task_id)
        if current.get("status") == "running" and current.get("phase") == stage:
            return False
        raise HTTPException(status_code=409, detail=f"Task stage was already claimed: {current.get('status')}")
    threading.Thread(target=_run, args=(task_id, stage), daemon=True, name=f"literature-{stage}-{task_id[:8]}").start()
    return True


def _status_or_404(task_id: str) -> dict[str, Any]:
    try:
        return pipeline.status(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Task not found") from exc


@app.get("/literature-health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "db_path": str(settings.db_path),
        "search_rounds": settings.search_rounds,
        "search_limit": settings.search_limit,
        "per_provider": settings.per_provider,
        "max_search_variants": settings.max_search_variants,
        "openalex_api_key_configured": bool(settings.openalex_api_key),
        "download_concurrency": settings.download_concurrency,
        "download_retries": settings.download_retries,
        "download_request_interval_ms": settings.download_request_interval_ms,
        "download_backend": settings.download_backend,
        "scihub_job_namespace": settings.scihub_namespace,
        "scihub_job_timeout": settings.scihub_job_timeout,
        "llm_enabled": settings.llm_enabled,
        "llm_max_candidates": settings.llm_max_candidates,
    }


@app.post("/literature-download")
def create_download_task(request: DownloadRequest) -> dict[str, Any]:
    email = request.email.strip()
    if email and not _is_email(email):
        raise HTTPException(status_code=422, detail="请输入有效的邮箱地址")
    # Task identity, rather than user identity, is the isolation boundary.
    # Multiple tasks from the same user may run independently.
    with _submission_lock:
        rounds = request.search_rounds or settings.search_rounds
        task_id = pipeline.create_task(
            request.topic,
            rounds,
            request.user_id,
            request.providers,
            email,
        )
        _start_thread(task_id, "search")
    return _status_or_404(task_id)


@app.get("/literature-download/{task_id}")
def get_download_task(task_id: str) -> dict[str, Any]:
    return _status_or_404(task_id)


@app.post("/literature-download/{task_id}/download")
def start_download_task(task_id: str, request: DownloadTriggerRequest) -> dict[str, Any]:
    """Trigger the optional PDF collection stage for a completed search."""
    email = request.email.strip()
    if not _is_email(email):
        raise HTTPException(status_code=422, detail="请输入有效的邮箱地址")

    current = _status_or_404(task_id)
    if current.get("status") == "running" and current.get("phase") == "collect":
        return {"ok": True, "action": "download", "task": current}
    if current.get("status") != "ready:download":
        raise HTTPException(
            status_code=409,
            detail=f"Current task status does not allow download: {current.get('status')}",
        )
    # The download stage can be triggered much later than the search stage and
    # may notify a different recipient. Persist the address before the worker
    # starts so completion and failure notifications use this request's email.
    pipeline.db.update_task(task_id, email=email)
    _start_thread(task_id, "collect")
    return {"ok": True, "action": "download", "task": _status_or_404(task_id)}


def _safe_file(path: Path, expected_root: Path) -> Path:
    resolved = path.resolve()
    root = expected_root.resolve()
    if resolved != root and root not in resolved.parents:
        raise HTTPException(status_code=403, detail="Invalid file path")
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return resolved


@app.get("/literature-download/{task_id}/report")
def read_download_report(task_id: str) -> Response:
    try:
        path = _safe_file(pipeline.report_path(task_id), settings.reports_dir)
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(path.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")


@app.get("/literature-reports")
def search_history(
    q: str = Query(default="", description="搜索主题、任务 ID 或状态"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    """Return persisted task history for the UI and email follow-up."""
    query = q if isinstance(q, str) else str(getattr(q, "default", "") or "")
    limit_value = limit if isinstance(limit, int) else int(getattr(limit, "default", 20) or 20)
    offset_value = offset if isinstance(offset, int) else int(getattr(offset, "default", 0) or 0)
    rows = pipeline.db.search_history(query, limit_value, offset_value)
    for row in rows:
        task_id = row["id"]
        # Do not expose absolute filesystem paths from the persistence layer.
        row.pop("reports", None)
        row["report_url"] = f"/literature-download/{task_id}/report/download" if row["report_available"] else ""
        row["doi_url"] = f"/literature-download/{task_id}/doi-list/download" if row.get("doi_available") else ""
        row["pdf_url"] = f"/literature-download/{task_id}/files/download" if row["pdf_available"] else ""
    return rows


@app.get("/literature-download/{task_id}/reports")
def list_task_reports(task_id: str) -> list[dict[str, Any]]:
    _status_or_404(task_id)
    return [
        {
            "id": row["id"],
            "round": row["round"],
            "report_type": row["report_type"],
            "created_at": row["created_at"],
            "download_url": f"/literature-download/{task_id}/reports/{row['id']}/download",
        }
        for row in pipeline.db.list_reports(task_id)
    ]


@app.get("/literature-download/{task_id}/reports/{report_id}/download")
def download_stage_report(task_id: str, report_id: int) -> FileResponse:
    _status_or_404(task_id)
    reports = [row for row in pipeline.db.list_reports(task_id) if int(row["id"]) == report_id]
    if not reports:
        raise HTTPException(status_code=404, detail="Report not found")
    path = _safe_file(Path(reports[0]["path"]), settings.reports_dir)
    return FileResponse(path, media_type="text/markdown", filename=path.name)


@app.get("/literature-download/{task_id}/report/download")
def download_report(task_id: str) -> FileResponse:
    try:
        path = _safe_file(pipeline.report_path(task_id), settings.reports_dir)
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="text/markdown", filename="literature_download_report.md")


@app.get("/literature-download/{task_id}/doi-list/download")
def download_doi_list(task_id: str) -> FileResponse:
    try:
        path = _safe_file(pipeline.doi_list_path(task_id), settings.reports_dir)
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="text/markdown", filename="doi_list.md")


@app.get("/literature-download/{task_id}/files/download")
def download_pdfs(task_id: str) -> FileResponse:
    try:
        path = _safe_file(pipeline.zip_path(task_id), settings.reports_dir)
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="application/zip", filename="verified_pdfs.zip")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("literature_downloader.api:app", host="0.0.0.0", port=8001, reload=False)
