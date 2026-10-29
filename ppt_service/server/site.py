"""Real-auth website with explicit admission to the isolated Hermes controller."""
from __future__ import annotations
import os
import stat
from pathlib import Path
from fastapi import HTTPException, Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Match
from .app import create_app
from .auth import Settings, authenticate

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; "
       "img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'")
HEADERS = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
           'Content-Security-Policy': CSP}
MEDIA_TYPES = {'.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png',
               '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp',
               '.svg': 'image/svg+xml', '.ico': 'image/x-icon', '.woff': 'font/woff',
               '.woff2': 'font/woff2'}
MAX_STATIC_FILES = 1024
MAX_STATIC_BYTES = 64 * 1024 * 1024
MAX_STATIC_FILE_BYTES = 16 * 1024 * 1024
EXECUTION_PATHS = frozenset({
    '/api/v1/projects/{pid}/plans',
    '/api/v1/projects/{pid}/plans/{revision}/confirm',
    '/api/v1/projects/{pid}/generations',
    '/api/v1/projects/{pid}/revisions',
    '/api/v1/projects/{pid}/review-revisions',
})


def _built_files(dist):
    # Immutable startup snapshot: requests never resolve filesystem paths.
    if not dist:
        raise RuntimeError('PPT_WEB_DIST must name a readable built frontend directory')
    root = Path(dist).absolute()
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise RuntimeError('Built frontend paths must not contain symlinks')
    total_bytes = 0
    file_count = 0

    def bounded_read(path):
        nonlocal total_bytes, file_count
        limit = 1024 * 1024 if path == root / 'index.html' else MAX_STATIC_FILE_BYTES
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('static input must be a regular file')
        if (info.st_size > limit or total_bytes + info.st_size > MAX_STATIC_BYTES
                or file_count >= MAX_STATIC_FILES):
            raise ValueError('built frontend exceeds snapshot limits')
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
        with os.fdopen(os.open(path, flags), 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('static input must be a regular file')
            content = stream.read(min(limit, MAX_STATIC_BYTES - total_bytes) + 1)
        if len(content) > limit or total_bytes + len(content) > MAX_STATIC_BYTES:
            raise ValueError('built frontend exceeds snapshot limits')
        total_bytes += len(content)
        file_count += 1
        return content

    try:
        index = root / 'index.html'
        if not root.is_dir() or index.is_symlink() or not index.is_file():
            raise ValueError('missing regular index.html')
        content = bounded_read(index)
        if not content.strip():
            raise ValueError('empty index.html')
        files = {'/': (content, 'text/html')}
        assets = root / 'assets'
        if assets.is_symlink():
            raise ValueError('symlink assets directory')
        if assets.exists():
            if not assets.is_dir():
                raise ValueError('assets is not a directory')
            for count, path in enumerate(assets.rglob('*'), 1):
                if count > 2048:
                    raise ValueError('too many built frontend entries')
                if path.is_symlink():
                    raise ValueError('symlink asset')
                relative = path.relative_to(root)
                if any(part.startswith('.') for part in relative.parts):
                    continue
                if path.is_file() and path.suffix in MEDIA_TYPES:
                    files['/' + relative.as_posix()] = (bounded_read(path), MEDIA_TYPES[path.suffix])
        return files
    except (OSError, ValueError) as exc:
        raise RuntimeError('A readable regular built frontend is required') from exc


def create_site(settings: Settings | None = None, engine=None, dist=None):
    """Uvicorn factory; reverse proxy owns OAuth, rebuild/restart updates assets."""
    if os.getenv('PPT_LOCAL_TEST_ONLY') is not None:
        raise RuntimeError('PPT_LOCAL_TEST_ONLY is forbidden for the real-auth site')
    execution_flag = os.getenv('PPT_EXECUTION_ENABLED', 'false').strip().lower()
    if execution_flag not in ('true', 'false'):
        raise RuntimeError('Invalid execution admission flag')
    execution_enabled = execution_flag == 'true'
    if execution_enabled and os.getenv('PPT_EXECUTION_BACKEND') not in ('kubernetes','agent-service'):
        raise RuntimeError('Execution requires an isolated configured controller')
    files = _built_files(dist if dist is not None else os.getenv('PPT_WEB_DIST'))
    api = create_app(settings, engine)
    execution_routes = [route for route in api.routes if getattr(route, 'path', None) in EXECUTION_PATHS]

    class Site:
        state = api.state

        async def __call__(self, scope, receive, send):
            if scope['type'] != 'http':
                return await api(scope, receive, send)
            path = scope['path']
            execution = scope['method'] == 'POST' and any(
                route.matches(scope)[0] == Match.FULL for route in execution_routes)
            static = path == '/' or path.startswith('/assets/')
            if execution or static:
                # Unchanged real dependency and actual ASGI socket peer.
                try:
                    await authenticate(Request(dict(scope, app=api), receive=receive))
                except HTTPException as exc:
                    return await JSONResponse({'detail': exc.detail}, exc.status_code,
                                              headers=HEADERS)(scope, receive, send)
                if execution and not execution_enabled:
                    return await JSONResponse({'detail': {'code': 'execution_not_connected'}},
                                              503, headers=HEADERS)(scope, receive, send)
                if static and scope['method'] not in ('GET', 'HEAD'):
                    return await JSONResponse({'detail': {'code': 'method_not_allowed'}}, 405,
                                              headers=HEADERS | {'Allow': 'GET, HEAD'})(scope, receive, send)
                item = files.get(path)
                if item is not None:
                    content, media_type = item
                    headers = HEADERS | {'Content-Length': str(len(content))}
                    return await Response(content if scope['method'] == 'GET' else b'',
                                          media_type=media_type, headers=headers)(scope, receive, send)
            if path == '/api' or path.startswith('/api/') or path == '/healthz':
                return await api(scope, receive, send)
            return await JSONResponse({'detail': {'code': 'not_found'}}, 404,
                                      headers=HEADERS)(scope, receive, send)

    return Site()
