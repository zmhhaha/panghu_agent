"""Scientific research API: compatible CrewAI routes and optional durable Pi backend."""
import sys, os, threading, re, logging, uuid
from contextlib import asynccontextmanager
from typing import Literal
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
import scientific_agent

# The existing CrewAI ConfigMap overlays the whole scientific_agent directory.
# Preserve its modules first and resolve new Pi modules from fixed image files.
if os.path.isdir('/opt/scientific-pi-extension'):
    scientific_agent.__path__.append('/opt/scientific-pi-extension')

from scientific_agent.pi_contracts import ContractError, public_status, validate_task_id
from tools import sqlite_client as db
from scientific_agent.pi_client import PiClient, WorkerError
from scientific_agent.pi_projection import PiProjection

log = logging.getLogger(__name__)

def llm_service_config_error():
    if not os.getenv('LLM_BASE_URL', '').strip(): return 'LLM_BASE_URL is not configured'
    if not os.getenv('LLM_SERVICE_TOKEN', '').strip(): return 'LLM_SERVICE_TOKEN is not configured'

class ResearchRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=500)
    email: str = Field(default='', max_length=200)
    user_id: str = Field(default='', max_length=200)
    engine: Literal['crewai', 'pi'] | None = None
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
    engine: str = 'crewai'
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

class ReportItem(BaseModel):
    id: str
    topic: str
    summary: str | None = None
    keywords: str | None = None
    model_used: str | None = None
    created_at: str | None = None

def _send_notification(email, topic, task_id):
    from tools.email_client import send_email
    if not send_email(to=email, subject=f'🔬 综述完成: {topic[:30]}', body=f'研究报告已完成。报告 ID: {task_id}。请访问研究助手查看或下载。'):
        raise RuntimeError('Notification delivery was not confirmed')

def _run_refinement(task_id, topic, email='', database=None, projection=None):
    database = database or db
    try:
        database.update_task(task_id,status='running')
        from scientific_agent.crew import create_scientific_crew
        result = str(create_scientific_crew().kickoff(inputs={'topic':topic}))
        database.save_report(task_id,topic,_extract_summary(result),_extract_keywords(topic),result)
        stored = database.get_report_by_task(task_id)
        if not stored or stored['content'] != result: raise RuntimeError('Report storage verification failed')
        database.update_task(task_id,status='done',report=result)
    except Exception as exc:
        database.update_task(task_id,status='failed',error=str(exc))
        return
    # Notification failures never change the research result.
    if email:
        try: projection.notify(task_id,email,topic) if projection else _send_notification(email,topic,task_id)
        except Exception: log.exception('Notification failed for %s',task_id)

