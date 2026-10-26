"""Attempt-scoped Linux UID supervision inside one private PID-namespace Pod.

The controller is PID 1, root and trusted. The Agent runs as its reserved UID without
capabilities; a process group or an engine terminal flag is never stop proof.
Kubernetes Pod UID and process-namespace identity fence this local observation.
The external provider must additionally observe the exact retained Pod stop.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import time
import uuid

from .contracts import canonical_bytes
from .engines import StopEvidence


AGENT_UID = 10001


class StopScopeError(RuntimeError):
    def __init__(self, code='host_stop_scope_invalid'):
        self.code = code
        super().__init__(code)


def _uuid(value):
    try:
        return type(value) is str and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


@dataclass(frozen=True)
class KernelScope:
    boot_id: str
    pid_namespace: str


def private_kernel_scope() -> KernelScope:
    # A hostPID Pod cannot become the host's PID 1; wrappers/tini must not hide
    # the controller. This image's fixed ENTRYPOINT executes Python directly.
    if (sys.platform != 'linux' or os.geteuid() != 0 or os.getpid() != 1
            or os.getppid() != 0 or not hasattr(os, 'pidfd_open')
            or not hasattr(signal, 'pidfd_send_signal')):
        raise StopScopeError('private_pid_namespace_required')
    own = os.readlink('/proc/self/ns/pid')
    if own != os.readlink('/proc/1/ns/pid') or not re.fullmatch(r'pid:\[[1-9][0-9]*\]', own):
        raise StopScopeError('private_pid_namespace_required')
    boot = Path('/proc/sys/kernel/random/boot_id').read_text(encoding='ascii').strip()
    if not _uuid(boot):
        raise StopScopeError()
    return KernelScope(boot, own)


def _ordinary_private(path, owner=0):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != owner
            or info.st_mode & 0o077 or stat.S_ISLNK(info.st_mode)):
        raise StopScopeError('host_control_storage_invalid')
    return info


class LinuxUidStopController:
    """Persist one immutable attempt/Pod/UID binding before any native launch.

    Underscored function injection exists for offline kernel-contract tests only;
    the worker entrypoint exposes no fixture, command or alternate /proc option.
    """
    def __init__(self, control_dir: Path, attempt_id: str, pod_uid: str,
                 request_sha256: str, *, agent_uid=AGENT_UID, _scope_reader=None, _clock=time.monotonic,
                 _sleep=time.sleep, stop_seconds=3.0, _control_uid=0):
        if (not _uuid(attempt_id) or not _uuid(pod_uid)
                or not re.fullmatch(r'[0-9a-f]{64}', request_sha256)
                or type(agent_uid) is not int or not 1 <= agent_uid <= 2**31 - 1
                or not 0 < stop_seconds <= 10):
            raise StopScopeError()
        self.control_dir = Path(os.path.abspath(control_dir))
        self.attempt_id, self.pod_uid = attempt_id, pod_uid
        self.request_sha256 = request_sha256
        # The default is retained for old offline fixtures. The production host
        # supplies its provider-reserved UID from trusted capsule environment.
        self.agent_uid = agent_uid
        self._scope_reader = _scope_reader or private_kernel_scope
        self._clock, self._sleep = _clock, _sleep
        self.stop_seconds = stop_seconds
        self._control_uid = _control_uid
        self._scope = self._scope_reader()
        self._binding = dict(schema_version=1, attempt_id=attempt_id, pod_uid=pod_uid,
                             request_sha256=request_sha256, agent_uid=agent_uid,
                             kernel=asdict(self._scope))
        self._record = None

    @property
    def consumed(self):
        return bool(self._record and self._record['revoked'])

    def initialize(self):
        for parent in (*reversed(self.control_dir.parents), self.control_dir):
            if parent.is_symlink():
                raise StopScopeError('host_control_storage_invalid')
        self.control_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.control_dir.stat()
        if info.st_uid != self._control_uid or info.st_mode & 0o077:
            raise StopScopeError('host_control_storage_invalid')
        path = self.control_dir / 'uid-scope.json'
        if path.exists() or path.is_symlink():
            _ordinary_private(path, self._control_uid)
            try:
                raw = path.read_bytes()
                if len(raw) > 4096:
                    raise ValueError()
                record = json.loads(raw)
                if (set(record) != {'binding', 'revoked', 'stopped'}
                        or record['binding'] != self._binding
                        or type(record['revoked']) is not bool or type(record['stopped']) is not bool
                        or record['stopped'] and not record['revoked']):
                    raise ValueError()
            except (ValueError, TypeError, KeyError):
                raise StopScopeError('host_scope_binding_mismatch') from None
            self._record = record
        else:
            self._write(dict(binding=self._binding, revoked=False, stopped=False))
        self._assert_scope(self.attempt_id)

    def _write(self, value):
        path = self.control_dir / 'uid-scope.json'
        pending = self.control_dir / ('scope-' + uuid.uuid4().hex + '.tmp')
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(canonical_bytes(value))
                stream.flush()
                os.fsync(stream.fileno())
            pending.replace(path)
            directory = os.open(self.control_dir, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            _ordinary_private(path, self._control_uid)
        finally:
            pending.unlink(missing_ok=True)
        self._record = value

    def _assert_scope(self, execution_id):
        if execution_id != self.attempt_id or self._scope_reader() != self._scope or self._record is None:
            raise StopScopeError('host_scope_binding_mismatch')

    def _uid_processes(self):
        matches = []
        for path in Path('/proc').iterdir():
            if not path.name.isdecimal():
                continue
            try:
                fields = dict(line.split(':', 1) for line in (path / 'status').read_text().splitlines() if ':' in line)
            except FileNotFoundError:
                continue
            except OSError:
                raise StopScopeError('process_inventory_unavailable') from None
            try:
                ids = [int(item) for item in fields['Uid'].split()]
                state = fields['State'].strip().split()[0]
                if len(ids) != 4:
                    raise ValueError()
            except (ValueError, KeyError, IndexError):
                raise StopScopeError('process_inventory_unavailable') from None
            if self.agent_uid in ids and state not in {'Z', 'X'}:
                matches.append(int(path.name))
        return matches

    def stop(self, execution_id):
        self._assert_scope(execution_id)
        self._write(dict(self._record, revoked=True, stopped=False))
        deadline = self._clock() + self.stop_seconds
        empty = 0
        while self._clock() < deadline:
            self._assert_scope(execution_id)
            pids = self._uid_processes()
            if not pids:
                empty += 1
                if empty >= 2:
                    self._write(dict(self._record, stopped=True))
                    return
            else:
                empty = 0
                for pid in pids:
                    try:
                        fd = os.pidfd_open(pid)
                    except ProcessLookupError:
                        continue
                    try:
                        # A reused PID must still belong to our UID after opening
                        # its stable pidfd, before sending a signal to that object.
                        if pid in self._uid_processes():
                            try:
                                signal.pidfd_send_signal(fd, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                    finally:
                        os.close(fd)
            self._sleep(0.02)
        raise StopScopeError('writers_not_stopped')

    def prove_stopped(self, execution_id):
        self._assert_scope(execution_id)
        if not self._record['revoked'] or not self._record['stopped']:
            return StopEvidence(execution_id, False, ())
        for _ in range(2):
            if self._uid_processes():
                return StopEvidence(execution_id, False, ())
            self._sleep(0.02)
            self._assert_scope(execution_id)
        return StopEvidence(execution_id, True,
                            ('pod:' + self.pod_uid, self._scope.pid_namespace,
                             'uid:' + str(self.agent_uid), 'attempt:' + self.attempt_id))
