"""Scientific research API with neutral execution and legacy report access.

Internal callers pass X-Forwarded-User from the existing authenticated UI proxy.
This service is not a public identity-verifying gateway.
"""
import sys, os, threading, re, logging, hashlib, json
from contextlib import asynccontextmanager
from typing import Literal
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from scientific_agent.pi_contracts import ContractError, public_status, validate_task_id, report_sha256
from scientific_agent.pi_citations import TraceUnavailable, validate_manifest, build_citation_trace
from scientific_agent.pi_sections import SectionError, index_sections, select_section
from scientific_agent.pi_report_versions import VersionError, ReportVersionStore
from tools import sqlite_client as db
from scientific_agent.pi_client import PiClient, WorkerError
from scientific_agent.pi_projection import PiProjection, sql
from scientific_agent.agent_config import AgentServiceSettings, execution_backend

log = logging.getLogger(__name__)

class ResearchRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=500)
    email: str = Field(default='', max_length=200)
    user_id: str = Field(default='', max_length=200)
    engine: Literal['pi', 'agent-service'] | None = None
    mode: Literal['brief', 'review'] = 'review'
    timeout_seconds: int = Field(default=1800, ge=60, le=3600, strict=True)

    @field_validator('topic', mode='before')
    @classmethod
    def trim_topic(cls, value):
        return value.strip() if isinstance(value, str) else value

class TaskResponse(BaseModel):
    id: str
    topic: str
    status: str
    user_id: str | None = None
    report: str | None = None
    error: str | None = None
    engine: str = 'pi'
    state: str | None = None
    stage: str | None = None
    progress: int | None = None
    attempt: int | None = None
    revision: int | None = None
    mode: str | None = None
    report_id: str | None = None
    error_code: str | None = None
    notification_status: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    execution_deadline_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    schema_version: str | None = None
    report_sha256: str | None = None
    result_digest: str | None = None
    task_kind: str | None = None
    intent_id: str | None = None
    parent_version_id: str | None = None
    root_report_id: str | None = None
    candidate_version_id: str | None = None

class ReportItem(BaseModel):
    id: str
    topic: str
    summary: str | None = None
    keywords: str | None = None
    model_used: str | None = None
    created_at: str | None = None


class RevisionPreviewRequest(BaseModel):
    model_config = {'extra': 'forbid'}
    base_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    section_id: str = Field(min_length=1, max_length=200, pattern=r'^[A-Za-z0-9_-]+$')
    instruction: str = Field(min_length=1, max_length=2000)
    timeout_seconds: int = Field(default=1800, ge=60, le=3600, strict=True)

    @field_validator('instruction')
    @classmethod
    def validate_instruction(cls, value):
        value = value.strip()
        if not value or any((ord(c) < 32 and c not in '\n\t\r') or ord(c) == 127 for c in value):
            raise ValueError('A nonblank modification instruction without control characters is required')
        return value


class RevisionStartRequest(BaseModel):
    model_config = {'extra':'forbid'}


class AdoptCandidateRequest(BaseModel):
    model_config = {'extra':'forbid'}
    expected_current_version_id: str | None = Field(default=None,pattern=r'^[0-9a-f-]{36}$')
    expected_selection_revision: int = Field(ge=0,lt=2**53-1,strict=True)
    base_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    candidate_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    comparison_token: str = Field(pattern=r'^[0-9a-f]{64}$')

def _send_notification(email, topic, task_id):
    from tools.email_client import send_email
    if not send_email(to=email, subject=f'🔬 综述完成: {topic[:30]}', body=f'研究报告已完成。报告 ID: {task_id}。请访问研究助手查看或下载。'):
        raise RuntimeError('Notification delivery was not confirmed')

