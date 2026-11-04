"""Public documentation imports: honest bytes, commit pins and network bounds."""
import base64
import hashlib
import json
import socket
import time
from unittest.mock import MagicMock

import pytest

from scientific_agent import official_sources as sources
from scientific_agent.workspace_materials import materials_summary


COMMIT = 'a' * 40
BASE = 'https://api.github.com/repos/example/project'


def github_file(path, text):
    raw = text.encode('utf-8')
    return dict(type='file', encoding='base64', path=path, size=len(raw),
        sha=hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest(),
        content=base64.b64encode(raw).decode('ascii'))


class FixtureTransport:
    """Explicit deterministic HTTP fixture; not an online acquisition result."""
    def __init__(self, mapping):
        self.mapping, self.requests = mapping, []

    def get(self, url, *, deadline, limit):
        self.requests.append((url, deadline, limit))
        value = self.mapping[url]
        if isinstance(value, sources.SourceResponse):
            return value
        if isinstance(value, int):
            return sources.SourceResponse(url, value, {}, b'')
        return sources.SourceResponse(url, 200, {'content-type': 'application/json'}, json.dumps(value).encode())


def repo_fixture(**extra):
    return FixtureTransport({BASE: {'default_branch': 'main'},
        BASE + '/commits/main': {'sha': COMMIT},
        BASE + '/readme?ref=' + COMMIT: github_file('README.md', '# Project\nInstall the declared dependencies.\n'),
        BASE + '/contents/pyproject.toml?ref=' + COMMIT: github_file('pyproject.toml', '[project]\nname = "project"\n'),
        **extra})


def web_fixture(body, *, content_type='text/html; charset=utf-8', url='https://docs.example.org/guide'):
    return FixtureTransport({url: sources.SourceResponse(url, 200, {'content-type': content_type}, body)})


@pytest.mark.parametrize('url', [None, 1, '', ' https://example.org/',
    'http://example.org/', 'file:///etc/passwd', 'https://user:password@example.org/',
    'https://example.org:8080/', 'https://example.org/?token=secret',
    'https://127.0.0.1/', 'https://10.0.0.1/', 'https://169.254.169.254/',
    'https://100.64.0.1/', 'https://198.18.0.1/', 'https://[::1]/',
    'https://[::ffff:127.0.0.1]/', 'https://localhost/', 'https://service.svc/',
    'https://svc.namespace.svc.cluster.local/', 'https://host.internal/',
    'https://example.org/\r\nHost:localhost', 'https://example.org/%0d%0aHost:localhost',
    'https://example.org\\@127.0.0.1/'])
def test_invalid_targets_fail_before_network(monkeypatch, url):
    dns = MagicMock(side_effect=AssertionError('DNS must not run'))
    monkeypatch.setattr(sources.public_https, '_https', dns)
    with pytest.raises(sources.OfficialSourceError, match='official_source_invalid_url'):
        sources.fetch_official_material(url)
    dns.assert_not_called()


def test_root_import_freezes_all_files_to_the_same_commit():
    client = repo_fixture()
    material = sources.fetch_official_material('https://github.com/example/project', transport=client)
    assert material['status'] == 'ready' and material['access_level'] == 'full_text'
    assert 'Install the declared dependencies.' in material['text']
    assert '[project]\nname = "project"' in material['text']
    assert COMMIT in material['text']
    provenance = material['retrieval']
    assert provenance['github']['commit'] == COMMIT
    assert provenance['final_url'] == 'https://github.com/example/project/tree/' + COMMIT
    assert provenance['authority'] == 'user_selected_url'
    requests = [url for url, _, _ in client.requests]
    assert requests == [BASE, BASE + '/commits/main', BASE + '/readme?ref=' + COMMIT,
                        BASE + '/contents/pyproject.toml?ref=' + COMMIT]
    assert all(limit == sources.MAX_RESPONSE_BYTES for _, _, limit in client.requests)
    assert len({deadline for _, deadline, _ in client.requests}) == 1
    assert material['retrieval']['extracted_sha256'] == material['text_sha256']
    summary = materials_summary([material, material])
    assert summary['paper_count'] == 0 and summary['document_count'] == 2
    # Every declared range points at the actual bytes, not the provenance wrapper.
    lines = material['text'].splitlines()
    for declaration, document in zip(material['declared_sources'], provenance['github']['files']):
        excerpt = '\n'.join(lines[declaration['start_line'] - 1:declaration['end_line']])
        assert hashlib.sha256(excerpt.encode()).hexdigest() == declaration['excerpt_sha256']
        assert declaration['title'] == document['path']


