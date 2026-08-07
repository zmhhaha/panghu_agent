"""Small HTTP client with bounded retries for fixed academic API endpoints."""

from __future__ import annotations

import time
from typing import Any


DEFAULT_TIMEOUT = 20
_TRANSIENT_STATUS = {429, 500, 502, 503, 504}


def _retry_delay(response: Any, attempt: int) -> float:
    value = str(response.headers.get("Retry-After", "") if response is not None else "")
    try:
        return min(8.0, max(0.0, float(value)))
    except ValueError:
        return min(8.0, 0.75 * (2**attempt))


def _get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int | float = DEFAULT_TIMEOUT,
    attempts: int = 3,
):
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("Academic search requires the requests package") from exc

    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        response = None
        try:
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
