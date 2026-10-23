"""Fixed, single-phase paid-development Pi PPT entry. Not a website worker.

Only a trusted host runner may provision this entry and its network cage. The
base PptProfile remains disabled; this compiled development profile is limited
to plan_initial with measured mounts, source-bound context and a live host lease.
"""
from __future__ import annotations
from datetime import datetime,timedelta,timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import stat
import sys
import threading
import time
import urllib.request
sys.path.insert(0,'/code')
if __package__ in (None,''):__package__='ppt_service.worker'
from pi_worker.runtime import PiRuntime,RuntimeSettings
from .container_lease import ContainerLaunch,ContainerLease,read_private
from .model_grant import ModelGrant
from .pi_context import PreparedPptContext
from .pi_profile import PptProfile
from .model_proxy import DEFAULT_MODEL
from ..control.pi_container import PINNED_IMAGE
from ..control.preview_launch import _safe_tree,_mutate
from ..control.staging import read_json

RUNTIME_REVISION='pi0.73.1-ppt-development-v1'
SKILL_BUNDLE_REVISION='ppt-master-c350bc31-development-v1'
SKILL=Path('/opt/ppt-master/skills/ppt-master/SKILL.md')
ROOT=Path('/work')


def require(ok,code='ppt_development_boundary_invalid'):
    if not ok:raise RuntimeError(code)


def write_report(path,value):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as out:
        os.fchmod(out.fileno(),0o600)
        json.dump(value,out,ensure_ascii=False,sort_keys=True,allow_nan=False)
        out.flush();os.fsync(out.fileno())


def mount_boundary():
    mounts={}
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left,right=line.split(' - ',1);fields=left.split()
        require(fields[4] not in mounts)
        mounts[fields[4]]=(set(fields[5].split(',')),right.split()[0])
    expected={'/':'ro','/code':'ro',**{'/work/'+x:'ro' for x in ('inputs','task','base')},
              **{'/work/'+x:'rw' for x in ('project','result','audit','runtime')},'/tmp':'rw'}
    for target,mode in expected.items():
        require(target in mounts and mode in mounts[target][0])
    for target in ('/tmp','/work/runtime'):require(mounts[target][1]=='tmpfs')
    for target in mounts:
        require(not(target=='/opt/ppt-master' or target.startswith('/opt/ppt-master/')))
        require(not any(target.startswith(prefix+'/') for prefix in ('/code','/work/inputs','/work/task','/work/base')))
    for path in (Path('/work'),Path('/work/audit'),Path('/work/task'),Path('/work/inputs'),Path('/work/base')):
        info=path.lstat()
        require(stat.S_ISDIR(info.st_mode) and not path.is_symlink() and info.st_uid==0 and not info.st_mode&0o022)
    status=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    require(all(int(status[k],16)==0xe3 for k in ('CapEff','CapPrm','CapBnd')))
    require(status['NoNewPrivs'].strip()=='1')


def network_preflight():
    report={'schema_version':1,'passed':False}
    write_report(ROOT/'audit/network-preflight.json',report)
    for line in Path('/proc/net/route').read_text().splitlines()[1:]:
        f=line.split()
        require(not(len(f)>7 and f[1]=='00000000' and f[7]=='00000000' and int(f[3],16)&1),'external_default_route')
    v6=Path('/proc/net/ipv6_route')
    if v6.exists():
        for line in v6.read_text().splitlines():
            f=line.split()
            require(not(len(f)>=10 and f[0]=='0'*32 and f[1]=='00' and int(f[8],16)&1 and not int(f[8],16)&0x200),'external_default_route')
    addresses={row[4][0] for row in socket.getaddrinfo('model-proxy',8080,type=socket.SOCK_STREAM)}
    require(addresses=={'172.30.240.2'},'proxy_address_mismatch')
    try:socket.getaddrinfo('example.com',443,type=socket.SOCK_STREAM)
    except socket.gaierror:pass
    else:raise RuntimeError('external_dns_enabled')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open('http://model-proxy:8080/healthz',timeout=5) as response:
        require(response.status==200 and json.load(response)=={'status':'ok'},'proxy_not_ready')
    for host,port in (('172.30.240.1',22),('192.168.137.101',22),('169.254.169.254',80),('62.234.50.20',443)):
        s=socket.socket(socket.AF_INET,socket.SOCK_STREAM);s.settimeout(1)
        try:s.connect((host,port))
        except OSError:pass
        else:raise RuntimeError('forbidden_tcp_succeeded')
        finally:s.close()
    report.update(passed=True,no_default_route=True,external_dns_blocked=True,proxy_exact=True,forbidden_tcp_denied=True)
    write_report(ROOT/'audit/network-preflight.json',report)
    return report


