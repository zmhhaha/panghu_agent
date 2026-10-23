"""Loopback-published development site; never a production authentication entrypoint.

This harness supplies a fixed local test identity through FastAPI's test override.
The production app's JWT/proxy checks are unchanged. No model execution is enabled.
"""
from __future__ import annotations
import os
from pathlib import Path
from fastapi import Request, HTTPException
from starlette.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.staticfiles import StaticFiles
from ppt_service.server.app import create_app
from ppt_service.server.auth import Settings, Identity, authenticate
from ppt_service.server.migrate import migrate


def create_development_site():
    if os.getenv('PPT_LOCAL_TEST_ONLY') != '1':
        raise RuntimeError('Explicit isolated local development mode is required')
    if os.getenv('PPT_EXECUTION_ENABLED', 'false').lower() != 'false':
        raise RuntimeError('This local test harness cannot enable model execution')
    dist = Path(os.environ['PPT_WEB_DIST']).resolve(strict=True)
    if not (dist/'index.html').is_file():
        raise RuntimeError('A real built frontend is required')
    settings = Settings.env()
    api = create_app(settings)
    migrate(api.state.engine)
    origins = {'http://127.0.0.1:18505', 'http://localhost:18505'}

    async def local_test_identity(request: Request):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            if origin is not None and origin not in origins:
                raise HTTPException(403, detail={'code':'origin_rejected'})
        return Identity('urn:ppt:development', 'local-development-owner')

    api.dependency_overrides[authenticate] = local_test_identity
    static = StaticFiles(directory=str(dist), follow_symlink=False)

    class LocalDevelopmentSite:
        async def __call__(self, scope, receive, send):
            if scope['type'] != 'http':
                return await api(scope, receive, send)
            path = scope['path']
            if scope['method'] == 'POST' and (path.endswith('/plans') or path.endswith('/generations') or path.endswith('/revisions') or path.endswith('/confirm')):
                response = JSONResponse({'detail':{'code':'execution_not_connected','message':'开发测试环境尚未接通执行器，不会调用模型。'}}, status_code=503)
                return await response(scope, receive, send)
            if path.startswith('/api/') or path == '/healthz':
                return await api(scope, receive, send)
            if path in ('/oauth2/start', '/oauth2/sign_out'):
                return await RedirectResponse('/', status_code=303)(scope, receive, send)
            if scope['method'] not in ('GET', 'HEAD'):
                return await JSONResponse({'detail':{'code':'method_not_allowed'}}, status_code=405)(scope, receive, send)
            if path.startswith('/assets/'):
                return await static(scope, receive, send)
            response = FileResponse(dist/'index.html', headers={
                'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff',
                'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'",
            })
            return await response(scope, receive, send)
    return LocalDevelopmentSite()
