"""Opt-in process locking for shared slots in disposable PostgreSQL schemas.

The imported fixture requires an explicitly supplied *_agent_fixture database;
without AGENT_TEST_DATABASE_URL every test safely skips before connecting.
No business settings or model credentials are loaded.
"""
import multiprocessing

import pytest

from agent_service.contracts import ServiceError
from agent_service.store import Store
from test_execution_slots import Clock, SCOPES, _finish, _submit
from test_postgres import pg_url  # Reuse the disposable-database/schema guard.


@pytest.fixture
def shared_pg_store(pg_url):
    store = Store(pg_url, clock=Clock(), execution_slots=5)
    store.initialize()
    try:
        yield store
    finally:
        store.close()


def _claim_process(url, ready, release, output):
    store = None
    try:
        store = Store(url, clock=Clock(), execution_slots=5)
        ready.put(True)
        if not release.wait(30):
            raise RuntimeError("fixture_release_timeout")
        job = store.claim()
        output.put({"job_id": None if job is None else job["id"],
                    "attempt_id": None if job is None else job["attempt_id"],
                    "fence": None if job is None else job["fence"]})
    except BaseException as exc:
        # Never include connection strings, credentials or exception messages.
        output.put({"error_type": type(exc).__name__})
    finally:
        if store is not None:
            store.close()


def test_pg_eight_processes_claim_exactly_five_distinct_attempts(shared_pg_store, pg_url):
    store = shared_pg_store
    for index in range(8):
        _submit(store, SCOPES[index % len(SCOPES)], str(index))
    context = multiprocessing.get_context("spawn")
    ready, release, output = context.Queue(), context.Event(), context.Queue()
    children = [context.Process(target=_claim_process, args=(pg_url, ready, release, output)) for _ in range(8)]
    try:
        for child in children:
            child.start()
        assert [ready.get(timeout=30) for _ in children] == [True] * len(children)
        release.set()
        replies = [output.get(timeout=30) for _ in children]
        assert not any("error_type" in reply for reply in replies)
        winners = [reply for reply in replies if reply["job_id"] is not None]
        assert len(winners) == 5
        assert {reply["fence"] for reply in winners} == {1, 2, 3, 4, 5}
        assert len({reply["attempt_id"] for reply in winners}) == 5
        assert len({reply["job_id"] for reply in winners}) == 5
        assert store.claim() is None
        active = [store.active(slot_id=slot_id) for slot_id in range(1, 6)]
        assert {job["attempt_id"] for job in active} == {reply["attempt_id"] for reply in winners}
        _finish(store, active[2])
        following = store.claim()
        assert following["fence"] == 6
        assert store.active(slot_id=3)["attempt_id"] == following["attempt_id"]
    finally:
        release.set()
        for child in children:
            if child.pid is not None:
                child.join(timeout=5)
                if child.is_alive():
                    child.terminate()
                    child.join(timeout=5)
        ready.close()
        output.close()


def test_pg_quarantine_retains_one_slot_and_same_caller_uses_other_capacity(shared_pg_store, pg_url):
    store = shared_pg_store
    for index in range(6):
        _submit(store, SCOPES[0], str(index))
    blocked = store.claim(slot_id=1)
    _finish(store, blocked, stopped=False, files=[{"name": "unsafe.txt", "content": b"unsafe"}])
    peer = Store(pg_url, clock=store.clock, execution_slots=5)
    try:
        peer.initialize()
        assert peer.active(slot_id=1)["state"] == "quarantined"
        assert peer.claim(slot_id=1) is None
        others = [peer.claim() for _ in range(4)]
        assert all(job is not None for job in others)
        assert peer.claim() is None
        _finish(peer, others[0])
        assert store.claim() is not None
        assert store.active(slot_id=1)["attempt_id"] == blocked["attempt_id"]
        assert peer.get_artifacts(*SCOPES[0], blocked["id"]) == []
    finally:
        peer.close()


def test_pg_busy_global_upgrade_preserves_owner_then_empty_upgrade_keeps_fence(pg_url):
    clock = Clock()
    legacy = Store(pg_url, clock=clock)
    modern = Store(pg_url, clock=clock, execution_slots=5)
    try:
        legacy.initialize()
        _submit(legacy, SCOPES[0], "running-before-upgrade")
        queued = _submit(legacy, SCOPES[2], "queued-before-upgrade")
        original = legacy.claim()
        with pytest.raises(ServiceError) as exc:
            modern.initialize()
        assert exc.value.code == "scheduler_busy"
        assert legacy.active()["attempt_id"] == original["attempt_id"]
        _finish(legacy, original)
        modern.initialize()
        modern.initialize()
        with pytest.raises(ServiceError) as exc:
            legacy.claim()
        assert exc.value.code == "scheduler_configuration_mismatch"
        following = modern.claim()
        assert following["id"] == queued["id"]
        assert following["fence"] > original["fence"]
    finally:
        modern.close()
        legacy.close()
