"""TEST ONLY: fixed offline Pi protocol and process containment probe."""
import json, os, resource, subprocess, sys, time
from pathlib import Path
if '--version' in sys.argv:
    print('0.73.1'); raise SystemExit
mode = Path(sys.argv[-1][1:]).read_text().strip()
assert mode in {'normal', 'cancel', 'deadline', 'fence'}
status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
checks = {'uid': os.getresuid(), 'gid': os.getresgid(), 'groups': os.getgroups(), 'nproc': resource.getrlimit(resource.RLIMIT_NPROC), 'status': {k: status[k].strip() for k in ('CapEff','CapPrm','CapInh','CapAmb','NoNewPrivs','Seccomp')}}
assert len(set(checks['uid'])) == 1 and checks['uid'][0] >= 20000
assert checks['uid'] == checks['gid'] and checks['groups'] == []
assert all(int(checks['status'][k],16) == 0 for k in ('CapEff','CapPrm','CapInh','CapAmb'))
assert checks['status']['NoNewPrivs'] == '1' and checks['status']['Seccomp'] == '2'
assert checks['nproc'] == (32,32)
for target in ('/work/control/sentinel','/proc/1/environ','/work/other-attempt/sentinel'):
    try: Path(target).read_bytes()
    except PermissionError: pass
    else: raise AssertionError('denied path readable: '+target)
checks['denied_paths'] = True
try:
    Path('/opt/pi-candidate/source/pi_worker/__init__.py').open('a')
except (PermissionError, OSError): checks['source_readonly'] = True
else: raise AssertionError('source writable')
Path('checks.json').write_text(json.dumps(checks))
child = subprocess.Popen([sys.executable, '-I', '/opt/pi-candidate/harness/writer.py'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
Path('child.pid').write_text(str(child.pid))
until = time.monotonic() + 3
while not Path('heartbeat').exists() and time.monotonic() < until:
    time.sleep(0.02)
assert Path('heartbeat').exists(), 'detached writer did not start'
print(json.dumps({'type':'agent_start'}), flush=True)
if mode in {'cancel','deadline'}:
    time.sleep(90)
message = {'role':'assistant','provider':'test-only','model':'synthetic','stopReason':'stop','content':[]}
print(json.dumps({'type':'message_end','message':message}), flush=True)
print(json.dumps({'type':'agent_end','messages':[message]}), flush=True)
