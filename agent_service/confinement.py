"""Measure this controller's kernel confinement before reading task credentials."""
from __future__ import annotations

from contextlib import contextmanager
import os
import stat
import sys

from .contracts import ServiceError


APPARMOR_LABEL = 'agent-capsule-inputs (enforce)'
STATUS = '/proc/self/status'
APPARMOR_CURRENT = '/proc/self/attr/apparmor/current'
LSM_CURRENT = '/proc/self/attr/current'
LIMIT = 64 * 1024


@contextmanager
def _current_proc_file(path, mode):
    """Open only fixed current-process proc files, rejecting links/special files.

    /proc/self is kernel-generated; address our numeric PID instead so every
    opened path component can use O_NOFOLLOW. proc regular files have dynamic
    contents and usually st_size=0, so bound reads instead of trusting size.
    """
    relatives = {STATUS: ('status',), APPARMOR_CURRENT: ('attr', 'apparmor', 'current'),
                 LSM_CURRENT: ('attr', 'current')}
    if sys.platform != 'linux' or path not in relatives or not hasattr(os, 'O_NOFOLLOW'):
        raise ServiceError('controller_confinement_unavailable', 503)
    descriptors = []
    try:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptors.append(os.open('/proc', directory_flags))
        descriptors.append(os.open(str(os.getpid()), directory_flags, dir_fd=descriptors[-1]))
        components = relatives[path]
        for component in components[:-1]:
            descriptors.append(os.open(component, directory_flags, dir_fd=descriptors[-1]))
        before = os.stat(components[-1], dir_fd=descriptors[-1], follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid():
            raise ServiceError('controller_confinement_unavailable', 503)
        descriptor = os.open(components[-1], mode | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=descriptors[-1])
        descriptors.append(descriptor)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(before, opened):
            raise ServiceError('controller_confinement_unavailable', 503)
        yield descriptor
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _read_current_proc(path):
    with _current_proc_file(path, os.O_RDONLY) as descriptor:
        chunks, size = [], 0
        while True:
            chunk = os.read(descriptor, min(4096, LIMIT + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > LIMIT:
                raise ServiceError('controller_confinement_unavailable', 503)
        return b''.join(chunks).decode('ascii')


def _read_apparmor(reader):
    try:
        return reader(APPARMOR_CURRENT), APPARMOR_CURRENT
    except FileNotFoundError:
        # Only absence permits the older generic LSM interface.
        return reader(LSM_CURRENT), LSM_CURRENT


def verify_controller_confinement(*, read_only_mounts, _reader=None):
    """Require effective NNP/seccomp and, for mount pools, exact enforced label.

    The private reader hook is for offline tests; there is no environment/CLI
    switch or alternative proc root. Seccomp mode 2 proves filtering is active;
    the existing Pod spec still requires the runtime's RuntimeDefault profile.
    """
    reader = _reader or _read_current_proc
    try:
        if type(read_only_mounts) is not bool:
            raise ValueError()
        raw = reader(STATUS)
        if type(raw) is not str or not raw or len(raw) > LIMIT or '\x00' in raw:
            raise ValueError()
        fields = {}
        for line in raw.splitlines():
            if ':' not in line:
                raise ValueError()
            name, value = line.split(':', 1)
            if name in fields:
                raise ValueError()
            fields[name] = value.strip()
        if fields.get('NoNewPrivs') != '1':
            raise ServiceError('controller_no_new_privileges_required', 503)
        if fields.get('Seccomp') != '2':
            raise ServiceError('controller_seccomp_filter_required', 503)
        label = None
        if read_only_mounts:
            label, _ = _read_apparmor(reader)
            if label not in (APPARMOR_LABEL, APPARMOR_LABEL + '\n'):
                raise ServiceError('controller_apparmor_not_enforced', 503)
        return dict(no_new_privileges=True, seccomp_filtering=True,
                    apparmor_label=APPARMOR_LABEL if read_only_mounts else None)
    except (OSError, ValueError, TypeError, UnicodeError):
        raise ServiceError('controller_confinement_unavailable', 503) from None


def _restrict_to_fixed_profile(path):
    if path not in (APPARMOR_CURRENT, LSM_CURRENT):
        raise ServiceError('controller_confinement_unavailable', 503)
    command = b'changeprofile agent-capsule-inputs'
    with _current_proc_file(path, os.O_WRONLY) as descriptor:
        if os.write(descriptor, command) != len(command):
            raise ServiceError('controller_confinement_unavailable', 503)


def ensure_controller_confinement(*, read_only_mounts, _reader=None, _writer=None):
    """Self-restrict only an exactly unconfined controller to the fixed profile.

    No policy is loaded, capability added or NNP/seccomp setting changed. Both
    the initial prerequisites and final exact enforced label are measured. A
    wrong/complain profile is rejected rather than transitioned or weakened.
    """
    reader = _reader or _read_current_proc
    try:
        if type(read_only_mounts) is not bool:
            raise ValueError()
        verify_controller_confinement(read_only_mounts=False, _reader=reader)
        transitioned = False
        if read_only_mounts:
            label, path = _read_apparmor(reader)
            if label in ('unconfined', 'unconfined\n'):
                (_writer or _restrict_to_fixed_profile)(path)
                transitioned = True
            elif label not in (APPARMOR_LABEL, APPARMOR_LABEL + '\n'):
                raise ServiceError('controller_apparmor_not_enforced', 503)
        measured = verify_controller_confinement(read_only_mounts=read_only_mounts, _reader=reader)
        return dict(**measured, apparmor_self_restricted=transitioned)
    except (OSError, ValueError, TypeError, UnicodeError):
        raise ServiceError('controller_confinement_unavailable', 503) from None
