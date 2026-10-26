"""Read-only prerequisites for a scope-owned, no-model firewall probe."""
from __future__ import annotations
import argparse,hashlib,ipaddress,json,re,uuid
from pathlib import Path
from ppt_service.control.pods import CommandRunner,Docker,Firewall
from ppt_service.control.staging import atomic_json
TARGETS=[ipaddress.ip_network('172.30.240.0/29'),ipaddress.ip_network('172.30.241.0/29')]

def digest_rules(raw):
    lines=[]
    for line in raw.decode().splitlines():
        if not line or line.startswith('#'):continue
        lines.append(re.sub(r'\[\d+:\d+\]','[0:0]',line))
    return hashlib.sha256(('\n'.join(lines)+'\n').encode()).hexdigest(),len(lines)

def preflight(uid):
    runner=CommandRunner();docker=Docker(runner);fw=Firewall(runner);wb,eb,chain=fw.names(uid)
    status=Path('/proc/self/status').read_text();caps=int(re.search(r'^CapEff:\s*([0-9a-f]+)',status,re.M).group(1),16)
    report={'uid':uid,'worker_bridge':wb,'egress_bridge':eb,'chain':chain,'cap_net_admin':bool(caps&(1<<12)),'bridge_nf_iptables':Path('/proc/sys/net/bridge/bridge-nf-call-iptables').read_text().strip()=='1','fixed_subnets':[str(n) for n in TARGETS]}
    code,_=fw.ipt('-S','DOCKER-USER',check=False);report['docker_user_exists']=code==0
    code,_=fw.ipt('-S',chain,check=False);report['owned_chain_absent']=code!=0
    _,raw=docker.call('network','ls','-q');ids=raw.decode().split()
    networks=[]
    if ids:
        _,raw=docker.call('network','inspect',*ids);networks=json.loads(raw)
    occupied=[]
    for network in networks:
        for config in network.get('IPAM',{}).get('Config') or []:
            subnet=config.get('Subnet')
            if subnet:
                actual=ipaddress.ip_network(subnet,strict=False)
                if actual.version==4 and any(actual.overlaps(target) for target in TARGETS):occupied.append(network['Id'])
    report['subnets_unoccupied']=not occupied
    report['owned_network_names_absent']=not any(n.get('Name') in (wb,eb) for n in networks)
    _,raw=runner(['/usr/sbin/ip','-j','link','show']);links=json.loads(raw)
    report['owned_bridge_names_absent']=not any(n.get('ifname') in (wb,eb) for n in links)
    _,raw=runner(['/usr/sbin/ip','-j','-4','route','show','table','all']);routes=json.loads(raw)
    conflicts=[]
    for route in routes:
        dest=route.get('dst','default')
        if dest in ('default','0.0.0.0/0'):continue
        try:network=ipaddress.ip_network(dest,strict=False)
        except ValueError:continue
        if network.version==4 and any(network.overlaps(target) for target in TARGETS):conflicts.append(route)
    report['fixed_subnet_routes_absent']=not conflicts
    _,raw=runner(['/usr/sbin/iptables-save']);report['baseline_iptables_sha256'],report['baseline_rule_line_count']=digest_rules(raw)
    report['baseline_routes_sha256']=hashlib.sha256(json.dumps(sorted(routes,key=lambda r:json.dumps(r,sort_keys=True)),sort_keys=True,separators=(',',':')).encode()).hexdigest()
    required=('cap_net_admin','bridge_nf_iptables','docker_user_exists','owned_chain_absent','subnets_unoccupied','owned_network_names_absent','owned_bridge_names_absent','fixed_subnet_routes_absent')
    report['ready']=all(report[k] for k in required)
    return report
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--case',type=Path,required=True);args=parser.parse_args();report=preflight(str(uuid.uuid4()));atomic_json(args.case/'preflight.json',report);print(json.dumps(report));raise SystemExit(0 if report['ready'] else 2)
