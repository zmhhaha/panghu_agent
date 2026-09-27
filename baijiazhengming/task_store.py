"""显式 Agent 作用域的任务存储，不使用 sqlite_client 的全局 service。"""
from __future__ import annotations

import json
import re
import uuid

from tools import sqlite_client


_SERVICE_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class TaskStore:
    def __init__(self, service_name: str):
        if not _SERVICE_NAME.fullmatch(service_name):
            raise ValueError(f"invalid task-store service name: {service_name!r}")
        self.service_name = service_name
        self.tasks_table = f"{service_name}_tasks"
        self.reports_table = f"{service_name}_reports"

    def initialize(self) -> None:
        sqlite_client._execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.tasks_table} (
                id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                user_id TEXT,
                report TEXT,
                error TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        sqlite_client._execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.reports_table} (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                topic TEXT NOT NULL,
                summary TEXT,
                keywords TEXT,
                content TEXT NOT NULL,
                tokens_used INTEGER DEFAULT 0,
                model_used TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """
        )

    def clear_stale_tasks(self) -> None:
        sqlite_client._execute(
            f"UPDATE {self.tasks_table} SET status='failed', "
            "error='服务重启导致任务中断，请重新提交', updated_at=datetime('now') "
            "WHERE status IN ('pending','running')"
        )

    def create_task(self, text: str, user_id: str = "") -> str:
        task_id = str(uuid.uuid4())
        sqlite_client._execute(
            f"INSERT INTO {self.tasks_table} (id,topic,status,user_id) VALUES ("
            f"'{task_id}','{self._escape(text)}','pending','{self._escape(user_id)}')"
        )
        return task_id

    def update_task(self, task_id: str, **values: str) -> None:
        allowed = {"status", "report", "error"}
        if not values or not set(values).issubset(allowed):
            raise ValueError("unsupported task update")
        assignments = ", ".join(
            f"{key}='{self._escape(str(value))}'" for key, value in values.items()
        )
        sqlite_client._execute(
            f"UPDATE {self.tasks_table} SET {assignments}, updated_at=datetime('now') "
            f"WHERE id='{self._escape(task_id)}'"
        )

    def get_task(self, task_id: str) -> dict | None:
        rows = sqlite_client._query(
            f"SELECT * FROM {self.tasks_table} WHERE id='{self._escape(task_id)}'"
        )
        return rows[0] if rows else None

    def get_running_task(self, user_id: str) -> dict | None:
        if not user_id:
            return None
        rows = sqlite_client._query(
            f"SELECT * FROM {self.tasks_table} "
            f"WHERE user_id='{self._escape(user_id)}' "
            "AND status IN ('pending','running') ORDER BY created_at ASC LIMIT 1"
        )
        return rows[0] if rows else None

    def find_cached(self, text: str) -> str | None:
        rows = sqlite_client._query(
            f"SELECT content FROM {self.reports_table} "
            f"WHERE topic='{self._escape(text)}' AND content IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1"
        )
        if rows and rows[0].get("content"):
            return rows[0]["content"]
        return None

    def save_report(self, task_id: str, text: str, content: str) -> None:
        summary = self._summary(content)
        keywords = self._keywords(text)
        sqlite_client._execute(
            f"INSERT OR REPLACE INTO {self.reports_table} "
            "(id,task_id,topic,summary,keywords,content,created_at) VALUES ("
            f"'{self._escape(task_id)}','{self._escape(task_id)}',"
            f"'{self._escape(text)}','{self._escape(summary)}',"
            f"'{self._escape(keywords)}','{self._escape(content)}',datetime('now'))"
        )

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("'", "''")

    @staticmethod
    def _summary(content: str, max_length: int = 150) -> str:
        for line in content.splitlines():
            value = line.strip()
            if value and not value.startswith("#") and len(value) > 15:
                return value[:max_length] + ("..." if len(value) > max_length else "")
        return content[:max_length]

    @staticmethod
    def _keywords(text: str) -> str:
        stopwords = {"的", "与", "及", "和", "在", "了", "是", "有", "之", "为", "等", "中"}
        words = text.replace("、", " ").replace(",", " ").replace("，", " ").split()
        return json.dumps(
            [word for word in words if len(word) >= 2 and word not in stopwords][:5],
            ensure_ascii=False,
        )
