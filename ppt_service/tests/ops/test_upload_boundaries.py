"""Synthetic upload transport/file limits; no parser, model, or business DB."""
import asyncio
import hashlib
import json

from fastapi import HTTPException
import pytest
from starlette.exceptions import HTTPException as TransportHTTPException
from starlette.responses import JSONResponse

from ppt_service.server.middleware import BodyLimitMiddleware
from ppt_service.server.storage import MAX_FILE, Storage


class Stream:
    def __init__(self, size, *, filename="material.md", suffix=b"", fail_after=None, failure=OSError):
        self.filename = filename
        self.remaining = size
        self.suffix = suffix
        self.reads = 0
        self.fail_after = fail_after
        self.failure = failure

    async def read(self, amount):
        self.reads += 1
        if self.fail_after is not None and self.reads > self.fail_after:
            raise self.failure("synthetic interrupted stream")
        if self.remaining:
            count = min(self.remaining, amount)
            self.remaining -= count
            return b"a" * count
        value, self.suffix = self.suffix, b""
        return value


def originals(root):
    return list(root.rglob("original"))


def test_exact_25_mib_file_is_received_and_hashed_without_parser(tmp_path):
    storage = Storage(tmp_path)
    _, key, size, digest, media_type = asyncio.run(storage.upload("synthetic-project", Stream(MAX_FILE)))
    assert size == MAX_FILE
    assert digest == hashlib.sha256(b"a" * MAX_FILE).hexdigest()
    assert media_type == "text/markdown"
    with storage.open(key, MAX_FILE) as stream:
        assert len(stream.read()) == MAX_FILE


@pytest.mark.parametrize("stream,status,code", [
    (lambda: Stream(MAX_FILE + 1), 413, "file_too_large"),
    (lambda: Stream(0), 415, "invalid_blob_signature"),
    (lambda: Stream(1, filename="unexpected.exe"), 415, "unsupported_media_type"),
    (lambda: Stream(5, filename="invalid.pdf"), 415, "invalid_blob_signature"),
    (lambda: Stream(65536, suffix=b"\x00"), 415, "invalid_text_encoding"),
    (lambda: Stream(65536, suffix=b"\xe4"), 415, "invalid_blob_signature"),
])
def test_rejected_file_never_retains_partial_original(tmp_path, stream, status, code):
    with pytest.raises(HTTPException) as error:
        asyncio.run(Storage(tmp_path).upload("synthetic-project", stream()))
    assert error.value.status_code == status
    assert error.value.detail["code"] == code
    assert not originals(tmp_path)


@pytest.mark.parametrize("failure", [OSError, asyncio.CancelledError])
def test_interrupted_stream_removes_partial_original_without_success(tmp_path, failure):
    with pytest.raises(failure):
        asyncio.run(Storage(tmp_path).upload("synthetic-project", Stream(1024**2, fail_after=2, failure=failure)))
    assert not originals(tmp_path)
    assets = tmp_path / "projects" / "synthetic-project" / "assets"
    assert not assets.exists() or not list(assets.iterdir())


async def transport(*, declared=None, total=0, path="/api/v1/projects/synthetic/assets"):
    """Exercise actual ASGI limiter with multiple body chunks, before spooling.

    The inner app only consumes bytes. Its exception adapter mirrors the router
    response and is deliberately not multipart, reverse-proxy, or SSO evidence.
    """
    called = False
    sent = []
    remaining = total

    async def inner(scope, receive, send):
        nonlocal called
        called = True
        try:
            while (await receive()).get("more_body", False):
                pass
            response = JSONResponse({"received": True}, 200)
        except TransportHTTPException as error:
            response = JSONResponse({"detail": error.detail}, error.status_code)
        await response(scope, receive, send)

    async def receive():
        nonlocal remaining
        count = min(65536, remaining)
        remaining -= count
        return {"type": "http.request", "body": b"x" * count, "more_body": remaining > 0}

    async def send(message):
        sent.append(message)

    headers = [] if declared is None else [(b"content-length", declared)]
    await BodyLimitMiddleware(inner)({"type": "http", "method": "POST", "path": path, "headers": headers}, receive, send)
    status = next(message["status"] for message in sent if message["type"] == "http.response.start")
    body = json.loads(b"".join(message.get("body", b"") for message in sent if message["type"] == "http.response.body"))
    return status, body, called


@pytest.mark.parametrize("declared,status", [(b"not-a-number", 400), (b"-1", 413), (str(27 * 1024**2).encode(), 413)])
def test_bad_declared_lengths_reject_before_downstream_spooling(declared, status):
    actual, _, called = asyncio.run(transport(declared=declared, total=0))
    assert actual == status
    assert called is False


@pytest.mark.parametrize("declared", [None, b"1"])
def test_chunked_or_underdeclared_oversize_body_is_counted(declared):
    status, body, called = asyncio.run(transport(declared=declared, total=26 * 1024**2 + 1))
    assert called is True
    assert status == 413
    assert body == {"detail": {"code": "request_too_large"}}


def test_exact_transport_envelope_and_non_upload_json_limit():
    assert asyncio.run(transport(total=26 * 1024**2))[0] == 200
    assert asyncio.run(transport(total=128 * 1024 + 1, path="/api/v1/projects"))[0] == 413
