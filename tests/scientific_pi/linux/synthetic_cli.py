"""Synthetic executable for Linux OS acceptance; never a model/Pi acceptance probe."""
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import time

FIXTURE_VERSION = "0.0.0-linux-os-fixture-v1"


def emit(event):
    print(json.dumps(event), flush=True)


def main():
    if "--version" in sys.argv:
        print(FIXTURE_VERSION)
        return
    if "--detached-child" in sys.argv:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        Path("detached-ready.json").write_text(json.dumps({"pid": os.getpid(), "sid": os.getsid(0)}))
        while True:
            time.sleep(1)
    config = json.loads(Path(sys.argv[sys.argv.index("--case-config") + 1]).read_text())
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    denials = []
    for target in config["denied_paths"]:
        outcomes = {}
        for operation, flags in (("read", os.O_RDONLY), ("write", os.O_WRONLY)):
            try:
                fd = os.open(target, flags | os.O_NONBLOCK)
            except OSError as exc:
                outcomes[operation] = exc.errno
            else:
                os.close(fd)
                outcomes[operation] = "OPENED"
        denials.append({"path": target, **outcomes})
    directory_denials = []
    for directory in config["denied_directories"]:
        outcomes = {"path": directory}
        for operation in ("list", "create"):
            try:
                if operation == "list":
                    os.listdir(directory)
                else:
                    fd = os.open(str(Path(directory) / "synthetic-must-not-create"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    os.close(fd)
            except OSError as exc:
                outcomes[operation] = exc.errno
            else:
                outcomes[operation] = "ALLOWED"
        directory_denials.append(outcomes)
    try:
        os.kill(config["root_controller_pid"], 0)
    except OSError as exc:
        controller_signal_errno = exc.errno
    else:
        controller_signal_errno = "ALLOWED"
    children = []
    fork_error = None
    try:
        # Retain concurrent children to prove kernel enforcement, not just a setting.
        for _ in range(config["max_processes"] + 4):
            try:
                pid = os.fork()
            except OSError as exc:
                fork_error = exc.errno
                break
            if pid == 0:
                time.sleep(60)
                os._exit(0)
            children.append(pid)
    finally:
        for pid in children:
            os.kill(pid, signal.SIGKILL)
        for pid in children:
            os.waitpid(pid, 0)
    observation = {
        "fixture_version": FIXTURE_VERSION, "fixture_scope": "linux-os-mechanisms-only",
        "pid": os.getpid(), "ppid": os.getppid(), "uid": os.getresuid(), "gid": os.getresgid(),
        "groups": os.getgroups(), "status": {key: status[key].strip() for key in
            ("CapEff", "CapPrm", "CapInh", "CapAmb", "NoNewPrivs")},
        "nproc": resource.getrlimit(resource.RLIMIT_NPROC), "forked": len(children), "fork_errno": fork_error,
        "env": dict(os.environ), "denials": denials, "directory_denials": directory_denials,
        "controller_signal_errno": controller_signal_errno,
    }
    if config["mode"] in ("normal", "cancel", "deadline", "crash"):
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--detached-child"],
                                 start_new_session=True, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        deadline = time.monotonic() + 3
        while not Path("detached-ready.json").exists():
            if child.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("detached child failed to become ready")
            time.sleep(0.01)
        observation["detached_pid"] = child.pid
    if config["mode"] == "fifo":
        os.mkfifo("report.md", 0o600)
    Path("observed.tmp").write_text(json.dumps(observation))
    Path("observed.tmp").replace("observed.json")
    emit({"type": "linux_os_fixture", "fixture_version": FIXTURE_VERSION,
          "scope": "linux-os-mechanisms-only"})
    emit({"type": "agent_start"})
    if config["mode"] in ("cancel", "deadline", "crash"):
        while True:
            time.sleep(1)
    provider = sys.argv[sys.argv.index("--provider") + 1]
    model = sys.argv[sys.argv.index("--model") + 1]
    emit({"type": "message_end", "message": {"role": "assistant", "provider": provider,
          "model": model, "stopReason": "stop", "content": []}})
    emit({"type": "agent_end", "fixture_version": FIXTURE_VERSION})


if __name__ == "__main__":
    main()
