"""Persistent three-stage literature collection state machine."""

from __future__ import annotations

import threading
import zipfile
from pathlib import Path
from typing import Any, Callable

from .collector import collect_paper
from .config import Settings, settings
from .db import Database
from .models import Paper
from .reports import (
    format_collection_report,
    format_final_report,
    format_need_to_download,
    format_search_report,
    format_verification_report,
    save_report,
)
from .searcher import search_literature
from .verifier import verify_pdf


ProgressCallback = Callable[[dict[str, Any]], None]


class LiteraturePipeline:
    """Runs short background stages and persists each user checkpoint."""

    def __init__(self, config: Settings = settings, db: Database | None = None):
        self.config = config
        self.config.ensure_dirs()
        self.db = db or Database(config.db_path)
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    def _lock(self, task_id: str) -> threading.RLock:
        with self._locks_guard:
            return self._locks.setdefault(task_id, threading.RLock())

    def _task_dir(self, task_id: str) -> Path:
        path = self.config.reports_dir / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _pdf_dir(self, task_id: str) -> Path:
        path = self.config.pdf_dir / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _emit(self, task_id: str, phase: str, progress: str, callback: ProgressCallback | None = None, **extra: Any) -> None:
        values = {"status": "running", "phase": phase, "progress": progress, **extra}
        self.db.update_task(task_id, **values)
        if callback:
            callback({"id": task_id, **values})

    def create_task(
        self,
        topic: str,
        max_rounds: int | None = None,
        user_id: str = "",
        providers: list[str] | None = None,
        email: str = "",
    ) -> str:
        rounds = min(max(int(max_rounds or self.config.max_rounds), 1), 10)
        task_id = self.db.create_task(topic, rounds, user_id, email)
        if providers:
            self.db.update_task(task_id, search={"providers": providers})
        return task_id

    def claim_stage(self, task_id: str, stage: str) -> bool:
        """Atomically claim a pending stage before starting a worker thread."""
        allowed = ("pending",) if stage == "search" else ("waiting:search_approval", "waiting:collect_approval")
        with self._lock(task_id):
            task = self._require_task(task_id)
            if task["status"] not in allowed:
                return False
            progress = "正在检索文献" if stage == "search" else "正在启动文献收集"
            self.db.update_task(task_id, status="running", phase=stage, progress=progress, error="")
            return True

    def search(self, task_id: str, callback: ProgressCallback | None = None) -> dict[str, Any]:
        with self._lock(task_id):
            task = self._require_task(task_id)
            self._emit(task_id, "search", "正在查询本地文献库和外部学术 API", callback)
            try:
                result = search_literature(
                    task["topic"], self.db,
                    providers=(task.get("search") or {}).get("providers"),
                    config=self.config,
                )
                for row in result["papers"]:
                    if row.get("pdf_path") and row.get("pdf_status") == "verified":
                        row["pdf_status"] = "verified"
                    else:
                        row["pdf_status"] = "pending_download"
                    paper_id = self.db.upsert_paper(task_id, row)
                    row["local_id"] = paper_id

                need_download = [row for row in result["papers"] if row.get("pdf_status") != "verified"]
                task_dir = self._task_dir(task_id)
                search_path = save_report(format_search_report(result), task["topic"], "search", task_dir, "search_report.md")
                list_path = save_report(format_need_to_download(need_download), task["topic"], "need_to_download", task_dir, "need_to_download.md")
                self.db.save_report(task_id, 0, "search", str(search_path), search_path.read_text(encoding="utf-8"))
                self.db.save_report(task_id, 0, "need_to_download", str(list_path), list_path.read_text(encoding="utf-8"))

                search_state = {
                    "total": len(result["papers"]),
                    "local_hits": result["local_hits"],
                    "need_download": len(need_download),
                    "by_provider": result["provider_counts"],
                    "errors": result["errors"],
                    "papers": [self._public_paper(row) for row in result["papers"]],
                }
                reports = {"search": str(search_path), "need_to_download": str(list_path)}
                status = "waiting:search_approval" if need_download else "completed"
                self.db.update_task(
                    task_id,
                    status=status,
                    phase="search" if need_download else "done",
                    progress=(f"检索完成：{len(result['papers'])} 篇，{len(need_download)} 篇待下载" if need_download else "本地文献均已就绪"),
                    search=search_state,
                    reports=reports,
                )
                if not need_download:
                    return self.finalize(task_id)
                return self.status(task_id)
            except Exception as exc:
                self._fail(task_id, exc)
                raise

    def run_all(self, task_id: str, callback: ProgressCallback | None = None) -> dict[str, Any]:
        """Run search, collection rounds, verification, and finalization in order."""
        result = self.search(task_id, callback)
        while result.get("status") in {"waiting:search_approval", "waiting:collect_approval"}:
            result = self.collect_round(task_id, callback)
        return result

    def collect_round(self, task_id: str, callback: ProgressCallback | None = None) -> dict[str, Any]:
        with self._lock(task_id):
            task = self._require_task(task_id)
            ready = task["status"] in {"waiting:search_approval", "waiting:collect_approval"}
            started_by_api = task["status"] == "running" and task["phase"] == "collect"
            if not ready and not started_by_api:
                raise ValueError(f"task status does not allow collection: {task['status']}")
            round_num = int(task["current_round"] or 0) + 1
            if round_num > int(task["max_rounds"]):
                return self.finalize(task_id)
            targets = self.db.list_papers(task_id, ("pending_download", "failed"))
            if not targets:
                return self.finalize(task_id)

            self._emit(task_id, "collect", f"第 {round_num} 轮：准备下载 {len(targets)} 篇文献", callback, current_round=round_num)
            collection_rows: list[dict[str, Any]] = []
            verification_rows: list[dict[str, Any]] = []
            try:
                for index, row in enumerate(targets, 1):
                    paper_id = int(row["id"])
                    self._emit(task_id, "collect", f"第 {round_num} 轮：下载 {index}/{len(targets)} - {row['title'][:60]}", callback, current_round=round_num)
                    self.db.update_paper(paper_id, pdf_status="downloading")
                    paper = Paper.from_record({**row, "local_id": paper_id})
                    downloaded = collect_paper(paper, self._pdf_dir(task_id), self.config)
                    for attempt in downloaded.attempts:
                        self.db.add_attempt(task_id, paper_id, round_num, attempt)
                    item = {"paper_id": paper_id, "title": paper.title, **downloaded.to_dict()}
                    collection_rows.append(item)
                    if not downloaded.ok:
                        self.db.update_paper(paper_id, pdf_status="failed", verification_status="", pdf_path="")
                        continue

                    self.db.update_paper(paper_id, pdf_status="downloaded", pdf_path=downloaded.path)
                    paper.pdf_path = downloaded.path
                    verification = verify_pdf(paper, self.config)
                    verification_row = {"paper_id": paper_id, **verification.to_dict()}
                    verification_rows.append(verification_row)
                    new_status = "verified" if verification.verdict == "pass" else "failed"
                    self.db.update_paper(
                        paper_id,
                        pdf_status=new_status,
                        verification_status=verification.verdict,
                        verification=verification.to_dict(),
                    )

                round_dir = self._task_dir(task_id) / f"collection_round_{round_num}"
                collection_path = save_report(
                    format_collection_report(collection_rows, round_num), task["topic"], "download_collection", round_dir, "collection_report.md"
                )
                verification_path = save_report(
                    format_verification_report(verification_rows, round_num), task["topic"], "verification", round_dir, "verification_report.md"
                )
                self.db.save_report(task_id, round_num, "download_collection", str(collection_path), collection_path.read_text(encoding="utf-8"))
                self.db.save_report(task_id, round_num, "verification", str(verification_path), verification_path.read_text(encoding="utf-8"))

                collection = dict(task.get("collection") or {})
                rounds = list(collection.get("rounds") or [])
                rounds.append({
                    "round": round_num,
                    "attempted": len(collection_rows),
                    "downloaded": sum(1 for row in collection_rows if row["ok"]),
                    "download_failed": sum(1 for row in collection_rows if not row["ok"]),
                    "verified": sum(1 for row in verification_rows if row["verdict"] == "pass"),
                    "uncertain": sum(1 for row in verification_rows if row["verdict"] == "uncertain"),
                    "verification_failed": sum(1 for row in verification_rows if row["verdict"] == "fail"),
                    "collection_report": str(collection_path),
                    "verification_report": str(verification_path),
                    "papers": collection_rows,
                    "verification": verification_rows,
                })
                pending = self.db.list_papers(task_id, ("pending_download", "failed"))
                verified = self.db.list_papers(task_id, ("verified",))
                collection.update({
                    "rounds": rounds,
                    "current_round": round_num,
                    "cumulative_verified": len(verified),
                    "still_pending_count": len(pending),
                })
                reports = dict(task.get("reports") or {})
                reports.update({"current_collection": str(collection_path), "current_verification": str(verification_path)})
                self.db.update_task(task_id, current_round=round_num, collection=collection, reports=reports)
                if not pending or round_num >= int(task["max_rounds"]):
                    return self.finalize(task_id)
                self.db.update_task(
                    task_id,
                    status="waiting:collect_approval",
                    phase="verify",
                    progress=f"第 {round_num} 轮完成：累计通过 {len(verified)} 篇，仍待处理 {len(pending)} 篇",
                )
                return self.status(task_id)
            except Exception as exc:
                self._fail(task_id, exc)
                raise

    def finalize(self, task_id: str) -> dict[str, Any]:
        with self._lock(task_id):
            task = self._require_task(task_id)
            verified = self.db.list_papers(task_id, ("verified",))
            pending = self.db.list_papers(task_id, ("pending_download", "failed", "downloaded", "downloading"))
            collection = dict(task.get("collection") or {})
            final_path = save_report(
                format_final_report(task["topic"], list(collection.get("rounds") or []), verified, pending),
                task["topic"], "final_download", self._task_dir(task_id), "download_report.md",
            )
            zip_path = self.create_zip(task_id, verified)
            self.db.save_report(task_id, int(task["current_round"] or 0), "final_download", str(final_path), final_path.read_text(encoding="utf-8"))
            reports = dict(task.get("reports") or {})
            reports.update({"final": str(final_path), "pdf_zip": str(zip_path)})
            self.db.update_task(
                task_id,
                status="completed",
                phase="done",
                progress=f"收集完成：{len(verified)} 篇通过校验，{len(pending)} 篇未完成",
                reports=reports,
            )
            return self.status(task_id)

    def abort(self, task_id: str) -> dict[str, Any]:
        task = self._require_task(task_id)
        if task["status"] == "running":
            raise ValueError("running stage cannot be interrupted safely")
        self.db.update_task(task_id, status="aborted", phase="done", progress="任务已由用户中止")
        return self.status(task_id)

    def create_zip(self, task_id: str, verified: list[dict[str, Any]] | None = None) -> Path:
        papers = verified if verified is not None else self.db.list_papers(task_id, ("verified",))
        zip_path = self._task_dir(task_id) / "verified_pdfs.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for paper in papers:
                path = Path(str(paper.get("pdf_path") or ""))
                if path.exists() and path.is_file():
                    archive.write(path, arcname=path.name)
        return zip_path

    def status(self, task_id: str) -> dict[str, Any]:
        task = self._require_task(task_id)
        task["papers"] = [self._public_paper(row) for row in self.db.list_papers(task_id)]
        return task

    def report_path(self, task_id: str) -> Path:
        task = self._require_task(task_id)
        path = Path(str((task.get("reports") or {}).get("final") or ""))
        if not path.exists():
            raise FileNotFoundError("final report is not available")
        return path

    def zip_path(self, task_id: str) -> Path:
        task = self._require_task(task_id)
        path = Path(str((task.get("reports") or {}).get("pdf_zip") or ""))
        if not path.exists():
            raise FileNotFoundError("PDF archive is not available")
        return path

    def _require_task(self, task_id: str) -> dict[str, Any]:
        task = self.db.get_task(task_id)
        if not task:
            raise KeyError(f"task not found: {task_id}")
        return task

    def _fail(self, task_id: str, exc: Exception) -> None:
        self.db.update_task(task_id, status="failed", phase="error", progress="任务执行失败", error=f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _public_paper(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row.get("id") or row.get("local_id"),
            "title": row.get("title", ""),
            "authors": row.get("authors", ""),
            "date": row.get("date", ""),
            "doi": row.get("doi", ""),
            "arxiv_id": row.get("arxiv_id") or (row.get("identifiers") or {}).get("arxiv", ""),
            "provider": row.get("provider", ""),
            "providers": row.get("providers", []),
            "url": row.get("url", ""),
            "pdf_status": row.get("pdf_status", "pending_download"),
            "verification_status": row.get("verification_status", ""),
            "pdf_path": row.get("pdf_path", ""),
            "relevance_score": row.get("relevance_score", 0),
        }
