"""Inspect only a newly created, never-started own template container."""
import json,uuid
from pathlib import Path
from ppt_service.control.pods import Docker,secure_container,mount
from ppt_service.control.execution import PINNED_WORKER
case=Path('/root/ppt-service-network-test-20261022');uid=str(uuid.uuid4());docker=Docker();cid=None
try:
    args=secure_container('ppt-net-template-'+uid[:8],uid,memory='256m',cpus='0.5')+['--network','none']+mount(case/'scripts/test_network_isolation_fixture.py','/fixture.py',True)+mount(case/'run/proxy','/output')+['--entrypoint','/opt/hermes/.venv/bin/python',PINNED_WORKER,'--version']
    _,raw=docker.call(*args);cid=raw.decode().strip()
    _,raw=docker.call('inspect','--format','{{json .HostConfig}}',cid);host=json.loads(raw)
    result={'container_id':cid,'never_started':True,'host_config':{k:host.get(k) for k in ('ReadonlyRootfs','CapDrop','SecurityOpt','Dns','Sysctls','RestartPolicy')}}
    _,raw=docker.call('inspect','--format','{{json .Mounts}}',cid);result['mounts']=[{k:m.get(k) for k in ('Type','Destination','RW')} for m in json.loads(raw)]
    _,raw=docker.call('inspect','--format','{{.Config.User}}',cid);result['user']=raw.decode().strip();print(json.dumps(result))
finally:
    if cid:docker.call('rm','--force',cid)
