"""Real loopback HTTP coverage for the standalone, standard-library CLI."""
from collections import deque
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from video_workbench import animation_lab_client as module
from video_workbench.animation_lab_client import AnimationLabClient, AnimationLabClientError


PREFIX = "/dev/animations/api/method-lab/"
TOKEN = "csrf-fixture-token-with-sufficient-length"
SESSION = "operator-provided-session-fixture"
REQUEST_ID = "cd8b0724-b5e0-4e67-b83f-41296c991db8"
COMMISSION_ID = "commission-1"


class FixtureHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), FixtureHandler)
        self.calls = []
        self.responses = deque()
        self.states = deque()


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def handle_request(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.calls.append({"method": self.command, "path": self.path,
                                  "headers": dict(self.headers), "raw": raw})
        if self.server.responses:
            spec = self.server.responses.popleft()
        elif self.path == PREFIX + "catalog":
            spec = {"json": {"csrf_token": TOKEN, "commissions": [], "effects": []}}
        elif self.command == "POST" and self.path == PREFIX + "commissions":
            spec = {"status": 202, "json": {"id": COMMISSION_ID, "state": "queued"}}
        else:
            state = self.server.states.popleft() if self.server.states else "completed"
            spec = {"json": {"id": COMMISSION_ID, "state": state}}
        if spec.get("delay"):
            time.sleep(spec["delay"])
        content = spec.get("raw")
        if content is None:
            content = json.dumps(spec.get("json", {})).encode("utf-8")
        self.send_response(spec.get("status", 200))
        self.send_header("Content-Type", spec.get("content_type", "application/json"))
        if spec.get("length", len(content)) is not None:
            self.send_header("Content-Length", str(spec.get("length", len(content))))
        for key, value in spec.get("headers", {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    do_GET = handle_request
    do_POST = handle_request


@contextmanager
def serving():
    server = FixtureHTTPServer()
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def cookie_file(tmp_path, *, path="/dev/animations/", secure=False, expires="0"):
    target = tmp_path / "operator.cookies.txt"
    target.write_text("# Netscape HTTP Cookie File\n127.0.0.1\tFALSE\t" + path
                      + "\t" + ("TRUE" if secure else "FALSE") + "\t" + expires
                      + "\tfixture_session\t" + SESSION + "\n", encoding="utf-8")
    return target


@pytest.fixture
def peer(tmp_path):
    with serving() as server:
        # Empty expiry is a session cookie in the Netscape format.
        cookies = cookie_file(tmp_path, expires="")
        base = "http://127.0.0.1:" + str(server.server_port) + "/dev/animations/"
        yield server, AnimationLabClient(base, cookie_file=cookies), cookies, base


def request_file(tmp_path, raw=None):
    if raw is None:
        raw = ('{\n  "request_id": "' + REQUEST_ID + '",\n'
               '  "goal": "呈现清晰的因果关系", "budget": {"max_rounds": 2, "max_seconds": 60},\n'
               '  "base_commission_id": "previous", "base_candidate_id": "candidate-1",\n'
               '  "feedback": "保留关键关系"\n}\n').encode("utf-8")
    target = tmp_path / "request.json"
    target.write_bytes(raw)
    return target, raw


def test_constructor_does_not_connect_and_catalog_hides_csrf(peer):
    server, client, _, _ = peer
    assert server.calls == []
    assert client.catalog() == {"commissions": [], "effects": []}
    [call] = server.calls
    assert call["path"] == PREFIX + "catalog"
    assert call["headers"]["Cookie"] == "fixture_session=" + SESSION
    assert all(key not in call["headers"] for key in (
        "Authorization", "X-Forwarded-User", "X-Animation-Creation-Owner", "Origin",
        "X-Animation-Lab-Token",
    ))


@pytest.mark.parametrize("api_base", [False, True])
def test_submit_fetches_token_then_posts_identical_file_and_preserves_continuation(peer, tmp_path, api_base):
    server, client, cookies, base = peer
    if api_base:
        client = AnimationLabClient(base + "api/method-lab", cookie_file=cookies)
    path, raw = request_file(tmp_path)
    assert client.submit(path) == {"id": COMMISSION_ID, "state": "queued"}
    assert [(call["method"], call["path"]) for call in server.calls] == [
        ("GET", PREFIX + "catalog"), ("POST", PREFIX + "commissions"),
    ]
    post = server.calls[-1]
    assert post["raw"] == raw
    assert json.loads(post["raw"])["request_id"] == REQUEST_ID
    assert post["headers"]["Origin"] == base.removesuffix("/dev/animations/")
    assert post["headers"]["X-Animation-Lab-Token"] == TOKEN
    assert post["headers"]["Content-Type"] == "application/json"
    assert post["headers"]["Cookie"] == "fixture_session=" + SESSION
    assert not any(key in post["headers"] for key in ("Authorization", "X-Forwarded-User"))


def test_status_feedback_cancel_use_only_explicit_nonapproval_routes(peer, tmp_path):
    server, client, _, _ = peer
    assert client.status(COMMISSION_ID)["state"] == "completed"
    raw = json.dumps({"request_id": REQUEST_ID, "candidate_id": "candidate-1",
                      "comment": "此处转折需要更清楚"}, ensure_ascii=False).encode("utf-8")
    path, _ = request_file(tmp_path, raw)
    client.feedback(COMMISSION_ID, path)
    client.cancel(COMMISSION_ID)
    assert [(call["method"], call["path"]) for call in server.calls] == [
        ("GET", PREFIX + "commissions/commission-1"),
        ("GET", PREFIX + "catalog"),
        ("POST", PREFIX + "commissions/commission-1/feedback"),
        ("GET", PREFIX + "catalog"),
        ("POST", PREFIX + "commissions/commission-1/cancel"),
    ]
    assert server.calls[2]["raw"] == raw
    assert server.calls[4]["raw"] == b"{}"


@pytest.mark.parametrize("final", ["completed", "failed", "cancelled"])
def test_wait_handles_real_commission_state_machine_without_posting(peer, final):
    server, client, _, _ = peer
    server.states.extend(["queued", "running", "cancelling", final])
    assert client.wait(COMMISSION_ID, timeout=2, interval=.005)["state"] == final
    assert len(server.calls) == 4 and all(call["method"] == "GET" for call in server.calls)


def test_wait_timeout_is_bounded_and_review_is_not_a_commission_terminal_state(peer):
    server, client, _, _ = peer
    server.states.extend(["running"] * 100)
    started = time.monotonic()
    with pytest.raises(AnimationLabClientError, match="^wait_timeout$"):
        client.wait(COMMISSION_ID, timeout=.06, interval=.02)
    assert time.monotonic() - started < 1
    assert all(call["method"] == "GET" for call in server.calls)
    server.states.clear()
    server.states.append("review")
    with pytest.raises(AnimationLabClientError, match="^invalid_commission_state$"):
        client.wait(COMMISSION_ID, timeout=1, interval=.01)


@pytest.mark.parametrize("kwargs", [
    {"timeout": 0, "interval": 1}, {"timeout": float("inf"), "interval": 1},
    {"timeout": 1, "interval": 0}, {"timeout": 1, "interval": float("nan")},
])
def test_invalid_wait_parameters_do_not_connect(peer, kwargs):
    server, client, _, _ = peer
    with pytest.raises(AnimationLabClientError):
        client.wait(COMMISSION_ID, **kwargs)
    assert server.calls == []


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_is_never_followed_or_exposed(peer, status):
    server, client, _, _ = peer
    server.responses.append({"status": status, "raw": b"sensitive SSO error",
                             "headers": {"Location": "http://127.0.0.1:9/sso?private=value"}})
    with pytest.raises(AnimationLabClientError) as caught:
        client.catalog()
    assert caught.value.as_dict() == {"error": "redirect_rejected"}
    assert len(server.calls) == 1
    assert "sensitive" not in str(caught.value) and "private" not in str(caught.value)


def test_post_redirect_is_not_replayed(peer, tmp_path):
    server, client, _, _ = peer
    server.responses.extend([
        {"json": {"csrf_token": TOKEN}},
        {"status": 307, "headers": {"Location": PREFIX + "commissions"}},
    ])
    path, _ = request_file(tmp_path)
    with pytest.raises(AnimationLabClientError, match="^redirect_rejected$"):
        client.submit(path)
    assert len(server.calls) == 2
    assert [call["method"] for call in server.calls].count("POST") == 1


@pytest.mark.parametrize("status", [400, 401, 403, 409, 429, 500, 503])
def test_http_errors_do_not_parse_or_expose_server_body(peer, status):
    server, client, _, _ = peer
    server.responses.append({"status": status, "raw": (SESSION + " error sensitive body").encode()})
    with pytest.raises(AnimationLabClientError) as caught:
        client.catalog()
    assert caught.value.as_dict() == {"error": "http_" + str(status)}
    assert caught.value.__cause__ is None
    assert SESSION not in str(caught.value)


def test_sso_html_is_not_treated_as_json_or_exposed(peer):
    server, client, _, _ = peer
    server.responses.append({"content_type": "text/html", "raw": ("<html>" + SESSION).encode()})
    with pytest.raises(AnimationLabClientError) as caught:
        client.catalog()
    assert caught.value.as_dict() == {"error": "authentication_required"}


@pytest.mark.parametrize("raw", [
    b'{"request_id":"one","request_id":"two"}',
    b'{"nested":{"a":1,"a":2}}', b'{"a":NaN}', b'{"a":Infinity}', b'{"a":1e9999}',
    b'[]', b'{', b'{"a":"\\ud800"}', b'\xff',
])
def test_invalid_request_json_is_rejected_before_catalog(peer, tmp_path, raw):
    server, client, _, _ = peer
    path, _ = request_file(tmp_path, raw)
    with pytest.raises(AnimationLabClientError, match="^invalid_request_json$"):
        client.submit(path)
    assert server.calls == []


@pytest.mark.parametrize("request_id", [None, "", "new-id", 42, "{cd8b0724-b5e0-4e67-b83f-41296c991db8}"])
def test_explicit_uuid_request_id_is_required_and_never_generated(peer, tmp_path, request_id):
    server, client, _, _ = peer
    path, _ = request_file(tmp_path, json.dumps({"request_id": request_id}).encode())
    with pytest.raises(AnimationLabClientError, match="^explicit_uuid_request_id_required$"):
        client.submit(path)
    assert server.calls == []


def test_request_size_is_bounded_before_connecting(peer, tmp_path, monkeypatch):
    server, client, _, _ = peer
    monkeypatch.setattr(module, "MAX_REQUEST_BYTES", 100)
    path, _ = request_file(tmp_path, b" " * 101)
    with pytest.raises(AnimationLabClientError, match="^request_too_large$"):
        client.submit(path)
    assert server.calls == []


@pytest.mark.parametrize("raw", [
    b'{"state":"completed","state":"failed"}', b'{"value":NaN}', b'{"value":1e9999}',
    b'{"nested":{"key":1,"key":2}}', b'[]', b'{"value":"\\ud800"}',
])
def test_invalid_response_json_is_rejected_without_body_disclosure(peer, raw):
    server, client, _, _ = peer
    server.responses.append({"raw": raw})
    with pytest.raises(AnimationLabClientError, match="^invalid_response_json$"):
        client.status(COMMISSION_ID)


@pytest.mark.parametrize("advertise", [False, True])
def test_response_size_is_bounded(peer, monkeypatch, advertise):
    server, client, _, _ = peer
    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 100)
    spec = {"raw": b" " * 101}
    if advertise:
        spec["length"] = 999999999
    else:
        spec["length"] = None
    server.responses.append(spec)
    with pytest.raises(AnimationLabClientError, match="^response_too_large$"):
        client.catalog()


@pytest.mark.parametrize("base", [
    "http://example.org/dev/animations/", "http://127.0.0.1.evil.invalid/", "https://user:secret@example.org/",
    "https://example.org/dev/../", "https://example.org/dev/%2e%2e/", "https://example.org//api/",
    "https://example.org/path?secret=value", "https://example.org/#fragment", "ftp://127.0.0.1/",
    "https://example.org/\\escape", "https://example.org/\nsecret", "https://example.org:99999/",
])
def test_unsafe_base_urls_are_rejected_before_loading_credentials(tmp_path, base):
    with pytest.raises(AnimationLabClientError, match="^invalid_base_url$"):
        AnimationLabClient(base, cookie_file=tmp_path / "not-read")


@pytest.mark.parametrize("value", ["../escape", "..", "/sso", "https://evil.invalid", "%2fescape", "a?x=y", "a#x", "a\\b", "a\nsecret"])
def test_identifiers_cannot_escape_the_fixed_api_directory(peer, value):
    server, client, _, _ = peer
    for operation in (client.status, client.cancel):
        with pytest.raises(AnimationLabClientError, match="^invalid_commission_id$"):
            operation(value)
    assert server.calls == []


@pytest.mark.parametrize("secure,expires,path", [(True, "", "/"), (False, "1", "/"), (False, "", "/another/")])
def test_cookiejar_preserves_secure_expiry_and_path_scope(peer, tmp_path, secure, expires, path):
    server, _, _, base = peer
    cookies = cookie_file(tmp_path, secure=secure, expires=expires, path=path)
    client = AnimationLabClient(base, cookie_file=cookies)
    client.catalog()
    assert "Cookie" not in server.calls[-1]["headers"]


@pytest.mark.parametrize("header", ["not Netscape: ", "# Netscape HTTP Cookie File\n"])
def test_cookie_file_failures_do_not_echo_credential_data(tmp_path, recwarn, header):
    cookies = tmp_path / "operator.cookies.txt"
    cookies.write_text(header + SESSION, encoding="utf-8")
    with pytest.raises(AnimationLabClientError) as caught:
        AnimationLabClient(cookie_file=cookies)
    assert caught.value.as_dict() == {"error": "cookie_file_unreadable"}
    assert caught.value.__cause__ is None
    assert not recwarn


def test_submit_timeout_is_unknown_and_never_retries_post(peer, tmp_path):
    server, _, cookies, base = peer
    client = AnimationLabClient(base, cookie_file=cookies, request_timeout=.03)
    server.responses.extend([
        {"json": {"csrf_token": TOKEN}},
        {"delay": .15, "json": {"id": COMMISSION_ID, "state": "queued"}},
    ])
    path, _ = request_file(tmp_path)
    with pytest.raises(AnimationLabClientError) as caught:
        client.submit(path)
    assert caught.value.code == "submission_outcome_unknown"
    assert caught.value.as_dict()["request_id"] == REQUEST_ID
    assert "same request_id" in caught.value.as_dict()["guidance"]
    assert len(server.calls) == 2 and sum(call["method"] == "POST" for call in server.calls) == 1
    assert SESSION not in json.dumps(caught.value.as_dict())


def test_failed_catalog_prevents_post(peer, tmp_path):
    server, client, _, _ = peer
    server.responses.append({"status": 401, "raw": SESSION.encode()})
    path, _ = request_file(tmp_path)
    with pytest.raises(AnimationLabClientError, match="^http_401$"):
        client.submit(path)
    assert len(server.calls) == 1 and server.calls[0]["method"] == "GET"


def test_cli_success_and_errors_are_safe_json(peer, capsys):
    server, _, cookies, base = peer
    common = ["--base-url", base, "--cookie-file", str(cookies)]
    assert module.main(common + ["catalog"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"commissions": [], "effects": []}
    assert not captured.err and TOKEN not in captured.out and SESSION not in captured.out
    server.responses.append({"status": 403, "raw": (SESSION + TOKEN).encode()})
    assert module.main(common + ["catalog"]) == 1
    captured = capsys.readouterr()
    assert not captured.out and json.loads(captured.err) == {"error": "http_403"}
    calls = len(server.calls)
    assert module.main(common + ["wait", COMMISSION_ID]) == 1
    assert json.loads(capsys.readouterr().err) == {"error": "invalid_arguments"}
    assert len(server.calls) == calls


def test_downloaded_standalone_file_has_no_repository_dependency(tmp_path):
    standalone = tmp_path / "agent-api-client.py"
    standalone.write_bytes(Path(module.__file__).read_bytes())
    outcome = subprocess.run([sys.executable, "-I", str(standalone), "--help"], cwd=tmp_path,
                             capture_output=True, text=True, timeout=10)
    assert outcome.returncode == 0 and not outcome.stderr
    assert "--cookie-file" in outcome.stdout and "submit" in outcome.stdout


def test_download_media_uses_best_id_and_ignores_server_supplied_urls(peer, tmp_path):
    server, client, _, _ = peer
    raw = b"\x00\x00\x00\x18ftypmp42-real-loopback-fixture"
    server.responses.extend([
        {"json": {"id": COMMISSION_ID, "best_candidate_id": "candidate-1",
                   "media_url": "https://evil.invalid/steal-session"}},
        {"raw": raw, "content_type": "video/mp4"},
    ])
    output = tmp_path / "candidate.mp4"
    result = client.download(COMMISSION_ID, output=output)
    assert output.read_bytes() == raw
    assert result == dict(commission_id=COMMISSION_ID, candidate_id="candidate-1", kind="media",
                          bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    assert [call["path"] for call in server.calls] == [
        PREFIX + "commissions/commission-1",
        PREFIX + "commissions/commission-1/candidates/candidate-1/media",
    ]
    assert not list(tmp_path.glob(".animation-lab-*.part"))


def test_download_source_preserves_bytes_and_explicit_candidate_needs_no_status(peer, tmp_path):
    server, client, _, _ = peer
    raw = b'{\n "engine": "html-svg", "files": {"scene.html": "<svg/>"}\n}\n'
    server.responses.append({"raw": raw})
    output = tmp_path / "source.json"
    result = client.download(COMMISSION_ID, candidate_id="candidate-1", kind="source", output=output)
    assert output.read_bytes() == raw and result["kind"] == "source"
    assert [call["path"] for call in server.calls] == [
        PREFIX + "commissions/commission-1/candidates/candidate-1/source",
    ]


def test_download_never_overwrites_even_when_another_writer_wins(peer, tmp_path, monkeypatch):
    server, client, _, _ = peer
    output = tmp_path / "candidate.mp4"
    output.write_bytes(b"existing-operator-file")
    with pytest.raises(AnimationLabClientError, match="^output_exists$"):
        client.download(COMMISSION_ID, candidate_id="candidate-1", output=output)
    assert output.read_bytes() == b"existing-operator-file" and not server.calls
    racing_output = tmp_path / "racing.mp4"
    original_link = module.os.link

    def another_writer(source, destination):
        Path(destination).write_bytes(b"concurrent-operator-file")
        original_link(source, destination)

    monkeypatch.setattr(module.os, "link", another_writer)
    server.responses.append({"raw": b"candidate-media", "content_type": "video/mp4"})
    with pytest.raises(AnimationLabClientError, match="^output_exists$"):
        client.download(COMMISSION_ID, candidate_id="candidate-1", output=racing_output)
    assert racing_output.read_bytes() == b"concurrent-operator-file"
    assert not list(tmp_path.glob(".animation-lab-*.part"))


@pytest.mark.parametrize("spec,code", [
    ({"raw": b" " * 101, "content_type": "video/mp4", "length": None}, "response_too_large"),
    ({"raw": b"truncated", "content_type": "video/mp4", "length": 99}, "incomplete_response"),
    ({"status": 302, "headers": {"Location": "https://evil.invalid/private"}}, "redirect_rejected"),
    ({"raw": SESSION.encode(), "content_type": "text/html"}, "authentication_required"),
])
def test_download_failure_removes_only_its_private_temporary_file(peer, tmp_path, monkeypatch, spec, code):
    server, client, _, _ = peer
    monkeypatch.setattr(module, "MAX_MEDIA_BYTES", 100)
    other_file = tmp_path / ".animation-lab-other.part"
    other_file.write_bytes(b"not-owned-by-client")
    server.responses.append(spec)
    output = tmp_path / "candidate.mp4"
    with pytest.raises(AnimationLabClientError, match="^" + code + "$"):
        client.download(COMMISSION_ID, candidate_id="candidate-1", output=output)
    assert not output.exists() and other_file.read_bytes() == b"not-owned-by-client"
    assert list(tmp_path.glob(".animation-lab-*.part")) == [other_file]
    assert len(server.calls) == 1


def test_download_source_rejects_duplicate_json_keys_before_installing(peer, tmp_path):
    server, client, _, _ = peer
    server.responses.append({"raw": b'{"engine":"html-svg","engine":"other"}'})
    output = tmp_path / "source.json"
    with pytest.raises(AnimationLabClientError, match="^invalid_response_json$"):
        client.download(COMMISSION_ID, candidate_id="candidate-1", kind="source", output=output)
    assert not output.exists() and not list(tmp_path.glob(".animation-lab-*.part"))
