"""Authenticated internal API. Import has no filesystem/database side effects.

Launch: uvicorn pi_worker.app:create_app --factory --host 0.0.0.0 --port 8080
"""
import hmac
import json
import os
import sqlite3
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from scientific_agent.pi_contracts import ContractError, ResearchJob
from .artifacts import artifact_bytes
from .scheduler import InstanceLock, Scheduler
from .store import QueueStore


@dataclass(frozen=True)
class WorkerSettings:
    runtime_dir: Path
    worker_token: str
    runtime_revision: Optional[str] = None
    skill_bundle_revision: Optional[str] = None
    start_scheduler: bool = True
    artifact_retention_days: int = 30
    max_attempts: int = 2
    max_queue: int = 1000

    def __post_init__(self):
        if not isinstance(self.worker_token,str) or not self.worker_token.strip():
            raise RuntimeError('Worker token is required')
        if self.artifact_retention_days < 1 or self.max_attempts not in (1,2) or self.max_queue < 1:
            raise RuntimeError('Invalid worker lifecycle limits')

    @classmethod
    def from_env(cls):
        token = os.environ.get('SCIENTIFIC_PI_WORKER_TOKEN','')
        root = os.environ.get('SCIENTIFIC_PI_RUNTIME_DIR','')
        if not token or not root:
            raise RuntimeError('SCIENTIFIC_PI_WORKER_TOKEN and SCIENTIFIC_PI_RUNTIME_DIR are required')
        return cls(Path(root), token, os.environ.get('SCIENTIFIC_PI_RUNTIME_REVISION'), os.environ.get('SCIENTIFIC_PI_SKILL_BUNDLE_REVISION'))


