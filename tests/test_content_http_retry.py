from io import BytesIO
from urllib.error import HTTPError

import pytest

from content_agents.common import http


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b'{"items": []}'


def _bad_gateway():
    return HTTPError(
        url="http://llm.test",
        code=502,
        msg="Bad Gateway",
        hdrs=None,
        fp=BytesIO(b'{"detail":"upstream timeout"}'),
    )


def test_502_is_retried_once_then_succeeds(monkeypatch):
    calls = []

    def fake_urlopen(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise _bad_gateway()
        return _Response()

    monkeypatch.setattr(http, "urlopen", fake_urlopen)

    assert http.post_json("http://llm.test", {"x": 1}) == {"items": []}
    assert len(calls) == 2


def test_second_502_raises_with_reason_and_attempt_count(monkeypatch):
    monkeypatch.setattr(http, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(_bad_gateway()))

    with pytest.raises(http.HttpClientError) as caught:
        http.post_json("http://llm.test", {"x": 1})

    assert caught.value.status_code == 502
    assert caught.value.attempts == 2
    assert caught.value.response_detail == "upstream timeout"
