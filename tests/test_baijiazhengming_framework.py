"""百家争鸣框架：运行中任务看门狗、终态保护与框架级鉴权。

这三处都**曾经静默失效过**，所以各留一条回归测试：

1. 看门狗在 `TaskStore` 重写时被丢掉（旧 `sqlite_client.get_running_task_by_user()`
   有 1 小时自动超时），结果是卡在 `running` 的任务会**永久**锁死该用户；
2. `update_task` 没有终态保护，被看门狗判死的任务还能被"迟到的"执行线程写回 `done`；
3. 框架 API 从来没有鉴权（`user_id` 还来自请求体），集群里任何 Pod 都能提交任务、
   伪造 `user_id` 占住别人的槽位。

`baijiazhengming.api` 故意**不在测试里导入**：它在导入期就会 `TaskStore.initialize()`
并起调度线程，会去连真实的 sqlite 服务。这里只测可以直接构造的部分。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi import HTTPException

from baijiazhengming import auth
from baijiazhengming.task_store import RUNNING_TTL_SECONDS, TaskStore
from tools import sqlite_client as db

REPO = Path(__file__).resolve().parents[1]
PERSONAS = [
    "bingbichunqiu",
    "daofaziran",
    "fofawubian",
    "xiaotanrenjian",
    "yimaneili",
    "zhenzhuzhida",
    "zhongkuifumo",
    "zhougongjiemeng",
]


class _FakeDB:
    """替掉 sqlite_client 的 HTTP 传输层，只记录下发的 SQL。"""

    def __init__(self, rows: list[dict]):
        self._rows = list(rows)
        self.queries: list[str] = []
        self.executes: list[str] = []

    def query(self, sql: str) -> list[dict]:
        """按 sqlite_client._query 的契约返回**行列表**（不是单行）。"""
        self.queries.append(sql)
        return [self._rows.pop(0)] if self._rows else []

    def execute(self, sql: str) -> None:
        self.executes.append(sql)


@pytest.fixture()
def fake(monkeypatch):
    fake_db = _FakeDB([])

    def install(rows=()):
        fake_db._rows = list(rows)
        fake_db.queries.clear()
        fake_db.executes.clear()
        monkeypatch.setattr(db, "_query", fake_db.query)
        monkeypatch.setattr(db, "_execute", fake_db.execute)
        return fake_db

    return install


# ---------------------------------------------------------------- 看门狗


def test_expired_running_task_is_reaped_and_user_released(fake):
    db_fake = fake([{"id": "t1", "status": "running", "expired": 1}])
    store = TaskStore("daofaziran_agent")

    assert store.get_running_task("u1") is None
    assert len(db_fake.executes) == 1
    sql = db_fake.executes[0]
    assert "status='timeout'" in sql
    assert f"超过 {RUNNING_TTL_SECONDS} 秒" in sql


def test_live_running_task_still_blocks_the_user(fake):
    task = {"id": "t2", "status": "running", "expired": 0}
    db_fake = fake([task])
    store = TaskStore("daofaziran_agent")

    assert store.get_running_task("u1") == task
    assert db_fake.executes == [], "没过期就不该写库"


def test_expiry_is_computed_by_sqlite_not_the_local_clock(fake):
    db_fake = fake([])
    store = TaskStore("daofaziran_agent")

    store.get_running_task("u1")
    sql = db_fake.queries[0]
    assert f"'+{RUNNING_TTL_SECONDS} seconds'" in sql
    assert "datetime('now')" in sql
    # 基准是 updated_at（转 running 的时刻），不是 created_at（可能包含排队时间）
    assert "COALESCE(updated_at, created_at)" in sql


def test_anonymous_submitter_has_no_dedupe_query(fake):
    db_fake = fake([{"id": "t3", "expired": 0}])
    store = TaskStore("daofaziran_agent")

    assert store.get_running_task("") is None
    assert db_fake.queries == []


def test_update_task_cannot_resurrect_a_reaped_task(fake):
    db_fake = fake([])
    store = TaskStore("daofaziran_agent")

    store.update_task("t1", status="done", report="迟到的结果")
    assert "AND status IN ('pending','running')" in db_fake.executes[0]


# ---------------------------------------------------------------- 鉴权


def test_auth_fails_closed_when_token_is_not_injected(monkeypatch):
    monkeypatch.delenv(auth.ENV_NAME, raising=False)

    with pytest.raises(HTTPException) as error:
        auth.require_token("Bearer anything")
    assert error.value.status_code == 503
    assert auth.configuration_error() is not None


def test_auth_rejects_wrong_token(monkeypatch):
    monkeypatch.setenv(auth.ENV_NAME, "s3cret")

    with pytest.raises(HTTPException) as error:
        auth.require_token("Bearer nope")
    assert error.value.status_code == 401
    assert auth.configuration_error() is None


@pytest.mark.parametrize("header", [None, "", "s3cret", "Basic s3cret", "Bearer "])
def test_auth_rejects_malformed_authorization(monkeypatch, header):
    monkeypatch.setenv(auth.ENV_NAME, "s3cret")

    with pytest.raises(HTTPException) as error:
        auth.require_token(header)
    assert error.value.status_code == 401


def test_auth_accepts_the_configured_token(monkeypatch):
    monkeypatch.setenv(auth.ENV_NAME, "s3cret")

    assert auth.require_token("Bearer s3cret") is None


# ---------------------------------------------------------------- LLM 超时


@pytest.mark.parametrize("slug", PERSONAS)
def test_every_persona_passes_a_timeout_to_the_llm(slug):
    """静态检查而不是导入：crewai 只在镜像里，且这里只想守住"别把 timeout 删了"。"""
    path = REPO / f"{slug}_agent" / "crew.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "LLM"
    ]
    assert calls, f"{path} 里找不到 LLM(...) 调用"
    for call in calls:
        assert "timeout" in {kw.arg for kw in call.keywords}, f"{path} 的 LLM() 没有 timeout"
