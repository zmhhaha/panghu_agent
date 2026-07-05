"""SQLite 服务 HTTP 客户端 — 所有持久化走共享 SQLite 服务，容器本地不留数据。"""

import json
import urllib.request
import urllib.error

SQLITE_URL = "http://sqlite.data.svc.cluster.local:8000"


def _execute(sql: str):
    """执行写操作（INSERT/UPDATE/DELETE/CREATE）"""
    data = json.dumps({"sql": sql}).encode("utf-8")
    req = urllib.request.Request(f"{SQLITE_URL}/execute", data=data,
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10)


def _query(sql: str) -> list:
    """执行查询，返回 rows 列表"""
    data = json.dumps({"sql": sql}).encode("utf-8")
    req = urllib.request.Request(f"{SQLITE_URL}/query", data=data,
                                 headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read().decode("utf-8")).get("rows", [])


# ============================================================
#  建表
# ============================================================

def init_db():
    _execute("""
        CREATE TABLE IF NOT EXISTS research_tasks (
            id         TEXT PRIMARY KEY,
            topic      TEXT NOT NULL,
            status     TEXT DEFAULT 'pending',
            report     TEXT,
            error      TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    _execute("""
        CREATE TABLE IF NOT EXISTS research_reports (
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


# ============================================================
#  任务操作
# ============================================================

def create_task(topic: str) -> str:
    import uuid
    tid = str(uuid.uuid4())
    _execute(f"INSERT INTO research_tasks (id, topic, status) VALUES ('{tid}', '{_esc(topic)}', 'pending')")
    return tid


def update_task(task_id: str, **kwargs):
    sets = ", ".join(f"{k}='{_esc(str(v))}'" for k, v in kwargs.items())
    _execute(f"UPDATE research_tasks SET {sets}, updated_at=datetime('now') WHERE id='{task_id}'")


def get_task(task_id: str) -> dict | None:
    rows = _query(f"SELECT * FROM research_tasks WHERE id='{task_id}'")
    return rows[0] if rows else None


# ============================================================
#  报告操作
# ============================================================

def save_report(task_id: str, topic: str, summary: str, keywords: str, content: str):
    _execute(
        f"INSERT OR REPLACE INTO research_reports (id, task_id, topic, summary, keywords, content, created_at) "
        f"VALUES ('{task_id}', '{task_id}', '{_esc(topic)}', '{_esc(summary)}', "
        f"'{_esc(keywords)}', '{_esc(content)}', datetime('now'))"
    )


def search_reports(q: str = "", limit: int = 20, offset: int = 0) -> list:
    if q:
        pattern = f"%{q}%"
        sql = (f"SELECT * FROM research_reports WHERE topic LIKE '{pattern}' "
               f"OR summary LIKE '{pattern}' OR content LIKE '{pattern}' "
               f"ORDER BY created_at DESC LIMIT {limit} OFFSET {offset}")
    else:
        sql = f"SELECT * FROM research_reports ORDER BY created_at DESC LIMIT {limit} OFFSET {offset}"
    return _query(sql)


def get_report(report_id: str) -> dict | None:
    rows = _query(f"SELECT * FROM research_reports WHERE id='{report_id}'")
    return rows[0] if rows else None


def get_report_by_task(task_id: str) -> dict | None:
    rows = _query(f"SELECT * FROM research_reports WHERE task_id='{task_id}'")
    return rows[0] if rows else None


# ============================================================
#  工具
# ============================================================

def _esc(s: str) -> str:
    """SQL 字符串转义（防注入 + 单引号）"""
    return s.replace("'", "''")
