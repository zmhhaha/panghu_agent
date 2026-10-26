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

from agent_service.contracts import (ExecutionBindingPins, ExecutionEnvelope, InlineFile,
                                     ServiceError, WorkerResult, sign_receipt)
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


def test_binding_is_durable_and_original_request_digest_survives_route_change(pg_store, pg_url):
    store, clock = pg_store
    base = _pins()
    binding = ExecutionBindingPins.create(**base, binding_id='workspace-pi', binding_revision='r1',
        contract_sha256='1' * 64, bundle_name='workspace-text', bundle_revision='r1', bundle_sha256='2' * 64,
        tool_environment_name='text-tools', tool_environment_revision='r1', tool_environment_sha256='3' * 64,
        adapter_revision='pi-v1', model_policy_revision='model-v1', model_policy_sha256='4' * 64,
        acceptance_sha256='5' * 64)
    original, created = store.submit('fixture-tenant', 'ppt', 'binding-once', _request(),
                                    {**base, 'execution_binding': binding.model_dump(mode='json')})
    assert created
    peer = Store(pg_url, clock=clock)
    try:
        alternate = {**_pins('hermes')}
        replay, created = peer.submit('fixture-tenant', 'ppt', 'binding-once', _request(), alternate)
        assert created is False and replay['id'] == original['id']
        assert replay['pins']['execution_binding'] == binding.model_dump(mode='json')
        claim = peer.claim()
        # Use the public dispatcher construction, then compare against Store's
        # own authoritative envelope when reserving a resource for the attempt.
        from agent_service.dispatcher import Dispatcher
        envelope = Dispatcher(peer, {})._envelope(claim)
        assert envelope.execution_binding == binding and envelope.engine == 'pi'
        allocation = peer.reserve_worker(envelope, pool_id='fixture-pool',
            namespace='agent-workers', pod_name='agent-' + envelope.attempt_id,
            intent_sha256='6' * 64, agent_uid=90001)
        assert allocation['request_sha256'] == envelope.request_sha256
    finally:
        peer.close()


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


def test_pg_bootstrap_audit_handles_mixed_relation_kinds_and_detects_public_grants(pg_url):
    """Audit a low-privilege built-in role without creating cluster-wide roles.

    The disposable schema contains a table, index, view and sequence. PostgreSQL
    may evaluate AND operands in either order, so a sequence ACL function must
    never be invoked on those other pg_class objects. PUBLIC grants still count.
    """
    from agent_service.deploy.bootstrap import CROSS_DATABASE_INSPECTION
    audit = CROSS_DATABASE_INSPECTION.replace("rolname = 'agent_service'",
        "rolname = 'pg_read_all_settings'")
    engine = create_engine(pg_url)
    try:
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT NOT rolsuper FROM pg_roles "
                "WHERE rolname = 'pg_read_all_settings'")) is True
            connection.exec_driver_sql('CREATE TABLE bootstrap_audit_table (id integer PRIMARY KEY)')
            connection.exec_driver_sql('CREATE VIEW bootstrap_audit_view AS SELECT id FROM bootstrap_audit_table')
            connection.exec_driver_sql('CREATE SEQUENCE bootstrap_audit_sequence')
            before = connection.scalar(text(audit))['unsafe_objects']
            connection.exec_driver_sql('GRANT SELECT ON bootstrap_audit_table, bootstrap_audit_view TO PUBLIC')
            connection.exec_driver_sql('GRANT USAGE ON SEQUENCE bootstrap_audit_sequence TO PUBLIC')
            after = connection.scalar(text(audit))['unsafe_objects']
            assert after == before + 3
            connection.exec_driver_sql('REVOKE SELECT ON bootstrap_audit_table, bootstrap_audit_view FROM PUBLIC')
            connection.exec_driver_sql('REVOKE USAGE ON SEQUENCE bootstrap_audit_sequence FROM PUBLIC')
            assert connection.scalar(text(audit))['unsafe_objects'] == before
    finally:
        engine.dispose()


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


def _worker_envelope(job):
    return ExecutionEnvelope(job_id=job["id"], attempt_id=job["attempt_id"], fence=job["fence"],
        engine=job["pins"]["engine"], profile=job["request"]["profile"],
        profile_revision=job["request"]["profile_revision"], runtime_revision=job["pins"]["runtime_revision"],
        image_digest=job["pins"]["image_digest"], deadline_at=float(job["deadline_at"]),
        lease_expires_at=float(job["lease_expires_at"]), task=job["request"]["task"])