def test_missing_optional_repository_file_preserves_readme_and_explicit_gap():
    client = repo_fixture(**{BASE + '/contents/pyproject.toml?ref=' + COMMIT: 404})
    material = sources.fetch_official_material('https://github.com/example/project', transport=client)
    assert material['status'] == 'ready'
    assert len(material['declared_sources']) == 1
    assert material['retrieval']['gaps'] == [{'path': 'pyproject.toml', 'code': 'official_source_not_found'}]
    assert 'pyproject.toml' not in material['text']


def test_missing_required_readme_does_not_produce_a_ready_material():
    client = repo_fixture(**{BASE + '/readme?ref=' + COMMIT: 404})
    with pytest.raises(sources.OfficialSourceError, match='official_source_not_found'):
        sources.fetch_official_material('https://github.com/example/project', transport=client)


def test_optional_file_cannot_exceed_the_combined_text_budget_and_discard_readme():
    text = 'a' * sources.MAX_GITHUB_FILE_BYTES
    client = repo_fixture(**{
        BASE + '/readme?ref=' + COMMIT: github_file('README.md', text),
        BASE + '/contents/pyproject.toml?ref=' + COMMIT: github_file('pyproject.toml', text)})
    material = sources.fetch_official_material('https://github.com/example/project', transport=client)
    assert material['status'] == 'ready' and len(material['text'].encode()) <= sources.MAX_TEXT_BYTES
    assert len(material['declared_sources']) == len(material['retrieval']['github']['files']) == 1
    assert material['retrieval']['gaps'] == [{'path': 'pyproject.toml', 'code': 'official_source_text_limit'}]


def test_blob_import_uses_commit_not_unpinned_download_url():
    path = 'docs/architecture.md'
    record = github_file(path, '# Architecture\nController calls the tool adapter.\n')
    record['download_url'] = 'http://169.254.169.254/secret'
    client = FixtureTransport({BASE + '/commits/v1.2': {'sha': COMMIT},
        BASE + '/contents/' + path + '?ref=' + COMMIT: record})
    material = sources.fetch_official_material('https://github.com/example/project/blob/v1.2/' + path,
                                               transport=client)
    assert material['retrieval']['final_url'].endswith('/blob/' + COMMIT + '/' + path)
    assert [url for url, _, _ in client.requests] == [BASE + '/commits/v1.2',
        BASE + '/contents/' + path + '?ref=' + COMMIT]
    assert 'Controller calls the tool adapter.' in material['text']


@pytest.mark.parametrize('mutation', ['sha', 'path', 'encoding', 'type', 'size', 'content'])
def test_github_file_identity_and_actual_bytes_must_match(mutation):
    record = github_file('README.md', '# Project\nGenuine file bytes.\n')
    record[mutation] = {'sha': 'b' * 40, 'path': '../../etc/passwd', 'encoding': 'none',
        'type': 'symlink', 'size': 999, 'content': 'not valid base64!'}[mutation]
    client = repo_fixture(**{BASE + '/readme?ref=' + COMMIT: record})
    with pytest.raises(sources.OfficialSourceError, match='official_source_invalid_file'):
        sources.fetch_official_material('https://github.com/example/project', transport=client)


@pytest.mark.parametrize('url', ['https://github.com/example',
    'https://github.com/example/project/issues/1',
    'https://github.com/example/project/blob/main',
    'https://github.com/example/project/tree/main/docs',
    'https://github.com/example/project/blob/main/../secret'])
def test_unsupported_repository_target_is_rejected_before_client(url):
    client = FixtureTransport({})
    with pytest.raises(sources.OfficialSourceError, match='official_source_github_url_unsupported'):
        sources.fetch_official_material(url, transport=client)
    assert client.requests == []


def test_html_import_extracts_visible_body_and_records_unknown_version():
    body = b'''<html><head><title>Project documentation</title><style>style-secret</style></head>
      <body><nav>nav-secret</nav><main><h1>Installation</h1>
      <p>Read <code>README.md</code> and <code>pyproject.toml</code>.</p>
      <script>script-secret</script><p>Never assume linked papers were read.</p></main>
      <footer>footer-secret</footer></body></html>'''
    material = sources.fetch_official_material('https://docs.example.org/guide', transport=web_fixture(body))
    assert material['status'] == 'ready'
    assert 'README.md' in material['text'] and 'pyproject.toml' in material['text']
    assert all(value not in material['text'] for value in ['nav-secret', 'style-secret', 'script-secret', 'footer-secret'])
    assert material['retrieval']['version'] is None
    assert material['retrieval']['content_sha256'] == hashlib.sha256(body).hexdigest()
    assert materials_summary([material])['paper_count'] == 0
    assert 'Version: not recorded' in material['text']


