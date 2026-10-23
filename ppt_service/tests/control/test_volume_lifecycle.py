"""Scoped anonymous-volume lifecycle checks; real Docker checks are explicit opt-in."""
import hashlib,json,os,uuid
from pathlib import Path
import pytest
from ppt_service.control.execution import ControlError,Handle,RuntimeConfig
from ppt_service.control.pods import Docker,DockerBackend,DockerSandbox,LABEL,secure_container,mount
from ppt_service.control.staging import atomic_json,read_json
from test_render_validation import FakeDocker as ValidationDocker,inputs


def handle():
    return Handle(str(uuid.uuid4()),str(uuid.uuid4()),1,1,'unused-worker','unused-proxy')


class FirewallSpy:
    def __init__(self):self.removed=[]
    def remove(self,uid):self.removed.append(uid)


@pytest.mark.parametrize('state',[None,{'Running':True,'Pid':1},{'Running':False,'Pid':1}])
def test_backend_refuses_removal_without_stop_proof(tmp_path,state):
    calls=[];h=handle()
    def runner(args,**kwargs):
        args=tuple(args[3:]);calls.append(args)
        if args[0]=='ps':return 0,b'abcdef\n'
        if args[0]=='inspect':return (1,b'') if state is None else (0,json.dumps(state).encode())
        raise AssertionError(args)
    fw=FirewallSpy()
    with pytest.raises(ControlError,match='writers_not_stopped'):DockerBackend(RuntimeConfig(tmp_path),Docker(runner),fw).cleanup(h)
    assert calls[0]==('ps','-aq','--filter',f'label={LABEL}={h.uid}')
    assert not fw.removed


@pytest.mark.parametrize('fail',[False,True])
def test_backend_removes_only_label_selected_stopped_ids(tmp_path,fail):
    calls=[];h=handle();fw=FirewallSpy()
    def runner(args,**kwargs):
        args=tuple(args[3:]);calls.append(args)
        if args[0]=='ps':return 0,b'abcdef\n123abc\n'
        if args[0]=='inspect':return 0,b'{"Running":false,"Pid":0}'
        if args[0]=='rm' and fail:raise ControlError('control_command_failed')
        return 0,b''
    backend=DockerBackend(RuntimeConfig(tmp_path),Docker(runner),fw)
    if fail:
        with pytest.raises(ControlError,match='control_command_failed'):backend.cleanup(h)
        assert not fw.removed
    else:backend.cleanup(h);assert fw.removed==[h.uid]
    assert calls[0]==('ps','-aq','--filter',f'label={LABEL}={h.uid}')
    assert [c for c in calls if c[0]=='rm']==[('rm','--volumes','abcdef','123abc')]


class ParserDocker:
    def __init__(self,fault=None):self.calls=[];self.fault=fault
    def call(self,*args,**kwargs):
        self.calls.append(args)
        if args[0]=='create':
            for value in args:
                if str(value).startswith('type=bind,') and 'dst=/output' in value:self.output=Path(value.split('src=',1)[1].split(',',1)[0])
            self.name=args[args.index('--name')+1]
        if args[0]=='wait':
            if self.fault=='timeout':raise ControlError('control_command_unavailable')
            atomic_json(self.output/'parsed.json',{'sources':[]})
            return 0,b'0'
        return 0,b''


@pytest.mark.parametrize('fault',[None,'timeout'])
def test_parser_removes_exact_created_name_on_success_or_timeout(tmp_path,fault):
    docker=ParserDocker(fault);sandbox=DockerSandbox(RuntimeConfig(tmp_path),docker)
    if fault:
        with pytest.raises(ControlError,match='control_command_unavailable'):sandbox.parse(tmp_path,{})
    else:assert sandbox.parse(tmp_path,{})=={'sources':[]}
    assert [c for c in docker.calls if c[0]=='rm']==[('rm','--force','--volumes',docker.name)]


@pytest.mark.parametrize('fault',[None,'running'])
def test_validation_volume_removal_preserves_stop_gate_and_sources(inputs,tmp_path,fault):
    snapshot,_,_,request=inputs;before={p:p.read_bytes() for p in snapshot.rglob('*') if p.is_file()}
    docker=ValidationDocker(fault);runtime=tmp_path/'runtime';runtime.mkdir()
    sandbox=DockerSandbox(RuntimeConfig(runtime),docker)
    if fault:
        with pytest.raises(ControlError,match='validation_writers_not_stopped') as raised:sandbox.validate(snapshot,request)
        audit=read_json(Path(raised.value.evidence_path),'controller-quarantine.json')
        assert audit['writers_stopped'] is False and audit['transport_verified'] is False
    else:
        result=sandbox.validate(snapshot,request)
        assert result['accepted'] is False
        assert result['persistent_evidence']['publication_authorization'] is False
    created=next(c for c in docker.calls if c[0]=='create');name=created[created.index('--name')+1]
    assert [c for c in docker.calls if c[0]=='rm']==[('rm','--force','--volumes',name)]
    assert all(p.read_bytes()==data for p,data in before.items())
    binds=[x for x in created if str(x).startswith('type=bind,')]
    assert all(x.endswith(',readonly') for x in binds if 'dst=/output' not in x)