def _worker_create_process(url, attempt_id, request_sha256, ready, release, output):
    store = None
    try:
        store = Store(url, clock=Clock())
        ready.put(True)
        if not release.wait(15):
            raise RuntimeError("fixture_release_timeout")
        output.put({"claimed": store.claim_worker_create(attempt_id, request_sha256)})
    except BaseException as exc:
        output.put({"error_type": type(exc).__name__})
    finally:
        if store is not None:
            store.close()


def test_pg_worker_create_cas_and_candidate_binding_survive_processes_and_reconnect(pg_store, pg_url):
    store, clock = pg_store
    _submit(store)
    job = store.claim()
    envelope = _worker_envelope(job)
    allocation = store.reserve_worker(envelope, "pi-pool", "agent-service", "agent-" + envelope.attempt_id,
                                      "a" * 64, agent_uid=65536)
    ctx = multiprocessing.get_context("spawn")
    ready, release, output = ctx.Queue(), ctx.Event(), ctx.Queue()
    children = [ctx.Process(target=_worker_create_process,
        args=(pg_url, envelope.attempt_id, envelope.request_sha256, ready, release, output)) for _ in range(2)]
    peer = Store(pg_url, clock=clock)
    try:
        for child in children:
            child.start()
        assert [ready.get(timeout=15) for _ in children] == [True, True]
        release.set()
        replies = [output.get(timeout=15) for _ in children]
        assert not any("error_type" in reply for reply in replies)
        assert sorted(reply["claimed"] for reply in replies) == [False, True]
        assert peer.claim_worker_create(envelope.attempt_id, envelope.request_sha256) is False
        assert peer.get_worker_allocation(envelope.attempt_id)["pod_name"] == allocation["pod_name"]
        assert peer.get_worker_allocation(envelope.attempt_id)["agent_uid"] == 65536
        with pytest.raises(ServiceError) as exc:
            peer.reserve_worker(envelope, "pi-pool", "agent-service", allocation["pod_name"],
                                "a" * 64, agent_uid=65537)
        assert exc.value.code == "worker_allocation_conflict"
        peer.bind_worker(envelope.attempt_id, envelope.request_sha256, "pg-pod-uid-1")
        with pytest.raises(ServiceError) as exc:
            store.bind_worker(envelope.attempt_id, envelope.request_sha256, "recreated-pod-uid")
        assert exc.value.code == "worker_allocation_conflict"
        assert store.claim_worker_start(envelope.attempt_id, envelope.request_sha256) is True
        assert peer.claim_worker_start(envelope.attempt_id, envelope.request_sha256) is False
        result = WorkerResult(state="completed", artifacts=[InlineFile.from_bytes("result.txt", b"durable candidate")])
        result = result.model_copy(update={"stop_receipt": sign_receipt(envelope, result,
            b"fixture-only-receipt-key-0000000000", ("pg-pod-uid-1",), clock.now)})
        store.stage_worker_result(envelope.attempt_id, envelope.request_sha256, result.model_dump())
        assert peer.get_worker_allocation(envelope.attempt_id)["candidate_result"] == result.model_dump()
        assert peer.get_artifacts("fixture-tenant", "ppt", job["id"]) == []
        with pytest.raises(ServiceError):
            peer.retire_worker(envelope.attempt_id, envelope.request_sha256, "pg-pod-uid-1")
        peer.observe_worker_stopped(envelope.attempt_id, envelope.request_sha256, "pg-pod-uid-1", clock.now)
        assert store.get_worker_allocation(envelope.attempt_id)["stop_observed_at"] == clock.now
        _finish(store, job)
        peer.retire_worker(envelope.attempt_id, envelope.request_sha256, "pg-pod-uid-1")
        assert store.get_worker_allocation(envelope.attempt_id)["retired"] is True
        _submit(peer, "next-uid")
        next_envelope = _worker_envelope(peer.claim())
        next_resource = ("other-pool", "another-namespace", "agent-" + next_envelope.attempt_id, "b" * 64)
        with pytest.raises(ServiceError) as exc:
            store.reserve_worker(next_envelope, *next_resource, agent_uid=65536)
        assert exc.value.code == "worker_allocation_conflict"
        assert store.reserve_worker(next_envelope, *next_resource, agent_uid=65537)["agent_uid"] == 65537
    finally:
        peer.close()
        release.set()
        for child in children:
            if child.pid is not None:
                child.join(timeout=5)
                if child.is_alive():
                    child.terminate()
                    child.join(timeout=5)
        ready.close(); output.close()


