"""Shared execution capacity and durable fair scheduling, without model calls.

SQLite verifies scheduler behavior. PostgreSQL process locking is verified in
test_execution_slots_postgres.py.
"""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from agent_service.contracts import ServiceError
from agent_service.dispatcher import Dispatcher
from agent_service.store import Store
from test_kubernetes_provider import make_harness  # Real adapter, synthetic K8s/Worker HTTP.


SCOPES = (("platform", "research"), ("platform", "ppt"), ("platform", "video"))


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _request(key="one", **changes):
    return {"api_version": "1", "profile": "workspace-task.v1", "profile_revision": "r1",
            "business_ref": key, "task": {"prompt": "An offline scheduling fixture"},
            "inputs": [], "timeout_seconds": 600, **changes}


def _pins(engine="pi"):
    return {"profile": "workspace-task.v1", "profile_revision": "r1", "engine": engine,
            "runtime_revision": "fixture-r1", "image_digest": "sha256:" + "a" * 64}


def _submit(store, scope, key="one", **changes):
    job = store.submit(*scope, key, _request(key, **changes), _pins())[0]
    # Equal creation times order by UUID, so make intended FIFO explicit.
    store.clock.now += 0.01
    return job


def _finish(store, job, *, stopped=True, files=None):
    return store.finish(job["id"], job["attempt_id"], job["fence"],
        {"state": "completed", "usage": None, "error_code": None,
         "stop_receipt": {"kind": "explicit_shared_slot_fixture"}},
        files or [], stopped=stopped)


@pytest.fixture
def make_store(tmp_path):
    url = "sqlite:///" + str(tmp_path / "execution-slots.sqlite")
    clock, opened = Clock(), []

    def make(count=5, *, initialize=True):
        store = Store(url, allow_sqlite=True, clock=clock, execution_slots=count)
        opened.append(store)
        if initialize:
            store.initialize()
        return store

    yield make
    for store in reversed(opened):
        store.close()


def test_five_shared_slots_allow_same_caller_parallelism_and_sixth_waits(make_store):
    store = make_store()
    queued = [_submit(store, SCOPES[0], str(index)) for index in range(6)]
    running = [store.claim() for _ in range(5)]
    assert [job["id"] for job in running] == [job["id"] for job in queued[:5]]
    assert {job["fence"] for job in running} == {1, 2, 3, 4, 5}
    assert len({job["attempt_id"] for job in running}) == 5
    assert store.claim() is None
    assert store.active()["attempt_id"] == running[0]["attempt_id"]
    for slot_id, job in enumerate(running, 1):
        assert store.active(slot_id=slot_id)["attempt_id"] == job["attempt_id"]
        assert store.claim(slot_id=slot_id) is None
    _finish(store, running[2])
    following = store.claim()
    assert following["id"] == queued[5]["id"] and following["fence"] == 6
    assert store.active(slot_id=3)["attempt_id"] == following["attempt_id"]
    for slot_id in (1, 2, 4, 5):
        assert store.active(slot_id=slot_id)["attempt_id"] == running[slot_id - 1]["attempt_id"]


def test_explicit_slot_selects_only_its_own_capacity(make_store):
    store = make_store()
    first = _submit(store, SCOPES[0], "first")
    second = _submit(store, SCOPES[1], "second")
    claimed = store.claim(slot_id=5)
    assert claimed["id"] == first["id"]
    assert store.active(slot_id=1) is None
    assert store.active(slot_id=5)["id"] == first["id"]
    assert store.claim(slot_id=5) is None
    assert store.claim(slot_id=1)["id"] == second["id"]


def test_fair_round_robin_uses_durable_caller_history_and_each_caller_fifo(make_store):
    store = make_store()
    queued = {scope: [_submit(store, scope, str(index)) for index in range(3)] for scope in SCOPES}
    for index in range(3):
        for scope in SCOPES:
            # Reopen before every claim so fairness cannot depend on local state.
            peer = make_store(initialize=False)
            job = peer.claim()
            assert job["id"] == queued[scope][index]["id"]
            _finish(peer, job)


def test_new_platform_gets_next_free_slot_ahead_of_busy_platform_backlog(make_store):
    store = make_store()
    backlog = [_submit(store, SCOPES[0], str(index)) for index in range(6)]
    running = [store.claim() for _ in range(5)]
    newcomer = _submit(store, SCOPES[1], "new-platform")
    _finish(store, running[0])
    assert store.claim()["id"] == newcomer["id"]
    _finish(store, running[1])
    assert store.claim()["id"] == backlog[5]["id"]


