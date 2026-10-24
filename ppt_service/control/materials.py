"""No-model material parsing with durable asset leases and fenced completion.

Runs on the trusted Docker host, independently of model-job execution flags.
The public API never imports Office parsers. All actual parsing uses Sandbox.parse.
"""
from __future__ import annotations
import argparse,contextlib,hashlib,json,os,re,stat,time,uuid
from dataclasses import dataclass
from datetime import timedelta,timezone
from pathlib import Path
from sqlalchemy import create_engine,select,exists,and_,or_
from sqlalchemy.orm import sessionmaker
from ppt_service.server.models import Asset,Project,now
from .execution import RuntimeConfig,ControlError
from .staging import EXT,copy_file,atomic_json,make_readonly
from .pods import Docker,DockerSandbox

SAFE_ERRORS=frozenset(('parse_failed','source_unavailable','source_hash_mismatch','parse_result_mismatch','parse_interrupted','parse_aborted'))
def future(value):
    if value is None:return False
    if value.tzinfo is None:value=value.replace(tzinfo=timezone.utc)
    return value>now()
@dataclass(frozen=True)
class MaterialClaim:
    asset_id:str
    project_id:str
    token:str
    scope_uid:str
    attempt:int
    storage_key:str
    sha256:str
    media_type:str
    bytes:int
    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps({'id':self.asset_id,'project_id':self.project_id,'storage_key':self.storage_key,'sha256':self.sha256,'media_type':self.media_type,'bytes':self.bytes},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    @property
    def filename(self):return self.asset_id+EXT[self.media_type]
    def matches(self,a):
        return (a.id,a.project_id,a.parse_token,a.parse_scope_uid,a.parse_attempt,a.storage_key,a.sha256,a.media_type,a.bytes)==(self.asset_id,self.project_id,self.token,self.scope_uid,self.attempt,self.storage_key,self.sha256,self.media_type,self.bytes)

def claim_value(a):return MaterialClaim(a.id,a.project_id,a.parse_token,a.parse_scope_uid,a.parse_attempt,a.storage_key,a.sha256,a.media_type,a.bytes)

class ParseScopes:
    """Only inspect/stop the exact asset parser UID; never starts a proxy/model."""
    def __init__(self,docker=None):self.docker=docker or Docker()
    def prove_stopped(self,uid):
        ids=self.docker.ids(uid)
        for cid in ids:
            state=self.docker.inspect(cid)
            if state is None or state.get('Running') or state.get('Pid',0)!=0:return False
        return True
    def stop_and_prove(self,uid):
        for cid in self.docker.ids(uid):
            self.docker.call('stop','--time','5',cid,timeout=15,check=False)
            state=self.docker.inspect(cid)
            if state and state.get('Running'):self.docker.call('kill',cid,check=False)
        if not self.prove_stopped(uid):return False
        ids=self.docker.ids(uid)
        if ids:self.docker.call('rm',*ids)
        return self.prove_stopped(uid)

class MaterialParser:
    def __init__(self,sessions,config:RuntimeConfig,sandbox,scopes=None,*,lease_seconds=90,max_attempts=2,project_ids=()):
        if lease_seconds<config.parse_timeout+30 or not 1<=max_attempts<=5:raise ControlError('invalid_parse_configuration')
        self.sessions=sessions;self.config=config;self.sandbox=sandbox;self.scopes=scopes or ParseScopes();self.lease_seconds=lease_seconds;self.max_attempts=max_attempts
        self.project_ids=tuple(str(uuid.UUID(value)) for value in project_ids)
        self.root=config.data_root.absolute();self.root.mkdir(parents=True,exist_ok=True)
    @contextlib.contextmanager
    def scope_lock(self,asset_id):
        """Shared-filesystem flock prevents a paused old launcher starting late.

        Recovery never requeues while any old executor still holds this lock,
        even after the DB lease expires. The lock is outside project cleanup.
        """
        import fcntl
        name=str(uuid.UUID(asset_id));folder=self.root/'material-locks';folder.mkdir(exist_ok=True)
        fd=os.open(folder/name,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:os.close(fd);raise ControlError('invalid_material_lock')
        acquired=False
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);acquired=True
            except BlockingIOError:pass
            yield acquired
        finally:
            if acquired:fcntl.flock(fd,fcntl.LOCK_UN)
            os.close(fd)
    def claim(self):
        with self.sessions.begin() as s:
            eligible=exists(select(Asset.id).where(Asset.project_id==Project.id,Asset.parse_status=='queued',Asset.removed==False,Asset.parse_attempt<self.max_attempts))
            # Match the API's project -> asset lock order. SKIP LOCKED prevents
            # another parser blocking or double-claiming the same asset.
            p=s.scalar(select(Project).where(Project.deletion_requested==False,Project.active_job_id==None,eligible,Project.id.in_(self.project_ids) if self.project_ids else True).order_by(Project.created_at,Project.id).with_for_update(skip_locked=True).limit(1))
            if not p:return None
            a=s.scalar(select(Asset).where(Asset.project_id==p.id,Asset.parse_status=='queued',Asset.removed==False,Asset.parse_attempt<self.max_attempts).order_by(Asset.id).with_for_update(skip_locked=True).limit(1))
            if not a:return None
            token=str(uuid.uuid4());a.parse_attempt+=1;a.parse_token=token;a.parse_scope_uid=str(uuid.uuid5(uuid.NAMESPACE_URL,'ppt-material:'+a.id+':'+token));a.parse_status='parsing';a.parse_lease_until=now()+timedelta(seconds=self.lease_seconds);a.parse_error=None;a.text_summary=None
            # The durable no-writer scope must exist before the DB claim can
            # commit. Recovery after a crash between claim and execute can then
            # prove that no Pod was ever submitted rather than wedging forever.
            if hasattr(self.scopes,'ensure_scope'):
                self.scopes.ensure_scope(a.parse_scope_uid)
            return claim_value(a)
    def _locked(self,s,c):
        p=s.scalar(select(Project).where(Project.id==c.project_id).with_for_update())
        a=s.scalar(select(Asset).where(Asset.id==c.asset_id).with_for_update())
        return p,a
    def _eligible(self,p,a,c):return bool(p and a and not p.deletion_requested and not a.removed and a.parse_status=='parsing' and future(a.parse_lease_until) and c.matches(a))
    def live(self,c):
        with self.sessions.begin() as s:
            p,a=self._locked(s,c);return self._eligible(p,a,c)
    def complete(self,c,result=None,*,error=None):
        summary=None
        if error is None:
            try:summary=self._summary(c,result)
            except (ControlError,KeyError,TypeError,ValueError):error='parse_result_mismatch'
        if error is not None and error not in SAFE_ERRORS:raise ControlError('unsafe_parse_error')
        with self.sessions.begin() as s:
            p,a=self._locked(s,c)
            if not self._eligible(p,a,c):return False
            a.parse_status='failed' if error else 'ready';a.parse_error=error;a.text_summary=None if error else summary;a.parse_lease_until=None
            return a.parse_status
    @staticmethod
    def _summary(c,result):
        if not isinstance(result,dict) or result.get('input_snapshot_sha256')!=c.fingerprint:raise ControlError('parse_result_mismatch')
        rows=result.get('sources')
        if not isinstance(rows,list) or len(rows)!=1:raise ControlError('parse_result_mismatch')
        row=rows[0]
        if row.get('id')!=c.asset_id or row.get('sha256')!=c.sha256 or row.get('path')!=c.filename or row.get('parse_status')!='succeeded':raise ControlError('parse_result_mismatch')
        text=row.get('text')
        if not isinstance(text,str) or len(text)>500000:raise ControlError('parse_result_mismatch')
        if c.media_type.startswith('image/'):
            image=row.get('image',{});width=image.get('width');height=image.get('height')
            if type(width) is not int or type(height) is not int or not (0<width<=20000 and 0<height<=20000) or width*height>20_000_000:raise ControlError('parse_result_mismatch')
            return f'Image: {width} × {height} pixels.'
        if c.media_type=='application/pdf' and (type(row.get('pages')) is not int or not 1<=row['pages']<=150):raise ControlError('parse_result_mismatch')
        # This is a bounded plain-text excerpt, not generated content or HTML.
        text=re.sub(r'<[^>]{0,1000}>',' ',text)
        text=''.join(ch if ch.isprintable() or ch in '\n\t\r' else ' ' for ch in text)
        return ' '.join(text.split())[:2000]
    def recover_one(self):
        with self.sessions.begin() as s:
            expired=or_(Asset.parse_lease_until==None,Asset.parse_lease_until<=now())
            condition=or_(and_(Asset.parse_status=='parsing',or_(expired,Asset.removed==True,Project.deletion_requested==True)),and_(Asset.parse_status=='stopping',expired))
            p=s.scalar(select(Project).where(exists(select(Asset.id).where(Asset.project_id==Project.id,condition)),Project.id.in_(self.project_ids) if self.project_ids else True).order_by(Project.id).with_for_update(skip_locked=True).limit(1))
            if not p:return None
            a=s.scalar(select(Asset).join(Project,Project.id==Asset.project_id).where(Asset.project_id==p.id,condition).order_by(Asset.id).with_for_update(of=Asset,skip_locked=True).limit(1))
            if not a:return None
            c=claim_value(a);a.parse_status='stopping';a.parse_lease_until=now()+timedelta(seconds=self.lease_seconds)
        # Stopping revokes old completion first. A paused old launcher cannot pass
        # the shared flock and token check after this scope is reconciled.
        with self.scope_lock(c.asset_id) as acquired:
            if not acquired:return {'status':'waiting_for_old_parser','asset_id':c.asset_id}
            if not self.scopes.stop_and_prove(c.scope_uid):return {'status':'stopping','asset_id':c.asset_id}
            with self.sessions.begin() as s:
                p,a=self._locked(s,c)
                if not a or not c.matches(a) or a.parse_status!='stopping':return {'status':'stale','asset_id':c.asset_id}
                invalid=not p or p.deletion_requested or a.removed
                a.parse_status='failed' if invalid or a.parse_attempt>=self.max_attempts else 'queued'
                a.parse_error='parse_aborted' if invalid else 'parse_interrupted' if a.parse_status=='failed' else None
                a.text_summary=None;a.parse_lease_until=None;a.parse_token=None;a.parse_scope_uid=None
                return {'status':a.parse_status,'asset_id':c.asset_id,'reconciled':True}
    def execute(self,c):
        with self.scope_lock(c.asset_id) as acquired:
            if not acquired:return {'status':'waiting_for_old_parser','asset_id':c.asset_id}
            if not self.live(c):return {'status':'stale','asset_id':c.asset_id}
            result=None;error=None
            try:
                if hasattr(self.scopes,'ensure_scope'):
                    self.scopes.ensure_scope(c.scope_uid)
                expected=f'projects/{c.project_id}/assets/{c.asset_id}/original'
                if c.storage_key!=expected or c.media_type not in EXT:raise ControlError('source_unavailable')
                inputs=self.root/f'projects/{c.project_id}/material-parses/{c.asset_id}/{c.token}/inputs';inputs.mkdir(parents=True,exist_ok=False)
                copied=copy_file(self.root,c.storage_key,inputs/c.filename,25*1024**2,c.sha256)
                if copied['bytes']!=c.bytes:raise ControlError('source_hash_mismatch')
                inventory={'schema_version':1,'input_snapshot_sha256':c.fingerprint,'sources':[{'id':c.asset_id,'path':c.filename,'sha256':c.sha256,'bytes':c.bytes,'media_type':c.media_type}]}
                atomic_json(inputs/'inventory.json',inventory);make_readonly(inputs)
                if not self.live(c):return {'status':'stale','asset_id':c.asset_id}
                self.sandbox.scope_uid=c.scope_uid
                result=self.sandbox.parse(inputs,inventory)
            except (OSError,KeyError):error='source_unavailable'
            except Exception as exc:error=exc.code if isinstance(exc,ControlError) and exc.code in SAFE_ERRORS else 'parse_failed'
            # Even failure cannot release/requeue an attempt if Docker cannot
            # prove every parser process in its exact scope has stopped.
            if not self.scopes.prove_stopped(c.scope_uid):return {'status':'stopping','asset_id':c.asset_id}
            accepted=self.complete(c,result,error=error)
            return {'status':accepted if accepted else 'stale','asset_id':c.asset_id}
    def tick(self):
        recovered=self.recover_one()
        if recovered:return recovered
        c=self.claim()
        return self.execute(c) if c else {'status':'idle'}

def main():
    parser=argparse.ArgumentParser(description='Parse material locally in a network-none sandbox; never calls models.')
    parser.add_argument('--once',action='store_true');parser.add_argument('--project-id',action='append',default=[]);args=parser.parse_args()
    url=os.environ['DATABASE_URL']
    if not url.startswith('postgresql'):raise ControlError('postgresql_required')
    config=RuntimeConfig(data_root=Path(os.environ['DATA_ROOT']),code_root=Path(os.environ.get('PPT_CONTROL_CODE_ROOT',str(Path(__file__).parent))))
    engine=create_engine(url,pool_pre_ping=True);sessions=sessionmaker(engine,expire_on_commit=False)
    runner=MaterialParser(sessions,config,DockerSandbox(config),project_ids=args.project_id)
    try:
        while True:
            print(json.dumps(runner.tick()),flush=True)
            if args.once:return 0
            time.sleep(1)
    finally:engine.dispose()
if __name__=='__main__':raise SystemExit(main())
