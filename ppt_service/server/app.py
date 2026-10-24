from __future__ import annotations
import asyncio,json,re,secrets
from typing import Literal
from fastapi import FastAPI,Depends,Request,Response,UploadFile,File,Header,HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel,Field,ConfigDict
from sqlalchemy import create_engine,select,func
from sqlalchemy.orm import sessionmaker
import jwt
from .auth import Settings,Identity,authenticate
from .models import *
from .repository import Repository,fail,job_dto,TERMINAL
from .storage import Storage,MAX_PROJECT
from .middleware import BodyLimitMiddleware
CSRF_TOKEN_RE=re.compile(r'[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]\Z')
class Strict(BaseModel):model_config=ConfigDict(extra='forbid')
class ProjectCreate(Strict):
    title:str=Field(min_length=1,max_length=200)
    purpose:str=Field(default='',max_length=4000)
    audience:str=Field(default='',max_length=4000)
    page_count:int=Field(default=8,ge=3,le=20)
    language:str=Field(default='zh',max_length=50)
    style:str=Field(default='',max_length=4000)
    requirements:str=Field(default='',max_length=12000)
    external_model_ack:bool=False
class ProjectPatch(ProjectCreate):expected_revision:int
class PlanRequest(Strict):
    expected_revision:int
    feedback:str=Field(default='',max_length=12000)
    phase:Literal['plan_initial','plan_options']='plan_initial'
class Confirm(Strict):
    expected_revision:int
    input_snapshot_hash:str=Field(pattern='^[a-f0-9]{64}$')
    selection:dict
class Generate(Strict):
    expected_revision:int
    plan_revision:int
class Revise(Strict):
    expected_revision:int
    base_version_id:str
    feedback:str=Field(min_length=1,max_length=12000)
    pages:list[int]=Field(default_factory=list,max_length=20)
def asset_dto(a):return {k:getattr(a,k) for k in ('id','display_name','media_type','bytes','sha256','parse_status','text_summary','parse_error')}
def plan_dto(a):return {k:getattr(a,k) for k in ('revision','phase','input_snapshot_hash','outline','design','confirmation_selection','confirmed_at')}|{'options':a.design.get('directions',a.design.get('options',[]))}
def version_dto(v):
    slides=[{'number':i,'title':slide.get('title',''),'summary':slide.get('summary',''),'preview_url':f'/api/v1/versions/{v.id}/slides/{i}/preview'} for i,slide in enumerate(v.manifest.get('slides',[]),1)]
    value={'id':v.id,'project_id':v.project_id,'parent_version_id':v.parent_version_id,'status':v.status,'created_at':v.created_at,'slides':slides,'download_url':f'/api/v1/versions/{v.id}/download','preview_notice':'Preview may differ from Microsoft PowerPoint.','can_revise':v.status=='published'}
    if v.status=='review':
        allowed={'independent_export_build_contract','independent_visual_review','powerpoint_open_edit_save_reopen','postflight_warnings_review'}
        value.update(validation_status='gate_pending',publication_authorization=False,required_pending_gates=[gate for gate in v.manifest.get('required_pending_gates',[]) if gate in allowed])
    return value
def project_dto(p,s,detail=False):
    d={k:getattr(p,k) for k in ('id','title','revision','stage','active_job_id','confirmed_plan_revision','latest_version_id','external_model_ack','deletion_requested')}
    d.update(p.settings)
    d['input_snapshot_hash']=Repository(s).snapshot(p)['hash']
    if detail:
        d['assets']=[asset_dto(a) for a in s.scalars(select(Asset).where(Asset.project_id==p.id,Asset.removed==False))]
        d['plans']=[plan_dto(a) for a in s.scalars(select(Plan).where(Plan.project_id==p.id).order_by(Plan.revision))]
    return d
