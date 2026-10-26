"""Opt-in deployed-image smoke; invoked only by the authorized host deployment.

The installed old image has two removal sites; validate delegates to _run.
This check imports the actual image module (no source overlay), captures only
its newly created child, and proves that child's implicit volume is removed.
"""
import hashlib,json,os,uuid
from pathlib import Path
import pytest
from ppt_service.control import pods
from ppt_service.control.execution import RuntimeConfig
from ppt_service.control.staging import atomic_json

@pytest.mark.skipif(os.getenv('PPT_CLEANUP_DEPLOYMENT_SMOKE')!='true',reason='Explicit owned-host opt-in required')
def test_deployed_image_parser_volume(tmp_path):
    assert hashlib.sha256(Path(pods.__file__).read_bytes()).hexdigest()=='b7f8e19ccaa2bd5b58acb1c9268dd22c8027666f9d6eafc5750feaf47b30d8c1'
    created=[]
    class RecordingDocker(pods.Docker):
        def call(self,*args,**kwargs):
            result=super().call(*args,**kwargs)
            if args[0]=='create' and result[0]==0:
                cid=result[1].decode().strip()
                _,raw=super().call('inspect',cid);d=json.loads(raw)[0]
                volumes=[m['Name'] for m in d['Mounts'] if m['Type']=='volume' and m['Destination']=='/opt/data']
                assert len(volumes)==1 and d['HostConfig']['NetworkMode']=='none'
                assert all(not m['RW'] for m in d['Mounts'] if m['Type']=='bind' and m['Destination']!='/output')
                created.append({'container_id':cid,'volumes':volumes,'scope':d['Config']['Labels'][pods.LABEL]})
            return result
    tmp_path.chmod(0o755);inputs=tmp_path/'inputs';inputs.mkdir()
    source=b'Public cleanup deployment regression.';p=inputs/'source.md';p.write_bytes(source)
    inventory={'schema_version':1,'input_snapshot_sha256':'a'*64,'sources':[{'id':str(uuid.uuid4()),'path':p.name,'sha256':hashlib.sha256(source).hexdigest(),'bytes':len(source),'media_type':'text/markdown'}]}
    atomic_json(inputs/'inventory.json',inventory)
    docker=RecordingDocker();code=Path(os.environ['PPT_CONTROL_HOST_CODE_ROOT']);before=hashlib.sha256((code/'parsing.py').read_bytes()).hexdigest()
    sandbox=pods.DockerSandbox(RuntimeConfig(tmp_path,code_root=code),docker);sandbox.scope_uid=str(uuid.uuid4())
    result=sandbox.parse(inputs,inventory)
    assert result['sources'][0]['text'].strip()==source.decode()
    assert docker.ids(sandbox.scope_uid)==() and len(created)==1
    for row in created:
        assert docker.inspect(row['container_id']) is None
        for volume in row['volumes']:assert docker.call('volume','inspect',volume,check=False)[0]!=0
    assert p.read_bytes()==source and hashlib.sha256((code/'parsing.py').read_bytes()).hexdigest()==before
    print('deployment_smoke='+json.dumps({'removed':created,'parser_source_sha256':before,'actual_image_module_sha256_verified':True}))
