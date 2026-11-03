#!/usr/bin/env python3
"""Real kernel failure probes on disposable server containers, never production.

This measures Docker isolation, OOM, SIGKILL, timeout, ENOSPC and network-none.
It does not claim a Kubernetes/Agent workflow, a production recovery drill or
human review. Domain fencing/version retention is checked in separate suites.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

LABEL = 'ppt.disposable-fault.owner'
PROGRAMS = {
    'sigkill': 'import os,signal; os.kill(os.getpid(),signal.SIGKILL)',
    'oom': 'import time; blocks=[]\nwhile True:\n blocks.append(bytearray(4*1024*1024)); time.sleep(.01)',
    'timeout': 'import os,time\nif os.fork()==0:\n os.setsid(); time.sleep(600)\ntime.sleep(600)',
    'disk-full': '''import errno,json,os
try:
 with open('/work/fill','wb',buffering=0) as f:
  for _ in range(128): f.write(b'x'*65536)
 raise RuntimeError('bounded_tmpfs_was_not_full')
except OSError as e:
 assert e.errno==errno.ENOSPC
assert open('/retained/source','rb').read()==b'Synthetic retained input; not a PPTX or owner version.\\n'
print(json.dumps({'enospc':True,'retained_readable':True}))
''',
    'network-and-privileges': '''import ctypes,errno,json,os,socket
with socket.socket() as s:
 s.settimeout(.5)
 try:s.connect(('192.0.2.1',443));raise RuntimeError('network_unexpectedly_available')
 except OSError as e:assert e.errno in (errno.ENETUNREACH,errno.EHOSTUNREACH)
status=open('/proc/self/status').read()
assert 'CapEff:\\t0000000000000000' in status and 'NoNewPrivs:\\t1' in status
try:open('/forbidden-write','wb');raise RuntimeError('rootfs_writable')
except OSError as e:assert e.errno in (errno.EROFS,errno.EACCES)
lib=ctypes.CDLL(None,use_errno=True)
assert lib.mount(b'none',b'/work',b'tmpfs',0,None)==-1 and ctypes.get_errno()==errno.EPERM
print(json.dumps({'network_none':True,'caps_empty':True,'no_new_privileges':True,
 'rootfs_write_denied':True,'arbitrary_mount_denied':True}))
''',
}


def call(args, *, timeout=45, check=True):
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError('probe_command_failed:' + args[0])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'linux' or os.geteuid() != 0:
        parser.error('Linux_server_root_required_for_owned_container_cleanup')
    if not re.fullmatch(r'(?:[a-zA-Z0-9][a-zA-Z0-9._:/-]*@)?sha256:[a-f0-9]{64}', args.image):
        parser.error('cached_immutable_image_required')
    root = args.output.absolute()
    if any(p.is_symlink() for p in (root, *root.parents)) or root.exists():
        parser.error('new_unlinked_output_required')
    image = json.loads(call(['docker', 'image', 'inspect', args.image]).stdout)[0]
    if image.get('Os') != 'linux':
        parser.error('Linux_image_required')
    root.mkdir(mode=0o755, parents=False)
    retained = root / 'retained'
    retained.mkdir(mode=0o755)
    original = retained / 'source'
    original.write_bytes(b'Synthetic retained input; not a PPTX or owner version.\n')
    original.chmod(0o444)
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    owner = 'ppt-fault-' + uuid.uuid4().hex
    owned, rows, error = [], {}, None

    def stopped(name):
        state = json.loads(call(['docker', 'inspect', '--format', '{{json .State}}', name]).stdout)
        return state

    try:
        for case, program in PROGRAMS.items():
            name = owner + '-' + case
            owned.append(name)
            # Namespace PID 1 has special signal/OOM semantics. Give the
            # Python probe a normal child PID and reap it with Docker's init.
            call(['docker', 'run', '-d', '--init', '--pull=never', '--name', name,
                '--label', LABEL + '=' + owner, '--network', 'none', '--read-only',
                '--user', '10000:10000', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                '--memory', '64m', '--memory-swap', '64m', '--pids-limit', '16', '--cpus', '1',
                '--tmpfs', '/tmp:rw,nosuid,nodev,size=8m,mode=1777',
                '--tmpfs', '/work:rw,nosuid,nodev,size=128k,uid=10000,gid=10000,mode=700',
                '--mount', 'type=bind,src=' + str(retained) + ',dst=/retained,readonly',
                '--env', 'PYTHONDONTWRITEBYTECODE=1', '--entrypoint', 'python', args.image, '-c', program])
            deadline = time.monotonic() + (2 if case == 'timeout' else 25)
            state = stopped(name)
            while state['Running'] and time.monotonic() < deadline:
                time.sleep(.2)
                state = stopped(name)
            deadline_fired = state['Running']
            if deadline_fired:
                call(['docker', 'kill', '--signal', 'KILL', name])
                for _ in range(20):
                    state = stopped(name)
                    if not state['Running']:
                        break
                    time.sleep(.1)
            # Wait for Docker's terminal-state publication as well as the
            # kernel process exit; an early inspect can still show exit 0 and
            # OOMKilled=false while the daemon is finalizing a killed process.
            waited = call(['docker', 'wait', name], timeout=15)
            for _ in range(30):
                state = stopped(name)
                if state.get('Status') == 'exited' and state['ExitCode'] == int(waited.stdout.strip()):
                    break
                time.sleep(.1)
            logs = call(['docker', 'logs', name], check=False).stdout
            (root / (case + '.log')).write_text(logs, encoding='utf-8')
            terminal = not state['Running'] and state['Pid'] == 0
            if case == 'oom':
                passed = terminal and state['OOMKilled'] and state['ExitCode'] == 137 and not deadline_fired
            elif case == 'sigkill':
                passed = terminal and not state['OOMKilled'] and state['ExitCode'] == 137 and not deadline_fired
            elif case == 'timeout':
                passed = terminal and deadline_fired and not state['OOMKilled'] and state['ExitCode'] == 137
            else:
                passed = terminal and not deadline_fired and state['ExitCode'] == 0 and bool(logs.strip())
            rows[case] = {'passed': passed, 'exit_code': state['ExitCode'],
                'oom_killed': state['OOMKilled'], 'deadline_kill': deadline_fired,
                'container_stopped': terminal, 'log_sha256': hashlib.sha256(logs.encode()).hexdigest()}
            if hashlib.sha256(original.read_bytes()).hexdigest() != digest:
                raise RuntimeError('retained_synthetic_input_changed')
    except Exception as exc:
        error = type(exc).__name__ + ':' + str(exc)
    finally:
        cleaned = True
        for name in reversed(owned):
            result = call(['docker', 'inspect', '--format', '{{index .Config.Labels "' + LABEL + '"}}', name], check=False)
            if result.returncode == 0:
                if result.stdout.strip() != owner:
                    cleaned = False
                elif call(['docker', 'rm', '-f', name], check=False).returncode:
                    cleaned = False
        remaining = call(['docker', 'ps', '-aq', '--filter', 'label=' + LABEL + '=' + owner], check=False)
        cleaned = cleaned and remaining.returncode == 0 and not remaining.stdout.strip()
    result = {'schema': 'ppt-service.disposable-kernel-faults.v1', 'owner': owner,
        'image': args.image, 'image_id': image['Id'], 'cases': rows, 'error': error,
        'owned_resources_removed': cleaned, 'retained_synthetic_input_sha256': digest,
        'retained_synthetic_input_unchanged': hashlib.sha256(original.read_bytes()).hexdigest() == digest,
        'production_data_accessed': False, 'kubernetes_enforcement_verified': False,
        'agent_recovery_workflow_verified': False, 'human_acceptance': False,
        'passed': error is None and cleaned and len(rows) == len(PROGRAMS) and all(r['passed'] for r in rows.values())}
    (root / 'summary.json').write_text(json.dumps(result, sort_keys=True, indent=2) + '\n')
    print(json.dumps(result, sort_keys=True))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
