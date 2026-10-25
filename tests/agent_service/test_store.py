"""SQLite verifies store semantics, never PostgreSQL locking/production isolation."""
from concurrent.futures import ThreadPoolExecutor
import hashlib

import pytest

from agent_service.contracts import ServiceError
from agent_service.store import Store


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    value = Store("sqlite:///" + str(tmp_path / "agent.db"), allow_sqlite=True, clock=clock)
    value.initialize()
    yield value
    value.close()


def request(**changes):
    return {"api_version": "1", "profile": "workspace-task.v1", "profile_revision": "r1",
            "business_ref": "business-opaque-1", "task": {"prompt": "Read the given text"},
            "inputs": [], "timeout_seconds": 600, **changes}


def pins(engine="pi"):
    return {"profile": "workspace-task.v1", "profile_revision": "r1", "engine": engine,
            "runtime_revision": "reviewed-r1", "image_digest": "sha256:" + "a" * 64}


def submit(store, key="one", **changes):
    return store.submit("tenant-a", "ppt", key, request(**changes), pins())[0]


def outcome(state="completed", **changes):
    return {"state": state, "error_code": None, "usage": None,
            "stop_receipt": {"authenticated": "test-only"}, **changes}


def finish(store, claimed, state="completed", files=None, stopped=True):
    return store.finish(claimed["id"], claimed["attempt_id"], claimed["fence"],
                        outcome(state), files or [], stopped=stopped)


def test_sqlite_requires_explicit_opt_in():
    with pytest.raises(ServiceError) as exc:
        Store("sqlite:///:memory:")
    assert exc.value.code == "postgresql_required"
    assert exc.value.status == 503


def test_idempotent_replay_freezes_deadline_and_engine(store, clock):
    original = submit(store)
    clock.now += 100
    repeated, created = store.submit("tenant-a", "ppt", "one", request(), pins("hermes"))
    assert created is False
    assert repeated["id"] == original["id"]
    assert repeated["deadline_at"] == 1600
    assert repeated["pins"]["engine"] == "pi"
    assert repeated["event_cursor"] == 1
    assert store.replay("tenant-a", "ppt", "one", request())["id"] == original["id"]
    with pytest.raises(ServiceError) as exc:
        store.lookup_submission("tenant-a", "ppt", "one", request(business_ref="changed"))
    assert exc.value.code == "idempotency_conflict"


def test_idempotency_scope_does_not_share_jobs(store):
    one = submit(store)
    other = store.submit("tenant-a", "research", "one", request(), pins())[0]
    assert one["id"] != other["id"]
    with pytest.raises(ServiceError) as exc:
        store.get_job("tenant-a", "research", one["id"])
    assert exc.value.status == 404
    with pytest.raises(ServiceError):
        store.events("tenant-b", "ppt", one["id"])
    with pytest.raises(ServiceError):
        store.cancel("tenant-b", "ppt", one["id"])


def test_input_hash_owner_and_replay_are_rechecked(store):
    item = store.put_input("tenant-a", "ppt", "材料.txt", b"source")
    ref = {"input_id": item["id"], "sha256": item["sha256"]}
    doc = request(inputs=[ref])
    job = store.submit("tenant-a", "ppt", "one", doc, pins())[0]
    assert store.get_input("tenant-a", "ppt", item["id"])["content"] == b"source"
    for tenant, caller in [("tenant-b", "ppt"), ("tenant-a", "research")]:
        with pytest.raises(ServiceError) as exc:
            store.submit(tenant, caller, "one", doc, pins())
        assert exc.value.status == 404
    altered = request(inputs=[{**ref, "sha256": "b" * 64}])
    with pytest.raises(ServiceError) as exc:
        store.lookup_submission("tenant-a", "ppt", "one", altered)
    assert exc.value.code == "input_hash_mismatch"
    assert store.get_job("tenant-a", "ppt", job["id"])["state"] == "queued"


@pytest.mark.parametrize("name", ["../a", "a/b", "a\\b", "C:a", "..", "a\x00b"])
def test_input_file_names_cannot_be_paths(store, name):
    with pytest.raises(ServiceError):
        store.put_input("tenant-a", "ppt", name, b"payload")