def create_app(settings:Settings|None=None,engine=None):
    settings=settings or Settings.env()
    if engine is None:
        if not settings.database_url.startswith('postgresql'):raise RuntimeError('PostgreSQL required')
        engine=create_engine(settings.database_url,pool_pre_ping=True)
    app=FastAPI(title='PPT Service',version='1.0.0')
    app.add_middleware(BodyLimitMiddleware)
    app.state.settings=settings;app.state.engine=engine
    app.state.sessions=sessionmaker(engine,expire_on_commit=False)
    app.state.storage=Storage(settings.data_root)
    app.state.jwks=jwt.PyJWKClient(settings.jwks_url,cache_keys=True,lifespan=300) if settings.jwks_url.startswith('https://') else None
    def session():
        with app.state.sessions() as s:
            with s.begin():yield s
    @app.middleware('http')
    async def security_headers(request,call_next):
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff';response.headers['Cache-Control']='no-store'
        response.headers['Content-Security-Policy']="default-src 'none'; frame-ancestors 'none'"
        return response
    @app.get('/healthz')
    def health():return {'status':'ok'}
    @app.get('/api/v1/me')
    def me(request:Request,response:Response,owner=Depends(authenticate),s=Depends(session)):
        # Other tabs share this cookie but retain their own in-memory header.
        # Reading a profile must not invalidate those authenticated documents.
        token=request.cookies.get('ppt_csrf','')
        if not CSRF_TOKEN_RE.fullmatch(token):token=secrets.token_urlsafe(32)
        response.set_cookie('ppt_csrf',token,secure=True,httponly=False,samesite='strict',path='/')
        q=s.get(UserQuota,(owner.issuer,owner.sub))
        return {'issuer':owner.issuer,'sub':owner.sub,'csrf_token':token,'quota':{'enabled':bool(q and q.enabled),'period':q.period if q else None,'generation_limit':q.generation_limit if q else None,'revision_limit':q.revision_limit if q else None}}
    @app.post('/api/v1/projects',status_code=201)
    def create(body:ProjectCreate,owner=Depends(authenticate),s=Depends(session)):
        data=body.model_dump();title=data.pop('title');ack=data.pop('external_model_ack')
        p=Project(issuer=owner.issuer,subject=owner.sub,title=title,external_model_ack=ack,settings=data);s.add(p);s.flush();return project_dto(p,s)
    @app.get('/api/v1/projects')
    def projects(owner=Depends(authenticate),s=Depends(session)):
        return [project_dto(p,s) for p in s.scalars(select(Project).where(Project.issuer==owner.issuer,Project.subject==owner.sub,Project.deletion_requested==False).order_by(Project.created_at.desc()))]
    @app.get('/api/v1/projects/{pid}')
    def project(pid:str,owner=Depends(authenticate),s=Depends(session)):
        return project_dto(Repository(s).project(pid,owner),s,True)
    @app.patch('/api/v1/projects/{pid}')
    def patch(pid:str,body:ProjectPatch,owner=Depends(authenticate),s=Depends(session)):
        r=Repository(s);p=r.project(pid,owner,True);r.revision(p,body.expected_revision)
        if p.active_job_id:fail('project_busy')
        data=body.model_dump(exclude={'expected_revision'});p.title=data.pop('title');p.external_model_ack=data.pop('external_model_ack');p.settings=data;p.revision+=1;p.confirmed_plan_revision=None;p.stage='draft'
        return project_dto(p,s)
    @app.post('/api/v1/projects/{pid}/assets',status_code=201)
    async def upload(pid:str,file:UploadFile=File(...),owner=Depends(authenticate),s=Depends(session)):
        r=Repository(s);r.admission_lock();p=r.project(pid,owner,True)
        if not p.external_model_ack:fail('external_model_ack_required',422)
        if p.active_job_id:fail('project_busy')
        count=s.scalar(select(func.count(Asset.id)).where(Asset.project_id==pid,Asset.removed==False))
        total=s.scalar(select(func.coalesce(func.sum(Asset.bytes),0)).where(Asset.project_id==pid))
        if count>=10:fail('project_file_limit',413)
        aid,key,size,h,mime=await app.state.storage.upload(pid,file)
        try:
            global_used=s.scalar(select(func.coalesce(func.sum(Asset.bytes),0)))
            if total+size>MAX_PROJECT or global_used+size>settings.max_storage_bytes:fail('storage_full',413)
            a=Asset(id=aid,project_id=pid,storage_key=key,display_name=(file.filename or 'asset').replace('\\','/').split('/')[-1][:200],sha256=h,media_type=mime,bytes=size)
            s.add(a);p.revision+=1;p.confirmed_plan_revision=None;p.stage='draft';s.flush();s.commit()
            return asset_dto(a)
        except BaseException:app.state.storage.remove_upload(key);raise
    @app.delete('/api/v1/projects/{pid}/assets/{aid}')
    def delete_asset(pid:str,aid:str,expected_revision:int,owner=Depends(authenticate),s=Depends(session)):
        r=Repository(s);p=r.project(pid,owner,True);r.revision(p,expected_revision)
        if p.active_job_id:fail('project_busy')
        a=s.scalar(select(Asset).where(Asset.id==aid,Asset.project_id==pid))
        if not a:fail('not_found',404)
        # Snapshot-referenced bytes remain until trusted project cleanup.
        a.removed=True;p.revision+=1;p.confirmed_plan_revision=None;p.stage='draft';return {'revision':p.revision}
    @app.post('/api/v1/projects/{pid}/plans',status_code=202)
    def plan(pid:str,body:PlanRequest,key:str|None=Header(None,alias='Idempotency-Key'),owner=Depends(authenticate),s=Depends(session)):
        r=Repository(s)
        def action():
            p=r.project(pid,owner,True);extra={'feedback':body.feedback}
            if body.phase=='plan_options':
                initial=s.scalar(select(Plan).where(Plan.project_id==pid,Plan.revision==p.confirmed_plan_revision,Plan.phase=='plan_initial',Plan.confirmed_at!=None))
                if not initial or initial.input_snapshot_hash!=r.snapshot(p)['hash']:fail('initial_confirmation_required')
                extra['checkpoint_id']=initial.checkpoint_id
                extra['confirmation_selection']=initial.confirmation_selection
            j=r.enqueue(p,owner,body.phase,body.expected_revision,extra);j.checkpoint_id=extra.get('checkpoint_id');p.confirmed_plan_revision=None
            return job_dto(j)
        return r.idempotent(owner,f'projects/{pid}/plans',key,body.model_dump(),action,
                            authorize=lambda:r.project(pid,owner,True))
    @app.post('/api/v1/projects/{pid}/plans/{revision}/confirm')
    def confirm(pid:str,revision:int,body:Confirm,key:str|None=Header(None,alias='Idempotency-Key'),owner=Depends(authenticate),s=Depends(session)):
        r=Repository(s)
        def action():
            p=r.project(pid,owner,True);r.revision(p,body.expected_revision)
            if p.active_job_id:fail('project_busy')
            plan=s.scalar(select(Plan).where(Plan.project_id==pid,Plan.revision==revision).with_for_update())
            latest=s.scalar(select(func.max(Plan.revision)).where(Plan.project_id==pid))
            if not plan or latest!=revision:fail('stale_plan_revision')
            if plan.input_snapshot_hash!=body.input_snapshot_hash or plan.input_snapshot_hash!=r.snapshot(p)['hash']:fail('stale_input_snapshot')
            selection=body.selection
            if plan.phase=='plan_initial':
                if set(selection)-{'design_mode','template_id'}:fail('invalid_confirmation_selection',422)
                if selection.get('design_mode')!='free_design' or selection.get('template_id') is not None:fail('accepted_template_unavailable',422)
            else:
                if set(selection)-{'direction_id','page_plan','production_parameters'}:fail('invalid_confirmation_selection',422)
                directions=plan.design.get('directions',plan.design.get('options',[]))
                selected=next((d for d in directions if isinstance(d,dict) and d.get('id')==selection.get('direction_id')),None)
                if not selected:fail('unknown_direction',422)
                for field in ('page_plan','production_parameters'):
                    if field in selection and selection[field]!=selected.get(field,plan.design.get(field)):fail('replanning_required',409)
            if plan.confirmed_at and plan.confirmation_selection!=selection:fail('already_confirmed_different_selection')
            plan.confirmation_selection=selection
            plan.confirmed_at=now();p.confirmed_plan_revision=revision;p.revision+=1
            return {'project_id':pid,'revision':p.revision,'confirmed_plan_revision':revision,'phase':plan.phase,'next_action':'plan_options' if plan.phase=='plan_initial' else 'generate'}
        return r.idempotent(owner,f'projects/{pid}/plans/{revision}/confirm',key,body.model_dump(),action,
                            authorize=lambda:r.project(pid,owner,True))
    @app.post('/api/v1/projects/{pid}/generations',status_code=202)
    def generate(pid:str,body:Generate,key:str|None=Header(None,alias='Idempotency-Key'),owner=Depends(authenticate),s=Depends(session)):
        r=Repository(s)
        def action():
            p=r.project(pid,owner,True)
            plan=s.scalar(select(Plan).where(Plan.project_id==pid,Plan.revision==body.plan_revision,Plan.phase=='plan_options',Plan.confirmed_at!=None))
            if not plan or p.confirmed_plan_revision!=body.plan_revision:fail('options_confirmation_required')
            if plan.input_snapshot_hash!=r.snapshot(p)['hash']:fail('stale_input_snapshot')
            j=r.enqueue(p,owner,'prepare',body.expected_revision,{'plan_revision':plan.revision,'checkpoint_id':plan.checkpoint_id,'confirmation_selection':plan.confirmation_selection});j.checkpoint_id=plan.checkpoint_id
            return job_dto(j)
        return r.idempotent(owner,f'projects/{pid}/generations',key,body.model_dump(),action,
                            authorize=lambda:r.project(pid,owner,True))
    @app.post('/api/v1/projects/{pid}/revisions',status_code=202)
    def revise(pid:str,body:Revise,key:str|None=Header(None,alias='Idempotency-Key'),owner=Depends(authenticate),s=Depends(session)):
        r=Repository(s)
        def action():
            p=r.project(pid,owner,True);v=r.version(body.base_version_id,owner)
            if v.project_id!=pid:fail('not_found',404)
            if v.status!='published':fail('review_version_not_revision_base')
            if any(n<1 or n>len(v.manifest.get('slides',[])) for n in body.pages):fail('invalid_slide_number',422)
            j=r.enqueue(p,owner,'revise',body.expected_revision,{'feedback':body.feedback,'pages':body.pages,'base_version_id':v.id,'checkpoint_id':v.checkpoint_id});j.base_version_id=v.id;j.checkpoint_id=v.checkpoint_id
            return job_dto(j)
        return r.idempotent(owner,f'projects/{pid}/revisions',key,body.model_dump(),action,
                            authorize=lambda:r.project(pid,owner,True))
    @app.get('/api/v1/jobs/{jid}')
    def job(jid:str,owner=Depends(authenticate),s=Depends(session)):return job_dto(Repository(s).job(jid,owner))
    @app.post('/api/v1/jobs/{jid}/cancel')
    def cancel(jid:str,key:str|None=Header(None,alias='Idempotency-Key'),owner=Depends(authenticate),s=Depends(session)):
        r=Repository(s)
        return r.idempotent(owner,f'jobs/{jid}/cancel',key,{},lambda:r.cancel(r.job(jid,owner,True)),
                            authorize=lambda:r.job(jid,owner,True))
    @app.delete('/api/v1/projects/{pid}',status_code=202)
    def delete(pid:str,owner=Depends(authenticate),s=Depends(session)):
        r=Repository(s);r.admission_lock();p=r.project(pid,owner,deleted=True)
        prior_job=p.active_job_id
        active=s.get(Job,prior_job,with_for_update=True) if prior_job else None
        p=r.project(pid,owner,True,True)
        if p.active_job_id!=prior_job:fail('project_changed_retry')
        p.deletion_requested=True;p.revision+=1
        if active:r.cancel(active)
        else:p.cleanup_ready=True
        return {'project_id':pid,'status':'deleting','cleanup_ready':p.cleanup_ready}
    @app.get('/api/v1/projects/{pid}/versions')
    def versions(pid:str,owner=Depends(authenticate),s=Depends(session)):
        Repository(s).project(pid,owner)
        return [version_dto(v) for v in s.scalars(select(Version).where(Version.project_id==pid,Version.status.in_(('published','review'))).order_by(Version.created_at))]
    def blob(v,key,mime,filename=None):
        if not isinstance(key,str) or not key.startswith(v.artifact_root+'/'):fail('artifact_unavailable',404)
        try:stream=app.state.storage.open(key,200*1024**2)
        except (OSError,ValueError):fail('artifact_unavailable',404)
        signature=stream.read(8);stream.seek(0)
        if (mime=='image/png' and signature!=b'\x89PNG\r\n\x1a\n') or (filename and not signature.startswith(b'PK')):
            stream.close();fail('artifact_unavailable',404)
        headers={'Content-Disposition':'attachment; filename="presentation.pptx"'} if filename else {}
        def chunks():
            try:
                while data:=stream.read(65536):yield data
            finally:stream.close()
        return StreamingResponse(chunks(),media_type=mime,headers=headers)
    @app.get('/api/v1/versions/{vid}/download')
    def download(vid:str,owner=Depends(authenticate),s=Depends(session)):
        v=Repository(s).version(vid,owner);key=v.manifest.get('pptx_key')
        if not key or not key.endswith('.pptx'):fail('artifact_unavailable',404)
        return blob(v,key,'application/vnd.openxmlformats-officedocument.presentationml.presentation',True)
    @app.get('/api/v1/versions/{vid}/slides/{number}/preview')
    def preview(vid:str,number:int,owner=Depends(authenticate),s=Depends(session)):
        v=Repository(s).version(vid,owner);slides=v.manifest.get('slides',[])
        if number<1 or number>len(slides):fail('not_found',404)
        key=slides[number-1].get('preview_key')
        if not key or not key.endswith('.png'):fail('artifact_unavailable',404)
        return blob(v,key,'image/png')
    @app.get('/api/v1/jobs/{jid}/events')
    def events(jid:str,request:Request,last_event_id:str|None=Header(None,alias='Last-Event-ID'),owner=Depends(authenticate),s=Depends(session)):
        r=Repository(s);j=r.job(jid,owner)
        try:cursor=int(last_event_id or request.query_params.get('after','0'))
        except ValueError:fail('invalid_event_cursor',400)
        if cursor<j.event_floor:raise HTTPException(410,detail={'code':'event_cursor_expired','action':'refresh_snapshot'})
        if cursor<0 or cursor>j.event_cursor:fail('invalid_event_cursor',409)
        s.commit()
        async def stream():
            nonlocal cursor
            # Separate short transactions: never hold DB connection for stream lifetime.
            while not await request.is_disconnected():
                with app.state.sessions.begin() as ss:
                    rr=Repository(ss)
                    try:jj=rr.job(jid,owner)
                    except HTTPException:return
                    snapshot_required=cursor<jj.event_floor
                    rows=[] if snapshot_required else ss.scalars(select(JobEvent).where(JobEvent.job_id==jid,JobEvent.seq>cursor).order_by(JobEvent.seq).limit(100)).all()
                    data=[(e.seq,e.type,e.payload) for e in rows]
                    done=jj.status in TERMINAL;end_cursor=jj.event_cursor
                if snapshot_required:
                    yield 'event: snapshot.required\ndata: {"action":"refresh_snapshot"}\n\n';return
                for seq,type,payload in data:
                    cursor=seq;yield f'id: {seq}\nevent: {type}\ndata: '+json.dumps({'type':type,'seq':seq,'payload':payload},ensure_ascii=False)+'\n\n'
                # Terminal status does not mean the first event page is the last.
                if done and cursor>=end_cursor:return
                if done and not data:
                    yield 'event: snapshot.required\ndata: {"action":"refresh_snapshot"}\n\n';return
                # Drain persisted backlog immediately, rechecking access each page.
                if data:continue
                yield ': keepalive\n\n'
                await asyncio.sleep(1)
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no'})
    return app
