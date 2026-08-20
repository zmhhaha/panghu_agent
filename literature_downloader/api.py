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
    max_rounds: int = Field(default=3, ge=1, le=10)
    email: str = Field(default="", max_length=200)
    user_id: str = Field(default="", max_length=200)
    providers: list[str] | None = None


class ActionRequest(BaseModel):
    action: str = Field(default="approve")


def _run(task_id: str, stage: str) -> None:
    try:
        if stage == "search":
            pipeline.run_all(task_id)
        elif stage == "collect":
            pipeline.collect_round(task_id)
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
    if status == "completed":
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
        subject = f"文献下载失败：{topic[:40]}"
        message = (
            "<h2>文献下载任务失败</h2>"
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


def _require_transition(task_id: str, allowed: set[str]) -> dict[str, Any]:
    status = _status_or_404(task_id)
    if status["status"] not in allowed:
        raise HTTPException(status_code=409, detail=f"Current task status does not allow this action: {status['status']}")
    return status


@app.get("/literature-health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "db_path": str(settings.db_path),
        "max_rounds": settings.max_rounds,
        "search_limit": settings.search_limit,
        "per_provider": settings.per_provider,
        "max_search_variants": settings.max_search_variants,
        "download_concurrency": settings.download_concurrency,
        "llm_enabled": settings.llm_enabled,
        "llm_max_candidates": settings.llm_max_candidates,
    }


@app.post("/literature-download")
def create_download_task(request: DownloadRequest) -> dict[str, Any]:
    email = request.email.strip()
    if email and not _is_email(email):
        raise HTTPException(status_code=422, detail="请输入有效的邮箱地址")
    owner = request.user_id.strip() or email
    with _submission_lock:
        active = pipeline.db.get_active_task(owner)
        if active:
            raise HTTPException(
                status_code=429,
                detail=f"您已有任务正在执行（{str(active['id'])[:8]}），请等待完成后再提交新的任务",
            )
        task_id = pipeline.create_task(
            request.topic,
            request.max_rounds,
            request.user_id,
            request.providers,
            email,
        )
        _start_thread(task_id, "search")
    return _status_or_404(task_id)


@app.get("/literature-download/{task_id}")
def get_download_task(task_id: str) -> dict[str, Any]:
    return _status_or_404(task_id)


@app.post("/literature-download/{task_id}/approve")
def approve_download_task(task_id: str, request: ActionRequest | None = None) -> dict[str, Any]:
    action = (request.action if request else "approve").lower()
    if action == "approve":
        current = _status_or_404(task_id)
        if current.get("status") == "running" and current.get("phase") == "collect":
            return {"ok": True, "action": action, "task": current}
        if current.get("status") != "waiting:search_approval":
            raise HTTPException(
                status_code=409,
                detail=f"Current task status does not allow this action: {current.get('status')}",
            )
        _start_thread(task_id, "collect")
        return {"ok": True, "action": action, "task": _status_or_404(task_id)}
    if action == "retry":
        return retry_download_task(task_id)
    if action in {"finish", "abort"}:
        return finish_download_task(task_id) if action == "finish" else abort_download_task(task_id)
    raise HTTPException(status_code=400, detail="action must be approve, retry, finish, or abort")


@app.post("/literature-download/{task_id}/retry")
def retry_download_task(task_id: str) -> dict[str, Any]:
    current = _status_or_404(task_id)
    if current.get("status") == "running" and current.get("phase") == "collect":
        return {"ok": True, "action": "retry", "task": current}
    if current.get("status") != "waiting:collect_approval":
        raise HTTPException(status_code=409, detail=f"Current task status does not allow this action: {current.get('status')}")
    _start_thread(task_id, "collect")
    return {"ok": True, "action": "retry", "task": _status_or_404(task_id)}


@app.post("/literature-download/{task_id}/finish")
def finish_download_task(task_id: str) -> dict[str, Any]:
    _require_transition(task_id, {"waiting:collect_approval", "waiting:search_approval"})
    try:
        task = pipeline.finalize(task_id)
        _notify_task(task, "finish")
        return {"ok": True, "action": "finish", "task": task}
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/literature-download/{task_id}/abort")
def abort_download_task(task_id: str) -> dict[str, Any]:
    try:
        return {"ok": True, "action": "abort", "task": pipeline.abort(task_id)}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
