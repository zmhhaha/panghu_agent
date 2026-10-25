"""Local integration harness: real HTTP apps/protocol, synthetic research only."""
from __future__ import annotations

from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

from app.api.scientific_agent import create_app as create_public_app
from pi_worker.app import WorkerSettings, create_app as create_worker_app
from pi_worker.scheduler import Scheduler
from pi_worker.store import QueueStore
from pi_worker.research_tools import BUNDLE_REVISION
from scientific_agent.pi_client import PiClient
from scientific_agent.pi_contracts import utc_now
from scientific_agent.pi_projection import PiProjection

# Load C fixtures by explicit path; tests/ is intentionally not a Python package.
_fixture_path = Path(__file__).resolve().parents[1] / "research" / "fixtures.py"
_spec = importlib.util.spec_from_file_location("scientific_pi_integration_research_fixtures", _fixture_path)
research_fixtures = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(research_fixtures)


class SharedSQLFacade:
    """In-memory implementation of the existing shared HTTP SQL helper API.

    Actual SQL (including projection insert-if-absent and readback) is executed;
    only the remote shared database service and legacy helper transport are fake.
    """
    def __init__(self):
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.fail_report_writes = False
        self.fail_rejection_update_once = False
        self.executed = []
        self.legacy_cleanup_calls = 0

    def _execute(self, statement):
        with self.lock:
            self.executed.append(statement)
            if self.fail_report_writes and statement.startswith("INSERT INTO scientific_reports"):
                raise OSError("controlled report-store outage")
            if self.fail_rejection_update_once and statement.startswith("UPDATE scientific_pi_index") and "dispatch='rejected'" in statement:
                self.fail_rejection_update_once = False
                raise OSError("controlled rejected-intent persistence outage")
            self.connection.execute(statement)
            self.connection.commit()

    def _query(self, statement):
        with self.lock:
            return [dict(row) for row in self.connection.execute(statement).fetchall()]

    def init_db(self, service):
        if service != "scientific":
            raise AssertionError("unexpected database namespace")
        self._execute("CREATE TABLE IF NOT EXISTS scientific_tasks (id TEXT PRIMARY KEY,topic TEXT,status TEXT,user_id TEXT,report TEXT,error TEXT)")
        self._execute("CREATE TABLE IF NOT EXISTS scientific_reports (id TEXT PRIMARY KEY,task_id TEXT UNIQUE,topic TEXT,summary TEXT,keywords TEXT,content TEXT,model_used TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP)")

    def clear_stale_tasks(self):
        self.legacy_cleanup_calls += 1
        self._execute("UPDATE scientific_tasks SET status='failed' WHERE status IN ('pending','running')")

    def get_task(self, task_id):
        with self.lock:
            row = self.connection.execute("SELECT * FROM scientific_tasks WHERE id=?", (task_id,)).fetchone()
            return dict(row) if row else None

    def get_running_task_by_user(self, user_id):
        with self.lock:
            row = self.connection.execute("SELECT * FROM scientific_tasks WHERE user_id=? AND status IN ('pending','running')", (user_id,)).fetchone()
            return dict(row) if row else None

    def get_report(self, report_id):
        with self.lock:
            row = self.connection.execute("SELECT * FROM scientific_reports WHERE id=?", (report_id,)).fetchone()
            return dict(row) if row else None

    def get_report_by_task(self, task_id):
        with self.lock:
            row = self.connection.execute("SELECT * FROM scientific_reports WHERE task_id=?", (task_id,)).fetchone()
            return dict(row) if row else None

    def search_reports(self, query, limit, offset):
        # Public route already SQL-quotes search terms for existing helper semantics.
        raw_query = query.replace("''", "'")
        with self.lock:
            rows = self.connection.execute("SELECT * FROM scientific_reports WHERE topic LIKE ? OR content LIKE ? ORDER BY created_at DESC,id LIMIT ? OFFSET ?", ("%" + raw_query + "%", "%" + raw_query + "%", limit, offset)).fetchall()
            return [dict(row) for row in rows]


class WorkerTransport:
    """Actual worker ASGI responses adapted to PiClient's five-argument transport."""
    def __init__(self, worker_http):
        self.worker_http = worker_http
        self.calls = []
        self.unavailable = False
        self.lose_put_response_once = False
        self.lose_ack_before_once = False
        self.lose_ack_after_once = False

    def __call__(self, method, url, headers, payload, timeout):
        if timeout <= 0:
            raise AssertionError("expected finite client timeout")
        parsed = urlsplit(url)
        path = parsed.path + ("?" + parsed.query if parsed.query else "")
        self.calls.append((method, path, payload))
        if self.unavailable:
            raise TimeoutError("controlled unavailable worker")
        is_ack = path.endswith("/publication-ack")
        if is_ack and self.lose_ack_before_once:
            self.lose_ack_before_once = False
            raise TimeoutError("controlled ack request loss")
        response = self.worker_http.request(method, path, headers=headers, json=payload)
        if method == "PUT" and self.lose_put_response_once:
            self.lose_put_response_once = False
            raise TimeoutError("controlled accepted PUT response loss")
        if is_ack and self.lose_ack_after_once:
            self.lose_ack_after_once = False
            raise TimeoutError("controlled committed ack response loss")
        return response.status_code, dict(response.headers), response.content

    def count(self, method, suffix=None):
        return sum(m == method and (suffix is None or path.endswith(suffix)) for m, path, _ in self.calls)