def test_plaintext_import_preserves_indented_source_and_unicode():
    body = '接口约束\n    function(arg)\n'.encode()
    material = sources.fetch_official_material('https://docs.example.org/guide',
        transport=web_fixture(body, content_type='text/plain'))
    assert '接口约束\n    function(arg)' in material['text']


def test_declared_line_ranges_do_not_point_beyond_trimmed_trailing_whitespace():
    material = sources.fetch_official_material('https://docs.example.org/guide',
        transport=web_fixture(b'Body text.\n\n  \n', content_type='text/plain'))
    declaration = material['declared_sources'][0]
    lines = material['text'].splitlines()
    assert declaration['end_line'] <= len(lines)
    assert '\n'.join(lines[declaration['start_line'] - 1:declaration['end_line']]) == 'Body text.'


@pytest.mark.parametrize('body,content_type,code', [(b'%PDF-1.7', 'application/pdf', 'content_type_unsupported'),
    (b'\xff\xfe\0\0', 'text/plain', 'utf8_required'),
    (b'<html><script>only scripts</script></html>', 'text/html', 'no_readable_text'),
    (b'abc\0def', 'text/plain', 'no_readable_text')])
def test_unreadable_content_is_not_claimed_as_full_text(body, content_type, code):
    with pytest.raises(sources.OfficialSourceError, match='official_source_' + code):
        sources.fetch_official_material('https://docs.example.org/guide',
            transport=web_fixture(body, content_type=content_type))


def test_extracted_material_respects_the_existing_utf8_byte_limit():
    client = web_fixture(('中' * 180000).encode(), content_type='text/plain')
    with pytest.raises(sources.OfficialSourceError, match='official_source_text_limit'):
        sources.fetch_official_material('https://docs.example.org/guide', transport=client)


def test_oversized_wire_response_is_not_parsed():
    client = web_fixture(b'a' * (sources.MAX_RESPONSE_BYTES + 1), content_type='text/plain')
    with pytest.raises(sources.OfficialSourceError, match='official_source_byte_limit'):
        sources.fetch_official_material('https://docs.example.org/guide', transport=client)


@pytest.mark.parametrize('status,code,retryable', [(302, 'redirect_not_allowed', False),
    (404, 'not_found', False), (429, 'rate_limited', True), (503, 'unavailable', True)])
def test_http_failures_are_safe_and_do_not_follow_redirects(status, code, retryable):
    url = 'https://docs.example.org/guide'
    client = FixtureTransport({url: sources.SourceResponse(url, status,
        {'location': 'http://169.254.169.254/private'}, b'provider secret message')})
    with pytest.raises(sources.OfficialSourceError, match='official_source_' + code) as caught:
        sources.fetch_official_material(url, transport=client)
    assert caught.value.retryable is retryable
    assert 'secret' not in str(caught.value) and len(client.requests) == 1


def test_transport_cannot_silently_replace_the_requested_target():
    requested = 'https://docs.example.org/guide'
    client = FixtureTransport({requested: sources.SourceResponse('https://elsewhere.example.org/',
        200, {'content-type': 'text/plain'}, b'another source')})
    with pytest.raises(sources.OfficialSourceError, match='official_source_redirect_not_allowed'):
        sources.fetch_official_material(requested, transport=client)


def dns_response(host, ips):
    return json.dumps(dict(Status=0, TC=False, Question={'name': host, 'type': 1},
        Answer=[{'name': host, 'type': 1, 'TTL': 0, 'data': ip} for ip in ips])).encode()


def test_mixed_public_private_dns_answers_fail_closed_before_document_request(monkeypatch):
    host, calls = 'docs.example.org', []
    def https(name, address, path, headers, deadline, limit):
        calls.append((name, address))
        assert name == sources.public_https.RESOLVER_HOST
        return 200, {}, dns_response(host, ['93.184.215.14', '169.254.169.254'])
    monkeypatch.setattr(sources.public_https, '_https', https)
    with pytest.raises(sources.OfficialSourceError, match='official_source_dns_unavailable'):
        sources.fetch_official_material('https://' + host + '/')
    assert len(calls) == len(sources.public_https.RESOLVER_IPV4)


