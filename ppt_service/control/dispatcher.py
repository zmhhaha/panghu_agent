"""Durable single-slot controller; daemon CLI is disabled by default.

A replacement controller stops/reconciles expired executions, never retries a
possibly paid phase. All external calls occur outside database transactions.
"""
from __future__ import annotations
import argparse,json,os,time,uuid
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation
from pathlib import Path
from sqlalchemy import create_engine,select
from sqlalchemy.orm import sessionmaker
from ppt_service.server.models import Job,Project,Plan,Version,Checkpoint,Asset,ExecutionSlot,now
from ppt_service.server.repository import Repository,ACTIVE
from .execution import *
from .staging import Stager,read_json,open_regular,atomic_json
from .attempt_builder import build_attempt
from .publisher import Publisher

class Dispatcher:
    def __init__(self,sessions,config:RuntimeConfig,backend:ExecutionBackend,sandbox:Sandbox,*,oracle_provider=None):
        self.sessions=sessions;self.config=config;self.backend=backend;self.sandbox=sandbox
        self.stager=Stager(config);self.publisher=Publisher(config,sandbox);self.owned={};self.oracle_provider=oracle_provider
    def _attempt(self,s,j):
        return build_attempt(s,j)
    def _fail_unlaunched(self,s,jid,attempt,token,phase):
        """Dispose of this transaction's new claim before any external work.

        This path is only reached from a failed, pure attempt build immediately
        after claim_next. Existing/attached executions always require their real
        stop proof instead. Keep stopped false: rejection is not OS evidence.
        """
        slot=s.get(ExecutionSlot,1,with_for_update=True)
        j=s.get(Job,jid,with_for_update=True)
        if (not slot or not j or slot.job_id!=jid or slot.fencing_token!=token
                or j.fencing_token!=token or j.attempt!=attempt or j.kind!=phase
                or j.status not in ('starting','cancelling') or j.job_uid or j.run_id):
            raise ControlError('stale_claim')
        p=s.get(Project,j.project_id,with_for_update=True)
        if not p or p.active_job_id!=jid:raise ControlError('stale_claim')
        cancelled=j.status=='cancelling' or p.deletion_requested
        j.status='cancelled' if cancelled else 'failed'
        j.error_code=None if cancelled else 'execution_interrupted'
        j.lease_until=None;j.deadline=None
        p.active_job_id=None;p.stage='ready' if p.latest_version_id else 'draft'
        p.cleanup_ready=p.deletion_requested;slot.job_id=None
        Repository(s).event(j,'job.cancelled' if cancelled else 'job.failed',
            {} if cancelled else {'error_code':j.error_code})
        result={'status':j.status,'job_id':jid}
        if not cancelled:result['error_code']=j.error_code
        return result
    def _db_stopped(self,a,h):
        proof=self.backend.prove_stopped(h)
        if proof.uid!=a.uid or not proof.all_writers_stopped:raise ControlError('writers_not_stopped')
        with self.sessions.begin() as s:
            j=Repository(s).prove_stopped(a.job_id,a.attempt,a.token,job_uid=h.uid,all_writers_stopped=True)
            return j.status
    def progress(self,jid,attempt,token,stage):
        with self.sessions.begin() as s:
            r=Repository(s);j,p,slot=r.fenced(jid,attempt,token)
            if stage not in ('collecting','validating'):raise ControlError('invalid_progress')
            if not j.stopped:raise ControlError('stop_proof_required')
            # Progress describes trusted controller work after writers stopped,
            # never an Agent's assertion about pages or quality gates.
            if stage=='collecting' and j.stage=='validating':raise ControlError('invalid_progress')
            if j.stage==stage and j.status==stage:return
            r.event(j,'stage.changed',{'stage':stage});j.stage=stage;j.status=stage
    def _usage(self,a,layout):
        ledger=layout.audit/'proxy-ledger'
        if not ledger.exists() or not (ledger/'usage.jsonl').exists():return
        latest={}
        with open_regular(ledger,'usage.jsonl',16*1024**2) as stream:
            for line in stream:
                if len(line)>65536:raise ControlError('usage_record_limit')
                row=json.loads(line)
                if row.get('job_id')!=a.job_id or row.get('attempt')!=a.attempt or row.get('phase')!=a.phase or row.get('model')!=self.config.model:raise ControlError('usage_attribution_mismatch')
                rid=row.get('proxy_request_id');uuid.UUID(rid);latest[rid]=row
        with self.sessions.begin() as s:
            repo=Repository(s)
            for rid,row in latest.items():
                cost=row.get('cost_usd')
                if cost is not None:
                    try:
                        value=Decimal(str(cost))
                        if not value.is_finite() or value<0:cost=None
                    except InvalidOperation:cost=None
                for key in ('input_tokens','output_tokens'):
                    value=row.get(key)
                    if value is not None and (type(value) is not int or value<0 or value>=2**63):raise ControlError('invalid_usage_tokens')
                repo.record_usage(a.job_id,a.attempt,phase=a.phase,outcome=row.get('outcome','proxy_shutdown') if row.get('event')=='request_finished' else 'proxy_shutdown',model=self.config.model,provider='service-model-proxy',provider_request_id=row.get('provider_request_id') or rid,input_tokens=row.get('input_tokens'),output_tokens=row.get('output_tokens'),reported_cost=cost,currency='USD' if cost is not None else None)
    def _fail(self,a,h,code):
        self.backend.stop(h)
        status=self._db_stopped(a,h)
        layout=self.stager.layout(a)
        try:self._usage(a,layout)
        except Exception:
            # A ledger durability/attribution fault is operator-visible privately,
            # never a fabricated zero record. Preserve the ledger for recovery.
            atomic_json(layout.audit/'usage-import-error.json',{'error_code':'usage_import_failed'})
        if status!='cancelled':
            with self.sessions.begin() as s:Repository(s).finish_failed(a.job_id,a.attempt,a.token,error_code=code)
        self.backend.cleanup(h);self.owned.pop(a.job_id,None)
        return 'cancelled' if status=='cancelled' else 'failed'
    def _start(self,a,checkpoint_manifest):
        h=self.backend.reconcile(a);self.owned[a.job_id]=(a,h)
        # Persist attempt identity BEFORE any staging/container side effect. A
        # crash before create can then be safely reconciled as an empty group.
        with self.sessions.begin() as s:
            r=Repository(s);j,p,slot=r.fenced(a.job_id,a.attempt,a.token);j.job_uid=h.uid;j.session_id=a.session_id
        try:
            if 'review_base' in a.snapshot:
                # Review admission is a draft branch, not a published baseline.
                # Recheck its exact frozen evidence before creating any worker.
                from ppt_service.server.review_revisions import recheck_review_source
                from ppt_service.server.storage import Storage
                with self.sessions.begin() as s:
                    # Slot -> terminal source job -> current job -> project;
                    # the review HTTP resolver never takes the execution slot.
                    s.get(ExecutionSlot,1,with_for_update=True)
                    base=s.get(Version,a.base_version_id)
                    if not base:raise ControlError('base_version_mismatch')
                    s.get(Job,base.job_id,with_for_update=True)
                    j,_,_=Repository(s).fenced(a.job_id,a.attempt,a.token)
                    recheck_review_source(s,Storage(self.config.data_root),j)
            layout,inventory=self.stager.stage(a,checkpoint_manifest)
            if a.phase in ('generate','revise'):
                if self.oracle_provider is None:raise ControlError('trusted_chart_oracle_required')
                oracle=self.oracle_provider(a,checkpoint_manifest)
                if not isinstance(oracle,dict) or not isinstance(oracle.get('expected_charts'),list):raise ControlError('trusted_chart_oracle_required')
                authorized={x['sha256'] for x in a.snapshot.get('assets',[])}
                if oracle['expected_charts'] and not oracle.get('source_sha256'):raise ControlError('oracle_source_required')
                if not set(oracle.get('source_sha256',[])).issubset(authorized):raise ControlError('oracle_source_mismatch')
                atomic_json(layout.audit/'trusted-oracle.json',oracle)
            if hasattr(self.sandbox,'scope_uid'):self.sandbox.scope_uid=a.uid
            parsed=self.sandbox.parse(layout.inputs,inventory)
            self.stager.finish_inputs(a,layout,parsed)
            with self.sessions.begin() as s:
                r=Repository(s);j,p,slot=r.fenced(a.job_id,a.attempt,a.token)
                ids={row['id'] for row in parsed['sources']}
                if ids!={row['id'] for row in inventory['sources']}:raise ControlError('parse_inventory_mismatch')
                for aid in ids:
                    asset=s.get(Asset,aid)
                    if asset and asset.project_id==a.project_id:asset.parse_status='ready'
                r.heartbeat(a.job_id,a.attempt,a.token,self.config.lease_seconds)
            h=self.backend.create(a,layout);self.owned[a.job_id]=(a,h)
            with self.sessions.begin() as s:Repository(s).attach_execution(a.job_id,a.attempt,a.token,job_uid=h.uid,session_id=a.session_id)
            return {'status':'started','job_id':a.job_id}
        except Exception as exc:
            parse_errors=PARSE_ERROR_CODES|{'parse_snapshot_mismatch','parse_inventory_mismatch'}
            code='parse_failed' if isinstance(exc,ControlError) and isinstance(exc.code,str) and exc.code in parse_errors else 'execution_interrupted'
            self._fail(a,h,code)
            return {'status':'failed','job_id':a.job_id,'error_code':code}
    def _complete(self,a,h,checkpoint_manifest):
        finalized=False
        try:
            self.backend.stop(h)
            status=self._db_stopped(a,h)
            layout=self.stager.layout(a)
            self._usage(a,layout)
            if status=='cancelled':self.backend.cleanup(h);self.owned.pop(a.job_id,None);return {'status':'cancelled','job_id':a.job_id}
            self.progress(a.job_id,a.attempt,a.token,'collecting')
            snapshot,indexes=self.stager.collect(a,layout)
            request={'job_id':a.job_id,'attempt':a.attempt,'phase':a.phase,'session_id':a.session_id,'input_snapshot_sha256':a.snapshot['hash'],'pages':a.snapshot.get('settings',{}).get('page_count',8)}
            if hasattr(self.backend,'execution_image'):
                request['execution_image']=self.backend.execution_image(h)
            if checkpoint_manifest:
                for source,target in (('design_spec.md_sha256','approved_design_spec_sha256'),('spec_lock.md_sha256','approved_spec_lock_sha256')):
                    if source in checkpoint_manifest:request[target]=checkpoint_manifest[source]
            if a.phase in ('generate','revise'):
                oracle=read_json(layout.audit,'trusted-oracle.json')
                if not isinstance(oracle,dict) or not isinstance(oracle.get('expected_charts'),list):raise ControlError('trusted_chart_oracle_required')
                request['expected_charts']=oracle['expected_charts']
                authorized={x['sha256'] for x in a.snapshot.get('assets',[])}
                if oracle['expected_charts'] and not oracle.get('source_sha256'):raise ControlError('oracle_source_required')
                if not set(oracle.get('source_sha256',[])).issubset(authorized):raise ControlError('oracle_source_mismatch')
                request['trusted_oracle']=oracle
                request['authorized_source_sha256']=sorted(authorized)
            atomic_json(layout.audit/'validation-request.json',request)
            self.progress(a.job_id,a.attempt,a.token,'validating')
            validated=self.publisher.validate(snapshot,request)
            atomic_json(layout.audit/'validation-result.json',validated)
            if validated.get('review_candidate') is True and a.phase in ('generate','revise'):
                from .review_delivery import save_review_candidate
                with self.sessions.begin() as s:
                    repo=Repository(s);repo.admission_lock()
                    save_review_candidate(repo,a,snapshot,request,validated,self.config)
                outcome='review'
            elif not validated.get('accepted'):
                # Preserve original needs_input/actor record; do not manufacture a
                # plan/base. Current public schema supports failed, not needs_input.
                with self.sessions.begin() as s:Repository(s).finish_failed(a.job_id,a.attempt,a.token,error_code='quality_gate_failed')
                outcome='needs_input' if validated.get('status')=='needs_input' else 'failed'
            else:
                with self.sessions.begin() as s:
                    repo=Repository(s);repo.admission_lock();self.publisher.promote(repo,a,snapshot,validated)
                outcome='succeeded'
            finalized=True
            self.backend.cleanup(h);self.owned.pop(a.job_id,None)
            return {'status':outcome,'job_id':a.job_id}
        except Exception:
            if finalized:
                atomic_json(layout.audit/'cleanup-pending.json',{'uid':h.uid,'job_id':a.job_id,'attempt':a.attempt,'token':a.token})
                self.owned.pop(a.job_id,None)
                return {'status':outcome,'job_id':a.job_id,'cleanup_pending':True}
            # Do not retry a model phase. Stop/fence again before failure cleanup.
            outcome=self._fail(a,h,'quality_gate_failed')
            return {'status':outcome,'job_id':a.job_id}|({'error_code':'quality_gate_failed'} if outcome=='failed' else {})
    def tick(self):
        admission=self.config.state==RuntimeState.CONFIGURED and self.backend.ready
        # Paid admission stays closed until operator config is explicit.
        # Tests inject a backend; they do not enable a production bypass flag.
        with self.sessions.begin() as s:
            slot=s.get(ExecutionSlot,1)
            active=s.get(Job,slot.job_id) if slot and slot.job_id else None
            if active:
                a,cp=self._attempt(s,active)
                status=active.status;lease=active.lease_until;deadline=active.deadline
            else:
                if not admission:return {'status':'disabled'}
                j=Repository(s).claim_next(self.config.lease_seconds)
                if not j:return {'status':'idle'}
                claim=(j.id,j.attempt,j.fencing_token,j.kind)
                try:a,cp=self._attempt(s,j)
                except (ControlError,ValueError,TypeError,KeyError,AttributeError):
                    # A permanent bad checkpoint/lineage must not roll the
                    # claim back to queued forever and block later projects.
                    # The claim and terminal disposition commit atomically.
                    return self._fail_unlaunched(s,*claim)
                status='claimed';lease=j.lease_until;deadline=j.deadline
        if status=='claimed':return self._start(a,cp)
        def expired(value):
            if value is None:return True
            if value.tzinfo is None:value=value.replace(tzinfo=timezone.utc)
            return value<=now()
        owned=self.owned.get(a.job_id)
        h=owned[1] if owned else self.backend.reconcile(a)
        if not admission:
            self._fail(a,h,'execution_interrupted');return {'status':'disabled_stopped','job_id':a.job_id}
        if not owned:
            if not expired(lease) and status!='cancelling':return {'status':'leased','job_id':a.job_id}
            # Reconcile all old resources before releasing slot. Never resume/run.
            self._fail(a,h,'execution_interrupted')
            return {'status':'reconciled','job_id':a.job_id}
        if status=='cancelling' or expired(deadline):
            self._fail(a,h,'execution_timeout')
            return {'status':'stopped','job_id':a.job_id}
        observed=self.backend.observe(h)
        if observed.running:
            with self.sessions.begin() as s:Repository(s).heartbeat(a.job_id,a.attempt,a.token,self.config.lease_seconds)
            return {'status':'running','job_id':a.job_id}
        if observed.exit_code!=0:
            self._fail(a,h,'execution_interrupted');return {'status':'failed','job_id':a.job_id}
        return self._complete(a,h,cp)

