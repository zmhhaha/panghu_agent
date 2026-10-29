"""SQLite verifies store semantics, never PostgreSQL locking/production isolation."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import threading

import pytest

from agent_service.contracts import ExecutionEnvelope, InlineFile, ServiceError, WorkerResult, sign_receipt
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


@pytest.mark.parametrize("name", ["../a", "a\\b", "C:a", "..", "a\x00b"])
def test_input_file_names_reject_unsafe_paths(store, name):
    with pytest.raises(ServiceError):
        store.put_input("tenant-a", "ppt", name, b"payload")


def test_input_bound_and_empty_input(store):
    assert store.put_input("tenant-a", "ppt", "inputs/empty.bin", b"")['size'] == 0
    for content in [b"a" * (store.max_blob_bytes + 1), "not bytes"]:
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


def allocation_envelope(job, **changes):
    return ExecutionEnvelope(job_id=job["id"], attempt_id=job["attempt_id"], fence=job["fence"],
        engine=job["pins"]["engine"], profile=job["request"]["profile"],
        profile_revision=job["request"]["profile_revision"], runtime_revision=job["pins"]["runtime_revision"],
        image_digest=job["pins"]["image_digest"], deadline_at=float(job["deadline_at"]),
        lease_expires_at=float(job["lease_expires_at"]), task=job["request"]["task"], **changes)


def reserve_allocation(store, envelope):
    return store.reserve_worker(envelope, "pi-pool", "agent-service", "agent-" + envelope.attempt_id, "a" * 64)


def staged_result(envelope, now):
    result = WorkerResult(state="completed", artifacts=[InlineFile.from_bytes("result.txt", b"candidate")])
    return result.model_copy(update={"stop_receipt": sign_receipt(envelope, result,
        b"fixture-only-receipt-key-0000000000", ("pod-uid-1",), now)}).model_dump()


def test_worker_allocation_freezes_intent_and_measures_request_from_database(store):
    submit(store)
    job = store.claim()
    envelope = allocation_envelope(job)
    first = reserve_allocation(store, envelope)
    assert first == reserve_allocation(store, envelope)
    assert set(first) == {"attempt_id", "job_id", "fence", "engine", "request_sha256", "pool_id",
                          "namespace", "pod_name", "intent_sha256", "pod_uid", "create_claimed",
                          "retired", "worker_start_claimed", "candidate_result", "stop_observed_at", "created_at",
                          "agent_uid", "aux_uid"}
    assert first["request_sha256"] == envelope.request_sha256
    assert first["create_claimed"] is False and first["pod_uid"] is None
    with pytest.raises(ServiceError) as exc:
        store.reserve_worker(envelope, "other-pool", "agent-service", first["pod_name"], "a" * 64)
    assert exc.value.code == "worker_allocation_conflict"
    for changed in ({"task": {"prompt": "different task"}}, {"runtime_revision": "different-runtime"},
                    {"deadline_at": envelope.deadline_at + 1},
                    {"lease_expires_at": envelope.lease_expires_at + 1}):
        with pytest.raises(ServiceError) as exc:
            reserve_allocation(store, envelope.model_copy(update=changed))
        assert exc.value.code == "worker_allocation_conflict"
    assert store.get_worker_allocation(envelope.attempt_id) == first


def test_worker_allocation_identity_binds_measured_input_bytes(store):
    source = store.put_input("tenant-a", "ppt", "source.txt", b"given source")
    submit(store, inputs=[{"input_id": source["id"], "sha256": source["sha256"]}])
    job = store.claim()
    envelope = allocation_envelope(job, inputs=[InlineFile.from_bytes("source.txt", b"given source")])
    tampered = envelope.model_copy(update={"inputs": [InlineFile.from_bytes("source.txt", b"substitution")]})
    with pytest.raises(ServiceError) as exc:
        reserve_allocation(store, tampered)
    assert exc.value.code == "worker_allocation_conflict"
    assert reserve_allocation(store, envelope)["request_sha256"] == envelope.request_sha256


def test_worker_create_claim_is_once_across_connections_and_uid_cannot_be_rebound(store, clock):
    submit(store)
    envelope = allocation_envelope(store.claim())
    reserve_allocation(store, envelope)
    peer = Store(str(store.engine.url), allow_sqlite=True, clock=clock)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda s: s.claim_worker_create(envelope.attempt_id, envelope.request_sha256),
                                   [store, peer]))
        assert sorted(claims) == [False, True]
        assert peer.claim_worker_create(envelope.attempt_id, envelope.request_sha256) is False
        bound = peer.bind_worker(envelope.attempt_id, envelope.request_sha256, "pod-uid-1")
        assert store.bind_worker(envelope.attempt_id, envelope.request_sha256, "pod-uid-1") == bound
        with ThreadPoolExecutor(max_workers=2) as pool:
            native_claims = list(pool.map(lambda s: s.claim_worker_start(envelope.attempt_id, envelope.request_sha256),
                                          [store, peer]))
        assert sorted(native_claims) == [False, True]
        with pytest.raises(ServiceError) as exc:
            store.bind_worker(envelope.attempt_id, envelope.request_sha256, "new-pod-under-same-name")
        assert exc.value.code == "worker_allocation_conflict"
        assert peer.get_worker_allocation(envelope.attempt_id)["pod_uid"] == "pod-uid-1"
    finally:
        peer.close()


def test_worker_candidate_stop_observation_and_retirement_survive_finish(store, clock):
    submit(store)
    job = store.claim()
    envelope = allocation_envelope(job)
    reserve_allocation(store, envelope)
    with pytest.raises(ServiceError):
        store.bind_worker(envelope.attempt_id, envelope.request_sha256, "pod-uid-1")
    assert store.claim_worker_create(envelope.attempt_id, envelope.request_sha256)
    store.bind_worker(envelope.attempt_id, envelope.request_sha256, "pod-uid-1")
    assert store.claim_worker_start(envelope.attempt_id, envelope.request_sha256) is True
    assert store.claim_worker_start(envelope.attempt_id, envelope.request_sha256) is False
    # Pod disappearance alone must never authorize retiring/reusing resources.
    with pytest.raises(ServiceError):
        store.retire_worker(envelope.attempt_id, envelope.request_sha256, "pod-uid-1")
    with pytest.raises(ServiceError):
        store.observe_worker_stopped(envelope.attempt_id, envelope.request_sha256, "pod-uid-1", clock.now)
    candidate = staged_result(envelope, clock.now)
    first = store.stage_worker_result(envelope.attempt_id, envelope.request_sha256, candidate)
    assert store.stage_worker_result(envelope.attempt_id, envelope.request_sha256, candidate) == first
    assert store.get_artifacts("tenant-a", "ppt", envelope.job_id) == []
    assert store.get_job("tenant-a", "ppt", envelope.job_id)["state"] == "starting"
    other = WorkerResult(state="failed", error_code="different_result")
    other = other.model_copy(update={"stop_receipt": sign_receipt(envelope, other,
        b"fixture-only-receipt-key-0000000000", ("pod-uid-1",), clock.now)}).model_dump()
    with pytest.raises(ServiceError) as exc:
        store.stage_worker_result(envelope.attempt_id, envelope.request_sha256, other)
    assert exc.value.code == "worker_allocation_conflict"
    for value in (float("nan"), float("inf"), clock.now + 6, -1):
        with pytest.raises(ServiceError):
            store.observe_worker_stopped(envelope.attempt_id, envelope.request_sha256, "pod-uid-1", value)
    with pytest.raises(ServiceError):
        store.observe_worker_stopped(envelope.attempt_id, envelope.request_sha256, "pod-replacement", clock.now)
    observed = store.observe_worker_stopped(envelope.attempt_id, envelope.request_sha256, "pod-uid-1", clock.now)
    assert observed["stop_observed_at"] == clock.now
    finish(store, job)
    assert store.active() is None
    retired = store.retire_worker(envelope.attempt_id, envelope.request_sha256, "pod-uid-1")
    assert retired["retired"] is True and retired["candidate_result"] == candidate
    assert store.retire_worker(envelope.attempt_id, envelope.request_sha256, "pod-uid-1") == retired
    assert store.get_worker_allocation(envelope.attempt_id) == retired
    with pytest.raises(ServiceError):
        store.bind_worker(envelope.attempt_id, envelope.request_sha256, "pod-uid-1")


@pytest.mark.parametrize("reason", ["cancel", "lease", "deadline"])
def test_worker_allocation_cannot_launch_after_cancel_or_lease_expiry(store, clock, reason):
    submit(store, timeout_seconds=5 if reason == "deadline" else 600)
    job = store.claim(lease_seconds=10)
    envelope = allocation_envelope(job)
    reserve_allocation(store, envelope)
    if reason == "cancel":
        store.cancel("tenant-a", "ppt", job["id"])
    else:
        clock.now += 6 if reason == "deadline" else 11
    with pytest.raises(ServiceError) as exc:
        reserve_allocation(store, envelope)
    assert exc.value.code == "stale_attempt"
    with pytest.raises(ServiceError) as exc:
        store.claim_worker_create(envelope.attempt_id, envelope.request_sha256)
    assert exc.value.code == "stale_attempt"
    assert store.get_worker_allocation(envelope.attempt_id)["create_claimed"] is False


def test_worker_late_resource_observation_after_cancel_is_preserved_for_cleanup(store):
    submit(store)
    job = store.claim()
    envelope = allocation_envelope(job)
    reserve_allocation(store, envelope)
    assert store.claim_worker_create(envelope.attempt_id, envelope.request_sha256)
    store.cancel("tenant-a", "ppt", job["id"])
    assert store.bind_worker(envelope.attempt_id, envelope.request_sha256, "late-observed-pod")["pod_uid"] == "late-observed-pod"
    with pytest.raises(ServiceError) as exc:
        store.bind_worker(envelope.attempt_id, "0" * 64, "late-observed-pod")
    assert exc.value.code == "worker_allocation_conflict"


def test_worker_cold_capsule_fallback_requires_exact_failed_stop_identity(store, clock):
    submit(store)
    envelope = allocation_envelope(store.claim())
    reserve_allocation(store, envelope)
    assert store.claim_worker_create(envelope.attempt_id, envelope.request_sha256)
    store.bind_worker(envelope.attempt_id, envelope.request_sha256, "pod-uid-1")
    for state, code, files, resources in [
        ("completed", "capsule_stopped_without_result", [], ("k8s:agent-service:pod-uid-1",)),
        ("failed", "engine_error", [], ("k8s:agent-service:pod-uid-1",)),
        ("failed", "capsule_stopped_without_result", [], ("k8s:other-namespace:pod-uid-1",)),
        ("failed", "capsule_stopped_without_result", [InlineFile.from_bytes("unsafe.txt", b"unsafe")],
         ("k8s:agent-service:pod-uid-1",)),
    ]:
        result = WorkerResult(state=state, error_code=code, artifacts=files)
        result = result.model_copy(update={"stop_receipt": sign_receipt(envelope, result,
            b"fixture-only-receipt-key-0000000000", resources, clock.now)})
        with pytest.raises(ServiceError) as exc:
            store.stage_worker_result(envelope.attempt_id, envelope.request_sha256, result.model_dump())
        assert exc.value.code == "worker_allocation_conflict"
    result = WorkerResult(state="cancelled", error_code="capsule_stopped_without_result")
    result = result.model_copy(update={"stop_receipt": sign_receipt(envelope, result,
        b"fixture-only-receipt-key-0000000000", ("k8s:agent-service:pod-uid-1",), clock.now)})
    saved = store.stage_worker_result(envelope.attempt_id, envelope.request_sha256, result.model_dump())
    assert saved["worker_start_claimed"] is False
    store.observe_worker_stopped(envelope.attempt_id, envelope.request_sha256, "pod-uid-1", clock.now)
    assert store.retire_worker(envelope.attempt_id, envelope.request_sha256, "pod-uid-1")["retired"] is True


def test_quarantine_resolution_requires_candidate_stop_observation_and_retirement(store, clock):
    submit(store)
    job = store.claim()
    envelope = allocation_envelope(job)
    reserve_allocation(store, envelope)
    store.claim_worker_create(envelope.attempt_id, envelope.request_sha256)
    store.bind_worker(envelope.attempt_id, envelope.request_sha256, "pod-uid-1")
    store.claim_worker_start(envelope.attempt_id, envelope.request_sha256)
    finish(store, job, stopped=False)
    identity = (job["id"], job["attempt_id"], job["fence"])
    for phase in ("candidate", "stop_observation", "retired"):
        with pytest.raises(ServiceError) as exc:
            store.resolve_worker_quarantine(*identity)
        assert exc.value.code == "worker_stop_unverified"
        assert store.active()["state"] == "quarantined"
        if phase == "candidate":
            store.stage_worker_result(envelope.attempt_id, envelope.request_sha256, staged_result(envelope, clock.now))
        elif phase == "stop_observation":
            store.observe_worker_stopped(envelope.attempt_id, envelope.request_sha256, "pod-uid-1", clock.now)
        else:
            store.retire_worker(envelope.attempt_id, envelope.request_sha256, "pod-uid-1")
    resolved = store.resolve_worker_quarantine(*identity)
    assert resolved["state"] == "failed"
    assert resolved["outcome"]["error_code"] == "quarantine_stopped"
    assert store.active() is None
    assert store.get_artifacts("tenant-a", "ppt", job["id"]) == []


@pytest.mark.parametrize("expected", ["failed", "cancelled", "timed_out"])
def test_quarantine_resolution_preserves_identity_and_never_exports_late_candidates(store, clock, expected):
    submit(store, timeout_seconds=600 if expected == "failed" else 5)
    job = store.claim(lease_seconds=10)
    envelope = allocation_envelope(job)
    reserve_allocation(store, envelope)
    store.claim_worker_create(envelope.attempt_id, envelope.request_sha256)
    store.bind_worker(envelope.attempt_id, envelope.request_sha256, "pod-uid-1")
    store.claim_worker_start(envelope.attempt_id, envelope.request_sha256)
    if expected == "cancelled":
        store.cancel("tenant-a", "ppt", job["id"])
    clock.now += 11
    assert store.reconcile_expired() == 1
    usage = {"input_tokens": 7, "reported_cost": None, "cost_status": "unknown"} if expected == "failed" else None
    result = WorkerResult(state="completed", usage=usage,
                          artifacts=[InlineFile.from_bytes("late.txt", b"not accepted after quarantine")])
    result = result.model_copy(update={"stop_receipt": sign_receipt(envelope, result,
        b"fixture-only-receipt-key-0000000000", ("pod-uid-1",), clock.now)})
    store.stage_worker_result(envelope.attempt_id, envelope.request_sha256, result.model_dump())
    store.observe_worker_stopped(envelope.attempt_id, envelope.request_sha256, "pod-uid-1", clock.now)
    store.retire_worker(envelope.attempt_id, envelope.request_sha256, "pod-uid-1")
    identity = (job["id"], job["attempt_id"], job["fence"])
    result = store.resolve_worker_quarantine(*identity)
    assert result["state"] == expected
    assert result["outcome"]["usage"] == usage
    assert result["outcome"]["error_code"] == "quarantine_stopped"
    assert (result["attempt_id"], result["fence"]) == (job["attempt_id"], job["fence"])
    assert store.get_artifacts("tenant-a", "ppt", job["id"]) == []
    assert store.active() is None
    assert store.resolve_worker_quarantine(*identity)["event_cursor"] == result["event_cursor"]
    page = store.events("tenant-a", "ppt", job["id"])
    assert page["items"][-1]["type"] == "job." + expected
    assert page["items"][-1]["payload"] == {"error_code": "quarantine_stopped"}
    # A duplicate reconciliation cannot release the next job's physical slot.
    following = submit(store, "following")
    next_attempt = store.claim()
    assert next_attempt["id"] == following["id"]
    assert store.resolve_worker_quarantine(*identity)["event_cursor"] == result["event_cursor"]
    assert store.active()["attempt_id"] == next_attempt["attempt_id"]
    for wrong in ((job["id"], job["attempt_id"], job["fence"] + 1),
                  (job["id"], next_attempt["attempt_id"], job["fence"])):
        with pytest.raises(ServiceError) as exc:
            store.resolve_worker_quarantine(*wrong)
        assert exc.value.code == "stale_attempt"


def test_verified_retirement_does_not_allow_resolving_a_nonquarantined_job(store, clock):
    submit(store)
    job = store.claim()
    envelope = allocation_envelope(job)
    reserve_allocation(store, envelope)
    store.claim_worker_create(envelope.attempt_id, envelope.request_sha256)
    store.bind_worker(envelope.attempt_id, envelope.request_sha256, "pod-uid-1")
    store.claim_worker_start(envelope.attempt_id, envelope.request_sha256)
    store.stage_worker_result(envelope.attempt_id, envelope.request_sha256, staged_result(envelope, clock.now))
    store.observe_worker_stopped(envelope.attempt_id, envelope.request_sha256, "pod-uid-1", clock.now)
    store.retire_worker(envelope.attempt_id, envelope.request_sha256, "pod-uid-1")
    with pytest.raises(ServiceError) as exc:
        store.resolve_worker_quarantine(job["id"], job["attempt_id"], job["fence"])
    assert exc.value.code == "stale_attempt"
    assert store.active()["state"] == "starting"


def test_worker_uid_is_frozen_before_pod_binding_and_never_reused_by_later_attempts(store, clock):
    submit(store)
    first_job = store.claim()
    first_envelope = allocation_envelope(first_job)
    resource = ("pi-pool", "agent-service", "agent-" + first_envelope.attempt_id, "a" * 64)
    first = store.reserve_worker(first_envelope, *resource, agent_uid=65536)
    assert first["agent_uid"] == 65536 and first["pod_uid"] is None
    peer = Store(str(store.engine.url), allow_sqlite=True, clock=clock)
    try:
        assert peer.get_worker_allocation(first_envelope.attempt_id)["agent_uid"] == 65536
        assert peer.reserve_worker(first_envelope, *resource, agent_uid=65536) == first
        for changed in (None, 65537):
            with pytest.raises(ServiceError) as exc:
                peer.reserve_worker(first_envelope, *resource, agent_uid=changed)
            assert exc.value.code == "worker_allocation_conflict"
        finish(store, first_job)
        submit(peer, "following")
        following = peer.claim()
        assert following["fence"] > first_job["fence"]
        envelope = allocation_envelope(following)
        next_resource = ("other-pool", "another-namespace", "agent-" + envelope.attempt_id, "b" * 64)
        with pytest.raises(ServiceError) as exc:
            peer.reserve_worker(envelope, *next_resource, agent_uid=65536)
        assert exc.value.code == "worker_allocation_conflict"
        assert peer.reserve_worker(envelope, *next_resource, agent_uid=65537)["agent_uid"] == 65537
    finally:
        peer.close()


def test_worker_uid_rejects_unreserved_low_values_booleans_and_nonintegers(store):
    submit(store)
    envelope = allocation_envelope(store.claim())
    resource = ("pi-pool", "agent-service", "agent-" + envelope.attempt_id, "a" * 64)
    for invalid in (True, False, -1, 0, 10001, 65535, 2147483648, "65536", 65536.0):
        with pytest.raises(ServiceError) as exc:
            store.reserve_worker(envelope, *resource, agent_uid=invalid)
        assert exc.value.code == "invalid_worker_allocation"
    with pytest.raises(ServiceError) as exc:
        store.get_worker_allocation(envelope.attempt_id)
    assert exc.value.code == "worker_allocation_missing"
    assert store.reserve_worker(envelope, *resource, agent_uid=2147483647)["agent_uid"] == 2147483647


def test_unallocated_resolution_is_exact_and_does_not_manufacture_stop_proof(store):
    submit(store)
    job = store.claim(provisioning={"pi": "kubernetes_per_attempt"})
    envelope = allocation_envelope(job)
    with pytest.raises(ServiceError) as exc:
        store.resolve_uncreated_attempt(envelope.model_copy(update={"task": {"prompt": "changed"}}))
    assert exc.value.code == "stale_attempt"
    assert store.active()["state"] == "starting"
    result = store.resolve_uncreated_attempt(envelope)
    assert result["state"] == "failed" and store.active() is None
    assert result["outcome"] == {"state": "failed", "error_code": "worker_not_created",
                                 "usage": None, "stop_receipt": None, "_service_not_started": True}
    assert store.get_artifacts("tenant-a", "ppt", job["id"]) == []
    assert store.resolve_uncreated_attempt(envelope)["event_cursor"] == result["event_cursor"]
    with pytest.raises(ServiceError) as exc:
        reserve_allocation(store, envelope)
    assert exc.value.code == "stale_attempt"
    with pytest.raises(ServiceError) as exc:
        store.claim_worker_create(envelope.attempt_id, envelope.request_sha256)
    assert exc.value.code == "worker_allocation_missing"
    submit(store, "following")
    following = store.claim()
    assert store.resolve_uncreated_attempt(envelope)["event_cursor"] == result["event_cursor"]
    assert store.active()["attempt_id"] == following["attempt_id"]


def test_uncreated_resolution_retires_unclaimed_intent_and_blocks_late_creation(store):
    submit(store)
    job = store.claim(provisioning={"pi": "kubernetes_per_attempt"})
    envelope = allocation_envelope(job)
    reserve_allocation(store, envelope)
    result = store.resolve_uncreated_attempt(envelope)
    assert result["state"] == "failed" and store.active() is None
    allocation = store.get_worker_allocation(envelope.attempt_id)
    assert allocation["retired"] is True and allocation["create_claimed"] is False
    assert store.resolve_uncreated_attempt(envelope)["event_cursor"] == result["event_cursor"]
    with pytest.raises(ServiceError) as exc:
        store.claim_worker_create(envelope.attempt_id, envelope.request_sha256)
    assert exc.value.code == "stale_attempt"


def test_uncreated_resolution_refuses_any_claimed_creation_intent(store):
    submit(store)
    job = store.claim(provisioning={"pi": "kubernetes_per_attempt"})
    envelope = allocation_envelope(job)
    reserve_allocation(store, envelope)
    assert store.claim_worker_create(envelope.attempt_id, envelope.request_sha256) is True
    assert store.resolve_uncreated_attempt(envelope) is None
    assert store.active()["attempt_id"] == job["attempt_id"]


def test_unallocated_resolution_race_cannot_release_then_authorize_creation(store, clock):
    submit(store)
    job = store.claim(provisioning={"pi": "kubernetes_per_attempt"})
    envelope = allocation_envelope(job)
    peer = Store(str(store.engine.url), allow_sqlite=True, clock=clock)
    barrier = threading.Barrier(2)

    def reserve():
        barrier.wait(5)
        reserved = False
        try:
            reserve_allocation(store, envelope)
            reserved = True
            return {"reserved": True, "created": store.claim_worker_create(envelope.attempt_id, envelope.request_sha256)}
        except ServiceError as error:
            return {"reserved": reserved, "created": False, "code": error.code}

    def resolve():
        barrier.wait(5)
        return peer.resolve_uncreated_attempt(envelope)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            reserve_task, resolve_task = executor.submit(reserve), executor.submit(resolve)
            reservation, resolution = reserve_task.result(), resolve_task.result()
        if resolution is None:
            assert reservation == {"reserved": True, "created": True}
            assert peer.active()["attempt_id"] == job["attempt_id"]
        else:
            assert reservation["created"] is False and reservation["code"] == "stale_attempt"
            assert resolution["state"] == "failed" and peer.active() is None
            with pytest.raises(ServiceError) as exc:
                store.claim_worker_create(envelope.attempt_id, envelope.request_sha256)
            assert exc.value.code in {"worker_allocation_missing", "stale_attempt"}
            if reservation["reserved"]:
                assert store.get_worker_allocation(envelope.attempt_id)["retired"] is True
    finally:
        peer.close()


def test_uid_capacity_is_advisory_and_counts_fences_and_retired_uids(store, clock):
    assert store.worker_uid_available(70001, 2) is True
    submit(store)
    job = store.claim()
    envelope = allocation_envelope(job)
    store.reserve_worker(envelope, "pi-pool", "agent-service", "agent-" + envelope.attempt_id,
                         "a" * 64, agent_uid=70001, aux_uid=70002)
    store.claim_worker_create(envelope.attempt_id, envelope.request_sha256)
    store.bind_worker(envelope.attempt_id, envelope.request_sha256, "pod-uid-1")
    store.claim_worker_start(envelope.attempt_id, envelope.request_sha256)
    store.stage_worker_result(envelope.attempt_id, envelope.request_sha256, staged_result(envelope, clock.now))
    store.observe_worker_stopped(envelope.attempt_id, envelope.request_sha256, "pod-uid-1", clock.now)
    store.retire_worker(envelope.attempt_id, envelope.request_sha256, "pod-uid-1")
    finish(store, job)
    assert store.worker_uid_available(70001, 3) is False  # next fence consumes a second pair
    assert store.worker_uid_available(69999, 4) is False  # proposed pair reuses retired identities
    assert store.worker_uid_available(70001, 4) is True
    submit(store, "following")
    assert store.claim()["fence"] == 2  # advisory reads never consume a fence
    assert store.worker_uid_available(70001, 2) is False
    for start, count in ((True, 1), (70001, False), (65535, 1), (2147483647, 2), (70001, 1000001)):
        with pytest.raises(ServiceError) as exc:
            store.worker_uid_available(start, count)
        assert exc.value.code == "invalid_worker_allocation"


def test_attempt_provisioning_is_frozen_and_legacy_records_stay_static(store):
    from sqlalchemy import select, update
    from agent_service.store import attempts

    submit(store)
    static = store.claim()
    assert store.attempt_provisioning(allocation_envelope(static)) == "external_static"
    assert store.resolve_uncreated_attempt(allocation_envelope(static)) is None
    assert store.active()["attempt_id"] == static["attempt_id"]
    finish(store, static)
    submit(store, "new-kube")
    route = {"pi": "kubernetes_per_attempt"}
    claimed = store.claim(provisioning=route)
    envelope = allocation_envelope(claimed)
    route["pi"] = "external_static"
    assert store.attempt_provisioning(envelope) == "kubernetes_per_attempt"
    # Fixture an old/unknown persisted record, as opposed to new deployment
    # configuration. Neither may be reinterpreted as proof of no static run.
    for legacy in (None, "unknown-version"):
        with store.engine.begin() as connection:
            intent = dict(connection.execute(select(attempts.c.dispatch_intent).where(
                attempts.c.id == envelope.attempt_id)).scalar_one())
            if legacy is None:
                intent.pop("provisioning")
            else:
                intent["provisioning"] = legacy
            connection.execute(update(attempts).where(attempts.c.id == envelope.attempt_id).values(dispatch_intent=intent))
        assert store.attempt_provisioning(envelope) == "external_static"
        assert store.resolve_uncreated_attempt(envelope) is None
        assert store.active()["attempt_id"] == envelope.attempt_id


def test_invalid_provisioning_map_never_claims_an_attempt(store):
    submit(store)
    for invalid in ([], {"unknown": "kubernetes_per_attempt"}, {"pi": "unreviewed"},
                    {"pi": True}, {"pi": ["kubernetes_per_attempt"]}):
        with pytest.raises(ValueError, match="Invalid server-owned worker provisioning"):
            store.claim(provisioning=invalid)
    assert store.claim()["fence"] == 1