def create_app(database=None, worker=None, projection=None, start_reconciler=True):
    database = database if database is not None else getattr(projection, 'db', db)
    if worker is None:
        worker = getattr(projection, 'worker', None)
    if worker is None:
        worker = PiClient(os.getenv('SCIENTIFIC_PI_WORKER_URL',''),os.getenv('SCIENTIFIC_PI_WORKER_TOKEN',''))
    if projection is None:
        projection = PiProjection(database,worker,os.getenv('SCIENTIFIC_PI_SKILL_BUNDLE_REVISION',''),os.getenv('SCIENTIFIC_PI_RUNTIME_REVISION',''),_send_notification)
    stop = threading.Event()
    def reconcile_loop():
        while not stop.is_set():
            try: projection.reconcile()
            except Exception: log.exception('Pi reconciliation deferred')
            stop.wait(10)
    @asynccontextmanager
    async def lifespan(app):
        database.init_db('scientific')
        # Pi tasks live only in scientific_pi_index and never enter this legacy table.
        database.clear_stale_tasks()
        projection.initialize()
        thread = None
        if start_reconciler:
            thread = threading.Thread(target=reconcile_loop,daemon=True)
            thread.start()
        yield
        stop.set()
        if thread: thread.join(timeout=1)
    app = FastAPI(title='🔬 科学综述助手 API (internal)',version='1.1',lifespan=lifespan)
    app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_methods=['*'],allow_headers=['*'])
    app.state.projection, app.state.worker = projection, worker

    def worker_error(exc, task_id=None, ambiguous=False):
        raise HTTPException(503 if ambiguous else exc.status,detail={'code':exc.code,'message':str(exc),
            'task_id':task_id,'retryable':True if ambiguous else exc.retryable})

    def pi_row(task_id):
        try: validate_task_id(task_id)
        except ContractError: raise HTTPException(422,'Invalid task ID')
        try: return projection.get(task_id)
        except Exception: raise HTTPException(503,detail={'code':'storage_unavailable','message':'Task index unavailable','retryable':True,'task_id':task_id})

    def pi_response(row, snapshot):
        values = dict(snapshot,user_id=row['user_id'],notification_status=row.get('notification_status'))
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
        error = llm_service_config_error()
        ready, capabilities = False, None
        try:
            health = worker.health()
            ready = all(health.get(field) is True for field in ('alive','queue_writable','runtime_ready','worker_ready'))
            capabilities = health.get('capabilities',{})
        except WorkerError: pass
        backend = os.getenv('SCIENTIFIC_DEFAULT_ENGINE','crewai')
        return {'status':'ok' if (ready if backend=='pi' else not error) else 'degraded',
                'llm_configured':error is None,'backend':backend,'worker_ready':ready,'capabilities':capabilities}

    @app.post('/scientific-research',response_model=TaskResponse)
    def submit_research(req:ResearchRequest, idempotency_key:str|None=Header(default=None)):
        topic = req.topic.strip()
        if not topic: raise HTTPException(422,'Topic must not be blank')
        engine = req.engine or os.getenv('SCIENTIFIC_DEFAULT_ENGINE','crewai')
        if engine not in ('pi','crewai'): raise HTTPException(503,'Invalid default engine configuration')
        if engine == 'pi':
            if not req.user_id.strip(): raise HTTPException(422,'Pi requires a trusted user_id')
            if idempotency_key is not None and (not 1<=len(idempotency_key)<=128 or any(not 32<=ord(c)<=126 for c in idempotency_key)):
                raise HTTPException(422,'Invalid Idempotency-Key')
            payload = dict(topic=topic,user_id=req.user_id,engine='pi',mode=req.mode,timeout_seconds=req.timeout_seconds)
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
        config_error = llm_service_config_error()
        if config_error: raise HTTPException(503,config_error)
        if req.user_id and database.get_running_task_by_user(req.user_id): raise HTTPException(429,'您已有综述任务正在执行，请等待完成后再提交')
        task_id = database.create_task(topic,req.user_id)
        threading.Thread(target=_run_refinement,args=(task_id,topic,req.email,database,projection),daemon=True).start()
        return TaskResponse(id=task_id,topic=topic,status='pending',user_id=req.user_id)

    @app.get('/scientific-research/{task_id}',response_model=TaskResponse)
    def get_task(task_id:str):
        row = pi_row(task_id)
        if row:
            if row['dispatch']=='rejected': worker_error(WorkerError(int(row['rejection_status']),row['error_code'],row['error']),task_id)
            try: return pi_response(row,worker.get(task_id))
            except WorkerError as exc: worker_error(exc,task_id,exc.status==404 and row['dispatch'] in ('prepared','ambiguous'))
        task = database.get_task(task_id)
        if not task: raise HTTPException(404,'Task not found')
        return TaskResponse(**task,engine='crewai')

    @app.post('/scientific-research/{task_id}/cancel',response_model=TaskResponse)
    def cancel(task_id:str):
        row = pi_row(task_id)
        if not row: raise HTTPException(409,'Cancellation is supported for Pi tasks only')
        if row['dispatch']=='rejected': return get_task(task_id)
        try: return pi_response(row,worker.cancel(task_id))
        except WorkerError as exc: worker_error(exc,task_id)

    @app.get('/scientific-research/{task_id}/artifacts')
    def artifacts(task_id:str):
        if not pi_row(task_id): raise HTTPException(404,'Pi task not found')
        try:
            page = worker.artifacts(task_id)
            fields = ('artifact_id','media_type','size','sha256')
            return {'items':[{key:item[key] for key in fields} for item in page['items']]}
        except WorkerError as exc: worker_error(exc,task_id)

    @app.get('/scientific-research/{task_id}/artifacts/{artifact_id}')
    def artifact(task_id:str,artifact_id:str):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',artifact_id): raise HTTPException(422,'Invalid artifact ID')
        if not pi_row(task_id): raise HTTPException(404,'Pi task not found')
        try: content,headers = worker.artifact(task_id,artifact_id)
        except WorkerError as exc: worker_error(exc,task_id)
        headers = {key.lower():value for key,value in headers.items()}
        return Response(content,media_type=headers.get('content-type','application/octet-stream'),headers={'Content-Disposition':f'attachment; filename="{artifact_id}"','X-Content-Type-Options':'nosniff'})

    @app.get('/scientific-reports',response_model=list[ReportItem])
    def search_reports(q:str=Query(default=''),limit:int=Query(default=20,ge=1,le=100),offset:int=Query(default=0,ge=0)):
        return [ReportItem(**r) for r in database.search_reports(q.replace("'","''"),limit,offset)]

    @app.get('/scientific-reports/{report_id}')
    def get_report(report_id:str):
        pi_row(report_id)  # Validate IDs before legacy helpers interpolate SQL.
        row = database.get_report(report_id)
        if not row: raise HTTPException(404,'Report not found')
        return {k:row.get(k) for k in ('id','topic','summary','keywords','content','model_used','created_at')}

    @app.get('/scientific-download/{task_id}')
    def download_report(task_id:str):
        pi_row(task_id)
        row = database.get_report_by_task(task_id)
        if not row or row.get('content') is None: raise HTTPException(404,'Report not found')
        return Response(row['content'].encode('utf-8'),media_type='text/markdown; charset=utf-8',headers={'Content-Disposition':'attachment; filename='+_safe_filename(row.get('topic','review'))})
    return app

def _safe_filename(topic):
    safe = re.sub(r'[^\x00-\x7F]+','',topic)
    return (re.sub(r'[^\w\s-]','',safe).strip()[:40].replace(' ','_') or 'review')+'.md'

def _extract_summary(text,max_len=200):
    for line in text.splitlines():
        if line.strip() and not line.startswith('#') and len(line.strip())>20: return line.strip()[:max_len]
    return text[:max_len]

def _extract_keywords(topic):
    import json
    return json.dumps([w for w in topic.replace('、',' ').replace(',',' ').replace('，',' ').split() if len(w)>=2][:5],ensure_ascii=False)

app = create_app()
