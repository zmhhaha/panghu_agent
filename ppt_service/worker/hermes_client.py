"""Small, bounded Hermes API client; never follows redirects with credentials.

Admission is idempotent but execution completion is not delivery acceptance.
Only explicit loopback endpoints are supported by the dedicated worker.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable, Iterator


class HermesError(RuntimeError):
    def __init__(self, operation: str, status: int | None = None):
        self.operation, self.status = operation, status
        super().__init__(f"Hermes {operation} failed" + (f" (HTTP {status})" if status else ""))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class ServerEvent:
    name: str
    data: dict[str, Any]
    event_id: str | None = None


def decode_sse(lines: Iterable[bytes], *, max_event_bytes: int = 262_144) -> Iterator[ServerEvent]:
    """Decode SSE including multiline data, comments and a final unterminated event."""
    event_name, event_id, pieces, size = "message", None, [], 0
    for raw in lines:
        if len(raw) > max_event_bytes:
            raise HermesError("event_too_large")
        line = raw.decode("utf-8", errors="strict").rstrip("\r\n")
        if not line:
            if pieces:
                payload = json.loads("\n".join(pieces))
                if not isinstance(payload, dict):
                    raise HermesError("invalid_event")
                yield ServerEvent(event_name, payload, event_id)
            event_name, event_id, pieces, size = "message", None, [], 0
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_name = value
        elif field == "id" and "\x00" not in value:
            event_id = value
        elif field == "data":
            size += len(raw)
            if size > max_event_bytes:
                raise HermesError("event_too_large")
            pieces.append(value)
    if pieces:
        payload = json.loads("\n".join(pieces))
        if not isinstance(payload, dict):
            raise HermesError("invalid_event")
        yield ServerEvent(event_name, payload, event_id)


def safe_id(value: str) -> str:
    if not value or len(value) > 256 or not all(c.isascii() and (c.isalnum() or c in "_-.") for c in value):
        raise ValueError("Invalid internal Hermes identifier")
    if value in (".", ".."):
        raise ValueError("Invalid internal Hermes identifier")
    return urllib.parse.quote(value, safe="")


class HermesClient:
    def __init__(self, endpoint: str, api_key: str, *, timeout: float = 30, max_json_bytes: int = 2_097_152):
        parsed = urllib.parse.urlsplit(endpoint)
        try:
            loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
        except ValueError:
            loopback = False
        if parsed.scheme != "http" or not loopback or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
            raise ValueError("Worker Hermes endpoint must be an explicit loopback HTTP origin")
        if not api_key or "\r" in api_key or "\n" in api_key:
            raise ValueError("A non-empty internal API key is required")
        self.endpoint, self._key = endpoint.rstrip("/"), api_key
        self.timeout, self.max_json_bytes = timeout, max_json_bytes
        # Explicitly ignore inherited proxy environment variables for loopback traffic.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def _open(self, method: str, path: str, body: dict | None = None, *, headers: dict | None = None):
        request_headers = {"Authorization": f"Bearer {self._key}", "Accept": "application/json"}
        if headers:
            request_headers.update(headers)
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        if data is not None:
            request_headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.endpoint + path, data=data, headers=request_headers, method=method)
        try:
            return self._opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            exc.close()
            # Response bodies can contain prompts, upstream credentials or local paths.
            raise HermesError(path, exc.code) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
            raise HermesError(path) from None

    def json(self, method: str, path: str, body: dict | None = None, *, headers: dict | None = None) -> dict:
        with self._open(method, path, body, headers=headers) as response:
            raw = response.read(self.max_json_bytes + 1)
        if len(raw) > self.max_json_bytes:
            raise HermesError("response_too_large")
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise HermesError("invalid_json") from None
        if not isinstance(result, dict):
            raise HermesError("invalid_json")
        return result

    def capabilities(self) -> dict:
        return self.json("GET", "/v1/capabilities")

    def create_session(self, body: dict) -> dict:
        return self.json("POST", "/api/sessions", body)

    def admit(self, session_id: str, prompt: str, admission_key: str, *, instructions: str = "") -> dict:
        safe_id(session_id)
        if not admission_key or len(admission_key) > 255 or not all(33 <= ord(c) <= 126 for c in admission_key):
            raise ValueError("Admission key must be visible ASCII, 1–255 characters")
        body = {"session_id": session_id, "input": prompt}
        if instructions:
            body["instructions"] = instructions
        return self.json("POST", "/v1/runs", body, headers={"Idempotency-Key": admission_key})

    def status(self, run_id: str) -> dict:
        return self.json("GET", f"/v1/runs/{safe_id(run_id)}")

    def stop(self, run_id: str) -> dict:
        return self.json("POST", f"/v1/runs/{safe_id(run_id)}/stop", {})

    def events(self, run_id: str) -> Iterator[ServerEvent]:
        with self._open("GET", f"/v1/runs/{safe_id(run_id)}/events", headers={"Accept": "text/event-stream"}) as response:
            # readline(size) bounds allocation before the SSE parser checks the line.
            yield from decode_sse(iter(lambda: response.readline(262_145), b""))
