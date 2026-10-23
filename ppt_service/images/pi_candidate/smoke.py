"""Fixed offline TEST-ONLY smoke. Synthetic flags are not production evidence."""
import hashlib, importlib, json, os, subprocess, sys, threading, time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
assert len(sys.argv) == 1
ROOT = Path('/opt/pi-candidate')
PY = '/opt/hermes/.venv/bin/python'
def report(kind, **data): print(json.dumps({'check':kind, **data}, sort_keys=True), flush=True)
def run(command):
    p = subprocess.run(command, capture_output=True, text=True, timeout=30, env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':'/tmp/candidate-home','PI_OFFLINE':'1','PLAYWRIGHT_BROWSERS_PATH':'/opt/ms-playwright'})
    report('command', argv=command, rc=p.returncode, stdout=p.stdout[-12000:], stderr=p.stderr[-2000:])
    assert p.returncode == 0
    return p.stdout.strip() or p.stderr.strip()
report('test_scope', production=False, model_calls=False, synthetic_acceptance_flags=True, hermes_layers_retained=True)
status = dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
report('supervisor_status', **{k:status[k].strip() for k in ('CapEff','CapPrm','CapBnd','NoNewPrivs','Seccomp')})
assert int(status['CapEff'],16) == 0xe3
report('python_startup', version=sys.version, executable=sys.executable, path=sys.path, sitecustomize='sitecustomize' in sys.modules, usercustomize='usercustomize' in sys.modules)
site_module = sys.modules.get('sitecustomize')
if site_module is not None:
    site_path = Path(site_module.__file__)
    report('image_sitecustomize', path=str(site_path), sha256=hashlib.sha256(site_path.read_bytes()).hexdigest(), content=site_path.read_text(errors='replace')[:12000])
for directory in sys.path:
    if 'site-packages' in directory:
        for pth in sorted(Path(directory).glob('*.pth')):
            report('image_pth', path=str(pth), sha256=hashlib.sha256(pth.read_bytes()).hexdigest(), content=pth.read_text(errors='replace')[:6000])
assert run(['/usr/local/bin/node','--version']) == 'v24.21.0'
run(['/usr/local/bin/node','-p','JSON.stringify({arch:process.arch,platform:process.platform,abi:process.versions.modules})'])
assert 'not found' not in run(['/usr/bin/ldd','/usr/local/bin/node'])
clis = list(Path('/opt/pi').glob('**/pi-coding-agent/dist/cli.js'))
assert len(clis) == 1, clis
assert run(['/usr/local/bin/node',str(clis[0]),'--version']) == '0.73.1'
for module in ('pptx','PIL.Image','lxml.etree','playwright.sync_api'):
    loaded = importlib.import_module(module)
    report('python_import', module=module, path=loaded.__file__)
skill = Path('/opt/ppt-master/skills/ppt-master')
assert skill.joinpath('SKILL.md').is_file()
report('official_skill', path=str(skill), sha256=hashlib.sha256(skill.joinpath('SKILL.md').read_bytes()).hexdigest())
run([PY,str(skill/'scripts/attribution_guard.py')])
assert 'Noto Sans CJK' in run(['/usr/bin/fc-match','-f','%{family}','Noto Sans CJK SC'])
chromiums = sorted(str(p) for p in Path('/opt/ms-playwright').glob('**/chrome') if p.is_file())
report('chromium_metadata', executables=chromiums)
assert chromiums
assert not any(k == 'run_agent' or k.split('.')[0] in ('hermes','hermes_cli','agent') for k in sys.modules)
report('hermes_import_not_loaded', checked_modules_only=True)
sys.path.insert(0,str(ROOT/'source'))
import pi_worker.runtime.adapter as adapter
runtime_hash = hashlib.sha256(Path(adapter.__file__).read_bytes()).hexdigest()
assert runtime_hash == '297e77e2a56fd9af50a77a2b0b6ae14fbbf2b2c87771afe18a780a9c5beca3f2'
report('runtime_source', path=adapter.__file__, sha256=runtime_hash)
from pi_worker.runtime.adapter import PiRuntime, RuntimeSettings, RuntimeExecutionError
from pi_worker.runtime.profiles import ExecutionProfile, CommonExecution
class TestOnlyProfile(ExecutionProfile):
    production_ready = True  # Synthetic process proof ONLY, never PptProfile.
    def validate(self, wire): return CommonExecution(**wire)
    def build_prompt(self, wire): return wire['task_id']
    def execution_provenance(self, started, finished): return {'engine':'test-only','profile':'test-only'}
control = Path('/work/control'); control.mkdir(mode=0o700)
(control/'sentinel').write_text('synthetic root-only sentinel')
other = Path('/work/other-attempt'); other.mkdir(mode=0o700)
(other/'sentinel').write_text('synthetic other UID sentinel')
os.chown(other/'sentinel',29999,29999); os.chown(other,29999,29999)
testskill = ROOT/'harness/TEST_SKILL.md'
manifest = ROOT/'harness/manifest.json'
settings = RuntimeSettings(command=(PY,str(ROOT/'harness/fake_pi.py')),node_command=('/usr/local/bin/node',),provider='test-only',model='synthetic',skill_paths=(testskill,),skill_hashes=(hashlib.sha256(testskill.read_bytes()).hexdigest(),),bundle_manifest=manifest,bundle_manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),skill_bundle_revision='test-only-r19',runtime_revision='frozen-r19',image_digest='sha256:'+'0'*64,fixture_mode=False,capability_verified=True,model_verified=True,isolation_verified=True,control_dir=control,denied_paths=(control/'sentinel',Path('/proc/1/environ'),other/'sentinel'),term_grace_seconds=0.1,kill_grace_seconds=3,max_processes=32)
report('synthetic_settings', fixture_mode=False, acceptance_flags_are_test_inputs=True, digest_is_test_placeholder=True, uid_allocator='fresh container counter; no persisted allocator integration')
for index, mode in enumerate(('normal','cancel','deadline','fence')):
    uid = 20000 + index
    runtime = PiRuntime(replace(settings,run_gid=uid), profile=TestOnlyProfile())
    workspace = Path('/work') / mode
    cancellation = threading.Event()
    timer = None
    events = []
    deadline = datetime.now(timezone.utc) + timedelta(seconds=3 if mode == 'deadline' else 20)
    job = {'task_id':mode,'runtime_revision':'frozen-r19','skill_bundle_revision':'test-only-r19','timeout_seconds':3 if mode == 'deadline' else 20,'execution_deadline_at':deadline.isoformat(),'_runtime_uid':uid}
    if mode == 'cancel':
        def request_cancel():
            until = time.monotonic() + 10
            while not (workspace/'heartbeat').exists() and time.monotonic() < until: time.sleep(0.02)
            cancellation.set()
        timer = threading.Thread(target=request_cancel); timer.start()
    if mode == 'fence':
        real_stop = runtime._stop_tree
        def safe_simulated_failure(*args, **kwargs):
            real_stop(*args, **kwargs)
            raise RuntimeExecutionError('process_safety_unverified','TEST ONLY injected failure AFTER real successful cleanup')
        runtime._stop_tree = safe_simulated_failure
    error = None
    try: runtime.execute(job,workspace,events.append,cancellation)
    except RuntimeExecutionError as exc: error = exc.code
    if timer: timer.join(timeout=11); assert not timer.is_alive()
    expected = {'normal':None,'cancel':'cancelled','deadline':'timed_out','fence':'process_safety_unverified'}[mode]
    assert error == expected, (mode,error,expected)
    checks = json.loads((workspace/'checks.json').read_text())
    assert checks['uid'] == [uid,uid,uid]
    assert not runtime._uid_processes(uid)
    heartbeat = workspace/'heartbeat'
    before = heartbeat.stat().st_size
    time.sleep(0.15)
    assert heartbeat.stat().st_size == before
    if mode == 'fence':
        assert runtime.health()['code'] == 'process_safety_unverified'
        assert list(control.glob('runtime-*.json'))
        runtime._stop_tree = real_stop
        assert runtime.recover((uid,))
    assert not list(control.glob('runtime-*.json'))
    report('runtime_case', mode=mode, uid=uid, error=error, child_checks=checks, no_surviving_uid_process=True, no_surviving_writer=True, events=[e['type'] for e in events])
report('complete', phaseA=True, phaseB=True, actual_pi_model_call=False, ppt_profile_production_ready=False, renderer_handoff_proven=False)
