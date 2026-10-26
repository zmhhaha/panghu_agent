"""Fixed OFFLINE TEST-ONLY actual Pi/domain smoke; no live worker admission.

Run with no arguments, as Linux root in the pinned candidate image. The parent
must impose network=none and CPU/memory limits; this process cannot prove those
Docker settings. Mount current source read-only at /code, inputs/task/base RO,
project/result/audit RW, and /work/runtime plus /tmp as fresh tmpfs mounts.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import ssl
import stat
import sys
import threading
import time

sys.path.insert(0, '/code')

from pi_worker.runtime.adapter import PiRuntime, RuntimeSettings
from pi_worker.runtime.profiles import CommonExecution, ExecutionProfile
from ppt_service.control.execution import Attempt, StopProof
from ppt_service.control.pi_uid import PiUIDAllocator, UIDScope
from ppt_service.control.stop_gate import combined_stop_proof
from ppt_service.tests import test_pi_responses_proxy as fixture
from ppt_service.worker.pi_profile import PptProfile
from ppt_service.worker.proxy_usage import UsageLedger
from ppt_service.worker.model_grant import ModelGrant, ModelIdentity
from ppt_service.worker.container_lease import ContainerLaunch, ContainerLease, read_private

IMAGE = 'sha256:2a09398d26a387ac79b3b7b635202062ee7856f151b47eba0cb773b7e020e1a1'
PROJECT = '00000000-0000-4000-8000-000000000501'
JOB = '00000000-0000-4000-8000-000000000502'
REVISION = 'reviewed-test-only-domain-v1'
HELPER = Path('/code/ppt_service/images/pi_candidate/domain_probe.py')
COMMAND = '/opt/hermes/.venv/bin/python /code/ppt_service/images/pi_candidate/domain_probe.py'
MARKER = 'PI_DOMAIN_FIXED_PROBE_COMPLETE'
PROMPT = ('OFFLINE TEST ONLY. Run the fixed synthetic bash probe once. Read source '
          '/work/inputs/source.md; task /work/task/task.json and baseline /work/base/base.md '
          'are read-only. Write only /work/project/probe.txt and /work/result/probe.json. '
          'Use the reviewed fixed probe. No PPT generation or external model call.\n')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def directory(path, *, root=True):
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and not path.is_symlink(), 'ordinary directory required: ' + str(path))
    require(not info.st_mode & 0o022, 'replaceable directory: ' + str(path))
    if root:
        require(info.st_uid == 0, 'root-owned directory required: ' + str(path))
    return info


def ordinary(path, *, root=False):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and not path.is_symlink(), 'ordinary single-link file required: ' + str(path))
    if root:
        require(info.st_uid == 0 and not info.st_mode & 0o022, 'controller-owned file required')
    return {'path': str(path), 'bytes': info.st_size, 'sha256': sha(path), 'uid': info.st_uid,
            'mode': oct(stat.S_IMODE(info.st_mode))}


def mount_contract():
    mounts = {}
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left, right = line.split(' - ', 1)
        fields, fs = left.split(), right.split()
        target = fields[4]
        require(target not in mounts, 'stacked mount rejected: ' + target)
        mounts[target] = {'options': fields[5].split(','), 'fstype': fs[0]}
    expected = {'/': 'ro', '/code': 'ro', **{'/work/' + name: 'ro' for name in ('inputs', 'task', 'base')},
                **{'/work/' + name: 'rw' for name in ('project', 'result', 'audit', 'runtime')}, '/tmp': 'rw'}
    measured = {}
    for path, mode in expected.items():
        require(path in mounts and mode in mounts[path]['options'], 'exact mount mode required: ' + path + ' ' + mode)
        require(('rw' if mode == 'ro' else 'ro') not in mounts[path]['options'], 'contradictory mount options')
        measured[path] = mounts[path]
    for path in ('/work/runtime', '/tmp'):
        require(mounts[path]['fstype'] == 'tmpfs', 'fresh tmpfs required: ' + path)
    protected = ('/code', '/work/inputs', '/work/task', '/work/base')
    for target in mounts:
        require(not any(target.startswith(parent + '/') for parent in protected), 'nested mount in immutable domain')
    return measured


class TestOnlyDomainProfile(ExecutionProfile):
    # Synthetic readiness inputs for shared isolation code, never PptProfile.
    production_ready = True

    def validate(self, wire):
        return CommonExecution(**wire)

    def build_prompt(self, wire):
        return PROMPT

    def execution_provenance(self, started, finished):
        return {'engine': 'test-only', 'profile': 'test-only-domain',
                'execution_started_at': started, 'execution_finished_at': finished}


def main():
    require(sys.argv[1:] in ([], ['--host-controlled']) and sys.platform == 'linux' and os.getresuid() == (0, 0, 0), 'fixed Linux root test only')
    require(PptProfile.production_ready is False, 'PptProfile must remain disabled')
    mounts = mount_contract()  # Before any host-mounted directory changes.
    lease = None
    launch = None
    if sys.argv[1:] == ['--host-controlled']:
        launch = ContainerLaunch.parse(read_private(Path('/work/task/domain-launch.json'), launch=True))
        lease = ContainerLease(launch)
        lease.poll()  # Reject expiry/revocation before any private state or UID handoff.
    for path in ('/', '/work', '/code', '/work/inputs', '/work/task', '/work/base', '/work/project', '/work/result', '/work/audit'):
        directory(Path(path))
    # Runtime tmpfs may use mode1777; require sticky root ownership if writable.
    scratch_parent = Path('/work/runtime')
    info = scratch_parent.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not scratch_parent.is_symlink(), 'unsafe runtime mount')
    require(not info.st_mode & 0o022 or info.st_mode & stat.S_ISVTX, 'runtime tmpfs must protect root-created children')
    for path in HELPER.parents:
        directory(path)
    helper_before = ordinary(HELPER, root=True)
    source = Path('/work/inputs/source.md')
    seeded = [source, Path('/work/task/task.json'), Path('/work/base/base.md')]
    before = [ordinary(path, root=True) for path in seeded]
    require(all(row['bytes'] > 0 for row in before), 'nonempty synthetic seeds required')
    json.loads(seeded[1].read_text())
    source_bytes = source.read_bytes()
    for name in ('project', 'result'):
        require(not any(Path('/work', name).iterdir()), 'empty output domains required')
    audit = Path('/work/audit')
    control = audit / 'control-private'
    if control.exists():
        info = directory(control)
        require(stat.S_IMODE(info.st_mode) == 0o700, 'private controller mode required')
    else:
        control.mkdir(mode=0o700)
    sentinel = control / 'sentinel'
    if not sentinel.exists():
        sentinel.write_text('OFFLINE TEST ONLY controller sentinel\n')
        sentinel.chmod(0o600)
    ordinary(sentinel, root=True)
    if launch is None:
        scope = UIDScope('test-only-domain', 'candidate-2a0939', 'offline-container', 31800, 31999)
        ledger_dir = control / 'uid-ledger'
        allocator = PiUIDAllocator(ledger_dir, scope) if ledger_dir.exists() else PiUIDAllocator.initialize(ledger_dir, scope)
        import sqlite3
        with sqlite3.connect(allocator.path) as db:
            attempt_number = db.execute('SELECT count(*) FROM allocations').fetchone()[0] // 2 + 1
        fencing_token = attempt_number
        project_id, job_id, phase = PROJECT, JOB, 'plan_initial'
        binding = dict(project_id=project_id, job_id=job_id, attempt=attempt_number, token=fencing_token)
        pi_uid = allocator.allocate(**binding, role='pi')
        preview_uid = allocator.allocate(**binding, role='preview')
        require(pi_uid != preview_uid and all(allocator.verify(**binding, role=role) == uid for role, uid in (('pi', pi_uid), ('preview', preview_uid))), 'UID ledger verification failed')
    else:
        project_id, job_id = launch.identity.project_id, launch.identity.job_id
        attempt_number, fencing_token, phase = launch.identity.attempt, launch.identity.token, launch.identity.phase
        pi_uid, preview_uid = launch.pi_uid, launch.preview_uid
    writer_uids = (pi_uid, preview_uid)
    require(not PiRuntime._uid_processes(pi_uid) and not PiRuntime._uid_processes(preview_uid), 'fresh reserved UIDs have processes')
    for name in ('project', 'result'):
        path = Path('/work', name)
        os.chmod(path, 0o700)
        os.chown(path, pi_uid, pi_uid)
    resources = audit / 'resources'
    resources.mkdir(mode=0o755)
    skill = resources / 'SKILL.md'
    skill.write_text('---\nname: domain-reviewed-test-placeholder\ndescription: REVIEWED TEST placeholder for fixed offline domain smoke only.\n---\nREVIEWED TEST ONLY. No production instructions or PPT acceptance evidence.\n')
    skill.chmod(0o444)
    manifest = resources / 'manifest.json'
    manifest.write_text(json.dumps({'bundle_revision': REVISION, 'local_files': [{'path': 'SKILL.md', 'sha256': sha(skill)}]}, sort_keys=True))
    manifest.chmod(0o444)
    for resource in (skill, manifest):
        ordinary(resource, root=True)
    clis = list(Path('/opt/pi').glob('**/pi-coding-agent/dist/cli.js'))
    require(len(clis) == 1, 'exactly one installed pinned Pi CLI required')
    fixture.ARGS = {'command': COMMAND, 'timeout': 10}
    fixture.MARKER = MARKER
    usage_path = control / ('usage-' + str(attempt_number) + '.jsonl')
    scratch = scratch_parent / 'domain-attempt'
    require(not scratch.exists(), 'fresh separate scratch required')
    os.environ['DOMAIN_CONTROLLER_SECRET_API_KEY'] = 'test-only-must-never-reach-child'
    events = []
    interrupted = threading.Event()
    if launch is not None and launch.mode == 'hold':
        original_sse = fixture.sse
        def held_sse(round_number):
            if round_number == 1:
                marker = audit / 'model-request-started.json'
                marker.write_text(json.dumps({'identity': asdict(launch.identity), 'test_only': True}))
                marker.chmod(0o600)
                interrupted.wait(120)
            return original_sse(round_number)
        fixture.sse = held_sse
    attempt = Attempt(job_id, project_id, attempt_number, fencing_token, phase, {'hash': before[0]['sha256']}, 'test-only-domain')
    ledger = UsageLedger(str(usage_path), job_id=job_id, attempt=attempt_number, phase=phase)
    ttl = 30 if lease is None else lease.poll()['deadline_monotonic'] - time.monotonic()
    grant = ModelGrant(ModelIdentity(project_id, job_id, attempt_number, fencing_token, phase),
                       fixture.WORKER, ttl_seconds=ttl, max_requests=2, max_concurrency=1)
    with fixture.LocalProxy(ledger, model_grant=grant) as local:
        settings = RuntimeSettings(command=('/usr/local/bin/node', str(clis[0])), node_command=('/usr/local/bin/node',),
            provider='fixture', model=fixture.proxy.DEFAULT_MODEL, fixture_mode=False, compact_events=True,
            model_api='openai-responses', model_base_url=f'http://127.0.0.1:{local.proxy.server_port}/v1',
            model_env={'SCIENTIFIC_MODEL_API_KEY': fixture.WORKER}, skill_paths=(skill,), skill_hashes=(sha(skill),),
            bundle_manifest=manifest, bundle_manifest_sha256=sha(manifest), skill_bundle_revision=REVISION,
            runtime_revision=REVISION, image_digest=IMAGE, capability_verified=True, model_verified=True,
            isolation_verified=True, control_dir=control, run_gid=pi_uid, denied_paths=(sentinel,),
            idle_timeout_seconds=20, term_grace_seconds=.2, kill_grace_seconds=4, max_processes=64)
        runtime = PiRuntime(settings, profile=TestOnlyDomainProfile())
        health = runtime.health()  # Shared adapter's actual Node and CLI version checks.
        require(health['ready'] and health['pi_version'] == '0.73.1' and health['node_version'] == '24.21.0', 'pinned shared readiness failed: ' + repr(health))
        status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
        require(all(int(status[key], 16) == 0xe3 for key in ('CapEff', 'CapPrm', 'CapBnd')), 'root supervisor requires exactly five capabilities')
        if lease is not None:
            lease.bind(grant, interrupted)
        job = dict(task_id=job_id, runtime_revision=REVISION, skill_bundle_revision=REVISION,
                   timeout_seconds=60, execution_deadline_at=(datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(), _runtime_uid=pi_uid)
        execution_error = None
        try:
            runtime.execute(job, scratch, events.append, interrupted)
        except Exception as exc:
            execution_error = exc
        finally:
            proof = combined_stop_proof(attempt, writer_uids, grace=5)
            if lease is not None:lease.close()
        if execution_error is not None:
            if lease is not None and interrupted.is_set():
                stopped_report = audit / 'lifecycle-stop-report.json'
                stopped_report.write_text(json.dumps({'test_only': True, 'identity': asdict(launch.identity),
                    'model_grant': grant.snapshot(), 'lease': lease.snapshot(), 'stop_proof': asdict(proof),
                    'pi_error': getattr(execution_error, 'code', 'execution_stopped')}, sort_keys=True))
                stopped_report.chmod(0o600)
                raise SystemExit(2)
            raise execution_error
        require(type(proof) is StopProof and proof.uid == attempt.uid and proof.all_writers_stopped, 'controller stop proof mismatch')
        require(not runtime._uid_processes(pi_uid) and not runtime._uid_processes(preview_uid), 'surviving runtime UID process')
        require(not list(control.glob('runtime-*.json')) and not list(control.glob('launch-*.json')), 'runtime journals remain')
        require(not local.errors and len(local.requests) == len(local.connections) == 2, 'exactly two actual synthetic SSE requests required')
        require(local.observed == [('/v1/responses', fixture.proxy.DEFAULT_MODEL, True, False)] * 2, 'provider credential replacement failed')
        extra = fixture.http.client.HTTPConnection('127.0.0.1', local.proxy.server_port, timeout=2)
        try:
            extra.request('POST', '/v1/responses', body=json.dumps({'model': fixture.proxy.DEFAULT_MODEL, 'input': 'must not reach backend'}),
                          headers={'Authorization': 'Bearer ' + fixture.WORKER, 'Content-Type': 'application/json'})
            denied = extra.getresponse()
            require(denied.status == 429, 'spent task budget allowed another model request')
            denied.read()
        finally:
            extra.close()
        require(len(local.requests) == 2 and grant.snapshot()['requests_used'] == 2, 'task budget changed after refusal')
        for host, port, timeout, context in local.connections:
            require(host == 'custom-llm-proxy.panghuer.top' and port == 443 and 0 < timeout <= (30 if launch is None else 600) and context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED, 'task-bound proxy connector contract changed')
        first, second = local.requests
        calls = [row for row in second['input'] if row.get('type') == 'function_call']
        outputs = [row for row in second['input'] if row.get('type') == 'function_call_output']
        require(len(calls) == len(outputs) == 1, 'one tool call/output required')
        require(calls[0]['call_id'] == outputs[0]['call_id'] == fixture.CALL and calls[0]['name'] == 'bash', 'tool correlation failed')
        require(json.loads(calls[0]['arguments']) == fixture.ARGS, 'fixed probe command mismatch')
        tool_output = outputs[0]['output']
        if tool_output.strip() != MARKER:
            diagnostic = tool_output
            for secret in (fixture.WORKER, fixture.PROVIDER, 'test-only-must-never-reach-child'):
                diagnostic = diagnostic.replace(secret, '[REDACTED TEST KEY]')
            require(False, 'fixed probe output mismatch; sanitized_output=' + repr(diagnostic[:2000]))
        require('domain-reviewed-test-placeholder' in json.dumps(first['input']), 'reviewed test skill missing')
        request_text = json.dumps(local.requests)
        for secret in (fixture.WORKER, fixture.PROVIDER, 'test-only-must-never-reach-child', 'D:\\', '/host/', '/root/'):
            require(secret not in request_text, 'host path or credential leaked into model request')
    payload_path = Path('/work/result/probe.json')
    payload = json.loads(payload_path.read_text())
    require(payload['resuid'] == payload['resgid'] == [pi_uid] * 3 and payload['groups'] == [], 'child identities not fully dropped')
    require(all(int(payload['status'][key], 16) == 0 for key in ('CapEff', 'CapPrm', 'CapInh', 'CapAmb')) and payload['status']['NoNewPrivs'] == '1', 'child privilege verification failed')
    require(payload['source_sha256'] == before[0]['sha256'] and payload['source_bytes'] == len(source_bytes), 'actual source read mismatch')
    require(payload['synthetic_worker_key_only'] is True and len(payload['denied']) == 7 and all(payload['denied'].values()), 'domain/control denial checks missing')
    require(Path('/work/project/probe.txt').read_bytes() == b'OFFLINE TEST ONLY\n' + source_bytes, 'project output content mismatch')
    require([ordinary(path, root=True) for path in seeded] == before and ordinary(HELPER, root=True) == helper_before, 'immutable inputs or code changed')
    records = [json.loads(line) for line in usage_path.read_text().splitlines()]
    completed = [row for row in records if row['event'] != 'request_started']
    require(len(records) == 4 and len(completed) == 2 and len({row['proxy_request_id'] for row in completed}) == 2, 'usage request correlation failed')
    for n, row in enumerate(completed, 1):
        require(row['outcome'] == 'completed' and row['usage_status'] == 'known' and row['provider_response_id'] == f'resp_fixture_{n}' and row['provider_request_id'] == f'req_fixture_{n}', 'known usage missing')
        require([row[key] for key in ('input_tokens', 'output_tokens', 'cached_tokens', 'reasoning_tokens')] == [10*n, 3*n, n, n] and row['cost_usd'] is None, 'synthetic usage values mismatch')
    provenance = json.loads((scratch / 'provenance.json').read_text())
    require(provenance['fixture_mode'] is False and provenance['profile'] == 'test-only-domain' and provenance['image_digest'] == IMAGE, 'runtime provenance mismatch')
    require(events[-1]['type'] == 'runtime_finished', 'runtime completion missing')
    for tree in (scratch, Path('/work/project'), Path('/work/result')):
        for path in tree.rglob('*'):
            if path.is_file():
                data = path.read_bytes()
                require(all(secret.encode() not in data for secret in (fixture.PROVIDER, fixture.WORKER, 'test-only-must-never-reach-child')), 'credential persisted in child files')
    report = dict(test_only=True, external_model_calls=False, ppt_generation=False,
                  host_controlled_attempt=launch is not None, host_lease=None if lease is None else lease.snapshot(),
                  task_model_grant=grant.snapshot(), extra_model_request_denied=True,
                  synthetic_readiness_flags=True, **{'PptProfile.production_ready': False},
                  image_digest=IMAGE, fixture_mode=False, actual_pi_cli=True, actual_sse_requests=2,
                  preview_executed=False, pi_uid=pi_uid, preview_uid_reserved=preview_uid,
                  stop_proof=asdict(proof), no_runtime_uid_processes=True, mounts=mounts,
                  readiness=health, child_probe=payload, input_files=before,
                  measured_files=[ordinary(Path('/work/project/probe.txt')), ordinary(payload_path)],
                  usage=completed, helper=helper_before, runtime_adapter_sha256=sha(Path(sys.modules[PiRuntime.__module__].__file__)))
    encoded = json.dumps(report, sort_keys=True)
    output = audit / 'domain-smoke-report.json'
    require(not output.exists(), 'fresh report required')
    output.write_text(encoded + '\n')
    print(encoded, flush=True)


if __name__ == '__main__':
    main()
