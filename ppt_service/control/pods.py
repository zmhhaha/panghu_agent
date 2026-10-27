"""Fixed Docker development execution and network-none parser/validator sandboxes.

This module runs on a trusted Linux Docker host. It is never mounted into workers.
All Docker commands are argument lists; no workspace program/script is executed.
"""
from __future__ import annotations
import json,os,secrets,subprocess,tempfile,uuid,re
from pathlib import Path
from .execution import *
from .staging import atomic_json,read_json
from ppt_service.server.phase_limits import phase_timeout_seconds

LABEL='io.ppt-service.control'
class CommandRunner:
    def __call__(self,args,*,timeout=30,check=True):
        # Use files rather than pipe capture. Neither commands nor stderr are logged:
        # proxy args/config inspection may contain scoped or upstream credentials.
        with tempfile.TemporaryFile() as output:
            try:result=subprocess.run(args,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.DEVNULL,timeout=timeout,check=False)
            except (OSError,subprocess.TimeoutExpired):raise ControlError('control_command_unavailable') from None
            output.seek(0);data=output.read(2*1024**2+1)
        if len(data)>2*1024**2:raise ControlError('control_response_limit')
        if check and result.returncode:raise ControlError('control_command_failed')
        return result.returncode,data

class Docker:
    def __init__(self,runner=None):self.runner=runner or CommandRunner()
    def call(self,*args,timeout=30,check=True):return self.runner(['/usr/bin/docker','--host','unix:///var/run/docker.sock',*map(str,args)],timeout=timeout,check=check)
    def ids(self,uid):
        _,raw=self.call('ps','-aq','--filter',f'label={LABEL}={uid}')
        ids=tuple(x for x in raw.decode().splitlines() if x)
        if any(len(x)>64 or any(c not in '0123456789abcdef' for c in x) for x in ids):raise ControlError('invalid_container_identity')
        return ids
    def inspect(self,name):
        code,raw=self.call('inspect','--format','{{json .State}}',name,check=False)
        if code:return None
        try:return json.loads(raw)
        except Exception:raise ControlError('invalid_container_observation') from None

def secure_container(name,uid,*,memory='4g',cpus='2'):
    return ['create','--name',name,'--label',f'{LABEL}={uid}','--restart','no','--user','10000:10000','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','256','--memory',memory,'--cpus',cpus,'--ipc','private','--dns','127.0.0.1','--sysctl','net.ipv6.conf.all.disable_ipv6=1','--workdir','/','--tmpfs','/tmp:rw,noexec,nosuid,nodev,size=256m,mode=1777','--stop-timeout','30']
def mount(source,target,readonly=False):
    source=Path(source).absolute()
    if not source.exists() or source.is_symlink() or ',' in str(source):raise ControlError('invalid_mount')
    return ['--mount',f'type=bind,src={source},dst={target}'+(',readonly' if readonly else '')]

