"""Local SQLite persistence for tasks, papers, attempts, and reports."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_doi(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text)
    text = re.sub(r"^doi:\s*", "", text)
    return text.rstrip(".")


def normalize_title(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").lower())


def identity_key(paper: dict[str, Any]) -> str:
    doi = normalize_doi(paper.get("doi"))
    if doi:
        return f"doi:{doi}"
    arxiv = str(paper.get("arxiv_id") or (paper.get("identifiers") or {}).get("arxiv") or "").strip().lower()
    if arxiv:
        return f"arxiv:{arxiv}"
    return f"title:{normalize_title(paper.get('title'))}|authors:{normalize_title(paper.get('authors'))}"


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    phase TEXT NOT NULL DEFAULT 'init',
                    progress TEXT NOT NULL DEFAULT '',
                    max_rounds INTEGER NOT NULL DEFAULT 3,
                    current_round INTEGER NOT NULL DEFAULT 0,
                    user_id TEXT NOT NULL DEFAULT '',
                    email TEXT NOT NULL DEFAULT '',
                    search_json TEXT NOT NULL DEFAULT '{}',
                    collection_json TEXT NOT NULL DEFAULT '{}',
                    reports_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS papers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT,
                    identity_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    authors TEXT NOT NULL DEFAULT '',
                    date TEXT NOT NULL DEFAULT '',
                    doi TEXT NOT NULL DEFAULT '',
                    arxiv_id TEXT NOT NULL DEFAULT '',
                    pmid TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT '',
                    providers_json TEXT NOT NULL DEFAULT '[]',
                    abstract TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL DEFAULT '',
                    pdf_url TEXT NOT NULL DEFAULT '',
                    venue TEXT NOT NULL DEFAULT '',
                    cited_by_count INTEGER NOT NULL DEFAULT 0,
                    open_access INTEGER NOT NULL DEFAULT 0,
                    identifiers_json TEXT NOT NULL DEFAULT '{}',
                    pdf_path TEXT NOT NULL DEFAULT '',
                    pdf_status TEXT NOT NULL DEFAULT 'none',
                    verification_status TEXT NOT NULL DEFAULT '',
                    verification_json TEXT NOT NULL DEFAULT '{}',
                    relevance_score REAL NOT NULL DEFAULT 0,
                    relevance_method TEXT NOT NULL DEFAULT 'rules',
                    llm_included INTEGER,
                    llm_relevance_score REAL,
                    relevance_reason TEXT NOT NULL DEFAULT '',
                    source_record_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(task_id, identity_key),
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_papers_identity ON papers(identity_key);
                CREATE INDEX IF NOT EXISTS idx_papers_status ON papers(pdf_status);
                CREATE TABLE IF NOT EXISTS download_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    paper_id INTEGER NOT NULL,
                    round INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    url TEXT NOT NULL DEFAULT '',
                    ok INTEGER NOT NULL DEFAULT 0,
                    size INTEGER NOT NULL DEFAULT 0,
                    elapsed_ms INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                    FOREIGN KEY(paper_id) REFERENCES papers(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    round INTEGER NOT NULL DEFAULT 0,
                    report_type TEXT NOT NULL,
                    path TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
                );
                """
            )
            # Existing PVC databases predate email notifications. Keep the
            # migration idempotent so a rolling deployment can reuse them.
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
            if "email" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN email TEXT NOT NULL DEFAULT ''")
            paper_columns = {row["name"] for row in conn.execute("PRAGMA table_info(papers)").fetchall()}
            migrations = {
                "relevance_method": "ALTER TABLE papers ADD COLUMN relevance_method TEXT NOT NULL DEFAULT 'rules'",
                "llm_included": "ALTER TABLE papers ADD COLUMN llm_included INTEGER",
                "llm_relevance_score": "ALTER TABLE papers ADD COLUMN llm_relevance_score REAL",
                "relevance_reason": "ALTER TABLE papers ADD COLUMN relevance_reason TEXT NOT NULL DEFAULT ''",
                "source_record_json": "ALTER TABLE papers ADD COLUMN source_record_json TEXT NOT NULL DEFAULT '{}'",
            }
            for name, statement in migrations.items():
                if name not in paper_columns:
                    conn.execute(statement)

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    def create_task(self, topic: str, max_rounds: int, user_id: str = "", email: str = "") -> str:
        task_id = str(uuid.uuid4())
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO tasks (id, topic, max_rounds, user_id, email, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, topic.strip(), max_rounds, user_id, email.strip(), now, now),
            )
        return task_id

    def get_active_task(self, owner: str) -> dict[str, Any] | None:
        """Return the oldest unfinished task for a user or email."""
        owner = owner.strip()
        if not owner:
            return None
        with self.connect() as conn:
            row = conn.execute(
                """SELECT * FROM tasks
                WHERE status IN ('pending', 'running')
                  AND (user_id = ? OR email = ?)
                ORDER BY created_at ASC LIMIT 1""",
                (owner, owner),
            ).fetchone()
        return self._row(row)

    def search_history(self, query: str = "", limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        """Search persisted tasks for the history page."""
        query = query.strip()
        limit = min(max(int(limit), 1), 100)
        offset = max(int(offset), 0)
        with self.connect() as conn:
            if query:
                pattern = f"%{query}%"
                rows = conn.execute(
                    """SELECT id, topic, status, phase, progress, current_round,
                              created_at, updated_at, reports_json
                       FROM tasks
                       WHERE topic LIKE ? OR id LIKE ? OR status LIKE ?
                       ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                    (pattern, pattern, pattern, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT id, topic, status, phase, progress, current_round,
                              created_at, updated_at, reports_json
                       FROM tasks ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                    (limit, offset),
                ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            reports = json.loads(item.pop("reports_json") or "{}")
            item["reports"] = reports
            item["report_available"] = bool(reports.get("final")) and Path(str(reports["final"])).is_file()
            item["pdf_available"] = bool(reports.get("pdf_zip")) and Path(str(reports["pdf_zip"])).is_file()
            result.append(item)
        return result

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        result = self._row(row)
        if result:
            for key in ("search_json", "collection_json", "reports_json"):
                result[key[:-5]] = json.loads(result.pop(key) or "{}")
        return result

    def update_task(self, task_id: str, **values: Any) -> None:
        values["updated_at"] = utc_now()
        translated: dict[str, Any] = {}
        for key, value in values.items():
            if key in {"search", "collection", "reports"}:
                translated[f"{key}_json"] = self._json(value)
            else:
                translated[key] = value
        assignments = ", ".join(f"{key} = ?" for key in translated)
        with self.connect() as conn:
            conn.execute(f"UPDATE tasks SET {assignments} WHERE id = ?", (*translated.values(), task_id))

    def list_running_tasks(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM tasks WHERE status IN ('running', 'pending')").fetchall()
        return [dict(row) for row in rows]

    def interrupt_running_tasks(self) -> int:
        """Mark unfinished work lost during a process restart.

        The waiting statuses are checkpoints from the pre-automatic workflow.
        They cannot resume without a user action in the new UI, so they must
        not remain active forever after an API restart.
        """
        now = utc_now()
        with self.connect() as conn:
            cursor = conn.execute(
                """UPDATE tasks
                SET status = 'failed', phase = 'error',
                    progress = '服务重启中断了正在执行的阶段，请重新创建任务',
                    error = 'service restarted while task was running', updated_at = ?
                WHERE status IN ('pending', 'running', 'waiting:search_approval', 'waiting:collect_approval')""",
                (now,),
            )
            return int(cursor.rowcount)

    def upsert_paper(self, task_id: str, paper: dict[str, Any]) -> int:
        now = utc_now()
        key = identity_key(paper)
        values = (
            task_id,
            key,
            str(paper.get("title") or "").strip(),
            str(paper.get("authors") or ""),
            str(paper.get("date") or ""),
            normalize_doi(paper.get("doi")),
            str(paper.get("arxiv_id") or (paper.get("identifiers") or {}).get("arxiv") or ""),
            str(paper.get("pmid") or (paper.get("identifiers") or {}).get("pmid") or ""),
            str(paper.get("provider") or ""),
            self._json(paper.get("providers") or ([paper.get("provider")] if paper.get("provider") else [])),
            str(paper.get("abstract") or ""),
            str(paper.get("url") or ""),
            str(paper.get("pdf_url") or ""),
            str(paper.get("venue") or ""),
            int(paper.get("cited_by_count") or 0),
            int(bool(paper.get("open_access"))),
            self._json(paper.get("identifiers") or {}),
            str(paper.get("pdf_path") or ""),
            str(paper.get("pdf_status") or "pending_download"),
            str(paper.get("verification_status") or ""),
            self._json(paper.get("verification") or {}),
            float(paper.get("relevance_score") or 0),
            str(paper.get("relevance_method") or "rules"),
            (None if paper.get("llm_included") is None else int(bool(paper.get("llm_included")))),
            (None if paper.get("llm_relevance_score") is None else float(paper.get("llm_relevance_score"))),
            str(paper.get("relevance_reason") or ""),
            self._json(paper.get("source_record") or {}),
            now,
            now,
        )
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO papers (
                    task_id, identity_key, title, authors, date, doi, arxiv_id, pmid,
                    provider, providers_json, abstract, url, pdf_url, venue,
                    cited_by_count, open_access, identifiers_json, pdf_path, pdf_status,
                    verification_status, verification_json, relevance_score, relevance_method,
                    llm_included, llm_relevance_score, relevance_reason, source_record_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id, identity_key) DO UPDATE SET
                    title=CASE WHEN excluded.title <> '' THEN excluded.title ELSE papers.title END,
                    authors=CASE WHEN excluded.authors <> '' THEN excluded.authors ELSE papers.authors END,
                    date=CASE WHEN excluded.date <> '' THEN excluded.date ELSE papers.date END,
                    doi=CASE WHEN excluded.doi <> '' THEN excluded.doi ELSE papers.doi END,
                    arxiv_id=CASE WHEN excluded.arxiv_id <> '' THEN excluded.arxiv_id ELSE papers.arxiv_id END,
                    abstract=CASE WHEN length(excluded.abstract) > length(papers.abstract) THEN excluded.abstract ELSE papers.abstract END,
                    url=CASE WHEN excluded.url <> '' THEN excluded.url ELSE papers.url END,
                    pdf_url=CASE WHEN excluded.pdf_url <> '' THEN excluded.pdf_url ELSE papers.pdf_url END,
                    venue=CASE WHEN excluded.venue <> '' THEN excluded.venue ELSE papers.venue END,
                    cited_by_count=MAX(papers.cited_by_count, excluded.cited_by_count),
                    relevance_score=excluded.relevance_score,
                    relevance_method=excluded.relevance_method,
                    llm_included=excluded.llm_included,
                    llm_relevance_score=excluded.llm_relevance_score,
                    relevance_reason=excluded.relevance_reason,
                    source_record_json=CASE WHEN excluded.source_record_json <> '{}' THEN excluded.source_record_json ELSE papers.source_record_json END,
                    updated_at=excluded.updated_at
                """,
                values,
            )
            row = conn.execute("SELECT id FROM papers WHERE task_id = ? AND identity_key = ?", (task_id, key)).fetchone()
        if not row:
            raise RuntimeError("paper upsert failed")
        return int(row["id"])

    def get_paper(self, paper_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
        return self.paper_row(row)

    def list_papers(self, task_id: str, statuses: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM papers WHERE task_id = ?"
        params: list[Any] = [task_id]
        if statuses:
            query += f" AND pdf_status IN ({','.join('?' for _ in statuses)})"
            params.extend(statuses)
        query += " ORDER BY relevance_score DESC, id ASC"
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self.paper_row(row) for row in rows]

    def search_local(self, tokens: list[str], limit: int = 50) -> list[dict[str, Any]]:
        tokens = [token.strip() for token in tokens if token.strip()]
        conditions: list[str] = []
        params: list[Any] = []
        for token in tokens:
            pattern = f"%{token}%"
            conditions.append("(title LIKE ? OR authors LIKE ? OR abstract LIKE ? OR doi LIKE ?)")
            params.extend([pattern] * 4)
        where = " OR ".join(conditions) if conditions else "1=1"
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM papers WHERE ({where}) AND pdf_status = 'verified' ORDER BY date DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [self.paper_row(row) for row in rows]

    @staticmethod
    def paper_row(row: sqlite3.Row | None) -> dict[str, Any]:
        if not row:
            return {}
        result = dict(row)
        result["providers"] = json.loads(result.pop("providers_json") or "[]")
        result["identifiers"] = json.loads(result.pop("identifiers_json") or "{}")
        result["verification"] = json.loads(result.pop("verification_json") or "{}")
        result["source_record"] = json.loads(result.pop("source_record_json") or "{}")
        result["open_access"] = bool(result.get("open_access"))
        return result

    def update_paper(self, paper_id: int, **values: Any) -> None:
        values["updated_at"] = utc_now()
        translated: dict[str, Any] = {}
        for key, value in values.items():
            if key == "identifiers":
                translated["identifiers_json"] = self._json(value)
            elif key == "providers":
                translated["providers_json"] = self._json(value)
            elif key == "verification":
                translated["verification_json"] = self._json(value)
            elif key == "open_access":
                translated[key] = int(bool(value))
            else:
                translated[key] = value
        assignments = ", ".join(f"{key} = ?" for key in translated)
        with self.connect() as conn:
            conn.execute(f"UPDATE papers SET {assignments} WHERE id = ?", (*translated.values(), paper_id))

    def add_attempt(self, task_id: str, paper_id: int, round_num: int, attempt: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO download_attempts
                (task_id, paper_id, round, source, url, ok, size, elapsed_ms, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id, paper_id, round_num, attempt.get("source", ""), attempt.get("url", ""),
                    int(bool(attempt.get("ok"))), int(attempt.get("size") or 0), int(attempt.get("elapsed_ms") or 0),
                    attempt.get("error", ""), utc_now(),
                ),
            )

    def save_report(self, task_id: str, round_num: int, report_type: str, path: str, content: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO reports (task_id, round, report_type, path, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (task_id, round_num, report_type, path, content, utc_now()),
            )

    def list_reports(self, task_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM reports WHERE task_id = ? ORDER BY round, id", (task_id,)).fetchall()
        return [dict(row) for row in rows]
