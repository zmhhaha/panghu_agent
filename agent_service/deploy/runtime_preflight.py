"""Administrator-only runtime prerequisites; never executes a domain task.

The isolated Store below authorizes one fixed model echo. Its explicit admin
task is never admitted to a domain WorkerHost or production API. Package/version
inspection, kernel isolation and grant expiry are real measurements, separate
from domain workflow verification and any operator decision to enable a route.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from agent_service.contracts import (
    ExecutionEnvelope, JobRequest, ServiceError, canonical_bytes, digest,
)
from agent_service.deploy import acceptance_probe as base
from agent_service.worker_entry import CapsuleScope, HostConfig, HostSecrets, _read_operator_json


SCHEMA = 'agent-runtime-preflight.v1'
TASK_SCHEMA = 'administrator-runtime-preflight.v1'
OUTPUT_SCHEMA = 'administrator-runtime-preflight-evidence.v1'
PROFILES = frozenset('ppt.' + phase + '.v1' for phase in (
    'plan_initial', 'plan_options', 'prepare', 'generate', 'revise'))
TENANT, CALLER = 'runtime-preflight', 'administrator'
MAX_CONTROL_BYTES = 65536
ISOLATION = ('control_unreadable', 'input_write_denied', 'caps_empty', 'no_new_privileges',
             'read_only_mount_measured', 'arbitrary_mount_denied',
             'detached_writers_stopped', 'private_kernel_scope')


def require(value, code='runtime_preflight_invalid'):
    if not value:
        raise ServiceError(code, 422)


def harness_sha256():
    # render_probe projects canonical LF text, including on Windows.
    return hashlib.sha256(Path(__file__).read_text(encoding='utf-8').encode()).hexdigest()


def preflight_config(value):
    config, policy, binding = base.candidate_config(value)
    require(config.engine == 'pi' and policy.name in PROFILES,
            'disabled_ppt_pi_binding_required')
    return config, policy, binding


def echo_body(model):
    return dict(model=model, store=False, max_output_tokens=2048,
        input='Return the exact text CANDIDATE_MODEL_OK as plain text. This is a connectivity probe.')


def admin_pins(policy):
    return {**policy.pins(), 'task_schema': TASK_SCHEMA, 'output_schema': OUTPUT_SCHEMA}


class RuntimePreflightAuthority:
    """One isolated admin allocation and fixed echo, with no business fixtures."""

    def __init__(self, config, directory, upstream_token, bootstrap,
                 worker_name, worker_namespace, uid):
        from agent_service.model_proxy import ModelProxy, ModelProxySettings, requests
        from agent_service.store import Store

        self.config, self.policy, self.binding = preflight_config(config.model_dump(mode='json'))
        require(type(uid) is int and 65536 <= uid <= 2**31 - 4)
        self.bootstrap, self.uid = bootstrap, uid
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        require(not self.directory.is_symlink(), 'ordinary_preflight_directory_required')
        self.directory.chmod(0o700)
        # An ambiguous prior inference must not be reset by another coordinator.
        base.atomic(self.directory / 'once.json', dict(mode=TASK_SCHEMA,
            created_at=time.time(), harness_sha256=harness_sha256()))
        self.store = Store('sqlite:///' + str(self.directory / 'runtime-preflight.sqlite3'), allow_sqlite=True)
        self.store.initialize()
        self.proxy = ModelProxy(self.store, ModelProxySettings(secrets.token_bytes(48),
            'http://llm-service.llm.svc.cluster.local/v1', upstream_token,
            max_calls_per_attempt=1, max_output_tokens=2048))
        self.request_table = requests
        task = dict(schema_version=TASK_SCHEMA, target_profile=self.policy.name,
            target_profile_revision=self.policy.revision,
            target_combination_sha256=self.policy.execution_binding.combination_sha256,
            domain_execution_performed=False, business_workflow_verified=False)
        request = JobRequest(profile=self.policy.name, profile_revision=self.policy.revision,
            business_ref='administrator-runtime-preflight', task=task, inputs=[], timeout_seconds=600)
        # The binding identifies the measured target. The transport task/schema
        # are explicitly administrative; no PPT confirmations or base are made.
        pins = admin_pins(self.policy)
        self.store.submit(TENANT, CALLER, 'single-runtime-preflight', request.model_dump(mode='json'), pins)
        claimed = self.store.claim(lease_seconds=300, provisioning={'pi': 'kubernetes_per_attempt'})
        with self.store.engine.connect() as connection:
            self.envelope = self.store._worker_envelope(connection, claimed)
        require(self.envelope.task_schema == TASK_SCHEMA and not self.envelope.inputs
                and not self.envelope.checkpoint_inputs and self.envelope.checkpoint_ref is None)
        base.atomic(self.directory / 'runtime-preflight-request.json', dict(
            schema_version='administrator-runtime-preflight-request.v1',
            request_kind='runtime_prerequisites_only', domain_execution_performed=False,
            business_workflow_verified=False, envelope=self.envelope.model_dump(mode='json')))
        self.store.reserve_worker(self.envelope, TENANT, worker_namespace, worker_name,
            digest(dict(mode=TASK_SCHEMA, config=self.config.model_dump(mode='json'),
                worker_name=worker_name, worker_namespace=worker_namespace, uid=uid)),
            agent_uid=uid, aux_uid=uid + 1)
        require(self.store.claim_worker_create(self.envelope.attempt_id, self.envelope.request_sha256))
        self.grant = self.proxy.issuer.issue(self.envelope, self.binding.model_policy)
        self.worker_token, self.receipt_key = secrets.token_hex(24), secrets.token_bytes(32)
        self.registered = self.recorded = self.pending = None
        self.lock = threading.RLock()

    def register(self, pod_uid):
        try:
            require(type(pod_uid) is str and str(UUID(pod_uid)) == pod_uid)
        except (ValueError, TypeError, AttributeError):
            raise ServiceError('runtime_preflight_pod_identity_invalid', 422) from None
        with self.lock:
            require(self.pending is None and self.recorded is None, 'runtime_preflight_finished')
            if self.registered is None:
                self.store.bind_worker(self.envelope.attempt_id, self.envelope.request_sha256, pod_uid)
                require(self.store.claim_worker_start(self.envelope.attempt_id, self.envelope.request_sha256))
                require(self.store.mark_running(self.envelope.job_id, self.envelope.attempt_id, self.envelope.fence))
                self.registered = pod_uid
            require(self.registered == pod_uid, 'runtime_preflight_pod_conflict')
            return dict(harness_sha256=harness_sha256(), base_harness_sha256=base.harness_sha256(),
                config=self.config.model_dump(mode='json'), envelope=self.envelope.model_dump(mode='json'),
                scope=dict(attempt_id=self.envelope.attempt_id, pod_uid=pod_uid,
                    request_sha256=self.envelope.request_sha256, agent_uid=self.uid, preview_uid=self.uid + 1,
                    execution_binding_sha256=self.envelope.execution_binding.sha256),
                secrets=dict(worker_token=self.worker_token, receipt_key_hex=self.receipt_key.hex(),
                    model_proxy_token=self.grant, model_proxy_attempt_id=self.envelope.attempt_id,
                    model_proxy_request_sha256=self.envelope.request_sha256,
                    model_proxy_expires_at=self.envelope.deadline_at))

    def lease(self):
        with self.lock:
            require(self.pending is None and self.recorded is None, 'runtime_preflight_finished')
            require(self.store.renew(self.envelope.job_id, self.envelope.attempt_id,
                                    self.envelope.fence, lease_seconds=300))
            return {'lease_expires_at': self.store.get_job(TENANT, CALLER,
                                                         self.envelope.job_id)['lease_expires_at']}

    def _model_measurement(self, observation):
        from sqlalchemy import select
        import httpx
        with self.store.engine.connect() as connection:
            rows = list(connection.execute(select(self.request_table).where(
                self.request_table.c.attempt_id == self.envelope.attempt_id)).mappings())
        require(len(rows) == 1, 'one_fixed_echo_required')
        row = rows[0]
        require(row['state'] == 'complete' and row['status'] == 200
                and row['media_type'] == 'application/json' and row['response']
                and row['body_sha256'] == digest(echo_body(self.config.model.model)),
                'fixed_echo_ledger_mismatch')
        response = httpx.Response(200, content=row['response'])
        require(base.response_completed(response)
                and not any(item.get('type') == 'function_call' for item in response.json()['output']),
                'fixed_echo_response_invalid')
        response_hash = hashlib.sha256(row['response']).hexdigest()
        require(observation == dict(status=200, api_mode='responses', response_sha256=response_hash),
                'fixed_echo_observation_mismatch')
        return [dict(body_sha256=row['body_sha256'], state='complete', status=200,
                     media_type=row['media_type'], response_sha256=response_hash)]

    def result(self, document):
        """Record runtime observations only; never stage a domain WorkerResult."""
        with self.lock:
            require(self.registered is not None and self.pending is None and self.recorded is None,
                    'runtime_preflight_finished')
            require(type(document) is dict and set(document) == {'observations', 'stop_evidence'})
            observations, stopped = document['observations'], document['stop_evidence']
            require(type(observations) is dict and set(observations) == {
                'harness_sha256', 'base_harness_sha256', 'capability', 'isolation', 'model_round_trip'}
                and observations['harness_sha256'] == harness_sha256()
                and observations['base_harness_sha256'] == base.harness_sha256(),
                'runtime_preflight_harness_mismatch')
            from agent_service.pi_cage import PI_CAGE_REPOSITORY, PI_CAGE_REVISION, PI_VERSION
            from agent_service.profiles import implementation
            capability, isolation = observations['capability'], observations['isolation']
            require(type(capability) is dict and capability.get('engine') == 'pi'
                and capability.get('pi_version') == PI_VERSION
                and capability.get('pi_cage_repository') == PI_CAGE_REPOSITORY
                and capability.get('pi_cage_revision') == PI_CAGE_REVISION
                and type(capability.get('node_version')) is str
                and capability.get('package_measured') is True
                and capability.get('allowed_tools') == list(implementation(self.policy.name).required_tools),
                'runtime_preflight_capability_unverified')
            require(type(isolation) is dict and all(isolation.get(key) is True for key in ISOLATION)
                and isolation.get('uid') == [self.uid + 2] * 3 and isolation.get('groups') == [],
                'runtime_preflight_isolation_unverified')
            require(type(stopped) is dict and set(stopped) == {
                'all_writers_stopped', 'resource_ids', 'observed_at'}
                and stopped['all_writers_stopped'] is True
                and type(stopped['observed_at']) in (int, float)
                and math.isfinite(stopped['observed_at'])
                and 0 < stopped['observed_at'] <= time.time() + 5,
                'runtime_preflight_stop_unverified')
            ids = stopped['resource_ids']
            required = {'pod:' + self.registered, 'uid:' + str(self.uid),
                        'uid:' + str(self.uid + 1), 'attempt:' + self.envelope.attempt_id}
            require(type(ids) is list and len(ids) == 5 and all(type(item) is str for item in ids)
                and len(set(ids)) == len(ids) and required < set(ids)
                and len([item for item in ids if re.fullmatch(r'pid:\[[1-9][0-9]*\]', item)]) == 1,
                'runtime_preflight_stop_scope_mismatch')
            calls = self._model_measurement(observations['model_round_trip'])
            base.atomic(self.directory / 'runtime-preflight-observations.json', document)
            # Real wall-clock expiry, with no model request and no fake clock.
            require(self.store.renew(self.envelope.job_id, self.envelope.attempt_id,
                                    self.envelope.fence, lease_seconds=1))
            time.sleep(1.1)
            rejected = False
            try:
                self.proxy.authorize(self.grant)
            except ServiceError as error:
                rejected = error.code == 'model_grant_revoked' and error.status == 401
            require(rejected, 'runtime_preflight_lease_not_revoked')
            self.pending = dict(schema_version=SCHEMA, scope='profile_runtime_prerequisites_only',
                profile=self.policy.name, profile_revision=self.policy.revision, engine='pi',
                binding_id=self.binding.id, binding_revision=self.binding.revision,
                combination_sha256=self.envelope.execution_binding.combination_sha256,
                execution_binding_sha256=self.envelope.execution_binding.sha256,
                image_digest=self.policy.image_digest, runtime_revision=self.policy.runtime_revision,
                harness_sha256=harness_sha256(), base_harness_sha256=base.harness_sha256(),
                job_id=self.envelope.job_id, attempt_id=self.envelope.attempt_id,
                request_sha256=self.envelope.request_sha256, pod_uid=self.registered,
                target_contract_schema=self.policy.task_schema, admin_task_schema=TASK_SCHEMA,
                observations=observations, model_calls=calls, lease_expiry_rejected=True,
                all_uid_scopes_stopped=True, stop_evidence=stopped,
                checks={key: True for key in ('capability', 'model_round_trip', 'isolation', 'model_proxy_lease')},
                domain_execution_performed=False, business_workflow_verified=False,
                domain_model_requests=0, domain_tool_calls={}, production_acceptance_created=False,
                production_routes_changed=False, publication_authorized=False,
                pod_termination='external_operator_observation_required',
                unverified=['domain_workflow', 'native_domain_model_loop', 'domain_tool_execution',
                    'business_artifacts_and_publication', 'production_http_job_execution',
                    'external_actual_pod_image_and_security_context', 'external_exact_pod_termination'])
            return dict(runtime_observations_received=True, lease_expiry_rejected=True,
                        awaiting_expired_grant_http_rejection=True)

    def record_expired_http(self, token, error):
        """Only a real denial of this exact grant seals the final evidence."""
        with self.lock:
            if (self.pending is not None and self.recorded is None
                    and hmac.compare_digest(token, self.grant)
                    and error.code == 'model_grant_revoked' and error.status == 401):
                value = dict(self.pending, expired_grant_http_status=401)
                base.atomic(self.directory / 'runtime-preflight-evidence.json', value)
                self.recorded = value


def proxy_app(authority):
    """A fixed-echo proxy; no tool, arbitrary prompt, domain or result routes."""
    app = FastAPI()

    @app.exception_handler(ServiceError)
    async def failure(request, error):
        return JSONResponse({'error': {'code': error.code}}, status_code=error.status)

    def token(request):
        header = request.headers.get('authorization', '')
        if not header.startswith('Bearer '):
            raise ServiceError('authentication_required', 401)
        return header[7:]

    async def bounded(request, *, admin=False):
        if admin and not hmac.compare_digest(token(request), authority.bootstrap):
            raise ServiceError('runtime_preflight_admin_authentication_required', 401)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_CONTROL_BYTES:
                raise ServiceError('request_too_large', 413)
        try:
            from agent_service.model_proxy import _request_json
            value = _request_json(bytes(raw))
        except (ValueError, UnicodeError, RecursionError):
            raise ServiceError('invalid_json', 422) from None
        require(type(value) is dict)
        return value

    @app.post('/v1/responses')
    async def responses(request: Request):
        authorization = token(request)
        try:
            authority.proxy.authorize(authorization)
        except ServiceError as error:
            authority.record_expired_http(authorization, error)
            raise
        value = await bounded(request)
        require(canonical_bytes(value) == canonical_bytes(echo_body(authority.config.model.model)),
                'runtime_preflight_fixed_echo_only')
        from starlette.concurrency import run_in_threadpool
        status, media, payload = await run_in_threadpool(authority.proxy.respond, authorization, value)
        return Response(payload, status_code=status, media_type=media,
                        headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})

    @app.post('/preflight/register')
    async def register(request: Request):
        value = await bounded(request, admin=True)
        require(set(value) == {'pod_uid'})
        return authority.register(value['pod_uid'])

    @app.post('/preflight/lease')
    async def lease(request: Request):
        require(await bounded(request, admin=True) == {})
        return authority.lease()

    @app.post('/preflight/result')
    async def result(request: Request):
        value = await bounded(request, admin=True)
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(authority.result, value)

    @app.get('/preflight/evidence')
    def evidence(request: Request):
        if not hmac.compare_digest(token(request), authority.bootstrap):
            raise ServiceError('runtime_preflight_admin_authentication_required', 401)
        return authority.recorded or dict(status='pending', schema_version=SCHEMA,
            domain_execution_performed=False, business_workflow_verified=False,
            production_acceptance_created=False)

    @app.on_event('shutdown')
    def close():
        authority.proxy.client.close()
        authority.store.close()

    return app


def render_preflight(config, **options):
    """Reuse reviewed isolation wiring, projecting this exact new Git script."""
    preflight_config(config.model_dump(mode='json'))
    require(set(options) <= {'name', 'worker_image', 'node', 'uid_start', 'bootstrap_secret',
                             'upstream_secret', 'upstream_key'},
            'runtime_preflight_has_no_business_scenario')
    manifest = base.render_probe(config, **options)
    code = Path(__file__).read_text(encoding='utf-8')
    checksum = harness_sha256()
    for item in manifest['items']:
        item['metadata']['annotations']['agent-service.panghuer.top/runtime-preflight-harness-sha256'] = checksum
        if item['kind'] == 'ConfigMap':
            # Keep the original probe mounted only as a measured dependency.
            item['data']['runtime_preflight.py'] = code
        if item['kind'] == 'Pod':
            container = item['spec']['containers'][0]
            container['command'] = ['/opt/agent-service/.venv/bin/python', '/probe/runtime_preflight.py']
            container['volumeMounts'].append(dict(name='probe', mountPath='/probe/runtime_preflight.py',
                                                   subPath='runtime_preflight.py', readOnly=True))
            # The original helper is also projected at its import path, so its
            # hash is bound to the rendered source even on an older fixed image.
            container['volumeMounts'].append(dict(name='probe',
                mountPath='/opt/agent-service/agent_service/deploy/acceptance_probe.py',
                subPath='acceptance_probe.py', readOnly=True))
    return manifest


def run_worker(origin):
    import httpx
    from agent_service.confinement import ensure_controller_confinement
    from agent_service.linux_stop import LinuxUidStopController
    from agent_service.stop_group import StopGroup

    parsed = urlsplit(origin)
    require(parsed.scheme == 'http' and (parsed.hostname or '').endswith('.agent-service.svc.cluster.local')
        and parsed.port == 8080 and parsed.path == '' and not parsed.query and not parsed.fragment
        and not parsed.username and not parsed.password)
    ensure_controller_confinement(read_only_mounts=True)
    admin = base.credential(base.BOOTSTRAP)
    client = httpx.Client(trust_env=False, follow_redirects=False, timeout=30)
    headers = {'Authorization': 'Bearer ' + admin}
    stopper, scope = None, None
    try:
        pod_uid, end = os.environ['AGENT_POD_UID'], time.monotonic() + 120
        while True:
            try:
                response = client.post(origin + '/preflight/register', headers=headers, json={'pod_uid': pod_uid})
                response.raise_for_status()
                package = response.json()
                break
            except httpx.TransportError:
                require(time.monotonic() < end, 'runtime_preflight_proxy_unavailable')
                time.sleep(.5)
        config, policy, _ = preflight_config(package['config'])
        require(config.model.proxy_base_url == origin + '/v1')
        scope = CapsuleScope.model_validate(package['scope'])
        envelope = ExecutionEnvelope.model_validate(package['envelope'])
        credentials = HostSecrets.model_validate(package['secrets'])
        require(scope.pod_uid == pod_uid and scope.request_sha256 == envelope.request_sha256
            and scope.execution_binding_sha256 == envelope.execution_binding.sha256
            and envelope.task_schema == TASK_SCHEMA and envelope.task.get('schema_version') == TASK_SCHEMA
            and all(envelope.model_dump(mode='json').get(key) == value
                    for key, value in admin_pins(policy).items())
            and not envelope.inputs and not envelope.checkpoint_inputs
            and envelope.checkpoint_ref is None
            and package.get('harness_sha256') == harness_sha256()
            and package.get('base_harness_sha256') == base.harness_sha256())
        observations = dict(harness_sha256=harness_sha256(), base_harness_sha256=base.harness_sha256(),
                            isolation=base.isolation_preflight(scope))
        body = echo_body(config.model.model)
        model_headers = {'Authorization': 'Bearer ' + credentials.model_proxy_token.get_secret_value()}
        # Keep the original absolute deadline and an actively leased attempt.
        response = client.post(origin + '/preflight/lease', headers=headers, json={})
        response.raise_for_status()
        response = client.post(origin + '/v1/responses', headers=model_headers, json=body,
                               timeout=max(.1, min(180., envelope.deadline_at - time.time())))
        require(base.response_completed(response), 'runtime_preflight_model_round_trip_failed')
        observations['model_round_trip'] = dict(status=response.status_code,
            response_sha256=hashlib.sha256(response.content).hexdigest(), api_mode='responses')
        stopper = StopGroup(*[LinuxUidStopController(base.CONTROL / name, scope.attempt_id, scope.pod_uid,
            scope.request_sha256, agent_uid=uid) for name, uid in
            (('engine-scope', scope.agent_uid), ('preview-scope', scope.preview_uid))])
        stopper.initialize()
        # Factory construction measures the actual pinned package, Pi source
        # and executable versions. Never call WorkerHost.start/driver.execute.
        base.build_candidate_driver(config, credentials, scope, stopper, observations)
        stopper.stop(scope.attempt_id)
        proof = stopper.prove_stopped(scope.attempt_id)
        require(proof.all_writers_stopped and not stopper._uid_processes(),
                'runtime_preflight_writers_not_stopped')
        document = dict(observations=observations,
            stop_evidence=dict(all_writers_stopped=True, resource_ids=list(proof.resource_ids),
                               observed_at=time.time()))
        response = client.post(origin + '/preflight/result', headers=headers, json=document, timeout=45)
        response.raise_for_status()
        require(response.json().get('lease_expiry_rejected') is True,
                'runtime_preflight_lease_not_revoked')
        expired = client.post(origin + '/v1/responses', headers=model_headers, json=body)
        require(expired.status_code == 401 and expired.json().get('error', {}).get('code') == 'model_grant_revoked',
                'runtime_preflight_expired_grant_not_rejected')
        response = client.get(origin + '/preflight/evidence', headers=headers)
        response.raise_for_status()
        require(response.json().get('expired_grant_http_status') == 401,
                'runtime_preflight_http_rejection_unrecorded')
        print(json.dumps(dict(status='runtime_preflight_finished', schema_version=SCHEMA,
            profile=policy.name, profile_revision=policy.revision, pod_uid=pod_uid,
            domain_execution_performed=False, business_workflow_verified=False,
            production_acceptance_created=False)), flush=True)
        return 0
    finally:
        try:
            if stopper is not None:
                stopper.stop(scope.attempt_id)
        finally:
            client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    proxy = sub.add_parser('proxy')
    proxy.add_argument('--config', type=Path, required=True)
    proxy.add_argument('--state-dir', type=Path, default=base.CONTROL / 'runtime-preflight')
    proxy.add_argument('--worker-name', required=True)
    proxy.add_argument('--worker-namespace', default='agent-workers')
    proxy.add_argument('--uid-start', type=int, required=True,
                       help='Three fresh reserved UIDs outside production ranges')
    worker = sub.add_parser('worker')
    worker.add_argument('--proxy-origin', required=True)
    render = sub.add_parser('render')
    for name in ('config', 'output'):
        render.add_argument('--' + name, type=Path, required=True)
    for name in ('name', 'worker-image', 'node', 'bootstrap-secret', 'upstream-secret'):
        render.add_argument('--' + name, required=True)
    render.add_argument('--uid-start', type=int, required=True)
    render.add_argument('--upstream-key', default='token')
    args = parser.parse_args(argv)
    try:
        if args.mode == 'worker':
            return run_worker(args.proxy_origin)
        config = HostConfig.model_validate(_read_operator_json(args.config, max_bytes=4 * 1024 * 1024))
        if args.mode == 'render':
            manifest = render_preflight(config, name=args.name, worker_image=args.worker_image,
                node=args.node, uid_start=args.uid_start, bootstrap_secret=args.bootstrap_secret,
                upstream_secret=args.upstream_secret, upstream_key=args.upstream_key)
            base.atomic(args.output, manifest)
            print(json.dumps(dict(status='runtime_preflight_manifest_rendered',
                harness_sha256=harness_sha256(), base_harness_sha256=base.harness_sha256(),
                domain_execution_performed=False, production_acceptance_created=False)), flush=True)
            return 0
        authority = RuntimePreflightAuthority(config, args.state_dir, base.credential(base.UPSTREAM),
            base.credential(base.BOOTSTRAP), args.worker_name, args.worker_namespace, args.uid_start)
        import uvicorn
        from agent_service.__main__ import _logging_configuration
        uvicorn.run(proxy_app(authority), host='0.0.0.0', port=8080, access_log=False,
            proxy_headers=False, server_header=False, log_config=_logging_configuration())
        return 0
    except Exception as error:
        # Never print upstream/model exceptions, grants, headers or task bytes.
        print(json.dumps(dict(status='runtime_preflight_failed',
            code=error.code if isinstance(error, ServiceError) else 'runtime_preflight_failed',
            error_type=type(error).__name__, domain_execution_performed=False,
            production_acceptance_created=False)), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
