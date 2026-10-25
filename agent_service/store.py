"""Durable, engine-neutral queue. Public ownership never comes from job JSON.

PostgreSQL is the supported service database. SQLite is available only when a
trusted test/development caller explicitly opts in. No database is touched on
import. A quarantined attempt retains the global slot; a lease is not stop proof.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import re
import time
import uuid

from sqlalchemy import (Boolean, Column, Float, ForeignKey, Integer, JSON,
                        LargeBinary, MetaData, String, Table, UniqueConstraint,
                        create_engine, func, insert, select, text, update)
from sqlalchemy.engine import make_url
from sqlalchemy.pool import StaticPool

from .contracts import ServiceError, canonical_bytes, digest


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

    def claim(self, lease_seconds=60):
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ValueError("Invalid lease interval")
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
                          "pins": row["pins"], "deadline_at": row["deadline_at"]}
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
