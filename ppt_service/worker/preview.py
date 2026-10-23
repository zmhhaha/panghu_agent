"""Worker-owned lifecycle for the pinned official SVG preview (no model inputs)."""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import time

SERVER = "/opt/ppt-master/skills/ppt-master/scripts/svg_editor/server.py"
VISUAL_REVIEW = "/opt/ppt-master/skills/ppt-master/scripts/visual_review.py"
PYTHON = "/opt/hermes/.venv/bin/python"
PORT = 8765
ORIGIN = f"http://127.0.0.1:{PORT}"
MAX_ENTRIES = 20000
MAX_DEPTH = 32


def _ordinary(path: Path, *, directory: bool) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("Preview project contains a link")
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError("Preview project contains a special file")
    if not directory and info.st_nlink != 1:
        raise ValueError("Preview project contains a hardlink")


def project_root(parent: Path) -> Path:
    """Select one direct-child root while safety-scanning the whole restored tree."""
    parent = parent.absolute()
    for component in (*reversed(parent.parents), parent):
        _ordinary(component, directory=True)
    pending = [(parent, 0)]
    roots = []
    parent_has_markers = False
    count = 0
    while pending:
        directory, depth = pending.pop()
        with os.scandir(directory) as entries:
            names = set()
            for entry in entries:
                count += 1
                if count > MAX_ENTRIES:
                    raise ValueError("Preview project scan exceeds entry limit")
                path = directory / entry.name
                is_directory = stat.S_ISDIR(path.lstat().st_mode)
                _ordinary(path, directory=is_directory)
                names.add(entry.name)
                if is_directory:
                    if depth >= MAX_DEPTH:
                        raise ValueError("Preview project scan exceeds depth limit")
                    pending.append((path, depth + 1))
        if depth == 0:
            parent_has_markers = {"design_spec.md", "spec_lock.md"} <= names
        # Revision archives may repeat markers; only direct children are roots.
        if depth == 1 and {"design_spec.md", "spec_lock.md"} <= names:
            _ordinary(directory / "design_spec.md", directory=False)
            _ordinary(directory / "spec_lock.md", directory=False)
            roots.append(directory)
    if parent_has_markers or len(roots) != 1:
        raise ValueError("Expected one restored split project below /work/project")
    return roots[0]


def renderer_environment() -> dict[str, str]:
    # No inherited HOME, PYTHONPATH, proxies, model/API keys or platform flags.
    return {"PATH": "/opt/hermes/.venv/bin:/usr/bin:/bin", "HOME": "/tmp",
            "TMPDIR": "/tmp", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
            "PLAYWRIGHT_BROWSERS_PATH": "/opt/ms-playwright"}


class OfficialPreview:
    def __init__(self, parent: Path, *, lifetime: int):
        self.project = project_root(parent)
        self.lifetime = lifetime
        self.process = None
        self.ready = False
        self.stopped = True
        # Restored daemon locks can contain PIDs reused in this namespace. Never
        # kill/reuse that PID or mutate the restored checkpoint to bypass it.
        for lock in (self.project / "live_preview" / "lock.json",
                     self.project / ".live_preview.lock"):
            if lock.exists():
                raise ValueError("Restored preview lock requires controller cleanup")

    def _request(self, path: str, *, method: str = "GET") -> dict:
        connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=0.5)
        try:
            body = b'{"reason":"worker_shutdown"}' if method == "POST" else None
            headers = {"Content-Type": "application/json"} if method == "POST" else {}
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError("Preview HTTP status rejected")
            raw = response.read(65537)
        finally:
            connection.close()
        if len(raw) > 65536:
            raise ValueError("Preview response exceeds limit")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("Invalid preview response")
        return value

    def start(self, interrupted=lambda: False, *, ready_timeout: float = 30) -> None:
        if os.name != "posix":
            raise RuntimeError("Official preview supervision requires Linux process groups")
        uids = getattr(os, "getresuid", lambda: (0, 0, 0))()
        if len(set(uids)) != 1 or uids[0] <= 0:
            raise RuntimeError("Official preview must run under the non-root attempt identity")
        # Never attach to a listener we did not create; health also checks our PID.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", PORT))
        self.process = subprocess.Popen(
            [PYTHON, SERVER, str(self.project), "--live", "--no-browser",
             "--port", str(PORT), "--timeout", str(self.lifetime)],
            cwd=str(self.project), env=renderer_environment(), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.stopped = False
        deadline = time.monotonic() + ready_timeout
        while time.monotonic() < deadline:
            if interrupted():
                raise RuntimeError("Execution interrupted during preview startup")
            if self.process.poll() is not None:
                raise RuntimeError("Official preview exited before readiness")
            try:
                health = self._request("/api/health")
            except (OSError, ValueError, http.client.HTTPException):
                time.sleep(0.1)
                continue
            if (health.get("service") != "live_preview" or health.get("status") != "ok"
                    or health.get("project") != str(self.project)
                    or type(health.get("pid")) is not int or health["pid"] != self.process.pid
                    or health.get("live") is not True
                    or health.get("svg_output") != str(self.project / "svg_output")):
                raise RuntimeError("Official preview health identity mismatch")
            self.ready = True
            return
        raise RuntimeError("Official preview readiness timed out")

    def instructions(self) -> str:
        if not self.ready:
            raise RuntimeError("Preview has not passed readiness")
        return ("\n可信Worker预览上下文：执行器已启动并健康核对当前项目的临时官方live-preview。"
                "恢复材料中禁止Agent启动daemon的历史说明不阻止使用这个已就绪服务。"
                "不要自行启动、重启或关闭服务；Worker负责finally关闭。"
                f"\n项目绝对路径：{json.dumps(str(self.project), ensure_ascii=False)}"
                f"\n唯一已就绪官方origin：{ORIGIN}"
                f"\n使用官方 {VISUAL_REVIEW}；先读取其实际CLI帮助，"
                f"审查时显式传 --server-url {ORIGIN} 并绑定上述项目。"
                "省略--pages审查全部页；核对预期页数与实际rendered成功数，不能仅凭exit0声称完成。"
                "不得替换URL、端口或声称尚未执行的视觉审查已通过。\n")

    def stop(self, *, grace: float = 3) -> None:
        if self.process is None or self.stopped:
            return
        process = self.process
        if self.ready and process.poll() is None:
            try:
                self._request("/api/shutdown", method="POST")
            except (OSError, ValueError, http.client.HTTPException):
                pass
            try:
                process.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                pass
        # Shutdown os._exit ends the server, not necessarily descendant renderers.
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                process.wait(timeout=1)
                self.stopped = True
                self.ready = False
                return
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline:
                process.poll()
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    process.wait(timeout=1)
                    self.stopped = True
                    self.ready = False
                    return
                time.sleep(0.05)
        raise RuntimeError("Official preview process group did not stop")