def test_pg_worker_event_cursor_page_commit_is_atomic_and_replayable(pg_store, pg_url, monkeypatch):
    store, clock = pg_store
    _submit(store)
    job = store.claim()
    identity = (job["id"], job["attempt_id"], job["fence"])
    peer = Store(pg_url, clock=clock)

    def page(after):
        return [{"seq": after + i, "type": "engine.progress", "payload": {"event": "engine_progress"}}
                for i in (1, 2)]

    try:
        assert peer.worker_event_cursor(*identity) == 0
        assert store.ingest_worker_events(*identity, 0, page(0), 2) == 2
        assert peer.worker_event_cursor(*identity) == 2
        cursor = peer.events("fixture-tenant", "ppt", job["id"])["next_cursor"]
        assert peer.ingest_worker_events(*identity, 0, page(0), 2) == 2
        assert peer.events("fixture-tenant", "ppt", job["id"])["next_cursor"] == cursor
        original = store._event
        counter = [0]

        def interrupted(connection, row, kind, payload=None):
            counter[0] += 1
            if counter[0] == 2:
                raise RuntimeError("fixture_transaction_interruption")
            return original(connection, row, kind, payload)

        with monkeypatch.context() as changes:
            changes.setattr(store, "_event", interrupted)
            with pytest.raises(RuntimeError, match="fixture_transaction_interruption"):
                store.ingest_worker_events(*identity, 2, page(2), 4)
        assert peer.worker_event_cursor(*identity) == 2
        assert peer.events("fixture-tenant", "ppt", job["id"])["next_cursor"] == cursor
        assert peer.ingest_worker_events(*identity, 2, page(2), 4) == 4
        _finish(store, job)
        with pytest.raises(ServiceError) as exc:
            peer.ingest_worker_events(*identity, 0, page(0), 2)
        assert exc.value.code == "stale_attempt"
    finally:
        peer.close()


def _resolve_worker_process(url, identity, ready, release, output):
    store = None
    try:
        store = Store(url, clock=Clock())
        ready.put(True)
        if not release.wait(15):
            raise RuntimeError("fixture_release_timeout")
        result = store.resolve_worker_quarantine(*identity)
        output.put({"state": result["state"], "event_cursor": result["event_cursor"],
                    "attempt_id": result["attempt_id"]})
    except BaseException as exc:
        output.put({"error_type": type(exc).__name__})
    finally:
        if store is not None:
            store.close()


def test_pg_quarantine_release_is_durable_idempotent_and_exact_across_processes(pg_store, pg_url):
    store, clock = pg_store
    _submit(store)
    job = store.claim()
    envelope = _worker_envelope(job)
    identity = (job["id"], job["attempt_id"], job["fence"])
    store.reserve_worker(envelope, "pi-pool", "agent-service", "agent-" + envelope.attempt_id, "a" * 64)
    store.claim_worker_create(envelope.attempt_id, envelope.request_sha256)
    store.bind_worker(envelope.attempt_id, envelope.request_sha256, "pg-pod-uid-1")
    store.claim_worker_start(envelope.attempt_id, envelope.request_sha256)
    _finish(store, job, stopped=False)
    result = WorkerResult(state="completed", usage={"input_tokens": 12},
                          artifacts=[InlineFile.from_bytes("late.txt", b"do not publish")])
    result = result.model_copy(update={"stop_receipt": sign_receipt(envelope, result,
        b"fixture-only-receipt-key-0000000000", ("pg-pod-uid-1",), clock.now)})
    store.stage_worker_result(envelope.attempt_id, envelope.request_sha256, result.model_dump())
    store.observe_worker_stopped(envelope.attempt_id, envelope.request_sha256, "pg-pod-uid-1", clock.now)
    store.retire_worker(envelope.attempt_id, envelope.request_sha256, "pg-pod-uid-1")
    ctx = multiprocessing.get_context("spawn")
    ready, release, output = ctx.Queue(), ctx.Event(), ctx.Queue()
    children = [ctx.Process(target=_resolve_worker_process, args=(pg_url, identity, ready, release, output))
                for _ in range(2)]
    peer = Store(pg_url, clock=clock)
    try:
        assert peer.active()["state"] == "quarantined"
        for child in children:
            child.start()
        assert [ready.get(timeout=15) for _ in children] == [True, True]
        release.set()
        replies = [output.get(timeout=15) for _ in children]
        assert not any("error_type" in reply for reply in replies)
        assert [reply["state"] for reply in replies] == ["failed", "failed"]
        assert len({reply["event_cursor"] for reply in replies}) == 1
        assert {reply["attempt_id"] for reply in replies} == {job["attempt_id"]}
        assert peer.active() is None
        final = peer.get_job("fixture-tenant", "ppt", job["id"])
        assert final["outcome"]["error_code"] == "quarantine_stopped"
        assert final["outcome"]["usage"] == {"input_tokens": 12}
        assert peer.get_artifacts("fixture-tenant", "ppt", job["id"]) == []
        events = peer.events("fixture-tenant", "ppt", job["id"])["items"]
        assert sum(item["payload"].get("error_code") == "quarantine_stopped" for item in events) == 1
        _submit(peer, "following")
        following = peer.claim()
        assert following["id"] != job["id"] and following["fence"] > job["fence"]
        assert store.resolve_worker_quarantine(*identity)["event_cursor"] == final["event_cursor"]
        assert peer.active()["attempt_id"] == following["attempt_id"]
    finally:
        peer.close()
        release.set()
        for child in children:
            if child.pid is not None:
                child.join(timeout=5)
                if child.is_alive():
                    child.terminate()
                    child.join(timeout=5)
        ready.close(); output.close()


