from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class HttpClientError(RuntimeError):
    pass


def _request(url: str, *, method: str = "GET", headers: dict[str, str] | None = None, body: bytes | None = None, timeout: int = 20) -> bytes:
    request = Request(url, method=method, headers=headers or {}, data=body)
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except (HTTPError, URLError, TimeoutError) as exc:
        detail = getattr(exc, "reason", str(exc))
        raise HttpClientError(f"{method} {url} failed: {detail}") from exc


def get_json(url: str, *, headers: dict[str, str] | None = None, timeout: int = 20) -> Any:
    payload = _request(url, headers={"Accept": "application/json", **(headers or {})}, timeout=timeout)
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HttpClientError(f"{url} returned invalid JSON") from exc


def get_text(url: str, *, headers: dict[str, str] | None = None, timeout: int = 20) -> str:
    payload = _request(url, headers=headers, timeout=timeout)
    return payload.decode("utf-8", errors="replace")


def post_json(url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None, timeout: int = 20) -> Any:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    response = _request(
        url,
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json", **(headers or {})},
        body=body,
        timeout=timeout,
    )
    try:
        return json.loads(response.decode("utf-8")) if response else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HttpClientError(f"{url} returned invalid JSON") from exc

