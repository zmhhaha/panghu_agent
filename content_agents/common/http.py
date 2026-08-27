from __future__ import annotations

import json
import logging
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


class HttpClientError(RuntimeError):
    """An HTTP client failure with enough context for retry/fallback logs."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_detail: str = "",
        attempts: int = 1,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_detail = response_detail
        self.attempts = attempts


def _request(url: str, *, method: str = "GET", headers: dict[str, str] | None = None, body: bytes | None = None, timeout: int = 20) -> bytes:
    request = Request(url, method=method, headers=headers or {}, data=body)
    # A 502 from the shared LLM gateway is commonly transient. Give each
    # request one retry, then let the bot's existing fallback take over.
    for attempt in range(1, 3):
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except HTTPError as exc:
            raw_detail = exc.read().decode("utf-8", errors="replace").strip()
            detail = _response_detail(raw_detail) or str(getattr(exc, "reason", "HTTP error"))
            if exc.code == 502 and attempt == 1:
                logger.warning(
                    "%s %s returned HTTP 502 (%s); retrying once",
                    method,
                    url,
                    _log_detail(detail),
                )
                continue
            logger.error(
                "%s %s failed after %d attempt(s): HTTP %s (%s)",
                method,
                url,
                attempt,
                exc.code,
                _log_detail(detail),
            )
            raise HttpClientError(
                f"{method} {url} failed: HTTP {exc.code}: {_log_detail(detail)}",
                status_code=exc.code,
                response_detail=detail,
                attempts=attempt,
            ) from exc
        except (URLError, TimeoutError) as exc:
            detail = str(getattr(exc, "reason", exc))
            logger.error(
                "%s %s failed after %d attempt(s): %s",
                method,
                url,
                attempt,
                _log_detail(detail),
            )
            raise HttpClientError(
                f"{method} {url} failed: {detail}",
                response_detail=detail,
                attempts=attempt,
            ) from exc

    raise AssertionError("unreachable")


def _response_detail(raw_detail: str) -> str:
    if not raw_detail:
        return ""
    try:
        payload = json.loads(raw_detail)
    except json.JSONDecodeError:
        return raw_detail
    if isinstance(payload, dict) and payload.get("detail"):
        return str(payload["detail"])
    return raw_detail


def _log_detail(detail: str, limit: int = 500) -> str:
    compact = " ".join(detail.split())
    return compact if len(compact) <= limit else compact[:limit] + "..."


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
