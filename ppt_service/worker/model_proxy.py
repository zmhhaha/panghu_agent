"""Stdlib fixed-model Responses reverse proxy; bind only on the internal network."""
from __future__ import annotations

import hmac
import http.client
import json
import os
import signal
import socket
import ssl
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Mapping
from urllib.parse import urlsplit

from .proxy_usage import UsageLedger
from .model_grant import ModelGrant

MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
UPSTREAM_TIMEOUT = 600
GRANT_CLIENT_IDLE_TIMEOUT = 1.0
GRANT_MAX_CONNECTIONS = 8
CHUNK_BYTES = 64 * 1024
DEFAULT_UPSTREAM = "https://custom-llm-proxy.panghuer.top/v1"
DEFAULT_MODEL = "gpt-6.1-sol"


@dataclass(frozen=True)
class ProxyConfig:
    client_key: str
    upstream_api_key: str
    upstream_base_url: str = DEFAULT_UPSTREAM
    model: str = DEFAULT_MODEL

    def __post_init__(self) -> None:
        for key in (self.client_key, self.upstream_api_key):
            if not key or any(ord(c) < 33 or ord(c) > 126 for c in key):
                raise ValueError("invalid_proxy_configuration")
        if hmac.compare_digest(self.client_key, self.upstream_api_key):
            raise ValueError("invalid_proxy_configuration")
        if not self.model or not isinstance(self.model, str):
            raise ValueError("invalid_proxy_configuration")
        try:
            url = urlsplit(self.upstream_base_url)
            port = url.port
            if (url.scheme != "https" or not url.hostname or url.username is not None
                    or url.password is not None or url.query or url.fragment
                    or "?" in self.upstream_base_url or "#" in self.upstream_base_url
                    or "\\" in self.upstream_base_url
                    or any(ord(c) <= 32 or ord(c) >= 127 for c in self.upstream_base_url)
                    or (port is not None and not 1 <= port <= 65535)):
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("invalid_proxy_configuration") from None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ProxyConfig":
        env = os.environ if env is None else env
        return cls(client_key=env.get("PPT_PROXY_CLIENT_KEY", ""),
                   upstream_api_key=env.get("PPT_UPSTREAM_API_KEY", ""),
                   upstream_base_url=env.get("PPT_UPSTREAM_BASE_URL", DEFAULT_UPSTREAM),
                   model=env.get("PPT_MODEL", DEFAULT_MODEL))


def _strict_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("invalid_json_constant")


class _ShutdownRequested(Exception):
    """Internal serve-loop wakeup; raised only on the serving thread."""


class ModelProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], config: ProxyConfig,
                 connector: Callable | None = None, usage_ledger: UsageLedger | None = None,
                 *, model_grant: ModelGrant | None = None):
        self.config = config
        self.model_grant = model_grant
        self.connector = connector or http.client.HTTPSConnection
        self.usage_ledger = usage_ledger if usage_ledger is not None else UsageLedger.from_env()
        if model_grant is not None:
            if (type(model_grant) is not ModelGrant
                    or not model_grant.authenticates(("Bearer " + config.client_key).encode("ascii"))
                    or self.usage_ledger.job_id != model_grant.identity.job_id
                    or type(self.usage_ledger.attempt) is not int
                    or self.usage_ledger.attempt != model_grant.identity.attempt
                    or self.usage_ledger.phase != model_grant.identity.phase):
                raise ValueError("invalid_proxy_configuration")
        self._connection_slots = threading.BoundedSemaphore(GRANT_MAX_CONNECTIONS)
        self._connection_lock = threading.Lock()
        self._accepted_connections = set()
        self._connections_closing = False
        self.tls_context = ssl.create_default_context()
        self.shutdown_requested = threading.Event()
        super().__init__(address, ModelProxyHandler)

    def process_request(self, request, client_address) -> None:
        if self.model_grant is None:
            super().process_request(request, client_address)
            return
        if not self._connection_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        with self._connection_lock:
            closing = self._connections_closing
            if not closing:
                self._accepted_connections.add(request)
        if closing:
            self._connection_slots.release()
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._retire_connection(request)
            raise

    def _retire_connection(self, request) -> None:
        with self._connection_lock:
            tracked = request in self._accepted_connections
            self._accepted_connections.discard(request)
        if tracked:
            self._connection_slots.release()

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            if self.model_grant is not None:
                self._retire_connection(request)

    def service_actions(self) -> None:
        # shutdown() cannot be called on the serve_forever thread: it deadlocks.
        if self.shutdown_requested.is_set():
            raise _ShutdownRequested

    def server_close(self) -> None:
        # Handlers are daemon threads and may be blocked on a 600s upstream read.
        # Seal and finish their observations before allowing process exit.
        if self.model_grant is not None:
            with self._connection_lock:
                self._connections_closing = True
                connections = tuple(self._accepted_connections)
            self.model_grant.close()
            for connection in connections:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    connection.close()
                except OSError:
                    pass
        self.usage_ledger.shutdown()
        super().server_close()

    def handle_error(self, request, client_address) -> None:
        # No request contents, provider bodies, credentials or tracebacks in logs.
        pass


class ModelProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ModelProxy"
    sys_version = ""

    def setup(self) -> None:
        super().setup()
        timeout = UPSTREAM_TIMEOUT
        if self.server.model_grant is not None:
            # Bound pre-admission reads too: on Windows a buffered partial-body
            # read can remain blocked after another thread shuts down its socket.
            timeout = max(0.001, min(GRANT_CLIENT_IDLE_TIMEOUT, timeout,
                                     self.server.model_grant.snapshot()["remaining_seconds"]))
        self.connection.settimeout(timeout)

    def log_message(self, format, *args) -> None:
        pass

    def _headers(self, status: int, content_type: str, length: int | None = None) -> None:
        self.close_connection = True
        self.send_response_only(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.end_headers()

    def _error(self, status: int, code: str) -> None:
        body = json.dumps({"error": {"code": code}}, separators=(",", ":")).encode()
        self._headers(status, "application/json", len(body))
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_error(self, code, message=None, explain=None) -> None:
        self._error(code, "request_rejected")

    def handle_expect_100(self) -> bool:
        self._error(417, "expectation_not_supported")
        return False

    def _exact_target(self, target: str) -> bool:
        # BaseHTTPRequestHandler collapses leading //; validate the original target.
        parts = self.requestline.split()
        return len(parts) == 3 and parts[1] == target and self.path == target

    def do_GET(self) -> None:
        if not self._exact_target("/healthz"):
            self._error(404, "route_not_found")
            return
        if not self.server.usage_ledger.healthy:
            self._error(503, "usage_ledger_unhealthy")
            return
        body = b'{"status":"ok"}'
        self._headers(200, "application/json", len(body))
        self.wfile.write(body)

    def do_POST(self) -> None:
        auth = self.headers.get_all("Authorization", [])
        supplied = auth[0].encode("utf-8") if len(auth) == 1 else b""
        grant = self.server.model_grant
        if grant is None:
            expected = ("Bearer " + self.server.config.client_key).encode("ascii")
            authenticated = hmac.compare_digest(supplied, expected)
        else:
            authenticated = grant.authenticates(supplied)
        self._model_authorization = supplied
        if not authenticated:
            self._error(401, "unauthorized")
            return
        if not self._exact_target("/v1/responses"):
            self._error(404, "route_not_found")
            return
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get_all("Transfer-Encoding"):
            self._error(400, "transfer_encoding_not_supported")
            return
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
            self._error(411, "content_length_required")
            return
        if len(lengths[0]) > 10 or int(lengths[0]) > MAX_REQUEST_BYTES:
            self._error(413, "request_too_large")
            return
        try:
            length = int(lengths[0])
            raw = self.rfile.read(length)
            if len(raw) != length:
                self._error(400, "incomplete_request")
                return
            payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object,
                                 parse_constant=_invalid_constant)
        except (ValueError, UnicodeError, RecursionError):
            self._error(400, "invalid_json")
            return
        except (TimeoutError, OSError):
            self._error(408, "request_timeout")
            return
        if not isinstance(payload, dict) or payload.get("model") != self.server.config.model:
            self._error(400, "invalid_model")
            return
        self._forward(raw)

    def _forward(self, body: bytes) -> None:
        config = self.server.config
        url = urlsplit(config.upstream_base_url)
        grant = self.server.model_grant
        permit = None
        if grant is not None:
            permit = grant.acquire(getattr(self, "_model_authorization", b""))
            if permit is None:
                self._error(429, "model_grant_unavailable")
                return
        observation = None
        connection = None
        response = None
        started = False
        outcome = "upstream_unavailable"
        transport = "upstream"

        def fail(status: int, code: str) -> None:
            nonlocal observation, outcome
            if permit is not None and not permit.allowed():
                outcome = "proxy_shutdown"
            if observation is not None:
                observation.finish(outcome)
                observation = None
            if permit is None or permit.allowed():
                self._error(status, code)

        try:
            if permit is not None and not permit.attach_socket(self.connection):
                return
            observation = self.server.usage_ledger.begin(config.model)
            if observation is not None and not observation.start_ok:
                fail(503, "usage_ledger_unhealthy")
                return
            timeout = UPSTREAM_TIMEOUT
            if permit is not None:
                timeout = min(UPSTREAM_TIMEOUT, grant.snapshot()["remaining_seconds"])
                if timeout <= 0 or not permit.allowed():
                    return
            connection = self.server.connector(
                url.hostname, port=url.port or 443, timeout=timeout,
                context=self.server.tls_context)
            if permit is not None:
                if not permit.allowed():
                    return
                # DNS/connect can outlive revocation; never let request() perform
                # that setup implicitly and then submit paid HTTP after expiry.
                connection.connect()
                if not permit.attach_socket(connection.sock) or not permit.allowed():
                    return
            connection.request("POST", url.path.rstrip("/") + "/responses", body=body,
                               headers={"Authorization": "Bearer " + config.upstream_api_key,
                                        "Content-Type": "application/json",
                                        "Accept": "application/json, text/event-stream",
                                        "Accept-Encoding": "identity", "Connection": "close"})
            if permit is not None:
                # getresponse() may clear connection.sock for a close-delimited
                # response while HTTPResponse.fp still owns the live transport.
                if not permit.attach_socket(connection.sock) or not permit.allowed():
                    return
            response = connection.getresponse()
            if permit is not None and not permit.allowed():
                return
            if observation is not None:
                observation.response_headers(response.headers, "")
            if not 200 <= response.status < 300:
                outcome = "upstream_error"
                fail(502, "upstream_error")
                return
            types = response.headers.get_all("Content-Type", [])
            media_type = types[0].split(";", 1)[0].strip().lower() if len(types) == 1 else ""
            if media_type not in ("application/json", "text/event-stream"):
                outcome = "invalid_content_type"
                fail(502, "upstream_content_type")
                return
            if observation is not None:
                observation.response_headers(response.headers, media_type)
            lengths = response.headers.get_all("Content-Length", [])
            if lengths and (len(lengths) != 1 or not lengths[0].isascii()
                            or not lengths[0].isdigit() or len(lengths[0]) > 10
                            or int(lengths[0]) > MAX_RESPONSE_BYTES):
                outcome = "response_too_large"
                fail(502, "upstream_response_too_large")
                return
            # Close-delimited HTTP/1.1; never copy provider headers/cookies.
            # read1 emits available SSE bytes without waiting to fill CHUNK_BYTES.
            transport = "client"
            self._headers(response.status, media_type)
            started = True
            total = 0
            while True:
                if permit is not None and not permit.allowed():
                    break
                transport = "upstream"
                chunk = response.read1(min(CHUNK_BYTES, MAX_RESPONSE_BYTES - total + 1))
                if permit is not None and not permit.allowed():
                    break
                if not chunk:
                    outcome = "upstream_disconnected" if response.length else "completed"
                    break
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    outcome = "response_too_large"
                    break  # Headers sent: terminate the stream on overflow.
                if observation is not None:
                    observation.feed(chunk)
                transport = "client"
                if permit is not None and not permit.allowed():
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except TimeoutError:
            outcome = "client_disconnected" if transport == "client" else "upstream_timeout"
            if not started:
                fail(504, "upstream_timeout")
        except (OSError, http.client.HTTPException, ValueError):
            if transport == "client":
                outcome = "client_disconnected"
            elif started:
                outcome = "upstream_disconnected"
            else:
                outcome = "upstream_unavailable"
            if not started:
                fail(502, "upstream_unavailable")
        finally:
            self.close_connection = True
            if permit is not None and not permit.allowed():
                outcome = "proxy_shutdown"
            try:
                if connection is not None:
                    connection.close()
                if permit is not None and response is not None:
                    response.close()
            finally:
                try:
                    if observation is not None:
                        observation.finish(outcome)
                finally:
                    if permit is not None:
                        permit.release()


def main() -> None:
    try:
        config = ProxyConfig.from_env()
        host = os.environ.get("PPT_PROXY_HOST", "0.0.0.0")
        port = int(os.environ.get("PPT_PROXY_PORT", "8080"))
        server = ModelProxyServer((host, port), config)
    except (ValueError, OSError):
        raise SystemExit("invalid_proxy_configuration") from None
    previous_term = signal.signal(signal.SIGTERM, lambda *_: server.shutdown_requested.set())
    try:
        server.serve_forever(poll_interval=0.1)
    except (KeyboardInterrupt, _ShutdownRequested):
        pass
    finally:
        try:
            server.server_close()
        finally:
            signal.signal(signal.SIGTERM, previous_term)


if __name__ == "__main__":
    main()
