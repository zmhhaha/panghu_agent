"""Administrator-only candidate probe; never enables or manufactures acceptance.

The proxy owns an isolated test database/signing key/upstream credential. A
separate private-PID Worker receives only one scoped grant. All production
factories, routes, accepted_host checks and deployment records remain unchanged.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import replace
from datetime import datetime, timezone, timedelta
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time
from uuid import UUID, uuid4

from fastapi import Request

from agent_service.contracts import (ExecutionEnvelope, InlineFile, JobRequest, ServiceError,
    WorkerResult, canonical_bytes, digest, verify_receipt)
from agent_service.confinement import ensure_controller_confinement
from agent_service.worker_entry import (CapsuleScope, HostConfig, HostSecrets,
    _read_operator_json, build_pi_runtime_settings, host_policy, PI_ADAPTER_REVISION)


PROFILES = {'ppt.' + stage + '.v1' for stage in (
    'plan_initial', 'plan_options', 'prepare', 'generate', 'revise')} | {
    'research.generate.v1', 'research.revise.v1', 'research.plan.v1',
    'research.retrieve.v1', 'research.outline.v1', 'research.draft.v1',
    'video.storyboard.v1', 'video.scene.v1'}
FIXTURE_PROFILES = {'ppt.plan_initial.v1', 'research.revise.v1'}
CONTROL = Path('/run/agent-control')
WORK = Path('/work')
BOOTSTRAP = Path('/run/agent-probe-bootstrap/token')
UPSTREAM = Path('/run/agent-probe-upstream/token')
MAX_WIRE = 16 * 1024 * 1024


def harness_sha256():
    # The mounted source uses canonical LF text, including when rendered on Windows.
    return hashlib.sha256(Path(__file__).read_text(encoding='utf-8').encode('utf-8')).hexdigest()


def require(value, code='candidate_probe_invalid'):
    if not value:
        raise ServiceError(code, 422)


def candidate_config(value):
    config = HostConfig.model_validate(value)
    require(config.profile.name in PROFILES and not config.profile.enabled
        and config.binding_id is not None and config.model is not None)
    from agent_service.worker_entry import _host_registry
    registry = _host_registry(config)
    policy = host_policy(config)
    binding = registry.binding(config.binding_id, config.binding_revision)
    require(binding.acceptance is None and not policy.enabled
        and all(not route.get('enabled', False) for route in config.execution_catalog['routes'])
        and binding.adapter_revision == (PI_ADAPTER_REVISION if config.engine == 'pi' else 'hermes-adapter-v1')
        and binding.model_policy.model == config.model.model
        and binding.model_policy.provider == config.model.provider)
    return config, policy, binding


def atomic(path, value):
    raw = value if type(value) is bytes else canonical_bytes(value)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def credential(path):
    # Same root-owned projected-file boundary used by the actual capsule.
    path = Path(path)
    resolved = path.resolve(strict=True)
    info = resolved.stat()
    require(info.st_uid == 0 and info.st_nlink == 1 and not info.st_mode & 0o077
        and 32 <= info.st_size <= 4096)
    value = resolved.read_text().strip()
    require(32 <= len(value) <= 2048 and all(33 <= ord(c) <= 126 for c in value))
    return value


def fixture(profile, timeout=600):
    """Explicit synthetic business data; only the Agent/model execution is real."""
    require(profile in FIXTURE_PROFILES, 'candidate_scenario_required')
    business = str(uuid4())
    if profile == 'ppt.plan_initial.v1':
        project, asset = str(uuid4()), str(uuid4())
        source = b'# Candidate fixture\nExplain a three-page source-to-result Agent workflow. Synthetic acceptance material only.\n'
        snapshot = dict(project_id=project, title='Candidate acceptance fixture',
            settings=dict(page_count=3, language='en', aspect_ratio='16:9',
                audience='developers', purpose='explain the synthetic source, task and candidate result'),
            assets=[dict(id=asset, storage_key=f'projects/{project}/assets/{asset}/original',
                sha256=hashlib.sha256(source).hexdigest(), media_type='text/markdown', bytes=len(source))])
        task = dict(schema_version='ppt-plan_initial.v1', phase='plan_initial', project_id=project,
            business_job_id=business, input_snapshot_sha256=digest(snapshot), input_snapshot=snapshot,
            feedback='', revision_pages=[], confirmations={}, checkpoint=None, base_version=None)
        return task, {asset + '.md': source}
    require(profile == 'research.revise.v1')
    report = ('# Synthetic fixture\nThis is not a scientific finding.\n\n## Methods\n'
        'Only recorded empty evidence is available.\n\n## Findings\nInsufficient evidence.\n\n'
        '## Limitations\nNo scientific conclusion is supported.\n\n## References\nNone.\n')
    artifacts = {'papers': b'', 'evidence': b'', 'claims': b'', 'references': b'[]'}
    items = [dict(artifact_id=key, size=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
        media_type='text/markdown' if key == 'report' else
            'application/json' if key == 'references' else 'application/x-ndjson')
        for key, raw in {**artifacts, 'report': report.encode()}.items()]
    from scientific_agent.pi_sections import index_sections, select_section
    index = index_sections(report)
    selected = next(row for row in index['sections'] if row['title'] == 'Findings')
    material = dict(report=report, items=items,
        artifacts={key: base64.b64encode(raw).decode() for key, raw in artifacts.items()})
    now = datetime.now(timezone.utc).replace(microsecond=0)
    task = dict(schema_version='1', business_task_id=business, topic='Synthetic empty-evidence fixture',
        mode='review', created_at=now.isoformat().replace('+00:00', 'Z'),
        execution_deadline_at=(now + timedelta(seconds=timeout)).isoformat().replace('+00:00', 'Z'),
        intent_id=str(uuid4()), parent_version_id=str(uuid4()), root_report_id=str(uuid4()),
        base_sha256=index['report_sha256'], section_id=selected['section_id'], section_sha256=selected['sha256'],
        instruction='Improve clarity of this section, explicitly retaining insufficient evidence. No new sources or claims.',
        input_sha256=digest(material), material_policy='recorded_evidence_only')
    files = {'revision-materials.json': canonical_bytes(material), 'input-report.md': report.encode(),
        'input-section.md': select_section(report, selected['section_id'], index['report_sha256'])['text'].encode(),
        **{key + ('.json' if key == 'references' else '.jsonl'): raw for key, raw in artifacts.items()}}
    return task, files


class ProbeAuthority:
    def __init__(self, config, directory, upstream_token, bootstrap, worker_name, worker_namespace, uid, *, scenario=None):
        from agent_service.model_proxy import ModelProxy, ModelProxySettings, requests
        from agent_service.store import Store
        self.config, self.policy, self.binding = candidate_config(config.model_dump(mode='json'))
        require(type(uid) is int and 65536 <= uid <= 2**31 - 4)
        self.bootstrap, self.uid, self.scenario = bootstrap, uid, scenario
        if scenario is not None:
            require(scenario.document['profile'] == self.policy.name)
            scenario.reserve_uid(uid)
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        # A second coordinator must never reset an ambiguous paid probe.
        atomic(self.directory / 'once.json', {'mode': 'candidate-only', 'created_at': time.time()})
        if scenario is None:
            self.store = Store('sqlite:///' + str(self.directory / 'candidate.sqlite3'), allow_sqlite=True)
        else:
            from agent_service.deploy.candidate_scenario import CandidateScenarioStore
            self.store = CandidateScenarioStore('sqlite:///' + str(self.directory / 'candidate.sqlite3'),
                allow_sqlite=True, scenario=scenario)
        self.store.initialize()
        self.proxy = ModelProxy(self.store, ModelProxySettings(secrets.token_bytes(48),
            'http://llm-service.llm.svc.cluster.local/v1', upstream_token,
            max_calls_per_attempt=40, max_output_tokens=32768))
        self.request_table, self.receipt_key = requests, secrets.token_bytes(32)
        task, files = (fixture(config.profile.name) if scenario is None else
                       (scenario.document['task'], scenario.files))
        from agent_service.profiles import implementation
        implementation(config.profile.name).validate_task(task)
        refs = []
        for name, raw in sorted(files.items()):
            item = self.store.put_input('candidate-probe', 'administrator', name, raw)
            refs.append({'input_id': item['input_id'], 'sha256': item['sha256']})
        timeout = 600 if scenario is None else scenario.document['timeout_seconds']
        absolute = None
        if scenario is not None and self.policy.name.startswith('research.'):
            from agent_service.profiles.research import task_deadline
            absolute = task_deadline(task)
        request = JobRequest(profile=self.policy.name, profile_revision=self.policy.revision,
            business_ref='candidate-only:' + str(uuid4()), task=task, inputs=refs,
            timeout_seconds=timeout, execution_deadline_at=absolute)
        self.store.submit('candidate-probe', 'administrator', 'single-candidate-probe',
            request.model_dump(mode='json'), self.policy.pins())
        claimed = self.store.claim(lease_seconds=300, provisioning={self.policy.engine: 'kubernetes_per_attempt'})
        with self.store.engine.connect() as db:
            self.envelope = self.store._worker_envelope(db, claimed)
        require(len(canonical_bytes(self.envelope.model_dump(mode='json'))) < MAX_WIRE - 1024 * 1024,
            'candidate_probe_scale_exceeded')
        atomic(self.directory / 'candidate-request.json', dict(schema_version='agent-candidate-request.v1',
            candidate_scope_only=True, envelope=self.envelope.model_dump(mode='json')))
        self.store.reserve_worker(self.envelope, 'candidate-probe', worker_namespace, worker_name,
            digest({'mode': 'candidate-only', 'config': config.model_dump(mode='json'),
                'worker_name': worker_name, 'worker_namespace': worker_namespace, 'uid': uid}),
            agent_uid=uid, aux_uid=uid + 1)
        require(self.store.claim_worker_create(self.envelope.attempt_id, self.envelope.request_sha256))
        self.grant = self.proxy.issuer.issue(self.envelope, self.binding.model_policy)
        self.registered = self.recorded = None
        self.worker_token = secrets.token_hex(24)
        self.lock = threading.RLock()

    def register(self, pod_uid):
        try:
            require(str(UUID(pod_uid)) == pod_uid)
        except (ValueError, TypeError, AttributeError):
            raise ServiceError('candidate_pod_identity_invalid', 422) from None
        with self.lock:
            if self.registered is not None:
                require(self.registered == pod_uid, 'candidate_pod_conflict')
            else:
                self.store.bind_worker(self.envelope.attempt_id, self.envelope.request_sha256, pod_uid)
                require(self.store.claim_worker_start(self.envelope.attempt_id, self.envelope.request_sha256))
                require(self.store.mark_running(self.envelope.job_id, self.envelope.attempt_id, self.envelope.fence))
                self.registered = pod_uid
            return dict(harness_sha256=harness_sha256(),
                scenario=None if self.scenario is None else self.scenario.metadata,
                config=self.config.model_dump(mode='json'),
                envelope=self.envelope.model_dump(mode='json'),
                scope=dict(attempt_id=self.envelope.attempt_id, pod_uid=pod_uid,
                    request_sha256=self.envelope.request_sha256, agent_uid=self.uid, preview_uid=self.uid + 1,
                    execution_binding_sha256=self.envelope.execution_binding.sha256),
                secrets=dict(worker_token=self.worker_token, receipt_key_hex=self.receipt_key.hex(),
                    model_proxy_token=self.grant, model_proxy_attempt_id=self.envelope.attempt_id,
                    model_proxy_request_sha256=self.envelope.request_sha256,
                    model_proxy_expires_at=self.envelope.deadline_at))

    def lease(self):
        require(self.recorded is None, 'candidate_probe_finished')
        require(self.store.renew(self.envelope.job_id, self.envelope.attempt_id, self.envelope.fence, lease_seconds=300))
        value = self.store.get_job('candidate-probe', 'administrator', self.envelope.job_id)
        return {'lease_expires_at': value['lease_expires_at']}

    def calls(self):
        from sqlalchemy import select
        with self.store.engine.connect() as db:
            return [dict(body_sha256=row['body_sha256'], state=row['state'], status=row['status'],
                         response_sha256=hashlib.sha256(row['response']).hexdigest() if row['response'] else None)
                for row in db.execute(select(self.request_table).where(
                    self.request_table.c.attempt_id == self.envelope.attempt_id)).mappings()]

    def result(self, document):
        with self.lock:
            require(self.recorded is None, 'candidate_probe_finished')
            result = WorkerResult.model_validate(document['result'])
            require(verify_receipt(self.envelope, result, self.receipt_key, time.time()), 'candidate_stop_receipt_invalid')
            require(type(document.get('observations')) is dict)
            require(document['observations'].get('harness_sha256') == harness_sha256(), 'candidate_harness_mismatch')
            if self.scenario is not None:
                require(document['observations'].get('scenario') == self.scenario.metadata, 'candidate_harness_mismatch')
            atomic(self.directory / 'candidate-result.json', document)
            self.store.stage_worker_result(self.envelope.attempt_id, self.envelope.request_sha256,
                result.model_dump(mode='json'))
            before = self.calls()
            # Real wall-clock lease expiry, with no fake clock and no new model
            # request. Worker writers have already stopped before this step.
            require(self.store.renew(self.envelope.job_id, self.envelope.attempt_id, self.envelope.fence, lease_seconds=1))
            time.sleep(1.1)
            revoked = False
            try:
                self.proxy.authorize(self.grant)
            except ServiceError as error:
                revoked = error.code == 'model_grant_revoked' and error.status == 401
            require(revoked, 'candidate_lease_not_revoked')
            from agent_service.registry import binding_fingerprint
            from agent_service.worker_entry import _host_registry
            registry = _host_registry(self.config)
            combination = binding_fingerprint(registry.contract(self.policy.name, self.policy.revision),
                registry.bundle(self.binding.bundle.name, self.binding.bundle.revision),
                registry.environment(self.binding.tool_environment.name, self.binding.tool_environment.revision), self.binding)
            self.recorded = dict(schema_version='agent-candidate-probe.v1', candidate_only=True,
                candidate_scope_only=True, scenario=None if self.scenario is None else self.scenario.metadata,
                production_acceptance_created=False, production_routes_changed=False,
                engine=self.policy.engine, profile=self.policy.name, profile_revision=self.policy.revision,
                binding_id=self.binding.id, binding_revision=self.binding.revision, combination_sha256=combination,
                harness_sha256=harness_sha256(),
                image_digest=self.policy.image_digest, execution_binding_sha256=self.envelope.execution_binding.sha256,
                job_id=self.envelope.job_id, attempt_id=self.envelope.attempt_id, pod_uid=self.registered,
                request_sha256=self.envelope.request_sha256, result_state=result.state,
                candidate_result_sha256=digest(document['result']), artifact_count=len(result.artifacts),
                artifacts=[dict(name=item.name, sha256=item.sha256, bytes=len(item.content()))
                           for item in result.artifacts],
                writer_resources=list(result.stop_receipt.resource_ids),
                writers_observed_stopped_at=result.stop_receipt.observed_at,
                observations=document['observations'], model_calls=before, lease_expiry_rejected=True,
                pod_termination='external_operator_observation_required',
                unverified=['external_actual_pod_image_and_security_context', 'external_pod_termination',
                    'other_profiles_and_engines', 'full_production_acceptance_suite'])
            atomic(self.directory / 'candidate-evidence.json', self.recorded)
            print(json.dumps(self.recorded, sort_keys=True), flush=True)
            return {'recorded': True, 'lease_expiry_rejected': True}


def proxy_app(authority):
    from agent_service.model_proxy import create_model_proxy_app
    from fastapi.responses import JSONResponse
    app = create_model_proxy_app(authority.proxy)
    @app.exception_handler(ServiceError)
    async def failure(request, exc):
        # Candidate-only diagnostics distinguish pre-admission rejection from a
        # provider failure without recording headers, request bodies or grants.
        known = {'authentication_required', 'invalid_json', 'request_too_large',
            'model_grant_invalid', 'model_grant_revoked', 'model_grant_scope_mismatch',
            'model_request_scope_invalid', 'model_output_bound_invalid',
            'model_input_reference_not_authorized', 'model_admission_uncertain',
            'model_budget_exhausted', 'model_upstream_rejected',
            'model_upstream_protocol_invalid', 'model_response_too_large',
            'research_tool_not_authorized', 'research_tool_budget_exhausted',
            'research_tool_output_limit', 'tool_observation_uncertain'}
        route = request.url.path if request.url.path in {
            '/v1/responses', '/v1/models', '/v1/tools/academic'} else 'candidate-control'
        print(json.dumps(dict(event='candidate_request_rejected', route=route,
            status=exc.status if type(exc.status) is int and 400 <= exc.status <= 599 else None,
            code=exc.code if exc.code in known else 'candidate_request_failed')), flush=True)
        return JSONResponse({'error': {'code': exc.code}}, status_code=exc.status)
    def authorize(request):
        if not hmac.compare_digest(request.headers.get('authorization', ''), 'Bearer ' + authority.bootstrap):
            raise ServiceError('candidate_admin_authentication_required', 401)
    async def bounded(request):
        authorize(request)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_WIRE:
                raise ServiceError('request_too_large', 413)
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise ServiceError('invalid_json', 422) from None
        require(type(value) is dict)
        return value
    @app.post('/probe/register')
    async def register(request: Request):
        value = await bounded(request)
        require(set(value) == {'pod_uid'} and type(value['pod_uid']) is str)
        return authority.register(value['pod_uid'])
    @app.post('/probe/lease')
    async def lease(request: Request):
        require(await bounded(request) == {})
        return authority.lease()
    @app.post('/probe/result')
    async def result(request: Request):
        value = await bounded(request)
        require(set(value) == {'result', 'observations'})
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(authority.result, value)
    @app.get('/probe/evidence')
    def evidence(request: Request):
        authorize(request)
        return authority.recorded or {'status': 'pending', 'candidate_only': True}
    return app


CHILD = r'''
import errno,json,os,subprocess,sys,time
from pathlib import Path
root=Path(sys.argv[1]); private=Path(sys.argv[2])
status=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
denied=False
try: private.read_bytes()
except PermissionError: denied=True
write_denied=False
try: (root/'inputs/input.txt').write_bytes(b'tampering')
except OSError as error: write_denied=error.errno in (errno.EROFS,errno.EACCES,errno.EPERM)
writer="from pathlib import Path;import sys,time;p=Path(sys.argv[1]);n=0\nwhile True:\n n+=1;p.write_text(str(n));time.sleep(.02)"
child=subprocess.Popen([sys.executable,'-c',writer,str(root/'detached-heartbeat')],start_new_session=True)
result=dict(uid=list(os.getresuid()),groups=os.getgroups(),control_unreadable=denied,input_write_denied=write_denied,
 caps_empty=all(int(status[k].strip(),16)==0 for k in ('CapEff','CapPrm','CapInh','CapAmb')),
 no_new_privileges=status.get('NoNewPrivs','').strip()=='1',detached_pid=child.pid)
(root/'observation.json').write_text(json.dumps(result))
while True: time.sleep(.1)
'''


def isolation_preflight(scope):
    from agent_service.linux_stop import LinuxUidStopController, private_kernel_scope
    from agent_service.mounts import ReadOnlyInputs
    from agent_service.staging import ExecutionContext, stage_inputs
    confinement = ensure_controller_confinement(read_only_mounts=True)
    private_kernel_scope()
    CONTROL.mkdir(mode=0o700, parents=True, exist_ok=True); CONTROL.chmod(0o700)
    WORK.mkdir(mode=0o711, parents=True, exist_ok=True); WORK.chmod(0o711)
    private = CONTROL / 'private-sentinel'
    atomic(private, b'candidate-probe-private-sentinel\n')
    identity, uid = str(uuid4()), scope.agent_uid + 2
    workspace = WORK / identity
    workspace.mkdir(mode=0o700); os.chown(workspace, uid, uid)
    source = stage_inputs(CONTROL / 'input-sources', identity, [InlineFile.from_bytes('input.txt', b'fixed')],
        max_bytes=1024, max_files=1, subtree='inputs')
    mounts = ReadOnlyInputs(workspace, ExecutionContext(inputs=source))
    stopper = LinuxUidStopController(CONTROL / 'preflight-scope', identity, scope.pod_uid,
                                    scope.request_sha256, agent_uid=uid)
    stopper.initialize()
    process = None
    try:
        mounts.prepare()
        require(os.statvfs(workspace / 'inputs').f_flag & os.ST_RDONLY)
        forbidden = workspace / 'forbidden'; forbidden.mkdir()
        libc = mounts._libc()
        denied = libc.mount(b'tmpfs', os.fsencode(forbidden), b'tmpfs', 0, None) != 0
        if not denied:
            libc.umount2(os.fsencode(forbidden), 0)
        require(denied, 'candidate_arbitrary_mount_not_denied')
        launch = CONTROL / 'preflight-launch.json'
        atomic(launch, dict(controller_pid=os.getpid(), max_processes=64, cgroup=None, uid=uid, gid=uid,
            denied_paths=[str(private), str(BOOTSTRAP)], cwd=str(workspace),
            command=[sys.executable, '-c', CHILD, str(workspace), str(private)]))
        from agent_service.runtime import launcher
        process = subprocess.Popen([sys.executable, str(Path(launcher.__file__)), str(launch)],
            cwd=workspace, env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(workspace)},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        end = time.monotonic() + 10
        observed = None
        while time.monotonic() < end:
            require(process.poll() is None, 'candidate_privilege_launcher_failed')
            try:
                observed = json.loads((workspace / 'observation.json').read_bytes())
                break
            except (FileNotFoundError, ValueError):
                pass
            time.sleep(.02)
        require(observed is not None, 'candidate_privilege_launcher_failed')
        require(observed['uid'] == [uid] * 3 and observed['groups'] == []
            and all(observed[key] is True for key in ('control_unreadable', 'input_write_denied', 'caps_empty', 'no_new_privileges')))
        end = time.monotonic() + 3
        while time.monotonic() < end and not (workspace / 'detached-heartbeat').exists():
            time.sleep(.02)
        require(observed['detached_pid'] in stopper._uid_processes() and
                os.getpgid(observed['detached_pid']) == observed['detached_pid'])
        stopper.stop(identity)
        require(stopper.prove_stopped(identity).all_writers_stopped and not stopper._uid_processes())
        heartbeat = (workspace / 'detached-heartbeat').read_bytes()
        time.sleep(.1)
        require(heartbeat == (workspace / 'detached-heartbeat').read_bytes())
        observed.pop('detached_pid')
        return dict(**observed, controller_confinement=confinement,
                    read_only_mount_measured=True, arbitrary_mount_denied=True,
                    detached_writers_stopped=True, private_kernel_scope=True)
    finally:
        stopper.stop(identity)
        if process is not None:
            process.wait(timeout=3)
        mounts.close()
        source.cleanup()


def build_candidate_driver(config, credentials, scope, stopper, observations):
    """Explicit admin-only factory using measured preflights, no acceptance file."""
    from agent_service.domain_runtime import DomainExecutionProfile
    from agent_service.resources import measured_package
    config, policy, binding = candidate_config(config.model_dump(mode='json'))
    require(not policy.enabled and observations['model_round_trip']['status'] == 200
        and observations['isolation']['detached_writers_stopped'] is True
        and observations['isolation']['read_only_mount_measured'] is True)
    root, manifest, checksum, skills, hashes = measured_package(config, policy, CONTROL)
    if config.engine == 'hermes':
        from agent_service.hermes_host import HermesHostDriver, HERMES
        require(os.path.isfile(HERMES) and os.access(HERMES, os.X_OK))
        class CandidateHermesDriver(HermesHostDriver):
            @property
            def ready(self):
                return not stopper.consumed and time.time() < credentials.model_proxy_expires_at
        driver = CandidateHermesDriver(config, credentials, scope, stopper, CONTROL, root, skills)
        observations['capability'] = dict(engine='hermes', executable_measured=True, package_measured=True,
            allowed_tools=list(driver.impl.required_tools))
        return driver
    from agent_service.runtime import PiRuntime
    from agent_service.worker_entry import NODE, PI_CLI
    from agent_service.pi_cage import ACADEMIC_EXTENSION
    from agent_service.engines import PiDriver
    extensions = ((Path(ACADEMIC_EXTENSION),)
                  if policy.name in {'research.generate.v1', 'research.retrieve.v1'} else ())
    profile = DomainExecutionProfile(policy, policy.execution_binding.bundle_revision, extensions=extensions)
    profile.agent_uid, profile.preview_uid, profile.control_root = scope.agent_uid, scope.preview_uid, CONTROL
    settings = build_pi_runtime_settings(policy, command=(NODE, PI_CLI), node_command=(NODE,), provider=config.model.provider,
        model=config.model.model, model_base_url=config.model.proxy_base_url, model_api=config.model.api,
        model_max_tokens=8192,
        model_input=('text', 'image') if 'vision' in binding.model_policy.capabilities else ('text',),
        model_env={'AGENT_MODEL_API_KEY': credentials.model_proxy_token.get_secret_value()},
        tool_proxy_base_url=config.model.proxy_base_url, runtime_revision=policy.runtime_revision,
        skill_bundle_revision=policy.execution_binding.bundle_revision, image_digest=policy.image_digest,
        skill_paths=skills, skill_hashes=hashes, bundle_manifest=manifest,
        bundle_manifest_sha256=checksum, bundle_root=root, control_dir=CONTROL,
        run_uid_start=scope.agent_uid, run_gid=scope.agent_uid, denied_paths=(BOOTSTRAP, CONTROL / 'private-sentinel'),
        compact_events=True)
    preliminary = PiRuntime(settings, profile=profile)
    preliminary._verify_resources()
    pi_version, node_version = preliminary._readiness_versions()
    observations['capability'] = dict(engine='pi', pi_version=pi_version, node_version=node_version,
        package_measured=True, allowed_tools=list(profile.impl.required_tools),
        **preliminary._source_provenance())
    # These prerequisite booleans are derived from this actual probe, not from a
    # manufactured production approval or a user-supplied all-true record.
    runtime = PiRuntime(replace(settings, capability_verified=bool(pi_version and node_version),
        model_verified=observations['model_round_trip']['status'] == 200,
        isolation_verified=all(observations['isolation'].get(key) is True for key in
            ('control_unreadable', 'input_write_denied', 'caps_empty', 'no_new_privileges',
             'read_only_mount_measured', 'arbitrary_mount_denied', 'detached_writers_stopped', 'private_kernel_scope'))), profile=profile)
    def wire(request):
        require(request.execution_id == scope.attempt_id, 'candidate_scope_mismatch')
        require(not stopper.consumed, 'candidate_scope_consumed')
        require(time.time() < credentials.model_proxy_expires_at, 'candidate_grant_expired')
        require(request.deadline_at.timestamp() <= credentials.model_proxy_expires_at,
            'candidate_request_deadline_mismatch')
        return {**profile.make_wire(request), '_runtime_uid': scope.agent_uid}
    driver = PiDriver(runtime, stopper, wire_builder=wire, enabled=True)
    if policy.name in {'research.generate.v1', 'research.retrieve.v1'}:
        driver.academic_observer = lambda workspace: __import__(
            'agent_service.tool_client', fromlist=['save_academic_observations']).save_academic_observations(
                config.model.proxy_base_url, credentials.model_proxy_token.get_secret_value(), workspace)
    return driver


def response_completed(response):
    try:
        value = response.json()
        texts = [part['text'] for item in value['output'] if item.get('type') == 'message'
                 for part in item.get('content', []) if part.get('type') == 'output_text']
        return (response.status_code == 200 and value.get('status') == 'completed'
                and ''.join(texts).strip() == 'CANDIDATE_MODEL_OK')
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


def render_probe(config, *, name, worker_image, node, uid_start, bootstrap_secret,
                 upstream_secret, upstream_key='token', scenario=None, source_dir=None, parent_evidence_dir=None):
    """Public one-shot manifests only; never reads or emits any Secret value."""
    config, policy, _ = candidate_config(config.model_dump(mode='json'))
    prepared = None
    if scenario is not None:
        require(source_dir is not None)
        from agent_service.deploy.candidate_scenario import load_scenario
        prepared = load_scenario(scenario, source_dir, parent_evidence_dir, policy)
        prepared.reserve_uid(uid_start)
    else:
        require(source_dir is None and parent_evidence_dir is None)
        # Preserve legacy manifest-only rendering. Its runtime still rejects
        # unsupported built-in fixtures; new video candidates require their
        # reviewed immutable input before any manifest is produced.
        require(not policy.name.startswith('video.'), 'candidate_scenario_required')
    resource_name = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?\Z')
    require(all(type(value) is str and resource_name.fullmatch(value)
                for value in (name, bootstrap_secret, upstream_secret)))
    require(type(node) is str and re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}', node))
    require(type(uid_start) is int and 65536 <= uid_start <= 2**31 - 4)
    from agent_service.catalog import IMAGE
    require(IMAGE.fullmatch(worker_image) and worker_image.endswith('@' + policy.image_digest)
        and policy.image_digest != 'sha256:' + '0' * 64)
    require(type(upstream_key) is str and re.fullmatch(r'[A-Za-z0-9_.-]{1,253}', upstream_key))
    origin = 'http://' + name + '.agent-service.svc.cluster.local:8080'
    # A proxy URL is transport wiring, outside the immutable execution binding.
    value = config.model_dump(mode='json')
    value['model']['proxy_base_url'] = origin + '/v1'
    config, _, _ = candidate_config(value)
    code = Path(__file__).read_text(encoding='utf-8')
    script_hash = harness_sha256()
    worker_name = name + '-worker'
    identity = {'agent-service.panghuer.top/candidate-probe': name}
    annotations = {'agent-service.panghuer.top/harness-sha256': script_hash}
    items = []

    def obj(kind, namespace, object_name, **fields):
        return dict(apiVersion='networking.k8s.io/v1' if kind == 'NetworkPolicy' else 'v1', kind=kind,
            metadata=dict(name=object_name, namespace=namespace, labels=identity, annotations=annotations), **fields)

    probe_data = {'acceptance_probe.py': code, 'config.json': canonical_bytes(value).decode('utf-8')}
    if prepared is not None:
        from agent_service.deploy import candidate_scenario
        probe_data['candidate_scenario.py'] = Path(candidate_scenario.__file__).read_text(encoding='utf-8')
        probe_data['scenario.json'] = canonical_bytes(prepared.document).decode('utf-8')
        annotations['agent-service.panghuer.top/scenario-harness-sha256'] = candidate_scenario.module_sha256()
    for namespace in ('agent-service', 'agent-workers'):
        items.append(obj('ConfigMap', namespace, name, immutable=True,
            data=probe_data))
    # This unique selector never overlaps the production model-proxy Service.
    proxy_labels = {**identity, 'app': 'agent-model-proxy', 'llm-client': 'true',
                    'app.kubernetes.io/name': 'agent-candidate-probe', 'component': 'candidate-proxy'}
    worker_labels = {**identity, 'app': 'agent-capsule'}
    items.append(obj('Service', 'agent-service', name,
        spec=dict(selector=identity, ports=[dict(name='http', port=8080, targetPort=8080)])))
    dns = dict(namespaceSelector={'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}},
               podSelector={'matchLabels': {'k8s-app': 'kube-dns'}})
    items.append(obj('NetworkPolicy', 'agent-service', name, spec=dict(
        podSelector={'matchLabels': identity}, policyTypes=['Ingress', 'Egress'],
        ingress=[{'from': [dict(namespaceSelector={'matchLabels': {'kubernetes.io/metadata.name': 'agent-workers'}},
            podSelector={'matchLabels': worker_labels})], 'ports': [dict(protocol='TCP', port=8080)]}],
        egress=[{'to': [dns], 'ports': [dict(protocol='UDP', port=53), dict(protocol='TCP', port=53)]},
            {'to': [dict(namespaceSelector={'matchLabels': {'kubernetes.io/metadata.name': 'llm'}},
                podSelector={'matchLabels': {'app': 'llm-service'}})],
             'ports': [dict(protocol='TCP', port=80), dict(protocol='TCP', port=8000)]}])))
    if policy.name in {'research.generate.v1', 'research.retrieve.v1'}:
        # Same public HTTPS boundary as the production academic proxy. The
        # native Agent still has only its task-proxy network access; fixed
        # academic bridge code controls destinations, redirects and payloads.
        items[-1]['spec']['egress'].append({'to': [{'ipBlock': {'cidr': '0.0.0.0/0',
            'except': ['0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8',
                '169.254.0.0/16', '172.16.0.0/12', '192.168.0.0/16', '198.18.0.0/15',
                '224.0.0.0/4', '240.0.0.0/4']}}], 'ports': [{'protocol': 'TCP', 'port': 443}]})
    base_env = [{'name': key, 'value': setting} for key, setting in
        {'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1', 'PYTHONPATH': '/opt/agent-service',
         'HOME': str(CONTROL)}.items()]
    config_volume = dict(name='probe', configMap=dict(name=name, defaultMode=0o444))
    bootstrap_volume = dict(name='bootstrap', secret=dict(secretName=bootstrap_secret, defaultMode=0o400,
        items=[dict(key='token', path='token')]))
    probe_mounts = [dict(name='probe', mountPath='/probe/acceptance_probe.py',
                        subPath='acceptance_probe.py', readOnly=True),
                    dict(name='probe', mountPath='/probe/config.json', subPath='config.json', readOnly=True)]
    if prepared is not None:
        probe_mounts.append(dict(name='probe',
            mountPath='/opt/agent-service/agent_service/deploy/candidate_scenario.py',
            subPath='candidate_scenario.py', readOnly=True))
    bootstrap_mount = dict(name='bootstrap', mountPath=str(BOOTSTRAP), subPath='token', readOnly=True)
    writable = [dict(name='control', emptyDir=dict(sizeLimit='2Gi')),
                dict(name='tmp', emptyDir=dict(medium='Memory', sizeLimit='128Mi'))]
    mounts = [*probe_mounts, bootstrap_mount, dict(name='control', mountPath=str(CONTROL)),
              dict(name='tmp', mountPath='/tmp')]
    security = dict(runAsUser=0, runAsGroup=0, readOnlyRootFilesystem=True,
        allowPrivilegeEscalation=False, capabilities=dict(drop=['ALL']))
    base_spec = dict(automountServiceAccountToken=False, restartPolicy='Never', enableServiceLinks=False,
        hostPID=False, hostIPC=False, hostNetwork=False, shareProcessNamespace=False,
        terminationGracePeriodSeconds=30, securityContext=dict(seccompProfile=dict(type='RuntimeDefault')))
    command = ['/opt/agent-service/.venv/bin/python', '/probe/acceptance_probe.py']
    scenario_args, scenario_volumes, scenario_mounts = [], [], []
    if prepared is not None:
        scenario_args = ['--scenario', '/probe/scenario.json', '--source-dir', '/run/agent-probe-sources']
        scenario_mounts = [dict(name='probe', mountPath='/probe/scenario.json', subPath='scenario.json', readOnly=True),
            dict(name='scenario-sources', mountPath='/run/agent-probe-sources', readOnly=True)]
        scenario_volumes = [dict(name='scenario-sources', hostPath=dict(path=str(prepared.source_dir), type='Directory'))]
        if prepared.parent_dir is not None:
            scenario_args += ['--parent-evidence-dir', '/run/agent-probe-parent']
            scenario_mounts.append(dict(name='scenario-parent', mountPath='/run/agent-probe-parent', readOnly=True))
            scenario_volumes.append(dict(name='scenario-parent', hostPath=dict(path=str(prepared.parent_dir), type='Directory')))
    # Use the exact fixed Worker image for both Pods, so the proxy also has the
    # same neutral profile validators; no native Agent runs in the proxy.
    items.append(obj('Pod', 'agent-service', name + '-proxy', spec={**base_spec,
        'activeDeadlineSeconds': 1200,
        'containers': [dict(name='proxy', image=worker_image, imagePullPolicy='IfNotPresent',
            command=command, args=['proxy', '--config', '/probe/config.json', '--worker-name', worker_name,
                '--uid-start', str(uid_start), *scenario_args], env=base_env, securityContext=security,
            resources=dict(requests=dict(cpu='50m', memory='128Mi'), limits=dict(cpu='1', memory='1Gi')),
            ports=[dict(name='http', containerPort=8080)],
            volumeMounts=mounts + scenario_mounts + [dict(name='upstream', mountPath=str(UPSTREAM), subPath='token', readOnly=True)])],
        'volumes': [config_volume, bootstrap_volume, *writable, *scenario_volumes, dict(name='upstream',
            secret=dict(secretName=upstream_secret, defaultMode=0o400,
                        items=[dict(key=upstream_key, path='token')]))]}))
    items[-1]['metadata']['labels'] = proxy_labels
    if prepared is not None:
        items[-1]['spec']['nodeSelector'] = {'kubernetes.io/hostname': node}
    worker_security = {**security, 'capabilities': dict(drop=['ALL'],
        add=['CHOWN', 'DAC_OVERRIDE', 'KILL', 'SETUID', 'SETGID', 'SYS_ADMIN']),
        'appArmorProfile': dict(type='Localhost', localhostProfile='agent-capsule-inputs')}
    items.append(obj('Pod', 'agent-workers', worker_name, spec={**base_spec,
        'activeDeadlineSeconds': 900, 'nodeSelector': {'kubernetes.io/hostname': node},
        'containers': [dict(name='worker', image=worker_image, imagePullPolicy='IfNotPresent',
            command=command, args=['worker', '--proxy-origin', origin],
            env=base_env + [{'name': 'AGENT_POD_UID', 'valueFrom': {'fieldRef': {'fieldPath': 'metadata.uid'}}},
                {'name': 'AGENT_READ_ONLY_MOUNTS', 'value': '1'}], securityContext=worker_security,
            resources=dict(requests=dict(cpu='100m', memory='256Mi'),
                           limits=dict(cpu='1', memory='2Gi', **{'ephemeral-storage': '4Gi'})),
            volumeMounts=mounts + [dict(name='work', mountPath=str(WORK)),
                # The native Hermes launcher checks this protected sentinel.
                dict(name='bootstrap', mountPath='/run/agent-secrets/worker.json', subPath='token', readOnly=True)])],
        'volumes': [config_volume, bootstrap_volume, *writable, dict(name='work', emptyDir=dict(sizeLimit='1Gi'))]}))
    items[-1]['metadata']['labels'] = worker_labels
    return dict(apiVersion='v1', kind='List', items=items)


def run_worker(origin):
    import httpx
    from agent_service.linux_stop import LinuxUidStopController
    from agent_service.stop_group import StopGroup
    from agent_service.worker_api import LeaseRequest, WorkerHost
    from urllib.parse import urlsplit
    parsed = urlsplit(origin)
    require(parsed.scheme == 'http' and (parsed.hostname or '').endswith('.agent-service.svc.cluster.local')
        and parsed.port == 8080 and parsed.path == '' and not parsed.query and not parsed.fragment
        and not parsed.username and not parsed.password)
    ensure_controller_confinement(read_only_mounts=True)
    admin = credential(BOOTSTRAP)
    client = httpx.Client(trust_env=False, follow_redirects=False, timeout=30)
    headers = {'Authorization': 'Bearer ' + admin}
    pod_uid = os.environ['AGENT_POD_UID']
    end = time.monotonic() + 120
    while True:
        try:
            response = client.post(origin + '/probe/register', headers=headers, json={'pod_uid': pod_uid})
            response.raise_for_status()
            package = response.json()
            break
        except httpx.TransportError:
            require(time.monotonic() < end, 'candidate_proxy_unavailable')
            time.sleep(.5)
    config, policy, _ = candidate_config(package['config'])
    require(config.model.proxy_base_url == origin + '/v1')
    scope, envelope = CapsuleScope.model_validate(package['scope']), ExecutionEnvelope.model_validate(package['envelope'])
    credentials = HostSecrets.model_validate(package['secrets'])
    require(scope.pod_uid == pod_uid and scope.request_sha256 == envelope.request_sha256
        and scope.execution_binding_sha256 == envelope.execution_binding.sha256
        and package.get('harness_sha256') == harness_sha256())
    observations = {'harness_sha256': harness_sha256(), 'isolation': isolation_preflight(scope)}
    if package.get('scenario') is not None:
        from agent_service.deploy.candidate_scenario import module_sha256
        require(package['scenario'].get('scenario_harness_sha256') == module_sha256(), 'candidate_harness_mismatch')
        observations['scenario'] = package['scenario']
    model_body = dict(model=config.model.model, store=False, max_output_tokens=2048,
        input='Return the exact text CANDIDATE_MODEL_OK as plain text. This is a connectivity probe.')
    model_headers = {'Authorization': 'Bearer ' + credentials.model_proxy_token.get_secret_value()}
    response = client.post(origin + '/v1/responses', headers=model_headers, json=model_body, timeout=120)
    require(response_completed(response), 'candidate_model_round_trip_failed')
    observations['model_round_trip'] = dict(status=response.status_code,
        response_sha256=hashlib.sha256(response.content).hexdigest(), api_mode='responses')
    stopper = StopGroup(*[LinuxUidStopController(CONTROL / name, scope.attempt_id, scope.pod_uid,
        scope.request_sha256, agent_uid=uid) for name, uid in
        (('engine-scope', scope.agent_uid), ('preview-scope', scope.preview_uid))])
    stopper.initialize()
    driver = build_candidate_driver(config, credentials, scope, stopper, observations)
    host = WorkerHost(replace(policy, enabled=True), driver, CONTROL, WORK,
                      bytes.fromhex(credentials.receipt_key_hex.get_secret_value()))
    host.initialize()
    try:
        # This local WorkerHost admission is confined to the administrative
        # process. The operator registry and all production routes stay disabled.
        host.start(envelope)
        renewed = time.monotonic()
        while host.status(envelope.attempt_id)['state'] not in {'completed', 'failed', 'cancelled', 'timed_out', 'quarantined', 'needs_input'}:
            if time.monotonic() - renewed >= 5:
                response = client.post(origin + '/probe/lease', headers=headers, json={})
                response.raise_for_status()
                host.renew(envelope.attempt_id, LeaseRequest(fence=envelope.fence, **response.json()))
                renewed = time.monotonic()
            time.sleep(.1)
        result = host.result(envelope.attempt_id)
        require(len(canonical_bytes({'result': result, 'observations': observations})) <= MAX_WIRE,
            'candidate_probe_scale_exceeded')
        response = client.post(origin + '/probe/result', headers=headers,
            json={'result': result, 'observations': observations}, timeout=45)
        response.raise_for_status()
        revoked = client.post(origin + '/v1/responses', headers=model_headers, json=model_body)
        require(revoked.status_code == 401, 'candidate_expired_grant_not_rejected')
        print(json.dumps(dict(status='candidate_probe_finished', engine=config.engine, profile=policy.name,
            result_state=result['state'], lease_expired_http_status=revoked.status_code,
            production_acceptance_created=False, pod_uid=pod_uid)), flush=True)
        return 0 if result['state'] == 'completed' else 2
    finally:
        host.close()
        stopper.stop(scope.attempt_id)
        client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    proxy = sub.add_parser('proxy')
    proxy.add_argument('--config', type=Path, required=True)
    proxy.add_argument('--state-dir', type=Path, default=CONTROL / 'candidate-probe')
    proxy.add_argument('--worker-name', required=True)
    proxy.add_argument('--worker-namespace', default='agent-workers')
    proxy.add_argument('--uid-start', type=int, required=True,
        help='Three never-reused administrator-reserved UIDs, separate from production ranges')
    proxy.add_argument('--scenario', type=Path)
    proxy.add_argument('--source-dir', type=Path)
    proxy.add_argument('--parent-evidence-dir', type=Path)
    worker = sub.add_parser('worker')
    worker.add_argument('--proxy-origin', required=True)
    render = sub.add_parser('render', help='Only public one-shot objects; creates no Secrets or production approvals')
    render.add_argument('--config', type=Path, required=True)
    render.add_argument('--name', required=True)
    render.add_argument('--worker-image', required=True)
    render.add_argument('--node', required=True)
    render.add_argument('--uid-start', type=int, required=True)
    render.add_argument('--bootstrap-secret', required=True)
    render.add_argument('--upstream-secret', required=True)
    render.add_argument('--upstream-key', default='token')
    render.add_argument('--output', type=Path, required=True)
    render.add_argument('--scenario', type=Path)
    render.add_argument('--source-dir', type=Path)
    render.add_argument('--parent-evidence-dir', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.mode == 'worker':
            return run_worker(args.proxy_origin)
        if args.mode == 'render':
            config = HostConfig.model_validate(json.loads(args.config.read_bytes()))
            manifest = render_probe(config, name=args.name, worker_image=args.worker_image,
                node=args.node, uid_start=args.uid_start, bootstrap_secret=args.bootstrap_secret,
                upstream_secret=args.upstream_secret, upstream_key=args.upstream_key,
                scenario=args.scenario, source_dir=args.source_dir, parent_evidence_dir=args.parent_evidence_dir)
            atomic(args.output, manifest)
            print(json.dumps({'status': 'candidate_manifest_rendered', 'harness_sha256': harness_sha256(),
                'production_acceptance_created': False, 'secret_values_emitted': False}), flush=True)
            return 0
        config = HostConfig.model_validate(_read_operator_json(args.config, max_bytes=4 * 1024 * 1024))
        scenario = None
        if args.scenario is not None:
            # Host paths are accepted only by the administrator render CLI.
            # The runtime proxy sees fixed read-only mount destinations.
            require(args.scenario == Path('/probe/scenario.json')
                and args.source_dir == Path('/run/agent-probe-sources')
                and args.parent_evidence_dir in (None, Path('/run/agent-probe-parent')))
            from agent_service.deploy.candidate_scenario import load_scenario
            scenario = load_scenario(args.scenario, args.source_dir, args.parent_evidence_dir, host_policy(config))
        else:
            require(args.source_dir is None and args.parent_evidence_dir is None)
        authority = ProbeAuthority(config, args.state_dir, credential(UPSTREAM), credential(BOOTSTRAP),
            args.worker_name, args.worker_namespace, args.uid_start, scenario=scenario)
        import uvicorn
        from agent_service.__main__ import _logging_configuration
        uvicorn.run(proxy_app(authority), host='0.0.0.0', port=8080, access_log=False,
            proxy_headers=False, server_header=False, log_config=_logging_configuration())
        return 0
    except Exception as error:
        # HTTP/native/model exceptions and package dictionaries may carry keys.
        code = error.code if isinstance(error, ServiceError) else 'candidate_probe_failed'
        print(json.dumps({'status': 'candidate_probe_failed', 'code': code,
            'error_type': type(error).__name__, 'production_acceptance_created': False}), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
