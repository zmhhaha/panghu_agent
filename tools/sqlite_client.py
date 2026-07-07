"""
SQLite 服务 HTTP 客户端（通用）

- 每个服务调用 init_db("my_service") 后，所有 API 自动路由到
  <my_service>_tasks / <my_service>_reports 表
- 多个服务共用一个 SQLite 数据库，表名隔离（research / scientific / …）
"""
from __future__ import annotations
import json
import uuid
import urllib.request
import urllib.error

SQLITE_URL = "http://sqlite.data.svc.cluster.local:8000"
_SERVICE = "default"   # init_db() 之前的值


def set_service(name: str):
    """切换当前服务上下文：init_db 或手动 set_service 都可以"""
    global _SERVICE
    _SERVICE = name


def get_service() -> str:
    return _SERVICE


# ── low-level ──

def _execute(sql: str):
    data = json.dumps({"sql": sql}).encode("utf-8")
    req = urllib.request.Request(f"{SQLITE_URL}/execute", data=data,
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10)


def _query(sql: str) -> list[dict]:
    data = json.dumps({"sql": sql}).encode("utf-8")
    req = urllib.request.Request(f"{SQLITE_URL}/query", data=data,
                                 headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read().decode("utf-8")).get("rows", [])


def _esc(s: str) -> str:
    return s.replace("'", "''")


# ── schemas ──

def init_db(service: str):
    """每个服务启动时调用一次，自动建表"""
    set_service(service)
    _execute(f"""
        CREATE TABLE IF NOT EXISTS {service}_tasks (
            id         TEXT PRIMARY KEY,
            topic      TEXT NOT NULL,
            status     TEXT DEFAULT 'pending',
            report     TEXT,
            error      TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    _execute(f"""
        CREATE TABLE IF NOT EXISTS {service}_reports (
            id          TEXT PRIMARY KEY,
            task_id     TEXT NOT NULL,
            topic       TEXT NOT NULL,
            summary     TEXT,
            keywords    TEXT,
            content     TEXT NOT NULL,
            tokens_used INTEGER DEFAULT 0,
            model_used  TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)


# ── tasks ──

def create_task(topic: str) -> str:
    tid = str(uuid.uuid4())
    _execute(f"INSERT INTO {_SERVICE}_tasks (id,topic,status) VALUES ('{tid}','{_esc(topic)}','pending')")
    return tid


def update_task(task_id: str, **kwargs):
    sets = ", ".join(f"{k}='{_esc(str(v))}'" for k, v in kwargs.items())
    _execute(f"UPDATE {_SERVICE}_tasks SET {sets}, updated_at=datetime('now') WHERE id='{task_id}'")


def get_task(task_id: str) -> dict | None:
    rows = _query(f"SELECT * FROM {_SERVICE}_tasks WHERE id='{task_id}'")
    return rows[0] if rows else None


# ── reports ──

def save_report(task_id: str, topic: str, summary: str, keywords: str, content: str):
    _execute(
        f"INSERT OR REPLACE INTO {_SERVICE}_reports (id,task_id,topic,summary,keywords,content,created_at) "
        f"VALUES ('{task_id}','{task_id}','{_esc(topic)}','{_esc(summary)}',"
        f"'{_esc(keywords)}','{_esc(content)}',datetime('now'))"
    )


def search_reports(q: str = "", limit: int = 20, offset: int = 0) -> list:
    if q:
        pattern = f"%{q}%"
        sql = (f"SELECT * FROM {_SERVICE}_reports WHERE topic LIKE '{pattern}' "
               f"OR summary LIKE '{pattern}' OR content LIKE '{pattern}' "
               f"ORDER BY created_at DESC LIMIT {limit} OFFSET {offset}")
    else:
        sql = f"SELECT * FROM {_SERVICE}_reports ORDER BY created_at DESC LIMIT {limit} OFFSET {offset}"
    return _query(sql)


def get_report(report_id: str) -> dict | None:
    rows = _query(f"SELECT * FROM {_SERVICE}_reports WHERE id='{report_id}'")
    return rows[0] if rows else None


def get_report_by_task(task_id: str) -> dict | None:
    rows = _query(f"SELECT * FROM {_SERVICE}_reports WHERE task_id='{task_id}'")
    return rows[0] if rows else None