def _uncreated_race_process(url, wire, operation, ready, release, output):
    store = None
    reserved = False
    try:
        store = Store(url, clock=Clock())
        envelope = ExecutionEnvelope.model_validate(wire)
        ready.put(True)
        if not release.wait(15):
            raise RuntimeError("fixture_release_timeout")
        if operation == "reserve":
            store.reserve_worker(envelope, "pi-pool", "agent-service", "agent-" + envelope.attempt_id,
                                 "a" * 64, agent_uid=70001)
            reserved = True
            created = store.claim_worker_create(envelope.attempt_id, envelope.request_sha256)
            output.put({"operation": operation, "reserved": True, "created": created})
        else:
            result = store.resolve_uncreated_attempt(envelope)
            output.put({"operation": operation, "resolved": result is not None,
                        "state": None if result is None else result["state"]})
    except ServiceError as exc:
        output.put({"operation": operation, "reserved": reserved, "created": False, "code": exc.code})
    except BaseException as exc:
        output.put({"operation": operation, "error_type": type(exc).__name__})
    finally:
        if store is not None:
            store.close()


def test_pg_uncreated_resolution_cannot_race_with_late_resource_authorization(pg_store, pg_url):
    store, clock = pg_store
    _submit(store)
    job = store.claim(provisioning={"pi": "kubernetes_per_attempt"})
    envelope = _worker_envelope(job)
    ctx = multiprocessing.get_context("spawn")
    ready, release, output = ctx.Queue(), ctx.Event(), ctx.Queue()
    children = [ctx.Process(target=_uncreated_race_process,
        args=(pg_url, envelope.model_dump(), operation, ready, release, output))
        for operation in ("reserve", "resolve")]
    peer = Store(pg_url, clock=clock)
    try:
        assert peer.attempt_provisioning(envelope) == "kubernetes_per_attempt"
        for child in children:
            child.start()
        assert [ready.get(timeout=15) for _ in children] == [True, True]
        release.set()
        replies = [output.get(timeout=15) for _ in children]
        assert not any("error_type" in reply for reply in replies)
        values = {reply["operation"]: reply for reply in replies}
        reservation, resolution = values["reserve"], values["resolve"]
        if resolution["resolved"]:
            assert resolution["state"] == "failed" and peer.active() is None
            assert reservation["created"] is False and reservation["code"] == "stale_attempt"
            assert peer.get_artifacts("fixture-tenant", "ppt", job["id"]) == []
            with pytest.raises(ServiceError) as exc:
                store.claim_worker_create(envelope.attempt_id, envelope.request_sha256)
            assert exc.value.code in {"stale_attempt", "worker_allocation_missing"}
            if reservation["reserved"]:
                allocation = peer.get_worker_allocation(envelope.attempt_id)
                assert allocation["retired"] is True and allocation["create_claimed"] is False
                assert allocation["agent_uid"] == 70001
        else:
            assert reservation == {"operation": "reserve", "reserved": True, "created": True}
            assert peer.active()["attempt_id"] == job["attempt_id"]
            assert peer.resolve_uncreated_attempt(envelope) is None
        assert peer.worker_uid_available(70001, 1) is False
    finally:
        peer.close()
        release.set()
        for child in children:
            if child.pid is not None:
                child.join(timeout=5)
                if child.is_alive():
                    child.terminate()
                    child.join(timeout=5)
        ready.close(); output.close()