def create_app(settings=None, store=None, runtime=None, validator=None):
    settings = settings or WorkerSettings.from_env()
    if not settings.worker_token:
        raise RuntimeError('Worker token is required')
    lock = InstanceLock(Path(settings.runtime_dir)/'control'/'instance.lock')

    def capability_ready():
        if runtime is None:
            return False
        health = getattr(runtime,'health',None)
        if callable(health):
            return health().get('ready') is True
        capability = getattr(runtime,'ready',False)
        return capability() is True if callable(capability) else capability is True

    @asynccontextmanager
    async def lifespan(app):
        nonlocal runtime
        lock.acquire()
        try:
            app.state.store = store or QueueStore(Path(settings.runtime_dir)/'control'/'queue.sqlite')
            if runtime is None:
                from scientific_agent.pi_runtime_config import runtime_from_env
                runtime = runtime_from_env(Path(settings.runtime_dir))
            app.state.runtime = runtime
            app.state.scheduler = None
            if capability_ready() and settings.start_scheduler:
                scheduler = Scheduler(app.state.store,runtime,settings.runtime_dir,validator,max_attempts=settings.max_attempts,retention_days=settings.artifact_retention_days)
                app.state.scheduler = scheduler
                scheduler.start()
            yield
        finally:
            scheduler = getattr(app.state,'scheduler',None)
            if scheduler is None or scheduler.stop():
                lock.release()
            # Keep instance ownership until process exit if execution won't stop.

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings

    @app.middleware('http')
    async def authentication(request, call_next):
        authorization = request.headers.get('authorization','')
        expected = 'Bearer '+settings.worker_token
        if not hmac.compare_digest(authorization.encode('utf-8'),expected.encode('utf-8')):
            return JSONResponse(ContractError('unauthorized','Worker authentication required',401).to_dict(),status_code=401)
        # Enforce actual bytes independently of a caller's Content-Length.
        if request.method in ('PUT','POST'):
            parts = []
            size = 0
            async for part in request.stream():
                size += len(part)
                if size>16384:
                    return JSONResponse(ContractError('request_too_large','Request exceeds 16 KiB',413).to_dict(),status_code=413)
                parts.append(part)
            request._body = b''.join(parts)
        return await call_next(request)

    @app.exception_handler(ContractError)
    async def contract_error(request, exc):
        headers = {'Retry-After':'10'} if exc.status_code==429 else None
        return JSONResponse(exc.to_dict(request.path_params.get('task_id')),status_code=exc.status_code,headers=headers)

    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(request, exc):
        return JSONResponse(ContractError('invalid_request','Invalid request parameters',422).to_dict(),status_code=422)

    @app.exception_handler(sqlite3.Error)
    async def storage_error(request, exc):
        return JSONResponse(ContractError('storage_unavailable','Queue storage unavailable',503,True).to_dict(),status_code=503)

    def valid_id(value):
        try:
            if str(uuid.UUID(value)) != value:
                raise ValueError()
        except ValueError:
            raise ContractError('invalid_task_id','Expected canonical UUID',422)
        return value

    async def body_json(request):
        try:
            value = json.loads(await request.body())
            if not isinstance(value,dict):
                raise ValueError()
            return value
        except (ValueError, UnicodeDecodeError):
            raise ContractError('invalid_request','Expected JSON object',422)

    def queue(request):
        return request.app.state.store

    def ready(request):
        if runtime is None:
            return False
        scheduler = request.app.state.scheduler
        if scheduler is not None and scheduler.degraded:
            return False
        return capability_ready()

    @app.put('/v1/tasks/{task_id}')
    async def put_task(task_id: str, request: Request):
        valid_id(task_id)
        payload = await body_json(request)
        job = ResearchJob.from_dict(payload)
        if job.to_dict()['task_id'] != task_id:
            raise ContractError('invalid_task_id','URL/body task IDs differ',422)
        data = job.to_dict()
        # Replays must remain available even when the runtime is temporarily down.
        try:
            queue(request).get(task_id)
        except ContractError as exc:
            if exc.status_code != 404:
                raise
            if not ready(request):
                raise ContractError('runtime_not_ready','Runtime capability is not ready',503,True)
            for key in ('runtime_revision','skill_bundle_revision'):
                configured = getattr(settings,key)
                if configured is not None and data[key]!=configured:
                    queue(request).reject(data,ContractError('configuration_missing','Pinned runtime/skill revision mismatch',422))
        snapshot, created = queue(request).submit(job,max_queue=settings.max_queue)
        return JSONResponse(snapshot,status_code=201 if created else 200)

    @app.get('/v1/tasks')
    def pending_tasks(request: Request, publication: str = 'pending', cursor: Optional[str] = None, limit: int = 50):
        if publication!='pending':
            raise ContractError('invalid_publication','Only pending publication is supported',422)
        return queue(request).pending(cursor,limit)

    @app.get('/v1/tasks/{task_id}')
    def get_task(task_id: str, request: Request):
        return queue(request).get(valid_id(task_id))

    @app.post('/v1/tasks/{task_id}/cancel')
    def cancel_task(task_id: str, request: Request):
        return queue(request).cancel(valid_id(task_id))

    @app.get('/v1/tasks/{task_id}/result')
    def get_result(task_id: str, request: Request):
        return queue(request).result(valid_id(task_id))

    @app.post('/v1/tasks/{task_id}/publication-ack')
    async def ack_task(task_id: str, request: Request):
        valid_id(task_id)
        body = await body_json(request)
        import re
        if set(body)!={'attempt','result_digest','report_id'} or type(body['attempt']) is not int or body['attempt']<1 or not isinstance(body['result_digest'],str) or not re.fullmatch('[0-9a-f]{64}',body['result_digest']) or not isinstance(body['report_id'],str):
            raise ContractError('invalid_ack','Invalid publication acknowledgement',422)
        valid_id(body['report_id'])
        return queue(request).ack(task_id,body['attempt'],body['result_digest'],body['report_id'])

    @app.get('/v1/tasks/{task_id}/artifacts')
    def get_artifacts(task_id: str, request: Request):
        result, _ = queue(request).artifact_record(valid_id(task_id))
        return {'items':[{k:v for k,v in a.items() if k!='path'} for a in result['artifacts']]}

    @app.get('/v1/tasks/{task_id}/artifacts/{artifact_id}')
    def get_artifact(task_id: str, artifact_id: str, request: Request):
        data, media = artifact_bytes(queue(request),valid_id(task_id),artifact_id)
        return Response(data,media_type=media,headers={'X-Content-Type-Options':'nosniff'})

    @app.get('/v1/health')
    def health(request: Request):
        writable = queue(request).writable()
        return dict(schema_version='1', alive=True, queue_writable=writable, runtime_ready=ready(request), worker_ready=writable and ready(request), capabilities=getattr(runtime,'capabilities',{}) if runtime else {})

    return app
