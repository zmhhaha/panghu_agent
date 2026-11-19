"""Bounded, on-demand video observations without storing a reference video.

Only this trusted host adapter has network access.  Decoders receive an
unpredictable loopback Range URL; every real HTTPS connection is checked and
pinned to a public IP, including redirects and provider-resolved media URLs.
Provider metadata identity is deliberately not a complete-media SHA-256.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from http import client as http_client
from http import cookiejar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import ipaddress
import json
import math
from pathlib import Path
import re
import secrets
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from agent_service.contracts import ServiceError, digest


SOURCE_SCHEMA = 'video.remote-reference-source.v1'
MAX_DURATION = 6 * 60 * 60
MAX_DETAIL_BYTES = 2 * 1024 * 1024
MAX_OVERVIEW_BYTES = 96 * 1024
DEFAULT_FETCH_BYTES = 128 * 1024 * 1024
MAX_REQUESTS = 128
_RANGE = re.compile(r'bytes=(?:\d+-\d*|-\d+)\Z')
_BILI_PATH = re.compile(r'/video/(?P<identity>BV[A-Za-z0-9]{10}|av[1-9][0-9]{0,15})/?\Z')
_SAFE_HEADERS = frozenset({'accept', 'accept-language', 'user-agent', 'referer', 'origin', 'range'})
_IP_TRANSITIONS = tuple(ipaddress.ip_network(value) for value in (
    '64:ff9b::/96', '64:ff9b:1::/48', '2002::/16', '2001::/32',
))
_LIMITATIONS = [
    'Only the supplied sampled pixels were observed; continuous motion and audio remain unverified.',
    'Source identity is provider metadata, not a digest of the complete video.',
    'Unobserved source bytes may change without a detectable metadata change.',
]


def _require(value, code='remote_reference_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _url(value):
    _require(type(value) is str and 1 <= len(value) <= 4096
        and not any(ord(c) <= 32 or ord(c) == 127 for c in value)
        and '\\' not in value, 'remote_reference_url_invalid')
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or '').encode('idna').decode('ascii').lower().rstrip('.')
        port = parsed.port
    except (ValueError, UnicodeError):
        raise ServiceError('remote_reference_url_invalid', 422) from None
    _require(parsed.scheme == 'https' and host and port in (None, 443)
        and parsed.username is None and parsed.password is None and '%' not in host
        and len(host) <= 253, 'remote_reference_url_invalid')
    _require(not parsed.fragment, 'remote_reference_url_invalid')
    return urlunsplit(('https', '[' + host + ']' if ':' in host else host,
        parsed.path or '/', parsed.query, ''))


def normalize_source_url(value):
    """Keep an original source locator; strip Bilibili tracking parameters."""
    value = _url(value)
    parsed = urlsplit(value)
    if parsed.hostname in {'www.bilibili.com', 'bilibili.com', 'm.bilibili.com'}:
        match = _BILI_PATH.fullmatch(parsed.path)
        _require(match is not None, 'remote_reference_provider_unsupported')
        values = parse_qs(parsed.query, keep_blank_values=True).get('p', ['1'])
        _require(len(values) == 1 and re.fullmatch(r'[1-9][0-9]{0,3}', values[0])
            and int(values[0]) <= 1000, 'remote_reference_page_invalid')
        return 'https://www.bilibili.com/video/' + match['identity'] + '/?' + urlencode({'p': int(values[0])})
    _require(parsed.path.lower().endswith(('.mp4', '.webm', '.mov', '.m4v')),
        'remote_reference_provider_unsupported')
    # Expiring provider playback URLs are not a durable source registration.
    _require(not any(key.lower() in {'access_token', 'authorization', 'credential', 'signature', 'token'}
        for key in parse_qs(parsed.query)), 'remote_reference_source_credentials_forbidden')
    return value


def validate_crop(value):
    if value is None:
        return None
    _require(type(value) is dict and set(value) == {'x', 'y', 'width', 'height'}
        and all(_finite(item) for item in value.values()), 'remote_reference_crop_invalid')
    x, y, width, height = (value[key] for key in ('x', 'y', 'width', 'height'))
    _require(0 <= x < 1 and 0 <= y < 1 and 0 < width <= 1 and 0 < height <= 1
        and x + width <= 1 + 1e-12 and y + height <= 1 + 1e-12, 'remote_reference_crop_invalid')
    return dict(x=x, y=y, width=width, height=height)


class _Budget:
    def __init__(self, deadline_at=None, max_bytes=DEFAULT_FETCH_BYTES, max_requests=MAX_REQUESTS):
        self.deadline_at = time.time() + 120 if deadline_at is None else deadline_at
        _require(_finite(self.deadline_at) and self.deadline_at > time.time(),
            'remote_reference_deadline_exhausted', 409)
        self.max_bytes, self.max_requests = max_bytes, max_requests
        self.bytes = self.requests = 0
        self.lock = threading.Lock()

    def remaining(self):
        value = self.deadline_at - time.time()
        _require(value > 0, 'remote_reference_deadline_exhausted', 409)
        return value

    def begin(self):
        self.remaining()
        with self.lock:
            self.requests += 1
            _require(self.requests <= self.max_requests, 'remote_reference_request_limit', 413)

    def consume(self, size):
        self.remaining()
        with self.lock:
            self.bytes += size
            _require(self.bytes <= self.max_bytes, 'remote_reference_byte_limit', 413)


class _Response:
    def __init__(self, connection, response, url, budget):
        self.connection, self.response, self.url, self.budget = connection, response, url, budget
        self.status, self.headers = response.status, response.headers
        self.socket = connection.sock or getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)

    def read(self, size=65536):
        remaining = self.budget.remaining()
        if self.response.isclosed():
            return b''
        try:
            if self.socket is not None:
                self.socket.settimeout(min(20, remaining))
            # One socket read prevents a slowly trickling response from keeping
            # a single read(size) alive past the overall deadline.
            raw = self.response.read1(size)
        except (OSError, http_client.HTTPException):
            raise ServiceError('remote_reference_fetch_failed', 502) from None
        self.budget.consume(len(raw))
        return raw

    def close(self):
        try:
            if self.socket:
                self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.response.close()
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class PublicHTTPS:
    """A small HTTPS-only transport with DNS pinning and no ambient proxies."""
    def __init__(self, *, resolver=socket.getaddrinfo, connector=socket.create_connection,
                 context=None, cookie_file=None):
        self.resolver, self.connector = resolver, connector
        self.context = context or ssl.create_default_context()
        self.cookies = None
        if cookie_file is not None:
            # Configured host-owned file only, never a request/prompt parameter.
            self.cookies = cookiejar.MozillaCookieJar(str(cookie_file))
            try:
                self.cookies.load(ignore_discard=True, ignore_expires=False)
            except (OSError, cookiejar.LoadError):
                raise ServiceError('remote_reference_cookie_config_invalid', 503) from None

    def _addresses(self, host, budget=None):
        try:
            answers = (self.resolver.resolve(host, budget=budget) if hasattr(self.resolver, 'resolve')
                else self.resolver(host, 443, type=socket.SOCK_STREAM))
            addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
            _require(bool(addresses), 'remote_reference_dns_failed', 502)
            for address in addresses:
                ip = ipaddress.ip_address(address)
                _require(ip.is_global and not ip.is_multicast and not ip.is_unspecified
                    and not ip.is_reserved and not ip.is_loopback and not ip.is_link_local
                    and '%' not in address and (getattr(ip, 'ipv4_mapped', None) is None)
                    and not any(ip.version == network.version and ip in network for network in _IP_TRANSITIONS),
                    'remote_reference_destination_rejected', 403)
            return addresses
        except (OSError, ValueError):
            raise ServiceError('remote_reference_dns_failed', 502) from None

    def _connect(self, host, budget):
        addresses = self._addresses(host, budget)
        last = None
        for address in addresses[:4]:
            raw = None
            try:
                raw = self.connector((address, 443), timeout=min(20, budget.remaining()))
                _require(ipaddress.ip_address(raw.getpeername()[0]) == ipaddress.ip_address(address),
                    'remote_reference_peer_mismatch', 403)
                secure = self.context.wrap_socket(raw, server_hostname=host)
                connection = http_client.HTTPSConnection(host, timeout=min(20, budget.remaining()), context=self.context)
                connection.sock = secure
                return connection
            except (OSError, ssl.SSLError) as error:
                last = error
                if raw is not None:
                    raw.close()
            except ServiceError:
                if raw is not None:
                    raw.close()
                raise
        if last is not None:
            raise ServiceError('remote_reference_fetch_failed', 502) from None
        raise ServiceError('remote_reference_fetch_failed', 502)

    def open(self, url, *, budget, method='GET', headers=None):
        _require(method in {'GET', 'HEAD'}, 'remote_reference_method_rejected')
        base_headers = {str(k): str(v) for k, v in (headers or {}).items()
            if str(k).lower() in _SAFE_HEADERS}
        _require(not any(any(ord(c) < 32 or ord(c) > 126 for c in value)
            for value in base_headers.values()),
            'remote_reference_headers_invalid')
        current = _url(url)
        for redirect in range(5):
            budget.begin()
            parsed = urlsplit(current)
            connection = self._connect(parsed.hostname, budget)
            request_headers = dict(base_headers, **{'Accept-Encoding': 'identity', 'Connection': 'close'})
            request_headers.setdefault('User-Agent', 'Mozilla/5.0 PanghuReferenceReader/1.0')
            if self.cookies is not None and parsed.hostname in {'api.bilibili.com', 'www.bilibili.com'}:
                from urllib.request import Request
                cookie_request = Request(current)
                self.cookies.add_cookie_header(cookie_request)
                cookie = cookie_request.get_header('Cookie')
                if cookie:
                    request_headers['Cookie'] = cookie
            try:
                connection.request(method, urlunsplit(('', '', parsed.path or '/', parsed.query, '')),
                    headers=request_headers)
                response = connection.getresponse()
            except (OSError, http_client.HTTPException):
                connection.close()
                raise ServiceError('remote_reference_fetch_failed', 502) from None
            result = _Response(connection, response, current, budget)
            if response.status not in {301, 302, 303, 307, 308}:
                return result
            location = response.headers.get('Location')
            result.close()
            _require(location and redirect < 4, 'remote_reference_redirect_rejected', 502)
            current = _url(urljoin(current, location))
            # Headers are not credentials. Cookies are selected afresh per origin.
        raise ServiceError('remote_reference_redirect_rejected', 502)


class PublicDNSResolver:
    """Opt-in genuine DNS for hosts whose system resolver uses Fake-IP.

    The DoH endpoint and bootstrap addresses are operator-selected constants,
    never source/prompt parameters.  Its transport has the same public-IP and
    TLS checks as the media transport and never uses ambient proxy settings.
    """
    PROVIDERS = {
        'alidns': ('dns.alidns.com', '/resolve', ('223.5.5.5', '223.6.6.6')),
        'cloudflare': ('cloudflare-dns.com', '/dns-query', ('1.1.1.1', '1.0.0.1')),
        'google': ('dns.google', '/resolve', ('8.8.8.8', '8.8.4.4')),
    }

    def __init__(self, provider='alidns', *, transport=None):
        _require(provider in self.PROVIDERS, 'remote_reference_dns_provider_invalid')
        self.provider, self.endpoint = provider, self.PROVIDERS[provider]
        endpoint_host, _, addresses = self.endpoint

        def bootstrap(host, port, **kwargs):
            _require(host == endpoint_host and port == 443, 'remote_reference_dns_endpoint_rejected', 403)
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (address, 443))
                for address in addresses]

        self.transport = transport or PublicHTTPS(resolver=bootstrap)
        self.cache, self.lock = {}, threading.Lock()

    @staticmethod
    def _domain(value):
        _require(type(value) is str and len(value) <= 254, 'remote_reference_dns_response_invalid', 502)
        try:
            value = value.rstrip('.').encode('idna').decode('ascii').lower()
        except UnicodeError:
            raise ServiceError('remote_reference_dns_response_invalid', 502) from None
        _require(bool(value) and all(re.fullmatch(r'[a-z0-9_-]{1,63}', label)
            for label in value.split('.')), 'remote_reference_dns_response_invalid', 502)
        return value

    def _query(self, host, kind, budget):
        endpoint_host, path, _ = self.endpoint
        url = 'https://' + endpoint_host + path + '?' + urlencode({'name': host, 'type': kind})
        with self.transport.open(url, budget=budget, headers={'Accept': 'application/dns-json'}) as response:
            _require(response.status == 200, 'remote_reference_dns_failed', 502)
            chunks, size = [], 0
            while True:
                raw = response.read(min(8192, 65537 - size))
                if not raw:
                    break
                chunks.append(raw)
                size += len(raw)
                _require(size <= 65536, 'remote_reference_dns_response_invalid', 502)
        try:
            document = json.loads(b''.join(chunks))
        except (ValueError, UnicodeError):
            raise ServiceError('remote_reference_dns_response_invalid', 502) from None
        _require(type(document) is dict and document.get('Status') == 0,
            'remote_reference_dns_failed', 502)
        rows = document.get('Answer', [])
        _require(type(rows) is list and len(rows) <= 64 and all(type(row) is dict for row in rows),
            'remote_reference_dns_response_invalid', 502)
        aliases = {host}
        for _ in range(8):
            before = len(aliases)
            for row in rows:
                if row.get('type') == 5 and self._domain(row.get('name')) in aliases:
                    aliases.add(self._domain(row.get('data')))
            if len(aliases) == before:
                break
        _require(len(aliases) <= 9, 'remote_reference_dns_response_invalid', 502)
        addresses, ttl = [], 60
        expected = 1 if kind == 'A' else 28
        for row in rows:
            if row.get('type') != expected or self._domain(row.get('name')) not in aliases:
                continue
            try:
                address = str(ipaddress.ip_address(row['data']))
                row_ttl = int(row.get('TTL', 0))
            except (ValueError, KeyError, TypeError):
                raise ServiceError('remote_reference_dns_response_invalid', 502) from None
            _require(ipaddress.ip_address(address).version == (4 if kind == 'A' else 6),
                'remote_reference_dns_response_invalid', 502)
            addresses.append(address)
            ttl = min(ttl, max(0, row_ttl))
        return list(dict.fromkeys(addresses)), ttl

    def resolve(self, host, *, budget=None):
        budget = budget or _Budget(time.time() + 15, max_bytes=256 * 1024, max_requests=8)
        budget.remaining()
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            addresses = [str(literal)]
        else:
            host = self._domain(host)
            with self.lock:
                cached = self.cache.get(host)
            if cached is not None and cached[0] > time.monotonic():
                addresses = list(cached[1])
            else:
                addresses, ttl = self._query(host, 'A', budget)
                if not addresses:
                    addresses, ttl = self._query(host, 'AAAA', budget)
                _require(bool(addresses), 'remote_reference_dns_failed', 502)
                with self.lock:
                    if len(self.cache) >= 128:
                        self.cache.clear()
                    self.cache[host] = (time.monotonic() + ttl, tuple(addresses))
        return [(socket.AF_INET6 if ':' in address else socket.AF_INET, socket.SOCK_STREAM,
            socket.IPPROTO_TCP, '', (address, 443)) for address in addresses]


class _RangeProxy:
    """The decoder can read only one fixed media object through this proxy."""
    def __init__(self, transport, media_url, budget, headers=None):
        self.transport, self.media_url, self.budget, self.headers = transport, media_url, budget, headers or {}
        self.error, self.active, self.lock = None, set(), threading.Lock()
        self.token = secrets.token_hex(24)
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_HEAD(self):
                self.serve('HEAD')

            def do_GET(self):
                self.serve('GET')

            def serve(self, method):
                if self.path != '/' + outer.token + '/media':
                    self.send_error(404)
                    return
                value = self.headers.get('Range')
                if value is not None and not _RANGE.fullmatch(value):
                    self.send_error(416)
                    return
                headers = dict(outer.headers)
                if value:
                    headers['Range'] = value
                response = None
                try:
                    response = outer.transport.open(outer.media_url, budget=outer.budget, method=method, headers=headers)
                    with outer.lock:
                        outer.active.add(response)
                    _require(response.status in {200, 206, 416}, 'remote_reference_media_unavailable', 502)
                    if value and value.startswith('bytes=') and not value.startswith('bytes=0-'):
                        _require(response.status in {206, 416}, 'remote_reference_range_unsupported', 422)
                    self.send_response(response.status)
                    for key in ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges'):
                        item = response.headers.get(key)
                        if item is not None:
                            self.send_header(key, item)
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    if method == 'GET':
                        while True:
                            raw = response.read(65536)
                            if not raw:
                                break
                            try:
                                self.wfile.write(raw)
                            except OSError:
                                break  # Includes Windows' normal aborted-socket variants.
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass  # Normal: a decoder stops after obtaining the needed bytes.
                except ServiceError as error:
                    outer.error = error
                    if not self.wfile.closed:
                        self.close_connection = True
                except OSError:
                    # Transport failures are coded ServiceErrors above.  An
                    # OSError here is the local decoder closing its socket.
                    pass
                finally:
                    if response is not None:
                        with outer.lock:
                            outer.active.discard(response)
                        response.close()

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            block_on_close = False

        self.server = Server(('127.0.0.1', 0), Handler)
        self.server.timeout = 1
        self.url = 'http://127.0.0.1:%d/%s/media' % (self.server.server_port, self.token)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        with self.lock:
            active = list(self.active)
        for response in active:
            response.close()
        self.server.server_close()
        self.thread.join(timeout=2)


def _checked_format(value, *, fallback_id='video'):
    _require(type(value) is dict, 'remote_reference_source_metadata_invalid', 502)
    try:
        width, height = int(value['width']), int(value['height'])
        fps = float(value.get('fps', value.get('frame_rate', 0)) or 0)
    except (KeyError, TypeError, ValueError):
        raise ServiceError('remote_reference_source_metadata_invalid', 502) from None
    _require(1 <= width <= 7680 and 1 <= height <= 4320 and width * height <= 32_000_000
        and _finite(fps) and 0 <= fps <= 240, 'remote_reference_source_metadata_invalid', 502)
    identity = str(value.get('format_id', value.get('id', fallback_id)))[:100]
    label = str(value.get('label', str(height) + 'p'))[:100]
    return dict(format_id=identity, width=width, height=height, fps=fps, label=label,
        codec=str(value.get('codec', value.get('codecs', 'unknown')))[:100])


class RemoteReferenceMedia:
    """Shared metadata/observation adapter for picture and video learning."""
    def __init__(self, root, *, ffmpeg='ffmpeg', ffprobe='ffprobe', transport=None,
                 cookie_file=None, max_fetch_bytes=DEFAULT_FETCH_BYTES):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ffmpeg, self.ffprobe = ffmpeg, ffprobe
        self.transport = transport or PublicHTTPS(cookie_file=cookie_file)
        _require(type(max_fetch_bytes) is int and 1024 * 1024 <= max_fetch_bytes <= 512 * 1024 * 1024,
            'remote_reference_byte_budget_invalid')
        self.max_fetch_bytes = max_fetch_bytes

    def _json(self, url, budget):
        with self.transport.open(url, budget=budget, headers={'Accept': 'application/json',
                'Referer': 'https://www.bilibili.com/'}) as response:
            _require(response.status not in {401, 403}, 'remote_reference_access_required', 403)
            _require(response.status == 200, 'remote_reference_source_unavailable', 502)
            chunks, size = [], 0
            while True:
                chunk = response.read(min(65536, 2 * 1024 * 1024 + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                _require(size <= 2 * 1024 * 1024, 'remote_reference_metadata_too_large', 413)
            raw = b''.join(chunks)
        try:
            document = json.loads(raw)
        except (ValueError, UnicodeError):
            raise ServiceError('remote_reference_source_metadata_invalid', 502) from None
        _require(type(document) is dict, 'remote_reference_source_metadata_invalid', 502)
        return document

    def _bilibili(self, source_url, budget):
        parsed = urlsplit(source_url)
        identity = _BILI_PATH.fullmatch(parsed.path)['identity']
        query = {'bvid': identity} if identity.startswith('BV') else {'aid': identity[2:]}
        view = self._json('https://api.bilibili.com/x/web-interface/view?' + urlencode(query), budget)
        _require(view.get('code') not in {-101, -10403}, 'remote_reference_access_required', 403)
        _require(view.get('code') == 0 and type(view.get('data')) is dict,
            'remote_reference_source_unavailable', 502)
        data = view['data']
        index = int(parse_qs(parsed.query)['p'][0])
        pages = data.get('pages', [])
        _require(type(pages) is list and 1 <= index <= len(pages), 'remote_reference_page_invalid')
        page = pages[index - 1]
        try:
            duration, cid = float(page['duration']), int(page['cid'])
            bvid = str(data['bvid'])
        except (KeyError, TypeError, ValueError):
            raise ServiceError('remote_reference_source_metadata_invalid', 502) from None
        _require(re.fullmatch(r'BV[A-Za-z0-9]{10}', bvid) and cid > 0
            and _finite(duration) and 0 < duration <= MAX_DURATION,
            'remote_reference_source_metadata_invalid', 502)
        play = self._json('https://api.bilibili.com/x/player/playurl?' + urlencode(
            dict(bvid=bvid, cid=cid, qn=127, fnval=4048, fourk=1)), budget)
        _require(play.get('code') not in {-101, -10403}, 'remote_reference_access_required', 403)
        _require(play.get('code') == 0 and type(play.get('data')) is dict,
            'remote_reference_source_unavailable', 502)
        playback = play['data']
        dash = playback.get('dash', {})
        raw_formats = dash.get('video', []) if type(dash) is dict else []
        labels = dict(zip(playback.get('accept_quality', []), playback.get('accept_description', [])))
        formats = []
        for row in raw_formats:
            if type(row) is not dict:
                continue
            normalized = dict(row, format_id=str(row.get('id', '')) + '-' + str(row.get('codecid', '')),
                label=labels.get(row.get('id'), str(row.get('height', '')) + 'p'))
            fps = str(row.get('frameRate', row.get('frame_rate', '0')))
            try:
                numerator, separator, denominator = fps.partition('/')
                normalized['fps'] = float(numerator) / (float(denominator) if separator else 1)
            except (ValueError, ZeroDivisionError):
                normalized['fps'] = 0
            info = _checked_format(normalized)
            backups = row.get('backupUrl', row.get('backup_url', []))
            candidates = [row.get('baseUrl', row.get('base_url'))] + (backups if type(backups) is list else [])
            for url in candidates[:8]:
                if type(url) is not str:
                    continue
                try:
                    parsed_url = urlsplit(url)
                    host = parsed_url.hostname or ''
                    if not any(host == suffix or host.endswith('.' + suffix)
                        for suffix in ('bilivideo.com', 'bilivideo.cn', 'bilivideo.net', 'hdslb.com', 'akamaized.net')):
                        continue
                    if parsed_url.scheme == 'http' and parsed_url.port in (None, 80):
                        url = urlunsplit(('https', host, parsed_url.path, parsed_url.query, ''))
                    url = _url(url)
                except (ServiceError, ValueError):
                    continue  # e.g. a provider P2P transport on port 8082.
                formats.append((info, url))
                break
        _require(bool(formats), 'remote_reference_playback_unsupported', 422)
        formats.sort(key=lambda pair: (pair[0]['height'], pair[0]['width'],
            pair[0]['codec'].startswith('avc'), pair[0]['fps']), reverse=True)
        selected, media_url = formats[0]
        canonical = 'https://www.bilibili.com/video/' + bvid + '/?' + urlencode({'p': index})
        descriptor = self._descriptor(canonical, provider='bilibili', source_id='bilibili:' + bvid + ':p' + str(index),
            title=str(data.get('title', bvid))[:500], duration=duration, selected=selected,
            available=[item[0] for item in formats], has_audio=bool(dash.get('audio')))
        descriptor['quality']['unsupported_format_count'] = len(raw_formats) - len(formats)
        if descriptor['quality']['unsupported_format_count']:
            descriptor['quality']['limitation'] += ' Some source formats use unsupported transports; only HTTPS/443 media is admitted.'
        return descriptor, media_url, {'Referer': canonical}

    @staticmethod
    def _descriptor(source_url, *, provider, source_id, title, duration, selected, available, has_audio):
        descriptor = dict(schema=SOURCE_SCHEMA, source_type='video-url', provider=provider,
            source_url=source_url, source_id=source_id, title=title, duration_seconds=duration,
            width=selected['width'], height=selected['height'], has_audio=bool(has_audio),
            source_identity_scope='provider_metadata', quality=dict(selected=deepcopy(selected),
                available=deepcopy(available), selection='highest_accessible',
                limitation='Only formats accessible to the configured host are listed; higher site/account tiers may exist.'))
        identity = {key: descriptor[key] for key in ('provider', 'source_id', 'duration_seconds', 'width', 'height')}
        identity['selected_format'] = selected
        descriptor['source_snapshot_sha256'] = digest(identity)
        return descriptor

    def _run(self, command, budget, proxy):
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=budget.remaining(), check=False)
        except subprocess.TimeoutExpired:
            raise ServiceError('remote_reference_deadline_exhausted', 409) from None
        except OSError:
            raise ServiceError('remote_reference_decoder_unavailable', 503) from None
        if proxy.error is not None:
            raise proxy.error
        _require(result.returncode == 0 and len(result.stderr) <= 2 * 1024 * 1024,
            'remote_reference_decode_failed', 422)
        return result

    def _probe(self, proxy, budget):
        result = self._run([self.ffprobe, '-v', 'error', '-protocol_whitelist', 'http,tcp',
            '-format_whitelist', 'mov,matroska,webm', '-rw_timeout', '20000000',
            '-probesize', '4194304', '-analyzeduration', '2000000', '-show_entries',
            'stream=codec_type,codec_name,width,height,avg_frame_rate:format=duration',
            '-of', 'json'] + self._demuxer_options(proxy) + [proxy.url], budget, proxy)
        _require(len(result.stdout) <= 65536, 'remote_reference_source_metadata_invalid', 502)
        try:
            document = json.loads(result.stdout)
            streams = document['streams']
            video = next(row for row in streams if row.get('codec_type') == 'video')
            duration = float(document['format']['duration'])
            numerator, denominator = str(video.get('avg_frame_rate', '0/1')).split('/')
            fps = float(numerator) / float(denominator)
        except (KeyError, ValueError, TypeError, ZeroDivisionError, StopIteration):
            raise ServiceError('remote_reference_source_metadata_invalid', 502) from None
        _require(_finite(duration) and 0 < duration <= MAX_DURATION,
            'remote_reference_source_metadata_invalid', 502)
        format_info = _checked_format(dict(video, format_id='direct-video', fps=fps,
            codec=video.get('codec_name', 'unknown')))
        return dict(duration=duration, selected=format_info,
            has_audio=any(row.get('codec_type') == 'audio' for row in streams))

    @staticmethod
    def _demuxer_options(proxy):
        # MOV external track/alias references can otherwise instruct a
        # decoder to open another URL.  They are disabled by default in
        # FFmpeg; make the boundary explicit for admitted MOV/MP4 sources.
        return ([] if urlsplit(proxy.media_url).path.lower().endswith('.webm') else
            ['-enable_drefs', '0', '-use_absolute_path', '0'])

    def _resolve(self, source_url, budget):
        source_url = normalize_source_url(source_url)
        if urlsplit(source_url).hostname == 'www.bilibili.com':
            return self._bilibili(source_url, budget)
        with _RangeProxy(self.transport, source_url, budget) as proxy:
            metadata = self._probe(proxy, budget)
        descriptor = self._descriptor(source_url, provider='https-media',
            source_id='https-media:' + hashlib.sha256(source_url.encode()).hexdigest(),
            title=Path(urlsplit(source_url).path).name[:500], duration=metadata['duration'],
            selected=metadata['selected'], available=[metadata['selected']], has_audio=metadata['has_audio'])
        return descriptor, source_url, {}

    def describe(self, source_url, *, deadline_at=None):
        budget = _Budget(deadline_at, self.max_fetch_bytes)
        descriptor, _, _ = self._resolve(source_url, budget)
        return descriptor

    @staticmethod
    def _image(raw, quality):
        from PIL import Image
        maximum = MAX_DETAIL_BYTES if quality == 'detail' else MAX_OVERVIEW_BYTES
        bounds = (1920, 1080) if quality == 'detail' else (640, 480)
        with Image.open(io.BytesIO(raw)) as original:
            _require(original.width * original.height <= 32_000_000,
                'remote_reference_frame_dimensions_invalid', 413)
            image = original.convert('RGB')
            image.thumbnail(bounds)
            for jpeg_quality in ((95, 90, 85, 80, 72) if quality == 'detail' else (85, 75, 60, 45)):
                output = io.BytesIO()
                image.save(output, format='JPEG', quality=jpeg_quality)
                content = output.getvalue()
                if len(content) <= maximum:
                    return content, image.width, image.height
        raise ServiceError('remote_reference_frame_too_large', 413)

    def _decode(self, proxy, budget, target, requested, source, crop, quality):
        width, height = source['width'], source['height']
        filters = ['select=gte(t\\,%s)' % requested, 'showinfo']
        if crop is not None:
            left, top = int(crop['x'] * width), int(crop['y'] * height)
            cw = max(1, min(width - left, int(crop['width'] * width)))
            ch = max(1, min(height - top, int(crop['height'] * height)))
            filters.append('crop=%d:%d:%d:%d:exact=1' % (cw, ch, left, top))
        bound = (1920, 1080) if quality == 'detail' else (640, 480)
        filters.append("scale='min(%d,iw)':'min(%d,ih)':force_original_aspect_ratio=decrease" % bound)
        result = self._run([self.ffmpeg, '-v', 'info', '-nostdin', '-protocol_whitelist', 'http,tcp',
            '-format_whitelist', 'mov,matroska,webm', '-rw_timeout', '20000000',
            '-ss', str(requested), '-copyts'] + self._demuxer_options(proxy) + ['-i', proxy.url, '-map', '0:v:0',
            '-vf', ','.join(filters), '-frames:v', '1', '-an', '-fps_mode', 'passthrough',
            '-threads', '1', str(target)], budget, proxy)
        log = result.stderr.decode('utf-8', errors='replace')
        bases = re.findall(r'^\[Parsed_showinfo_(\d+) @ ([^\]]+)\]\s+config in time_base:\s*(\d+)/(\d+)', log, re.M)
        first = re.findall(r'^\[Parsed_showinfo_(\d+) @ ([^\]]+)\]\s+n:\s*0\s+pts:\s*(-?\d+)\s+pts_time:', log, re.M)
        _require(len(bases) == len(first) == 1 and bases[0][:2] == first[0][:2]
            and target.is_file(), 'remote_reference_timestamp_unverified')
        numerator, denominator, pts = int(bases[0][2]), int(bases[0][3]), int(first[0][2])
        _require(numerator > 0 and denominator > 0 and pts >= 0,
            'remote_reference_timestamp_unverified')
        actual = pts * numerator / denominator
        _require(_finite(actual) and actual + 1e-7 >= requested and actual < source['duration_seconds'],
            'remote_reference_timestamp_unverified')
        return actual, pts, [numerator, denominator]

    def observe(self, source, *, times, crop=None, quality='overview', deadline_at=None):
        _require(type(source) is dict and source.get('schema') == SOURCE_SCHEMA
            and source.get('source_type') == 'video-url', 'remote_reference_source_invalid')
        _require(_finite(source.get('duration_seconds')) and 0 < source['duration_seconds'] <= MAX_DURATION,
            'remote_reference_source_invalid')
        _require(type(times) is list and 1 <= len(times) <= 8 and all(_finite(item)
            and 0 <= item < source.get('duration_seconds', 0) for item in times),
            'remote_reference_times_invalid')
        _require(quality in {'overview', 'detail'}, 'remote_reference_quality_invalid')
        crop = validate_crop(crop)
        budget = _Budget(deadline_at, self.max_fetch_bytes)
        current, media_url, headers = self._resolve(source.get('source_url'), budget)
        _require(current['source_snapshot_sha256'] == source.get('source_snapshot_sha256')
            and current['source_id'] == source.get('source_id'), 'remote_reference_source_changed', 409)
        frames, requests, seen = [], [], {}
        with tempfile.TemporaryDirectory(prefix='remote-observation-', dir=self.root) as directory:
            with _RangeProxy(self.transport, media_url, budget, headers) as proxy:
                # Provider metadata is not sufficient to assert actual decode dimensions.
                metadata = self._probe(proxy, budget)
                _require(metadata['selected']['width'] == current['width']
                    and metadata['selected']['height'] == current['height'],
                    'remote_reference_quality_changed', 409)
                for index, requested in enumerate(times):
                    target = Path(directory) / ('frame-%02d.png' % index)
                    actual, ticks, base = self._decode(proxy, budget, target, requested, current, crop, quality)
                    raw, width, height = self._image(target.read_bytes(), quality)
                    sha = hashlib.sha256(raw).hexdigest()
                    identity = digest(dict(source=current['source_snapshot_sha256'], pts=ticks,
                        time_base=base, crop=crop, quality=quality, sha256=sha))
                    frame_id = 'remote-' + identity[:24]
                    alias = identity in seen
                    requests.append(dict(requested_time=requested, actual_pts=actual,
                        pts_ticks=ticks, time_base=base, frame_id=frame_id, alias=alias))
                    if alias:
                        continue
                    seen[identity] = frame_id
                    frames.append(dict(frame_id=frame_id, file_name=frame_id + '.jpg', content=raw,
                        mime_type='image/jpeg', sha256=sha, width=width, height=height,
                        requested_time=requested, actual_pts=actual, pts_ticks=ticks, time_base=base,
                        crop=deepcopy(crop), quality=quality, source_snapshot_sha256=current['source_snapshot_sha256'],
                        source_identity_scope='provider_metadata'))
        return dict(source_snapshot=current, frames=frames, coverage=dict(scope='sampled_frames_only',
            source_snapshot_sha256=current['source_snapshot_sha256'], source_identity_scope='provider_metadata',
            source_width=current['width'], source_height=current['height'], quality=quality,
            requested_samples=len(times), unique_presentation_frames=len(frames),
            aliased_samples=len(times) - len(frames), requests=requests, received_bytes=budget.bytes,
            continuous_motion_verified=False, audio_verified=False, limitations=deepcopy(_LIMITATIONS)))
