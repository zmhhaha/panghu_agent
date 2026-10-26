"""Read-only, fail-closed host lease for the fixed offline container entry.

Only the trusted host writes authority. The worker UID cannot read the private
record. Monotonic deadlines are valid only on the same Linux boot. Invalid,
missing, expired or regressed records irreversibly revoke this reader.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time
import uuid
from .model_grant import ModelGrant, ModelIdentity
from ..control.pi_container import PINNED_IMAGE

STATES = frozenset(('reserved','created','starting','running','revoking','stopped','stop_unproven'))
LAUNCH_FIELDS = frozenset(('schema','identity','nonce','pi_uid','preview_uid','image_digest','mode'))
CONTROL_FIELDS = frozenset(('schema','identity','nonce','pi_uid','preview_uid','boot_id',
    'deadline_monotonic','seq','revoked','state','container_id','image_digest'))


class ContainerLeaseError(RuntimeError):pass


def require(ok):
    if not ok:raise ContainerLeaseError('container_authority_invalid')


def _identity(value):
    require(type(value) is dict and set(value)=={'project_id','job_id','attempt','token','phase'})
    try:return ModelIdentity(**value)
    except (ValueError,TypeError,AttributeError):raise ContainerLeaseError('container_identity_invalid') from None


def _uids(value):
    for name in ('pi_uid','preview_uid'):
        uid=value[name]
        require(type(uid) is int and 10000<=uid<2**31-1 and uid!=65534)
    require(value['pi_uid']!=value['preview_uid'])


@dataclass(frozen=True)
class ContainerLaunch:
    identity:ModelIdentity
    nonce:str
    pi_uid:int
    preview_uid:int
    image_digest:str
    mode:str

    @classmethod
    def parse(cls,value):
        require(type(value) is dict and set(value)==LAUNCH_FIELDS)
        require(type(value['schema']) is int and value['schema']==1)
        require(type(value['nonce']) is str and re.fullmatch('[0-9a-f]{64}',value['nonce']))
        require(value['image_digest']==PINNED_IMAGE and value['mode'] in ('complete','hold'))
        _uids(value)
        return cls(_identity(value['identity']),value['nonce'],value['pi_uid'],
                   value['preview_uid'],value['image_digest'],value['mode'])


def decode(raw):
    def pairs(items):
        result={}
        for k,v in items:
            require(k not in result);result[k]=v
        return result
    def invalid(_):raise ContainerLeaseError('container_authority_invalid')
    try:return json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=invalid)
    except (ValueError,UnicodeError,RecursionError):raise ContainerLeaseError('container_authority_invalid') from None


def read_private(path,*,launch=False):
    """No-follow bounded read; root-owned immutable parents prevent replacement."""
    path=Path(path)
    require(path.is_absolute() and '..' not in path.parts)
    for parent in reversed(path.parents):
        info=parent.lstat()
        sticky=info.st_uid==0 and bool(info.st_mode&stat.S_ISVTX)
        require(stat.S_ISDIR(info.st_mode) and not parent.is_symlink() and info.st_uid==0
                and (not info.st_mode&0o022 or sticky))
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid==0
                and info.st_size<=16384 and stat.S_IMODE(info.st_mode)==(0o444 if launch else 0o600))
        raw=stream.read(16385)
        require(len(raw)<=16384)
    return decode(raw)


class ContainerLease:
    def __init__(self,launch,path=Path('/work/audit/container-authority/control.json'),*,
                 clock=time.monotonic,boot_id=None,reader=read_private):
        require(type(launch) is ContainerLaunch)
        self.launch,self.path,self._clock,self._reader=launch,Path(path),clock,reader
        self._boot=boot_id or Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        require(str(uuid.UUID(self._boot))==self._boot)
        self._seq=0;self._digest=None;self._failed=False;self._error=None
        self._lock=threading.Lock();self._stop=threading.Event();self._thread=None

    def poll(self):
        with self._lock:
            if self._failed:raise ContainerLeaseError(self._error or 'container_authority_revoked')
            try:
                v=self._reader(self.path)
                require(type(v) is dict and set(v)==CONTROL_FIELDS and type(v['schema']) is int and v['schema']==1)
                require(_identity(v['identity'])==self.launch.identity and v['nonce']==self.launch.nonce
                    and v['pi_uid']==self.launch.pi_uid and v['preview_uid']==self.launch.preview_uid
                    and v['image_digest']==self.launch.image_digest and v['boot_id']==self._boot)
                _uids(v)
                require(type(v['seq']) is int and v['seq']>0 and v['seq']>=self._seq)
                require(type(v['revoked']) is bool and not v['revoked'] and v['state'] in ('starting','running'))
                require(v['state'] in STATES)
                require(v['container_id'] is None or (type(v['container_id']) is str and re.fullmatch('[0-9a-f]{64}',v['container_id'])))
                deadline=v['deadline_monotonic'];now=self._clock()
                require(type(deadline) in (int,float) and math.isfinite(deadline) and now<deadline<=now+600)
                digest=hashlib.sha256(json.dumps(v,sort_keys=True,allow_nan=False).encode()).hexdigest()
                require(v['seq']!=self._seq or digest==self._digest)
                self._seq,self._digest=v['seq'],digest
                return dict(v)
            except Exception as exc:
                self._failed=True
                self._error='container_authority_revoked'
                raise ContainerLeaseError(self._error) from None

    def bind(self,grant,interrupted):
        require(type(grant) is ModelGrant and grant.identity==self.launch.identity and self._thread is None)
        initial=self.poll()
        require(grant.renew(self.launch.identity,initial['deadline_monotonic']-self._clock()))
        applied=initial['seq']
        def watch():
            nonlocal applied
            while not self._stop.wait(0.02):
                try:
                    snapshot=self.poll()
                    if snapshot['seq']!=applied:
                        if not grant.renew(self.launch.identity,snapshot['deadline_monotonic']-self._clock()):
                            raise ContainerLeaseError('model_authority_revoked')
                        applied=snapshot['seq']
                except Exception:
                    grant.revoke(self.launch.identity)
                    interrupted.set()
                    return
        self._thread=threading.Thread(target=watch,daemon=True,name='ppt-container-lease')
        self._thread.start()

    def snapshot(self):
        with self._lock:return dict(sequence=self._seq,failed=self._failed,error=self._error)

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
            require(not self._thread.is_alive())
