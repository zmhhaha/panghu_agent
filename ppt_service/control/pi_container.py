"""Reviewed offline container plans. No production model admission or shell strings."""
from __future__ import annotations
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from .execution import Attempt, ControlError, Layout, RuntimeConfig

PPT_BASE_IMAGE = 'sha256:6e9bdb213f75120171d712e9bd037759cedfce6aced2caff0dae01d5fa08583a'
PINNED_IMAGE = 'sha256:2a09398d26a387ac79b3b7b635202062ee7856f151b47eba0cb773b7e020e1a1'
REVIEWED_IMAGES = frozenset((PINNED_IMAGE, PPT_BASE_IMAGE))
ROOT_CAPABILITIES = ('CHOWN', 'DAC_OVERRIDE', 'KILL', 'SETGID', 'SETUID')
DOMAIN_MOUNTS = (('inputs', 'ro'), ('task', 'ro'), ('base', 'ro'),
                 ('project', 'rw'), ('result', 'rw'), ('audit', 'rw'))
MAX_ARGV = 80


@dataclass(frozen=True)
class AttemptContainer:
    name: str
    image: str
    argv: tuple[str, ...]

    def redacted(self):
        redacted=list(self.argv)
        for i,value in enumerate(self.argv[:-1]):
            if value in ('--volume','--tmpfs'):
                redacted[i+1]='<mount>'
        return tuple(redacted)


def _ordinary_directory(path):
    """Validate before resolving: resolution must not hide linked ancestors."""
    if not isinstance(path,Path) or not path.is_absolute() or '..' in path.parts:
        raise ControlError('unsafe_attempt_path')
    for part in (*reversed(path.parents),path):
        info=part.lstat()
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info,'st_file_attributes',0)&0x400):
            raise ControlError('unsafe_attempt_path')
    return path.resolve()


def plan_attempt_container(config, attempt, layout, *, pi_uid, image=PINNED_IMAGE,
                           memory='1g', cpus='2', pids=128, name_prefix='ppt-pi'):
    """Trusted mount intent only. Pi UID dropping occurs in the fixed supervisor.

    This function does not assert a UID was reserved or a model is admitted;
    callers must use the durable allocator and verify container/process stops.
    """
    if sys.platform!='linux':raise ControlError('container_plan_requires_linux')
    if type(config) is not RuntimeConfig or type(attempt) is not Attempt or type(layout) is not Layout:
        raise ControlError('invalid_attempt_request')
    if type(image) is not str or not re.fullmatch(r'sha256:[0-9a-f]{64}',image):
        raise ControlError('unpinned_runtime_image')
    if image not in REVIEWED_IMAGES:raise ControlError('unreviewed_runtime_image')
    if type(pi_uid) is not int or not 10000<=pi_uid<2**31-1 or pi_uid==65534:
        raise ControlError('invalid_writer_ids')
    if type(pids) is not int or not 16<=pids<=4096:
        raise ControlError('invalid_container_limits')
    if cpus not in ('1','2','4') or memory not in ('512m','1g','2g','4g'):
        raise ControlError('invalid_container_limits')
    if type(name_prefix) is not str or not re.fullmatch(r'[a-z][a-z0-9-]{0,31}',name_prefix):
        raise ControlError('invalid_container_name')
    data_root=_ordinary_directory(Path(config.data_root))
    expected=data_root/'projects'/attempt.project_id/'attempts'/attempt.job_id/f'{attempt.attempt}-{attempt.token}'
    # Compare lexical canonical identity before resolving any child path.
    if Path(os.path.abspath(layout.root))!=expected:
        raise ControlError('attempt_layout_mismatch')
    root=_ordinary_directory(Path(layout.root))
    mounts=[]
    for name,mode in DOMAIN_MOUNTS:
        given=Path(getattr(layout,name))
        if given!=Path(layout.root)/name:raise ControlError('attempt_layout_mismatch')
        path=_ordinary_directory(given)
        if path!=root/name:raise ControlError('unsafe_attempt_path')
        if os.name=='posix' and any(c in str(path) for c in ':\n\r'):
            raise ControlError('unsafe_attempt_path')
        mounts.append((str(path),f'/work/{name}',mode))
    name=f'{name_prefix}-{attempt.uid}'
    argv=['run','--rm','--name',name,'--user','0:0','--cap-drop','ALL']
    for capability in ROOT_CAPABILITIES:argv+=['--cap-add',capability]
    argv+=['--security-opt','no-new-privileges','--read-only','--network','none','--ipc','none',
        '--memory',memory,'--cpus',cpus,'--pids-limit',str(pids),
        '--tmpfs','/tmp:rw,nosuid,nodev,mode=1777,size=64m',
        '--tmpfs','/work/runtime:rw,nosuid,nodev,mode=1777,size=256m','--workdir','/work/project']
    for host,target,mode in mounts:argv+=['--volume',f'{host}:{target}:{mode}']
    argv+=[image]
    if len(argv)>MAX_ARGV:raise ControlError('container_plan_too_large')
    return AttemptContainer(name,image,tuple(argv))


def plan_domain_smoke(config,attempt,layout,*,pi_uid,code_root):
    """Fixed test-only entry with RO reviewed code; never takes a user command."""
    base=plan_attempt_container(config,attempt,layout,pi_uid=pi_uid)
    source=_ordinary_directory(Path(code_root))
    if not (source/'ppt_service/images/pi_candidate/domain_smoke.py').is_file():
        raise ControlError('offline_entry_missing')
    argv=base.argv[:-1]+('--volume',f'{source}:/code:ro',
        '--entrypoint','/opt/hermes/.venv/bin/python',base.image,
        '/code/ppt_service/images/pi_candidate/domain_smoke.py')
    return AttemptContainer(base.name,base.image,argv)


def require_fixture_admission(runtime_settings):
    if getattr(runtime_settings,'fixture_mode',None) is not True:
        raise ControlError('production_admission_not_configured')
    return runtime_settings
