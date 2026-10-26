"""Durable, engine-neutral queue. Public ownership never comes from job JSON.

PostgreSQL is the supported service database. SQLite is available only when a
trusted test/development caller explicitly opts in. No database is touched on
import. A quarantined attempt retains the global slot; a lease is not stop proof.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import math
import re
import time
import uuid

from sqlalchemy import (Boolean, Column, Float, ForeignKey, Integer, JSON,
                        LargeBinary, MetaData, String, Table, UniqueConstraint,
                        create_engine, func, insert, select, text, update)
from sqlalchemy.engine import make_url
from sqlalchemy.pool import StaticPool
from pydantic import ValidationError

from .contracts import (ExecutionBindingPins, ExecutionEnvelope, InlineFile, ServiceError,
                        WorkerResult, canonical_bytes, digest)


metadata = MetaData()
inputs = Table(
    "agent_inputs", metadata,
    Column("id", String(36), primary_key=True),
    Column("tenant", String(128), nullable=False),
    Column("caller", String(128), nullable=False),
    Column("name", String(128), nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("size", Integer, nullable=False),
    Column("content", LargeBinary, nullable=False),
    Column("created_at", Float, nullable=False),
)
jobs = Table(
    "agent_jobs", metadata,
    Column("id", String(36), primary_key=True),
    Column("tenant", String(128), nullable=False),
    Column("caller", String(128), nullable=False),
    Column("idempotency_key", String(128), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("request", JSON, nullable=False),
    Column("pins", JSON, nullable=False),
    Column("state", String(32), nullable=False),
    Column("created_at", Float, nullable=False),
    Column("deadline_at", Float, nullable=False),
    Column("event_cursor", Integer, nullable=False, default=0),
    Column("attempt_id", String(36)),
    Column("fence", Integer, nullable=False, default=0),
    Column("lease_expires_at", Float),
    Column("cancel_requested", Boolean, nullable=False, default=False),
    Column("outcome", JSON),
    UniqueConstraint("tenant", "caller", "idempotency_key", name="agent_job_idempotency"),
)
attempts = Table(
    "agent_attempts", metadata,
    Column("id", String(36), primary_key=True),
    Column("job_id", String(36), ForeignKey("agent_jobs.id"), nullable=False),
    Column("fence", Integer, nullable=False),
    Column("state", String(32), nullable=False),
    Column("created_at", Float, nullable=False),
    Column("lease_expires_at", Float, nullable=False),
    Column("deadline_at", Float, nullable=False),
    Column("dispatch_intent", JSON, nullable=False),
    Column("outcome", JSON),
    UniqueConstraint("job_id", "fence", name="agent_attempt_fence"),
)
worker_allocations = Table(
    "agent_worker_allocations", metadata,
    Column("attempt_id", String(36), ForeignKey("agent_attempts.id"), primary_key=True),
    Column("job_id", String(36), ForeignKey("agent_jobs.id"), nullable=False),
    Column("fence", Integer, nullable=False),
    Column("engine", String(16), nullable=False),
    Column("request_sha256", String(64), nullable=False),
    Column("pool_id", String(64), nullable=False),
    Column("namespace", String(63), nullable=False),
    Column("pod_name", String(63), nullable=False),
    Column("intent_sha256", String(64), nullable=False),
    Column("agent_uid", Integer),
    Column("pod_uid", String(128)),
    Column("create_claimed", Boolean, nullable=False, default=False),
    Column("worker_start_claimed", Boolean, nullable=False, default=False),
    Column("retired", Boolean, nullable=False, default=False),
    Column("candidate_result", JSON),
    Column("stop_observed_at", Float),
    Column("created_at", Float, nullable=False),
    UniqueConstraint("pool_id", "namespace", "pod_name", name="agent_worker_resource_name"),
    UniqueConstraint("agent_uid", name="agent_worker_uid_never_reused"),
)
worker_event_cursors = Table(
    "agent_worker_event_cursors", metadata,
    Column("attempt_id", String(36), ForeignKey("agent_attempts.id"), primary_key=True),
    Column("job_id", String(36), ForeignKey("agent_jobs.id"), nullable=False),
    Column("fence", Integer, nullable=False),
    Column("cursor", Integer, nullable=False, default=0),
)
events = Table(
    "agent_events", metadata,
    Column("job_id", String(36), ForeignKey("agent_jobs.id"), primary_key=True),
    Column("seq", Integer, primary_key=True),
    Column("type", String(64), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("created_at", Float, nullable=False),
)
artifacts = Table(
    "agent_artifacts", metadata,
    Column("id", String(36), primary_key=True),
    Column("job_id", String(36), ForeignKey("agent_jobs.id"), nullable=False),
    Column("attempt_id", String(36), ForeignKey("agent_attempts.id"), nullable=False),
    Column("name", String(128), nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("size", Integer, nullable=False),
    Column("content", LargeBinary, nullable=False),
    UniqueConstraint("job_id", "name", name="agent_artifact_name"),
)
slot = Table(
    "agent_slot", metadata,
    Column("id", Integer, primary_key=True),
    Column("job_id", String(36), ForeignKey("agent_jobs.id")),
    Column("next_fence", Integer, nullable=False, default=1),
)

ACTIVE = frozenset({"starting", "running", "cancelling"})
TERMINAL = frozenset({"completed", "needs_input", "failed", "cancelled", "timed_out", "quarantined"})
EVENT_TYPES = frozenset({"job.queued", "job.starting", "job.running", "job.cancelling",
                         "job.completed", "job.needs_input", "job.failed", "job.cancelled",
                         "job.timed_out", "job.quarantined", "engine.progress"})
_CODE = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_RESOURCE_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_POD_UID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_WORKER_EVENT_KINDS = frozenset({"pi_event", "runtime_finished", "engine_started", "engine_progress"})


def _name(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 128
            or value in {".", ".."} or any(c in value for c in "/\\:")
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ServiceError("invalid_file_name", 422)
    return value


def _identity(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise ServiceError("invalid_identity", 403)
    return value


def _job(row):
    result = dict(row)
    result["job_id"] = result["id"]
    result.pop("idempotency_key", None)
    result.pop("request_hash", None)
    return result


def _input(row, content=False):
    result = {k: row[k] for k in ("id", "name", "sha256", "size", "created_at")}
    result["input_id"] = result["id"]
    if content:
        result["content"] = row["content"]
    return result


class Store:
    def __init__(self, database_url: str, *, allow_sqlite=False, clock=time.time,
                 max_blob_bytes=1024 * 1024, max_artifact_bytes=8 * 1024 * 1024,
                 max_events=1000, max_queue=1000):
        dialect = make_url(database_url).get_backend_name()
        if dialect not in {"postgresql", "sqlite"} or (dialect == "sqlite" and allow_sqlite is not True):
            raise ServiceError("postgresql_required", 503)
        if (type(max_blob_bytes) is not int or max_blob_bytes < 1
                or type(max_artifact_bytes) is not int or max_artifact_bytes < 1
                or type(max_events) is not int or max_events < 32
                or type(max_queue) is not int or max_queue < 1):
            raise ValueError("Invalid bounded store limits")
        options = {"pool_pre_ping": True}
        if dialect == "sqlite":
            options["connect_args"] = {"timeout": 30, "check_same_thread": False}
            if make_url(database_url).database in {None, "", ":memory:"}:
                # Tests needing concurrency should use separate connections to a
                # file DB; this pool exists for single-thread in-memory fixtures.
                options["poolclass"] = StaticPool
        self.engine = create_engine(database_url, **options)
        self.clock = clock
        self.dialect = dialect
        self.max_blob_bytes = max_blob_bytes
        self.max_artifact_bytes = max_artifact_bytes
        self.max_events = max_events
        self.max_queue = max_queue

    @contextmanager
    def _tx(self):
        with self.engine.connect() as conn:
            if self.dialect == "sqlite":
                conn.exec_driver_sql("PRAGMA foreign_keys=ON")
                conn.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                conn.begin()
                # One global slot initially: a DB-wide transaction lock also
                # serializes admission/cancel/finish with cross-process claim.
                conn.execute(text("SELECT pg_advisory_xact_lock(73124001)"))
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def initialize(self):
        metadata.create_all(self.engine)
        with self._tx() as conn:
            if conn.execute(select(slot.c.id).where(slot.c.id == 1)).first() is None:
                conn.execute(insert(slot).values(id=1, job_id=None, next_fence=1))

    def close(self):
        self.engine.dispose()

    def _find(self, conn, job_id, tenant=None, caller=None):
        query = select(jobs).where(jobs.c.id == job_id)
        if tenant is not None:
            query = query.where(jobs.c.tenant == tenant, jobs.c.caller == caller)
        row = conn.execute(query).mappings().first()
        if row is None:
            raise ServiceError("not_found", 404)
        return dict(row)

    def _event(self, conn, row, kind, payload=None):
        if kind not in EVENT_TYPES:
            raise ServiceError("invalid_event", 422)
        payload = {} if payload is None else payload
        if not isinstance(payload, dict) or len(canonical_bytes(payload)) > 8192:
            raise ServiceError("invalid_event", 422)
        if kind == "engine.progress":
            if not set(payload) <= {"phase", "event", "code", "count"}:
                raise ServiceError("invalid_event", 422)
            for key, value in payload.items():
                if key == "count":
                    if type(value) is not int or not 0 <= value <= 2**31 - 1:
                        raise ServiceError("invalid_event", 422)
                elif not isinstance(value, str) or not _CODE.fullmatch(value):
                    raise ServiceError("invalid_event", 422)
            # Reserve room for lifecycle/terminal events even if an engine floods.
            if row["event_cursor"] >= self.max_events - 16:
                return False
        elif not set(payload) <= {"error_code"}:
            raise ServiceError("invalid_event", 422)
        elif payload and (not isinstance(payload.get("error_code"), str)
                          or not _CODE.fullmatch(payload["error_code"])):
            raise ServiceError("invalid_event", 422)
        if row["event_cursor"] >= self.max_events:
            raise ServiceError("event_limit", 409)
        row["event_cursor"] += 1
        conn.execute(insert(events).values(job_id=row["id"], seq=row["event_cursor"],
                                         type=kind, payload=payload, created_at=self.clock()))
        conn.execute(update(jobs).where(jobs.c.id == row["id"]).values(event_cursor=row["event_cursor"]))
        return True

    def put_input(self, tenant, caller, name, content):
        _identity(tenant); _identity(caller); _name(name)
        if type(content) is not bytes or not 0 < len(content) <= self.max_blob_bytes:
            raise ServiceError("input_size_limit", 413)
        values = {"id": str(uuid.uuid4()), "tenant": tenant, "caller": caller,
                  "name": name, "sha256": hashlib.sha256(content).hexdigest(),
                  "size": len(content), "content": content, "created_at": self.clock()}
        with self._tx() as conn:
            conn.execute(insert(inputs).values(**values))
        return _input(values)

    def get_input(self, tenant, caller, input_id):
        with self._tx() as conn:
            row = conn.execute(select(inputs).where(inputs.c.id == input_id,
                              inputs.c.tenant == tenant, inputs.c.caller == caller)).mappings().first()
            if row is None:
                raise ServiceError("not_found", 404)
            return _input(row, True)

    def _check_inputs(self, conn, tenant, caller, request):
        refs = request.get("inputs", [])
        if not isinstance(refs, list) or len(refs) > 32:
            raise ServiceError("invalid_inputs", 422)
        seen = set()
        for ref in refs:
            if not isinstance(ref, dict) or not {"input_id", "sha256"} <= set(ref):
                raise ServiceError("invalid_inputs", 422)
            identifier = ref["input_id"]
            if not isinstance(identifier, str) or identifier in seen:
                raise ServiceError("invalid_inputs", 422)
            seen.add(identifier)
            source = conn.execute(select(inputs.c.sha256).where(inputs.c.id == identifier,
                                  inputs.c.tenant == tenant, inputs.c.caller == caller)).first()
            if source is None:
                raise ServiceError("not_found", 404)
            if source.sha256 != ref["sha256"]:
                raise ServiceError("input_hash_mismatch", 409)

    def lookup_submission(self, tenant, caller, idempotency_key, request):
        """Replay independent of current routes, after resource reauthorization."""
        _identity(tenant); _identity(caller)
        if (not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128
                or any(not 33 <= ord(c) <= 126 for c in idempotency_key)):
            raise ServiceError("idempotency_key_required", 400)
        if not isinstance(request, dict) or len(canonical_bytes(request)) > 256 * 1024:
            raise ServiceError("invalid_request", 422)
        with self._tx() as conn:
            self._check_inputs(conn, tenant, caller, request)
            old = conn.execute(select(jobs).where(jobs.c.tenant == tenant,
                               jobs.c.caller == caller, jobs.c.idempotency_key == idempotency_key)).mappings().first()
            if old is None:
                return None
            if old["request_hash"] != digest(request):
                raise ServiceError("idempotency_conflict", 409)
            return _job(old)

    replay = lookup_submission

    def submit(self, tenant, caller, idempotency_key, request, pins):
        _identity(tenant); _identity(caller)
        if (not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128
                or any(not 33 <= ord(c) <= 126 for c in idempotency_key)):
            raise ServiceError("idempotency_key_required", 400)
        if not isinstance(request, dict) or len(canonical_bytes(request)) > 256 * 1024:
            raise ServiceError("invalid_request", 422)
        if (not isinstance(pins, dict) or pins.get("engine") not in {"pi", "hermes"}
                or not all(isinstance(pins.get(k), str) and pins[k] for k in ("runtime_revision", "image_digest"))
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", pins["image_digest"])):
            raise ServiceError("invalid_execution_pins", 503)
        if "execution_binding" in pins:
            try:
                binding = ExecutionBindingPins.model_validate(pins["execution_binding"])
                if (not binding.matches_base(pins)
                        or binding.profile != request.get("profile")
                        or binding.profile_revision != request.get("profile_revision")):
                    raise ValueError("Execution binding disagrees with submitted pins")
            except (ValidationError, ValueError, TypeError):
                raise ServiceError("invalid_execution_pins", 503) from None
            # Persist the validated, explicit wire representation. Registry
            # changes cannot alter this already accepted execution contract.
            pins = {**pins, "execution_binding": binding.model_dump(mode="json")}
        seconds = request.get("timeout_seconds")
        if type(seconds) is not int or not 1 <= seconds <= 3600:
            raise ServiceError("invalid_timeout", 422)
        request_hash = digest(request)
        with self._tx() as conn:
            self._check_inputs(conn, tenant, caller, request)
            existing = conn.execute(select(jobs).where(jobs.c.tenant == tenant,
                                    jobs.c.caller == caller, jobs.c.idempotency_key == idempotency_key)).mappings().first()
            if existing:
                if existing["request_hash"] != request_hash:
                    raise ServiceError("idempotency_conflict", 409)
                # Route changes never mutate an already accepted job's pins.
                return _job(existing), False
            if conn.scalar(select(func.count()).select_from(jobs).where(jobs.c.state == "queued")) >= self.max_queue:
                raise ServiceError("queue_full", 429)
            created = self.clock()
            row = {"id": str(uuid.uuid4()), "tenant": tenant, "caller": caller,
                   "idempotency_key": idempotency_key, "request_hash": request_hash,
                   "request": request, "pins": pins, "state": "queued", "created_at": created,
                   "deadline_at": created + seconds, "event_cursor": 0,
                   "attempt_id": None, "fence": 0, "lease_expires_at": None,
                   "cancel_requested": False, "outcome": None}
            conn.execute(insert(jobs).values(**row))
            self._event(conn, row, "job.queued")
            return _job(row), True

    def get_job(self, tenant, caller, job_id):
        with self._tx() as conn:
            return _job(self._find(conn, job_id, tenant, caller))

    def events(self, tenant, caller, job_id, after=0, limit=100):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ServiceError("invalid_cursor", 422)
        with self._tx() as conn:
            row = self._find(conn, job_id, tenant, caller)
            if after > row["event_cursor"]:
                raise ServiceError("invalid_cursor", 409)
            page = list(conn.execute(select(events).where(events.c.job_id == job_id,
                        events.c.seq > after).order_by(events.c.seq).limit(limit + 1)).mappings())
            visible = [dict(item) for item in page[:limit]]
            return {"items": visible, "next_cursor": visible[-1]["seq"] if visible else after,
                    "has_more": len(page) > limit}

    def cancel(self, tenant, caller, job_id):
        with self._tx() as conn:
            row = self._find(conn, job_id, tenant, caller)
            if row["state"] in TERMINAL or row["state"] == "cancelling":
                return _job(row)
            row["cancel_requested"] = True
            if row["state"] == "queued":
                row["state"] = "cancelled"
                row["outcome"] = {"state": "cancelled", "error_code": "cancelled_before_launch",
                                  "usage": None, "stop_receipt": None}
            else:
                row["state"] = "cancelling"
            conn.execute(update(jobs).where(jobs.c.id == job_id).values(
                state=row["state"], cancel_requested=True, outcome=row["outcome"]))
            if row["attempt_id"]:
                conn.execute(update(attempts).where(attempts.c.id == row["attempt_id"]).values(state=row["state"]))
            self._event(conn, row, "job." + row["state"])
            return _job(row)

    def _quarantine(self, conn, row, code):
        if row["state"] == "quarantined":
            return
        row["state"] = "quarantined"
        row["outcome"] = {"state": "quarantined", "error_code": code, "usage": None, "stop_receipt": None}
        conn.execute(update(jobs).where(jobs.c.id == row["id"]).values(state="quarantined", outcome=row["outcome"]))
        if row["attempt_id"]:
            conn.execute(update(attempts).where(attempts.c.id == row["attempt_id"]).values(
                state="quarantined", outcome=row["outcome"]))
        self._event(conn, row, "job.quarantined", {"error_code": code})

    def reconcile_expired(self):
        count = 0
        with self._tx() as conn:
            rows = conn.execute(select(jobs).where(jobs.c.state.in_(ACTIVE),
                                jobs.c.lease_expires_at <= self.clock())).mappings().all()
            for row in rows:
                self._quarantine(conn, dict(row), "lease_expired")
                count += 1
        return count

    def active(self):
        """Trusted controller recovery lookup, never an unauthenticated route.

        Quarantine is intentionally returned, retaining the unresolved resource
        binding. Callers must not replace or re-submit this physical attempt.
        """
        with self._tx() as conn:
            current = conn.execute(select(slot.c.job_id).where(slot.c.id == 1)).first()
            if current is None:
                raise ServiceError("store_not_initialized", 503)
            return None if current.job_id is None else _job(self._find(conn, current.job_id))

    def claim(self, lease_seconds=60, *, provisioning=None):
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ValueError("Invalid lease interval")
        provisioning = {} if provisioning is None else provisioning
        if (not isinstance(provisioning, dict) or not set(provisioning) <= {"pi", "hermes"}
                or any(not isinstance(value, str) or value not in {"external_static", "kubernetes_per_attempt"}
                       for value in provisioning.values())):
            raise ValueError("Invalid server-owned worker provisioning")
        with self._tx() as conn:
            control = conn.execute(select(slot).where(slot.c.id == 1).with_for_update()).mappings().first()
            if control is None:
                raise ServiceError("store_not_initialized", 503)
            if control["job_id"]:
                old = self._find(conn, control["job_id"])
                if old["state"] in ACTIVE and old["lease_expires_at"] <= self.clock():
                    self._quarantine(conn, old, "lease_expired")
                return None
            rows = conn.execute(select(jobs).where(jobs.c.state == "queued").order_by(jobs.c.created_at, jobs.c.id)).mappings().all()
            for raw in rows:
                row = dict(raw)
                if row["deadline_at"] <= self.clock():
                    row["state"] = "timed_out"
                    row["outcome"] = {"state": "timed_out", "error_code": "deadline_before_launch",
                                      "usage": None, "stop_receipt": None}
                    conn.execute(update(jobs).where(jobs.c.id == row["id"]).values(
                        state=row["state"], outcome=row["outcome"]))
                    self._event(conn, row, "job.timed_out", {"error_code": "deadline_before_launch"})
                    continue
                attempt_id = str(uuid.uuid4())
                fence = control["next_fence"]
                # The execution deadline is immutable. An extra bounded window
                # keeps control ownership alive only for stop and handoff.
                lease = min(self.clock() + lease_seconds, row["deadline_at"] + 45)
                intent = {"job_id": row["id"], "attempt_id": attempt_id, "fence": fence,
                          "pins": row["pins"], "deadline_at": row["deadline_at"],
                          "provisioning": provisioning.get(row["pins"]["engine"], "external_static")}
                conn.execute(insert(attempts).values(id=attempt_id, job_id=row["id"], fence=fence,
                             state="starting", created_at=self.clock(), lease_expires_at=lease,
                             deadline_at=row["deadline_at"], dispatch_intent=intent, outcome=None))
                conn.execute(update(slot).where(slot.c.id == 1).values(job_id=row["id"], next_fence=fence + 1))
                row.update(state="starting", attempt_id=attempt_id, fence=fence, lease_expires_at=lease)
                conn.execute(update(jobs).where(jobs.c.id == row["id"]).values(
                    state="starting", attempt_id=attempt_id, fence=fence, lease_expires_at=lease))
                self._event(conn, row, "job.starting")
                return _job(row)
            return None

    def _current(self, conn, job_id, attempt_id, fence):
        row = self._find(conn, job_id)
        if (row["attempt_id"] != attempt_id or type(fence) is not int or row["fence"] != fence
                or row["state"] not in ACTIVE):
            return None
        return row

    def _worker_current(self, conn, job_id, attempt_id, fence, *, launching=False):
        row = self._current(conn, job_id, attempt_id, fence)
        if (row is None or row["lease_expires_at"] <= self.clock()
                or launching and (row["cancel_requested"] or row["deadline_at"] <= self.clock())):
            raise ServiceError("stale_attempt", 409)
        return row

    def _worker_envelope(self, conn, row):
        """Measure immutable identity from authority, not a caller-supplied hash."""
        files = []
        for reference in row["request"].get("inputs", []):
            item = conn.execute(select(inputs).where(inputs.c.id == reference["input_id"],
                                inputs.c.tenant == row["tenant"], inputs.c.caller == row["caller"])).mappings().first()
            if (item is None or item["sha256"] != reference["sha256"]
                    or hashlib.sha256(item["content"]).hexdigest() != reference["sha256"]):
                raise ServiceError("input_integrity_invalid", 503)
            files.append(InlineFile.from_bytes(item["name"], item["content"]))
        return ExecutionEnvelope(job_id=row["id"], attempt_id=row["attempt_id"], fence=row["fence"],
            engine=row["pins"]["engine"], profile=row["request"]["profile"],
            profile_revision=row["request"]["profile_revision"],
            runtime_revision=row["pins"]["runtime_revision"], image_digest=row["pins"]["image_digest"],
            execution_binding=row["pins"].get("execution_binding"),
            deadline_at=float(row["deadline_at"]), lease_expires_at=float(row["lease_expires_at"]),
            task=row["request"]["task"], inputs=files)

    def _matching_worker_request(self, conn, envelope):
        if not isinstance(envelope, ExecutionEnvelope):
            raise ServiceError("invalid_worker_allocation", 422)
        row = self._find(conn, envelope.job_id)
        if (row["attempt_id"] != envelope.attempt_id or row["fence"] != envelope.fence
                or envelope.request_sha256 != self._worker_envelope(conn, row).request_sha256):
            raise ServiceError("stale_attempt", 409)
        return row

    @staticmethod
    def _attempt_provisioning(conn, row):
        value = conn.execute(select(attempts.c.dispatch_intent).where(
            attempts.c.id == row["attempt_id"], attempts.c.job_id == row["id"],
            attempts.c.fence == row["fence"])).first()
        if value is None:
            raise ServiceError("stale_attempt", 409)
        intent = value.dispatch_intent
        # Older/unknown records cannot prove that a static Worker did not run.
        return ("kubernetes_per_attempt" if isinstance(intent, dict)
                and intent.get("provisioning") == "kubernetes_per_attempt" else "external_static")

    def attempt_provisioning(self, envelope):
        """Read immutable attempt ownership, independently of current routing."""
        with self._tx() as conn:
            return self._attempt_provisioning(conn, self._matching_worker_request(conn, envelope))

    @staticmethod
    def _allocation(conn, attempt_id, request_sha256=None):
        row = conn.execute(select(worker_allocations).where(
            worker_allocations.c.attempt_id == attempt_id)).mappings().first()
        if row is None:
            raise ServiceError("worker_allocation_missing", 404)
        if request_sha256 is not None and row["request_sha256"] != request_sha256:
            raise ServiceError("worker_allocation_conflict", 409)
        return dict(row)

    def reserve_worker(self, envelope, pool_id, namespace, pod_name, intent_sha256, agent_uid=None):
        """Persist one immutable resource intent before a Kubernetes API call.

        The allocation contains only identity/pins digests. It deliberately
        stores neither task/Pod content nor tokens, receipt keys or credentials.
        A configured host-global Agent UID is burned for this attempt forever;
        nullable UIDs exist only for legacy/offline fixtures, not production.
        """
        if (not isinstance(envelope, ExecutionEnvelope)
                or not isinstance(pool_id, str) or not _CODE.fullmatch(pool_id)
                or not isinstance(namespace, str) or not _RESOURCE_NAME.fullmatch(namespace)
                or not isinstance(pod_name, str) or not _RESOURCE_NAME.fullmatch(pod_name)
                or not isinstance(intent_sha256, str) or not _SHA256.fullmatch(intent_sha256)
                or agent_uid is not None and (type(agent_uid) is not int or not 65536 <= agent_uid <= 2147483647)):
            raise ServiceError("invalid_worker_allocation", 422)
        with self._tx() as conn:
            row = self._worker_current(conn, envelope.job_id, envelope.attempt_id,
                                       envelope.fence, launching=True)
            if (envelope.request_sha256 != self._worker_envelope(conn, row).request_sha256
                    or not self.clock() < envelope.lease_expires_at <= row["lease_expires_at"]):
                raise ServiceError("worker_allocation_conflict", 409)
            immutable = {"attempt_id": envelope.attempt_id, "job_id": envelope.job_id,
                         "fence": envelope.fence, "engine": envelope.engine,
                         "request_sha256": envelope.request_sha256, "pool_id": pool_id,
                         "namespace": namespace, "pod_name": pod_name, "intent_sha256": intent_sha256,
                         "agent_uid": agent_uid}
            existing = conn.execute(select(worker_allocations).where(
                worker_allocations.c.attempt_id == envelope.attempt_id)).mappings().first()
            if existing is not None:
                if any(existing[name] != value for name, value in immutable.items()) or existing["retired"]:
                    raise ServiceError("worker_allocation_conflict", 409)
                return dict(existing)
            named = conn.execute(select(worker_allocations.c.attempt_id).where(
                worker_allocations.c.pool_id == pool_id, worker_allocations.c.namespace == namespace,
                worker_allocations.c.pod_name == pod_name)).first()
            if named is not None:
                raise ServiceError("worker_allocation_conflict", 409)
            if agent_uid is not None and conn.execute(select(worker_allocations.c.attempt_id).where(
                    worker_allocations.c.agent_uid == agent_uid)).first() is not None:
                raise ServiceError("worker_allocation_conflict", 409)
            values = {**immutable, "pod_uid": None, "create_claimed": False, "worker_start_claimed": False,
                      "retired": False, "candidate_result": None, "stop_observed_at": None,
                      "created_at": self.clock()}
            conn.execute(insert(worker_allocations).values(**values))
            return values

    def claim_worker_create(self, attempt_id, request_sha256):
        """At most one controller may issue resource creation for an attempt.

        Crash after this committed claim is intentionally uncertain. A caller
        may inspect the original name, but must never create it again after a
        404, because a previous Pod with that name might already have executed.
        """
        with self._tx() as conn:
            allocation = self._allocation(conn, attempt_id, request_sha256)
            self._worker_current(conn, allocation["job_id"], attempt_id, allocation["fence"], launching=True)
            if allocation["retired"]:
                raise ServiceError("worker_allocation_conflict", 409)
            if allocation["create_claimed"]:
                return False
            changed = conn.execute(update(worker_allocations).where(
                worker_allocations.c.attempt_id == attempt_id,
                worker_allocations.c.create_claimed.is_(False),
                worker_allocations.c.retired.is_(False)).values(create_claimed=True))
            return changed.rowcount == 1

    def get_worker_allocation(self, attempt_id):
        """Trusted-controller resource lookup also works after logical finish."""
        with self._tx() as conn:
            return self._allocation(conn, attempt_id)

    def worker_uid_available(self, uid_start, uid_count):
        """Advisory capacity only; reservation still fences every actual launch."""
        if (type(uid_start) is not int or not 65536 <= uid_start <= 2147483647
                or type(uid_count) is not int or not 1 <= uid_count <= 1000000
                or uid_start + uid_count - 1 > 2147483647):
            raise ServiceError("invalid_worker_allocation", 422)
        with self._tx() as conn:
            control = conn.execute(select(slot).where(slot.c.id == 1)).mappings().first()
            if control is None:
                raise ServiceError("store_not_initialized", 503)
            fence = control["next_fence"]
            if fence > uid_count:
                return False
            uid = uid_start + fence - 1
            return conn.execute(select(worker_allocations.c.attempt_id).where(
                worker_allocations.c.agent_uid == uid)).first() is None

    def resolve_uncreated_attempt(self, envelope, code="worker_not_created"):
        """Kubernetes-controller-only release before resource creation authority.

        The immutable attempt must belong to the Kubernetes provisioner;
        absence of this table's records says nothing about a static Worker. The
        same transaction lock serializes this decision with resource reservation
        and creation claims. An unclaimed intent is retired here, retaining its
        burned UID; no old controller may acquire launch authority afterwards.
        No stopped receipt or artifacts are manufactured.
        """
        if (not isinstance(envelope, ExecutionEnvelope) or not isinstance(code, str)
                or not _CODE.fullmatch(code)):
            raise ServiceError("invalid_worker_allocation", 422)
        with self._tx() as conn:
            row = self._matching_worker_request(conn, envelope)
            if self._attempt_provisioning(conn, row) != "kubernetes_per_attempt":
                return None
            allocation = conn.execute(select(worker_allocations).where(
                worker_allocations.c.attempt_id == envelope.attempt_id)).mappings().first()
            if allocation is not None:
                if (allocation["job_id"] != row["id"] or allocation["fence"] != row["fence"]
                        or allocation["request_sha256"] != envelope.request_sha256):
                    raise ServiceError("worker_allocation_conflict", 409)
                if (allocation["create_claimed"] or allocation["worker_start_claimed"]
                        or allocation["pod_uid"] is not None or allocation["candidate_result"] is not None
                        or allocation["stop_observed_at"] is not None):
                    return None
            previous = row["outcome"]
            if (row["state"] in {"cancelled", "timed_out", "failed"}
                    and isinstance(previous, dict) and previous.get("error_code") == code
                    and previous.get("usage") is None and previous.get("stop_receipt") is None):
                return _job(row)
            if allocation is not None and allocation["retired"]:
                return None
            control = conn.execute(select(slot).where(slot.c.id == 1).with_for_update()).mappings().first()
            if (row["state"] not in ACTIVE | {"quarantined"}
                    or control is None or control["job_id"] != row["id"]):
                raise ServiceError("stale_attempt", 409)
            state = ("cancelled" if row["cancel_requested"] else
                     "timed_out" if row["deadline_at"] <= self.clock() else "failed")
            outcome = {"state": state, "error_code": code, "usage": None, "stop_receipt": None}
            row.update(state=state, outcome=outcome)
            if allocation is not None:
                conn.execute(update(worker_allocations).where(
                    worker_allocations.c.attempt_id == envelope.attempt_id).values(retired=True))
            conn.execute(update(jobs).where(jobs.c.id == row["id"]).values(state=state, outcome=outcome))
            conn.execute(update(attempts).where(attempts.c.id == envelope.attempt_id,
                         attempts.c.fence == envelope.fence).values(state=state, outcome=outcome))
            self._event(conn, row, "job." + state, {"error_code": code})
            conn.execute(update(slot).where(slot.c.id == 1, slot.c.job_id == row["id"]).values(job_id=None))
            return _job(row)

    def claim_worker_start(self, attempt_id, request_sha256):
        """Persist sole authority for native PUT, separately from Pod creation."""
        with self._tx() as conn:
            allocation = self._allocation(conn, attempt_id, request_sha256)
            self._worker_current(conn, allocation["job_id"], attempt_id, allocation["fence"], launching=True)
            if (allocation["retired"] or not allocation["create_claimed"] or allocation["pod_uid"] is None
                    or allocation["candidate_result"] is not None):
                raise ServiceError("worker_allocation_conflict", 409)
            if allocation["worker_start_claimed"]:
                return False
            changed = conn.execute(update(worker_allocations).where(
                worker_allocations.c.attempt_id == attempt_id,
                worker_allocations.c.worker_start_claimed.is_(False),
                worker_allocations.c.retired.is_(False)).values(worker_start_claimed=True))
            return changed.rowcount == 1

    def bind_worker(self, attempt_id, request_sha256, pod_uid):
        """Record observed physical identity exactly once, including after cancel.

        This observation cannot start execution. A late creation response still
        needs to bind its UID so cancellation/recovery can stop the right Pod.
        """
        if not isinstance(pod_uid, str) or not _POD_UID.fullmatch(pod_uid):
            raise ServiceError("invalid_worker_allocation", 422)
        with self._tx() as conn:
            allocation = self._allocation(conn, attempt_id, request_sha256)
            if (not allocation["create_claimed"] or allocation["retired"]
                    or allocation["pod_uid"] not in {None, pod_uid}):
                raise ServiceError("worker_allocation_conflict", 409)
            if allocation["pod_uid"] is None:
                conn.execute(update(worker_allocations).where(
                    worker_allocations.c.attempt_id == attempt_id).values(pod_uid=pod_uid))
                allocation["pod_uid"] = pod_uid
            return allocation

    def stage_worker_result(self, attempt_id, request_sha256, result_wire):
        """Persist an authenticated candidate before terminating its Pod.

        The trusted provider authenticates the receipt first; these checks also
        bind the stored object to this allocation. Nothing here publishes Job
        artifacts or treats native completion as host-observed process stop.
        """
        try:
            if not isinstance(result_wire, dict) or len(canonical_bytes(result_wire)) > 12 * 1024 * 1024:
                raise ValueError()
            result = WorkerResult.model_validate(result_wire)
            if result.state == "quarantined" or result.stop_receipt is None:
                raise ValueError()
            if (len({item.name for item in result.artifacts}) != len(result.artifacts)
                    or sum(len(item.content()) for item in result.artifacts) > self.max_artifact_bytes):
                raise ValueError()
            normalized = result.model_dump()
        except (ValueError, TypeError, RecursionError):
            raise ServiceError("invalid_worker_result", 422) from None
        with self._tx() as conn:
            allocation = self._allocation(conn, attempt_id, request_sha256)
            receipt = result.stop_receipt
            stopped_without_result = (
                result.state in {"failed", "cancelled", "timed_out"} and not result.artifacts
                and result.error_code == "capsule_stopped_without_result"
                and "k8s:" + allocation["namespace"] + ":" + str(allocation["pod_uid"]) in receipt.resource_ids)
            if (receipt.job_id != allocation["job_id"] or receipt.attempt_id != attempt_id
                    or receipt.fence != allocation["fence"] or receipt.engine != allocation["engine"]
                    or receipt.request_sha256 != request_sha256
                    or receipt.result_sha256 != result.result_digest()
                    or not allocation["create_claimed"]
                    or not (allocation["worker_start_claimed"] or stopped_without_result)
                    or allocation["pod_uid"] is None):
                raise ServiceError("worker_allocation_conflict", 409)
            if allocation["candidate_result"] is not None:
                if digest(allocation["candidate_result"]) != digest(normalized):
                    raise ServiceError("worker_allocation_conflict", 409)
                return allocation
            if allocation["retired"]:
                raise ServiceError("worker_allocation_conflict", 409)
            conn.execute(update(worker_allocations).where(
                worker_allocations.c.attempt_id == attempt_id).values(candidate_result=normalized))
            allocation["candidate_result"] = normalized
            return allocation

    def observe_worker_stopped(self, attempt_id, request_sha256, pod_uid, observed_at):
        """Trusted-provider observation, never an external boolean callback."""
        if (not isinstance(pod_uid, str) or not _POD_UID.fullmatch(pod_uid)
                or type(observed_at) not in {int, float} or not math.isfinite(observed_at)
                or not 0 < observed_at <= self.clock() + 5):
            raise ServiceError("invalid_worker_stop_observation", 422)
        with self._tx() as conn:
            allocation = self._allocation(conn, attempt_id, request_sha256)
            if (allocation["pod_uid"] != pod_uid or not allocation["create_claimed"]
                    or allocation["candidate_result"] is None):
                raise ServiceError("worker_allocation_conflict", 409)
            if allocation["stop_observed_at"] is None:
                if allocation["retired"]:
                    raise ServiceError("worker_allocation_conflict", 409)
                conn.execute(update(worker_allocations).where(
                    worker_allocations.c.attempt_id == attempt_id).values(stop_observed_at=float(observed_at)))
                allocation["stop_observed_at"] = float(observed_at)
            return allocation

    def retire_worker(self, attempt_id, request_sha256, pod_uid):
        """Mark exact observed resources retired; this is not a stop proof."""
        if not isinstance(pod_uid, str) or not _POD_UID.fullmatch(pod_uid):
            raise ServiceError("invalid_worker_allocation", 422)
        with self._tx() as conn:
            allocation = self._allocation(conn, attempt_id, request_sha256)
            if (allocation["pod_uid"] != pod_uid or not allocation["create_claimed"]
                    or allocation["stop_observed_at"] is None):
                raise ServiceError("worker_allocation_conflict", 409)
            if not allocation["retired"]:
                conn.execute(update(worker_allocations).where(
                    worker_allocations.c.attempt_id == attempt_id).values(retired=True))
                allocation["retired"] = True
            return allocation

    def resolve_worker_quarantine(self, job_id, attempt_id, fence):
        """Release only a quarantined slot with durable exact physical stop.

        The Kubernetes provider must authenticate its recovered receipt before
        calling this internal method. This guard additionally requires that the
        exact Pod UID was observed stopped and retired. Recovery never publishes
        candidate artifacts, replaces the attempt, or grants another model call.
        """
        with self._tx() as conn:
            row = self._find(conn, job_id)
            if (row["attempt_id"] != attempt_id or type(fence) is not int or row["fence"] != fence):
                raise ServiceError("stale_attempt", 409)
            allocation = self._allocation(conn, attempt_id)
            if (allocation["job_id"] != job_id or allocation["fence"] != fence
                    or not allocation["pod_uid"] or allocation["candidate_result"] is None
                    or allocation["stop_observed_at"] is None or not allocation["retired"]):
                raise ServiceError("worker_stop_unverified", 409)
            resolved = row["outcome"]
            if (row["state"] in {"cancelled", "timed_out", "failed"}
                    and isinstance(resolved, dict) and resolved.get("error_code") == "quarantine_stopped"):
                return _job(row)
            control = conn.execute(select(slot).where(slot.c.id == 1).with_for_update()).mappings().first()
            if row["state"] != "quarantined" or control is None or control["job_id"] != job_id:
                raise ServiceError("stale_attempt", 409)
            state = ("cancelled" if row["cancel_requested"] else
                     "timed_out" if row["deadline_at"] <= self.clock() else "failed")
            candidate = allocation["candidate_result"]
            outcome = {"state": state, "error_code": "quarantine_stopped",
                       "usage": candidate.get("usage"), "stop_receipt": candidate.get("stop_receipt")}
            row.update(state=state, outcome=outcome)
            conn.execute(update(jobs).where(jobs.c.id == job_id).values(state=state, outcome=outcome))
            conn.execute(update(attempts).where(attempts.c.id == attempt_id,
                         attempts.c.fence == fence).values(state=state, outcome=outcome))
            self._event(conn, row, "job." + state, {"error_code": "quarantine_stopped"})
            conn.execute(update(slot).where(slot.c.id == 1, slot.c.job_id == job_id).values(job_id=None))
            return _job(row)

    def worker_event_cursor(self, job_id, attempt_id, fence):
        with self._tx() as conn:
            self._worker_current(conn, job_id, attempt_id, fence)
            row = conn.execute(select(worker_event_cursors).where(
                worker_event_cursors.c.attempt_id == attempt_id)).mappings().first()
            if row is None:
                return 0
            if row["job_id"] != job_id or row["fence"] != fence:
                raise ServiceError("stale_attempt", 409)
            return row["cursor"]

    def ingest_worker_events(self, job_id, attempt_id, fence, after, items, next_cursor):
        """Atomically ingest an exact sanitized page and advance its durable cursor."""
        if (type(after) is not int or type(next_cursor) is not int
                or not 0 <= after <= next_cursor <= 500 or not isinstance(items, list)
                or len(items) > 100 or next_cursor != after + len(items)):
            raise ServiceError("invalid_worker_events", 422)
        for index, item in enumerate(items, after + 1):
            if (not isinstance(item, dict) or set(item) != {"seq", "type", "payload"}
                    or type(item["seq"]) is not int or item["seq"] != index
                    or item["type"] != "engine.progress" or not isinstance(item["payload"], dict)
                    or set(item["payload"]) != {"event"} or not isinstance(item["payload"]["event"], str)
                    or item["payload"]["event"] not in _WORKER_EVENT_KINDS):
                raise ServiceError("invalid_worker_events", 422)
        with self._tx() as conn:
            row = self._worker_current(conn, job_id, attempt_id, fence)
            saved = conn.execute(select(worker_event_cursors).where(
                worker_event_cursors.c.attempt_id == attempt_id)).mappings().first()
            cursor = 0 if saved is None else saved["cursor"]
            if saved is not None and (saved["job_id"] != job_id or saved["fence"] != fence):
                raise ServiceError("stale_attempt", 409)
            if cursor >= next_cursor:
                return cursor
            if cursor != after:
                raise ServiceError("worker_event_cursor_conflict", 409)
            for item in items:
                self._event(conn, row, item["type"], item["payload"])
            if saved is None:
                conn.execute(insert(worker_event_cursors).values(
                    attempt_id=attempt_id, job_id=job_id, fence=fence, cursor=next_cursor))
            else:
                conn.execute(update(worker_event_cursors).where(
                    worker_event_cursors.c.attempt_id == attempt_id).values(cursor=next_cursor))
            return next_cursor

    def mark_running(self, job_id, attempt_id, fence):
        with self._tx() as conn:
            row = self._current(conn, job_id, attempt_id, fence)
            if row is None or row["state"] != "starting" or row["lease_expires_at"] <= self.clock():
                return False
            row["state"] = "running"
            conn.execute(update(jobs).where(jobs.c.id == job_id).values(state="running"))
            conn.execute(update(attempts).where(attempts.c.id == attempt_id).values(state="running"))
            self._event(conn, row, "job.running")
            return True

    def renew(self, job_id, attempt_id, fence, lease_seconds=60):
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ValueError("Invalid lease interval")
        with self._tx() as conn:
            row = self._current(conn, job_id, attempt_id, fence)
            now = self.clock()
            if row is None or row["lease_expires_at"] <= now or row["deadline_at"] + 45 <= now:
                return False
            lease = min(now + lease_seconds, row["deadline_at"] + 45)
            conn.execute(update(jobs).where(jobs.c.id == job_id).values(lease_expires_at=lease))
            conn.execute(update(attempts).where(attempts.c.id == attempt_id).values(lease_expires_at=lease))
            return True

    def append_event(self, job_id, attempt_id, fence, event_type, payload):
        if event_type != "engine.progress":
            raise ServiceError("invalid_event", 422)
        with self._tx() as conn:
            row = self._current(conn, job_id, attempt_id, fence)
            if row is None or row["lease_expires_at"] <= self.clock():
                return False
            return self._event(conn, row, event_type, payload)

    def finish(self, job_id, attempt_id, fence, outcome, artifacts=None, *, stopped):
        """Trusted controller-only commit, after authenticating exact stop evidence.

        ``stopped`` is deliberately not an HTTP callback contract. It must be
        supplied by trusted worker-result verification, never from request JSON.
        """
        if (not isinstance(outcome, dict) or outcome.get("state") not in TERMINAL
                or len(canonical_bytes(outcome)) > 64 * 1024):
            raise ServiceError("invalid_outcome", 422)
        code = outcome.get("error_code")
        if code is not None and (not isinstance(code, str) or not _CODE.fullmatch(code)):
            raise ServiceError("invalid_outcome", 422)
        candidates = [] if artifacts is None else artifacts
        if not isinstance(candidates, list) or len(candidates) > 32:
            raise ServiceError("artifact_size_limit", 413)
        checked = []
        names = set()
        total = 0
        for item in candidates:
            if not isinstance(item, dict):
                raise ServiceError("invalid_artifact", 422)
            name = _name(item.get("name"))
            content = item.get("content")
            if type(content) is not bytes or not content or name in names:
                raise ServiceError("invalid_artifact", 422)
            names.add(name)
            total += len(content)
            if total > self.max_artifact_bytes:
                raise ServiceError("artifact_size_limit", 413)
            checksum = hashlib.sha256(content).hexdigest()
            if item.get("sha256", checksum) != checksum:
                raise ServiceError("artifact_hash_mismatch", 409)
            checked.append({"id": str(uuid.uuid4()), "job_id": job_id, "attempt_id": attempt_id,
                            "name": name, "sha256": checksum, "size": len(content), "content": content})
        with self._tx() as conn:
            row = self._current(conn, job_id, attempt_id, fence)
            if row is None:
                raise ServiceError("stale_attempt", 409)
            if stopped is not True:
                self._quarantine(conn, row, code or "stop_unverified")
                return _job(row)
            if row["lease_expires_at"] <= self.clock():
                self._quarantine(conn, row, "lease_expired")
                return _job(row)
            final = dict(outcome)
            # Cancellation wins an in-flight completion race. No late result is
            # exported when the business already requested stop.
            if row["cancel_requested"]:
                final.update(state="cancelled", error_code="cancelled")
            elif row["deadline_at"] <= self.clock():
                final.update(state="timed_out", error_code="deadline_exceeded")
            state = final["state"]
            final.setdefault("usage", None)
            final.setdefault("stop_receipt", None)
            row.update(state=state, outcome=final)
            if state in {"completed", "needs_input"} and checked:
                conn.execute(insert(globals()["artifacts"]), checked)
            conn.execute(update(jobs).where(jobs.c.id == job_id).values(state=state, outcome=final))
            conn.execute(update(attempts).where(attempts.c.id == attempt_id).values(state=state, outcome=final))
            payload = {"error_code": final["error_code"]} if final.get("error_code") else {}
            self._event(conn, row, "job." + state, payload)
            if state != "quarantined":
                conn.execute(update(slot).where(slot.c.id == 1, slot.c.job_id == job_id).values(job_id=None))
            return _job(row)

    def get_artifacts(self, tenant, caller, job_id):
        with self._tx() as conn:
            self._find(conn, job_id, tenant, caller)
            rows = conn.execute(select(artifacts).where(artifacts.c.job_id == job_id).order_by(artifacts.c.name)).mappings()
            return [self._artifact(row) for row in rows]

    @staticmethod
    def _artifact(row, content=False):
        result = {key: row[key] for key in ("id", "job_id", "attempt_id", "name", "sha256", "size")}
        result["artifact_id"] = result["id"]
        if content:
            result["content"] = row["content"]
        return result

    def get_artifact(self, tenant, caller, artifact_id):
        with self._tx() as conn:
            row = conn.execute(select(artifacts).join(jobs, jobs.c.id == artifacts.c.job_id).where(
                artifacts.c.id == artifact_id, jobs.c.tenant == tenant, jobs.c.caller == caller)).mappings().first()
            if row is None:
                raise ServiceError("not_found", 404)
            return self._artifact(row, True)