def build_controller(sessions,config):
    """Select one backend; Kubernetes does not require a Docker socket."""
    from .chart_oracle import ChartOracle
    mode=os.getenv('PPT_EXECUTION_BACKEND','docker')
    if mode=='agent-service':
        from agent_service.client import AgentClient
        from .agent_connector import AgentConnector,AgentConnectorSettings
        from .agent_validation import AgentKubernetesSandbox
        from .kubernetes import KubeSettings,KubernetesParseScopes
        from .materials import MaterialParser
        token_path=Path(os.environ['PPT_AGENT_SERVICE_TOKEN_FILE'])
        with open_regular(token_path.parent,token_path.name,512) as stream:
            token=stream.read(513).decode('ascii').strip()
        client=AgentClient(os.environ['PPT_AGENT_SERVICE_URL'],token)
        sandbox=AgentKubernetesSandbox(config,KubeSettings.env())
        oracle=ChartOracle.env()
        def oracle_for_task(task):
            from types import SimpleNamespace
            snapshot=dict(task['input_snapshot'],hash=task['input_snapshot_sha256'])
            return oracle(SimpleNamespace(snapshot=snapshot))
        controller=AgentConnector(sessions,config,client,sandbox,
            settings=AgentConnectorSettings.env(),oracle_provider=oracle_for_task)
        materials=MaterialParser(sessions,config,sandbox,KubernetesParseScopes(sandbox.transport.backend),lease_seconds=300)
        return controller,materials
    elif mode=='kubernetes':
        from .kubernetes import KubeSettings,KubernetesBackend,KubernetesParseScopes
        from .kubernetes_sandbox import KubernetesSandbox
        from .materials import MaterialParser
        settings=KubeSettings.env()
        def check_live_claim(a):
            with sessions.begin() as s:
                r=Repository(s);j,p,_=r.fenced(a.job_id,a.attempt,a.token)
                def live(value):
                    if value is None:return False
                    if value.tzinfo is None:value=value.replace(tzinfo=timezone.utc)
                    return value>now()
                if (j.job_uid!=a.uid or not live(j.lease_until) or not live(j.deadline)
                        or not p.external_model_ack):raise ControlError('execution_revoked')
                r.heartbeat(a.job_id,a.attempt,a.token,config.lease_seconds)
        backend=KubernetesBackend(config,settings,before_worker=check_live_claim)
        sandbox=KubernetesSandbox(config,settings,backend=backend)
        materials=MaterialParser(sessions,config,sandbox,KubernetesParseScopes(backend),lease_seconds=300)
    elif mode=='docker':
        from .pods import DockerBackend,DockerSandbox
        if config.state==RuntimeState.CONFIGURED:config.require_paid_admission()
        backend= DockerBackend(config);sandbox=DockerSandbox(config);materials=None
    else:raise ControlError('unsupported_execution_backend')
    return Dispatcher(sessions,config,backend,sandbox,oracle_provider=ChartOracle.env()),materials

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--once',action='store_true');args=parser.parse_args()
    config=RuntimeConfig.env()
    url=os.environ['DATABASE_URL']
    if not url.startswith('postgresql'):raise ControlError('postgresql_required')
    engine=create_engine(url,pool_pre_ping=True);sessions=sessionmaker(engine,expire_on_commit=False)
    controller,materials=build_controller(sessions,config)
    try:
        while True:
            try:
                result=controller.tick()
                # Parsing never delays heartbeats for an active model attempt.
                if materials is not None and result['status'] in ('idle','disabled'):
                    parsed=materials.tick()
                    if parsed['status']!='idle':result={'status':'material','result':parsed}
            except ControlError as exc:
                # Preserve unresolved scopes and the DB slot. API error bodies,
                # credentials and model output are never written to stdout.
                result={'status':'control_blocked','error_code':exc.code}
            print(json.dumps(result),flush=True)
            if args.once:return 0
            time.sleep(1)
    finally:engine.dispose()
if __name__=='__main__':raise SystemExit(main())
