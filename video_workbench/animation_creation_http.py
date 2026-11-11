"""Private creation executor transport; the developer lab never owns a runtime.

The browser uses the lab's existing authenticated/CSRF-protected endpoints. The
lab forwards its trusted principal over this separately authenticated internal
connection. GET requests only read; one bounded background pump owns advances.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler


LIMITS = dict(initial_creations=1, reviews=1, creator_revisions=1, renders=2)
PREFIX = '/api/creation-runs'
MAX_JSON = 2 * 1024 * 1024
MAX_ARTIFACT = 32 * 1024 * 1024
OWNER = re.compile(r'[A-Za-z0-9][A-Za-z0-9._@:/+-]{0,199}\Z')
RUN = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}\Z')


class CreationTransportError(ValueError):
    def __init__(self, code, status=503):
        super().__init__(code)
        self.code, self.status = code, status


def _require(value, code='animation_creation_request_invalid', status=422):
    if not value:
        raise CreationTransportError(code, status)


def _json(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            _require(key not in result, 'animation_creation_duplicate_field', 400)
            result[key] = value
        return result
    def constant(_):
        raise CreationTransportError('animation_creation_json_invalid', 400)
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def _token(value):
    _require(type(value) is str and 32 <= len(value) <= 512
             and all(33 <= ord(c) <= 126 for c in value), 'animation_creation_token_invalid')
    return value


def _owner(value):
    _require(type(value) is str and OWNER.fullmatch(value), 'animation_creation_owner_invalid', 403)
    return value


def _run(value):
    _require(type(value) is str and RUN.fullmatch(value), 'animation_creation_run_invalid')
    return value


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise CreationTransportError('animation_creation_redirect_rejected', 502)


class CreationAPIClient:
    """Single-attempt fixed-origin requests; never retries a creation POST."""
    def __init__(self, base_url, token, *, timeout=30):
        parsed = urlsplit(base_url)
        _require(type(base_url) is str and parsed.scheme in {'http', 'https'} and parsed.hostname
                 and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
                 and parsed.path in ('', '/') and '\\' not in base_url
                 and not any(ord(c) <= 32 for c in base_url), 'animation_creation_origin_invalid')
        self.base_url, self._token, self.timeout = base_url.rstrip('/'), _token(token), timeout
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    def _request(self, owner, method, path, body=None, *, artifact=False):
        headers = {'Authorization': 'Bearer ' + self._token, 'X-Animation-Creation-Owner': _owner(owner),
                   'Accept': 'application/octet-stream' if artifact else 'application/json'}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=True, allow_nan=False).encode()
            headers['Content-Type'] = 'application/json'
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                limit = MAX_ARTIFACT if artifact else MAX_JSON
                raw = response.read(limit + 1)
                _require(len(raw) <= limit, 'animation_creation_response_too_large', 502)
                if artifact:
                    return raw, response.headers.get_content_type()
                return _json(raw)
        except HTTPError as error:
            # Private responses can carry task content; never forward their raw
            # body, exception text, URLs or authentication headers to a browser.
            code = 'animation_creation_request_rejected'
            try:
                value = _json(error.read(4096))
                if type(value) is dict and re.fullmatch(r'[a-z][a-z0-9_]{0,99}', str(value.get('error', ''))):
                    code = value['error']
            except (ValueError, TypeError):
                pass
            status = error.code if error.code in (400, 403, 404, 409, 413, 422, 429, 503) else 502
            raise CreationTransportError(code, status) from None
        except (URLError, TimeoutError, OSError):
            raise CreationTransportError('animation_creation_executor_unavailable', 503) from None
        except (UnicodeError, json.JSONDecodeError):
            raise CreationTransportError('animation_creation_response_invalid', 502) from None

    def list(self, owner, experiment_id=None):
        query = '' if experiment_id is None else '?' + urlencode({'experiment_id': experiment_id})
        return self._request(owner, 'GET', PREFIX + query)

    def get(self, owner, run_id):
        return self._request(owner, 'GET', PREFIX + '/' + _run(run_id))

    def start(self, owner, body):
        return self._request(owner, 'POST', PREFIX, body)

    def cancel(self, owner, run_id):
        return self._request(owner, 'POST', PREFIX + '/' + _run(run_id) + '/cancel', {})

    def artifact(self, owner, run_id, version_id, kind):
        _require(version_id in {'original', 'revised'} and kind in {'media', 'source'})
        return self._request(owner, 'GET', PREFIX + '/' + _run(run_id) + '/versions/'
                             + quote(version_id) + '/' + kind, artifact=True)


class CreationPump:
    """One finite advance per active run per tick, with durable startup recovery."""
    def __init__(self, service, *, interval=1):
        self.service, self.interval = service, interval
        self.stopping, self.wake = threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name='animation-creation')

    def start(self):
        self.thread.start()

    def notify(self):
        self.wake.set()

    def _run(self):
        while not self.stopping.is_set():
            try:
                pending = self.service.pending(limit=100)
            except Exception:
                pending = []
            for row in pending:
                if self.stopping.is_set():
                    break
                try:
                    self.service.advance(row['owner'], row['id'])
                except Exception:
                    # A transport exception never authorizes another key or
                    # render. The durable service reconciles the same claim.
                    pass
            self.wake.wait(self.interval)
            self.wake.clear()

    def close(self):
        self.stopping.set()
        self.wake.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)


class CreationHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, service, token, *, host='127.0.0.1', port=0, pump=True, method_service=None,
                 close_callbacks=()):
        self.service, self._token = service, _token(token)
        self.pump = CreationPump(service) if pump else None
        self.method_service = method_service
        self.method_pump = CreationPump(method_service) if pump and method_service is not None else None
        self.close_callbacks = tuple(close_callbacks)
        super().__init__((host, port), CreationHandler)
        if self.pump:
            self.pump.start()
        if self.method_pump:
            self.method_pump.start()

    def server_close(self):
        if self.pump:
            self.pump.close()
        if self.method_pump:
            self.method_pump.close()
        callbacks, self.close_callbacks = self.close_callbacks, ()
        for callback in callbacks:
            callback()
        super().server_close()


class CreationHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _send(self, value, status=200, *, raw=False, mime='application/json'):
        content = value if raw else json.dumps(value, ensure_ascii=True, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; sandbox")
        self.end_headers()
        self.wfile.write(content)

    def _auth(self):
        auth = self.headers.get_all('Authorization', [])
        _require(len(auth) == 1 and auth[0].isascii()
                 and secrets.compare_digest(auth[0], 'Bearer ' + self.server._token),
                 'animation_creation_unauthorized', 403)
        owners = self.headers.get_all('X-Animation-Creation-Owner', [])
        _require(len(owners) == 1, 'animation_creation_owner_invalid', 403)
        return _owner(owners[0])

    def _route(self, method):
        parsed = urlsplit(self.path)
        if method == 'GET' and parsed.path == '/healthz' and not parsed.query:
            return self._send(dict(status='ok'))
        owner = self._auth()
        pieces = parsed.path.strip('/').split('/')
        service = self.server.service
        if method == 'GET' and parsed.path.startswith('/api/method-lab/'):
            from .animation_method_http import route_private
            _require(not parsed.query)
            if route_private(self, owner, method, parsed.path):
                return
        if method == 'GET':
            if parsed.path == PREFIX:
                query = parse_qs(parsed.query, keep_blank_values=True)
                _require(set(query) <= {'experiment_id'} and all(len(v) == 1 for v in query.values()))
                experiment = query.get('experiment_id', [None])[0]
                return self._send(dict(schema='video.animation-creation-runs.v1', enabled=True,
                    disabled_reason=None, limits=LIMITS, runs=service.list(owner, experiment)))
            _require(not parsed.query)
            if len(pieces) == 3 and pieces[:2] == ['api', 'creation-runs']:
                return self._send(service.get(owner, _run(pieces[2])))
            if len(pieces) == 6 and pieces[:2] == ['api', 'creation-runs'] and pieces[3] == 'versions':
                _require(pieces[4] in {'original', 'revised'} and pieces[5] in {'media', 'source'})
                path = Path(service.artifact(owner, _run(pieces[2]), pieces[4], pieces[5]))
                _require(path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_ARTIFACT,
                         'animation_creation_artifact_unavailable', 409)
                return self._send(path.read_bytes(), raw=True,
                    mime='video/mp4' if pieces[5] == 'media' else 'application/json')
        else:
            _require(not parsed.query)
            lengths = self.headers.get_all('Content-Length', [])
            body_limit = 1024 * 1024 if parsed.path.startswith('/api/method-lab/') else 32768
            _require(len(lengths) == 1 and lengths[0].isdigit() and 0 < int(lengths[0]) <= body_limit
                     and not self.headers.get_all('Transfer-Encoding'), 'animation_creation_body_size', 413)
            _require(self.headers.get_content_type() == 'application/json', 'animation_creation_json_required', 415)
            body = _json(self.rfile.read(int(lengths[0])))
            if parsed.path.startswith('/api/method-lab/'):
                from .animation_method_http import route_private
                if route_private(self, owner, method, parsed.path, body):
                    return
            if parsed.path == PREFIX:
                result = service.start(owner, body)
                if self.server.pump:
                    self.server.pump.notify()
                return self._send(result, 202)
            if len(pieces) == 4 and pieces[:2] == ['api', 'creation-runs'] and pieces[3] == 'cancel':
                _require(body == {})
                result = service.cancel(owner, _run(pieces[2]))
                if self.server.pump:
                    self.server.pump.notify()
                return self._send(result)
        raise CreationTransportError('animation_creation_not_found', 404)

    def _handle(self, method):
        try:
            self._route(method)
        except Exception as error:
            code, status = getattr(error, 'code', None), getattr(error, 'status', 503)
            if not isinstance(code, str) or re.fullmatch(r'[a-z][a-z0-9_]{0,99}', code) is None:
                code, status = 'animation_creation_executor_error', 503
            self._send(dict(error=code), status if status in (400, 403, 404, 409, 413, 415, 422, 429, 503) else 502)

    def do_GET(self):
        self._handle('GET')

    def do_POST(self):
        self._handle('POST')
