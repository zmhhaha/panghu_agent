"""Standard-library CLI for the authenticated animation method laboratory.

Run with ``python -m video_workbench.animation_lab_client --help`` or download
this file and run it directly. The operator supplies a Netscape cookie file;
this client never reads browser profiles, exports cookies, impersonates a
forwarded user, or accepts/publishes a result. Construction performs no I/O
except loading the explicitly supplied file. POST requests are never retried.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import http.client
from http.cookiejar import LoadError, MozillaCookieJar
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import time
import warnings
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import (
    HTTPCookieProcessor, HTTPRedirectHandler, ProxyHandler, Request, build_opener,
)
from uuid import UUID


DEFAULT_BASE_URL = "https://video-service.panghuer.top/dev/animations/"
MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_MEDIA_BYTES = 64 * 1024 * 1024
MAX_COOKIE_BYTES = 1024 * 1024
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
ACTIVE_STATES = frozenset({"queued", "running", "cancelling"})
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9_-]{16,256}\Z")


class AnimationLabClientError(Exception):
    """An intentionally small, safe error: no response body or URL is retained."""

    def __init__(self, code, *, request_id=None):
        self.code = code
        self.request_id = request_id
        super().__init__(code)

    def as_dict(self):
        result = {"error": self.code}
        if self.code == "submission_outcome_unknown":
            if self.request_id is not None:
                result["request_id"] = self.request_id
            result["guidance"] = (
                "Check catalog/status before acting. For a request with request_id, "
                "replay the identical JSON file with the same request_id only after "
                "checking; never generate a replacement request_id for an uncertain submission."
            )
        return result


def _positive_number(value, code):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise AnimationLabClientError(code)
    return float(value)


def _json_object(raw, *, limit, code):
    if not isinstance(raw, bytes) or len(raw) > limit:
        raise AnimationLabClientError(code)

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    def finite_float(text):
        result = float(text)
        if not math.isfinite(result):
            raise ValueError()
        return result

    def reject_constant(_):
        raise ValueError()

    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                            parse_float=finite_float, parse_constant=reject_constant)
        if type(result) is not dict:
            raise ValueError()
        # Reject escaped lone surrogates too, before stdout or HTTP can see them.
        json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise AnimationLabClientError(code) from None


def _request_document(path):
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(MAX_REQUEST_BYTES + 1)
    except (OSError, ValueError, TypeError):
        raise AnimationLabClientError("request_file_unreadable") from None
    if len(raw) > MAX_REQUEST_BYTES:
        raise AnimationLabClientError("request_too_large")
    document = _json_object(raw, limit=MAX_REQUEST_BYTES, code="invalid_request_json")
    request_id = document.get("request_id")
    try:
        if type(request_id) is not str or str(UUID(request_id)) != request_id.lower():
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise AnimationLabClientError("explicit_uuid_request_id_required") from None
    return raw, document


def _base_url(value):
    try:
        if (type(value) is not str or not value.isascii()
                or any(ord(char) <= 32 or ord(char) == 127 for char in value)
                or "\\" in value or "%" in value):
            raise ValueError()
        parts = urlsplit(value)
        if (parts.scheme not in {"https", "http"} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment):
            raise ValueError()
        host, port = parts.hostname, parts.port
        if parts.scheme == "http":
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                loopback = host == "localhost"
            if not loopback:
                raise ValueError()
        # Restrict the base to an unambiguous directory; never resolve input URLs.
        path = parts.path or "/"
        if not path.startswith("/") or "//" in path:
            raise ValueError()
        for segment in path.strip("/").split("/"):
            if segment and (segment in {".", ".."}
                            or re.fullmatch(r"[A-Za-z0-9._~-]+", segment) is None):
                raise ValueError()
        if not path.endswith("/"):
            path += "/"
        if not path.endswith("/api/method-lab/"):
            path += "api/method-lab/"
        netloc = "[" + host + "]" if ":" in host else host
        if port is not None and port != (443 if parts.scheme == "https" else 80):
            netloc += ":" + str(port)
        origin = urlunsplit((parts.scheme, netloc, "", "", ""))
        return origin, urlunsplit((parts.scheme, netloc, path, "", ""))
    except (ValueError, TypeError, UnicodeError):
        raise AnimationLabClientError("invalid_base_url") from None


def _identifier(value):
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise AnimationLabClientError("invalid_commission_id")
    return quote(value, safe="")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AnimationLabClient:
    """One origin, one API directory, explicit cookie file, no automatic retries."""

    def __init__(self, base_url=DEFAULT_BASE_URL, *, cookie_file, request_timeout=30):
        self.origin, self.api_url = _base_url(base_url)
        self.request_timeout = _positive_number(request_timeout, "invalid_request_timeout")
        try:
            target = Path(cookie_file)
            if not target.is_file() or target.stat().st_size > MAX_COOKIE_BYTES:
                raise ValueError()
            cookies = MozillaCookieJar(str(target))
            # Session cookies from an operator-created file are usable. Expired
            # cookies and Secure cookies over HTTP retain CookieJar protection.
            # CookieJar otherwise emits a warning containing the malformed
            # cookie line before raising LoadError. Only the safe code may escape.
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message=r"http\.cookiejar bug!")
                cookies.load(ignore_discard=True, ignore_expires=False)
        except (OSError, LoadError, ValueError, TypeError):
            raise AnimationLabClientError("cookie_file_unreadable") from None
        # No ambient proxy or redirect can receive this session or the CSRF token.
        self._opener = build_opener(ProxyHandler({}), _NoRedirect(), HTTPCookieProcessor(cookies))

    @contextmanager
    def _response(self, suffix, *, raw=None, token=None, request_id=None, timeout=None,
                  mime="application/json", limit=MAX_RESPONSE_BYTES):
        # Every caller uses fixed routes and validated path components. Do not
        # accept server-supplied URLs, redirects, authorization or identity headers.
        headers = {"Accept": "application/json", "Accept-Encoding": "identity",
                   "User-Agent": "animation-lab-stdlib-client/1"}
        if raw is not None:
            headers.update({"Content-Type": "application/json", "Origin": self.origin,
                            "X-Animation-Lab-Token": token})
        request = Request(self.api_url + suffix, data=raw, headers=headers,
                          method="POST" if raw is not None else "GET")
        effective_timeout = self.request_timeout if timeout is None else min(self.request_timeout, timeout)
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message=r"http\.cookiejar bug!")
                opened = self._opener.open(request, timeout=effective_timeout)
            with opened as response:
                if 300 <= response.status < 400:
                    raise AnimationLabClientError("redirect_rejected")
                if response.status < 200 or response.status >= 300:
                    raise AnimationLabClientError("http_" + str(response.status))
                actual_mime = response.headers.get_content_type()
                if actual_mime == "text/html":
                    raise AnimationLabClientError("authentication_required")
                if actual_mime != mime:
                    raise AnimationLabClientError("invalid_response_type")
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise AnimationLabClientError("unsupported_response_encoding")
                size = response.headers.get("Content-Length")
                if size is not None:
                    if not size.isascii() or not size.isdigit() or len(size) > 10:
                        raise AnimationLabClientError("invalid_response_size")
                    if int(size) > limit:
                        raise AnimationLabClientError("response_too_large")
                yield response
        except HTTPError as error:
            status = error.code
            error.close()
            raise AnimationLabClientError(
                "redirect_rejected" if 300 <= status < 400 else "http_" + str(status)
            ) from None
        except (URLError, OSError, socket.timeout, http.client.HTTPException, ValueError):
            if raw is not None:
                raise AnimationLabClientError("submission_outcome_unknown", request_id=request_id) from None
            raise AnimationLabClientError("request_failed") from None

    def _request(self, suffix, *, raw=None, token=None, request_id=None, timeout=None):
        with self._response(suffix, raw=raw, token=token, request_id=request_id,
                            timeout=timeout, limit=MAX_RESPONSE_BYTES) as response:
            result = response.read(MAX_RESPONSE_BYTES + 1)
            if len(result) > MAX_RESPONSE_BYTES:
                raise AnimationLabClientError("response_too_large")
            size = response.headers.get("Content-Length")
            if size is not None and len(result) != int(size):
                raise AnimationLabClientError("incomplete_response")
            return _json_object(result, limit=MAX_RESPONSE_BYTES, code="invalid_response_json")

    def _catalog(self):
        result = self._request("catalog")
        token = result.pop("csrf_token", None)
        if type(token) is not str or _TOKEN.fullmatch(token) is None:
            raise AnimationLabClientError("invalid_catalog_token")
        return result, token

    def catalog(self):
        return self._catalog()[0]

    def submit(self, request_file):
        raw, document = _request_document(request_file)
        _, token = self._catalog()
        return self._request("commissions", raw=raw, token=token,
                             request_id=document["request_id"])

    def status(self, commission_id, *, timeout=None):
        return self._request("commissions/" + _identifier(commission_id), timeout=timeout)

    def wait(self, commission_id, *, timeout, interval):
        timeout = _positive_number(timeout, "invalid_wait_timeout")
        interval = _positive_number(interval, "invalid_wait_interval")
        _identifier(commission_id)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AnimationLabClientError("wait_timeout")
            result = self.status(commission_id, timeout=remaining)
            state = result.get("state")
            if type(state) is not str or state not in TERMINAL_STATES | ACTIVE_STATES:
                raise AnimationLabClientError("invalid_commission_state")
            if state in TERMINAL_STATES:
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AnimationLabClientError("wait_timeout")
            time.sleep(min(interval, remaining))

    def feedback(self, commission_id, request_file):
        suffix = "commissions/" + _identifier(commission_id) + "/feedback"
        raw, document = _request_document(request_file)
        _, token = self._catalog()
        return self._request(suffix, raw=raw, token=token, request_id=document["request_id"])

    def cancel(self, commission_id):
        suffix = "commissions/" + _identifier(commission_id) + "/cancel"
        _, token = self._catalog()
        return self._request(suffix, raw=b"{}", token=token)

    def download(self, commission_id, *, output, candidate_id=None, kind="media"):
        commission_path = _identifier(commission_id)
        if kind not in {"media", "source"}:
            raise AnimationLabClientError("invalid_artifact_kind")
        if candidate_id is not None:
            _identifier(candidate_id)
        temporary = None
        try:
            destination = Path(output).absolute()
            if destination.exists() or destination.is_symlink():
                raise AnimationLabClientError("output_exists")
            # Reserve only our private temporary file. Hard-link installation
            # below is atomic and cannot replace a destination created meanwhile.
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".animation-lab-",
                                             suffix=".part", delete=False) as stream:
                temporary = Path(stream.name)
                if candidate_id is None:
                    candidate_id = self.status(commission_id).get("best_candidate_id")
                    if candidate_id is None:
                        raise AnimationLabClientError("best_candidate_unavailable")
                suffix = ("commissions/" + commission_path + "/candidates/"
                          + _identifier(candidate_id) + "/" + kind)
                limit = MAX_MEDIA_BYTES if kind == "media" else MAX_RESPONSE_BYTES
                checksum, count = hashlib.sha256(), 0
                with self._response(suffix, mime="video/mp4" if kind == "media" else "application/json",
                                    limit=limit) as response:
                    while True:
                        block = response.read(min(64 * 1024, limit + 1 - count))
                        if not block:
                            break
                        count += len(block)
                        if count > limit:
                            raise AnimationLabClientError("response_too_large")
                        try:
                            stream.write(block)
                        except OSError:
                            raise AnimationLabClientError("output_failed") from None
                        checksum.update(block)
                    size = response.headers.get("Content-Length")
                    if size is not None and count != int(size):
                        raise AnimationLabClientError("incomplete_response")
            if kind == "source":
                _json_object(temporary.read_bytes(), limit=limit, code="invalid_response_json")
            os.link(temporary, destination)
            return dict(commission_id=commission_id, candidate_id=candidate_id, kind=kind,
                        bytes=count, sha256=checksum.hexdigest())
        except FileExistsError:
            raise AnimationLabClientError("output_exists") from None
        except (OSError, ValueError, TypeError):
            raise AnimationLabClientError("output_failed") from None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except OSError:
                    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise AnimationLabClientError("invalid_arguments")


def _parser():
    parser = _Parser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help="Site directory or /api/method-lab base; HTTPS except loopback")
    parser.add_argument("--cookie-file", required=True,
                        help="Operator-provided Netscape/Mozilla cookie file; never exported")
    parser.add_argument("--request-timeout", type=float, default=30,
                        help="Per-request network timeout in seconds (default: 30)")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    commands.add_parser("catalog", help="Read effects and commissions, without exposing the CSRF token")
    submit = commands.add_parser("submit", help="Submit original JSON with an explicit UUID request_id")
    submit.add_argument("request_file")
    status = commands.add_parser("status", help="Read a commission")
    status.add_argument("commission_id")
    wait = commands.add_parser("wait", help="Poll until completed, failed or cancelled")
    wait.add_argument("commission_id")
    wait.add_argument("--timeout", type=float, required=True)
    wait.add_argument("--interval", type=float, required=True)
    feedback = commands.add_parser("feedback", help="Save feedback from a JSON file; grants no approval")
    feedback.add_argument("commission_id")
    feedback.add_argument("request_file")
    cancel = commands.add_parser("cancel", help="Request cancellation; cancelling may still be active")
    cancel.add_argument("commission_id")
    download = commands.add_parser("download", help="Download a candidate through a fixed same-origin route")
    download.add_argument("commission_id")
    download.add_argument("--candidate-id", help="Default: the commission's best_candidate_id")
    download.add_argument("--kind", choices=("media", "source"), default="media")
    download.add_argument("--output", required=True, help="New output file; existing files are never overwritten")
    return parser


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
        client = AnimationLabClient(args.base_url, cookie_file=args.cookie_file,
                                    request_timeout=args.request_timeout)
        if args.command == "catalog":
            result = client.catalog()
        elif args.command == "submit":
            result = client.submit(args.request_file)
        elif args.command == "status":
            result = client.status(args.commission_id)
        elif args.command == "wait":
            result = client.wait(args.commission_id, timeout=args.timeout, interval=args.interval)
        elif args.command == "feedback":
            result = client.feedback(args.commission_id, args.request_file)
        elif args.command == "download":
            result = client.download(args.commission_id, candidate_id=args.candidate_id,
                                     kind=args.kind, output=args.output)
        else:
            result = client.cancel(args.commission_id)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except AnimationLabClientError as error:
        print(json.dumps(error.as_dict(), ensure_ascii=True), file=sys.stderr)
        return 1
    except Exception:
        # A CLI traceback must never disclose a credential, response or local path.
        print('{"error": "client_failed"}', file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