def create_app(database=None, worker=None, projection=None, start_reconciler=True, versions=None, workspace=None):
    database = database if database is not None else getattr(projection, 'db', db)
    if worker is None:
        worker = getattr(projection, 'worker', None)
    if worker is None:
        if execution_backend() == 'agent-service':
            from scientific_agent.agent_client import ScientificAgentClient
            settings = AgentServiceSettings.from_env()
            legacy = None
            if os.getenv('SCIENTIFIC_PI_WORKER_URL') and os.getenv('SCIENTIFIC_PI_WORKER_TOKEN'):
                legacy = PiClient(os.environ['SCIENTIFIC_PI_WORKER_URL'], os.environ['SCIENTIFIC_PI_WORKER_TOKEN'])
            worker = ScientificAgentClient(database, settings.base_url, settings.token,
                generation_revision=settings.generation_revision, revision_revision=settings.revision_revision,
                legacy_worker=legacy)
        else:
            worker = PiClient(os.getenv('SCIENTIFIC_PI_WORKER_URL',''),os.getenv('SCIENTIFIC_PI_WORKER_TOKEN',''))
    if projection is None:
        if getattr(worker, 'backend', None) == 'agent-service':
            from scientific_agent.agent_projection import ScientificAgentProjection
            projection = ScientificAgentProjection(database, worker, _send_notification)
        else:
            projection = PiProjection(database,worker,os.getenv('SCIENTIFIC_PI_SKILL_BUNDLE_REVISION',''),os.getenv('SCIENTIFIC_PI_RUNTIME_REVISION',''),_send_notification)
    versions = versions if versions is not None else ReportVersionStore(database)
    projection.versions = versions
    if workspace is None:
        from scientific_agent.workspace import WorkspaceStore
        from scientific_agent.workspace_execution import WorkspaceService
        workspace = WorkspaceService(WorkspaceStore(database), worker)
    stop = threading.Event()
    def reconcile_loop():
        while not stop.is_set():
            try: projection.reconcile()
            except Exception: log.exception('Pi reconciliation deferred')
            # Expired evidence must release payload quota even when task
            # reconciliation is unavailable. The store preserves pinned inputs
            # and idempotency tombstones in one atomic statement.
            try: versions.cleanup_expired()
            except Exception: log.exception('Report evidence cleanup deferred')
            try: workspace.reconcile_all()
            except Exception: log.exception('Research workspace reconciliation deferred')
            stop.wait(10)
    @asynccontextmanager
    async def lifespan(app):
        database.init_db('scientific')
        # Legacy tasks remain historical records; switching execution must not
        # rewrite their status. Pi owns recovery through its durable projection.
        projection.initialize()
        versions.initialize()
        workspace.store.initialize()
        thread = None
        if start_reconciler:
            thread = threading.Thread(target=reconcile_loop,daemon=True)
            thread.start()
        yield
        stop.set()
        if thread: thread.join(timeout=1)
    app = FastAPI(title='🔬 科学综述助手 API (internal)',version='1.2',lifespan=lifespan)
    app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_methods=['*'],allow_headers=['*'])
    app.state.projection, app.state.worker = projection, worker
    app.state.versions = versions
    app.state.workspace = workspace
    from app.api.scientific_workspace import create_router
    app.include_router(create_router(workspace))

    def worker_error(exc, task_id=None, ambiguous=False):
        raise HTTPException(503 if ambiguous else exc.status,detail={'code':exc.code,'message':str(exc),
            'task_id':task_id,'retryable':True if ambiguous else exc.retryable})

    def pi_row(task_id):
        try: validate_task_id(task_id)
        except ContractError: raise HTTPException(422,'Invalid task ID')
        try: return projection.get(task_id)
        except Exception: raise HTTPException(503,detail={'code':'storage_unavailable','message':'Task index unavailable','retryable':True,'task_id':task_id})

    def trusted_user(value, required=True):
        user_id = (value or '').strip()
        if not user_id:
            if required: raise HTTPException(401,'Authenticated user is required')
            return None
        if len(user_id) > 200 or any(ord(char) < 32 or ord(char) == 127 for char in user_id):
            raise HTTPException(422,'Invalid authenticated user')
        return user_id

    def require_owner(row, forwarded_user):
        if row and row['user_id'] != trusted_user(forwarded_user):
            raise HTTPException(404,'Task not found')
        return row

    def report_owner(report, forwarded_user):
        # Both identifiers are checked so an independently named report cannot
        # bypass task ownership. Legacy reports have no Pi index row.
        for identifier in {report['id'], report.get('task_id')} - {None}:
            require_owner(pi_row(identifier),forwarded_user)

    def writing_operation(operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except (SectionError, VersionError) as exc:
            raise HTTPException(exc.status,detail={'code':exc.code,'message':str(exc),'retryable':False})
        except Exception:
            raise HTTPException(503,detail={'code':'version_storage_unavailable','message':'Report writing records are temporarily unavailable','retryable':True})

    def owned_report(report_id, user):
        require_owner(pi_row(report_id),user)
        try:
            report = database.get_report(report_id)
        except Exception:
            raise HTTPException(503,detail={'code':'storage_unavailable','message':'Published report unavailable','retryable':True})
        if not report:
            raise HTTPException(404,'Report not found')
        report_owner(report,user)
        if not isinstance(report.get('content'),str):
            raise HTTPException(503,detail={'code':'storage_unavailable','message':'Published report unavailable','retryable':True})
        return report

    def snapshot_materials(task_id, content):
        try:
            page = worker.artifacts(task_id)
            items = page.get('items') if isinstance(page,dict) else None
            manifest = validate_manifest(content,items)
            records = {}
            for artifact_id,item in manifest.items():
                raw, _ = worker.artifact(task_id,artifact_id,max_bytes=item['size'])
                if (not isinstance(raw,bytes) or len(raw) != item['size']
                        or hashlib.sha256(raw).hexdigest() != item['sha256']):
                    raise TraceUnavailable('artifact_mismatch')
                records[artifact_id] = raw
            # Validate the complete relationship graph before persisting a
            # fixed snapshot. This reads recorded evidence, not original files.
            build_citation_trace(content,items,records)
            return items,records
        except TraceUnavailable as exc:
            raise HTTPException(409,detail={'code':'evidence_unavailable','reason':exc.code,'message':'Consistent evidence records are required before preparing a revision','retryable':False})
        except WorkerError as exc:
            if exc.status == 410:
                raise HTTPException(410,detail={'code':'source_evidence_expired','message':'Evidence records have expired; the published report is unchanged','retryable':False})
            if exc.status in (404,409) or exc.code == 'artifact_size_mismatch':
                raise HTTPException(409,detail={'code':'evidence_unavailable','message':'Consistent evidence records are required before preparing a revision','retryable':False})
            raise HTTPException(503,detail={'code':'evidence_unavailable','message':'Evidence records are temporarily unavailable','retryable':True})

    def pi_response(row, snapshot):
        values = dict(snapshot,user_id=row['user_id'],notification_status=row.get('notification_status'))
        import json
        job = json.loads(row['job'])
        if job.get('task_kind') == 'revision':
            values.update({key:job[key] for key in ('task_kind','intent_id','parent_version_id','root_report_id')})
            binding = writing_operation(versions.get_revision_job,row['user_id'],row['id'])
            values['candidate_version_id'] = binding.get('candidate_version_id') if binding else None
        values['status'] = public_status(values['state'])
        if values['status'] == 'done':
            try: report = database.get_report_by_task(row['id'])
            except Exception: report = None
            if not report:
                raise HTTPException(503,detail={'code':'storage_unavailable','message':'Published report unavailable','task_id':row['id'],'retryable':True})
            values['report'], values['report_id'] = report['content'],row['id']
        if row.get('error_code') and values['state'] == 'publishing':
            values['error_code'],values['error'] = row['error_code'],row['error']
        return TaskResponse(**values)

    @app.get('/scientific-health')
    def health():
        ready, capabilities = False, None
        try:
            health = worker.health()
            ready = all(health.get(field) is True for field in ('alive','queue_writable','runtime_ready','worker_ready'))
            capabilities = health.get('capabilities',{})
        except WorkerError: pass
        # Runtime readiness is reported by the worker. This endpoint neither
        # reads its model credentials nor performs a live upstream model probe.
        return {'status':'ok' if ready else 'degraded','llm_configured':None,
                'llm_configuration_source':'worker','model_probe_performed':False,
                'backend':getattr(worker,'backend','pi'),'worker_ready':ready,'capabilities':capabilities}

    @app.post('/scientific-research',response_model=TaskResponse)
    def submit_research(req:ResearchRequest, idempotency_key:str|None=Header(default=None),
                        x_forwarded_user:str|None=Header(default=None)):
        topic = req.topic.strip()
        if not topic: raise HTTPException(422,'Topic must not be blank')
        user_id = trusted_user(x_forwarded_user)
        if req.user_id and req.user_id != user_id:
            raise HTTPException(422,'Request user_id does not match authenticated user')
        if idempotency_key is not None and (not 1<=len(idempotency_key)<=128 or any(not 32<=ord(c)<=126 for c in idempotency_key)):
            raise HTTPException(422,'Invalid Idempotency-Key')
        # The engine field is retained in the historical business descriptor.
        # Server configuration selects the backend; requests cannot route an
        # Agent Job to a native engine or override its execution/model pins.
        payload = dict(topic=topic,user_id=user_id,engine='pi',mode=req.mode,timeout_seconds=req.timeout_seconds)
        try: row = projection.prepare(payload,req.email,idempotency_key)
        except WorkerError as exc: worker_error(exc)
        except ContractError as exc:
            worker_error(WorkerError(503 if exc.code == 'invalid_revision' else exc.status_code,exc.code,str(exc),exc.code == 'invalid_revision'))
        except Exception: raise HTTPException(503,detail={'code':'storage_unavailable','message':'Dispatch intent was not stored; worker was not called','retryable':True})
        try: snapshot = projection.dispatch(row)
        except WorkerError as exc:
            try: current = projection.get(row['id'])
            except Exception: current = None
            worker_error(exc,row['id'],current is None or current['dispatch'] in ('prepared','ambiguous'))
        except Exception:
            raise HTTPException(503,detail={'code':'storage_unavailable','message':'Submission may have been accepted; query this task ID','task_id':row['id'],'retryable':True})
        return pi_response(row,snapshot)

    @app.get('/scientific-research/{task_id}',response_model=TaskResponse)
    def get_task(task_id:str,x_forwarded_user:str|None=Header(default=None)):
        row = require_owner(pi_row(task_id),x_forwarded_user)
        if row:
            if row['dispatch']=='rejected': worker_error(WorkerError(int(row['rejection_status']),row['error_code'],row['error']),task_id)
            try:
                if projection.is_revision(row):
                    binding = versions.get_revision_job(row['user_id'],task_id)
                    if binding['status'] in ('cancel_requested','cancelled'):
                        return pi_response(row,projection.recover_revision_cancel(row))
                return pi_response(row,projection.sync_revision(row,worker.get(task_id)))
            except WorkerError as exc: worker_error(exc,task_id,exc.status==404 and row['dispatch'] in ('prepared','ambiguous'))
        task = database.get_task(task_id)
        if not task: raise HTTPException(404,'Task not found')
        return TaskResponse(**task,engine='crewai')

    @app.post('/scientific-research/{task_id}/cancel',response_model=TaskResponse)
    def cancel(task_id:str,x_forwarded_user:str|None=Header(default=None)):
        row = require_owner(pi_row(task_id),x_forwarded_user)
        if not row: raise HTTPException(409,'Cancellation requires a managed scientific task')
        if row['dispatch']=='rejected': return get_task(task_id,x_forwarded_user)
        try:
            if projection.is_revision(row):
                return pi_response(row,projection.cancel_revision(row))
            return pi_response(row,projection.sync_revision(row,worker.cancel(task_id)))
        except WorkerError as exc: worker_error(exc,task_id)

    @app.get('/scientific-research/{task_id}/artifacts')
    def artifacts(task_id:str,x_forwarded_user:str|None=Header(default=None)):
        if not require_owner(pi_row(task_id),x_forwarded_user): raise HTTPException(404,'Pi task not found')
        try:
            page = worker.artifacts(task_id)
            fields = ('artifact_id','media_type','size','sha256')
            return {'items':[{key:item[key] for key in fields} for item in page['items']]}
        except WorkerError as exc: worker_error(exc,task_id)

    @app.get('/scientific-research/{task_id}/artifacts/{artifact_id}')
    def artifact(task_id:str,artifact_id:str,x_forwarded_user:str|None=Header(default=None)):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',artifact_id): raise HTTPException(422,'Invalid artifact ID')
        if not require_owner(pi_row(task_id),x_forwarded_user): raise HTTPException(404,'Pi task not found')
        try: content,headers = worker.artifact(task_id,artifact_id)
        except WorkerError as exc: worker_error(exc,task_id)
        headers = {key.lower():value for key,value in headers.items()}
        return Response(content,media_type=headers.get('content-type','application/octet-stream'),headers={'Content-Disposition':f'attachment; filename="{artifact_id}"','X-Content-Type-Options':'nosniff'})

    @app.get('/scientific-research/{task_id}/citations')
    def citations(task_id:str,x_forwarded_user:str|None=Header(default=None)):
        # The same trusted identity boundary applies to every trace read, even
        # when a report has no structured records. Authorize before worker I/O.
        user = trusted_user(x_forwarded_user)
        row = require_owner(pi_row(task_id),user)
        try:
            report = database.get_report_by_task(task_id)
        except Exception:
            raise HTTPException(503,detail={'code':'storage_unavailable','message':'Published report unavailable','retryable':True})
        if not report:
            if row:
                raise HTTPException(409,detail={'code':'result_not_ready','message':'No published report is available','retryable':True})
            raise HTTPException(404,'Report not found')
        # A shared legacy alias must not expose a privately owned Pi report.
        report_owner(report,user)
        content = report.get('content')
        if not isinstance(content,str):
            raise HTTPException(503,detail={'code':'storage_unavailable','message':'Published report unavailable','retryable':True})

        def unavailable(availability,reason):
            return dict(schema_version='1',availability=availability,reason=reason,
                        report_sha256=report_sha256(content),citations=[],warnings=[])

        if row is None:
            return unavailable('legacy','structured_records_unavailable')
        try:
            page = worker.artifacts(task_id)
            items = page.get('items') if isinstance(page,dict) else None
            manifest = validate_manifest(content,items)
            # Validate the entire manifest and report binding before downloading
            # any records. Never fetch arbitrary IDs supplied by the worker.
            records = {}
            for artifact_id,item in manifest.items():
                content_bytes, _ = worker.artifact(task_id,artifact_id,max_bytes=item['size'])
                if (not isinstance(content_bytes,bytes) or len(content_bytes) != item['size']
                        or hashlib.sha256(content_bytes).hexdigest() != item['sha256']):
                    raise TraceUnavailable('artifact_mismatch')
                records[artifact_id] = content_bytes
            return build_citation_trace(content,items,records)
        except TraceUnavailable as exc:
            return unavailable('unavailable',exc.code)
        except WorkerError as exc:
            if exc.code == 'artifact_size_mismatch':
                return unavailable('unavailable','artifact_mismatch')
            if exc.status == 410:
                return unavailable('expired','artifact_expired')
            if exc.status == 404:
                return unavailable('unavailable','structured_records_unavailable')
            if exc.status == 409:
                return unavailable('unavailable','result_not_ready')
            # No controller URLs, tokens or upstream error text enter this
            # response; a retry must not be mistaken for missing evidence.
            raise HTTPException(503,detail={'code':'trace_unavailable','message':'Citation records are temporarily unavailable','retryable':True})

    @app.get('/scientific-reports',response_model=list[ReportItem])
    def search_reports(q:str=Query(default=''),limit:int=Query(default=20,ge=1,le=100),offset:int=Query(default=0,ge=0),
                       x_forwarded_user:str|None=Header(default=None)):
        user_id = trusted_user(x_forwarded_user,required=False)
        # Apply ownership before LIMIT/OFFSET. Legacy reports remain shared;
        # Pi reports are visible only to the task owner.
        hidden = '1=1' if user_id is None else 'p.user_id != '+sql(user_id)
        condition = 'NOT EXISTS (SELECT 1 FROM scientific_pi_index p WHERE (p.id=r.task_id OR p.id=r.id) AND '+hidden+')'
        if q:
            pattern = sql('%'+q+'%')
            condition += ' AND (r.topic LIKE '+pattern+' OR r.summary LIKE '+pattern+' OR r.content LIKE '+pattern+')'
        rows = database._query('SELECT r.* FROM scientific_reports r WHERE '+condition+
                               ' ORDER BY r.created_at DESC,r.id DESC LIMIT '+str(limit)+' OFFSET '+str(offset))
        return [ReportItem(**r) for r in rows]

    @app.get('/scientific-reports/{report_id}')
    def get_report(report_id:str,x_forwarded_user:str|None=Header(default=None)):
        require_owner(pi_row(report_id),x_forwarded_user)
        row = database.get_report(report_id)
        if not row: raise HTTPException(404,'Report not found')
        report_owner(row,x_forwarded_user)
        return {k:row.get(k) for k in ('id','topic','summary','keywords','content','model_used','created_at')}

    @app.get('/scientific-reports/{report_id}/sections')
    def report_sections(report_id:str,x_forwarded_user:str|None=Header(default=None)):
        user = trusted_user(x_forwarded_user)
        report = owned_report(report_id,user)
        return dict(writing_operation(index_sections,report['content']),report_id=report['id'])

    @app.get('/scientific-reports/{report_id}/versions')
    def report_versions(report_id:str,limit:int=Query(default=50,ge=1,le=100),offset:int=Query(default=0,ge=0),
                        x_forwarded_user:str|None=Header(default=None)):
        user = trusted_user(x_forwarded_user)
        report = owned_report(report_id,user)
        # Reading history does not create private copies or snapshots.
        return dict(schema_version='1',source_report_id=report['id'],
                    source_sha256=report_sha256(report['content']),
                    items=writing_operation(versions.list_versions,user,report['id'],limit=limit,offset=offset),
                    limit=limit,offset=offset)

    @app.get('/scientific-reports/{report_id}/writing-state')
    def writing_state(report_id:str,x_forwarded_user:str|None=Header(default=None)):
        user = trusted_user(x_forwarded_user)
        report = owned_report(report_id,user)
        return dict(writing_operation(versions.writing_state,user,report['id'],report_sha256(report['content'])),
                    source_report_id=report['id'],source_sha256=report_sha256(report['content']))

    def published_candidate(version_id,user):
        version = writing_operation(versions.get_version,user,version_id,include_content=True)
        binding = writing_operation(versions.get_revision_job,user,version['source_report_id'])
        if (not binding or binding['status'] != 'succeeded' or binding['candidate_version_id'] != version_id
                or not binding['result_digest'] or version['parent_version_id'] is None):
            raise HTTPException(409,detail={'code':'candidate_not_available','message':'A fully published revision candidate is required','retryable':False})
        try:
            source = owned_report(version['source_report_id'],user)
        except HTTPException as exc:
            if exc.status_code != 404: raise
            raise HTTPException(409,detail={'code':'candidate_not_available','message':'The revision source report has not been published','retryable':True})
        row = require_owner(pi_row(version['source_report_id']),user)
        if (not row or row['job'] != binding['job'] or source['id'] != version['source_report_id']
                or source.get('task_id') != version['source_report_id']
                or source['content'] != version['content']
                or report_sha256(source['content']) != version['report_sha256']):
            raise HTTPException(409,detail={'code':'candidate_not_available','message':'The published revision does not match its registered version','retryable':False})
        return version

    @app.get('/scientific-report-versions/{candidate_id}/comparison')
    def compare_revision(candidate_id:str,x_forwarded_user:str|None=Header(default=None)):
        user = trusted_user(x_forwarded_user)
        published_candidate(candidate_id,user)
        return writing_operation(versions.compare_candidate,user,candidate_id)

    @app.post('/scientific-report-versions/{candidate_id}/adopt')
    def adopt_revision(candidate_id:str,req:AdoptCandidateRequest,idempotency_key:str|None=Header(default=None),
                       x_forwarded_user:str|None=Header(default=None)):
        user = trusted_user(x_forwarded_user)
        if idempotency_key is None or not 1 <= len(idempotency_key) <= 128 or any(not 32 <= ord(c) <= 126 for c in idempotency_key):
            raise HTTPException(422,'A valid Idempotency-Key is required')
        previous = writing_operation(versions.find_adoption,user,idempotency_key)
        if previous is None:
            published_candidate(candidate_id,user)
        receipt = writing_operation(versions.adopt_candidate,user,candidate_id,
                    req.expected_current_version_id,req.expected_selection_revision,req.base_sha256,
                    req.candidate_sha256,req.comparison_token,idempotency_key)
        return dict(writing_operation(versions.writing_state,user,receipt['root_report_id'],include_versions=False),
                    adoption=receipt)

    def public_snapshot(snapshot):
        metadata = {key:snapshot.get(key) for key in ('snapshot_id','version_id','report_sha256',
                    'payload_bytes','created_at','expires_at','pinned') if key in snapshot}
        metadata['snapshot_sha256'] = snapshot.get('payload_sha256')
        metadata['material_policy'] = snapshot.get('scope')
        return metadata

    def snapshot_key(version_id):
        return hashlib.sha256((version_id + ':recorded-evidence-v1').encode('ascii')).hexdigest()

    @app.post('/scientific-reports/{report_id}/revision-previews')
    def prepare_revision(report_id:str,req:RevisionPreviewRequest,idempotency_key:str|None=Header(default=None),
                         x_forwarded_user:str|None=Header(default=None)):
        user = trusted_user(x_forwarded_user)
        if idempotency_key is None or not 1 <= len(idempotency_key) <= 128 or any(not 32 <= ord(c) <= 126 for c in idempotency_key):
            raise HTTPException(422,'A valid Idempotency-Key is required')
        report = owned_report(report_id,user)
        section = writing_operation(select_section,report['content'],req.section_id,req.base_sha256)
        if not section['editable']:
            raise HTTPException(409,detail={'code':'section_not_editable','message':'The selected section has no editable text','retryable':False})
        # Legacy shared originals remain readable. Revision preparation needs
        # the Pi evidence protocol; it never guesses records for old reports.
        task_id = report.get('task_id') or report['id']
        if not require_owner(pi_row(task_id),user):
            raise HTTPException(409,detail={'code':'structured_records_unavailable','message':'This report has no structured evidence for revision','retryable':False})
        writing_operation(versions.cleanup_expired)
        version = writing_operation(versions.ensure_base,user,report['id'],report['content'],report.get('topic') or '')
        previous = writing_operation(versions.find_intent,user,idempotency_key)
        if previous:
            snapshot_id = previous['snapshot_id']
        else:
            key = snapshot_key(version['version_id'])
            fixed = writing_operation(versions.get_snapshot_by_key,user,key)
            if fixed is None:
                items,records = snapshot_materials(task_id,report['content'])
                fixed = writing_operation(versions.save_snapshot,user,version['version_id'],items,records,key)
            snapshot_id = fixed['snapshot_id']
        intent = writing_operation(versions.prepare_intent,user,version['version_id'],req.base_sha256,
                    req.section_id,section['sha256'],req.instruction,idempotency_key,snapshot_id,
                    timeout_seconds=req.timeout_seconds)
        snapshot = writing_operation(versions.get_snapshot,user,snapshot_id)
        return dict(schema_version='1',intent=intent,parent=version,section=section,
                    snapshot=public_snapshot(snapshot),dispatch_available=False,
                    message='Revision input is fixed; no model task has been started')

    @app.get('/scientific-report-versions/{version_id}')
    def version_detail(version_id:str,x_forwarded_user:str|None=Header(default=None)):
        user = trusted_user(x_forwarded_user)
        return writing_operation(versions.get_version,user,version_id,include_content=True)

    @app.get('/scientific-report-versions/{version_id}/sections')
    def version_sections(version_id:str,x_forwarded_user:str|None=Header(default=None)):
        user = trusted_user(x_forwarded_user)
        version = writing_operation(versions.get_version,user,version_id,include_content=True)
        return dict(writing_operation(index_sections,version['content']),version_id=version['version_id'])

    @app.get('/scientific-report-versions/{version_id}/download')
    def version_download(version_id:str,x_forwarded_user:str|None=Header(default=None)):
        user = trusted_user(x_forwarded_user)
        version = writing_operation(versions.get_version,user,version_id,include_content=True)
        return Response(version['content'].encode('utf-8'),media_type='text/markdown; charset=utf-8',
                        headers={'Content-Disposition':'attachment; filename='+_safe_filename(version.get('topic') or 'report-version'),
                                 'X-Content-Type-Options':'nosniff'})

    @app.get('/scientific-report-snapshots/{snapshot_id}')
    def snapshot_detail(snapshot_id:str,x_forwarded_user:str|None=Header(default=None)):
        user = trusted_user(x_forwarded_user)
        return public_snapshot(writing_operation(versions.get_snapshot,user,snapshot_id))

    @app.get('/scientific-revision-previews/{intent_id}')
    def revision_preview(intent_id:str,x_forwarded_user:str|None=Header(default=None)):
        user = trusted_user(x_forwarded_user)
        binding = writing_operation(versions.find_revision_job,user,intent_id)
        if binding:
            return dict(schema_version='1',task_id=binding['task_id'],state=binding['status'],
                        candidate_version_id=binding['candidate_version_id'],dispatch_available=False)
        return dict(schema_version='1',intent=writing_operation(versions.get_intent,user,intent_id),dispatch_available=False,
                    start_url='/scientific-revision-previews/'+intent_id+'/start')

    @app.post('/scientific-revision-previews/{intent_id}/start',response_model=TaskResponse)
    def start_revision(intent_id:str,req:RevisionStartRequest|None=None,
                       x_forwarded_user:str|None=Header(default=None)):
        user = trusted_user(x_forwarded_user)
        try:
            row = projection.prepare_revision(user,intent_id)
        except WorkerError as exc: worker_error(exc)
        except (VersionError,SectionError) as exc:
            raise HTTPException(exc.status,detail={'code':exc.code,'message':str(exc),'retryable':False})
        except ContractError as exc:
            raise HTTPException(exc.status_code,detail={'code':exc.code,'message':str(exc),'retryable':False})
        except Exception:
            raise HTTPException(503,detail={'code':'version_storage_unavailable','message':'Revision preparation may be stored; retry the same preview','retryable':True})
        try:
            snapshot = projection.dispatch(row)
        except WorkerError as exc:
            try: current = projection.get(row['id'])
            except Exception: current = None
            worker_error(exc,row['id'],current is None or current['dispatch'] in ('prepared','ambiguous'))
        except Exception:
            raise HTTPException(503,detail={'code':'storage_unavailable','message':'Revision submission may have been accepted; query this task ID','task_id':row['id'],'retryable':True})
        return pi_response(row,snapshot)

    @app.get('/scientific-download/{task_id}')
    def download_report(task_id:str,x_forwarded_user:str|None=Header(default=None)):
        require_owner(pi_row(task_id),x_forwarded_user)
        row = database.get_report_by_task(task_id)
        if not row or row.get('content') is None: raise HTTPException(404,'Report not found')
        report_owner(row,x_forwarded_user)
        return Response(row['content'].encode('utf-8'),media_type='text/markdown; charset=utf-8',headers={'Content-Disposition':'attachment; filename='+_safe_filename(row.get('topic','review'))})
    return app

def _safe_filename(topic):
    safe = re.sub(r'\s+', '_', (topic or '').strip())
    safe = re.sub(r'[^A-Za-z0-9_-]', '', safe)[:40]
    return (safe or 'review')+'.md'

app = create_app()
