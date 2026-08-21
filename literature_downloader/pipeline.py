"""Persistent three-stage literature collection state machine."""

from __future__ import annotations

import json
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from .collector import collect_paper
from .config import Settings, settings
from .db import Database, identity_key
from .models import Paper
from .reports import (
    format_collection_report,
    format_doi_list,
    format_final_report,
    format_need_to_download,
    format_search_report,
    format_verification_report,
    save_report,
)
from .scihub_job import KubernetesJobClient, build_input_config_map, build_job
from .searcher import merge_search_results, search_literature
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

    def _cached_library_paper(self, row: dict[str, Any]) -> dict[str, Any] | None:
        """Return a reusable global-library row only while its PDF exists."""
        cached = self.db.get_library_paper(identity_key(row))
        if not cached or cached.get("pdf_status") != "verified":
            return None
        path = Path(str(cached.get("pdf_path") or ""))
        if not path.is_file():
            return None
        return cached

    @staticmethod
    def _cached_verification(row: dict[str, Any], cached: dict[str, Any], path: Path) -> dict[str, Any]:
        verification = dict(cached.get("verification") or {})
        verification.setdefault("title", row.get("title", ""))
        verification.setdefault("verdict", "pass")
        verification.setdefault("path", str(path))
        verification.setdefault("size", path.stat().st_size)
        return verification

    def _emit(self, task_id: str, phase: str, progress: str, callback: ProgressCallback | None = None, **extra: Any) -> None:
        values = {"status": "running", "phase": phase, "progress": progress, **extra}
        self.db.update_task(task_id, **values)
        if callback:
            callback({"id": task_id, **values})

    def create_task(
        self,
        topic: str,
        search_rounds: int | None = None,
        user_id: str = "",
        providers: list[str] | None = None,
        email: str = "",
    ) -> str:
        rounds = min(max(int(search_rounds or self.config.search_rounds), 1), 10)
        task_id = self.db.create_task(topic, rounds, user_id, email)
        if providers:
            self.db.update_task(task_id, search={"providers": providers})
        return task_id

    def claim_stage(self, task_id: str, stage: str) -> bool:
        """Atomically claim a pending stage before starting a worker thread."""
        allowed = ("pending",) if stage == "search" else ("ready:download",)
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
                search_rounds = min(max(int(task.get("search_rounds") or self.config.search_rounds), 1), 10)
                round_results: list[dict[str, Any]] = []
                plan: dict[str, Any] | None = None
                provider_cooldowns: dict[str, int] = {}
                for round_index in range(search_rounds):
                    self._emit(
                        task_id,
                        "search",
                        f"正在进行第 {round_index + 1}/{search_rounds} 轮检索",
                        callback,
                    )
                    current = search_literature(
                        task["topic"],
                        self.db,
                        providers=(task.get("search") or {}).get("providers"),
                        search_round=round_index,
                        search_plan=plan,
                        use_llm=round_index == 0,
                        config=self.config,
                        provider_cooldowns=provider_cooldowns,
                    )
                    plan = current.get("search_plan") or plan
                    round_results.append(current)
                result = merge_search_results(
                    round_results,
                    limit=max(int(self.config.search_limit), 0),
                    topic=task["topic"],
                )
                for row in result["papers"]:
                    if (
                        row.get("pdf_path")
                        and row.get("pdf_status") == "verified"
                        and Path(str(row["pdf_path"])).is_file()
                    ):
                        row["pdf_status"] = "verified"
                    else:
                        row["pdf_status"] = "pending_download"
                        row["pdf_path"] = ""
                    paper_id = self.db.upsert_paper(task_id, row)
                    row["local_id"] = paper_id

                need_download = [row for row in result["papers"] if row.get("pdf_status") != "verified"]
                task_dir = self._task_dir(task_id)
                search_path = save_report(format_search_report(result), task["topic"], "search", task_dir, "search_report.md")
                list_path = save_report(format_need_to_download(need_download), task["topic"], "need_to_download", task_dir, "need_to_download.md")
                doi_path = save_report(format_doi_list(result), task["topic"], "doi_list", task_dir, "doi_list.md")
                self.db.save_report(task_id, 0, "search", str(search_path), search_path.read_text(encoding="utf-8"))
                self.db.save_report(task_id, 0, "need_to_download", str(list_path), list_path.read_text(encoding="utf-8"))
                self.db.save_report(task_id, 0, "doi_list", str(doi_path), doi_path.read_text(encoding="utf-8"))

                search_state = {
                    "total": len(result["papers"]),
                    "local_hits": result["local_hits"],
                    "need_download": len(need_download),
                    "query_variants": result.get("query_variants", []),
                    "search_plan": result.get("search_plan", {}),
                    "relevance": result.get("relevance", {}),
                    "by_provider": result["provider_counts"],
                    "errors": result["errors"],
                    "search_rounds": result.get("search_rounds") or [],
                    "search_round_count": search_rounds,
                    "doi_count": sum(1 for row in result["papers"] if str(row.get("doi") or "").strip()),
                    # Keep provider-level records in the task snapshot so
                    # the final report can explain every hit, including
                    # papers found in the shared local library.
                    "local_results": [self._public_paper(row) for row in result.get("local_results", [])],
                    "api_results": {
                        provider: [self._public_paper(row) for row in rows]
                        for provider, rows in (result.get("api_results") or {}).items()
                    },
                    "provider_queries": result.get("provider_queries") or {},
                    "papers": [self._public_paper(row) for row in result["papers"]],
                }
                reports = {
                    "search": str(search_path),
                    "need_to_download": str(list_path),
                    "doi_list": str(doi_path),
                }
                self.db.update_task(
                    task_id,
                    status="ready:download",
                    phase="search",
                    progress=f"检索完成：{len(result['papers'])} 篇，{len(need_download)} 篇可下载",
                    search=search_state,
                    reports=reports,
                )
                return self.status(task_id)
            except Exception as exc:
                self._fail(task_id, exc)
                raise

    def _collect_one(
        self,
        task_id: str,
        round_num: int,
        row: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Download and verify one paper without holding the task lock."""
        paper_id = int(row["id"])
        paper = Paper.from_record({**row, "local_id": paper_id})
        try:
            cached = self._cached_library_paper(row)
            if cached:
                path = Path(str(cached["pdf_path"]))
                verification = self._cached_verification(row, cached, path)
                attempt = {
                    "source": "local-library",
                    "url": "",
                    "ok": True,
                    "size": path.stat().st_size,
                    "error": "",
                }
                self.db.add_attempt(task_id, paper_id, round_num, attempt)
                self.db.update_paper(
                    paper_id,
                    pdf_status="verified",
                    pdf_path=str(path),
                    verification_status="pass",
                    verification=verification,
                )
                verification_row = {
                    "paper_id": paper_id,
                    **verification,
                    "title": paper.title,
                    "authors": paper.authors,
                    "date": paper.date,
                    "doi": paper.doi,
                    "arxiv_id": paper.arxiv_id,
                    "provider": paper.provider,
                    "providers": paper.providers,
                    "venue": paper.venue,
                    "url": paper.url,
                    "pdf_url": paper.pdf_url,
                    "identifiers": paper.identifiers,
                    "relevance_score": paper.relevance_score,
                    "relevance_method": paper.relevance_method,
                    "llm_included": paper.llm_included,
                    "llm_relevance_score": paper.llm_relevance_score,
                    "relevance_reason": paper.relevance_reason,
                    "source_record": paper.source_record,
                    "download_source": "local-library",
                    "download_url": "",
                }
                return {
                    "paper_id": paper_id,
                    "title": paper.title,
                    "authors": paper.authors,
                    "date": paper.date,
                    "doi": paper.doi,
                    "arxiv_id": paper.arxiv_id,
                    "provider": paper.provider,
                    "providers": paper.providers,
                    "venue": paper.venue,
                    "url": paper.url,
                    "pdf_url": paper.pdf_url,
                    "identifiers": paper.identifiers,
                    "relevance_score": paper.relevance_score,
                    "relevance_method": paper.relevance_method,
                    "llm_included": paper.llm_included,
                    "llm_relevance_score": paper.llm_relevance_score,
                    "relevance_reason": paper.relevance_reason,
                    "source_record": paper.source_record,
                    "ok": True,
                    "path": str(path),
                    "size": path.stat().st_size,
                    "source": "local-library",
                    "error": "",
                    "attempts": [attempt],
                }, verification_row

            downloaded = collect_paper(paper, self._pdf_dir(task_id), self.config)
            for attempt in downloaded.attempts:
                self.db.add_attempt(task_id, paper_id, round_num, attempt)
            item = {
                "paper_id": paper_id,
                "title": paper.title,
                "authors": paper.authors,
                "date": paper.date,
                "doi": paper.doi,
                "arxiv_id": paper.arxiv_id,
                "provider": paper.provider,
                "providers": paper.providers,
                "venue": paper.venue,
                "url": paper.url,
                "pdf_url": paper.pdf_url,
                "identifiers": paper.identifiers,
                "relevance_score": paper.relevance_score,
                "relevance_method": paper.relevance_method,
                "llm_included": paper.llm_included,
                "llm_relevance_score": paper.llm_relevance_score,
                "relevance_reason": paper.relevance_reason,
                "source_record": paper.source_record,
                **downloaded.to_dict(),
            }
            if not downloaded.ok:
                self.db.update_paper(paper_id, pdf_status="failed", verification_status="", pdf_path="")
                return item, None

            self.db.update_paper(paper_id, pdf_status="downloaded", pdf_path=downloaded.path)
            paper.pdf_path = downloaded.path
            verification = verify_pdf(paper, self.config)
            successful_attempt = next(
                (attempt for attempt in downloaded.attempts if attempt.get("ok")),
                {},
            )
            verification_row = {
                "paper_id": paper_id,
                "authors": paper.authors,
                "date": paper.date,
                "doi": paper.doi,
                "arxiv_id": paper.arxiv_id,
                "provider": paper.provider,
                "providers": paper.providers,
                "venue": paper.venue,
                "url": paper.url,
                "pdf_url": paper.pdf_url,
                "identifiers": paper.identifiers,
                "relevance_score": paper.relevance_score,
                "relevance_method": paper.relevance_method,
                "llm_included": paper.llm_included,
                "llm_relevance_score": paper.llm_relevance_score,
                "relevance_reason": paper.relevance_reason,
                "source_record": paper.source_record,
                "download_source": downloaded.source,
                "download_url": successful_attempt.get("url", ""),
                **verification.to_dict(),
            }
            new_status = "verified" if verification.verdict == "pass" else "failed"
            self.db.update_paper(
                paper_id,
                pdf_status=new_status,
                verification_status=verification.verdict,
                verification=verification.to_dict(),
            )
            if verification.verdict == "pass":
                self.db.upsert_library_paper({
                    **paper.to_dict(),
                    "pdf_path": downloaded.path,
                    "pdf_status": "verified",
                    "verification_status": verification.verdict,
                    "verification": verification.to_dict(),
                })
            return item, verification_row
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.db.update_paper(paper_id, pdf_status="failed", verification_status="", pdf_path="")
            return {
                "paper_id": paper_id,
                "title": paper.title,
                "authors": paper.authors,
                "date": paper.date,
                "doi": paper.doi,
                "arxiv_id": paper.arxiv_id,
                "provider": paper.provider,
                "providers": paper.providers,
                "venue": paper.venue,
                "url": paper.url,
                "pdf_url": paper.pdf_url,
                "identifiers": paper.identifiers,
                "relevance_score": paper.relevance_score,
                "relevance_method": paper.relevance_method,
                "llm_included": paper.llm_included,
                "llm_relevance_score": paper.llm_relevance_score,
                "relevance_reason": paper.relevance_reason,
                "source_record": paper.source_record,
                "ok": False,
                "path": "",
                "size": 0,
                "source": "",
                "error": error,
                "attempts": [],
            }, None

    @staticmethod
    def _scihub_identifier(row: dict[str, Any]) -> str:
        identifiers = row.get("identifiers") or {}
        for value in (
            row.get("doi"),
            row.get("arxiv_id"),
            identifiers.get("doi"),
            identifiers.get("arxiv"),
            row.get("pdf_url"),
            row.get("url"),
        ):
            value = str(value or "").replace("\r", " ").replace("\n", " ").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _scihub_item(
        row: dict[str, Any],
        *,
        ok: bool,
        path: str = "",
        size: int = 0,
        source: str = "",
        error: str = "",
        attempts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "paper_id": int(row["id"]),
            "title": row.get("title", ""),
            "authors": row.get("authors", ""),
            "date": row.get("date", ""),
            "doi": row.get("doi", ""),
            "arxiv_id": row.get("arxiv_id", ""),
            "provider": row.get("provider", ""),
            "providers": row.get("providers", []),
            "venue": row.get("venue", ""),
            "url": row.get("url", ""),
            "pdf_url": row.get("pdf_url", ""),
            "identifiers": row.get("identifiers", {}),
            "relevance_score": row.get("relevance_score", 0),
            "relevance_method": row.get("relevance_method", "rules"),
            "llm_included": row.get("llm_included"),
            "llm_relevance_score": row.get("llm_relevance_score"),
            "relevance_reason": row.get("relevance_reason", ""),
            "source_record": row.get("source_record", {}),
            "ok": ok,
            "path": path,
            "size": size,
            "source": source,
            "error": error,
            "attempts": attempts or [],
        }

    @staticmethod
    def _scihub_failure(output_dir: Path) -> str:
        report_path = output_dir / "download-report.json"
        if not report_path.is_file():
            return "SciHub CLI did not produce a PDF"
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            results = report.get("results") or report.get("download_failures") or []
            if results:
                return str(results[0].get("error") or "SciHub CLI download failed")
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
        return "SciHub CLI download failed"

    def _collect_scihub(
        self,
        task_id: str,
        round_num: int,
        targets: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        identifiers: dict[int, str] = {}
        collection_rows: list[dict[str, Any]] = []
        verification_rows: list[dict[str, Any]] = []
        for row in targets:
            paper_id = int(row["id"])
            cached = self._cached_library_paper(row)
            if cached:
                path = Path(str(cached["pdf_path"]))
                verification = self._cached_verification(row, cached, path)
                self.db.update_paper(
                    paper_id,
                    pdf_status="verified",
                    pdf_path=str(path),
                    verification_status="pass",
                    verification=verification,
                )
                self.db.add_attempt(
                    task_id,
                    paper_id,
                    round_num,
                    {
                        "source": "local-library",
                        "url": "",
                        "ok": True,
                        "size": path.stat().st_size,
                    },
                )
                collection_rows.append(
                    self._scihub_item(
                        row,
                        ok=True,
                        path=str(path),
                        size=path.stat().st_size,
                        source="local-library",
                        attempts=[
                            {
                                "source": "local-library",
                                "url": "",
                                "ok": True,
                                "size": path.stat().st_size,
                            }
                        ],
                    )
                )
                verification_rows.append({
                    "paper_id": paper_id,
                    **verification,
                    "title": row.get("title", ""),
                    "authors": row.get("authors", ""),
                    "date": row.get("date", ""),
                    "doi": row.get("doi", ""),
                    "arxiv_id": row.get("arxiv_id", ""),
                    "provider": row.get("provider", ""),
                    "providers": row.get("providers", []),
                    "venue": row.get("venue", ""),
                    "url": row.get("url", ""),
                    "pdf_url": row.get("pdf_url", ""),
                    "identifiers": row.get("identifiers", {}),
                    "relevance_score": row.get("relevance_score", 0),
                    "relevance_method": row.get("relevance_method", "rules"),
                    "relevance_reason": row.get("relevance_reason", ""),
                    "source_record": row.get("source_record", {}),
                    "download_source": "local-library",
                    "download_url": "",
                })
                continue
            identifier = self._scihub_identifier(row)
            if identifier:
                identifiers[paper_id] = identifier
            else:
                error = "文献没有 DOI、arXiv ID 或可下载 URL"
                self.db.update_paper(paper_id, pdf_status="failed", verification_status="", pdf_path="")
                attempt = {"source": "scihub-cli", "ok": False, "error": error}
                self.db.add_attempt(task_id, paper_id, round_num, attempt)
                collection_rows.append(self._scihub_item(row, ok=False, error=error, attempts=[attempt]))

        if not identifiers:
            return collection_rows, verification_rows

        namespace = self.config.scihub_namespace
        job_name = f"scihub-download-{task_id[:12]}-r{round_num}"
        input_name = f"scihub-input-{task_id[:12]}-r{round_num}"
        job = build_job(
            namespace=namespace,
            name=job_name,
            input_config_map=input_name,
            image=self.config.scihub_job_image,
            pvc_name=self.config.scihub_pvc_name,
            task_id=task_id,
            round_num=round_num,
            request_timeout=self.config.download_timeout,
            job_timeout_seconds=self.config.scihub_job_timeout,
            retries=self.config.scihub_retries,
            email=self.config.contact_email,
        )
        client = KubernetesJobClient()
        try:
            client.create_config_map(namespace, build_input_config_map(namespace, input_name, identifiers))
            client.create_job(namespace, job)
            job_status = client.wait_for_job(
                namespace,
                job_name,
                self.config.scihub_job_timeout,
                self.config.scihub_job_poll_interval,
            )
        finally:
            try:
                client.delete_config_map(namespace, input_name)
            except Exception:
                pass

        job_failed = int((job_status.get("status") or {}).get("failed") or 0) > 0
        root = self.config.scihub_papers_dir / "jobs" / task_id / f"round-{round_num}"
        for row in targets:
            paper_id = int(row["id"])
            if paper_id not in identifiers:
                continue
            output_dir = root / f"paper-{paper_id}"
            pdfs = sorted(path for path in output_dir.rglob("*.pdf") if path.is_file()) if output_dir.is_dir() else []
            if not pdfs:
                error = self._scihub_failure(output_dir)
                if job_failed and error == "SciHub CLI did not produce a PDF":
                    error = f"{error}; SciHub Job failed before producing output"
                attempt = {
                    "source": "scihub-cli",
                    "url": identifiers[paper_id],
                    "ok": False,
                    "error": error,
                }
                self.db.update_paper(paper_id, pdf_status="failed", verification_status="", pdf_path="")
                self.db.add_attempt(task_id, paper_id, round_num, attempt)
                collection_rows.append(self._scihub_item(row, ok=False, source="scihub-cli", error=error, attempts=[attempt]))
                continue

            path = pdfs[0]
            paper = Paper.from_record({**row, "pdf_path": str(path)})
            verification = verify_pdf(paper, self.config)
            attempt = {
                "source": "scihub-cli",
                "url": identifiers[paper_id],
                "ok": verification.verdict == "pass",
                "size": path.stat().st_size,
                "error": "" if verification.verdict == "pass" else verification.reason,
            }
            item = self._scihub_item(
                row,
                ok=True,
                path=str(path),
                size=path.stat().st_size,
                source="scihub-cli",
                error="" if verification.verdict == "pass" else verification.reason,
                attempts=[attempt],
            )
            collection_rows.append(item)
            verification_rows.append({"paper_id": paper_id, **verification.to_dict()})
            self.db.add_attempt(task_id, paper_id, round_num, attempt)
            new_status = "verified" if verification.verdict == "pass" else "failed"
            self.db.update_paper(
                paper_id,
                pdf_status=new_status,
                pdf_path=str(path),
                verification_status=verification.verdict,
                verification=verification.to_dict(),
            )
            if verification.verdict == "pass":
                self.db.upsert_library_paper({
                    **paper.to_dict(),
                    "pdf_path": str(path),
                    "pdf_status": "verified",
                    "verification_status": verification.verdict,
                    "verification": verification.to_dict(),
                })
        return collection_rows, verification_rows

    @staticmethod
    def _merge_fallback_rows(
        primary_rows: list[dict[str, Any]],
        fallback_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Keep one report row per paper while preserving both download attempts."""
        merged = {int(row.get("paper_id") or 0): dict(row) for row in primary_rows}
        for fallback in fallback_rows:
            paper_id = int(fallback.get("paper_id") or 0)
            previous = merged.get(paper_id)
            current = dict(fallback)
            if previous:
                current["attempts"] = [
                    *(previous.get("attempts") or []),
                    *(current.get("attempts") or []),
                ]
                if not current.get("ok") and previous.get("error"):
                    fallback_error = current.get("error") or "SciHub fallback failed"
                    current["error"] = f"原有下载失败: {previous['error']}; SciHub: {fallback_error}"
            merged[paper_id] = current
        return list(merged.values())

    @staticmethod
    def _merge_fallback_verification(
        primary_rows: list[dict[str, Any]],
        fallback_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Keep the latest verification result once a fallback has been tried."""
        merged = {int(row.get("paper_id") or 0): dict(row) for row in primary_rows}
        for fallback in fallback_rows:
            paper_id = int(fallback.get("paper_id") or 0)
            current = dict(merged.get(paper_id) or {})
            current.update(fallback)
            merged[paper_id] = current
        return list(merged.values())

    def collect_round(
        self,
        task_id: str,
        callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        with self._lock(task_id):
            task = self._require_task(task_id)
            ready = task["status"] == "ready:download"
            started_by_api = task["status"] == "running" and task["phase"] == "collect"
            if not ready and not started_by_api:
                raise ValueError(f"task status does not allow collection: {task['status']}")
            round_num = int(task["current_round"] or 0) + 1
            targets = self.db.list_papers(task_id, ("pending_download", "failed"))
            if not targets:
                return self.finalize(task_id)

            self._emit(task_id, "collect", f"正在准备单次下载 {len(targets)} 篇文献", callback, current_round=round_num)
            collection_rows: list[dict[str, Any]] = []
            verification_rows: list[dict[str, Any]] = []
            try:
                for row in targets:
                    self.db.update_paper(int(row["id"]), pdf_status="downloading")

                if self.config.download_backend == "scihub-job":
                    self._emit(
                        task_id,
                        "collect",
                        f"正在创建并等待 SciHub Job（{len(targets)} 篇）",
                        callback,
                        current_round=round_num,
                    )
                    collection_rows, verification_rows = self._collect_scihub(task_id, round_num, targets)
                else:
                    max_workers = min(self.config.download_concurrency, len(targets))
                    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="literature-download") as executor:
                        futures = {
                            executor.submit(self._collect_one, task_id, round_num, row): row
                            for row in targets
                        }
                        for completed, future in enumerate(as_completed(futures), 1):
                            row = futures[future]
                            item, verification_row = future.result()
                            collection_rows.append(item)
                            if verification_row is not None:
                                verification_rows.append(verification_row)
                            self._emit(
                                task_id,
                                "collect",
                                f"下载进度：已完成 {completed}/{len(targets)} - {row['title'][:60]}",
                                callback,
                                current_round=round_num,
                            )
                if self.config.download_backend == "hybrid":
                    fallback_targets = self.db.list_papers(task_id, ("pending_download", "failed"))
                    if fallback_targets:
                        self._emit(
                            task_id,
                            "collect",
                            f"SciHub fallback: {len(fallback_targets)} papers remain after direct download",
                            callback,
                            current_round=round_num,
                        )
                        fallback_rows, fallback_verification = self._collect_scihub(
                            task_id, round_num, fallback_targets
                        )
                        collection_rows = self._merge_fallback_rows(collection_rows, fallback_rows)
                        verification_rows = self._merge_fallback_verification(
                            verification_rows, fallback_verification
                        )

                collection_rows.sort(key=lambda item: int(item.get("paper_id") or 0))
                verification_rows.sort(key=lambda item: int(item.get("paper_id") or 0))

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
                return self.finalize(task_id)
            except Exception as exc:
                self._fail(task_id, exc)
                raise

    def download(self, task_id: str, callback: ProgressCallback | None = None) -> dict[str, Any]:
        """Run the download and verification stage once for a searched task."""
        return self.collect_round(task_id, callback)

    def finalize(self, task_id: str) -> dict[str, Any]:
        with self._lock(task_id):
            task = self._require_task(task_id)
            verified = self.db.list_papers(task_id, ("verified",))
            pending = self.db.list_papers(task_id, ("pending_download", "failed", "downloaded", "downloading"))
            collection = dict(task.get("collection") or {})
            final_path = save_report(
                format_final_report(
                    task["topic"],
                    list(collection.get("rounds") or []),
                    verified,
                    pending,
                    search=task.get("search") or {},
                ),
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
        reports = task.get("reports") or {}
        path = Path(str(reports.get("final") or reports.get("search") or ""))
        if not path.exists():
            raise FileNotFoundError("search report is not available")
        return path

    def doi_list_path(self, task_id: str) -> Path:
        task = self._require_task(task_id)
        path = Path(str((task.get("reports") or {}).get("doi_list") or ""))
        if not path.exists():
            raise FileNotFoundError("DOI list is not available")
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
            "id": row.get("local_id") or row.get("id"),
            "title": row.get("title", ""),
            "authors": row.get("authors", ""),
            "date": row.get("date", ""),
            "doi": row.get("doi", ""),
            "arxiv_id": row.get("arxiv_id") or (row.get("identifiers") or {}).get("arxiv", ""),
            "provider": row.get("provider", ""),
            "providers": row.get("providers", []),
            "venue": row.get("venue", ""),
            "abstract": row.get("abstract", ""),
            "pdf_url": row.get("pdf_url", ""),
            "identifiers": row.get("identifiers", {}),
            "open_access": row.get("open_access", False),
            "cited_by_count": row.get("cited_by_count", 0),
            "url": row.get("url", ""),
            "pdf_status": row.get("pdf_status", "pending_download"),
            "verification_status": row.get("verification_status", ""),
            "pdf_path": row.get("pdf_path", ""),
            "relevance_score": row.get("relevance_score", 0),
            "relevance_method": row.get("relevance_method", "rules"),
            "llm_included": row.get("llm_included"),
            "llm_relevance_score": row.get("llm_relevance_score"),
            "relevance_reason": row.get("relevance_reason", ""),
            "source_record": row.get("source_record", {}),
        }
