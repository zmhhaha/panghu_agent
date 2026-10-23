"""Linux-root, offline-selftest-only Docker lifecycle and durable lease authority."""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
import uuid
from .execution import Attempt, ControlError, Layout, Observation, RuntimeConfig, StopProof
from .pi_container import PINNED_IMAGE, plan_attempt_container, plan_domain_smoke
from .pods import Docker
from .pi_uid import _parents, _safe_stat, _sync_directory
from ..worker.model_grant import ModelIdentity

LABEL = 'io.ppt-service.offline'
HEX = re.compile(r'[0-9a-f]{64}')
STATES = {'reserved', 'created', 'starting', 'running', 'revoking', 'stopped', 'stop_unproven'}
FIELDS = {'schema', 'identity', 'nonce', 'pi_uid', 'preview_uid', 'boot_id',
          'deadline_monotonic', 'seq', 'revoked', 'state', 'container_id', 'image_digest'}


def require(ok, code='invalid_container_authority'):
    if not ok: raise ControlError(code)


def identity(a):
    require(type(a) is Attempt)
    return ModelIdentity(a.project_id, a.job_id, a.attempt, a.token, a.phase)


def boot_id():
    value = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    require(str(uuid.UUID(value)) == value)
    return value


def decoded(raw):
    require(type(raw) is bytes and len(raw) <= 2 * 1024**2)
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result
    try: return json.loads(raw, object_pairs_hook=pairs)
    except (ValueError, UnicodeError): raise ControlError('invalid_container_json') from None


def ordinary(path, mode):
    info = _safe_stat(path, private=mode == 0o600)
    require(stat.S_IMODE(info.st_mode) == mode and info.st_nlink == 1)
    return info


def exclusive(path, value, mode):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as out:
        os.fchmod(out.fileno(), mode)
        out.write(json.dumps(value, sort_keys=True, allow_nan=False).encode())
        out.flush(); os.fsync(out.fileno())
    ordinary(path, mode)
    _sync_directory(path.parent)


@dataclass(frozen=True)
class ContainerHandle:
    identity: ModelIdentity
    nonce: str
    container_id: str | None
    layout: Layout
    attempt: Attempt
    pi_uid: int
    preview_uid: int
    lease_seconds: float


