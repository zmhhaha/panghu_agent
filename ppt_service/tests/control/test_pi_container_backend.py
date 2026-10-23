"""Fake Docker protocol/failure tests; never invokes Docker or a public task API."""
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid
import pytest
from ppt_service.control import pi_container_backend as m
from ppt_service.control.execution import Attempt, ControlError, Layout, RuntimeConfig


class FakeDocker:
    def __init__(self):
        self.calls = []; self.objects = {}; self.cid = 'a' * 64
        self.fail = None; self.refuse_stop = False; self.after_create = False

    def call(self, *args, **kwargs):
        self.calls.append(args)
        if self.fail == args[0]: raise ControlError('daemon_unavailable')
        if args[0] == 'create':
            labels = {args[i+1].split('=',1)[0]:args[i+1].split('=',1)[1]
                      for i,x in enumerate(args) if x == '--label'}
            self.objects[self.cid] = dict(Id=self.cid,Image=m.PINNED_IMAGE,
                Config=dict(Labels=labels,Image=m.PINNED_IMAGE),
                HostConfig=dict(PidMode='',AutoRemove=False,RestartPolicy=dict(Name='no',MaximumRetryCount=0)),
                State=dict(Status='created',Running=False,Restarting=False,Pid=0,ExitCode=0))
            if self.after_create: raise ControlError('ambiguous_create')
            return 0,self.cid.encode()
        if args[0] == 'inspect': return 0,json.dumps([self.objects[args[-1]]]).encode()
        if args[0] == 'start': self.state.update(Status='running',Running=True,Pid=123)
        if args[0] in ('stop','kill') and not self.refuse_stop:
            self.state.update(Status='exited',Running=False,Pid=0)
        if args[0] == 'ps': return 0,'\n'.join(self.objects).encode()
        return 0,b''

    @property
    def state(self): return self.objects[self.cid]['State']


@pytest.fixture
def rig(request, monkeypatch):
    temp = tempfile.TemporaryDirectory(prefix='pcb-')
    request.addfinalizer(temp.cleanup)
    tmp_path = Path(temp.name).resolve()
    # Exercise the actual receipt JSON and lifecycle on stdlib Windows runners too.
    # Only host ownership/flock primitives are substituted; Linux test below uses real ones.
    a = Attempt(str(uuid.uuid4()),str(uuid.uuid4()),1,1,'generate',{'hash':'b'*64},'session')
    root = tmp_path/'projects'/a.project_id/'attempts'/a.job_id/'1-1'
    root.mkdir(parents=True)
    for n in ('inputs','task','home','project','result','audit','base'): (root/n).mkdir()
    layout = Layout(root,*(root/n for n in ('inputs','task','home','project','result','audit','base')))
    code = tmp_path/'source'; (code/'ppt_service/images/pi_candidate').mkdir(parents=True)
    (code/'ppt_service/images/pi_candidate/domain_smoke.py').write_text('')
    docker = FakeDocker(); backend = m.PiContainerBackend(RuntimeConfig(tmp_path),docker)
    monkeypatch.setattr(m.sys,'platform','linux')
    monkeypatch.setattr(m.os,'geteuid',lambda:0,raising=False)
    monkeypatch.setattr(m,'_parents',lambda p:None)
    monkeypatch.setattr(m,'_sync_directory',lambda p:None)
    monkeypatch.setattr(m,'ordinary',lambda p,mode:p.lstat())
    def exclusive(path,value,mode):
        with path.open('xb') as out: out.write(json.dumps(value).encode())
    monkeypatch.setattr(m,'exclusive',exclusive)
    @contextmanager
    def lock(a,layout): yield layout.audit/'container-authority'
    monkeypatch.setattr(backend,'_lock',lock)
    monkeypatch.setattr(m,'boot_id',lambda:'00000000-0000-0000-0000-000000000001')
    class Allocator:
        def verify(self,**kw): return 10001 if kw['role']=='pi' else 10002
    return backend,docker,a,layout,Allocator(),code


def create(rig, **kw):
    b,d,a,l,allocator,code = rig
    return b.create(a,l,allocator,code,**kw)


def receipt(rig):
    return json.loads((rig[3].audit/'container-authority/control.json').read_text())


def test_inherited_image_labels_do_not_break_exact_attempt_identity(rig):
    b,d,_,_,_,_=rig
    h=create(rig)
    d.objects[d.cid]['Config']['Labels']['ppt.execution']='offline-domain-test-only'
    d.objects[d.cid]['Config']['Labels']['ppt.production-ready']='false'
    assert b.observe(h).running
    assert b.stop(h).all_writers_stopped is True


