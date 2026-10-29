from __future__ import annotations
import hashlib,json,os
from datetime import datetime,timedelta,timezone
from fastapi import HTTPException
from sqlalchemy import select,func,text
from .models import *
from .phase_limits import phase_timeout_seconds
ACTIVE=('queued','starting','running','collecting','validating','cancelling')
TERMINAL=('succeeded','failed','cancelled','interrupted')
def fail(code,status=409): raise HTTPException(status,detail={'code':code})
def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def _iso_time(value):
    if not isinstance(value,datetime):return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()

def job_dto(j):
    try:budget=phase_timeout_seconds(j.kind)
    except ValueError:budget=None
    return {k:getattr(j,k) for k in ('id','project_id','kind','status','stage','event_cursor','attempt','error_code','base_version_id')}|{
        'job_id':j.id,'created_at':_iso_time(j.created_at),'deadline':_iso_time(j.deadline),
        # Current phase configuration, not a completion estimate or a historical
        # claim timestamp. The persisted deadline is never recalculated here.
        'phase_timeout_seconds':budget}
EVENT_FIELDS={'job.queued':{'stage'},'job.started':{'stage'},'stage.changed':{'stage'},'plan.ready':{'revision','phase'},'artifact.ready':{'version_id'},'job.succeeded':set(),'job.failed':{'error_code'},'job.cancelled':set(),'job.cancelling':set()}
class Repository:
    """All methods run in caller's session.begin(); control methods are trusted-only.

    Global lock ordering: admission advisory lock -> quota -> project -> job;
    control: execution slot -> job -> project. No public worker callback routes.
    Never reclaim an expired slot without external stop proof.
    """
    def __init__(self,s): self.s=s
    def project(self,pid,owner,lock=False,deleted=False):
        q=select(Project).where(Project.id==pid,Project.issuer==owner.issuer,Project.subject==owner.sub)
        if not deleted:q=q.where(Project.deletion_requested==False)
        if lock:q=q.with_for_update()
        p=self.s.scalar(q)
        if not p:fail('not_found',404)
        return p
    def job(self,jid,owner,lock=False):
        q=select(Job).join(Project,Project.id==Job.project_id).where(Job.id==jid,Project.issuer==owner.issuer,Project.subject==owner.sub,Project.deletion_requested==False)
        if lock:q=q.with_for_update(of=Job)
        j=self.s.scalar(q)
        if not j:fail('not_found',404)
        return j
    def version(self,vid,owner):
        v=self.s.scalar(select(Version).join(Project).where(Version.id==vid,Project.issuer==owner.issuer,Project.subject==owner.sub,Project.deletion_requested==False,Version.status.in_(('published','review'))))
        if not v:fail('not_found',404)
        return v
    def revision(self,p,expected):
        if p.revision!=expected:fail('stale_project_revision')
    def snapshot(self,p):
        assets=self.s.scalars(select(Asset).where(Asset.project_id==p.id,Asset.removed==False).order_by(Asset.id)).all()
        data={'project_id':p.id,'settings':p.settings,'title':p.title,'assets':[{'id':a.id,'storage_key':a.storage_key,'sha256':a.sha256,'media_type':a.media_type,'bytes':a.bytes} for a in assets]}
        return data|{'hash':digest(data)}
    def admission_lock(self):
        if self.s.bind.dialect.name=='postgresql':self.s.execute(text('SELECT pg_advisory_xact_lock(73114001)'))
    def quota(self,owner):
        q=self.s.get(UserQuota,(owner.issuer,owner.sub),with_for_update=True)
        if not q:
            q=UserQuota(issuer=owner.issuer,subject=owner.sub);self.s.add(q);self.s.flush()
        return q
    def idempotent(self,owner,route,key,body,action,*,authorize):
        """Recheck the current route resource before returning any cached reply.

        ``authorize`` checks ownership/deletion under the normal admission locks;
        it must not repeat the action's revision checks or side effects. Those
        checks belong to the first execution, so a valid retry keeps its original
        response even after that execution advanced the project revision.
        """
        if not key or len(key)>128:fail('idempotency_key_required',400)
        self.admission_lock()
        self.quota(owner) # serializes identity on every action, even across projects
        authorize()
        identity=(owner.issuer,owner.sub,route,key)
        old=self.s.get(IdempotencyKey,identity)
        h=digest(body)
        if old:
            if old.request_hash!=h:fail('idempotency_conflict')
            return old.response
        result=action()
        self.s.add(IdempotencyKey(issuer=owner.issuer,subject=owner.sub,route=route,key=key,request_hash=h,response=result,expires_at=now()+timedelta(days=7)))
        return result
    def event(self,j,type,payload=None):
        if type not in EVENT_FIELDS:raise ValueError('unsupported business event')
        payload=payload or {}
        clean={k:v for k,v in payload.items() if k in EVENT_FIELDS[type]}
        # Values are server business identifiers, never free-form Worker messages.
        if 'stage' in clean and clean['stage'] not in ('plan_initial','plan_options','prepare','generate','revise','parse','collecting','validating','execution_queued','execution_starting','execution_running'):raise ValueError('invalid stage')
        if 'error_code' in clean and clean['error_code'] not in ('parse_failed','model_unavailable','execution_timeout','quality_gate_failed','render_failed','export_failed','execution_interrupted','storage_full'):raise ValueError('invalid error')
        if 'revision' in clean and (not isinstance(clean['revision'],int) or isinstance(clean['revision'],bool) or clean['revision']<1):raise ValueError('invalid revision')
        for k in ('version_id',):
            if k in clean:uuid.UUID(clean[k])
        if 'phase' in clean and clean['phase'] not in ('plan_initial','plan_options'):raise ValueError('invalid phase')
        j.event_cursor+=1
        self.s.add(JobEvent(job_id=j.id,seq=j.event_cursor,type=type,payload=clean))
    def require_checkpoint_backend(self,p,kind,checkpoint_id):
        """Reject native recovery only for new intents after the service cutover.

        Keep this inside the idempotent action, never its authorization callback:
        an existing request must continue returning its original queued job.
        Read/download routes and restarting initial planning do not enter here.
        Full portable checkpoint validation remains the controller's job.
        """
        if os.getenv('PPT_EXECUTION_BACKEND')!='agent-service' or kind not in ('plan_options','prepare','revise'):return
        checkpoint=self.s.get(Checkpoint,checkpoint_id) if checkpoint_id else None
        if checkpoint and (checkpoint.project_id,checkpoint.issuer,checkpoint.subject)!=(p.id,p.issuer,p.subject):fail('not_found',404)
        manifest=checkpoint.manifest if checkpoint else None
        if (not isinstance(manifest,dict) or manifest.get('checkpoint_format')!='ppt-agent-artifacts-v1'
                or not isinstance(manifest.get('agent_checkpoint'),dict)
                or not isinstance(manifest.get('service_checkpoint_ref'),dict)):
            fail('legacy_checkpoint_requires_replan')
    def enqueue(self,p,owner,kind,expected,extra=None):
        self.revision(p,expected)
        if not p.external_model_ack:fail('external_model_ack_required',422)
        if p.active_job_id:fail('project_busy')
        self.require_checkpoint_backend(p,kind,(extra or {}).get('checkpoint_id'))
        waiting=self.s.scalar(select(func.count()).select_from(Job).where(Job.status=='queued'))
        own=self.s.scalar(select(func.count()).select_from(Job).where(Job.status=='queued',Job.issuer==owner.issuer,Job.subject==owner.sub))
        if waiting>=20 or own>=2:fail('queue_full',429)
        q=self.quota(owner)
        category='revision' if kind=='revise' else 'generation' if kind=='prepare' else None
        if category:
            limit=getattr(q,category+'_limit'); count=getattr(q,category+'_count')
            if q.enabled and (not q.period or limit is None):fail('quota_configuration_incomplete',503)
            if q.enabled and count>=limit:fail('quota_exceeded',429)
            setattr(q,category+'_count',count+1)
        snap=self.snapshot(p)
        if extra:snap=snap|extra
        j=Job(project_id=p.id,issuer=p.issuer,subject=p.subject,kind=kind,stage=kind,input_snapshot=snap)
        self.s.add(j);self.s.flush()
        p.active_job_id=j.id;p.stage='planning' if kind.startswith('plan') else 'revising' if kind=='revise' else 'generating';p.revision+=1
        self.event(j,'job.queued',{'stage':kind})
        return j
    def cancel(self,j):
        if j.status=='queued':
            j.status='cancelled';j.stopped=True
            p=self.s.get(Project,j.project_id,with_for_update=True);p.active_job_id=None
            p.stage='ready' if p.latest_version_id else 'draft'
            p.cleanup_ready=p.deletion_requested
            self.event(j,'job.cancelled')
        elif j.status in ACTIVE and j.status!='cancelling':
            j.status='cancelling';self.event(j,'job.cancelling')
        return job_dto(j)
    # Trusted controller integration. Slot must be initialized by migration.
    def claim_next(self,lease_seconds=60):
        slot=self.s.get(ExecutionSlot,1,with_for_update=True)
        if slot.job_id:return None
        j=self.s.scalar(select(Job).join(Project).where(Job.status=='queued',Project.deletion_requested==False).order_by(Job.created_at,Job.id).with_for_update(of=Job,skip_locked=True).limit(1))
        if not j:return None
        slot.fencing_token+=1;slot.job_id=j.id;j.fencing_token=slot.fencing_token
        j.status='starting';j.lease_until=now()+timedelta(seconds=lease_seconds)
        j.deadline=now()+timedelta(seconds=phase_timeout_seconds(j.kind))
        self.event(j,'job.started',{'stage':j.stage});return j
    def fenced(self,jid,attempt,token,allow_cancel=False):
        slot=self.s.get(ExecutionSlot,1,with_for_update=True)
        j=self.s.get(Job,jid,with_for_update=True)
        if not j or slot.job_id!=jid or j.attempt!=attempt or j.fencing_token!=token or slot.fencing_token!=token:fail('stale_execution')
        p=self.s.get(Project,j.project_id,with_for_update=True)
        if not allow_cancel and (j.status=='cancelling' or p.deletion_requested or j.status not in ACTIVE):fail('execution_revoked')
        return j,p,slot
    def heartbeat(self,jid,attempt,token,lease_seconds=60):
        j,_,_=self.fenced(jid,attempt,token,allow_cancel=True)
        j.lease_until=now()+timedelta(seconds=lease_seconds);return j
    def attach_execution(self,jid,attempt,token,*,job_uid,session_id=None,run_id=None):
        j,_,_=self.fenced(jid,attempt,token)
        if j.job_uid and j.job_uid!=job_uid:fail('execution_already_attached')
        j.job_uid=job_uid
        if session_id:j.session_id=session_id
        if run_id:j.run_id=run_id
        j.status='running';return j
    def prove_stopped(self,jid,attempt,token,*,job_uid,all_writers_stopped):
        j,p,slot=self.fenced(jid,attempt,token,allow_cancel=True)
        if not all_writers_stopped or (j.job_uid and j.job_uid!=job_uid):fail('stop_proof_required')
        j.stopped=True
        if j.status=='cancelling' or p.deletion_requested:
            j.status='cancelled';p.active_job_id=None;p.cleanup_ready=p.deletion_requested;slot.job_id=None
            self.event(j,'job.cancelled')
        else:j.status='validating'
        return j
    def _checkpoint(self,p,key,manifest):
        # key must identify a trusted immutable private snapshot, never worker staging
        if not key.startswith('projects/'+p.id+'/checkpoints/'):raise ValueError('checkpoint namespace')
        c=Checkpoint(project_id=p.id,issuer=p.issuer,subject=p.subject,storage_key=key,manifest=manifest)
        self.s.add(c);self.s.flush();return c
    def accept_plan(self,jid,attempt,token,*,outline,design,checkpoint_key,checkpoint_manifest,independently_validated=False):
        j,p,slot=self.fenced(jid,attempt,token)
        if not independently_validated or not j.stopped or j.status!='validating' or j.kind not in ('plan_initial','plan_options'):fail('trusted_validation_required')
        c=self._checkpoint(p,checkpoint_key,checkpoint_manifest)
        n=(self.s.scalar(select(func.max(Plan.revision)).where(Plan.project_id==p.id)) or 0)+1
        plan=Plan(project_id=p.id,revision=n,phase=j.kind,input_snapshot_hash=j.input_snapshot['hash'],outline=outline,design=design,user_feedback=j.input_snapshot.get('feedback',''),checkpoint_id=c.id)
        # A new candidate plan is unconfirmed. Keep the prior Stage1 pointer
        # during Stage2 execution/failure, but never transfer it to this plan.
        self.s.add(plan);p.stage='awaiting_confirmation';p.active_job_id=None;p.confirmed_plan_revision=None;p.revision+=1
        j.status='succeeded';slot.job_id=None;self.event(j,'plan.ready',{'revision':n,'phase':j.kind});self.event(j,'job.succeeded');return plan
    def accept_prepared(self,jid,attempt,token,*,checkpoint_key,checkpoint_manifest,independently_validated=False):
        j,p,slot=self.fenced(jid,attempt,token)
        if not independently_validated or not j.stopped or j.status!='validating' or j.kind!='prepare':fail('trusted_validation_required')
        c=self._checkpoint(p,checkpoint_key,checkpoint_manifest)
        # New immutable job, same admission/quota reservation, no third user gate.
        j.status='succeeded';self.event(j,'job.succeeded');self.s.flush()
        n=Job(project_id=p.id,issuer=p.issuer,subject=p.subject,kind='generate',stage='generate',input_snapshot=j.input_snapshot,checkpoint_id=c.id)
        self.s.add(n);self.s.flush();p.active_job_id=n.id;slot.job_id=None
        self.event(n,'job.queued',{'stage':'generate'});return n
    def publish_version(self,jid,attempt,token,*,manifest,artifact_root,checkpoint_key,checkpoint_manifest,independently_validated=False):
        j,p,slot=self.fenced(jid,attempt,token)
        # A review-only branch can produce another review draft, never a
        # published version, even if an internal caller supplies a pass flag.
        if 'review_base' in j.input_snapshot:fail('review_revision_not_publishable')
        if not independently_validated or not j.stopped or j.status!='validating' or j.kind not in ('generate','revise'):fail('trusted_validation_required')
        if not artifact_root.startswith('projects/'+p.id+'/versions/'):raise ValueError('version namespace')
        c=self._checkpoint(p,checkpoint_key,checkpoint_manifest)
        v=Version(project_id=p.id,job_id=j.id,issuer=p.issuer,subject=p.subject,parent_version_id=j.base_version_id,checkpoint_id=c.id,manifest=manifest,artifact_root=artifact_root)
        self.s.add(v);self.s.flush();p.latest_version_id=v.id;p.active_job_id=None;p.stage='ready';p.revision+=1;j.status='succeeded';slot.job_id=None
        self.event(j,'artifact.ready',{'version_id':v.id});self.event(j,'job.succeeded');return v
    def save_review_version(self,jid,attempt,token,*,version_id,manifest,artifact_root,checkpoint_key,checkpoint_manifest,review_validated=False):
        """Trusted draft persistence only; never promotes a revision baseline."""
        j,p,slot=self.fenced(jid,attempt,token)
        if not review_validated or not j.stopped or j.status!='validating' or j.kind not in ('generate','revise'):fail('trusted_validation_required')
        if str(uuid.UUID(version_id))!=version_id or artifact_root!=f'projects/{p.id}/versions/{version_id}/review':raise ValueError('review namespace')
        if manifest.get('status')!='review' or manifest.get('eligible_as_revision_base') is not False or manifest.get('publication_authorization') is not False:raise ValueError('review contract')
        if checkpoint_manifest.get('status')!='review' or checkpoint_manifest.get('eligible_as_revision_base') is not False:raise ValueError('review checkpoint contract')
        c=self._checkpoint(p,checkpoint_key,checkpoint_manifest)
        v=Version(id=version_id,project_id=p.id,job_id=j.id,issuer=p.issuer,subject=p.subject,parent_version_id=j.base_version_id,checkpoint_id=c.id,manifest=manifest,artifact_root=artifact_root,status='review')
        self.s.add(v);self.s.flush();p.active_job_id=None;p.stage='awaiting_review';p.revision+=1
        j.status='succeeded';j.stage='review';slot.job_id=None
        self.event(j,'artifact.ready',{'version_id':v.id});self.event(j,'job.succeeded');return v
    def finish_failed(self,jid,attempt,token,*,error_code):
        j,p,slot=self.fenced(jid,attempt,token,allow_cancel=True)
        if not j.stopped:fail('stop_proof_required')
        if j.status=='cancelling' or p.deletion_requested:fail('cancellation_pending')
        self.event(j,'job.failed',{'error_code':error_code});j.error_code=error_code;j.status='failed';p.active_job_id=None;p.stage='ready' if p.latest_version_id else 'draft';slot.job_id=None
    def record_usage(self,jid,attempt,*,phase,outcome,model=None,provider=None,provider_request_id=None,input_tokens=None,output_tokens=None,reported_cost=None,currency=None):
        j=self.s.get(Job,jid)
        if not j or attempt>j.attempt:fail('unknown_attempt')
        if provider_request_id:
            old=self.s.scalar(select(UsageRecord).where(UsageRecord.job_id==jid,UsageRecord.attempt==attempt,UsageRecord.provider==provider,UsageRecord.provider_request_id==provider_request_id))
            if old:return old
        u=UsageRecord(issuer=j.issuer,subject=j.subject,project_id=j.project_id,job_id=jid,attempt=attempt,phase=phase,outcome=outcome,model=model,provider=provider,provider_request_id=provider_request_id,input_tokens=input_tokens,output_tokens=output_tokens,reported_cost=str(reported_cost) if reported_cost is not None else None,currency=currency,cost_status='reported' if reported_cost is not None else 'unknown')
        self.s.add(u);return u