class PiContainerBackend:
    productionUserTasks = False

    def __init__(self, config, docker=None):
        require(type(config) is RuntimeConfig)
        self.config, self.docker = config, docker or Docker()

    def _layout(self, a, layout):
        require(sys.platform == 'linux' and os.geteuid() == 0, 'container_requires_linux_root')
        require(type(layout) is Layout)
        expected = Path(self.config.data_root) / 'projects' / a.project_id / 'attempts' / a.job_id / f'{a.attempt}-{a.token}'
        require(layout.root == expected and expected.is_absolute(), 'attempt_layout_mismatch')
        require(all(getattr(layout, n) == expected / n for n in ('inputs','task','home','project','result','audit','base')),
                'attempt_layout_mismatch')
        _parents(layout.audit)
        # Reuse all canonical mount/path checks even during recovery.
        plan_attempt_container(self.config, a, layout, pi_uid=10001)
        return layout.audit / 'container-authority'

    @contextmanager
    def _lock(self, a, layout):
        import fcntl
        directory = self._layout(a, layout)
        _safe_stat(directory, directory=True, private=True)
        path = directory / 'lock'
        ordinary(path, 0o600)
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
        try:
            require(os.fstat(fd).st_ino == path.lstat().st_ino)
            fcntl.flock(fd, fcntl.LOCK_EX)
            _safe_stat(directory, directory=True, private=True)
            yield directory
        finally: os.close(fd)

    def _read(self, directory, a, h=None):
        path = directory / 'control.json'
        ordinary(path, 0o600)
        with path.open('rb') as source: r = decoded(source.read(2 * 1024**2 + 1))
        require(type(r) is dict and set(r) == FIELDS)
        require(type(r['schema']) is int and r['schema'] == 1 and type(r['identity']) is dict)
        require(ModelIdentity(**r['identity']) == identity(a))
        require(type(r['nonce']) is str and HEX.fullmatch(r['nonce']))
        require(all(type(r[k]) is int and 10000 <= r[k] < 2**31-1 and r[k] != 65534 for k in ('pi_uid','preview_uid')) and r['pi_uid'] != r['preview_uid'])
        require(type(r['boot_id']) is str and str(uuid.UUID(r['boot_id'])) == r['boot_id'])
        require(type(r['deadline_monotonic']) is float and math.isfinite(r['deadline_monotonic']))
        require(type(r['seq']) is int and r['seq'] > 0 and type(r['revoked']) is bool)
        require(type(r['state']) is str and r['state'] in STATES and r['image_digest'] == PINNED_IMAGE)
        require(r['container_id'] is None or type(r['container_id']) is str and HEX.fullmatch(r['container_id']))
        if h is not None:
            require(type(h) is ContainerHandle and h.identity == identity(a) and h.attempt == a)
            require((h.nonce,h.container_id,h.pi_uid,h.preview_uid) == (r['nonce'],r['container_id'],r['pi_uid'],r['preview_uid']))
        launch = directory.parent.parent / 'task' / 'domain-launch.json'
        ordinary(launch, 0o444)
        with launch.open('rb') as source: metadata = decoded(source.read(8193))
        expected = {k:r[k] for k in ('schema','identity','nonce','pi_uid','preview_uid','image_digest')}
        require(type(metadata) is dict and metadata.get('mode') in ('complete','hold'))
        require(metadata == dict(expected, mode=metadata['mode']))
        return r

    def _write(self, directory, r, **changes):
        ordinary(directory / 'control.json', 0o600)
        value = dict(r, **changes, seq=r['seq'] + 1)
        temp = directory / ('receipt-' + secrets.token_hex(16))
        exclusive(temp, value, 0o600)
        os.replace(temp, directory / 'control.json'); _sync_directory(directory)
        r.clear(); r.update(value)

    def _handle(self, a, layout, r, ttl=90):
        return ContainerHandle(identity(a),r['nonce'],r['container_id'],layout,a,r['pi_uid'],r['preview_uid'],float(ttl))

    def _labels(self, a, r):
        values = dict(r['identity'], nonce=r['nonce'], uid=a.uid,
                      purpose='offline-selftest', image=PINNED_IMAGE)
        return {LABEL + '.' + k:str(v) for k,v in values.items()}

    def _inspect(self, a, r):
        require(r['container_id'] is not None, 'container_id_unknown')
        _, raw = self.docker.call('inspect',r['container_id'])
        rows = decoded(raw)
        require(type(rows) is list and len(rows) == 1 and type(rows[0]) is dict)
        obj = rows[0]
        require(obj.get('Id') == r['container_id'] and obj.get('Image') == PINNED_IMAGE, 'foreign_container')
        config, host, state = obj.get('Config'),obj.get('HostConfig'),obj.get('State')
        require(type(config) is dict and type(host) is dict and type(state) is dict)
        labels = config.get('Labels')
        require(type(labels) is dict and {k:v for k,v in labels.items() if k.startswith(LABEL + '.')} == self._labels(a,r)
                and config.get('Image') == PINNED_IMAGE, 'foreign_container')
        require(host.get('PidMode') in ('','private') and host.get('AutoRemove') is False
                and host.get('RestartPolicy') == {'Name':'no','MaximumRetryCount':0}, 'unsafe_container_policy')
        require(type(state.get('Status')) is str and type(state.get('Running')) is bool
                and type(state.get('Restarting')) is bool and type(state.get('Pid')) is int)
        return state

    def create(self, a, layout, allocator, code_root, lease_seconds=90, mode='complete'):
        require(type(lease_seconds) in (int,float) and math.isfinite(lease_seconds) and 0 < lease_seconds <= 600)
        require(mode in ('complete','hold'))
        lease_seconds = min(float(lease_seconds), float(self.config.lease_seconds))
        directory = self._layout(a,layout)
        binding = {k:getattr(a,k) for k in ('project_id','job_id','attempt','token')}
        pi = allocator.verify(**binding,role='pi'); preview = allocator.verify(**binding,role='preview')
        require(type(pi) is int and type(preview) is int and pi != preview)
        plan = plan_domain_smoke(self.config,a,layout,pi_uid=pi,code_root=code_root)
        directory.mkdir(mode=0o700); _sync_directory(directory.parent)
        exclusive(directory / 'lock', {}, 0o600)
        with self._lock(a,layout):
            r = dict(schema=1,identity=asdict(identity(a)),nonce=secrets.token_hex(32),pi_uid=pi,
                     preview_uid=preview,boot_id=boot_id(),deadline_monotonic=float(time.monotonic()+lease_seconds),
                     seq=1,revoked=False,state='reserved',container_id=None,image_digest=PINNED_IMAGE)
            metadata = {k:r[k] for k in ('schema','identity','nonce','pi_uid','preview_uid','image_digest')}
            exclusive(layout.task/'domain-launch.json',dict(metadata,mode=mode),0o444)
            exclusive(directory/'control.json',r,0o600)
            self._read(directory,a)
            argv = list(plan.argv); require(argv[:2] == ['run','--rm'])
            argv[:2] = ['create']; index = argv.index(PINNED_IMAGE)
            labels = ['--restart','no']
            for key,value in self._labels(a,r).items(): labels += ['--label',key+'='+value]
            argv[index:index] = labels
            argv.append('--host-controlled')
            _,raw = self.docker.call(*argv)
            cid = raw.decode('ascii').strip(); require(HEX.fullmatch(cid), 'invalid_container_id')
            self._write(directory,r,state='created',container_id=cid)
            self._inspect(a,r)
            require(r['boot_id'] == boot_id() and r['deadline_monotonic'] > time.monotonic(), 'container_lease_expired')
            self._write(directory,r,state='starting')
            self.docker.call('start',cid)
            self._write(directory,r,state='running')
            return self._handle(a,layout,r,lease_seconds)

    def observe(self, h):
        with self._lock(h.attempt,h.layout) as d:
            r = self._read(d,h.attempt,h); state = self._inspect(h.attempt,r)
            return Observation(state['Running'],state.get('ExitCode') if not state['Running'] else None)

    def _stop(self, a, directory, r):
        self._write(directory,r,revoked=True,state='revoking')
        proven = False
        try:
            state = self._inspect(a,r)
            if state['Status'] in ('running','created'):
                self.docker.call('stop','--time','2',r['container_id'],timeout=5,check=False)
                state = self._inspect(a,r)
                if state['Running'] and state['Status'] != 'paused':
                    self.docker.call('kill',r['container_id'],check=False)
                    state = self._inspect(a,r)
            proven = (state['Status'] == 'exited' and state['Running'] is False
                      and state['Restarting'] is False and state['Pid'] == 0)
        except (ControlError,OSError,ValueError): pass
        self._write(directory,r,state='stopped' if proven else 'stop_unproven')
        return StopProof(a.uid,proven,(r['container_id'],) if r['container_id'] else ())

    def stop(self, h):
        with self._lock(h.attempt,h.layout) as d:
            r = self._read(d,h.attempt,h)
            return self._stop(h.attempt,d,r)

    def heartbeat(self, h, queue, a):
        if type(h) is not ContainerHandle or identity(a) != h.identity: return False
        with self._lock(h.attempt,h.layout) as d:
            r = self._read(d,h.attempt,h)
            def live():
                return not r['revoked'] and r['state'] == 'running' and r['boot_id'] == boot_id() and r['deadline_monotonic'] > time.monotonic()
            if not live(): self._stop(a,d,r); return False
            state = self._inspect(a,r)
            if state['Status'] != 'running' or not state['Running'] or state['Restarting']:
                self._stop(a,d,r); return False
            sql_ttl = getattr(queue,'lease_seconds',None)
            if type(sql_ttl) not in (int,float) or not math.isfinite(sql_ttl) or not 0 < sql_ttl <= 600:
                self._stop(a,d,r); return False
            ttl = min(h.lease_seconds,float(self.config.lease_seconds),float(sql_ttl))
            renewed_deadline = time.monotonic() + ttl
            try: status = queue.heartbeat(a)  # This adapter returns only after transaction commit.
            except Exception: status = None
            if status != 'running' or not live() or renewed_deadline <= time.monotonic():
                self._stop(a,d,r); return False
            self._write(d,r,deadline_monotonic=float(renewed_deadline))
            return True

    def recover(self, a, layout):
        with self._lock(a,layout) as d:
            r = self._read(d,a)
            if r['container_id'] is None:
                # Reserved is deliberately ambiguous: Docker create may have committed.
                self._write(d,r,revoked=True,state='revoking')
                args = ['ps','-aq','--no-trunc']
                for key,value in self._labels(a,r).items(): args += ['--filter','label='+key+'='+value]
                _,raw = self.docker.call(*args)
                require(len(raw) <= 8192)
                ids = raw.decode('ascii').splitlines()
                require(len(ids) == 1 and HEX.fullmatch(ids[0]), 'container_recovery_unproven')
                candidate = dict(r,container_id=ids[0]); self._inspect(a,candidate)
                self._write(d,r,container_id=ids[0])
            return self._stop(a,d,r)

    def _proof(self, h, proof, d):
        r = self._read(d,h.attempt,h)
        require(type(proof) is StopProof and proof.uid == h.attempt.uid and proof.all_writers_stopped is True
                and proof.resource_ids == (h.container_id,) and r['revoked'] and r['state'] == 'stopped', 'writers_not_stopped')
        state = self._inspect(h.attempt,r)
        require(state['Status'] == 'exited' and state['Running'] is False and state['Restarting'] is False and state['Pid'] == 0, 'writers_not_stopped')

    def cleanup(self, h, proof):
        with self._lock(h.attempt,h.layout) as d:
            self._proof(h,proof,d)
            self.docker.call('rm','--volumes',h.container_id)

    def collect(self, h, proof):
        from .pi_collect import freeze_candidate
        with self._lock(h.attempt,h.layout) as d:
            self._proof(h,proof,d)
            return freeze_candidate(self.config,h.attempt,h.layout,proof)
