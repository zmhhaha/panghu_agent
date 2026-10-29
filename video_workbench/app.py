"""SVG/HTML authoring workbench; trusted proxy authentication or explicit loopback mode."""
from __future__ import annotations

import hashlib
import hmac
import html
import io
import json
import os
import re
import sqlite3
import zipfile
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_service.client import AgentClient
from agent_service.contracts import ServiceError
from .agent import VideoAgentGateway
from .config import AgentSettings
from .service import Workflow
from .store import Store

WEB = Path(__file__).parent / 'web'
PREVIEW_CSP = ("sandbox; default-src 'none'; script-src 'none'; "
               "style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; "
               "object-src 'none'; frame-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'")
UI_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
          "frame-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")

MESSAGES = {
    'not_found': '项目或资源不存在。', 'revision_conflict': '项目已更新，请核对最新内容后重试。',
    'head_conflict': '采用版本已变化，请核对后重试。', 'idempotency_conflict': '请求标识已用于另一次操作。',
    'operation_in_progress': '这个镜头或项目已有任务，请等待完成。',
    'agent_not_configured': '尚未配置通用 Agent 服务；可以先创建项目和编辑镜头。',
    'video_profile_unavailable': '通用 Agent 服务尚未启用所需的视觉任务。',
    'video_preflight_unavailable': '未能确认 Agent 服务能力，尚未提交任务；请稍后重试。',
    'storyboard_requires_empty_project': '仅支持对尚无镜头的项目进行自动拆镜。',
    'unadopted_scenes': '请先为每个镜头采用一个版本，再导出素材。',
    'authentication_required': '请通过已配置身份认证的入口访问。',
    'csrf_rejected': '会话校验失败，请刷新页面后重试。',
}


