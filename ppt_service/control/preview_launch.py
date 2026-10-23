"""Root-controller launch of the official preview under a dedicated UID.

This module never renders, never calls a model and never publishes. It only
starts the reviewed preview server as a dropped-privilege identity and checks
that the identity actually took effect.
"""
from __future__ import annotations

import http.client
import json
import os
import stat
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import stop_gate
from .execution import Attempt, ControlError, Layout

PREVIEW_PYTHON = Path('/opt/hermes/.venv/bin/python')
PREVIEW_SERVER = Path('/opt/ppt-master/skills/ppt-master/scripts/svg_editor/server.py')
PREVIEW_LAUNCHER = Path(__file__).resolve().parents[2] / 'pi_worker' / 'runtime' / 'launcher.py'
PREVIEW_PORT = 8765
MAX_LAUNCH_CONFIG = 64 * 1024
MAX_HEALTH_BYTES = 64 * 1024


@dataclass(frozen=True)
class PreviewSpec:
    """Fixed deployment paths. Tests may construct their own trusted spec."""

    launcher: Path
    python: Path
    server: Path
    port: int = PREVIEW_PORT
    lifetime: int = 1800
    ready_timeout: float = 30.0
    max_processes: int = 64

    @classmethod
    def deployed(cls):
        return cls(launcher=PREVIEW_LAUNCHER, python=PREVIEW_PYTHON, server=PREVIEW_SERVER)

    def validate(self):
        for name in ('launcher', 'python', 'server'):
            path = getattr(self, name)
            if not isinstance(path, Path) or not path.is_absolute():
                raise ControlError('invalid_preview_spec')
        if type(self.port) is not int or not 1024 <= self.port <= 65535:
            raise ControlError('invalid_preview_spec')
        if type(self.lifetime) is not int or not 10 <= self.lifetime <= 86400:
            raise ControlError('invalid_preview_spec')
        if type(self.ready_timeout) not in (int, float) or not 1 <= self.ready_timeout <= 120:
            raise ControlError('invalid_preview_spec')
        if type(self.max_processes) is not int or not 2 <= self.max_processes <= 4096:
            raise ControlError('invalid_preview_spec')
        return self


@dataclass(frozen=True)
class PreviewHandle:
    pid: int
    uid: int
    port: int
    project: Path
    config: Path
    process: subprocess.Popen = None

    def stop(self, *, grace=10):
        resources = stop_gate.stop_uids((self.uid,), grace=grace)
        if self.process is not None:
            try:
                self.process.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                raise ControlError('preview_process_not_reaped') from None
        return resources


def _require_linux_root():
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ControlError('preview_supervision_requires_linux_root')


def _safe_info(path):
    info = path.lstat()
    if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
            or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
            or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)):
        raise ControlError('unsafe_preview_project')
    return info