def test_input_bound_and_empty_input(store):
    for content in [b"", b"a" * (store.max_blob_bytes + 1), "not bytes"]:
        with pytest.raises(ServiceError) as exc:
            store.put_input("tenant-a", "ppt", "input.txt", content)
        assert exc.value.status == 413


def test_single_claim_across_independent_connections(store, tmp_path, clock):
    submit(store, "one")
    submit(store, "two")
    other = Store("sqlite:///" + str(tmp_path / "agent.db"), allow_sqlite=True, clock=clock)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(lambda item: item.claim(), [store, other]))
        winners = [value for value in values if value is not None]
        assert len(winners) == 1
        winner = winners[0]
        assert winner["state"] == "starting"
        assert winner["attempt_id"] and winner["fence"] == 1
        assert other.active()["attempt_id"] == winner["attempt_id"]
        assert store.mark_running(winner["id"], winner["attempt_id"], winner["fence"]) is True
        finish(store, winner)
        second = other.claim()
        assert second["id"] != winner["id"]
        assert second["fence"] == 2
        assert second["attempt_id"] != winner["attempt_id"]
    finally:
        other.close()


def test_queued_cancel_is_terminal_without_attempt_and_is_idempotent(store):
    job = submit(store)
    cancelled = store.cancel("tenant-a", "ppt", job["id"])
    assert cancelled["state"] == "cancelled" and cancelled["attempt_id"] is None
    assert store.cancel("tenant-a", "ppt", job["id"])["event_cursor"] == cancelled["event_cursor"]
    assert store.claim() is None


def test_running_cancel_requires_stopped_writers_and_wins_completion(store):
    job = submit(store)
    claimed = store.claim()
    assert store.mark_running(job["id"], claimed["attempt_id"], claimed["fence"])
    stopping = store.cancel("tenant-a", "ppt", job["id"])
    assert stopping["state"] == "cancelling"
    assert store.mark_running(job["id"], claimed["attempt_id"], claimed["fence"]) is False
    result = finish(store, claimed, files=[{"name": "result.txt", "content": b"late result"}])
    assert result["state"] == "cancelled"
    assert result["outcome"]["usage"] is None
    assert store.get_artifacts("tenant-a", "ppt", job["id"]) == []


def test_missing_stop_evidence_quarantines_and_never_releases_slot(store):
    submit(store, "one")
    submit(store, "two")
    claimed = store.claim()
    result = finish(store, claimed, files=[{"name": "output.txt", "content": b"unsafe"}], stopped=False)
    assert result["state"] == "quarantined"
    assert result["outcome"]["error_code"] == "stop_unverified"
    assert store.active()["attempt_id"] == claimed["attempt_id"]
    assert store.claim() is None
    assert store.get_artifacts("tenant-a", "ppt", claimed["id"]) == []
    with pytest.raises(ServiceError) as exc:
        finish(store, claimed)
    assert exc.value.code == "stale_attempt"


def test_expired_lease_cannot_be_renewed_or_late_committed(store, clock):
    submit(store)
    claimed = store.claim(lease_seconds=10)
    clock.now += 11
    assert store.renew(claimed["id"], claimed["attempt_id"], claimed["fence"]) is False
    assert store.append_event(claimed["id"], claimed["attempt_id"], claimed["fence"],
                              "engine.progress", {"event": "late"}) is False
    result = finish(store, claimed)
    assert result["state"] == "quarantined"
    assert store.reconcile_expired() == 0


def test_reconciliation_quarantines_live_attempt_without_retry(store, clock):
    submit(store)
    claimed = store.claim(lease_seconds=10)
    clock.now += 11
    assert store.reconcile_expired() == 1
    assert store.reconcile_expired() == 0
    assert store.get_job("tenant-a", "ppt", claimed["id"])["state"] == "quarantined"
    assert store.claim() is None


def test_stale_fence_and_attempt_cannot_update_or_publish(store):
    submit(store)
    claimed = store.claim()
    wrong_fence = claimed["fence"] + 1
    assert store.mark_running(claimed["id"], claimed["attempt_id"], wrong_fence) is False
    assert store.renew(claimed["id"], claimed["attempt_id"], wrong_fence) is False
    with pytest.raises(ServiceError) as exc:
        store.finish(claimed["id"], claimed["attempt_id"], wrong_fence, outcome(), [], stopped=True)
    assert exc.value.code == "stale_attempt"
    assert store.get_job("tenant-a", "ppt", claimed["id"])["state"] == "starting"


