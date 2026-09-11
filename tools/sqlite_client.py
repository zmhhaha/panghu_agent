"""
SQLite 服务 HTTP 客户端（通用）

- 每个服务调用 init_db("my_service") 后，所有 API 自动路由到
  <my_service>_tasks / <my_service>_reports 表
- 多个服务共用一个 SQLite 数据库，表名隔离（research / scientific / …）
"""
from __future__ import annotations
import json
import os
import time
import uuid
import urllib.request
import urllib.error

SQLITE_URL = "http://sqlite.data.svc.cluster.local:8000"
_SERVICE = "default"   # init_db() 之前的值

# sqlite 服务短暂不可用（如节点重启时 sqlite 尚未就绪）时的重试参数。
# 只在「连接被拒/连不上」时重试；HTTP 错误与超时不重试（避免重复写入）。
_RETRY_ATTEMPTS = int(os.environ.get("SQLITE_RETRY_ATTEMPTS", "10"))
_RETRY_DELAY = float(os.environ.get("SQLITE_RETRY_DELAY", "3"))
_RETRYABLE = (ConnectionRefusedError, ConnectionResetError, ConnectionAbortedError, ConnectionError)


def set_service(name: str):
    """切换当前服务上下文：init_db 或手动 set_service 都可以"""
    global _SERVICE
    _SERVICE = name


def get_service() -> str:
    return _SERVICE


# ── low-level ──

def _urlopen(req, timeout: int = 10):
    """带重试的 urlopen：sqlite 短暂不可用（连接被拒/连不上）时自动重试。
    服务已经响应（HTTP 4xx/5xx）或超时则直接抛出，不重试，避免重复写入。"""
    last_err = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError:
            raise  # 服务已正常响应，非连接问题
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), _RETRYABLE):
                last_err = e
                if attempt < _RETRY_ATTEMPTS - 1:
                    time.sleep(_RETRY_DELAY)
                    continue
            raise
    raise last_err


def _execute(sql: str):
    data = json.dumps({"sql": sql}).encode("utf-8")
    req = urllib.request.Request(f"{SQLITE_URL}/execute", data=data,
                                 headers={"Content-Type": "application/json"})
    _urlopen(req)


def _query(sql: str) -> list[dict]:
    data = json.dumps({"sql": sql}).encode("utf-8")
    req = urllib.request.Request(f"{SQLITE_URL}/query", data=data,
                                 headers={"Content-Type": "application/json"})
    resp = _urlopen(req)
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
            user_id    TEXT,
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

def create_task(topic: str, user_id: str = "") -> str:
    tid = str(uuid.uuid4())
    _execute(f"INSERT INTO {_SERVICE}_tasks (id,topic,status,user_id) "
             f"VALUES ('{tid}','{_esc(topic)}','pending','{_esc(user_id)}')")
    return tid


def update_task(task_id: str, **kwargs):
    sets = ", ".join(f"{k}='{_esc(str(v))}'" for k, v in kwargs.items())
    _execute(f"UPDATE {_SERVICE}_tasks SET {sets}, updated_at=datetime('now') WHERE id='{task_id}'")


def get_task(task_id: str) -> dict | None:
    rows = _query(f"SELECT * FROM {_SERVICE}_tasks WHERE id='{task_id}'")
    return rows[0] if rows else None


def get_running_task_by_user(user_id: str) -> dict | None:
    """返回该用户正在运行中的任务（status 为 pending/running），
    如果超过 1 小时则自动标记为 timeout 并返回 None（允许重入）"""
    rows = _query(
        f"SELECT * FROM {_SERVICE}_tasks "
        f"WHERE user_id='{_esc(user_id)}' AND status IN ('pending','running') "
        f"ORDER BY created_at ASC LIMIT 1"
    )
    if not rows:
        return None

    task = rows[0]
    # 判断是否超过 1 小时
    created = task.get("created_at", "")
    if created:
        expired = _query(
            f"SELECT CASE WHEN "
            f"  datetime('{_esc(created)}', '+1 hour') < datetime('now') "
            f"THEN 1 ELSE 0 END AS expired"
        )
        if expired and expired[0].get("expired") == 1:
            _execute(
                f"UPDATE {_SERVICE}_tasks SET status='timeout', "
                f"error='任务执行超过 1 小时，已自动超时', "
                f"updated_at=datetime('now') "
                f"WHERE id='{task['id']}'"
            )
            return None

    return task


# ── 启动时清理 ──

def clear_stale_tasks():
    """服务启动时调用，将上次进程挂掉遗留的 running/pending 任务标记为 failed"""
    _execute(
        f"UPDATE {_SERVICE}_tasks SET status='failed', "
        f"error='服务重启导致任务中断，请重新提交', "
        f"updated_at=datetime('now') "
        f"WHERE status IN ('pending','running')"
    )


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