def _safe_directory_chain(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ControlError('unsafe_preview_project')
    for parent in reversed((path, *path.parents)):
        if not stat.S_ISDIR(_safe_info(parent).st_mode):
            raise ControlError('unsafe_preview_project')


def _safe_tree(path):
    """Preflight a stopped tree completely before any privileged mutation."""
    path = Path(path)
    _safe_directory_chain(path)
    rows = []
    def visit(current):
        info = _safe_info(current)
        rows.append((current, info))
        if stat.S_ISDIR(info.st_mode):
            for child in sorted(current.iterdir()):
                visit(child)
    visit(path)
    return rows


def _mutate(path, *, uid=None, mode=None):
    # Ancestors were preflighted under the stopped, single-controller contract.
    info = _safe_info(path)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    if stat.S_ISDIR(info.st_mode):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ControlError('unsafe_preview_project')
        if mode is not None and uid not in (None, 0):
            os.fchmod(descriptor, mode)
        if uid is not None:
            os.fchown(descriptor, uid, uid)
        if mode is not None and uid in (None, 0):
            os.fchmod(descriptor, mode)
    finally:
        os.close(descriptor)


def _preview_uid(allocator, attempt, pi_uid=None):
    verify = getattr(allocator, 'verify', None)
    if not callable(verify):
        raise ControlError('invalid_uid_ledger')
    uid = verify(project_id=attempt.project_id, job_id=attempt.job_id,
                 attempt=attempt.attempt, token=attempt.token, role='preview')
    if type(uid) is not int or not 10000 <= uid < 2**31 - 1 or uid == 65534:
        raise ControlError('invalid_preview_uid')
    bound_pi = verify(project_id=attempt.project_id, job_id=attempt.job_id,
                      attempt=attempt.attempt, token=attempt.token, role='pi')
    if (type(bound_pi) is not int or not 10000 <= bound_pi < 2**31 - 1
            or bound_pi == 65534 or (pi_uid is not None and pi_uid != bound_pi)):
        raise ControlError('invalid_pi_uid')
    if uid == bound_pi:
        raise ControlError('preview_uid_shares_pi_identity')
    return uid


def _project(project, layout):
    if not isinstance(project, Path) or not project.is_absolute():
        raise ControlError('invalid_preview_project')
    layout_project = layout.project
    if project == layout_project or layout_project not in project.parents:
        raise ControlError('preview_project_outside_attempt')
    _safe_tree(project)
    info = project.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ControlError('unsafe_preview_project')
    if not stat.S_ISDIR(info.st_mode):
        raise ControlError('unsafe_preview_project')
    for name in ('design_spec.md', 'spec_lock.md'):
        marker = project / name
        try:
            marker_info = marker.lstat()
        except FileNotFoundError:
            raise ControlError('unsafe_preview_project') from None
        except OSError as exc:
            raise ControlError('unsafe_preview_project') from exc
        if stat.S_ISLNK(marker_info.st_mode) or not stat.S_ISREG(marker_info.st_mode):
            raise ControlError('unsafe_preview_project')
        if marker_info.st_nlink != 1:
            raise ControlError('unsafe_preview_project')
    return project


def _writable_by(info, uid):
    mode = stat.S_IMODE(info.st_mode)
    if info.st_uid == uid:
        return bool(mode & 0o200)
    if info.st_gid == uid:
        return bool(mode & 0o020)
    return bool(mode & 0o002)


def _traversable_by(info, uid):
    mode = stat.S_IMODE(info.st_mode)
    if info.st_uid == uid:
        return bool(mode & 0o100)
    if info.st_gid == uid:
        return bool(mode & 0o010)
    return bool(mode & 0o001)


def _require_traversal(project, layout, uid):
    """Every ancestor from the attempt root down must be usable by the preview."""
    chain = [project]
    for parent in project.parents:
        chain.append(parent)
        if parent == Path(layout.root):
            break
    else:
        raise ControlError('preview_project_outside_attempt')
    for path in reversed(chain):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ControlError('unsafe_preview_project')
        if not _traversable_by(info, uid):
            raise ControlError('preview_path_not_traversable')
    return project


def _trusted_parent(path):
    _safe_directory_chain(path)
    for parent in (Path(path), *Path(path).parents):
        info = parent.lstat()
        mode = stat.S_IMODE(info.st_mode)
        # Root-owned sticky system temporary parents are safe against replacement.
        if info.st_uid != 0 or (mode & 0o022 and not mode & stat.S_ISVTX):
            raise ControlError('unsafe_preview_runtime_dir')


def _private_dir(path, uid):
    _trusted_parent(path.parent)
    if path.exists() or path.is_symlink():
        rows = _safe_tree(path)
        if any(info.st_uid != 0 for _, info in rows):
            raise ControlError('unsafe_preview_runtime_dir')
    else:
        path.mkdir(mode=0o700)
    _mutate(path, mode=0o700, uid=uid)
    return path


def _config(project, uid, spec, denied_paths):
    return {
        'controller_pid': os.getpid(),
        'max_processes': spec.max_processes,
        'cgroup': None,
        'uid': uid,
        'gid': uid,
        'denied_paths': [str(path) for path in denied_paths],
        'cwd': str(project),
        'command': [str(spec.python), str(spec.server), str(project), '--live',
                    '--no-browser', '--port', str(spec.port), '--timeout', str(spec.lifetime)],
    }


def _environment(home, tmp):
    # No inherited HOME, proxies, credentials, PYTHONPATH or platform flags.
    return {'PATH': '/opt/hermes/.venv/bin:/usr/bin:/bin', 'HOME': str(home),
            'TMPDIR': str(tmp), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
            'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1',
            'PLAYWRIGHT_BROWSERS_PATH': '/opt/ms-playwright'}


def _write_config(path, config):
    raw = json.dumps(config, sort_keys=True).encode('utf-8')
    if len(raw) > MAX_LAUNCH_CONFIG:
        raise ControlError('preview_config_too_large')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, raw)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path