@pytest.mark.parametrize("cause", ["stop_unverified", "lease_expired"])
def test_quarantine_holds_only_one_slot_and_same_caller_can_use_other_four(make_store, cause):
    store = make_store()
    for index in range(7):
        _submit(store, SCOPES[0], str(index))
    blocked = store.claim(slot_id=1, lease_seconds=1)
    if cause == "stop_unverified":
        _finish(store, blocked, stopped=False, files=[{"name": "unsafe.txt", "content": b"unsafe"}])
    else:
        store.clock.now += 2
        assert store.reconcile_expired() == 1
    assert store.active(slot_id=1)["state"] == "quarantined"
    assert store.claim(slot_id=1) is None
    assert store.get_artifacts(*SCOPES[0], blocked["id"]) == []
    running = [store.claim() for _ in range(4)]
    assert all(job is not None for job in running)
    assert store.claim() is None
    for job in running:
        _finish(store, job)
    assert store.claim() is not None
    assert store.active(slot_id=1)["attempt_id"] == blocked["attempt_id"]


def test_cancel_or_finish_releases_only_its_own_slot(make_store):
    store = make_store()
    for scope in SCOPES:
        _submit(store, scope)
    running = [store.claim() for _ in SCOPES]
    cancelled = running[0]
    assert store.cancel(*SCOPES[0], cancelled["id"])["state"] == "cancelling"
    assert _finish(store, cancelled, files=[{"name": "late.txt", "content": b"late"}])["state"] == "cancelled"
    assert store.active(slot_id=1) is None
    assert store.get_artifacts(*SCOPES[0], cancelled["id"]) == []
    _finish(store, running[1])
    assert store.active(slot_id=2) is None
    assert store.active(slot_id=3)["attempt_id"] == running[2]["attempt_id"]


def test_late_finish_or_wrong_fence_cannot_release_reused_or_other_slots(make_store):
    store = make_store()
    for index in range(6):
        _submit(store, SCOPES[0], str(index))
    running = [store.claim() for _ in range(5)]
    _finish(store, running[0])
    following = store.claim()
    for stale in (running[0], {**following, "fence": running[1]["fence"]}):
        with pytest.raises(ServiceError) as exc:
            _finish(store, stale)
        assert exc.value.code == "stale_attempt"
    assert store.active(slot_id=1)["attempt_id"] == following["attempt_id"]
    for slot_id, job in enumerate(running[1:], 2):
        assert store.active(slot_id=slot_id)["attempt_id"] == job["attempt_id"]


def test_eight_connections_have_exactly_five_winners_and_unique_global_fences(make_store):
    store = make_store()
    for index in range(8):
        _submit(store, SCOPES[index % len(SCOPES)], str(index))
    peers = [make_store(initialize=False) for _ in range(8)]
    barrier = threading.Barrier(len(peers))

    def claim(peer):
        barrier.wait(timeout=10)
        return peer.claim()

    with ThreadPoolExecutor(max_workers=len(peers)) as pool:
        replies = list(pool.map(claim, peers))
    winners = [job for job in replies if job is not None]
    assert len(winners) == 5
    assert {job["fence"] for job in winners} == {1, 2, 3, 4, 5}
    assert len({job["attempt_id"] for job in winners}) == 5
    active = [store.active(slot_id=slot_id) for slot_id in range(1, 6)]
    assert {job["id"] for job in active} == {job["id"] for job in winners}


def test_restart_preserves_all_occupied_slots_and_global_fence(make_store):
    store = make_store()
    for index in range(10):
        _submit(store, SCOPES[0], str(index))
    running = [store.claim() for _ in range(5)]
    restarted = make_store()
    restarted.initialize()
    for slot_id, job in enumerate(running, 1):
        assert restarted.active(slot_id=slot_id)["attempt_id"] == job["attempt_id"]
        assert restarted.claim(slot_id=slot_id) is None
        _finish(restarted, job)
    assert {restarted.claim()["fence"] for _ in range(5)} == {6, 7, 8, 9, 10}


def test_five_live_kubernetes_attempts_have_distinct_agent_helper_uids_and_finish_independently(make_harness):
    h = make_harness()
    h.store.close()
    h.store = Store(h.url, allow_sqlite=True, clock=h.clock, execution_slots=5)
    h.store.initialize()
    h.rebuild_control()
    queued = [h.submit(str(index)) for index in range(5)]
    dispatchers = [Dispatcher(h.store, {h.pool.policy.engine: h.adapter},
                              clock=h.clock, slot_id=slot_id) for slot_id in range(1, 6)]
    running = [dispatcher.dispatch_once() for dispatcher in dispatchers]
    assert all(job["state"] == "running" for job in running)
    assert {job["id"] for job in running} == {job["id"] for job in queued}
    allocations = [h.allocation(job) for job in running]
    assert len({value["pod_uid"] for value in allocations}) == 5
    assert len({value["pod_name"] for value in allocations}) == 5
    assert len({value[key] for value in allocations for key in ("agent_uid", "aux_uid")}) == 10
    for job, allocation in zip(running, allocations):
        environment = {item["name"]: item.get("value")
                       for item in h.pod(job)["spec"]["containers"][0]["env"]}
        assert environment["AGENT_RUNTIME_UID"] == str(allocation["agent_uid"])
        assert environment["AGENT_PREVIEW_UID"] == str(allocation["aux_uid"])
        assert allocation["worker_start_claimed"] is True
    assert len(h.worker.puts()) == 5
    assert len(h.kube.objects["pods"]) == len(h.kube.objects["secrets"]) == 5

    for index in reversed(range(5)):
        h.worker.complete(running[index]["attempt_id"])
        assert dispatchers[index].poll(running[index])["state"] == "completed"
        assert h.store.active(slot_id=index + 1) is None
        for still_running in range(index):
            assert h.store.active(slot_id=still_running + 1)["attempt_id"] == running[still_running]["attempt_id"]
        assert h.allocation(running[index])["retired"] is True
    assert h.kube.objects["pods"] == h.kube.objects["secrets"] == {}
    assert h.store.active_jobs() == []
    assert len(h.worker.puts()) == 5


