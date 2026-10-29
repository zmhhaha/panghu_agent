"""Root-only Linux launcher, executable by file path without application imports."""
import ctypes
import json
import os
from pathlib import Path
import sys


def apply_process_limit(resource, limit):
    if type(limit) is not int or not 2 <= limit <= 4096:
        raise RuntimeError("invalid process limit")
    inherited_hard = resource.getrlimit(resource.RLIMIT_NPROC)[1]
    if inherited_hard != resource.RLIM_INFINITY:
        limit = min(limit, inherited_hard)
    resource.setrlimit(resource.RLIMIT_NPROC, (limit, limit))


def main():
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, 9, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG, SIGKILL
        raise RuntimeError("parent death signal failed")
    config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if os.getppid() != config["controller_pid"]:
        raise RuntimeError("controller already exited")
    if config["cgroup"]:
        (Path(config["cgroup"]) / "cgroup.procs").write_text(str(os.getpid()), encoding="ascii")
    import resource
    apply_process_limit(resource, config["max_processes"])
    uid, gid = config["uid"], config["gid"]
    os.setgroups([])
    os.setresgid(gid, gid, gid)
    os.setresuid(uid, uid, uid)
    # Linux clears PDEATHSIG on credential changes. Rearm and recheck the
    # parent so death during that interval cannot release an orphan task.
    if libc.prctl(1, 9, 0, 0, 0) != 0 or os.getppid() != config["controller_pid"]:
        raise RuntimeError("controller exited during privilege drop")
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise RuntimeError("no_new_privs failed")
    # Drop inheritable capabilities too; setresuid already clears permitted and
    # effective capabilities. Verify rather than trusting container defaults.
    class Header(ctypes.Structure):
        _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]
    class Data(ctypes.Structure):
        _fields_ = [("effective", ctypes.c_uint32), ("permitted", ctypes.c_uint32), ("inheritable", ctypes.c_uint32)]
    header, data = Header(0x20080522, 0), (Data * 2)()
    if libc.capset(ctypes.byref(header), ctypes.byref(data)) != 0:
        raise RuntimeError("capset failed")
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    if any(int(status[k].strip(), 16) for k in ("CapEff", "CapPrm", "CapInh", "CapAmb")):
        raise RuntimeError("capabilities remain")
    if os.getresuid() != (uid, uid, uid) or os.getresgid() != (gid, gid, gid) or os.getgroups():
        raise RuntimeError("IDs not fully dropped")
    for denied in config["denied_paths"]:
        try:
            fd = os.open(denied, os.O_RDONLY)
        except PermissionError:
            continue
        except FileNotFoundError:
            raise RuntimeError("isolation sentinel missing")
        else:
            os.close(fd)
            raise RuntimeError("isolation sentinel readable")
    os.chdir(config["cwd"])
    os.umask(config.get('umask', 0o077))
    os.execvpe(config["command"][0], config["command"], os.environ)


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        print('{"type":"runtime_launcher_error"}', flush=True)
        sys.exit(126)
