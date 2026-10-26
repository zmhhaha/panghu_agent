"""Host-owned official PPT preview under a separate reserved writer UID."""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import time

from .contracts import ServiceError
from .runtime.adapter import PiRuntime


PYTHON = '/opt/hermes/.venv/bin/python'
SERVER = '/opt/ppt-master/skills/ppt-master/scripts/svg_editor/server.py'
REVIEW = '/opt/ppt-master/skills/ppt-master/scripts/visual_review.py'
PORT = 8765
ORIGIN = 'http://127.0.0.1:8765'


class TrustedPreview:
    def __init__(self, project, workspace, control_root, agent_uid, preview_uid, deadline_at):
        self.project, self.workspace = Path(project), Path(workspace)
        self.control_root, self.agent_uid, self.preview_uid = Path(control_root), agent_uid, preview_uid
        self.deadline_at, self.process = deadline_at, None

    def start(self):
        if (type(self.preview_uid) is not int or self.preview_uid == self.agent_uid
                or os.geteuid() != 0 or not self.project.is_relative_to(self.workspace / 'project')):
            raise ServiceError('preview_scope_invalid', 503)
        for path in (self.project / 'live_preview' / 'lock.json', self.project / '.live_preview.lock'):
            if path.exists() or path.is_symlink():
                raise ServiceError('preview_checkpoint_lock_present', 422)
        # Preview and Agent share only the project group. Private HOME, inputs,
        # control credentials and observation files are not made group-readable.
        os.chown(self.workspace, self.agent_uid, self.agent_uid)
        self.workspace.chmod(0o710)
        for directory, dirs, files in os.walk(self.workspace / 'project', followlinks=False):
            os.chown(directory, self.agent_uid, self.agent_uid)
            os.chmod(directory, 0o2770)
            for name in files:
                target = Path(directory) / name
                if target.is_symlink() or target.stat().st_nlink != 1:
                    raise ServiceError('preview_project_invalid', 422)
                os.chown(target, self.agent_uid, self.agent_uid)
                target.chmod(0o660)
        home, tmp = self.workspace / '.preview-home', self.workspace / '.preview-tmp'
        for path in (home, tmp):
            path.mkdir(mode=0o700)
            os.chown(path, self.preview_uid, self.agent_uid)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(('127.0.0.1', PORT))
        seconds = max(1, int(self.deadline_at - time.time()))
        command = [PYTHON, SERVER, str(self.project), '--live', '--no-browser',
                   '--port', str(PORT), '--timeout', str(seconds)]
        launch = self.control_root / 'preview-launch.json'
        PiRuntime._atomic(launch, json.dumps(dict(controller_pid=os.getpid(), max_processes=64,
            cgroup=None, uid=self.preview_uid, gid=self.agent_uid,
            denied_paths=[str(self.control_root), '/run/agent-secrets/worker.json'],
            cwd=str(self.project), command=command, umask=0o007)))
        env = {'PATH': '/opt/hermes/.venv/bin:/usr/local/bin:/usr/bin:/bin',
            'HOME': str(home), 'TMPDIR': str(tmp), 'LANG': 'C.UTF-8',
            'PLAYWRIGHT_BROWSERS_PATH': '/opt/ms-playwright',
            'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1'}
        self.process = subprocess.Popen([sys.executable,
            str(Path(__file__).with_name('runtime') / 'launcher.py'), str(launch)],
            cwd=self.project, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
        end = min(time.time() + 30, self.deadline_at)
        while time.time() < end:
            if self.process.poll() is not None:
                break
            connection = http.client.HTTPConnection('127.0.0.1', PORT, timeout=.5)
            try:
                connection.request('GET', '/api/health')
                response = connection.getresponse()
                raw = response.read(65537)
                if response.status != 200 or len(raw) > 65536:
                    raise ValueError()
                value = json.loads(raw)
                if (value.get('service') == 'live_preview' and value.get('status') == 'ok'
                        and value.get('pid') == self.process.pid and value.get('project') == str(self.project)
                        and value.get('svg_output') == str(self.project / 'svg_output') and value.get('live') is True):
                    return
            except (ValueError, OSError, http.client.HTTPException):
                pass
            finally:
                connection.close()
            time.sleep(.1)
        raise ServiceError('preview_startup_unverified', 503)

    def instructions(self):
        return ('\nTrusted host tools: use /opt/hermes/.venv/bin/python for the fixed PPT package at '
            '/opt/ppt-master/skills/ppt-master/. The host has verified the official live preview for '
            f'{self.project} at {ORIGIN}. Do not start/stop any server yourself. '
            f'First read actual help with {PYTHON} {REVIEW} --help; then render all pages with '
            f'{PYTHON} {REVIEW} "{self.project}" --server-url {ORIGIN}. '
            'Check actual rendered counts and inspect every rendered image using the image-capable read tool. '
            'A render command exit code alone is not a visual review. Record real review output.\n')

    def cleanup_after_stop(self):
        """Drop only the two known ephemeral locks after trusted UID fencing."""
        for target in (self.project / '.live_preview.lock', self.project / 'live_preview' / 'lock.json'):
            if not target.exists() and not target.is_symlink():
                continue
            parents = [self.project]
            for part in target.parent.relative_to(self.project).parts:
                parents.append(parents[-1] / part)
            for path in parents:
                info = path.lstat()
                if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                    raise ServiceError('preview_cleanup_unverified', 503)
            if target.exists() or target.is_symlink():
                info = target.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ServiceError('preview_cleanup_unverified', 503)
                target.unlink()
