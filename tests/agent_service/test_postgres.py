"""Opt-in real PostgreSQL semantics; only a dedicated *_agent_fixture database.

The runner supplies AGENT_TEST_DATABASE_URL. These tests create a unique schema
inside that explicitly disposable database and drop only that schema. They never
find a database, read production settings, or start a database/model themselves.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import multiprocessing
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from agent_service.contracts import ServiceError
from agent_service.store import Store


class Clock:
    def __init__(self):
        self.now = 2000.0

    def __call__(self):
        return self.now


def _request(**changes):
    return {"api_version": "1", "profile": "workspace-task.v1", "profile_revision": "r1",
            "business_ref": "fixture-project-1", "task": {"prompt": "Fixture request"},
            "inputs": [], "timeout_seconds": 600, **changes}


def _pins(engine="pi"):
    return {"profile": "workspace-task.v1", "profile_revision": "r1", "engine": engine,
            "runtime_revision": "fixture-r1", "image_digest": "sha256:" + "a" * 64}


def _submit(store, key="one", **changes):
    return store.submit("fixture-tenant", "ppt", key, _request(**changes), _pins())[0]


def _finish(store, job, *, stopped=True, state="completed", artifacts=None):
    return store.finish(job["id"], job["attempt_id"], job["fence"],
                        {"state": state, "usage": None, "error_code": None,
                         "stop_receipt": {"kind": "explicit_pg_fixture"}},
                        artifacts or [], stopped=stopped)


@pytest.fixture
def pg_url():
    raw = os.environ.get("AGENT_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("AGENT_TEST_DATABASE_URL not configured; no PostgreSQL test was run")
    try:
        url = make_url(raw)
    except Exception:
        pytest.fail("Invalid explicit PostgreSQL fixture URL", pytrace=False)
    if url.get_backend_name() != "postgresql" or not (url.database or "").endswith("_agent_fixture"):
        pytest.fail("PostgreSQL tests require a dedicated *_agent_fixture database", pytrace=False)
    # A randomly named, SQL-identifier-safe schema isolates each test even when
    # several agents use the same disposable fixture database concurrently.
    schema = "agent_fixture_" + uuid.uuid4().hex
    admin = create_engine(url, pool_pre_ping=True)
    try:
        with admin.begin() as connection:
            connection.execute(text('CREATE SCHEMA "' + schema + '"'))
        scoped = url.update_query_dict({"options": "-csearch_path=" + schema})
        yield scoped.render_as_string(hide_password=False)
    finally:
        with admin.begin() as connection:
            connection.execute(text('DROP SCHEMA IF EXISTS "' + schema + '" CASCADE'))
        admin.dispose()


@pytest.fixture
def pg_store(pg_url):
    clock = Clock()
    store = Store(pg_url, clock=clock)
    store.initialize()
    yield store, clock
    store.close()


def _claim_process(url, ready, release, output):
    """Fresh process: no inherited SQLAlchemy pool or Python synchronization lock."""
    store = None
    try:
        store = Store(url, clock=Clock())
        ready.put(True)
        if not release.wait(15):
            raise RuntimeError("fixture_release_timeout")
        value = store.claim()
        output.put({"job_id": None if value is None else value["job_id"],
                    "attempt_id": None if value is None else value["attempt_id"]})
    except BaseException as exc:
        # Deliberately do not send exception strings/URLs/credentials to stdout.
        output.put({"error_type": type(exc).__name__})
    finally:
        if store is not None:
            store.close()


def test_pg_cross_connection_idempotency_and_frozen_routes(pg_store, pg_url):
    store, clock = pg_store
    peer = Store(pg_url, clock=clock)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(lambda item: item.submit("fixture-tenant", "ppt", "same-key",
                                                           _request(), _pins()), [store, peer]))
        assert len({job["id"] for job, created in replies}) == 1
        assert sum(created for job, created in replies) == 1
        original = replies[0][0]
        clock.now += 120
        repeated, created = peer.submit("fixture-tenant", "ppt", "same-key", _request(), _pins("hermes"))
        assert created is False
        assert repeated["deadline_at"] == original["deadline_at"]
        assert repeated["pins"] == original["pins"]
        with pytest.raises(ServiceError) as exc:
            peer.submit("fixture-tenant", "ppt", "same-key", _request(business_ref="different"), _pins())
        assert exc.value.code == "idempotency_conflict"
    finally:
        peer.close()


def test_pg_separate_process_claim_has_one_winner(pg_store, pg_url):
    store, clock = pg_store
    _submit(store, "one")
    _submit(store, "two")
    ctx = multiprocessing.get_context("spawn")
    ready, release, output = ctx.Queue(), ctx.Event(), ctx.Queue()
    children = [ctx.Process(target=_claim_process, args=(pg_url, ready, release, output)) for _ in range(2)]
    try:
        for child in children:
            child.start()
        assert [ready.get(timeout=15) for _ in children] == [True, True]
        release.set()
        replies = [output.get(timeout=15) for _ in children]
        assert not any("error_type" in reply for reply in replies)
        assert sum(reply["job_id"] is not None for reply in replies) == 1
        active = store.active()
        assert active["attempt_id"] == next(reply["attempt_id"] for reply in replies if reply["job_id"] is not None)
        assert active["state"] == "starting"
        _finish(store, active)
        following = store.claim()
        assert following["id"] != active["id"]
        assert following["fence"] > active["fence"]
    finally:
        release.set()
        for child in children:
            if child.pid is not None:
                child.join(timeout=5)
                if child.is_alive():
                    child.terminate()
                    child.join(timeout=5)
        ready.close(); output.close()


def test_pg_cancel_completion_race_does_not_export_late_result(pg_store, pg_url):
    store, clock = pg_store
    _submit(store)
    attempt = store.claim()
    peer = Store(pg_url, clock=clock)
    try:
        assert peer.cancel("fixture-tenant", "ppt", attempt["id"])["state"] == "cancelling"
        result = _finish(store, attempt, artifacts=[{"name": "late.txt", "content": b"late"}])
        assert result["state"] == "cancelled"
        assert peer.active() is None
        assert peer.get_artifacts("fixture-tenant", "ppt", attempt["id"]) == []
        assert peer.cancel("fixture-tenant", "ppt", attempt["id"])["event_cursor"] == result["event_cursor"]
    finally:
        peer.close()


def test_pg_expired_lease_quarantine_survives_reconnect(pg_store, pg_url):
    store, clock = pg_store
    _submit(store, "one")
    _submit(store, "two")
    attempt = store.claim(lease_seconds=10)
    clock.now += 11
    peer = Store(pg_url, clock=clock)
    try:
        assert peer.reconcile_expired() == 1
        assert store.reconcile_expired() == 0
        assert store.active()["state"] == "quarantined"
        assert peer.claim() is None
        assert peer.renew(attempt["id"], attempt["attempt_id"], attempt["fence"]) is False
        with pytest.raises(ServiceError) as exc:
            _finish(peer, attempt)
        assert exc.value.code == "stale_attempt"
        assert peer.get_artifacts("fixture-tenant", "ppt", attempt["id"]) == []
    finally:
        peer.close()


def test_pg_artifact_input_and_events_are_durable_and_owner_scoped(pg_store, pg_url):
    store, clock = pg_store
    source = store.put_input("fixture-tenant", "ppt", "source.txt", b"immutable source")
    _submit(store, inputs=[{"input_id": source["id"], "sha256": source["sha256"]}])
    attempt = store.claim()
    assert store.mark_running(attempt["id"], attempt["attempt_id"], attempt["fence"])
    assert store.append_event(attempt["id"], attempt["attempt_id"], attempt["fence"],
                              "engine.progress", {"event": "turn_end"})
    final = _finish(store, attempt, artifacts=[{"name": "result.txt", "content": b"durable result"}])
    peer = Store(pg_url, clock=clock)
    try:
        assert peer.get_input("fixture-tenant", "ppt", source["id"])["content"] == b"immutable source"
        listing = peer.get_artifacts("fixture-tenant", "ppt", attempt["id"])
        assert len(listing) == 1
        item = peer.get_artifact("fixture-tenant", "ppt", listing[0]["id"])
        assert item["content"] == b"durable result"
        assert item["sha256"] == hashlib.sha256(b"durable result").hexdigest()
        assert peer.get_job("fixture-tenant", "ppt", attempt["id"])["outcome"]["usage"] is None
        with pytest.raises(ServiceError) as exc:
            peer.get_artifact("other-tenant", "ppt", item["id"])
        assert exc.value.status == 404
        cursor, recorded = 0, []
        while True:
            page = peer.events("fixture-tenant", "ppt", attempt["id"], cursor, 2)
            recorded += page["items"]
            cursor = page["next_cursor"]
            if not page["has_more"]:
                break
        assert len(recorded) == final["event_cursor"]
        assert [item["seq"] for item in recorded] == list(range(1, len(recorded) + 1))
        assert recorded[-1]["type"] == "job.completed"
        assert peer.active() is None
    finally:
        peer.close()


def test_pg_no_stop_proof_keeps_slot_across_connection(pg_store, pg_url):
    store, clock = pg_store
    _submit(store, "one")
    _submit(store, "two")
    attempt = store.claim()
    _finish(store, attempt, stopped=False, artifacts=[{"name": "unsafe.txt", "content": b"unsafe"}])
    peer = Store(pg_url, clock=clock)
    try:
        assert peer.active()["attempt_id"] == attempt["attempt_id"]
        assert peer.active()["state"] == "quarantined"
        assert peer.claim() is None
        assert peer.get_artifacts("fixture-tenant", "ppt", attempt["id"]) == []
    finally:
        peer.close()