class Fields(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

    @field_validator('*')
    @classmethod
    def text_characters(cls, value):
        if isinstance(value, str) and any((ord(c) < 32 and c not in '\t\r\n') or ord(c) == 127
                                         or 0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError('Unsupported text characters')
        return value


class ProjectFields(Fields):
    title: str = Field(min_length=1, max_length=300)
    script: str = Field(min_length=1, max_length=50000)
    style: str = Field(default='', max_length=4000)


class SceneFields(Fields):
    title: str = Field(min_length=1, max_length=300)
    narration: str = Field(min_length=1, max_length=10000)
    visual_description: str = Field(min_length=1, max_length=10000)
    duration_seconds: float = Field(default=8, ge=1, le=120, allow_inf_nan=False)
    format: Literal['svg', 'html'] = 'svg'


class SceneEdit(SceneFields):
    expected_revision: int = Field(ge=1, strict=True)


class Revision(Fields):
    expected_revision: int = Field(ge=1, strict=True)


class Generation(Revision):
    base_version_id: str | None = Field(default=None, max_length=100)
    instruction: str = Field(default='', max_length=8000)


class Adoption(Fields):
    version_id: str = Field(min_length=1, max_length=100)
    expected_head_version_id: str | None = Field(max_length=100)


def preview_html(source, format):
    if format == 'html':
        return source
    return ('<!doctype html><html><head><meta charset="utf-8"><style>'
            'html,body{margin:0;width:100%;height:100%;background:#0e1420;overflow:hidden}'
            'svg{display:block;width:100%;height:100%;object-fit:contain}</style></head><body>' + source + '</body></html>')


def export_project(store, owner, project):
    if not project['scenes'] or any(not scene['head_version_id'] for scene in project['scenes']):
        raise ServiceError('unadopted_scenes', 409)
    buffer = io.BytesIO()
    manifest = dict(schema_version='1', title=project['title'], style=project['style'], scenes=[])
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for i, scene in enumerate(project['scenes'], 1):
            version = store.version(owner, scene['head_version_id'])
            name = f'scenes/{i:03d}.{version["format"]}'
            page = f'preview/{i:03d}.html'
            archive.writestr(name, version['source'])
            archive.writestr(page, preview_html(version['source'], version['format']))
            manifest['scenes'].append(dict(index=i, title=scene['title'], narration=scene['narration'],
                                          duration_seconds=scene['duration_seconds'], format=version['format'],
                                          source=name, preview=page, version_id=version['id'],
                                          source_sha256=hashlib.sha256(version['source'].encode()).hexdigest()))
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        # Inline sources in isolated srcdoc frames work when opened from file:// too.
        frames = ''.join(f'<iframe sandbox="" title="镜头 {i}" data-seconds="{s["duration_seconds"]}" '
                         f'srcdoc="{html.escape(preview_html(store.version(owner, s["version_id"])["source"], s["format"]), quote=True)}"></iframe>'
                         for i, s in enumerate(manifest['scenes'], 1))
        archive.writestr('index.html', '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" content="default-src &apos;none&apos;; '
            'script-src &apos;unsafe-inline&apos;; style-src &apos;unsafe-inline&apos;; frame-src about:;">'
            '<title>' + html.escape(project['title']) + '</title><style>'
            'body{margin:0;background:#0e1420;color:white;font:16px sans-serif;text-align:center}'
            'iframe{display:none;width:100vw;height:calc(100vh - 64px);border:0}'
            'iframe.active{display:block}button{margin:12px;padding:8px 20px}</style>'
            + frames + '<button id="play">播放 / 暂停</button><button id="prev">上一镜</button>'
            '<button id="next">下一镜</button><span id="position"></span>'
            '<script>const fs=[...document.querySelectorAll("iframe")];let i=0,t=null;'
            'function show(n){i=(n+fs.length)%fs.length;fs.forEach((f,k)=>f.classList.toggle("active",i===k));'
            'document.getElementById("position").textContent=(i+1)+" / "+fs.length}'
            'function stop(){clearTimeout(t);t=null}function step(){show(i+1);schedule()}'
            'function schedule(){t=setTimeout(step,Number(fs[i].dataset.seconds)*1000)}'
            'document.getElementById("play").onclick=()=>{if(t)stop();else schedule()};'
            'document.getElementById("prev").onclick=()=>{stop();show(i-1)};'
            'document.getElementById("next").onclick=()=>{stop();show(i+1)};show(0);</script></html>')
        archive.writestr('README.txt', '打开 index.html 查看已采用版本的顺序预览。\n'
                          '这是 SVG/HTML 素材包，不包含配音、音画同步或 MP4 编码。\n'
                          '动画在各 iframe 载入时开始；顺序播放器用于审阅，不是帧精确时间轴。\n')
    return buffer.getvalue()


def create_app(*, db_path=None, gateway=None, local_mode=False, background=True, trusted_hosts=None):
    owned_client = None
    if gateway is None:
        settings = AgentSettings.from_env()
        if settings:
            owned_client = AgentClient(settings.base_url, settings.token)
            gateway = VideoAgentGateway(owned_client, settings.revision)
    store = Store(db_path or os.environ.get('VIDEO_WORKBENCH_DB', '.runtime/video-workbench/workbench.db'))
    workflow = Workflow(store, gateway)

    @asynccontextmanager
    async def lifespan(app):
        if background:
            workflow.start()
        yield
        workflow.stop()
        if owned_client:
            owned_client.close()

    app = FastAPI(title='SVG / HTML 视频工作台', lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.store, app.state.workflow = store, workflow
    # Proxy mode must be bound behind an authenticated, header-scrubbing proxy.
    app.state.trusted_hosts = set(trusted_hosts or {'127.0.0.1', 'localhost', '[::1]'})

    def csrf(owner):
        return hmac.new(bytes.fromhex(store.csrf_secret), owner.encode(), 'sha256').hexdigest()

    def identity(request: Request):
        if local_mode:
            # The local CLI additionally binds only to loopback.
            if request.client is None or request.client.host not in {'127.0.0.1', '::1', 'testclient'}:
                raise ServiceError('authentication_required', 401)
            return 'local-developer'
        owner = request.headers.get('x-forwarded-user', '')
        if not 1 <= len(owner) <= 200 or any(ord(c) < 33 or ord(c) == 127 for c in owner):
            raise ServiceError('authentication_required', 401)
        return owner

    def mutation(request: Request, owner=Depends(identity)):
        supplied = request.headers.get('x-csrf-token', '')
        if not re.fullmatch(r'[0-9a-f]{64}', supplied) or not hmac.compare_digest(supplied, csrf(owner)):
            raise ServiceError('csrf_rejected', 403)
        origin = request.headers.get('origin')
        if origin:
            try:
                parsed = urlsplit(origin)
            except ValueError:
                raise ServiceError('csrf_rejected', 403) from None
            if origin == 'null' or parsed.scheme not in {'http', 'https'} or parsed.netloc != request.headers.get('host'):
                raise ServiceError('csrf_rejected', 403)
        return owner

    def idempotency(value):
        if value is None or not re.fullmatch(r'[A-Za-z0-9_.:-]{8,128}', value):
            raise ServiceError('idempotency_key_required', 422)
        return value

    @app.middleware('http')
    async def headers(request, call_next):
        # Host validation prevents DNS rebinding of the loopback development service.
        host = request.headers.get('host', '').rsplit(':', 1)[0] if ':' in request.headers.get('host', '') else request.headers.get('host', '')
        if request.headers.get('host', '').startswith('['):
            host = request.headers['host'].split(']')[0] + ']'
        if host not in app.state.trusted_hosts:
            return JSONResponse({'detail': {'code': 'invalid_host', 'message': '访问域名未配置。'}}, status_code=400)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        if '/preview' not in request.url.path:
            response.headers['Content-Security-Policy'] = UI_CSP
        return response

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return JSONResponse({'detail': {'code': exc.code, 'message': MESSAGES.get(exc.code, exc.code)}}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({'detail': {'code': 'invalid_request', 'message': '请检查输入字段和长度。'}}, status_code=422)

    @app.get('/healthz')
    def health():
        return {'status': 'ok'}

    @app.get('/readyz')
    def ready():
        # Readiness is local: never spend model calls or depend on an upstream
        # network round trip. mode=rw refuses to recreate a missing database.
        if gateway is None:
            return JSONResponse({'status': 'not_ready'}, status_code=503)
        try:
            with closing(sqlite3.connect(Path(store.path).as_uri() + '?mode=rw', uri=True, timeout=1)) as db:
                row = db.execute('SELECT value FROM metadata WHERE key=?', ('csrf_secret',)).fetchone()
                if row is None or row[0] != store.csrf_secret:
                    raise sqlite3.DatabaseError()
        except sqlite3.Error:
            return JSONResponse({'status': 'not_ready'}, status_code=503)
        return {'status': 'ready'}

    @app.get('/api/session')
    def session(owner=Depends(identity)):
        return dict(user_id=owner, csrf_token=csrf(owner), agent_configured=gateway is not None)

    @app.get('/api/projects')
    def projects(owner=Depends(identity)):
        return dict(projects=store.list_projects(owner))

    @app.post('/api/projects')
    def create_project(body: ProjectFields, owner=Depends(mutation)):
        project = store.create_project(owner, body.model_dump())
        return store.project(owner, project['id'])

    @app.get('/api/projects/{project_id}')
    def project(project_id: str, owner=Depends(identity)):
        return store.project(owner, project_id)

    @app.post('/api/projects/{project_id}/scenes')
    def create_scene(project_id: str, body: SceneFields, owner=Depends(mutation)):
        return store.save_scene(owner, project_id, body.model_dump())

    @app.patch('/api/scenes/{scene_id}')
    def edit_scene(scene_id: str, body: SceneEdit, owner=Depends(mutation)):
        return store.save_scene(owner, store.scene_project(owner, scene_id), body.model_dump(exclude={'expected_revision'}),
                                scene_id, body.expected_revision)

    @app.post('/api/projects/{project_id}/storyboard', status_code=202)
    def storyboard(project_id: str, body: Revision, owner=Depends(mutation), idempotency_key: str | None = Header(default=None)):
        workflow.require_gateway()
        return store.start(owner, project_id, 'storyboard', body.model_dump(), idempotency(idempotency_key))

    def generation(scene_id, kind, body, owner, idem):
        workflow.require_gateway()
        return store.start(owner, store.scene_project(owner, scene_id), kind, body.model_dump(), idempotency(idem), scene_id=scene_id)

    @app.post('/api/scenes/{scene_id}/generate', status_code=202)
    def generate(scene_id: str, body: Generation, owner=Depends(mutation), idempotency_key: str | None = Header(default=None)):
        return generation(scene_id, 'scene', body, owner, idempotency_key)

    @app.post('/api/scenes/{scene_id}/revise', status_code=202)
    def revise(scene_id: str, body: Generation, owner=Depends(mutation), idempotency_key: str | None = Header(default=None)):
        return generation(scene_id, 'revise', body, owner, idempotency_key)

    @app.post('/api/scenes/{scene_id}/adopt')
    def adopt(scene_id: str, body: Adoption, owner=Depends(mutation)):
        return store.adopt(owner, scene_id, body.version_id, body.expected_head_version_id)

    @app.post('/api/operations/{operation_id}/cancel')
    def cancel(operation_id: str, owner=Depends(mutation)):
        return store.cancel(owner, operation_id)

    @app.get('/api/versions/{version_id}/preview')
    def preview(version_id: str, owner=Depends(identity)):
        version = store.version(owner, version_id)
        return HTMLResponse(preview_html(version['source'], version['format']), headers={'Content-Security-Policy': PREVIEW_CSP})

    @app.get('/api/versions/{version_id}/source')
    def source(version_id: str, owner=Depends(identity)):
        version = store.version(owner, version_id)
        return Response(version['source'], media_type='text/plain', headers={
            'Content-Disposition': f'attachment; filename="scene-{version_id}.{version["format"]}"'})

    @app.get('/api/projects/{project_id}/export')
    def export(project_id: str, owner=Depends(identity)):
        content = export_project(store, owner, store.project(owner, project_id))
        return Response(content, media_type='application/zip', headers={
            'Content-Disposition': f'attachment; filename="video-{project_id}.zip"'})

    @app.get('/')
    def index(owner=Depends(identity)):
        return HTMLResponse((WEB / 'index.html').read_text(encoding='utf-8'))

    if WEB.exists():
        app.mount('/assets', StaticFiles(directory=WEB), name='assets')
    return app