class Firewall:
    """Install actual fail-closed rules; approval flag alone is never the firewall."""
    def __init__(self,runner=None):self.runner=runner or CommandRunner()
    def ipt(self,*args,check=True):return self.runner(['/usr/sbin/iptables','-w','5',*args],check=check)
    @staticmethod
    def names(uid):
        suffix=uid.replace('-','')[:10]
        return 'ppw'+suffix,'ppe'+suffix,'PPT'+suffix.upper()
    def install(self,uid,upstream):
        if Path('/proc/sys/net/bridge/bridge-nf-call-iptables').read_text().strip()!='1':raise ControlError('bridge_filtering_unavailable')
        wb,eb,chain=self.names(uid)
        # Only one global slot, fixed private /29s; old networks block creation.
        self.ipt('-N',chain)
        rules=[['-i',wb,'-s','172.30.240.3','-d','172.30.240.2','-p','tcp','--dport','8080','-j','ACCEPT'],['-i',wb,'-s','172.30.240.2','-d','172.30.240.3','-m','conntrack','--ctstate','ESTABLISHED,RELATED','-j','ACCEPT'],['-i',wb,'-j','DROP'],['-i',eb,'-s','172.30.241.2','-d',upstream,'-p','tcp','--dport','443','-j','ACCEPT'],['-i',eb,'-j','DROP'],['-o',eb,'-m','conntrack','--ctstate','ESTABLISHED,RELATED','-j','ACCEPT'],['-o',eb,'-j','DROP'],['-o',wb,'-j','DROP']]
        for rule in rules:self.ipt('-A',chain,*rule)
        self.ipt('-I','DOCKER-USER','1','-j',chain)
        # Traffic destined to host sockets is INPUT, not DOCKER-USER.
        for bridge in (wb,eb):self.ipt('-I','INPUT','1','-i',bridge,'-j','DROP')
        for rule in rules:self.ipt('-C',chain,*rule)
        self.ipt('-C','DOCKER-USER','-j',chain)
        for bridge in (wb,eb):self.ipt('-C','INPUT','-i',bridge,'-j','DROP')
    def remove(self,uid):
        wb,eb,chain=self.names(uid)
        self.ipt('-D','DOCKER-USER','-j',chain,check=False)
        for bridge in (wb,eb):self.ipt('-D','INPUT','-i',bridge,'-j','DROP',check=False)
        self.ipt('-F',chain,check=False);self.ipt('-X',chain,check=False)

class DockerBackend:
    @property
    def ready(self):
        try:self.config.require_paid_admission();return True
        except ControlError:return False
    def __init__(self,config:RuntimeConfig,docker=None,firewall=None):self.config=config;self.docker=docker or Docker();self.firewall=firewall or Firewall()
    def reconcile(self,a):
        prefix='ppt-'+a.uid.replace('-','')
        return Handle(a.uid,a.job_id,a.attempt,a.token,prefix+'-worker',prefix+'-proxy')
    def create(self,a,layout):
        self.config.require_paid_admission()
        h=self.reconcile(a)
        if self.docker.ids(h.uid):raise ControlError('execution_already_exists')
        wb,eb,_=Firewall.names(h.uid)
        self.docker.call('network','create','--internal','--subnet','172.30.240.0/29','--opt','com.docker.network.bridge.enable_icc=false','--opt',f'com.docker.network.bridge.name={wb}',wb)
        self.docker.call('network','create','--subnet','172.30.241.0/29','--opt','com.docker.network.bridge.enable_icc=false','--opt',f'com.docker.network.bridge.name={eb}',eb)
        self.firewall.install(h.uid,self.config.approved_upstream_ip)
        key=secrets.token_urlsafe(48)
        ledger=layout.audit/'proxy-ledger';ledger.mkdir(exist_ok=False)
        if os.name=='posix':os.chown(ledger,10000,10000)
        proxy=secure_container(h.proxy_name,h.uid,memory='512m',cpus='0.5')+['--network',eb,'--ip','172.30.241.2','--add-host',f'custom-llm-proxy.panghuer.top:{self.config.approved_upstream_ip}','--env-file',str(self.config.upstream_env_file),'--env','PPT_MODEL=gpt-6.1-sol','--env','PPT_UPSTREAM_BASE_URL=https://custom-llm-proxy.panghuer.top/v1','--env','PPT_PROXY_CLIENT_KEY='+key,'--env','PPT_PROXY_USAGE_PATH=/audit/usage.jsonl','--env','PPT_USAGE_JOB_ID='+a.job_id,'--env','PPT_USAGE_ATTEMPT='+str(a.attempt),'--env','PPT_USAGE_PHASE='+a.phase]+mount(ledger,'/audit')+['--entrypoint','/opt/hermes/.venv/bin/python',self.config.worker_image,'-m','ppt_service.worker.model_proxy']
        self.docker.call(*proxy)
        self.docker.call('network','connect','--ip','172.30.240.2','--alias','model-proxy',wb,h.proxy_name)
        worker=secure_container(h.worker_name,h.uid)+['--network',wb,'--ip','172.30.240.3','--add-host','model-proxy:172.30.240.2','--env','PPT_MODEL=gpt-6.1-sol','--env','PPT_MODEL_PROXY_ORIGIN=http://model-proxy:8080','--env','PPT_MODEL_BASE_URL=http://model-proxy:8080/v1','--env','PPT_MODEL_API_KEY='+key]
        for part in ('inputs','task','base','home','project','result'):worker+=mount(getattr(layout,part),'/work/'+part,part in ('inputs','task','base'))
        worker+=['--entrypoint','/opt/hermes/.venv/bin/python',self.config.worker_image,'-m','ppt_service.worker.execute','--task','/work/task/task.json','--deadline',str(phase_timeout_seconds(a.phase))]
        self.docker.call(*worker)
        # Persist identity before starting either process. Proxy key never enters DB.
        atomic_json(layout.audit/'execution-handle.json',{'uid':h.uid,'worker':h.worker_name,'proxy':h.proxy_name,'job_id':a.job_id,'attempt':a.attempt,'token':a.token})
        self.docker.call('start',h.proxy_name)
        self.docker.call('start',h.worker_name)
        return h
    def observe(self,h):
        state=self.docker.inspect(h.worker_name)
        if state is None:
            if self.docker.ids(h.uid):return Observation(True)
            return Observation(False,None)
        return Observation(bool(state.get('Running')),state.get('ExitCode') if not state.get('Running') else None)
    def stop(self,h):
        ids=self.docker.ids(h.uid)
        for cid in ids:self.docker.call('stop','--time','30',cid,timeout=40,check=False)
        for cid in ids:
            state=self.docker.inspect(cid)
            if state and state.get('Running'):self.docker.call('kill',cid,check=False)
    def prove_stopped(self,h):
        ids=self.docker.ids(h.uid)
        for cid in ids:
            state=self.docker.inspect(cid)
            if state is None or state.get('Running') or state.get('Pid',0)!=0:return StopProof(h.uid,False,ids)
        return StopProof(h.uid,True,ids)
    def cleanup(self,h):
        proof=self.prove_stopped(h)
        if not proof.all_writers_stopped:raise ControlError('writers_not_stopped')
        if proof.resource_ids:self.docker.call('rm','--volumes',*proof.resource_ids)
        self.firewall.remove(h.uid)
        wb,eb,_=Firewall.names(h.uid)
        for name in (wb,eb):self.docker.call('network','rm',name,check=False)