def test_malformed_dns_response_is_a_safe_source_failure(monkeypatch):
    malformed = json.dumps({'Status': 0, 'Question': ['invalid question row']}).encode()
    monkeypatch.setattr(sources.public_https, '_https', lambda *_: (200, {}, malformed))
    with pytest.raises(sources.OfficialSourceError, match='official_source_dns_unavailable'):
        sources.fetch_official_material('https://docs.example.org/')


def test_checked_public_address_is_the_one_given_to_pinned_https(monkeypatch):
    host, calls = 'docs.example.org', []
    def https(name, address, path, headers, deadline, limit):
        calls.append((name, address, path))
        if name == sources.public_https.RESOLVER_HOST:
            return 200, {}, dns_response(host, ['93.184.215.14'])
        return 200, {'Content-Type': 'text/plain'}, b'Actual document bytes.'
    monkeypatch.setattr(sources.public_https, '_https', https)
    monkeypatch.setattr(socket, 'getaddrinfo', MagicMock(side_effect=AssertionError('ambient DNS forbidden')))
    material = sources.fetch_official_material('https://' + host + '/guide')
    assert 'Actual document bytes.' in material['text']
    assert calls[-1] == (host, '93.184.215.14', '/guide')


def test_zero_ttl_is_not_cached_between_connections(monkeypatch):
    host, answers = 'docs.example.org', ['93.184.215.14', '10.0.0.1']
    def https(name, address, path, headers, deadline, limit):
        if name == sources.public_https.RESOLVER_HOST:
            return 200, {}, dns_response(host, [answers.pop(0) if answers else '10.0.0.1'])
        return 200, {'Content-Type': 'text/plain'}, b'Public first response.'
    monkeypatch.setattr(sources.public_https, '_https', https)
    client, deadline = sources.PublicSourceTransport(), time.monotonic() + 20
    assert client.get('https://' + host + '/', deadline=deadline).status == 200
    with pytest.raises(sources.OfficialSourceError, match='official_source_dns_unavailable'):
        client.get('https://' + host + '/', deadline=deadline)


def test_expired_shared_deadline_prevents_network(monkeypatch):
    https = MagicMock(side_effect=AssertionError('expired request must not connect'))
    monkeypatch.setattr(sources.public_https, '_https', https)
    with pytest.raises(sources.OfficialSourceError, match='official_source_timeout'):
        sources.PublicSourceTransport().get('https://docs.example.org/', deadline=time.monotonic() - 1)
    https.assert_not_called()


def test_internal_github_ref_query_is_immutable_and_external_queries_are_not_allowed():
    assert sources._url(BASE + '/contents/README.md?ref=' + COMMIT, github_api=True).endswith('?ref=' + COMMIT)
    for url in (BASE + '/contents/README.md?ref=main',
                'https://docs.example.org/?ref=' + COMMIT,
                BASE + '/contents/README.md?ref=' + COMMIT + '&token=secret'):
        with pytest.raises(sources.OfficialSourceError, match='official_source_invalid_url'):
            sources._url(url, github_api=True)


def test_pinned_connection_checks_public_ip_sni_host_and_response_limit(monkeypatch):
    # Exercise the reused real socket primitive; mock only socket/TLS/HTTP I/O.
    raw, secure, context, connection, response = [MagicMock() for _ in range(5)]
    context.wrap_socket.return_value = secure
    connection.getresponse.return_value = response
    response.status = 200
    response.getheaders.return_value = [('Content-Type', 'text/plain')]
    response.getheader.side_effect = lambda name, default=None: {'Content-Length': '4'}.get(name, default)
    response.read1.side_effect = [b'text', b'']
    response.isclosed.return_value = False
    monkeypatch.setattr(sources.public_https.socket, 'socket', MagicMock(return_value=raw))
    monkeypatch.setattr(sources.public_https, '_tls_context', MagicMock(return_value=context))
    monkeypatch.setattr(sources.public_https.http.client, 'HTTPConnection', MagicMock(return_value=connection))
    client = sources.PublicSourceTransport()
    monkeypatch.setattr(client, 'resolve', lambda *_: ('93.184.215.14',))
    fetched = client.get('https://docs.example.org/guide', deadline=time.monotonic() + 10)
    assert fetched.body == b'text'
    raw.connect.assert_called_once_with(('93.184.215.14', 443))
    context.wrap_socket.assert_called_once_with(raw, server_hostname='docs.example.org')
    sent = connection.request.call_args.kwargs['headers']
    assert sent['Host'] == 'docs.example.org' and sent['Accept-Encoding'] == 'identity'
    assert 'Authorization' not in sent and 'Cookie' not in sent
    connection.close.assert_called_once()
