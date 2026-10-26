"""Bounded, proxy-side usage observation and an append-only JSONL ledger.

Only verified provider responses are inputs to the observer. The ledger path
and attribution come from operator configuration, never from worker headers or
payloads. Keep the ledger outside every worker-writable directory or mount.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import threading
import uuid
from datetime import datetime, timezone
from typing import Mapping

MAX_OBSERVATION_BYTES = 1024 * 1024
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9_.:/-]{1,256}\Z")
_TERMINAL = {"completed", "failed", "incomplete", "cancelled", "canceled"}
_FIELDS = (
    "event", "proxy_request_id", "job_id", "attempt", "phase", "started_at", "ended_at",
    "outcome", "model", "provider_response_id", "provider_request_id", "input_tokens",
    "output_tokens", "cached_tokens", "reasoning_tokens", "cost_usd", "usage_status",
)
_OUTCOMES = frozenset({
    "completed", "provider_failed", "provider_incomplete", "provider_cancelled",
    "upstream_error", "upstream_timeout", "upstream_unavailable",
    "upstream_disconnected", "client_disconnected", "response_too_large",
    "invalid_content_type", "proxy_shutdown",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _identifier(value: object) -> str | None:
    return value if isinstance(value, str) and _SAFE_IDENTIFIER.fullmatch(value) else None


def _tokens(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def _object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("invalid_usage_json")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError("invalid_usage_json")


def _json(data: bytes | bytearray) -> object:
    return json.loads(data.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant)


class UsageLedger:
    """One process serializes writes and exposes a sticky durability failure."""

    def __init__(self, path: str | None = None, *, job_id: str | None = None,
                 attempt: int | None = None, phase: str | None = None):
        self.path = path or None
        self.job_id = job_id
        self.attempt = attempt
        self.phase = phase
        self._lock = threading.Lock()
        self._failed = threading.Event()
        self._active_lock = threading.Lock()
        self._closing = False
        self._active: set[UsageObservation] = set()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "UsageLedger":
        env = os.environ if env is None else env
        path = env.get("PPT_PROXY_USAGE_PATH")
        if not path:
            return cls()
        job_id = env.get("PPT_USAGE_JOB_ID") or None
        phase = env.get("PPT_USAGE_PHASE") or None
        raw_attempt = env.get("PPT_USAGE_ATTEMPT")
        if ((job_id is not None and _identifier(job_id) is None)
                or (phase is not None and _identifier(phase) is None)
                or (raw_attempt and (not raw_attempt.isascii() or not raw_attempt.isdigit()
                                     or len(raw_attempt) > 9))):
            raise ValueError("invalid_usage_configuration")
        attempt = int(raw_attempt) if raw_attempt else None
        return cls(path, job_id=job_id, attempt=attempt, phase=phase)

    @property
    def enabled(self) -> bool:
        return self.path is not None

    @property
    def healthy(self) -> bool:
        return not self._failed.is_set()

    def begin(self, model: str) -> "UsageObservation | None":
        if not self.enabled:
            return None
        observation = UsageObservation(self, model)
        with self._active_lock:
            if not self._closing:
                observation.start_ok = self.healthy and self.append(observation.record)
                if observation.start_ok:
                    self._active.add(observation)
        return observation

    def shutdown(self) -> None:
        """Seal admission and durably mark outstanding requests as interrupted.

        Never wait for upstream I/O: Docker's stop grace also bounds teardown.
        Observation locks serialize this with the request thread's normal finish.
        """
        with self._active_lock:
            self._closing = True
            active = tuple(self._active)
        for observation in active:
            observation.finish("proxy_shutdown")

    def _retire(self, observation: "UsageObservation") -> None:
        with self._active_lock:
            self._active.discard(observation)

    def append(self, record: dict) -> bool:
        """Record is built solely by UsageObservation from the fixed schema."""
        if not self.enabled:
            return True
        with self._lock:
            if self._failed.is_set():
                return False
            try:
                safe_record = {name: record.get(name) for name in _FIELDS}
                data = (json.dumps(safe_record, separators=(",", ":"), allow_nan=False) + "\n").encode()
                flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(self.path, flags, 0o600)
                try:
                    offset = 0
                    while offset < len(data):
                        written = os.write(descriptor, data[offset:])
                        if written <= 0:
                            raise OSError("usage_write_failed")
                        offset += written
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                return True
            except (OSError, ValueError, TypeError):
                if not self._failed.is_set():
                    self._failed.set()
                    # Fixed notice only: never include paths or exception text.
                    print("proxy_usage_write_failed", file=sys.stderr, flush=True)
                return False


class UsageObservation:
    def __init__(self, ledger: UsageLedger, model: str):
        self.ledger = ledger
        self.record = {
            "event": "request_started", "proxy_request_id": str(uuid.uuid4()),
            "job_id": ledger.job_id, "attempt": ledger.attempt, "phase": ledger.phase,
            "started_at": _now(), "ended_at": None, "outcome": "in_progress",
            "model": _identifier(model), "provider_response_id": None,
            "provider_request_id": None, "input_tokens": None, "output_tokens": None,
            "cached_tokens": None, "reasoning_tokens": None, "cost_usd": None,
            "usage_status": "unknown",
        }
        self.start_ok = False
        self.media_type = ""
        self.problem = False
        self.terminal_status = None
        self.valid_usage = False
        self._finished = False
        self._lock = threading.Lock()
        self._json_body = bytearray()
        self._line = bytearray()
        self._data = bytearray()
        self._event_name = b""
        self._event_size = 0
        self._event_dropped = False
        self._line_has_bytes = False
        self._skip_lf = False

    def response_headers(self, headers, media_type: str) -> None:
        with self._lock:
            if not self._finished:
                self.media_type = media_type
                ids = headers.get_all("x-request-id", [])
                self.record["provider_request_id"] = _identifier(ids[0]) if len(ids) == 1 else None

    def feed(self, chunk: bytes) -> None:
        with self._lock:
            if not self._finished:
                self._feed(chunk)

    def _feed(self, chunk: bytes) -> None:
        if self.media_type == "application/json":
            if not self.problem:
                if len(self._json_body) + len(chunk) > MAX_OBSERVATION_BYTES:
                    self.problem = True
                    self._json_body.clear()
                else:
                    self._json_body.extend(chunk)
        elif self.media_type == "text/event-stream":
            self._feed_sse(chunk)

    def _feed_sse(self, chunk: bytes) -> None:
        position = 0
        if self._skip_lf and chunk:
            position = int(chunk[0] == 10)
            self._skip_lf = False
        while position < len(chunk):
            cr = chunk.find(b"\r", position)
            lf = chunk.find(b"\n", position)
            boundary = min(cr, lf) if cr >= 0 and lf >= 0 else max(cr, lf)
            end = boundary if boundary >= 0 else len(chunk)
            part = chunk[position:end]
            if part:
                self._line_has_bytes = True
            self._event_size += len(part) + int(boundary >= 0)
            if self._event_size > MAX_OBSERVATION_BYTES:
                self.problem = True
                self._event_dropped = True
                self._line.clear()
                self._data.clear()
                self._event_name = b""
            if not self._event_dropped:
                self._line.extend(part)
            if boundary < 0:
                break
            self._end_line()
            position = boundary + 1
            if chunk[boundary] == 13:
                if position < len(chunk) and chunk[position] == 10:
                    position += 1
                elif position == len(chunk):
                    self._skip_lf = True

    def _end_line(self) -> None:
        if not self._line_has_bytes:
            if not self._event_dropped and self._data:
                self._event(bytes(self._data), self._event_name)
            self._data.clear()
            self._event_name = b""
            self._event_size = 0
            self._event_dropped = False
        elif not self._event_dropped:
            field, separator, value = self._line.partition(b":")
            if separator and value.startswith(b" "):
                value = value[1:]
            if field == b"data":
                if self._data:
                    self._data.extend(b"\n")
                self._data.extend(value)
            elif field == b"event":
                self._event_name = bytes(value) if len(value) <= 100 else b""
        self._line.clear()
        self._line_has_bytes = False

    def _event(self, data: bytes, event_name: bytes) -> None:
        if data.strip() == b"[DONE]":
            return
        try:
            value = _json(data)
            if not isinstance(value, dict):
                self.problem = True
                return
            kind = value.get("type")
            if not isinstance(kind, str):
                kind = event_name.decode("ascii", errors="replace")
            if kind in {"response.created", "response.in_progress", "response.completed",
                        "response.failed", "response.incomplete", "response.cancelled",
                        "response.canceled"}:
                self._response(value.get("response"), kind.removeprefix("response."))
        except (ValueError, UnicodeError, RecursionError):
            self.problem = True

    def _response(self, value: object, status_hint: str | None = None) -> None:
        if not isinstance(value, dict) or value.get("object", "response") != "response":
            self.problem = True
            return
        response_id = _identifier(value.get("id"))
        previous_id = self.record["provider_response_id"]
        if response_id:
            if previous_id is not None and previous_id != response_id:
                self.problem = True
            self.record["provider_response_id"] = response_id
        model = _identifier(value.get("model"))
        if model:
            self.record["model"] = model
        status = value.get("status", status_hint)
        if isinstance(status, str) and status in _TERMINAL:
            self.terminal_status = status
        usage = value.get("usage")
        if not isinstance(usage, dict):
            return
        input_tokens = _tokens(usage.get("input_tokens"))
        output_tokens = _tokens(usage.get("output_tokens"))
        self.valid_usage = input_tokens is not None and output_tokens is not None
        for name, count in (("input_tokens", input_tokens), ("output_tokens", output_tokens)):
            if count is not None:
                self.record[name] = count
        for detail, field in (("input_tokens_details", "cached_tokens"),
                              ("output_tokens_details", "reasoning_tokens")):
            nested = usage.get(detail)
            if isinstance(nested, dict):
                count = _tokens(nested.get(field))
                if count is not None:
                    self.record[field] = count
        # These counts are subsets of input/output, never added to either total.
        for subset, total in (("cached_tokens", "input_tokens"),
                              ("reasoning_tokens", "output_tokens")):
            if (self.record[subset] is not None and self.record[total] is not None
                    and self.record[subset] > self.record[total]):
                self.problem = True
                self.record[subset] = None
        # Only an explicit provider-reported price is accepted; no model pricing.
        cost = usage.get("cost_usd")
        if type(cost) in (int, float) and 0 <= cost <= 10**15 and math.isfinite(cost):
            self.record["cost_usd"] = cost

    def finish(self, outcome: str) -> bool:
        with self._lock:
            try:
                return self._finish(outcome)
            finally:
                self.ledger._retire(self)

    def _finish(self, outcome: str) -> bool:
        if self._finished or not self.start_ok:
            return self.ledger.healthy and self.start_ok
        self._finished = True
        self.record["event"] = "request_finished"
        if self.media_type == "application/json" and not self.problem:
            try:
                self._response(_json(self._json_body))
            except (ValueError, UnicodeError, RecursionError):
                self.problem = True
        elif self.media_type == "text/event-stream" and (self._line_has_bytes or self._data):
            self.problem = True  # An unterminated event is not authoritative.
        self._json_body.clear()
        self._line.clear()
        self._data.clear()
        if outcome == "completed":
            if self.terminal_status in {"failed", "incomplete", "cancelled", "canceled"}:
                suffix = "cancelled" if self.terminal_status == "canceled" else self.terminal_status
                outcome = "provider_" + suffix
            elif self.terminal_status != "completed":
                self.problem = True
        self.record["ended_at"] = _now()
        self.record["outcome"] = outcome if outcome in _OUTCOMES else "upstream_unavailable"
        self.record["usage_status"] = (
            "known" if outcome == "completed" and self.valid_usage and not self.problem else "unknown")
        return self.ledger.append(self.record)
