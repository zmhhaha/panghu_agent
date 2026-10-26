"""Linux controller-owned, read-only input mounts in the private capsule.

Requires an explicitly admitted mount-capable host. The Agent child loses all
capabilities before exec. Absence of mount support never falls back to a copy.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import stat

from .contracts import ServiceError

MS_BIND, MS_REMOUNT, MS_RDONLY = 4096, 32, 1
MS_NOSUID, MS_NODEV, MS_NOEXEC = 2, 4, 8


class ReadOnlyInputs:
    def __init__(self, workspace, context):
        self.workspace, self.context = Path(workspace), context
        self.targets = []

    @staticmethod
    def _libc():
        if os.name != 'posix' or os.geteuid() != 0 or os.environ.get('AGENT_READ_ONLY_MOUNTS') != '1':
            raise ServiceError('read_only_mounts_unavailable', 503)
        return ctypes.CDLL(None, use_errno=True)

    def prepare(self):
        sources = [(self.context.input_root, 'inputs'), (self.context.checkpoint_root, 'base')]
        if not any(root for root, _ in sources):
            return
        libc = self._libc()
        try:
            for source, target_name in sources:
                if source is None:
                    continue
                source = Path(source)
                # Domain paths carry a canonical inputs/ or base/ prefix. Mount
                # that subtree, not a second nested inputs/inputs directory.
                children = list(source.iterdir())
                if len(children) != 1 or children[0].name != target_name:
                    raise ServiceError('input_mount_layout_invalid', 422)
                child = children[0]
                info = child.lstat()
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o222:
                    raise ServiceError('unsafe_input_staging', 503)
                target = self.workspace / target_name
                target.mkdir(mode=0o555)
                if libc.mount(os.fsencode(child), os.fsencode(target), None, MS_BIND, None) != 0:
                    raise ServiceError('read_only_mount_failed', 503)
                self.targets.append(target)
                flags = MS_BIND | MS_REMOUNT | MS_RDONLY | MS_NOSUID | MS_NODEV | MS_NOEXEC
                if libc.mount(None, os.fsencode(target), None, flags, None) != 0:
                    raise ServiceError('read_only_mount_failed', 503)
                if not os.statvfs(target).f_flag & os.ST_RDONLY or not os.path.ismount(target):
                    raise ServiceError('read_only_mount_unverified', 503)
        except BaseException:
            self.close()
            raise

    def close(self):
        if not self.targets:
            return
        libc = self._libc()
        # No MNT_DETACH: a busy mount is uncertainty, never proof of cleanup.
        for target in reversed(self.targets[:]):
            if libc.umount2(os.fsencode(target), 0) != 0:
                raise ServiceError('input_unmount_unverified', 503)
            self.targets.remove(target)
