"""Same-origin scientific UI gateway behind the existing OAuth2 proxy.

Only the OAuth proxy may reach this Pod (see workbench deployment policy). It
overwrites X-Forwarded-User. Run uvicorn with --no-proxy-headers: browser-supplied
forwarded address/identity headers are never relayed to the internal API.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import secrets
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request as UpstreamRequest, build_opener, ProxyHandler, HTTPRedirectHandler

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

COOKIE = '__Host-research_csrf'
MAX_BODY = 12 * 1024 * 1024
MAX_RESPONSE = 24 * 1024 * 1024
TOKEN = re.compile(r'[A-Za-z0-9_-]{43}\Z')
READ_ROUTES = ('/scientific-reports', '/scientific-report-versions', '/scientific-download')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def authenticated_user(request):
    value = request.headers.get('x-forwarded-user', '').strip()
    if not value:
        raise HTTPException(401, detail={'code': 'authentication_required'})
    if len(value) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise HTTPException(401, detail={'code': 'invalid_identity'})
    return value


def checked_url(value, *, origin=False):
    parsed = urlsplit(value)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment
            or (origin and parsed.path) or (not origin and parsed.path not in ('', '/'))):
        raise ValueError('Invalid scientific gateway configuration')
    return value.rstrip('/')


def create_app(*, api_base=None, public_origin=None, static_root=None, transport=None):
    api_base = checked_url(api_base or os.getenv('API_BASE', 'http://api.scientific-agent.svc.cluster.local'))
    public_origin = checked_url(public_origin or os.getenv('SCIENTIFIC_PUBLIC_ORIGIN', 'https://scientific-agent.panghuer.top'), origin=True)
    static_root = Path(static_root or os.getenv('SCIENTIFIC_WEB_ROOT', Path(__file__).parent / 'web' / 'dist')).resolve()
    opener = build_opener(ProxyHandler({}), NoRedirect())
    app = FastAPI(title='Scientific workbench gateway', docs_url=None, redoc_url=None, openapi_url=None)

    def forward(method, path, query, headers, body):
        target = api_base + path + ('?' + query if query else '')
        request = UpstreamRequest(target, data=body if method not in {'GET', 'HEAD'} else None, headers=headers, method=method)
        try:
            try:
                result = opener.open(request, timeout=40)
            except HTTPError as error:
                result = error
            with result:
                content = result.read(MAX_RESPONSE + 1)
                if len(content) > MAX_RESPONSE:
                    raise ValueError('response limit')
                selected = {name: result.headers[name] for name in ('Content-Type', 'Content-Disposition', 'Retry-After') if name in result.headers}
                status = result.status if hasattr(result, 'status') else result.code
                if 300 <= status < 400:
                    raise ValueError('redirect forbidden')
                return status, selected, content
        except (OSError, URLError, ValueError):
            return 502, {'Content-Type': 'application/json'}, b'{"detail":{"code":"research_api_unavailable"}}'

    @app.middleware('http')
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers['Cache-Control'] = 'no-store' if request.url.path.startswith(('/api/', '/scientific-')) else 'no-cache'
        return response

    @app.get('/healthz')
    def health():
        ready = (static_root / 'index.html').is_file()
        return JSONResponse({'ok': ready, 'frontend_ready': ready}, status_code=200 if ready else 503)

    @app.get('/api/v1/research/me')
    def me(request: Request):
        user = authenticated_user(request)
        token = request.cookies.get(COOKIE, '')
        if not TOKEN.fullmatch(token):
            token = secrets.token_urlsafe(32)
        response = JSONResponse({'user_id': user, 'csrf_token': token})
        response.set_cookie(COOKIE, token, secure=True, httponly=True, samesite='strict', path='/')
        return response

    @app.api_route('/{path:path}', methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE'])
    async def route(path: str, request: Request):
        full_path = '/' + path
        workspace = full_path == '/api/v1/research' or full_path.startswith('/api/v1/research/')
        legacy = any(full_path == prefix or full_path.startswith(prefix + '/') for prefix in READ_ROUTES)
        if workspace or legacy:
            user = authenticated_user(request)
            if legacy and request.method not in {'GET', 'HEAD'}:
                raise HTTPException(405)
            if request.method not in {'GET', 'HEAD'}:
                token = request.cookies.get(COOKIE, '')
                header = request.headers.get('x-csrf-token', '')
                if (request.headers.get('origin') != public_origin or not TOKEN.fullmatch(token) or not TOKEN.fullmatch(header)
                        or not secrets.compare_digest(token, header)):
                    raise HTTPException(403, detail={'code': 'csrf_rejected'})
            try:
                length = int(request.headers.get('content-length', '0'))
            except ValueError:
                raise HTTPException(400, detail={'code': 'invalid_content_length'})
            if length < 0 or length > MAX_BODY:
                raise HTTPException(413, detail={'code': 'upload_too_large'})
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > MAX_BODY:
                    raise HTTPException(413, detail={'code': 'upload_too_large'})
            # Explicit allowlist, never pass Authorization, Cookie, browser
            # forwarding headers or model configuration to the internal API.
            headers = {'X-Forwarded-User': user}
            for name in ('Content-Type', 'Idempotency-Key', 'Accept'):
                if request.headers.get(name):
                    headers[name] = request.headers[name]
            sender = transport or forward
            status, selected, content = await run_in_threadpool(sender, request.method, full_path, request.url.query, headers, bytes(data))
            return Response(content=content, status_code=status, headers=selected)
        if full_path.startswith(('/api/', '/scientific-')) or request.method not in {'GET', 'HEAD'}:
            raise HTTPException(404)
        target = (static_root / path).resolve()
        if not target.is_relative_to(static_root):
            raise HTTPException(404)
        if not target.is_file():
            if Path(path).suffix:
                raise HTTPException(404)
            target = static_root / 'index.html'
        if not target.is_file():
            raise HTTPException(503, detail={'code': 'frontend_not_built'})
        return FileResponse(target)

    return app


app = create_app()