def test_extra_label_in_authority_namespace_is_rejected(rig):
    b,d,_,_,_,_=rig
    h=create(rig)
    d.objects[d.cid]['Config']['Labels'][m.LABEL+'.unreviewed']='yes'
    with pytest.raises(ControlError):b.observe(h)


def test_create_fixed_plan_and_durable_order(rig):
    b,d,a,l,_,_ = rig
    before = d.call
    def checked(*args,**kw):
        if args[0] == 'create':
            r=receipt(rig); assert r['state']=='reserved' and r['container_id'] is None
            assert (l.task/'domain-launch.json').exists()
        if args[0] == 'start':
            r=receipt(rig); assert r['state']=='starting' and r['container_id']==d.cid
        return before(*args,**kw)
    d.call=checked
    h=create(rig)
    argv=d.calls[0]
    assert argv[0]=='create' and '--rm' not in argv
    assert argv[argv.index('--restart')+1]=='no'
    assert argv[argv.index('--network')+1]=='none'
    assert argv.index('--label') < argv.index(m.PINNED_IMAGE)
    assert receipt(rig)['state']=='running' and h.identity==m.identity(a)
    assert len(h.nonce)==64 and m.PiContainerBackend.productionUserTasks is False


def test_stop_failure_no_proof_collect_or_remove(rig):
    b,d,*_=rig; h=create(rig); d.refuse_stop=True
    proof=b.stop(h)
    assert proof.all_writers_stopped is False
    assert receipt(rig)['state']=='stop_unproven' and receipt(rig)['revoked']
    with pytest.raises(ControlError): b.collect(h,proof)
    with pytest.raises(ControlError): b.cleanup(h,proof)
    assert not any(x[0]=='rm' for x in d.calls)


@pytest.mark.parametrize('mutation', ['cid','labels','restart','pidns','autoremove'])
def test_foreign_or_unsafe_container_never_targeted(rig,mutation):
    b,d,*_=rig; h=create(rig); obj=d.objects[d.cid]
    if mutation=='cid': obj['Id']='b'*64
    if mutation=='labels': obj['Config']['Labels'][m.LABEL+'.nonce']='b'*64
    if mutation=='restart': obj['HostConfig']['RestartPolicy']['Name']='always'
    if mutation=='pidns': obj['HostConfig']['PidMode']='host'
    if mutation=='autoremove': obj['HostConfig']['AutoRemove']=True
    assert not b.stop(h).all_writers_stopped
    assert not any(x[0] in ('stop','kill','rm') for x in d.calls)


@pytest.mark.parametrize('status', ['paused','dead','unknown'])
def test_non_exited_not_stop_evidence(rig,status):
    b,d,*_=rig; h=create(rig); d.state.update(Status=status,Running=False,Pid=0)
    d.refuse_stop=True
    assert not b.stop(h).all_writers_stopped


def test_valid_stop_retains_then_explicit_cleanup(rig):
    b,d,*_=rig; h=create(rig); proof=b.stop(h)
    assert proof.all_writers_stopped and not any(x[0]=='rm' for x in d.calls)
    b.cleanup(h,proof)
    assert d.calls[-1]==('rm','--volumes',h.container_id)


class Queue:
    lease_seconds = 90
    def __init__(self,status='running',commit=None): self.status=status; self.calls=0; self.commit=commit
    def heartbeat(self,a):
        self.calls+=1
        if self.commit: self.commit()
        return self.status


def test_wrong_heartbeat_identity_has_no_effect(rig):
    b,d,a,*_=rig; h=create(rig); q=Queue(); before=receipt(rig)
    assert not b.heartbeat(h,q,replace(a,token=2))
    assert q.calls==0 and receipt(rig)==before


def test_cancel_then_late_heartbeat_never_revives(rig):
    b,d,a,*_=rig; h=create(rig); q=Queue('cancelling')
    assert not b.heartbeat(h,q,a)
    seq=receipt(rig)['seq']
    assert not b.heartbeat(h,Queue(),a)
    assert receipt(rig)['revoked'] and receipt(rig)['seq']>seq


@pytest.mark.parametrize('during_commit',[False,True])
def test_expiry_cannot_be_revived_by_database_commit(rig,monkeypatch,during_commit):
    b,d,a,*_=rig; now=[10.0]; monkeypatch.setattr(m.time,'monotonic',lambda:now[0])
    h=create(rig,lease_seconds=2)
    q=Queue(commit=lambda:now.__setitem__(0,13.0))
    if not during_commit: now[0]=13.0
    assert not b.heartbeat(h,q,a)
    assert q.calls==int(during_commit) and receipt(rig)['revoked']


