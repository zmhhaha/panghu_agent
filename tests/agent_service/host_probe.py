"""Fixed offline Linux kernel probe, runnable only as a private container PID 1.

Not a pytest test and never a production acceptance generator. The caller must
provide a disposable read-only Docker container with --network=none and fresh
tmpfs mounts at /run/agent-control, /work and /tmp. No host process namespace,
production paths, user parameters, model URLs, credentials or network are used.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from agent_service.linux_stop import LinuxUidStopController, private_kernel_scope


CONTROL = Path('/run/agent-control')
WORK = Path('/work/host-probe')
ATTEMPT = str(uuid.UUID(int=1))
POD = str(uuid.UUID(int=2))
REQUEST = 'a' * 64
# A dedicated fixture UID avoids the old common UID10001 / host-global NPROC
# collision. This fixed probe value does not reserve any production UID range.
FIXTURE_UID = 1000001

# Fixed fixture code only. The descendant creates a new session, so killing the
# original process group alone would leave a writer alive.
WRITER = r'''
from pathlib import Path
import sys,time
target = Path(sys.argv[1])
number = 0
while True:
    number += 1
    target.write_text(str(number), encoding='ascii')
    time.sleep(0.01)
'''
AGENT = r'''
import json,os,subprocess,sys,time
from pathlib import Path
writer = WRITER_CODE
home = Path('/work/host-probe')
private_denied = False
try:
    Path('/run/agent-control/private-sentinel').read_bytes()
except PermissionError:
    private_denied = True
fields = dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
try:
    child = subprocess.Popen([sys.executable,'-c',writer,str(home/'detached-heartbeat')],
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        start_new_session=True,env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':str(home)})
except OSError as error:
    print(json.dumps({'type':'fixture_agent_error','kind':'descendant_spawn','errno':error.errno}),flush=True)
    sys.exit(125)
observation = {'private_denied':private_denied,'uid':list(os.getresuid()),'gid':list(os.getresgid()),
    'groups':os.getgroups(),'caps_empty':all(int(fields[k].strip(),16)==0 for k in ('CapEff','CapPrm','CapInh','CapAmb')),
    'nnp':fields.get('NoNewPrivs','').strip()=='1','detached_pid':child.pid}
(home/'agent-observation.json').write_text(json.dumps(observation),encoding='ascii')
number = 0
while True:
    number += 1
    (home/'main-heartbeat').write_text(str(number),encoding='ascii')
    time.sleep(0.01)
'''.replace('WRITER_CODE', repr(WRITER))

# Call the unchanged production launcher's main function. Only its outer error
# renderer is replaced by a fixed fixture diagnostic; no command, environment
# or exception text is printed. In particular EAGAIN can identify UID-global
# RLIMIT_NPROC pressure without inspecting the host's process namespace.
LAUNCHER_SHIM = r'''
import json,runpy,sys
launcher,config = sys.argv[1:]
entry = runpy.run_path(launcher,run_name='agent_host_probe_launcher')
sys.argv = [launcher,config]
try:
    entry['main']()
except OSError as error:
    print(json.dumps({'type':'fixture_launcher_error','kind':'os_error','errno':error.errno}),flush=True)
    sys.exit(126)
except BaseException:
    print(json.dumps({'type':'fixture_launcher_error','kind':'guard_error'}),flush=True)
    sys.exit(126)
'''


class ProbeFailure(RuntimeError):
    pass


def require(value):
    if not value:
        raise ProbeFailure()


def isolated_container_only():
    private_kernel_scope()  # Refuses host root, non-PID1 and unknown PID scope.
    require(len(sys.argv) == 1)
    require({p.name for p in Path('/sys/class/net').iterdir()} == {'lo'})
    mountinfo = [line.split() for line in Path('/proc/self/mountinfo').read_text().splitlines()]
    roots = [row for row in mountinfo if len(row) > 6 and row[4] == '/']
    require(len(roots) == 1 and 'ro' in roots[0][5].split(','))
    for path in ('/run/agent-control', '/work', '/tmp'):
        mounts = [row for row in mountinfo if len(row) > 6 and row[4] == path]
        require(len(mounts) == 1)
        row = mounts[0]
        separator = row.index('-')
        require(row[separator + 1] == 'tmpfs' and 'rw' in row[5].split(','))
        directory = Path(path)
        require(directory.is_dir() and not directory.is_symlink() and directory.stat().st_uid == 0)
    require(not WORK.exists())


def atomic_private(path, value):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, 'w', encoding='ascii') as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def launcher_diagnostics(process, diagnostics):
    diagnostics['launcher_exit_code'] = process.poll()
    diagnostics['observation_file_present'] = (WORK / 'agent-observation.json').exists()
    diagnostics['main_heartbeat_present'] = (WORK / 'main-heartbeat').exists()
    diagnostics['detached_heartbeat_present'] = (WORK / 'detached-heartbeat').exists()
    path = CONTROL / 'fixture-launcher.log'
    if not path.exists():
        return
    raw = path.read_bytes()[:4096]
    diagnostics['launcher_guard_error'] = b'"runtime_launcher_error"' in raw
    diagnostics['fixture_python_traceback'] = b'Traceback (most recent call last)' in raw
    for line in raw.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if type(row) is dict and row.get('type') == 'fixture_launcher_error':
            if row.get('kind') in {'os_error', 'guard_error'}:
                diagnostics['launcher_error_kind'] = row['kind']
            if type(row.get('errno')) is int and 0 < row['errno'] < 4096:
                diagnostics['launcher_errno'] = row['errno']
        elif type(row) is dict and row.get('type') == 'fixture_agent_error':
            if row.get('kind') == 'descendant_spawn':
                diagnostics['agent_error_kind'] = 'descendant_spawn'
            if type(row.get('errno')) is int and 0 < row['errno'] < 4096:
                diagnostics['agent_errno'] = row['errno']


def wait_writers(process, stopper, diagnostics):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if process.poll() is not None:
            launcher_diagnostics(process, diagnostics)
            raise ProbeFailure()
        observed = WORK / 'agent-observation.json'
        if observed.exists() and observed.stat().st_size <= 2048:
            try:
                value = json.loads(observed.read_bytes())
                detached = value.get('detached_pid')
                heartbeats = [WORK / 'main-heartbeat', WORK / 'detached-heartbeat']
                active = stopper._uid_processes()
                if (type(detached) is int and detached in active and len(active) >= 2
                        and os.getpgid(detached) == detached and detached != process.pid
                        and all(p.is_file() and p.read_bytes().strip().isdigit() for p in heartbeats)):
                    return value
            except (ValueError, OSError):
                pass  # The fixture may still be atomically starting its writers.
        time.sleep(0.02)
    launcher_diagnostics(process, diagnostics)
    raise ProbeFailure()


def reap_children():
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if not pid:
            time.sleep(0.01)


def run(checks, diagnostics):
    diagnostics['stage'] = 'prepare_private_scope'
    CONTROL.chmod(0o700)
    Path('/work').chmod(0o711)
    WORK.mkdir(mode=0o700)
    os.chown(WORK, FIXTURE_UID, FIXTURE_UID)
    stopper = LinuxUidStopController(CONTROL, ATTEMPT, POD, REQUEST, agent_uid=FIXTURE_UID)
    stopper.initialize()
    require(not stopper._uid_processes())
    checks['private_kernel_scope'] = True
    diagnostics['stage'] = 'write_fixed_launch_config'
    atomic_private(CONTROL / 'private-sentinel', 'offline fixture; no credential\n')
    launch = CONTROL / 'fixture-launch.json'
    atomic_private(launch, json.dumps({'controller_pid': os.getpid(), 'max_processes': 64,
        'cgroup': None, 'uid': FIXTURE_UID, 'gid': FIXTURE_UID,
        'denied_paths': [str(CONTROL)], 'cwd': str(WORK),
        'command': [sys.executable, '-c', AGENT]}))
    from agent_service.runtime import launcher
    process = None
    try:
        diagnostics['stage'] = 'start_uid_fixture'
        log = CONTROL / 'fixture-launcher.log'
        log_fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(log_fd, 'wb') as output:
            process = subprocess.Popen([sys.executable, '-c', LAUNCHER_SHIM,
                str(Path(launcher.__file__)), str(launch)],
                cwd=WORK, env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(WORK)},
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                start_new_session=True)
        diagnostics['stage'] = 'wait_detached_writers'
        observed = wait_writers(process, stopper, diagnostics)
        diagnostics['stage'] = 'verify_uid_fixture_observation'
        require(observed['private_denied'] is True and observed['caps_empty'] is True and observed['nnp'] is True)
        require(observed['uid'] == [FIXTURE_UID] * 3 and observed['gid'] == [FIXTURE_UID] * 3
                and observed['groups'] == [])
        checks.update(agent_uid_dropped=True, capabilities_cleared=True, no_new_privileges=True,
                      control_directory_unreadable=True, detached_session_writer_observed=True)
        diagnostics['stage'] = 'reopen_active_scope'
        reopened = LinuxUidStopController(CONTROL, ATTEMPT, POD, REQUEST, agent_uid=FIXTURE_UID)
        reopened.initialize()
        require(not reopened.consumed and not reopened.prove_stopped(ATTEMPT).all_writers_stopped)
        checks['binding_reopened_before_stop'] = True
        diagnostics['stage'] = 'stop_all_uid_writers'
        reopened.stop(ATTEMPT)
        proof = reopened.prove_stopped(ATTEMPT)
        require(proof.all_writers_stopped is True and 'pod:' + POD in proof.resource_ids
                and 'attempt:' + ATTEMPT in proof.resource_ids
                and 'uid:' + str(FIXTURE_UID) in proof.resource_ids and not reopened._uid_processes())
        checks['real_pidfd_all_uid_writers_stopped'] = True
        diagnostics['stage'] = 'verify_stopped_heartbeats'
        process.wait(timeout=2)
        reap_children()
        paths = [WORK / 'main-heartbeat', WORK / 'detached-heartbeat']
        stopped_bytes = [p.read_bytes() for p in paths]
        time.sleep(0.15)
        require([p.read_bytes() for p in paths] == stopped_bytes and not reopened._uid_processes())
        checks['heartbeats_stable_after_stop'] = True
        diagnostics['stage'] = 'reopen_stopped_scope'
        after = LinuxUidStopController(CONTROL, ATTEMPT, POD, REQUEST, agent_uid=FIXTURE_UID)
        after.initialize()
        require(after.consumed and after.prove_stopped(ATTEMPT).all_writers_stopped)
        checks['stopped_binding_reopened'] = True
        diagnostics['stage'] = 'complete'
    finally:
        # Fixed private container scope only; even failure cannot target host UIDs.
        try:
            stopper.stop(ATTEMPT)
        except Exception:
            pass
        if process is not None and process.poll() is not None:
            process.wait()
        reap_children()


def main():
    report = {'schema_version': 1, 'fixture_mode': True, 'model_requests': 0,
              'no_model_calls': True, 'production_acceptance': False, 'passed': False, 'checks': {},
              'diagnostics': {'stage': 'verify_disposable_container', 'fixture_uid': FIXTURE_UID,
                              'fixture_nproc_limit': 64}}
    try:
        isolated_container_only()
    except Exception:
        report['error_code'] = 'host_probe_container_scope_required'
        print(json.dumps(report, sort_keys=True))
        return 2
    try:
        run(report['checks'], report['diagnostics'])
        require(len(report['checks']) == 10 and all(value is True for value in report['checks'].values()))
        report['passed'] = True
        report['error_code'] = None
    except Exception:
        report['error_code'] = 'host_probe_failed'
    print(json.dumps(report, sort_keys=True))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
