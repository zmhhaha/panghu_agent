"""Real local-only isolation proof using exact production Firewall/templates."""
from __future__ import annotations
import argparse,json,os,signal,subprocess,sys,time
from pathlib import Path
from ppt_service.control.execution import PINNED_WORKER,ControlError
from ppt_service.control.pods import Docker,Firewall,secure_container,mount
from ppt_service.control.staging import atomic_json,read_json
from test_network_isolation_preflight import preflight
from test_network_isolation_fixture import call
PYTHON='/opt/hermes/.venv/bin/python'

def ready(path,timeout=4):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if (path/'ready.json').is_file():return read_json(path,'ready.json')
        time.sleep(0.05)
    raise RuntimeError('owned_listener_not_ready')
def check(value,label):
    if not value:raise RuntimeError(label)
def own_config(docker,cid,created_after):
    import datetime,re
    _,raw=docker.call('inspect','--format','{{json .HostConfig}}',cid);host=json.loads(raw)
    _,raw=docker.call('inspect','--format','{{json .Mounts}}',cid);mounts=json.loads(raw)
    _,raw=docker.call('inspect','--format','{{.Config.User}}',cid);user=raw.decode().strip()
    expected={'/fixture.py':False,'/output':True};binds=[m for m in mounts if m['Type']=='bind'];extras=[m for m in mounts if m['Type']!='bind'];volumes=[]
    for entry in extras:
        if entry['Type']=='tmpfs' and entry['Destination']=='/tmp':continue
        check(entry['Type']=='volume' and entry['Destination']=='/opt/data' and re.fullmatch(r'[0-9a-f]{64}',entry.get('Name','')),'unexpected_extra_mount')
        _,raw=docker.call('volume','inspect',entry['Name']);volume=json.loads(raw)[0]
        created=datetime.datetime.fromisoformat(volume['CreatedAt'].replace('Z','+00:00')).timestamp()
        _,raw=docker.call('ps','-aq','--no-trunc','--filter','volume='+entry['Name']);references=raw.decode().split()
        check('com.docker.volume.anonymous' in (volume.get('Labels') or {}) and volume['Mountpoint']==entry['Source'] and created_after-2<=created<=time.time()+2 and references==[cid],'anonymous_volume_ownership_unproven')
        volumes.append({'name':entry['Name'],'source':entry['Source'],'created_at':volume['CreatedAt'],'anonymous_label':True,'own_container_id':cid})
    check(len(volumes)==1,'expected_single_image_anonymous_volume')
    checks={'nonroot':user=='10000:10000','readonly_root':host['ReadonlyRootfs'] is True,'cap_drop_all':'ALL' in host.get('CapDrop',[]),'no_new_privileges':bool(set(host.get('SecurityOpt',[]))&{'no-new-privileges','no-new-privileges:true'}),'dns_disabled':host.get('Dns')==['127.0.0.1'],'ipv6_disabled':host.get('Sysctls',{}).get('net.ipv6.conf.all.disable_ipv6')=='1','only_owned_fixture_output_and_fresh_image_volume':len(binds)==2 and {m['Destination']:m['RW'] for m in binds}==expected,'restart_disabled':host.get('RestartPolicy',{}).get('Name')=='no'}
    return checks,volumes

def cleanup_containers(docker,created,volumes):
    errors=[];stopped=True;removed={}
    for cid in reversed(created):
        try:
            code,_=docker.call('rm','--force','--volumes',cid,check=False)
            # Failed inspect alone cannot prove absence: daemon failures also fail.
            _,raw=docker.call('ps','-aq','--no-trunc')
            if cid in raw.decode().split():
                errors.append('container_remains');stopped=False
            elif code:errors.append('container_removal_failed');stopped=False
        except Exception:stopped=False;errors.append('container_cleanup_unavailable')
    for volume in volumes:
        try:
            _,raw=docker.call('volume','ls','-q')
            removed[volume['name']]=volume['name'] not in raw.decode().split()
            if not removed[volume['name']]:errors.append('owned_volume_remains')
        except Exception:removed[volume['name']]=False;errors.append('volume_verification_unavailable')
    return stopped,errors,removed

