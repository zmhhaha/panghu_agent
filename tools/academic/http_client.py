"""Small HTTP client with bounded retries for fixed academic API endpoints."""

from __future__ import annotations

import time
import os
import threading
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse


DEFAULT_TIMEOUT = 20
_TRANSIENT_STATUS = {429, 500, 502, 503, 504}
_RATE_LOCK = threading.Lock()
_LAST_REQUEST_AT: dict[str, float] = {}


def _env_float(name: str, default: float, minimum: float, maximum: float | None = None) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    value = max(value, minimum)
    return min(value, maximum) if maximum is not None else value


def _throttle(url: str) -> None:
    """Keep requests to one academic API host below its public rate limit."""
    interval = _env_float("ACADEMIC_API_REQUEST_INTERVAL_MS", 350, 0, 60_000) / 1000.0
    if interval <= 0:
        return
    host = urlparse(url).netloc.lower()
    with _RATE_LOCK:
        now = time.monotonic()
        wait = _LAST_REQUEST_AT.get(host, 0.0) + interval - now
        # Reserve this host's next slot before sleeping. The lock is never
        # held during the wait, so concurrent requests to other API hosts can
        # continue normally.
        next_request_at = max(now, _LAST_REQUEST_AT.get(host, 0.0)) + interval
        _LAST_REQUEST_AT[host] = next_request_at
    if wait > 0:
        time.sleep(wait)


def _retry_delay(response: Any, attempt: int) -> float:
    value = str(response.headers.get("Retry-After", "") if response is not None else "")
    try:
        delay = max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value).timestamp()
            delay = max(0.0, retry_at - time.time())
        except (TypeError, ValueError, OverflowError):
            base = _env_float("ACADEMIC_API_RETRY_BACKOFF_SECONDS", 2.0, 0.1, 60.0)
            delay = base * (2**attempt)
    maximum = _env_float("ACADEMIC_API_RATE_LIMIT_MAX_WAIT_SECONDS", 60.0, 0.0, 900.0)
    return min(maximum, delay)


def _get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int | float = DEFAULT_TIMEOUT,
    attempts: int = 4,
):
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("Academic search requires the requests package") from exc

    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        response = None
        try:
            _throttle(url)
            response = requests.get(
                url,
                params=params,
                headers={"User-Agent": "PanghuAcademic/1.0", **(headers or {})},
                timeout=timeout,
            )
            if response.status_code in _TRANSIENT_STATUS and attempt < attempts - 1:
                time.sleep(_retry_delay(response, attempt))
                continue
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            status = getattr(response, "status_code", None)
            if attempt >= attempts - 1 or (status and status not in _TRANSIENT_STATUS):
                raise
            time.sleep(_retry_delay(response, attempt))
    raise last_error or RuntimeError("HTTP request failed")


def request_json(url: str, **kwargs: Any) -> dict[str, Any]:
    response = _get(url, **kwargs)
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object from {url}")
    return data


def request_text(url: str, **kwargs: Any) -> str:
    response = _get(url, **kwargs)
    if not response.encoding:
        response.encoding = "utf-8"
    return response.text
