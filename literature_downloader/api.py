"""FastAPI service for the literature downloader."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .config import settings
from .pipeline import LiteraturePipeline


pipeline = LiteraturePipeline()
pipeline.db.interrupt_running_tasks()
app = FastAPI(title="Literature Downloader API", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class DownloadRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=500)
    max_rounds: int = Field(default=3, ge=1, le=10)
    user_id: str = Field(default="", max_length=200)
    providers: list[str] | None = None


class ActionRequest(BaseModel):
    action: str = Field(default="approve")


def _run(task_id: str, stage: str) -> None:
    try:
        if stage == "search":
            pipeline.search(task_id)
        elif stage == "collect":
            pipeline.collect_round(task_id)
    except Exception:
        # Pipeline persists the error; polling clients receive it from status.
        pass


def _start_thread(task_id: str, stage: str) -> None:
    if not pipeline.claim_stage(task_id, stage):
        raise HTTPException(status_code=409, detail="Task stage was already claimed")
    threading.Thread(target=_run, args=(task_id, stage), daemon=True, name=f"literature-{stage}-{task_id[:8]}").start()


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
    return {"status": "ok", "db_path": str(settings.db_path), "max_rounds": settings.max_rounds}


@app.post("/literature-download")
def create_download_task(request: DownloadRequest) -> dict[str, Any]:
    task_id = pipeline.create_task(request.topic, request.max_rounds, request.user_id, request.providers)
    _start_thread(task_id, "search")
    return {"id": task_id, "status": "running", "phase": "search", "topic": request.topic}


@app.get("/literature-download/{task_id}")
def get_download_task(task_id: str) -> dict[str, Any]:
    return _status_or_404(task_id)


@app.post("/literature-download/{task_id}/approve")
def approve_download_task(task_id: str, request: ActionRequest | None = None) -> dict[str, Any]:
    action = (request.action if request else "approve").lower()
    if action == "approve":
        _require_transition(task_id, {"waiting:search_approval"})
        _start_thread(task_id, "collect")
        return {"ok": True, "action": action, "task": _status_or_404(task_id)}
    if action == "retry":
        return retry_download_task(task_id)
    if action in {"finish", "abort"}:
        return finish_download_task(task_id) if action == "finish" else abort_download_task(task_id)
    raise HTTPException(status_code=400, detail="action must be approve, retry, finish, or abort")


@app.post("/literature-download/{task_id}/retry")
def retry_download_task(task_id: str) -> dict[str, Any]:
    _require_transition(task_id, {"waiting:collect_approval"})
    _start_thread(task_id, "collect")
    return {"ok": True, "action": "retry", "task": _status_or_404(task_id)}


@app.post("/literature-download/{task_id}/finish")
def finish_download_task(task_id: str) -> dict[str, Any]:
    _require_transition(task_id, {"waiting:collect_approval", "waiting:search_approval"})
    try:
        return {"ok": True, "action": "finish", "task": pipeline.finalize(task_id)}
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
