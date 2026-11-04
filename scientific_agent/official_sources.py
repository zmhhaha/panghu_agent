"""Explicit, bounded public documentation imports; never execute repository code.

The caller supplies one HTTPS URL. GitHub imports resolve a commit before reading
file contents. Other URLs yield only their actual HTML/text response. Downloads
use the existing authenticated-DNS, pinned-public-IP HTTPS primitive, with no
proxies, cookies, credentials, automatic redirects, or background URL discovery.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
from html.parser import HTMLParser
import http.client
import ipaddress
import json
import re
import time
from urllib.parse import quote, unquote, urlencode, urlsplit, urlunsplit

from .pi_contracts import utc_now
from .workspace_materials import MaterialError, MAX_TEXT_BYTES, parse_material
from pi_worker.research_tools import transport as public_https


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_IMPORT_SECONDS = 30
MAX_GITHUB_FILE_BYTES = 256 * 1024
_SHA = re.compile(r'[0-9a-f]{40}')
_GITHUB_NAME = re.compile(r'[A-Za-z0-9_.-]{1,100}')
_TEXT_SUFFIXES = frozenset({'.md', '.markdown', '.txt', '.rst', '.toml', '.json',
    '.yaml', '.yml', '.ini', '.cfg', '.py', '.js', '.mjs', '.cjs', '.ts', '.tsx',
    '.jsx', '.rs', '.go', '.c', '.h', '.cpp', '.java', '.sh'})


class OfficialSourceError(MaterialError):
    def __init__(self, code, status=422, *, retryable=False):
        self.retryable = retryable
        super().__init__(code, status)


def _url(value, *, github_api=False):
    """Canonicalize a public HTTPS target before any DNS or socket operation."""
    if (not isinstance(value, str) or not 0 < len(value) <= 2048
            or value != value.strip() or '\\' in value
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
        raise OfficialSourceError('official_source_invalid_url')
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or '').encode('idna').decode('ascii').lower()
        if (parsed.scheme != 'https' or not host or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 443)
                or (parsed.query and not (github_api and host == 'api.github.com'
                    and re.fullmatch(r'ref=[0-9a-f]{40}', parsed.query)))
                or '%' in host or len(host) > 253
                or any(ord(char) < 32 or ord(char) == 127
                       for char in unquote(parsed.path + parsed.fragment))):
            raise ValueError()
        if host.endswith('.'):
            host = host[:-1]
        try:
            # Literal targets must satisfy the same public IPv4 policy as DNS.
            ipaddress.ip_address(host)
        except ValueError:
            if ('.' not in host or host.endswith(('.localhost', '.local', '.internal',
                    '.svc', '.cluster.local'))
                    or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part)
                               for part in host.split('.'))):
                raise ValueError()
        else:
            public_https.public_ipv4(host)
        path = quote(parsed.path or '/', safe='/%:@!$&\'()*+,;=-._~')
        return urlunsplit(('https', host, path, parsed.query, ''))
    except (ValueError, UnicodeError):
        raise OfficialSourceError('official_source_invalid_url') from None


@dataclass(frozen=True)
class SourceResponse:
    url: str
    status: int
    headers: dict
    body: bytes


class PublicSourceTransport:
    """No ambient HTTP client, system resolver, proxies, or credential state."""
    def __init__(self):
        self._cache = {}

    def resolve(self, host, deadline):
        try:
            return (public_https.public_ipv4(host),)
        except ValueError:
            pass
        cached = self._cache.get(host)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        for bootstrap in public_https.RESOLVER_IPV4:
            try:
                status, _, body = public_https._https(public_https.RESOLVER_HOST,
                    bootstrap, '/resolve?' + urlencode({'name': host, 'type': 'A'}),
                    {'Accept': 'application/dns-json'},
                    min(deadline, time.monotonic() + 6), public_https.DNS_READ_LIMIT)
                if status != 200:
                    continue
                addresses, ttl = public_https.parse_dns_answer(host, json.loads(body))
                self._cache[host] = (time.monotonic() + ttl, addresses)
                return addresses
            except (OSError, http.client.HTTPException, ValueError, TypeError, KeyError, AttributeError):
                if time.monotonic() >= deadline:
                    break
        raise OfficialSourceError('official_source_dns_unavailable', 502, retryable=True)

    def get(self, url, *, deadline, limit=MAX_RESPONSE_BYTES):
        canonical = _url(url, github_api=True)
        parsed = urlsplit(canonical)
        if time.monotonic() >= deadline:
            raise OfficialSourceError('official_source_timeout', 504, retryable=True)
        # The complete answer set is checked before selecting a literal address.
        # _https dials that literal and keeps the original TLS SNI and Host.
        addresses = self.resolve(parsed.hostname, deadline)
        for address in addresses[:2]:
            try:
                status, headers, body = public_https._https(parsed.hostname,
                    public_https.public_ipv4(address), parsed.path + ('?' + parsed.query if parsed.query else ''),
                    {'Accept': 'application/json, text/html, text/plain, text/markdown;q=0.9',
                     'User-Agent': 'PanghuResearchDocs/1.0'}, deadline, limit)
                return SourceResponse(canonical, status,
                    {key.lower(): value for key, value in headers.items()}, body)
            except (TimeoutError, OSError, http.client.HTTPException):
                if time.monotonic() >= deadline:
                    raise OfficialSourceError('official_source_timeout', 504, retryable=True) from None
            except ValueError as exc:
                message = str(exc)
                code = ('official_source_redirect_not_allowed' if 'redirects' in message else
                    'official_source_byte_limit' if 'byte limit' in message else
                    'official_source_encoding_unsupported' if 'compressed' in message else
                    'official_source_unavailable')
                raise OfficialSourceError(code) from None
        raise OfficialSourceError('official_source_unavailable', 502, retryable=True)


def _response(client, url, deadline):
    response = client.get(url, deadline=deadline, limit=MAX_RESPONSE_BYTES)
    # Even injected/internal transports must return bounded bytes and cannot
    # silently change targets. The production transport never follows redirects.
    if response.url != _url(url, github_api=True):
        raise OfficialSourceError('official_source_redirect_not_allowed')
    if not isinstance(response.body, bytes) or len(response.body) > MAX_RESPONSE_BYTES:
        raise OfficialSourceError('official_source_byte_limit', 413)
    if response.status != 200:
        if response.status in (301, 302, 303, 307, 308):
            raise OfficialSourceError('official_source_redirect_not_allowed')
        if response.status == 404:
            raise OfficialSourceError('official_source_not_found', 404)
        if response.status == 429:
            raise OfficialSourceError('official_source_rate_limited', 429, retryable=True)
        raise OfficialSourceError('official_source_unavailable', 502, retryable=response.status >= 500)
    if response.headers.get('content-encoding', 'identity').lower() != 'identity':
        raise OfficialSourceError('official_source_encoding_unsupported')
    return response


def _json(client, url, deadline):
    response = _response(client, url, deadline)
    try:
        value = json.loads(response.body)
    except (ValueError, UnicodeError):
        raise OfficialSourceError('official_source_invalid_response', 502) from None
    if not isinstance(value, dict):
        raise OfficialSourceError('official_source_invalid_response', 502)
    return value


def _github_target(url):
    parsed = urlsplit(url)
    if parsed.hostname != 'github.com':
        return None
    parts = unquote(parsed.path).strip('/').split('/')
    if (len(parts) < 2 or not all(_GITHUB_NAME.fullmatch(part) for part in parts[:2])
            or any(part in ('', '.', '..') for part in parts)):
        raise OfficialSourceError('official_source_github_url_unsupported')
    owner, repo = parts[:2]
    if repo.endswith('.git'):
        repo = repo[:-4]
    if not repo:
        raise OfficialSourceError('official_source_github_url_unsupported')
    if len(parts) == 2:
        return owner + '/' + repo, None, None
    if len(parts) >= 4 and parts[2] in ('blob', 'tree'):
        ref = parts[3]
        path = '/'.join(parts[4:])
        if len(ref) > 100 or (parts[2] == 'blob' and not path) or (parts[2] == 'tree' and path):
            raise OfficialSourceError('official_source_github_url_unsupported')
        return owner + '/' + repo, ref, path or None
    raise OfficialSourceError('official_source_github_url_unsupported')


def _github_file(value, *, expected_path=None):
    try:
        path, raw_sha = value['path'], value['sha']
        if (value.get('type') != 'file' or value.get('encoding') != 'base64'
                or not isinstance(path, str) or not 0 < len(path) <= 500
                or path.startswith('/') or '\\' in path
                or any(part in ('', '.', '..') for part in path.split('/'))
                or any(ord(char) < 32 or ord(char) == 127 for char in path)
                or (expected_path is not None and path != expected_path)
                or not isinstance(raw_sha, str) or not _SHA.fullmatch(raw_sha)
                or type(value.get('size')) is not int
                or not 0 < value['size'] <= MAX_GITHUB_FILE_BYTES):
            raise ValueError()
        suffix = '.' + path.rsplit('.', 1)[-1].lower() if '.' in path.rsplit('/', 1)[-1] else ''
        if suffix not in _TEXT_SUFFIXES and path.rsplit('/', 1)[-1].lower() not in {'readme', 'license', 'dockerfile', 'makefile'}:
            raise OfficialSourceError('official_source_file_type_unsupported')
        encoded = value['content']
        if not isinstance(encoded, str):
            raise ValueError()
        raw = base64.b64decode(''.join(encoded.split()), validate=True)
        if len(raw) != value['size'] or len(raw) > MAX_GITHUB_FILE_BYTES:
            raise ValueError()
        if hashlib.sha1(b'blob ' + str(len(raw)).encode('ascii') + b'\0' + raw).hexdigest() != raw_sha:
            raise ValueError()
        text = raw.decode('utf-8-sig')
        if not text.strip() or '\0' in text or any(ord(c) < 32 and c not in '\n\r\t' for c in text):
            raise ValueError()
        return dict(path=path, text=text, bytes=len(raw), blob_sha=raw_sha,
                    content_sha256=hashlib.sha256(raw).hexdigest())
    except OfficialSourceError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error):
        raise OfficialSourceError('official_source_invalid_file', 502) from None


class _Document(HTMLParser):
    _ignored = {'script', 'style', 'nav', 'footer', 'header', 'noscript', 'svg',
                'canvas', 'form', 'iframe', 'button', 'aside', 'template'}
    _blocks = {'p', 'div', 'main', 'article', 'section', 'li', 'h1', 'h2', 'h3',
               'h4', 'h5', 'h6', 'pre', 'blockquote', 'tr', 'td', 'th', 'br', 'hr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.all, self.main, self.title = [], [], []
        self.ignored, self.primary, self.in_title = [], [], False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self._ignored:
            self.ignored.append(tag)
        if tag in ('main', 'article') and not self.ignored:
            self.primary.append(tag)
        if tag == 'title':
            self.in_title = True
        if tag in self._blocks and not self.ignored:
            self.all.append('\n')
            if self.primary:
                self.main.append('\n')

    def handle_endtag(self, tag):
        if tag in self.ignored:
            index = len(self.ignored) - 1 - self.ignored[::-1].index(tag)
            del self.ignored[index:]
        if tag in self.primary:
            index = len(self.primary) - 1 - self.primary[::-1].index(tag)
            del self.primary[index:]
        if tag == 'title':
            self.in_title = False
        if tag in self._blocks and not self.ignored:
            self.all.append('\n')
            if self.primary:
                self.main.append('\n')

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        elif not self.ignored:
            self.all.append(data)
            if self.primary:
                self.main.append(data)

    def document(self):
        selected = self.main if ''.join(self.main).strip() else self.all
        lines = [line.rstrip() for line in ''.join(selected).splitlines()]
        return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


def _web_document(response):
    content_type = response.headers.get('content-type', '').split(';', 1)[0].strip().lower()
    if content_type not in {'text/html', 'application/xhtml+xml', 'text/plain',
                            'text/markdown', 'text/x-markdown', 'application/toml'}:
        raise OfficialSourceError('official_source_content_type_unsupported')
    try:
        decoded = response.body.decode('utf-8-sig')
    except UnicodeError:
        raise OfficialSourceError('official_source_utf8_required') from None
    if content_type in {'text/html', 'application/xhtml+xml'}:
        parser = _Document()
        try:
            parser.feed(decoded)
            parser.close()
        except (ValueError, RecursionError):
            raise OfficialSourceError('official_source_invalid_response', 502) from None
        title, text = ' '.join(''.join(parser.title).split()), parser.document()
    else:
        title, text = '', decoded.strip()
    if not text or '\0' in text or any(ord(c) < 32 and c not in '\n\r\t' for c in text):
        raise OfficialSourceError('official_source_no_readable_text')
    return title[:500] or urlsplit(response.url).hostname, text


def _material_text(title, metadata, documents, target):
    # Keep provenance in the actual frozen material, before content. It records
    # the acquisition scope without claiming repository execution or paper text.
    lines = ['# ' + title, '', 'Source: ' + metadata['final_url'],
        'Retrieved at (UTC): ' + metadata['fetched_at'],
        'Access: engineering document body only; linked sources were not fetched.',
        'Authority: URL selected by the user; official status is not independently verified.',
        'Version: ' + (metadata['version'] or 'not recorded'), '']
    declarations = []
    for document in documents:
        lines.extend(['## ' + document['path'], ''])
        start = len(lines) + 1
        body_lines = document['text'].rstrip().splitlines()
        lines.extend(body_lines)
        end = len(lines)
        lines.append('')
        identity = ('github:' + target[0] + ':' + document['path']) if target else metadata['final_url']
        declarations.append(dict(title=document['path'][:500],
            source_id='url:' + hashlib.sha256(identity.encode('utf-8')).hexdigest(),
            access_level='full_text', source_type='engineering_document',
            start_line=start, end_line=end, declaration_basis='fetched_document',
            excerpt_sha256=hashlib.sha256('\n'.join(body_lines).encode('utf-8')).hexdigest()))
    return ('\n'.join(lines).rstrip() + '\n').encode('utf-8'), declarations


def fetch_official_material(url, *, material_id=None, transport=None):
    """Return one ready material, or a safe error with no claimed acquisition.

    ``retrieval`` retains measured acquisition and GitHub commit/file metadata.
    ``text`` includes that identity because frozen workflow material schemas do
    not carry arbitrary metadata. This is an engineering document, not a new
    independent paper or a machine verification of the website's authority.
    """
    canonical = _url(url)
    client, deadline = transport or PublicSourceTransport(), time.monotonic() + MAX_IMPORT_SECONDS
    target = _github_target(canonical)
    metadata = dict(input_url=url, final_url=canonical, requested_at=utc_now(),
        access_scope='fetched_document_body', authority='user_selected_url',
        source_type='engineering_document', redirects_followed=False,
        version=None, github=None, gaps=[])
    documents = []
    if target:
        repository, ref, path = target
        base = 'https://api.github.com/repos/' + repository
        if ref is None:
            repo = _json(client, base, deadline)
            ref = repo.get('default_branch')
            if (not isinstance(ref, str) or not 0 < len(ref) <= 200
                    or any(ord(char) < 32 for char in ref)):
                raise OfficialSourceError('official_source_invalid_response', 502)
        commit_info = _json(client, base + '/commits/' + quote(ref, safe=''), deadline)
        commit = commit_info.get('sha')
        if not isinstance(commit, str) or not _SHA.fullmatch(commit):
            raise OfficialSourceError('official_source_invalid_response', 502)
        if path:
            document = _github_file(_json(client, base + '/contents/' + quote(path, safe='/')
                + '?ref=' + commit, deadline), expected_path=path)
            documents.append(document)
        else:
            documents.append(_github_file(_json(client, base + '/readme?ref=' + commit, deadline)))
            try:
                documents.append(_github_file(_json(client, base + '/contents/pyproject.toml?ref='
                    + commit, deadline), expected_path='pyproject.toml'))
            except OfficialSourceError as exc:
                # README remains useful if this optional file is absent or unreadable.
                metadata['gaps'].append(dict(path='pyproject.toml', code=exc.code))
        final = 'https://github.com/' + repository + '/tree/' + commit
        if path:
            final = 'https://github.com/' + repository + '/blob/' + commit + '/' + quote(path, safe='/')
        metadata.update(final_url=final, version=commit,
            github=dict(repository=repository, requested_ref=ref, commit=commit,
                files=[{key: value for key, value in item.items() if key != 'text'} for item in documents]))
        title = repository + ('/' + path if path else ' documentation')
    else:
        response = _response(client, canonical, deadline)
        title, text = _web_document(response)
        metadata.update(content_type=response.headers.get('content-type', ''),
            content_sha256=hashlib.sha256(response.body).hexdigest())
        documents.append(dict(path=canonical, text=text))
    metadata['fetched_at'] = utc_now()
    raw, declarations = _material_text(title, metadata, documents, target)
    if len(raw) > MAX_TEXT_BYTES and target and not target[2] and len(documents) > 1:
        # A large optional file cannot make the already obtained README unusable.
        omitted = documents.pop()
        metadata['github']['files'].pop()
        metadata['gaps'].append(dict(path=omitted['path'], code='official_source_text_limit'))
        raw, declarations = _material_text(title, metadata, documents, target)
    if len(raw) > MAX_TEXT_BYTES:
        raise OfficialSourceError('official_source_text_limit', 413)
    safe_name = re.sub(r'[^A-Za-z0-9_.-]+', '-', title).strip('-')[:140] or 'official-document'
    material = parse_material(safe_name + '.md', raw, material_id=material_id)
    if material['status'] != 'ready':
        raise OfficialSourceError('official_source_no_readable_text')
    metadata['extracted_sha256'] = material['text_sha256']
    material.update(retrieval=metadata, material_role='research_source', declared_sources=declarations)
    return material