def run(case):
    original=read_json(case,'preflight.json');uid=original['uid'];before=preflight(uid);check(before['ready'],'fresh_preflight_blocked')
    docker=Docker();fw=Firewall();wb,eb,chain=fw.names(uid);fixture=case/'scripts/test_network_isolation_fixture.py'
    work=case/'runs'/uid;work.mkdir(parents=True,exist_ok=False);outputs={name:work/name for name in ('proxy','worker','sentinel','host')}
    for path in outputs.values():path.mkdir();path.chmod(0o777 if path.name!='host' else 0o700)
    check(not (case/'network-isolation-result.json').exists(),'result_already_exists')
    created=[];networks=[];attempted=False;host_process=None;report={'uid':uid,'before':before,'model_calls':0,'upstream_requests':0,'approved_public_443_positive':'not_tested','paid_runtime_or_k8s_acceptance':False,'container_ids':{},'network_ids':{},'host_listener_pid':None,'previous_unknown_anonymous_volumes':{'possible_count':2,'untouched':True},'scope_names':{'worker_bridge':wb,'egress_bridge':eb,'chain':chain}}
    def save():atomic_json(case/'network-isolation-result.json',report)
    def interrupted(*_):raise RuntimeError('signal_interrupted')
    signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
    save()
    def create(role,network,ip):
        name='ppt-net-'+uid.replace('-','')[:12]+'-'+role
        args=secure_container(name,uid,memory='256m',cpus='0.5')+['--network',network,'--ip',ip,'--env','RES_OPTIONS=attempts:1 timeout:1']+mount(fixture,'/fixture.py',True)+mount(outputs[role],'/output')
        args+=['--entrypoint',PYTHON,PINNED_WORKER,'/fixture.py','--mode',role,'--out','/output']
        if role=='worker':args+=['--host-port',str(host_port),'--uid',uid]
        created_after=time.time();_,raw=docker.call(*args);cid=raw.decode().strip();created.append(cid);report['container_ids'][role]=cid;save()
        checks,volumes=own_config(docker,cid,created_after);report.setdefault('secure_container_checks',{})[role]=checks;report.setdefault('fresh_anonymous_volumes',{})[role]=volumes;save()
        check(all(checks.values()),'secure_container_contract_failed');return cid
    try:
        check(not docker.ids(uid),'scope_containers_already_exist')
        for name,subnet,internal in ((wb,'172.30.240.0/29',True),(eb,'172.30.241.0/29',False)):
            args=['network','create','--label','io.ppt-service.control='+uid,'--subnet',subnet,'--opt','com.docker.network.bridge.enable_icc=false','--opt','com.docker.network.bridge.name='+name]
            if internal:args+=['--internal']
            _,raw=docker.call(*args,name);network_id=raw.decode().strip();networks.append(network_id);report['network_ids'][name]=network_id;save()
        attempted=True;fw.install(uid,'62.234.50.20')
        proxy=create('proxy',eb,'172.30.241.2');docker.call('network','connect','--ip','172.30.240.2',wb,proxy)
        sentinel=create('sentinel',eb,'172.30.241.3');docker.call('start',proxy,sentinel)
        ready(outputs['proxy']);ready(outputs['sentinel'])
        for role,cid,ports in (('proxy',proxy,['8080','8081']),('sentinel',sentinel,['8080'])):
            _,raw=docker.call('exec',cid,PYTHON,'/fixture.py','--mode','health','--out','/output','--ports',*ports);health=json.loads(raw)
            check(all(h['reachable'] and h['fixed_body'] for h in health.values()),'owned_negative_listener_not_listening');report.setdefault('listener_positive_controls',{})[role]=health
        proxy_before=read_json(outputs['proxy'],'counts.json');sentinel_before=read_json(outputs['sentinel'],'counts.json')
        host_process=subprocess.Popen([sys.executable,str(fixture),'--mode','host','--out',str(outputs['host'])],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8'},start_new_session=True)
        report['host_listener_pid']=host_process.pid;host_port=ready(outputs['host'])['ports'][0];check(host_port>=1024,'host_port_not_high');report['host_listener_bind']='172.30.240.1';report['host_listener_port']=host_port
        health=call('172.30.240.1',host_port);check(health['reachable'] and health['fixed_body'],'owned_host_listener_not_listening');host_before=read_json(outputs['host'],'counts.json');report['listener_positive_controls']['host']=health
        worker=create('worker',wb,'172.30.240.3');docker.call('start',worker);_,raw=docker.call('wait',worker,timeout=20);check(int(raw.strip())==0,'worker_fixture_failed')
        worker_result=read_json(outputs['worker'],'worker-probes.json');report['worker_probes']=worker_result
        report['allow_path_passed']=worker_result['allowed_proxy_8080']['reachable'] and worker_result['allowed_proxy_8080']['fixed_body']
        _,raw=fw.ipt('-L',chain,'-n','-v','-x');report['owned_chain_counters']=raw.decode();save()
        for field in ('blocked_proxy_8081','blocked_proxy_egress_ip','blocked_host_sentinel'):check(not worker_result[field]['reachable'],field+'_unexpectedly_reachable')
        for field in ('dns_resolution_failed','ipv6_connection_failed','ipv6_disabled','caps_zero','no_new_privileges','root_write_blocked','docker_socket_absent'):check(worker_result[field],field+'_failed')
        check(worker_result['uid']==10000,'worker_not_nonroot')
        _,raw=docker.call('exec',proxy,PYTHON,'/fixture.py','--mode','proxy-probe','--out','/output','--host-port',str(host_port));proxy_result=json.loads(raw);report['proxy_probes']=proxy_result
        check(all(not p['reachable'] for p in proxy_result.values()),'nonallow_proxy_egress_reachable')
        proxy_after=read_json(outputs['proxy'],'counts.json');sentinel_after=read_json(outputs['sentinel'],'counts.json');host_after=read_json(outputs['host'],'counts.json')
        report['sentinel_counters']={'proxy_before':proxy_before,'proxy_after':proxy_after,'egress_before':sentinel_before,'egress_after':sentinel_after,'host_before':host_before,'host_after':host_after};save()
        check(report['allow_path_passed'],'allowed_worker_proxy_path_failed')
        check(proxy_after['counts']['8080']==proxy_before['counts']['8080']+1,'allowed_proxy_counter_mismatch')
        check(proxy_after['counts']['8081']==proxy_before['counts']['8081'],'blocked_proxy_counter_changed')
        check(sentinel_after==sentinel_before,'blocked_egress_sentinel_counter_changed');check(host_after==host_before,'blocked_host_counter_changed')
        report['sentinel_counters']={'proxy_before':proxy_before,'proxy_after':proxy_after,'egress_before':sentinel_before,'egress_after':sentinel_after,'host_before':host_before,'host_after':host_after}
        report['probe_checks_passed']=True
    except Exception as exc:
        report['probe_checks_passed']=False;report['error_type']=type(exc).__name__;report['error_code']=exc.code if isinstance(exc,ControlError) else str(exc)[:100]
    finally:
        cleanup=[];writers_stopped=True
        if host_process is not None:
            try:
                if host_process.poll() is None:host_process.terminate()
                host_process.wait(timeout=3)
            except subprocess.TimeoutExpired:host_process.kill();host_process.wait(timeout=3)
            report['host_listener_stopped']=host_process.poll() is not None
        volumes=[v for values in report.get('fresh_anonymous_volumes',{}).values() for v in values]
        writers_stopped,container_errors,removed=cleanup_containers(docker,created,volumes)
        cleanup.extend(container_errors);report['anonymous_volumes_removed']=removed
        writers_stopped=writers_stopped and (host_process is None or report.get('host_listener_stopped',False))
        if writers_stopped:
            if attempted:
                try:fw.remove(uid)
                except Exception:cleanup.append('firewall_cleanup_unavailable')
            for network_id in reversed(networks):
                code,_=docker.call('network','rm',network_id,check=False)
                if code:cleanup.append('owned_network_remains')
        else:cleanup.append('owned_firewall_retained_for_unknown_writers')
        try:
            after=preflight(uid);report['after']=after;report['other_rules_unchanged']=before['baseline_iptables_sha256']==after['baseline_iptables_sha256'];report['other_routes_unchanged']=before['baseline_routes_sha256']==after['baseline_routes_sha256'];report['remaining_scope_containers']=list(docker.ids(uid));report['cleanup_complete']=writers_stopped and not cleanup and after['ready'] and not report['remaining_scope_containers']
        except Exception:report['cleanup_complete']=False;cleanup.append('cleanup_verification_unavailable')
        report['cleanup_errors']=cleanup;report['passed']=report.get('probe_checks_passed',False) and report['cleanup_complete'] and report.get('other_rules_unchanged',False) and report.get('other_routes_unchanged',False)
        atomic_json(case/'network-isolation-result.json',report);print(json.dumps(report))
    return report['passed']
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--case',type=Path,required=True);args=parser.parse_args();raise SystemExit(0 if run(args.case) else 2)
