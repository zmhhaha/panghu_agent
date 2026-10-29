"""Bounded in-cluster transport uses fixed authority and private error codes."""
from __future__ import annotations

import io
from pathlib import Path

import httpx
import pytest

from agent_service.contracts import ServiceError
from agent_service.kubernetes_client import KubernetesClient


PATH = '/api/v1/namespaces/agent-workers/pods/example'
TOKEN = 'synthetic-private-kubernetes-token-0000000'


class Chunks(httpx.SyncByteStream):
    def __init__(self, values):
        self.values, self.read, self.closed = values, 0, False

    def __iter__(self):
        for value in self.values:
            self.read += 1
            yield value

    def close(self):
        self.closed = True


def client_for(handler, *, token_reader=lambda: TOKEN, follow_redirects=False):
    http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=follow_redirects,
                        trust_env=False)
    return KubernetesClient(token_reader=token_reader, client=http)


def production_factory(monkeypatch, tokens, handler):
    """Observe real constructor flags without reading the host's SA or network."""
    import agent_service.kubernetes_client as module
    token_reads, ssl_options, client_options = [], [], []
    values = iter(tokens)
    authority = object()
    original_open, original_client = Path.open, httpx.Client
    expected_root = Path('/var/run/secrets/kubernetes.io/serviceaccount')

    def open_file(path, mode='r', *args, **kwargs):
        if path == expected_root / 'token':
            assert mode == 'rb'
            token_reads.append(str(path))
            return io.BytesIO(next(values))
        return original_open(path, mode, *args, **kwargs)

    def ssl_context(**kwargs):
        ssl_options.append(kwargs)
        return authority

    def http_client(**kwargs):
        client_options.append(kwargs)
        assert kwargs['verify'] is authority
        return original_client(transport=httpx.MockTransport(handler), timeout=kwargs['timeout'],
            trust_env=kwargs['trust_env'], follow_redirects=kwargs['follow_redirects'])

    monkeypatch.setattr(Path, 'open', open_file)
    monkeypatch.setattr(module.ssl, 'create_default_context', ssl_context)
    monkeypatch.setattr(module.httpx, 'Client', http_client)
    return token_reads, ssl_options, client_options


def test_service_account_token_rotation_is_read_each_request_under_fixed_tls_authority(monkeypatch):
    observed = []
    first, second = b'first-rotating-kubernetes-token-000000', b'second-rotating-kubernetes-token-00000'
    def handler(request):
        observed.append((str(request.url), request.headers['authorization']))
        assert request.headers['accept-encoding'] == 'identity'
        return httpx.Response(200, json={'kind': 'Pod'})
    reads, ssl_options, options = production_factory(monkeypatch, [first, second], handler)
    # Ambient Kubernetes endpoints and proxy variables cannot redirect SA auth.
    monkeypatch.setenv('KUBERNETES_SERVICE_HOST', 'untrusted.invalid')
    monkeypatch.setenv('HTTPS_PROXY', 'http://untrusted.invalid:8888')
    monkeypatch.setenv('ALL_PROXY', 'http://untrusted.invalid:8888')
    client = KubernetesClient.from_service_account()
    try:
        assert reads == []  # Token read is lazy and respects projected rotation.
        assert options[0]['trust_env'] is options[0]['follow_redirects'] is False
        assert options[0]['timeout'] == 5
        assert ssl_options == [{'cafile': str(Path('/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'))}]
        assert client.request('GET', PATH) == {'kind': 'Pod'}
        assert client.request('GET', PATH) == {'kind': 'Pod'}
        assert len(reads) == 2
        assert observed == [('https://kubernetes.default.svc' + PATH, 'Bearer ' + first.decode()),
                            ('https://kubernetes.default.svc' + PATH, 'Bearer ' + second.decode())]
    finally:
        client.close()
    assert client.client.is_closed


@pytest.mark.parametrize('token', [b'short', b' ' * 32, b'x' * 16385, b'\xff' * 40, b'a' * 32 + b'\ninvalid'])
def test_invalid_projected_tokens_fail_without_http_or_echo(monkeypatch, token):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={})
    production_factory(monkeypatch, [token], handler)
    client = KubernetesClient.from_service_account()
    try:
        with pytest.raises(ServiceError) as error:
            client.request('GET', PATH)
        assert error.value.code == 'kubernetes_unavailable'
        assert calls == []
        assert str(error.value) == 'kubernetes_unavailable'
    finally:
        client.close()


@pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
def test_redirect_never_forwards_service_account_auth_even_if_injected_client_follows(status):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(status, headers={'Location': 'https://untrusted.invalid/collect'},
                              content=TOKEN.encode())
    client = client_for(handler, follow_redirects=True)
    try:
        with pytest.raises(ServiceError) as error:
            client.request('GET', PATH)
        assert error.value.code == 'kubernetes_request_rejected'
        assert calls == ['https://kubernetes.default.svc' + PATH]
        assert TOKEN not in str(error.value)
    finally:
        client.close()


@pytest.mark.parametrize('status,code,public_status', [
    (404, 'kubernetes_not_found', 404),
    (409, 'kubernetes_conflict', 409),
    (401, 'kubernetes_request_rejected', 503),
    (403, 'kubernetes_request_rejected', 503),
    (500, 'kubernetes_request_rejected', 503),
])
def test_http_status_is_classified_without_error_body_exposure(status, code, public_status):
    client = client_for(lambda request: httpx.Response(status, content=TOKEN.encode()))
    try:
        with pytest.raises(ServiceError) as error:
            client.request('GET', PATH)
        assert error.value.code == code and error.value.status == public_status
        assert TOKEN not in str(error.value)
    finally:
        client.close()


@pytest.mark.parametrize('body', [
    b'not JSON', b'[]', b'null', b'"private native text"',
    b'{"kind":"Pod","kind":"Secret"}',
    b'{"status":{"phase":"Running","phase":"Failed"}}',
    b'{"counter":NaN}', b'{"counter":Infinity}', b'{"counter":-Infinity}', b'{"invalid":"\xff"}',
])
def test_non_object_or_ambiguous_json_is_rejected_with_a_fixed_error(body):
    client = client_for(lambda request: httpx.Response(200, content=body))
    try:
        with pytest.raises(ServiceError) as error:
            client.request('GET', PATH)
        assert error.value.code == 'kubernetes_unavailable'
        assert 'private native text' not in str(error.value)
    finally:
        client.close()


def test_oversized_response_stops_consumption_and_closes_stream():
    stream = Chunks([b'x' * 65536] * 40)
    client = client_for(lambda request: httpx.Response(200, stream=stream))
    try:
        with pytest.raises(ServiceError) as error:
            client.request('GET', PATH)
        assert error.value.code == 'kubernetes_unavailable'
        assert stream.read == 33 and stream.closed
    finally:
        client.close()


@pytest.mark.parametrize('encoding', ['gzip', 'br', 'deflate', 'unknown'])
def test_encoded_response_is_rejected_before_consuming_private_body(encoding):
    stream = Chunks([TOKEN.encode()])
    client = client_for(lambda request: httpx.Response(200, headers={'Content-Encoding': encoding}, stream=stream))
    try:
        with pytest.raises(ServiceError) as error:
            client.request('GET', PATH)
        assert error.value.code == 'kubernetes_unavailable'
        assert stream.read == 0 and stream.closed
        assert TOKEN not in str(error.value)
    finally:
        client.close()


@pytest.mark.parametrize('source', ['token', 'transport', 'stream'])
def test_underlying_failures_do_not_echo_secrets_or_raw_exception(source, capsys):
    def token_reader():
        if source == 'token':
            raise RuntimeError('private token reader failure ' + TOKEN)
        return TOKEN
    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            raise httpx.ReadError('private stream failure ' + TOKEN)
            yield b''
    def handler(request):
        if source == 'transport':
            raise httpx.ConnectError('private URL and token ' + TOKEN)
        return httpx.Response(200, stream=BrokenStream())
    client = client_for(handler, token_reader=token_reader)
    try:
        with pytest.raises(ServiceError) as error:
            client.request('GET', PATH)
        assert str(error.value) == 'kubernetes_unavailable'
        assert error.value.__suppress_context__ is True
        assert capsys.readouterr() == ('', '')
    finally:
        client.close()


@pytest.mark.parametrize('path', ['/other', 'https://untrusted.invalid/',
    '/api/v1/namespaces/agent-workers/../other', '/api/v1/namespaces/agent-workers\\other'])
def test_non_namespaced_or_traversing_paths_are_rejected_before_auth_read(path):
    def forbidden():
        raise AssertionError('Invalid path must not read the service account token')
    client = client_for(lambda request: httpx.Response(200, json={}), token_reader=forbidden)
    try:
        with pytest.raises(ServiceError) as error:
            client.request('GET', path)
        assert error.value.code == 'kubernetes_path_invalid'
    finally:
        client.close()