def _port_is_free(port):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', port))


def _health(port):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=0.5)
    try:
        connection.request('GET', '/api/health')
        response = connection.getresponse()
        if response.status != 200:
            raise ControlError('preview_health_rejected')
        raw = response.read(MAX_HEALTH_BYTES + 1)
    finally:
        connection.close()
    if len(raw) > MAX_HEALTH_BYTES:
        raise ControlError('preview_health_rejected')
    value = json.loads(raw)
    if type(value) is not dict:
        raise ControlError('preview_health_rejected')
    return value


def _process_uid(pid):
    fields = dict(line.split(':', 1) for line in Path(f'/proc/{pid}/status').read_text().splitlines() if ':' in line)
    return [int(value) for value in fields['Uid'].split()]


def wait_ready(process, uid, project, spec, *, interrupted=lambda: False):
    deadline = time.monotonic() + float(spec.ready_timeout)
    while time.monotonic() < deadline:
        if interrupted():
            raise ControlError('preview_interrupted')
        if process.poll() is not None:
            raise ControlError('preview_exited_before_ready')
        try:
            health = _health(spec.port)
        except (OSError, ValueError, http.client.HTTPException, ControlError):
            time.sleep(0.1)
            continue
        if (health.get('service') != 'live_preview' or health.get('status') != 'ok'
                or health.get('project') != str(project) or health.get('live') is not True
                or health.get('pid') != process.pid):
            raise ControlError('preview_health_identity_mismatch')
        if set(_process_uid(process.pid)) != {uid}:
            raise ControlError('preview_identity_not_dropped')
        return health
    raise ControlError('preview_ready_timeout')


def launch_preview(attempt, layout, allocator, project, *, spec=None, denied_paths=(),
                   grace=10, interrupted=lambda: False, pi_uid=None):
    """Start the official preview for one attempt under its reserved preview UID."""
    _require_linux_root()
    if type(attempt) is not Attempt or type(layout) is not Layout:
        raise ControlError('invalid_preview_request')
    spec = (PreviewSpec.deployed() if spec is None else spec).validate()
    for path in (spec.launcher, spec.python, spec.server):
        if not path.is_file():
            raise ControlError('preview_binary_missing')
    project = _project(project, layout)
    uid = _preview_uid(allocator, attempt, pi_uid)
    if not _writable_by(project.lstat(), uid):
        raise ControlError('preview_project_not_writable')
    _require_traversal(project, layout, uid)
    for value in denied_paths:
        if type(value) is not Path or not value.is_absolute():
            raise ControlError('invalid_denied_path')
    audit = Path(layout.audit)
    _trusted_parent(audit.parent)
    if audit.exists() or audit.is_symlink():
        _trusted_parent(audit)
        if not stat.S_ISDIR(_safe_info(audit).st_mode):
            raise ControlError('unsafe_preview_audit')
    for name in ('preview-home', 'preview-tmp'):
        path = audit / name
        if path.exists() or path.is_symlink():
            if any(info.st_uid != 0 for _, info in _safe_tree(path)):
                raise ControlError('unsafe_preview_runtime_dir')
    audit.mkdir(exist_ok=True, mode=0o711)
    # Traverse-only access reaches private HOME/tmp; config files remain 0600.
    _mutate(audit, mode=0o711)
    home = _private_dir(audit / 'preview-home', uid)
    tmp = _private_dir(audit / 'preview-tmp', uid)
    _port_is_free(spec.port)
    config = _write_config(audit / ('preview-launch-' + uuid.uuid4().hex + '.json'),
                           _config(project, uid, spec, denied_paths))
    process = subprocess.Popen([sys.executable, str(spec.launcher), str(config)],
                               cwd=str(project), env=_environment(home, tmp),
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        wait_ready(process, uid, project, spec, interrupted=interrupted)
    except BaseException:
        try:
            stop_gate.stop_uids((uid,), grace=grace)
        except Exception as exc:
            raise ControlError('writers_not_stopped') from exc
        raise
    return PreviewHandle(process.pid, uid, spec.port, project, config, process)