@pytest.mark.parametrize("state", ["starting", "running", "quarantined"])
def test_busy_single_slot_refuses_upgrade_and_original_scheduler_can_continue(make_store, state):
    legacy = make_store(1)
    _submit(legacy, SCOPES[0], "old")
    original = legacy.claim()
    if state == "running":
        assert legacy.mark_running(original["id"], original["attempt_id"], original["fence"])
    elif state == "quarantined":
        _finish(legacy, original, stopped=False)
    upgraded = make_store(initialize=False)
    with pytest.raises(ServiceError) as exc:
        upgraded.initialize()
    assert exc.value.code == "scheduler_busy"
    assert legacy.active()["attempt_id"] == original["attempt_id"]
    assert legacy.active()["state"] == state
    assert legacy.claim() is None
    if state != "quarantined":
        assert _finish(legacy, original)["state"] == "completed"
        upgraded.initialize()
        _submit(upgraded, SCOPES[1], "after-upgrade")
        assert upgraded.claim()["fence"] > original["fence"]


def test_busy_layout_cannot_shrink_until_every_slot_is_idle(make_store):
    store = make_store()
    _submit(store, SCOPES[0])
    occupied = store.claim(slot_id=5)
    reduced = make_store(1, initialize=False)
    with pytest.raises(ServiceError) as exc:
        reduced.initialize()
    assert exc.value.code == "scheduler_busy"
    assert store.active(slot_id=5)["attempt_id"] == occupied["attempt_id"]
    _finish(store, occupied)
    reduced.initialize()
    _submit(reduced, SCOPES[1], "after-shrink")
    assert reduced.claim()["fence"] > occupied["fence"]


def test_stale_configuration_cannot_claim_or_report_partial_layout(make_store):
    old = make_store(1)
    _submit(old, SCOPES[0], "still-queued")
    current = make_store()
    for operation in (old.claim, old.active):
        with pytest.raises(ServiceError) as exc:
            operation()
        assert exc.value.code == "scheduler_configuration_mismatch"
    assert current.claim() is not None


def test_extra_capacity_does_not_extend_deadlines_or_change_idempotency(make_store):
    store = make_store()
    expired = _submit(store, SCOPES[0], "short", timeout_seconds=1)
    original = _submit(store, SCOPES[1], "replayed", timeout_seconds=40)
    store.clock.now += 2
    replay, created = store.submit(*SCOPES[1], "replayed", _request("replayed", timeout_seconds=40), _pins("hermes"))
    assert created is False
    assert replay["id"] == original["id"]
    assert replay["deadline_at"] == original["deadline_at"]
    assert replay["pins"] == original["pins"]
    running = store.claim()
    assert running["id"] == original["id"]
    assert running["deadline_at"] == original["deadline_at"]
    expired = store.get_job(*SCOPES[0], expired["id"])
    assert expired["state"] == "timed_out" and expired["attempt_id"] is None
    with pytest.raises(ServiceError) as exc:
        store.submit(*SCOPES[1], "replayed", _request("replayed", timeout_seconds=400), _pins())
    assert exc.value.code == "idempotency_conflict"


@pytest.mark.parametrize("slot_id", [0, 6, -1, True, 1.0, "1", ()])
def test_invalid_slot_cannot_select_capacity_outside_configured_layout(make_store, slot_id):
    store = make_store()
    for operation in (store.claim, store.active):
        with pytest.raises(ValueError):
            operation(slot_id=slot_id)


@pytest.mark.parametrize("count", [0, -1, 17, True, 5.0, "5", None, ()])
def test_slot_capacity_requires_a_bounded_integer(tmp_path, count):
    with pytest.raises(ValueError):
        Store("sqlite:///" + str(tmp_path / "invalid.sqlite"), allow_sqlite=True, execution_slots=count)