class DockerSandbox:
    def __init__(self,config:RuntimeConfig,docker=None):self.config=config;self.docker=docker or Docker();self.scope_uid=None
    def _run(self,module,mounts,request,output_name,timeout):
        with tempfile.TemporaryDirectory(prefix='ppt-control-sandbox-',dir=self.config.data_root) as temp:
            root=Path(temp);inputs=root/'request';output=root/'output';inputs.mkdir();output.mkdir();os.chmod(root,0o755);os.chmod(inputs,0o755);os.chmod(output,0o777)
            atomic_json(inputs/'request.json',request)
            name='ppt-sandbox-'+uuid.uuid4().hex;uid=self.scope_uid or str(uuid.uuid4())
            args=secure_container(name,uid,memory='1g',cpus='1')+['--network','none']
            args+=mount(self.config.code_root,'/opt/ppt-service/ppt_service/control',True)+mount(inputs,'/request',True)+mount(output,'/output')
            for source,target in mounts:args+=mount(source,target,True)
            args+=['--entrypoint','/opt/hermes/.venv/bin/python',self.config.worker_image,'-m',module]
            self.docker.call(*args)
            try:
                self.docker.call('start',name)
                _,raw=self.docker.call('wait',name,timeout=timeout)
                code=int(raw.strip())
                result=read_json(output,output_name,8*1024**2)
                if module=='ppt_service.control.parsing':
                    if code!=0:
                        reason=parse_failure_reason(result,request.get('input_snapshot_sha256'))
                        raise ControlError(reason or 'parse_failed')
                    if not isinstance(result,dict) or 'error_code' in result or result.get('status')=='failed' or result.get('schema')==PARSE_FAILURE_SCHEMA:raise ControlError('parse_failed')
                if code!=0:raise ControlError(result.get('error_code','sandbox_failed'))
                return result
            finally:
                # Forced removal tears down the complete sandbox namespace on timeout.
                self.docker.call('rm','--force','--volumes',name,timeout=30,check=False)
    def parse(self,inputs,inventory):
        return self._run('ppt_service.control.parsing',[(inputs,'/inputs')],{'input_snapshot_sha256':inventory.get('input_snapshot_sha256')},'parsed.json',self.config.parse_timeout)
    def validate(self,snapshot,request):
        """Persistent, bounded validation evidence; parse() keeps its old path."""
        from .staging import open_regular,copy_tree,make_readonly,relative
        from .render_validation import hash_file
        snapshot=Path(snapshot).absolute()
        for part in (*reversed(snapshot.parents),snapshot):
            if part.is_symlink():raise ControlError('linked_validation_snapshot')
        source_hash=hash_file(snapshot/'result','manifest.json',4*1024**2)['sha256']
        nonce=str(uuid.uuid4());envelope=dict(request,validation_nonce=nonce,source_manifest_sha256=source_hash)
        retained=snapshot.parent/'validation-evidence';retained.mkdir(exist_ok=True)
        if retained.is_symlink():raise ControlError('linked_validation_evidence')
        final=retained/nonce;pending=retained/('.pending-'+nonce)
        result=None;code=None;failure=None;container_id=None
        with tempfile.TemporaryDirectory(prefix='ppt-validation-',dir=self.config.data_root) as temp:
            root=Path(temp);inputs=root/'request';output=root/'output';inputs.mkdir();output.mkdir();root.chmod(0o755);inputs.chmod(0o755);output.chmod(0o777)
            atomic_json(inputs/'request.json',envelope)
            name='ppt-validator-'+nonce.replace('-','');uid=self.scope_uid or nonce
            args=secure_container(name,uid,memory='1g',cpus='1')+['--network','none']
            args+=mount(self.config.code_root,'/opt/ppt-service/ppt_service/control',True)+mount(inputs,'/request',True)+mount(output,'/output')+mount(snapshot,'/snapshot',True)
            args+=['--entrypoint','/opt/hermes/.venv/bin/python',self.config.worker_image,'-m','ppt_service.control.publisher']
            _,created=self.docker.call(*args);container_id=created.decode().strip()
            try:
                self.docker.call('start',name)
                _,raw=self.docker.call('wait',name,timeout=self.config.validation_timeout);code=int(raw.strip())
                result=read_json(output,'validated.json',8*1024**2)
            except Exception as exc:failure=exc
            finally:
                removed,_=self.docker.call('rm','--force','--volumes',name,timeout=30,check=False)
                if removed or self.docker.inspect(name) is not None:
                    quarantine=retained/('.running-'+nonce);os.replace(root,quarantine)
                    atomic_json(quarantine/'controller-quarantine.json',{'controller_nonce':nonce,'source_manifest_sha256':source_hash,'transport_verified':False,'controller_error_code':'validation_writers_not_stopped','writers_stopped':False})
                    problem=ControlError('validation_writers_not_stopped');problem.evidence_path=str(quarantine);raise problem
            # No writer remains. Copy only ordinary single-link bounded files,
            # independently hash them, then atomically expose a readonly bundle.
            try:
                if (output/'transport-manifest.json').exists() or (output/'.controller-quarantine.json').exists():raise ControlError('reserved_validation_output')
                inventory=copy_tree(output,pending,max_total=128*1024**2,max_files=4096)
            except Exception:
                if pending.exists():atomic_json(pending/'.controller-quarantine.json',{'controller_nonce':nonce,'source_manifest_sha256':source_hash,'transport_verified':False,'controller_error_code':'validation_output_copy_failed','writers_stopped':True})
                problem=ControlError('validation_output_copy_failed');problem.evidence_path=str(pending);raise problem
            by_path={row['path']:row for row in inventory['files']}
            if hash_file(snapshot/'result','manifest.json',4*1024**2)['sha256']!=source_hash:raise ControlError('validation_source_changed')
            if result is not None:
                if result.get('validation_nonce')!=nonce or result.get('source_manifest_sha256')!=source_hash:failure=ControlError('validation_envelope_mismatch')
                evidence=result.get('independent_programmatic_evidence')
                if isinstance(evidence,dict):
                    if evidence.get('source',{}).get('source_manifest_sha256')!=source_hash or evidence.get('controller_nonce')!=nonce:failure=ControlError('validation_envelope_mismatch')
                    try:
                        run_id=str(uuid.UUID(evidence['validation_id']))
                        raw_manifest=read_json(snapshot/'result','manifest.json',4*1024**2);slides=raw_manifest.get('slides',[]);pngs=evidence.get('pngs')
                        if not isinstance(pngs,list) or type(request.get('pages')) is not int or len(pngs)!=request['pages'] or len(pngs)!=len(slides):raise ControlError('validation_artifact_mismatch')
                        project_key=relative(raw_manifest['project_root']);seen=set()
                        for ordinal,(png,slide) in enumerate(zip(pngs,slides),1):
                            svg=relative(slide['svg_path']);expected='scratch-projects/'+project_key+'/.preview/'+Path(svg).stem+'.png'
                            if png.get('relative_path')!=expected or expected in seen or png.get('ordinal')!=ordinal or png.get('svg_path')!=svg:raise ControlError('validation_artifact_mismatch')
                            seen.add(expected)
                            if hash_file(snapshot/'project'/project_key,svg,8*1024**2)['sha256']!=png.get('svg_sha256'):raise ControlError('validation_artifact_mismatch')
                            key=relative('independent/'+run_id+'/'+expected);row=by_path.get(key)
                            if not row or row['sha256']!=png['sha256'] or row['bytes']!=png['bytes']:raise ControlError('validation_artifact_mismatch')
                    except (KeyError,ValueError,TypeError):failure=ControlError('validation_artifact_mismatch')
                    except ControlError as exc:failure=exc
                if request.get('phase') in ('generate','revise'):
                    if result.get('accepted') is not False:failure=ControlError('publication_guard_violation')
                    if result.get('status')=='candidate':
                        pending_gates=result.get('required_pending_gates')
                        if not isinstance(pending_gates,list) or any(not isinstance(g,str) for g in pending_gates) or not {'independent_visual_review','independent_postflight_and_delivery'}.issubset(set(pending_gates)):failure=ControlError('publication_guard_violation')
            safe_error=failure.code if isinstance(failure,ControlError) else (result.get('error_code','sandbox_failed') if isinstance(result,dict) and code!=0 else 'validation_execution_failed')
            if not isinstance(safe_error,str) or not re.fullmatch(r'[a-z_]{1,80}',safe_error):safe_error='validation_execution_failed'
            verified=failure is None and code==0
            transport={'schema_version':1,'controller_nonce':nonce,'source_manifest_sha256':source_hash,'container_id':container_id,'sandbox_exit_code':code,'writers_stopped':True,'transport_verified':verified,'controller_error_code':None if verified else safe_error,**inventory}
            atomic_json(pending/'transport-manifest.json',transport);make_readonly(pending)
            if final.exists():raise ControlError('validation_evidence_exists')
            os.replace(pending,final)
        if failure is not None or code!=0:
            problem=failure if isinstance(failure,ControlError) else ControlError(safe_error)
            problem.evidence_path=str(final);raise problem
        result['persistent_evidence']={'path':str(final),'controller_nonce':nonce,'source_manifest_sha256':source_hash,'transport_manifest':'transport-manifest.json','transport_verified':True,'publication_authorization':False,'file_count':len(inventory['files']),'bytes':inventory['bytes']}
        return result
