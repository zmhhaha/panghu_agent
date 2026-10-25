"""Host-owned Pi/Hermes drivers, without HTTP, queue or publication authority.

These drivers are disabled by default. The host supplies an isolated runtime,
durable native bindings and a stop controller covering *all* attempt writers.
An engine completing its turn never grants business acceptance or publication.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable, Protocol


MAX_PAYLOAD_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z")
_NATIVE_ID = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,199}\Z")


def _json_copy(value: Any, limit: int) -> Any:
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False,
                     separators=(",", ":")).encode("utf-8")
    if len(raw) > limit:
        raise ValueError("Bounded execution data exceeded its limit")
    return json.loads(raw)


def _without_private(value: Any) -> Any:
    """Private dispatch values are supplied only by a trusted wire builder."""
    if isinstance(value, dict):
        return {key: _without_private(item) for key, item in value.items()
                if isinstance(key, str) and not key.startswith("_")}
    if isinstance(value, list):
        return [_without_private(item) for item in value]
    return value


@dataclass(frozen=True)
class EngineRequest:
    execution_id: str
    profile: str
    payload: dict
    timeout_seconds: int
    deadline_at: datetime

    def __post_init__(self):
        if (type(self.execution_id) is not str or not _NAME.fullmatch(self.execution_id)
                or type(self.profile) is not str or not _NAME.fullmatch(self.profile)
                or type(self.payload) is not dict or type(self.timeout_seconds) is not int
                or not 1 <= self.timeout_seconds <= 86400
                or not isinstance(self.deadline_at, datetime)
                or self.deadline_at.tzinfo is None or self.deadline_at.utcoffset() is None):
            raise ValueError("Invalid bounded engine request")
        object.__setattr__(self, "payload", _without_private(_json_copy(self.payload, MAX_PAYLOAD_BYTES)))


@dataclass(frozen=True)
class NativeOutcome:
    status: str
    output: dict | str | None = None
    usage: dict | None = None
    native_ids: dict[str, str] = field(default_factory=dict)
    stopped: bool = False
    error_code: str | None = None
    resource_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"status": self.status, "output": deepcopy(self.output),
                "usage": deepcopy(self.usage), "native_ids": dict(self.native_ids),
                "stopped": self.stopped, "error_code": self.error_code,
                "resource_ids": list(self.resource_ids)}


@dataclass(frozen=True)
class StopEvidence:
    execution_id: str
    all_writers_stopped: bool
    resource_ids: tuple[str, ...]


class StopController(Protocol):
    def stop(self, execution_id: str) -> None: ...
    def prove_stopped(self, execution_id: str) -> StopEvidence: ...


@dataclass(frozen=True)
class NativeBinding:
    execution_id: str
    session_id: str
    admission_key: str
    admission_started: bool = False
    run_id: str | None = None


class NativeBindingStore(Protocol):
    """Production implementations must persist before returning.

    prepare atomically creates or returns an identical immutable binding.
    mark_admission_started is a compare-and-set: only its one winner returns True.
    bind_run accepts only the same run on replay. No in-memory production default
    is provided; the service's durable repository implements these operations.
    """
    def load(self, execution_id: str) -> NativeBinding | None: ...
    def prepare(self, execution_id: str, session_id: str, admission_key: str) -> NativeBinding: ...
    def mark_admission_started(self, execution_id: str) -> bool: ...
    def bind_run(self, execution_id: str, run_id: str) -> NativeBinding: ...


class _DriverError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _native_id(value: Any) -> str:
    if type(value) is not str or not _NATIVE_ID.fullmatch(value) or value in (".", ".."):
        raise _DriverError("native_protocol_error")
    return value


def _usage(value: Any) -> dict | None:
    """Known counters only. Missing supplier cost stays unknown, never zero."""
    if not isinstance(value, dict):
        return None
    result = {}
    for name in ("input_tokens", "output_tokens", "total_tokens"):
        counter = value.get(name)
        if type(counter) is int and 0 <= counter <= 2**63 - 1:
            result[name] = counter
    # A model's prose or locally estimated cost is not supplier billing evidence.
    if not result:
        return None
    return {**result, "reported_cost": None, "cost_status": "unknown"}


class _BaseDriver:
    def __init__(self, stop_controller: StopController | None, *, enabled: bool = False,
                 clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        if type(enabled) is not bool:
            raise ValueError("Execution enablement must be an operator boolean")
        self.stop_controller = stop_controller
        self.enabled = enabled
        self._clock, self._now = clock, now

    def _budget(self, request: EngineRequest) -> float:
        remaining = (request.deadline_at - self._now()).total_seconds()
        return self._clock() + max(0, min(request.timeout_seconds, remaining))

    def _check(self, cancel: threading.Event, end: float):
        if cancel.is_set():
            raise _DriverError("cancelled")
        if self._clock() >= end:
            raise _DriverError("timed_out")

    @staticmethod
    def _emit(emit: Callable[[dict], None], kind: str, **values):
        try:
            emit({"type": kind, **values})
        except Exception:
            raise _DriverError("event_sink_failed") from None

    def _fence(self, request: EngineRequest, outcome: NativeOutcome) -> NativeOutcome:
        try:
            if self.stop_controller is None:
                raise _DriverError("stop_controller_unavailable")
            self.stop_controller.stop(request.execution_id)
            proof = self.stop_controller.prove_stopped(request.execution_id)
            if (type(proof) is not StopEvidence or proof.execution_id != request.execution_id
                    or proof.all_writers_stopped is not True
                    or type(proof.resource_ids) is not tuple or not proof.resource_ids
                    or len(proof.resource_ids) > 64 or len(set(proof.resource_ids)) != len(proof.resource_ids)
                    or any(type(item) is not str or not 1 <= len(item) <= 200
                           or any(ord(char) < 32 or ord(char) == 127 for char in item)
                           for item in proof.resource_ids)):
                raise _DriverError("stop_evidence_missing")
        except Exception:
            return NativeOutcome("quarantined", native_ids=outcome.native_ids,
                                 error_code="writers_not_stopped")
        return NativeOutcome(outcome.status, outcome.output, outcome.usage,
                             outcome.native_ids, True, outcome.error_code, proof.resource_ids)


class PiDriver(_BaseDriver):
    """Runs an explicitly configured PiRuntime through a trusted wire builder.

    The builder is server code. It may add a persisted runtime UID and fixed
    revisions after EngineRequest has stripped private API input fields.
    Files remain candidate data for a separate stopped-artifact collector.
    """
    engine = "pi"

    def __init__(self, runtime, stop_controller: StopController | None, *,
                 wire_builder: Callable[[EngineRequest], dict] | None = None,
                 enabled: bool = False, **kwargs):
        super().__init__(stop_controller, enabled=enabled, **kwargs)
        self.runtime, self.wire_builder = runtime, wire_builder

    @property
    def ready(self) -> bool:
        if not self.enabled or self.stop_controller is None or self.wire_builder is None or self.runtime is None:
            return False
        try:
            health = self.runtime.health()
            return type(health) is dict and health.get("ready") is True and health.get("fixture_mode") is not True
        except Exception:
            return False

    def execute(self, request: EngineRequest, workspace: Path,
                emit: Callable[[dict], None], cancel: threading.Event) -> NativeOutcome:
        outcome = NativeOutcome("failed", error_code="engine_error")
        end = self._budget(request)
        try:
            self._check(cancel, end)
            if not self.enabled or self.runtime is None or self.wire_builder is None:
                raise _DriverError("engine_unavailable")
            # Readiness is enforced by the real runtime as well. Fixture runtimes
            # are explicitly injected by tests; never enable production readiness.
            wire = self.wire_builder(request)
            if type(wire) is not dict:
                raise _DriverError("invalid_native_request")
            wire = deepcopy(wire)
            if (wire.get("timeout_seconds") != request.timeout_seconds
                    or type(wire.get("execution_deadline_at")) is not str):
                raise _DriverError("native_deadline_mismatch")
            try:
                deadline = datetime.fromisoformat(wire["execution_deadline_at"].replace("Z", "+00:00"))
            except ValueError:
                raise _DriverError("native_deadline_mismatch") from None
            if deadline.tzinfo is None or deadline.utcoffset() is None or deadline != request.deadline_at:
                raise _DriverError("native_deadline_mismatch")

            def forward(event):
                self._check(cancel, end)
                if type(event) is not dict:
                    raise _DriverError("native_protocol_error")
                # Runtime output and tool payloads never enter public events.
                kind = event.get("type")
                if kind in {"runtime_started", "runtime_finished"}:
                    self._emit(emit, kind)
                elif kind == "pi_event" and event.get("event") in {
                        "agent_start", "agent_end", "turn_start", "turn_end", "message_end",
                        "tool_execution_start", "tool_execution_end"}:
                    self._emit(emit, "engine_progress", event=event["event"])

            self.runtime.execute(wire, Path(workspace), forward, cancel)
            self._check(cancel, end)
            # PiRuntime supplies files, not a trusted return payload or billing.
            outcome = NativeOutcome("completed")
        except _DriverError as exc:
            status = exc.code if exc.code in {"cancelled", "timed_out"} else "failed"
            outcome = NativeOutcome(status, error_code=exc.code)
        except Exception as exc:
            # Only reviewed runtime error categories are retained; exception text
            # may include credentials, input material or host paths.
            code = getattr(exc, "code", "engine_error")
            if code not in {"cancelled", "timed_out", "runtime_not_ready", "runtime_busy",
                            "runtime_exit", "runtime_protocol_error", "runtime_idle_timeout",
                            "runtime_output_limit", "process_safety_unverified", "model_error",
                            "model_http_transient", "model_mismatch"}:
                code = "engine_error"
            outcome = NativeOutcome(code if code in {"cancelled", "timed_out"} else "failed", error_code=code)
        finally:
            outcome = self._fence(request, outcome)
        return outcome


class HermesDriver(_BaseDriver):
    """Adapts a dedicated loopback Hermes gateway, never a personal dashboard.

    POST admission happens at most once after a durable CAS intent. A lost
    admission response is uncertain and stops the host; it is never retried.
    Only GET polling of the already bound run receives bounded recovery.
    """
    engine = "hermes"

    def __init__(self, client, bindings: NativeBindingStore | None,
                 stop_controller: StopController | None, *, enabled: bool = False,
                 poll_seconds: float = 0.25, status_retries: int = 3,
                 wait: Callable[[threading.Event, float], bool] | None = None, **kwargs):
        super().__init__(stop_controller, enabled=enabled, **kwargs)
        if not 0 < poll_seconds <= 10 or type(status_retries) is not int or not 0 <= status_retries <= 3:
            raise ValueError("Invalid bounded Hermes polling")
        self.client, self.bindings = client, bindings
        self.poll_seconds, self.status_retries = poll_seconds, status_retries
        self._wait = wait or (lambda event, seconds: event.wait(seconds))

    @property
    def ready(self) -> bool:
        if not self.enabled or self.client is None or self.bindings is None or self.stop_controller is None:
            return False
        try:
            from .hermes_client import HermesClient
            # Only the actual bounded loopback client can advertise readiness.
            return isinstance(self.client, HermesClient) and type(self.client.capabilities()) is dict
        except Exception:
            return False

    def _checked_binding(self, binding, request: EngineRequest) -> NativeBinding:
        if (type(binding) is not NativeBinding or binding.execution_id != request.execution_id
                or type(binding.admission_started) is not bool):
            raise _DriverError("native_binding_invalid")
        _native_id(binding.session_id)
        if (type(binding.admission_key) is not str or not binding.admission_key
                or len(binding.admission_key) > 255
                or any(not 33 <= ord(char) <= 126 for char in binding.admission_key)):
            raise _DriverError("native_binding_invalid")
        if binding.run_id is not None:
            _native_id(binding.run_id)
            if not binding.admission_started:
                raise _DriverError("native_binding_invalid")
        return binding

    def execute(self, request: EngineRequest, workspace: Path,
                emit: Callable[[dict], None], cancel: threading.Event) -> NativeOutcome:
        del workspace  # Workspace ownership and mounts belong to the host.
        outcome = NativeOutcome("failed", error_code="engine_error")
        native_ids = {}
        run_id = None
        end = self._budget(request)
        try:
            self._check(cancel, end)
            if not self.enabled or self.client is None or self.bindings is None:
                raise _DriverError("engine_unavailable")
            if set(request.payload) - {"prompt", "instructions"}:
                raise _DriverError("invalid_native_request")
            prompt = request.payload.get("prompt")
            instructions = request.payload.get("instructions", "")
            if type(prompt) is not str or not prompt.strip() or type(instructions) is not str:
                raise _DriverError("invalid_native_request")
            fingerprint = hashlib.sha256(request.execution_id.encode()).hexdigest()
            binding = self.bindings.load(request.execution_id)
            if binding is None:
                binding = self.bindings.prepare(request.execution_id, "agent_" + fingerprint[:40],
                                                "agent-" + fingerprint)
            binding = self._checked_binding(binding, request)
            native_ids["session_id"] = binding.session_id
            run_id = binding.run_id
            if run_id is None:
                if binding.admission_started:
                    raise _DriverError("admission_uncertain")
                # Session identity is durable before any side effect. On a
                # documented 409, verify exactly that original session.
                self.client.timeout = min(10, max(0.001, end - self._clock()))
                try:
                    created = self.client.create_session({"id": binding.session_id,
                                                          "title": "Agent service isolated execution",
                                                          "source": "api_server"})
                except Exception as exc:
                    if getattr(exc, "status", None) != 409:
                        raise
                    created = self.client.json("GET", f"/api/sessions/{binding.session_id}")
                if type(created) is not dict or created.get("session", {}).get("id") != binding.session_id:
                    raise _DriverError("native_protocol_error")
                self._check(cancel, end)
                if self.bindings.mark_admission_started(request.execution_id) is not True:
                    raise _DriverError("admission_uncertain")
                self.client.timeout = min(10, max(0.001, end - self._clock()))
                try:
                    admitted = self.client.admit(binding.session_id, prompt, binding.admission_key,
                                                 instructions=instructions)
                    if type(admitted) is not dict:
                        raise ValueError()
                    run_id = _native_id(admitted.get("run_id"))
                except Exception:
                    raise _DriverError("admission_uncertain") from None
                native_ids["run_id"] = run_id
                bound = self._checked_binding(self.bindings.bind_run(request.execution_id, run_id), request)
                if bound.run_id != run_id:
                    raise _DriverError("native_binding_invalid")
            native_ids["run_id"] = run_id
            self._emit(emit, "runtime_started")
            retries = 0
            while True:
                self._check(cancel, end)
                self.client.timeout = min(10, max(0.001, end - self._clock()))
                try:
                    snapshot = self.client.status(run_id)
                except Exception as exc:
                    # Transport retries never start a new paid run.
                    from .hermes_client import HermesError
                    transient = (isinstance(exc, HermesError)
                                 and exc.operation == f"/v1/runs/{run_id}"
                                 and exc.status in (None, 502, 503, 504))
                    if not transient or retries >= self.status_retries:
                        raise _DriverError("native_status_failed") from None
                    retries += 1
                    self._wait(cancel, min(self.poll_seconds, max(0, end - self._clock())))
                    continue
                if type(snapshot) is not dict:
                    raise _DriverError("native_protocol_error")
                state = snapshot.get("status")
                self._check(cancel, end)
                if state in {"completed", "failed", "cancelled", "interrupted"}:
                    if state != "completed":
                        outcome = NativeOutcome("cancelled" if state == "cancelled" else "failed",
                                                usage=_usage(snapshot.get("usage")),
                                                native_ids=native_ids,
                                                error_code="native_" + state)
                        break
                    output = snapshot.get("output")
                    if output is not None and type(output) not in (str, dict):
                        raise _DriverError("native_protocol_error")
                    output = _json_copy(output, MAX_OUTPUT_BYTES)
                    outcome = NativeOutcome("completed", output, _usage(snapshot.get("usage")), native_ids)
                    break
                if state not in {"queued", "starting", "running", "cancelling"}:
                    raise _DriverError("native_protocol_error")
                self._wait(cancel, min(self.poll_seconds, max(0, end - self._clock())))
        except _DriverError as exc:
            status = exc.code if exc.code in {"cancelled", "timed_out"} else "failed"
            outcome = NativeOutcome(status, native_ids=native_ids, error_code=exc.code)
        except Exception:
            outcome = NativeOutcome("failed", native_ids=native_ids, error_code="engine_error")
        finally:
            if run_id is not None and outcome.status != "completed":
                try:
                    self.client.timeout = 2
                    self.client.stop(run_id)
                except Exception:
                    # The mandatory host fence must still terminate all writers.
                    pass
            outcome = self._fence(request, outcome)
        return outcome