def test_heartbeat_deadline_deducts_commit_latency_and_caps_sql_lease(rig,monkeypatch):
    b,d,a,*_=rig
    now=[10.0];monkeypatch.setattr(m.time,'monotonic',lambda:now[0])
    h=create(rig,lease_seconds=600)
    assert h.lease_seconds==b.config.lease_seconds==90
    q=Queue(commit=lambda:now.__setitem__(0,30.0));q.lease_seconds=60
    assert b.heartbeat(h,q,a)
    assert receipt(rig)['deadline_monotonic']==70.0


def test_successful_heartbeat_commits_before_extending(rig):
    b,d,a,*_=rig; h=create(rig); old=receipt(rig)
    def commit(): assert receipt(rig)==old
    assert b.heartbeat(h,Queue(commit=commit),a)
    assert receipt(rig)['seq']==old['seq']+1


@pytest.mark.parametrize('failure',['create','start','inspect'])
def test_crash_markers_retained_and_recover_never_starts(rig,failure):
    b,d,a,l,*_=rig
    if failure=='create': d.after_create=True
    else: d.fail=failure
    with pytest.raises(ControlError): create(rig)
    r=receipt(rig)
    assert r['state']=={'create':'reserved','inspect':'created','start':'starting'}[failure]
    d.fail=None; d.after_create=False; count=sum(x[0]=='start' for x in d.calls)
    proof=b.recover(a,l)
    assert proof.all_writers_stopped
    assert sum(x[0]=='start' for x in d.calls)==count
    assert receipt(rig)['revoked']


@pytest.mark.parametrize('matches',[0,2])
def test_unknown_cid_zero_or_multiple_matches_fail_closed(rig,matches):
    b,d,a,l,*_=rig; d.after_create=True
    with pytest.raises(ControlError): create(rig)
    obj=d.objects[d.cid]
    d.objects.clear()
    if matches:
        d.objects.update({d.cid:obj,'b'*64:dict(obj,Id='b'*64)})
    with pytest.raises(ControlError): b.recover(a,l)
    assert receipt(rig)['revoked'] and receipt(rig)['container_id'] is None
    assert not any(x[0] in ('stop','kill','rm') for x in d.calls)


def test_changed_handle_cannot_pick_kill_target(rig):
    b,d,*_=rig; h=create(rig)
    with pytest.raises(ControlError): b.stop(replace(h,container_id='b'*64))
    assert not any(x[0] in ('stop','kill') for x in d.calls)


def test_repeated_create_never_overwrites_authority(rig):
    h=create(rig); old=receipt(rig)
    with pytest.raises(FileExistsError): create(rig)
    assert receipt(rig)==old


def test_launch_metadata_tamper_blocks_docker_actions(rig):
    b,d,a,l,*_=rig; h=create(rig)
    path=l.task/'domain-launch.json'; value=json.loads(path.read_text()); value['nonce']='b'*64
    path.write_text(json.dumps(value)); before=len(d.calls)
    with pytest.raises(ControlError): b.stop(h)
    assert len(d.calls)==before


def test_unknown_status_stays_unproven_even_if_docker_would_stop(rig):
    b,d,*_=rig; h=create(rig); d.state['Status']='unknown'
    assert not b.stop(h).all_writers_stopped
    assert not any(x[0] in ('stop','kill') for x in d.calls)


def test_daemon_unavailable_stop_retains_revoked_marker(rig):
    b,d,*_=rig; h=create(rig); d.fail='inspect'
    assert not b.stop(h).all_writers_stopped
    assert receipt(rig)['revoked'] and receipt(rig)['state']=='stop_unproven'


@pytest.mark.skipif(sys.platform!='linux',reason='real Linux ownership/permissions required')
def test_native_private_files_reject_links_and_wrong_modes(tmp_path):
    if os.geteuid()!=0: pytest.skip('root-owned authority required')
    path=tmp_path/'authority.json'
    m.exclusive(path,{'schema':1},0o600)
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError): m.exclusive(path,{},0o600)
    alias=tmp_path/'alias'; os.link(path,alias)
    with pytest.raises(Exception): m.ordinary(path,0o600)
    alias.unlink(); path.chmod(0o644)
    with pytest.raises(Exception): m.ordinary(path,0o600)


def test_json_duplicate_keys_and_nonbytes_refused():
    for raw in (b'{"seq":1,"seq":2}', '[]', b'x'*(2*1024**2+1)):
        with pytest.raises(ControlError): m.decoded(raw)