class ControlledDevelopmentPptProfile(PptProfile):
    # This entry is a reviewed development invocation, never the public profile.
    production_ready=True
    def __init__(self,*,runtime_revision,skill_bundle_revision,prepared_context):
        require(type(prepared_context) is PreparedPptContext and prepared_context.receipt['phase']=='plan_initial','bound_initial_context_required')
        require(runtime_revision==RUNTIME_REVISION and skill_bundle_revision==SKILL_BUNDLE_REVISION,'development_pin_mismatch')
        super().__init__(runtime_revision=runtime_revision,skill_bundle_revision=skill_bundle_revision,prepared_context=prepared_context)

    def build_prompt(self,wire):
        return super().build_prompt(wire)+('\n固定工具链：官方Python脚本使用 /opt/hermes/.venv/bin/python 调用；'
            '官方技能根为 /opt/ppt-master/skills/ppt-master。当前仅初始规划，停在Stage1确认门，'
            '不得宣称网站确认、生成SVG/PPTX或跳到Stage2。完成后务必写 /work/result/manifest.json。\n')


def run():
    require(len(sys.argv)==1 and sys.platform=='linux' and os.getresuid()==(0,0,0))
    require(PptProfile.production_ready is False)
    require(not os.environ.get('PPT_UPSTREAM_API_KEY'),'provider_credential_in_worker')
    mount_boundary()
    launch=ContainerLaunch.parse(read_private(ROOT/'task/domain-launch.json',launch=True))
    require(launch.identity.phase=='plan_initial','development_phase_not_enabled')
    lease=ContainerLease(launch);snapshot=lease.poll()
    while snapshot['state']!='running':
        time.sleep(0.05)
        snapshot=lease.poll()
    context=PreparedPptContext.from_mounts(read_json(ROOT/'task','mounted-context.json'),launch.identity,
        runtime_revision=RUNTIME_REVISION,skill_bundle_revision=SKILL_BUNDLE_REVISION)
    network_preflight()
    deadline=int(os.environ.get('PPT_DEADLINE_SECONDS','600'))
    require(60<=deadline<=600,'invalid_development_deadline')
    key=os.environ.get('PPT_MODEL_API_KEY','')
    require(len(key)>=16 and all(33<=ord(c)<=126 for c in key),'missing_task_credential')
    require(os.environ.get('PPT_MODEL_PROXY_ORIGIN')=='http://model-proxy:8080','invalid_model_origin')
    interrupted=threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:interrupted.set())
    gate=ModelGrant(launch.identity,key,ttl_seconds=min(600,snapshot['deadline_monotonic']-time.monotonic()),max_requests=1)
    lease.bind(gate,interrupted)
    control=ROOT/'audit/pi-runtime-control';control.mkdir(mode=0o700)
    resources=ROOT/'audit/pi-skill-resources';resources.mkdir(mode=0o755)
    skill_info=SKILL.lstat()
    require(stat.S_ISREG(skill_info.st_mode) and skill_info.st_nlink==1 and skill_info.st_uid==0 and not skill_info.st_mode&0o002)
    raw=SKILL.read_bytes();skill_hash=hashlib.sha256(raw).hexdigest()
    (resources/'SKILL.md').write_bytes(raw);(resources/'SKILL.md').chmod(0o444)
    manifest=resources/'manifest.json'
    manifest.write_text(json.dumps({'bundle_revision':SKILL_BUNDLE_REVISION,'local_files':[{'path':'SKILL.md','sha256':skill_hash}]},sort_keys=True))
    manifest.chmod(0o444)
    cli=list(Path('/opt/pi').glob('**/pi-coding-agent/dist/cli.js'));require(len(cli)==1)
    for uid in (launch.pi_uid,launch.preview_uid):require(not PiRuntime._uid_processes(uid),'writer_identity_in_use')
    for name in ('project','result'):
        rows=_safe_tree(ROOT/name)
        for path,info in rows:require(info.st_uid==0,'project_not_controller_owned')
        for path,info in rows:_mutate(path,uid=launch.pi_uid,mode=0o700 if stat.S_ISDIR(info.st_mode) else 0o600)
    settings=RuntimeSettings(command=('/usr/local/bin/node',str(cli[0])),node_command=('/usr/local/bin/node',),
        provider='ppt-task-proxy',model=DEFAULT_MODEL,fixture_mode=False,compact_events=True,
        model_api='openai-responses',model_base_url='http://model-proxy:8080/v1',model_input=('text','image'),
        model_max_tokens=8192,model_env={'SCIENTIFIC_MODEL_API_KEY':key},skill_paths=(SKILL,),skill_hashes=(skill_hash,),
        bundle_manifest=manifest,bundle_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        skill_bundle_revision=SKILL_BUNDLE_REVISION,runtime_revision=RUNTIME_REVISION,image_digest=PINNED_IMAGE,
        capability_verified=True,model_verified=True,isolation_verified=True,control_dir=control,run_gid=launch.pi_uid,
        denied_paths=(control,ROOT/'audit/container-authority/control.json'),
        idle_timeout_seconds=180,term_grace_seconds=0.5,kill_grace_seconds=5,max_processes=128)
    profile=ControlledDevelopmentPptProfile(runtime_revision=RUNTIME_REVISION,skill_bundle_revision=SKILL_BUNDLE_REVISION,prepared_context=context)
    now=datetime.now(timezone.utc)
    wire=dict(schema_version=1,domain='ppt',engine='pi',profile='ppt-master',phase=launch.identity.phase,
        job_id=launch.identity.job_id,input_snapshot_sha256=context.receipt['input_snapshot_sha256'],
        runtime_revision=RUNTIME_REVISION,skill_bundle_revision=SKILL_BUNDLE_REVISION,timeout_seconds=deadline,
        created_at=now.isoformat(),execution_deadline_at=(now+timedelta(seconds=deadline)).isoformat(),
        task=context.task,checkpoint=context.checkpoint)
    profile.validate(wire)
    runtime=PiRuntime(settings,profile=profile)
    require(runtime.health()['ready'],'runtime_not_ready')
    events=[]
    try:
        runtime.execute(dict(wire,_runtime_uid=launch.pi_uid),ROOT/'runtime/ppt-attempt',lambda e:events.append(e),interrupted)
        require(not runtime._uid_processes(launch.pi_uid),'writers_not_stopped')
        handoff=read_json(ROOT/'result','manifest.json')
        require(handoff.get('phase')==launch.identity.phase and handoff.get('input_snapshot_sha256')==context.receipt['input_snapshot_sha256']
                and handoff.get('status') in ('awaiting_confirmation','needs_input'),'candidate_handoff_invalid')
        summary={'status':'candidate_handoff','phase':launch.identity.phase,'manifest_status':handoff['status'],
            'development_only':True,'production_profile_enabled':False,'actual_provider_call':True,
            'event_types':[e.get('type') for e in events],'publication':False}
        write_report(ROOT/'audit/pi-execution.json',summary)
        return 0
    finally:
        lease.close();gate.close()


def main():
    try:code=run()
    except Exception as exc:
        code=2
        safe={'ppt_development_boundary_invalid','external_default_route','proxy_address_mismatch',
              'external_dns_enabled','proxy_not_ready','forbidden_tcp_succeeded','development_phase_not_enabled',
              'provider_credential_in_worker','bound_initial_context_required','development_pin_mismatch',
              'invalid_development_deadline','missing_task_credential','invalid_model_origin',
              'runtime_not_ready','candidate_handoff_invalid','project_not_controller_owned','writer_identity_in_use'}
        error_code=getattr(exc,'code',None)
        if error_code is None:error_code=str(exc) if str(exc) in safe else 'development_entry_failed'
        status={'status':'execution_failed','code':error_code,
                'development_only':True,'publication':False}
        try:write_report(ROOT/'audit/pi-execution.json',status)
        except Exception:pass
    print(json.dumps({'development_only':True,'exit_code':code,'publication':False}),flush=True)
    return code


if __name__=='__main__':raise SystemExit(main())