class FixtureRuntime:
    """No model/process/network: writes C's explicitly synthetic valid outputs."""
    ready = True
    capabilities = {"fixture_only": True, "real_pi_verified": False}

    def __init__(self):
        self.calls = []
        self.mutate = None
        self.last_report_bytes = None
        self.last_workspace = None

    def health(self):
        return {"ready": True, "fixture_only": True}

    def execute(self, job, workspace, event_sink, cancel_signal):
        if cancel_signal.is_set():
            raise RuntimeError("fixture attempt unexpectedly cancelled")
        self.calls.append(dict(job))
        self.last_workspace = Path(workspace)
        fixture = research_fixtures.abstract_only()
        # Include multibyte characters and preserve explicit CRLF bytes end to end.
        fixture["report"] += "\nFixture note: 科研证据为合成测试数据。\n"
        research_fixtures.write_fixture(workspace, fixture)
        self.last_report_bytes = fixture["report"].replace("\n", "\r\n").encode("utf-8")
        (workspace / "report.md").write_bytes(self.last_report_bytes)
        now = utc_now()
        provenance = {"model": "fixture-no-model-call", "pi_version": "fixture-no-pi-process", "node_version": "fixture-no-node-process", "image_digest": "sha256:" + "0" * 64, "skill_bundle_revision": job["skill_bundle_revision"], "runtime_revision": job["runtime_revision"], "retrieval_started_at": now, "retrieval_finished_at": now, "fixture_only": True, "token_usage": None}
        (workspace / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
        if self.mutate:
            self.mutate(workspace, fixture)
        event_sink({"stage": "write"})


class IntegrationHarness:
    TOKEN = "local-integration-worker-token-not-a-secret"
    RUNTIME_REVISION = "fixture-runtime-v1"

    def __init__(self, runtime=None, runtime_revision=None):
        self.stack = ExitStack()
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.db = SharedSQLFacade()
        # Close database before deleting temp directories; no background runner is used.
        self.stack.callback(self.db.connection.close)
        self.runtime = runtime if runtime is not None else FixtureRuntime()
        self.RUNTIME_REVISION = runtime_revision or self.RUNTIME_REVISION
        self.store = QueueStore(self.root / "control" / "queue.sqlite")
        settings = WorkerSettings(runtime_dir=self.root, worker_token=self.TOKEN,
                                  skill_bundle_revision=BUNDLE_REVISION,
                                  runtime_revision=self.RUNTIME_REVISION,
                                  start_scheduler=False)
        self.worker_app = create_worker_app(settings, store=self.store, runtime=self.runtime)
        self.worker_http = self.stack.enter_context(TestClient(self.worker_app))
        self.transport = WorkerTransport(self.worker_http)
        self.worker = PiClient("http://worker.test", self.TOKEN, self.transport)
        self.projection = PiProjection(self.db, self.worker, BUNDLE_REVISION, self.RUNTIME_REVISION,
                                       notifier=lambda *args: (_ for _ in ()).throw(AssertionError("email must not be sent")))
        self.public_app = create_public_app(self.db, self.worker, self.projection, start_reconciler=False)
        self.public_http = self.stack.enter_context(TestClient(self.public_app))
        self.public_http.headers['X-Forwarded-User'] = 'trusted-fixture-user'
        self.scheduler = Scheduler(self.store, self.runtime, self.root)

    def close(self):
        # Artifacts are intentionally made read-only by the real controller. Make
        # only this temporary harness tree writable so Windows can remove it.
        if os.name == "nt":
            for path in self.root.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    path.chmod(0o600)
        self.stack.close()

    def submit(self, key="integration-task", user="trusted-fixture-user", **overrides):
        payload = {"topic": "Controlled fixture research 科研", "user_id": user,
                   "engine": "pi", "mode": "review", "timeout_seconds": 1800,
                   "email": ""}
        payload.update(overrides)
        return self.public_http.post("/scientific-research", json=payload,
                                     headers={"Idempotency-Key": key,"X-Forwarded-User": user})

    def execute_next(self):
        claim = self.store.claim()
        if claim is None:
            raise AssertionError("expected a queued attempt")
        self.scheduler.run_claim(claim)
        return self.store.get(claim["snapshot"]["id"])

    def restart_projection(self):
        # Same durable API index/shared report SQL, new recovery service object.
        self.projection = PiProjection(self.db, self.worker, BUNDLE_REVISION, self.RUNTIME_REVISION)
        self.projection.initialize()
        return self.projection