def test_deadline_before_launch_skips_job_without_spawning(store, clock):
    first = submit(store, timeout_seconds=1)
    clock.now += 2
    second = submit(store, "two")
    claimed = store.claim()
    assert claimed["id"] == second["id"]
    expired = store.get_job("tenant-a", "ppt", first["id"])
    assert expired["state"] == "timed_out" and expired["attempt_id"] is None


def test_stopping_grace_never_extends_execution_deadline(store, clock):
    submit(store, timeout_seconds=5)
    claimed = store.claim(lease_seconds=60)
    assert claimed["deadline_at"] == 1005
    assert claimed["lease_expires_at"] == 1050
    clock.now = 1006
    assert store.renew(claimed["id"], claimed["attempt_id"], claimed["fence"], 60) is True
    result = finish(store, claimed)
    assert result["state"] == "timed_out"
    assert result["deadline_at"] == 1005


def test_artifacts_are_immutable_measured_and_owner_scoped(store):
    submit(store)
    claimed = store.claim()
    finish(store, claimed, files=[{"name": "result.txt", "content": b"actual bytes"}])
    listing = store.get_artifacts("tenant-a", "ppt", claimed["id"])
    assert len(listing) == 1
    assert listing[0]["sha256"] == hashlib.sha256(b"actual bytes").hexdigest()
    artifact = store.get_artifact("tenant-a", "ppt", listing[0]["id"])
    assert artifact["content"] == b"actual bytes"
    assert artifact["attempt_id"] == claimed["attempt_id"]
    with pytest.raises(ServiceError) as exc:
        store.get_artifact("tenant-a", "research", artifact["id"])
    assert exc.value.status == 404
    with pytest.raises(ServiceError):
        finish(store, claimed, files=[{"name": "result.txt", "content": b"overwrite"}])
    assert store.get_artifact("tenant-a", "ppt", artifact["id"])["content"] == b"actual bytes"


def test_artifact_bound_and_hash_failure_do_not_commit_terminal_state(store):
    submit(store)
    claimed = store.claim()
    bad = {"name": "output.txt", "content": b"actual", "sha256": "0" * 64}
    with pytest.raises(ServiceError) as exc:
        finish(store, claimed, files=[bad])
    assert exc.value.code == "artifact_hash_mismatch"
    assert store.get_job("tenant-a", "ppt", claimed["id"])["state"] == "starting"
    with pytest.raises(ServiceError) as exc:
        finish(store, claimed, files=[{"name": "output.bin", "content": b"x" * (store.max_artifact_bytes + 1)}])
    assert exc.value.code == "artifact_size_limit"


def test_event_pagination_and_reserved_terminal_capacity(tmp_path, clock):
    store = Store("sqlite:///" + str(tmp_path / "events.db"), allow_sqlite=True, clock=clock, max_events=32)
    store.initialize()
    try:
        submit(store)
        claimed = store.claim()
        for number in range(100):
            store.append_event(claimed["id"], claimed["attempt_id"], claimed["fence"],
                               "engine.progress", {"event": "turn_end", "count": number})
        finish(store, claimed)
        cursor = 0
        kinds = []
        while True:
            page = store.events("tenant-a", "ppt", claimed["id"], after=cursor, limit=3)
            kinds += [item["type"] for item in page["items"]]
            assert [item["seq"] for item in page["items"]] == list(range(cursor + 1, page["next_cursor"] + 1))
            cursor = page["next_cursor"]
            if not page["has_more"]:
                break
        assert kinds[0] == "job.queued" and kinds[-1] == "job.completed"
        assert len(kinds) <= 32
        with pytest.raises(ServiceError):
            store.events("tenant-a", "ppt", claimed["id"], after=cursor + 1)
    finally:
        store.close()


def test_raw_engine_text_and_unknown_event_cannot_enter_public_event_log(store):
    submit(store)
    claimed = store.claim()
    for kind, payload in [("engine.output", {"text": "private"}),
                          ("engine.progress", {"text": "private"}),
                          ("engine.progress", {"event": "token and api key"})]:
        with pytest.raises(ServiceError):
            store.append_event(claimed["id"], claimed["attempt_id"], claimed["fence"], kind, payload)
    assert store.events("tenant-a", "ppt", claimed["id"])["next_cursor"] == 2