real=pytest.mark.skipif(os.getenv('PPT_RUN_VOLUME_LIFECYCLE_TESTS')!='true',reason='Explicit isolated Docker volume test flag required')


class RecordingDocker(Docker):
    """Capture only our newly created container identity and mount metadata."""
    def __init__(self):super().__init__();self.created=[]
    def call(self,*args,**kwargs):
        result=super().call(*args,**kwargs)
        if args[0]=='create' and result[0]==0:
            cid=result[1].decode().strip()
            _,raw=super().call('inspect','--format','{{json .Mounts}}',cid)
            mounts=json.loads(raw);volumes=[m['Name'] for m in mounts if m['Type']=='volume' and m['Destination']=='/opt/data']
            self.created.append((cid,volumes,mounts))
        return result


def assert_removed(docker):
    assert docker.created
    for cid,volumes,_ in docker.created:
        assert docker.inspect(cid) is None
        assert len(volumes)==1
        for volume in volumes:
            code,_=docker.call('volume','inspect',volume,check=False)
            assert code!=0
    print('owned_container_and_volume_removed='+json.dumps([(c,v) for c,v,_ in docker.created]))


@real
def test_real_never_started_secure_container_preserves_permanent_binds(tmp_path):
    docker=RecordingDocker();config=RuntimeConfig(tmp_path);h=handle();name='ppt-volume-test-'+uuid.uuid4().hex
    before=set(docker.call('volume','ls','-q')[1].decode().splitlines());args=secure_container(name,h.uid)+['--network','none']
    originals={}
    for part in ('source','published','checkpoint'):
        root=tmp_path/part;root.mkdir();p=root/'immutable';p.write_bytes(part.encode());originals[p]=p.read_bytes();args+=mount(root,'/fixture/'+part,True)
    cid=None
    try:
        _,raw=docker.call(*(args+['--entrypoint','/opt/hermes/.venv/bin/python',config.worker_image,'-c','pass']));cid=raw.decode().strip()
        state=docker.inspect(cid);assert state['Status']=='created' and not state['Running'] and state['Pid']==0
        owned,volumes,mounts=docker.created[0];assert owned==cid and len(volumes)==1 and volumes[0] not in before
        assert all(not m['RW'] for m in mounts if m['Type']=='bind')
        # The backend helper is production cleanup; network/firewall calls are intercepted
        # because this never-started network-none fixture created neither resource.
        class ContainerOnlyDocker:
            def ids(self,uid):return docker.ids(uid)
            def inspect(self,target):return docker.inspect(target)
            def call(self,*args,**kwargs):
                assert args[0] in ('rm','network')
                return (0,b'') if args[0]=='network' else docker.call(*args,**kwargs)
        DockerBackend(config,ContainerOnlyDocker(),FirewallSpy()).cleanup(h)
        assert_removed(docker)
        assert all(p.read_bytes()==data for p,data in originals.items())
    finally:
        if cid and docker.inspect(cid) is not None:docker.call('rm','--force','--volumes',cid)


@real
def test_real_parser_removes_fresh_implicit_volume(tmp_path):
    tmp_path.chmod(0o755);source=tmp_path/'inputs';source.mkdir();data=b'# Public deterministic volume lifecycle source\n'
    path=source/'source.md';path.write_bytes(data)
    inventory={'schema_version':1,'input_snapshot_sha256':'a'*64,'sources':[{'id':str(uuid.uuid4()),'path':'source.md','sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'media_type':'text/markdown'}]}
    atomic_json(source/'inventory.json',inventory);original={p:p.read_bytes() for p in source.iterdir()}
    code_root=Path(os.environ['PPT_CONTROL_HOST_CODE_ROOT']);code_before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in code_root.glob('*.py')}
    docker=RecordingDocker();before=set(docker.call('volume','ls','-q')[1].decode().splitlines())
    sandbox=DockerSandbox(RuntimeConfig(tmp_path,code_root=code_root),docker);sandbox.scope_uid=str(uuid.uuid4())
    result=sandbox.parse(source,inventory)
    assert len(result['sources'])==1 and 'Public deterministic' in result['sources'][0]['text']
    assert docker.ids(sandbox.scope_uid)==()
    assert all(v not in before for _,volumes,_ in docker.created for v in volumes)
    assert_removed(docker)
    assert all(p.read_bytes()==data for p,data in original.items())
    assert all(hashlib.sha256(p.read_bytes()).hexdigest()==sha for p,sha in code_before.items())
