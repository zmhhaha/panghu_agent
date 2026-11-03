"""Private payload for run_current_recovery_kubernetes.py; never a service entry.

The Agent API, PostgreSQL Store, Dispatcher, Kubernetes provider, WorkerHost,
lease watchdog, Linux UID stopper and result signatures are real. The fixed
Python workload and its legacy unbound profile are explicitly synthetic. No Pi,
model, production acceptance record, PPT Version or publication is constructed.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

STATE = Path('/state')
SCRIPT = Path('/probe/recovery.py')
SETTINGS = Path('/settings/settings.json')
CONTROL = Path('/run/agent-control')
WORK = Path('/work')
PROFILE = 'workspace-task.v1'
REVISION = 'synthetic-recovery-20261103-v1'
SCENARIOS = {'baseline', 'restart', 'interruption', 'deadline', 'network',
             'bad-output', 'storage-error', 'following'}


class ProbeFailure(RuntimeError):
    pass


def require(value, code):
    if not value:
        raise ProbeFailure(code)


def raw(path, limit=2 * 1024 * 1024):
    with Path(path).open('rb') as stream:
        value = stream.read(limit + 1)
    require(len(value) <= limit, 'bounded_read_exceeded')
    return value


def read(path):
    return json.loads(raw(path))


def atomic(path, value, *, exclusive=False):
    path = Path(path)
    target = path if exclusive else path.with_name(path.name + '.new-' + uuid.uuid4().hex)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    if not exclusive:
        target.replace(path)


def settings():
    value = read(SETTINGS)
    require(os.environ.get('PPT_RECOVERY_NAMESPACE') == value['namespace']
            and value['namespace'] == 'ppt-recovery-' + value['run_id']
            and os.environ.get('PPT_RECOVERY_SCRIPT_SHA256') == value['script_sha256']
            and hashlib.sha256(raw(SCRIPT)).hexdigest() == value['script_sha256'],
            'isolated_scope_or_script_invalid')
    return value


def measured_sources(expected):
    import agent_service
    root = Path(agent_service.__file__).parent
    found = {}
    for name, checksum in expected.items():
        require('..' not in name.split('/') and not name.startswith('/'), 'invalid_source_name')
        found[name] = hashlib.sha256(raw(root / name)).hexdigest()
        require(found[name] == checksum, 'image_source_mismatch:' + name)
    return found


def policy(value):
    from agent_service.contracts import ProfilePolicy, validate_workspace_task
    return ProfilePolicy(PROFILE, REVISION, 'pi', REVISION,
        value['worker_image'].rsplit('@', 1)[1], enabled=True,
        max_timeout_seconds=180, task_validator=validate_workspace_task)


def chain(value):
    from agent_service.store import Store
    from agent_service.kubernetes_client import KubernetesClient
    from agent_service.kubernetes_provider import KubernetesPool, KubernetesWorkerAdapter

    # Only this isolated probe changes the fixed manifest. The expected manifest
    # and its persisted intent are deterministic across actual process restarts.
    # Every resource check, create/start CAS, HTTP transport, stop observation,
    # finalizer and cleanup operation remains the original provider's method.
    class SyntheticCapsuleManifest(KubernetesWorkerAdapter):
        def _check_resource(self, actual, expected, *, pod=False):
            super()._check_resource(actual, expected, pod=pod)
            if pod:
                for row in actual.get('status', {}).get('containerStatuses', []):
                    image_id = row.get('imageID', '')
                    require(row.get('restartCount') == 0 and (image_id.endswith(
                        value['worker_image'].rsplit('@', 1)[1]) if image_id else
                        type(row.get('state', {}).get('waiting')) is dict),
                        'actual_worker_image_or_restart_mismatch')
                atomic(STATE / ('observed-pod-' + actual['metadata']['uid'] + '.json'), actual)

        def _manifest(self, envelope):
            pod, secret = super()._manifest(envelope)
            for resource in (pod, secret):
                resource['metadata']['labels']['ppt.panghuer.top/recovery-owner'] = value['run_id']
            pod['metadata']['labels']['ppt.panghuer.top/recovery-role'] = 'worker'
            spec = pod['spec']
            spec['activeDeadlineSeconds'] = 240
            spec['nodeSelector'] = {'kubernetes.io/hostname': value['node']}
            container = spec['containers'][0]
            container['command'] = [value['worker_python'], '/probe/recovery.py', 'worker']
            container.pop('args', None)  # Kubernetes omits empty args on readback.
            container['resources'] = {'requests': {'cpu': '50m', 'memory': '64Mi'},
                'limits': {'cpu': '500m', 'memory': '256Mi', 'ephemeral-storage': '128Mi'}}
            container['env'] += [
                {'name': 'PPT_RECOVERY_NAMESPACE', 'value': value['namespace']},
                {'name': 'PPT_RECOVERY_SCRIPT_SHA256', 'value': value['script_sha256']},
                {'name': 'PYTHONDONTWRITEBYTECODE', 'value': '1'}]
            container['volumeMounts'] += [
                {'name': 'probe', 'mountPath': '/probe', 'readOnly': True},
                {'name': 'settings', 'mountPath': '/settings', 'readOnly': True}]
            # Public settings in the Worker contain no caller or signing keys.
            spec['volumes'] += [
                {'name': 'probe', 'configMap': {'name': 'recovery-code', 'defaultMode': 292}},
                {'name': 'settings', 'configMap': {'name': 'recovery-host',
                    'items': [{'key': 'public.json', 'path': 'settings.json'}], 'defaultMode': 292}}]
            for volume in spec['volumes']:
                if volume['name'] in {'control', 'work', 'tmp'}:
                    volume['emptyDir'] = {'medium': 'Memory',
                        'sizeLimit': '8Mi' if volume['name'] == 'tmp' else '16Mi'}
            return pod, secret

    store = Store(value['database_url'], execution_slots=1, max_queue=16, max_events=1000)
    pool = KubernetesPool('recovery-probe', policy(value), value['namespace'],
        value['worker_image'], 'recovery-host', tuple(value['pod_cidrs']), enabled=True,
        uid_start=value['uid_start'], uid_count=value['uid_count'],
        node_selector=(('kubernetes.io/hostname', value['node']),))
    adapter = SyntheticCapsuleManifest(store, pool, KubernetesClient.from_service_account(),
        value['pool_token'], bytes.fromhex(value['receipt_key_hex']))
    return store, adapter


def serve_api(value):
    import uvicorn
    from agent_service.api import ApiSettings, create_app
    from agent_service.contracts import Principal, ProfileRegistry
    from agent_service.__main__ import _logging_configuration
    measured_sources(value['sources']['control'])
    store, adapter = chain(value)
    try:
        app = create_app(ApiSettings({value['caller_token']: Principal('synthetic-recovery',
            'isolated-probe', frozenset({PROFILE}))}), store, ProfileRegistry([policy(value)]), {'pi': adapter})
        uvicorn.run(app, host='127.0.0.1', port=8080, access_log=False,
            proxy_headers=False, server_header=False, log_config=_logging_configuration())
    finally:
        adapter.close()
        store.engine.dispose()


def dispatch(value):
    from agent_service.dispatcher import Dispatcher
    measured_sources(value['sources']['control'])
    store, adapter = chain(value)
    try:
        actual = Dispatcher(store, {'pi': adapter}, lease_seconds=30)
        while True:
            result = actual.tick()
            atomic(STATE / 'dispatcher.json', {'pid': os.getpid(),
                'state': result['state'] if result else 'idle', 'at': time.time()})
            time.sleep(.25)
    finally:
        adapter.close()
        store.engine.dispose()


# This fixed child is launched with the unchanged production UID launcher. It
# cannot read the host journal/auth key and receives no ServiceAccount token.
CHILD = r'''
import errno,json,os,subprocess,sys,time
from pathlib import Path
root=Path.cwd()
fields=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
denied=False
try:Path('/run/agent-control/private-sentinel').read_bytes()
except PermissionError:denied=True
credential_denied=False
try:Path('/run/agent-secrets/worker.json').read_bytes()
except PermissionError:credential_denied=True
observation={'uid':list(os.getresuid()),'gid':list(os.getresgid()),'groups':os.getgroups(),
 'caps_empty':all(int(fields[k].strip(),16)==0 for k in ('CapEff','CapPrm','CapInh','CapAmb')),
 'nnp':fields.get('NoNewPrivs','').strip()=='1','control_denied':denied,'credential_denied':credential_denied,
 'sa_token_absent':not Path('/var/run/secrets/kubernetes.io/serviceaccount/token').exists()}
(root/'observation.json').write_text(json.dumps(observation))
if sys.argv[1]=='storage-error':
 try:
  with (root/'fill').open('wb',buffering=0) as stream:
   for _ in range(512):stream.write(b'x'*65536)
  raise RuntimeError('bounded_tmpfs_not_full')
 except OSError as error:
  assert error.errno==errno.ENOSPC
  print(json.dumps({'actual_errno':error.errno,'enospc':True}),flush=True)
 sys.exit(0)
writer="from pathlib import Path;import os,time; p=Path('detached-heartbeat'); i=0\nwhile True:\n i+=1;p.write_text(str(os.getpid())+':'+str(i));time.sleep(.03)"
child=subprocess.Popen([sys.executable,'-c',writer],stdin=subprocess.DEVNULL,
 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
(root/'descendant.json').write_text(json.dumps({'pid':child.pid,'pgid':os.getpgid(child.pid)}))
number=0
while True:
 number+=1;(root/'heartbeat').write_text(str(number));time.sleep(.03)
'''


def worker(value):
    import uvicorn
    from agent_service.contracts import canonical_bytes
    from agent_service.engines import NativeOutcome
    from agent_service.linux_stop import LinuxUidStopController, private_kernel_scope
    from agent_service.worker_entry import CapsuleScope, HostConfig, _read_operator_json
    from agent_service.worker_api import WorkerHost, create_worker_app
    from agent_service.__main__ import _logging_configuration
    from agent_service.runtime import launcher

    source_observation = measured_sources(value['sources']['worker'])
    private_kernel_scope()
    config = HostConfig.model_validate(_read_operator_json(Path('/etc/agent-worker/config.json')))
    scope = CapsuleScope.from_env(config)
    credentials = _read_operator_json(Path('/run/agent-secrets/worker.json'), private=True)
    require(set(credentials) == {'worker_token', 'receipt_key_hex'}, 'unexpected_worker_credentials')
    CONTROL.chmod(0o700)
    WORK.chmod(0o711)
    atomic(CONTROL / 'private-sentinel', {'synthetic': True}, exclusive=True)
    stopper = LinuxUidStopController(CONTROL / 'uid-scope', scope.attempt_id,
        scope.pod_uid, scope.request_sha256, agent_uid=scope.agent_uid)
    stopper.initialize()
    require(not stopper._uid_processes(), 'uid_already_has_writers')

    class SyntheticWorkload:
        ready, engine = True, 'pi'

        def execute(self, request, workspace, emit, cancel):
            scenario = request.payload.get('prompt')
            require(scenario in SCENARIOS, 'unknown_synthetic_workload')
            workspace.mkdir(mode=0o700)
            os.chown(workspace, scope.agent_uid, scope.agent_uid)
            atomic(CONTROL / 'native-launch.json', {'attempt_id': scope.attempt_id,
                'nonce': uuid.uuid4().hex, 'scenario': scenario, 'at': time.time()}, exclusive=True)
            launch = CONTROL / 'launch.json'
            atomic(launch, {'controller_pid': os.getpid(), 'max_processes': 32,
                'cgroup': None, 'uid': scope.agent_uid, 'gid': scope.agent_uid,
                'denied_paths': [str(CONTROL / 'private-sentinel')], 'cwd': str(workspace),
                'command': [sys.executable, '-c', CHILD, scenario]}, exclusive=True)
            with (CONTROL / 'child.log').open('xb') as output:
                os.chmod(CONTROL / 'child.log', 0o600)
                process = subprocess.Popen([sys.executable, str(Path(launcher.__file__)), str(launch)],
                    cwd=workspace, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                    env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(workspace)},
                    start_new_session=True)
            started = time.monotonic()
            duration = 20 if scenario == 'restart' else 1 if scenario in {'baseline', 'following', 'bad-output'} else 180
            try:
                if scenario == 'storage-error':
                    require(process.wait(timeout=10) == 0, 'storage_child_failed')
                    require(read(CONTROL / 'child.log') == {'actual_errno': 28, 'enospc': True},
                        'actual_enospc_not_observed')
                else:
                    while not cancel.wait(.03) and time.monotonic() - started < duration:
                        require(process.poll() is None, 'workload_exited_early')
            finally:
                stopper.stop(scope.attempt_id)
                process.wait(timeout=3)
                until = time.monotonic() + 1
                while time.monotonic() < until:
                    try:
                        pid, _ = os.waitpid(-1, os.WNOHANG)
                    except ChildProcessError:
                        break
                    if not pid:
                        time.sleep(.02)
            proof = stopper.prove_stopped(scope.attempt_id)
            require(proof.all_writers_stopped and not stopper._uid_processes(), 'uid_stop_not_proven')
            heartbeats = {name: hashlib.sha256(raw(workspace / name)).hexdigest()
                for name in ('heartbeat', 'detached-heartbeat') if (workspace / name).is_file()}
            time.sleep(.15)
            require(all(hashlib.sha256(raw(workspace / name)).hexdigest() == checksum
                for name, checksum in heartbeats.items()), 'heartbeat_after_stop')
            atomic(CONTROL / 'workload-stopped.json', {'all_writers_stopped': True,
                'resource_ids': list(proof.resource_ids), 'heartbeat_sha256': heartbeats,
                'actual_enospc': scenario == 'storage-error'}, exclusive=True)
            # A finite observation window preserves raw kernel-stop/ENOSPC
            # evidence outside this disposable Pod before result collection.
            time.sleep(3)
            if scenario == 'storage-error':
                return NativeOutcome('failed', error_code='synthetic_storage_enospc',
                    stopped=True, resource_ids=proof.resource_ids)
            output = {'schema_version': '1', 'text': 7 if scenario == 'bad-output' else 'SYNTHETIC:' + scenario}
            return NativeOutcome('completed', output=output, stopped=True, resource_ids=proof.resource_ids)

    host = WorkerHost(policy(value), SyntheticWorkload(), CONTROL, WORK,
        bytes.fromhex(credentials['receipt_key_hex']))
    app = create_worker_app(host, credentials['worker_token'])

    @app.middleware('http')
    async def count_admission(request, call_next):
        if (request.method == 'PUT' and request.url.path == '/v1/executions/' + scope.attempt_id
                and hmac.compare_digest(request.headers.get('authorization', ''),
                    'Bearer ' + credentials['worker_token'])):
            target = CONTROL / 'put-count.json'
            count = read(target)['count'] if target.exists() else 0
            require(count < 8, 'admission_counter_bound')
            atomic(target, {'count': count + 1})
        return await call_next(request)

    @app.get('/probe/state')
    def observation():
        result = {'attempt_id': scope.attempt_id, 'pod_uid': scope.pod_uid,
            'request_sha256': scope.request_sha256, 'uid': scope.agent_uid,
            'put_count': read(CONTROL / 'put-count.json')['count'] if (CONTROL / 'put-count.json').exists() else 0,
            'measured_sources': source_observation,
            'active_uid_writers': stopper._uid_processes(),
            'native_launch': read(CONTROL / 'native-launch.json') if (CONTROL / 'native-launch.json').exists() else None,
            'stopped': read(CONTROL / 'workload-stopped.json') if (CONTROL / 'workload-stopped.json').exists() else None}
        workspace = WORK / scope.attempt_id
        for name in ('observation', 'descendant'):
            path = workspace / (name + '.json')
            if path.exists():
                try:
                    result[name] = read(path)
                except ValueError:
                    pass  # The fixed child may still be writing its first observation.
        result['state'] = host.status(scope.attempt_id)['state'] if host._state else 'cold'
        return result

    uvicorn.run(app, host='0.0.0.0', port=8090, access_log=False,
        proxy_headers=False, server_header=False, log_config=_logging_configuration())


def coordinator(value):
    import httpx
    from sqlalchemy import select, func
    from agent_service.store import attempts, worker_allocations, slot
    from agent_service.dispatcher import Dispatcher
    from agent_service.contracts import TERMINAL_STATES

    STATE.mkdir(mode=0o700, exist_ok=True)
    os.chmod(STATE, 0o700)
    store = adapter = None
    children = []
    rows = []
    status = {'passed': False, 'phase': 'initializing', 'sources': {},
        'scope': 'real Agent control/provider/WorkerHost; synthetic Python workload; no model or PPT Version',
        'run_id': value['run_id'], 'model_requests': 0, 'scenarios': rows}
    api = scheduler = None

    def save(phase, action=None):
        status.update(phase=phase, action=action, at=time.time())
        atomic(STATE / 'status.json', status)

    def start(mode):
        log = (STATE / (mode + '-' + uuid.uuid4().hex + '.log')).open('xb')
        process = subprocess.Popen([sys.executable, '-B', str(SCRIPT), mode],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        log.close()
        children.append(process)
        return process

    def stop(process):
        require(process is not None and process.poll() is None, 'dispatch_exited_before_fault_injection')
        process.kill()
        require(process.wait(timeout=5) == -9, 'dispatch_process_not_killed')

    client = httpx.Client(base_url='http://127.0.0.1:8080', timeout=5, trust_env=False,
        headers={'Authorization': 'Bearer ' + value['caller_token']})

    def request(method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        require(response.status_code in {200, 201, 202}, 'actual_api_request_failed')
        return response.json()

    def wait(test, seconds=75):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if api is not None:
                require(api.poll() is None, 'actual_api_process_exited')
            try:
                found = test()
            except httpx.HTTPError:
                found = None
            if found:
                return found
            time.sleep(.2)
        raise ProbeFailure('bounded_observation_timeout')

    def job(jid):
        return request('GET', '/v1/jobs/' + jid)

    def owned(jid):
        return store.get_job('synthetic-recovery', 'isolated-probe', jid)

    def native(jid, *, stopped=False):
        current = owned(jid)
        if current['attempt_id'] is None:
            return None
        envelope = Dispatcher(store, {'pi': adapter}, lease_seconds=30)._envelope(current)
        from agent_service.contracts import ServiceError
        try:
            allocation = store.get_worker_allocation(envelope.attempt_id)
        except ServiceError as error:
            if error.status == 404:
                return None
            raise
        expected, _ = adapter._manifest(envelope)
        try:
            pod = adapter._pod(allocation, expected)
        except ServiceError as error:
            if error.code == 'worker_resource_missing' and allocation['pod_uid'] is None:
                return None
            raise
        if not pod or not pod.get('status', {}).get('podIP'):
            return None
        ip = pod['status']['podIP']
        endpoint = 'http://' + ('[' + ip + ']' if ':' in ip else ip) + ':8090/probe/state'
        token = adapter._credentials(envelope)['worker_token']
        try:
            response = httpx.get(endpoint, headers={'Authorization': 'Bearer ' + token},
                timeout=1, trust_env=False)
            if response.status_code == 200:
                observed = response.json()
                if (observed.get('native_launch') and observed.get('observation')
                        and (observed.get('stopped') if stopped else
                             observed.get('descendant') and observed.get('active_uid_writers'))):
                    allocation = store.get_worker_allocation(envelope.attempt_id)
                    require(allocation['pod_uid'] == pod['metadata']['uid'] == observed['pod_uid']
                        and allocation['request_sha256'] == observed['request_sha256'] == envelope.request_sha256
                        and observed['attempt_id'] == envelope.attempt_id, 'native_observation_identity_mismatch')
                    return {'job': current, 'envelope': envelope, 'allocation': allocation,
                        'pod': pod, 'observed': observed, 'endpoint': endpoint, 'token': token}
        except (httpx.HTTPError, ValueError):
            pass
        return None

    def check_native(observed):
        require(observed['put_count'] == 1 and observed['native_launch']['attempt_id'] == observed['attempt_id'],
            'native_admission_duplicated')
        data = observed['observation']
        require(data['uid'] == [observed['uid']] * 3 and data['gid'] == [observed['uid']] * 3
            and data['groups'] == [] and all(data[k] for k in
            ('caps_empty', 'nnp', 'control_denied', 'credential_denied', 'sa_token_absent')), 'native_privilege_boundary_failed')
        require(observed['descendant']['pid'] == observed['descendant']['pgid'], 'descendant_not_detached')

    def action(kind, context, **extra):
        document = dict(id=uuid.uuid4().hex, kind=kind,
            pod_name=context['allocation']['pod_name'], pod_uid=context['allocation']['pod_uid'],
            attempt_id=context['envelope'].attempt_id, pod_ip=context['pod']['status']['podIP'], **extra)
        save('waiting-for-owned-operator-action', document)
        ack = STATE / ('ack-' + document['id'] + '.json')
        result = wait(lambda: read(ack) if ack.exists() else None, 90)
        require(result.get('action_id') == document['id'] and result.get('passed') is True,
            'independent_action_failed')
        save('running-' + kind)
        return result

    baseline = None

    def baseline_unchanged():
        if baseline is None:
            return
        values = request('GET', '/v1/jobs/' + baseline['job_id'] + '/artifacts')['items']
        require(len(values) == 1 and values[0]['sha256'] == baseline['sha256'], 'old_artifact_metadata_changed')
        response = client.get('/v1/artifacts/' + values[0]['artifact_id'] + '/content')
        require(response.status_code == 200 and hashlib.sha256(response.content).hexdigest() == baseline['sha256']
            and response.content == baseline['bytes'], 'old_artifact_bytes_changed')

    def final_record(name, jid, initial=None, **extra):
        result = wait(lambda: (current if (current := job(jid))['state'] in TERMINAL_STATES
            and current['state'] != 'quarantined' else None), 130)
        require(result['stop_proven'] is True, 'real_external_stop_proof_missing')
        allocation = store.get_worker_allocation(result['attempt']['attempt_id'])
        require(allocation['retired'] and allocation['stop_observed_at'] is not None
            and allocation['worker_start_claimed'], 'physical_stop_not_durably_persisted')
        pod_observation = read(STATE / ('observed-pod-' + allocation['pod_uid'] + '.json'))
        require(adapter._all_terminated(pod_observation), 'retained_actual_container_termination_missing')
        with store.engine.connect() as connection:
            require(connection.scalar(select(func.count()).select_from(attempts).where(attempts.c.job_id == jid)) == 1
                and connection.scalar(select(func.count()).select_from(worker_allocations).where(
                    worker_allocations.c.job_id == jid)) == 1, 'actual_attempt_or_allocation_duplicated')
            require(connection.scalar(select(func.count()).select_from(slot).where(slot.c.job_id.is_not(None))) == 0,
                'slot_not_released_after_observed_stop')
        if initial:
            require(result['attempt']['attempt_id'] == initial['envelope'].attempt_id
                and result['attempt']['fence'] == initial['envelope'].fence
                and allocation['pod_uid'] == initial['allocation']['pod_uid']
                and result['deadline_at'] == initial['envelope'].deadline_at,
                'original_execution_identity_or_deadline_changed')
        baseline_unchanged()
        row = {'scenario': name, 'job': result, 'allocation': {key: allocation[key] for key in
            ('attempt_id', 'pod_uid', 'agent_uid', 'aux_uid', 'create_claimed', 'worker_start_claimed',
             'retired', 'stop_observed_at', 'request_sha256')},
            'one_durable_attempt': True, 'one_durable_allocation': True,
            'actual_pod_termination': pod_observation,
            'old_synthetic_agent_artifact_unchanged': baseline is not None, **extra}
        rows.append(row)
        save('completed-' + name)
        return result

    try:
        status['sources'] = measured_sources(value['sources']['control'])
        status['source_role'] = 'control'
        store, adapter = chain(value)
        store.initialize()
        api = start('api')
        wait(lambda: client.get('/healthz').status_code == 200 if api.poll() is None else False)
        scheduler = start('dispatch')

        def submit(name, timeout=120):
            return request('POST', '/v1/jobs', headers={'Idempotency-Key': 'synthetic-' + name},
                json={'profile': PROFILE, 'profile_revision': REVISION,
                      'business_ref': 'synthetic:' + name, 'task': {'prompt': name}, 'timeout_seconds': timeout})['job_id']

        save('baseline')
        jid = submit('baseline')
        result = final_record('baseline', jid)
        require(result['state'] == 'completed', 'synthetic_baseline_failed')
        artifact = request('GET', '/v1/jobs/' + jid + '/artifacts')['items'][0]
        response = client.get('/v1/artifacts/' + artifact['artifact_id'] + '/content')
        require(response.status_code == 200 and hashlib.sha256(response.content).hexdigest() == artifact['sha256'],
            'baseline_download_failed')
        baseline = {'job_id': jid, 'sha256': artifact['sha256'], 'bytes': response.content}
        atomic(STATE / 'baseline.json', {'job_id': jid, 'sha256': artifact['sha256']}, exclusive=True)

        jid = submit('restart')
        initial = wait(lambda: native(jid))
        check_native(initial['observed'])
        old_pid = scheduler.pid
        stop(scheduler)
        require(job(jid)['stop_proven'] is False and store.active()['job_id'] == jid,
            'slot_released_without_stop_on_restart')
        scheduler = start('dispatch')
        replacement = wait(lambda: native(jid))
        check_native(replacement['observed'])
        require(initial['allocation']['pod_uid'] == replacement['allocation']['pod_uid']
            and initial['observed']['native_launch']['nonce'] == replacement['observed']['native_launch']['nonce'],
            'restart_created_another_native_execution')
        result = final_record('restart', jid, initial, original_dispatcher_pid=old_pid,
            replacement_dispatcher_pid=scheduler.pid, observed_before=initial['observed'],
            observed_after=replacement['observed'])
        require(result['state'] == 'completed', 'same_attempt_restart_failed')

        jid = submit('interruption')
        initial = wait(lambda: native(jid))
        check_native(initial['observed'])
        stop(scheduler)
        require(store.active()['job_id'] == jid and job(jid)['stop_proven'] is False,
            'slot_released_before_worker_interruption')
        independent = action('kill-worker', initial)
        require(store.active()['job_id'] == jid and job(jid)['stop_proven'] is False,
            'external_observation_alone_released_slot')
        scheduler = start('dispatch')
        result = final_record('interruption', jid, initial,
            observed_before=initial['observed'], independent=independent)
        require(result['state'] == 'failed', 'interrupted_worker_not_failed')

        jid = submit('deadline', 25)
        initial = wait(lambda: native(jid), 20)
        check_native(initial['observed'])
        result = final_record('deadline', jid, initial, observed_before=initial['observed'])
        require(result['state'] == 'timed_out', 'absolute_deadline_not_enforced')

        jid = submit('network', 120)
        initial = wait(lambda: native(jid))
        check_native(initial['observed'])
        before_lease = owned(jid)['lease_expires_at']
        denied = action('deny-worker-network', initial)
        failures, allowed_during_propagation = [], 0
        denial_deadline = time.monotonic() + 20
        while len(failures) < 3 and time.monotonic() < denial_deadline:
            try:
                response = httpx.get(initial['endpoint'], headers={'Authorization': 'Bearer ' + initial['token']},
                    timeout=1.5, trust_env=False)
                require(response.status_code == 200, 'worker_failed_during_network_transition')
                allowed_during_propagation += 1
                failures = []
                time.sleep(.2)
            except (httpx.ConnectTimeout, httpx.ReadTimeout):
                failures.append('timeout')
        require(len(failures) == 3, 'three_actual_network_timeouts_not_observed')
        # With its Kubernetes API still reachable, a live Dispatcher correctly
        # deletes the quarantined Pod after expiry. Stop the actual process now
        # so this scenario can independently measure the Worker's own watchdog
        # and restore traffic to that exact still-live Pod before reconciliation.
        network_dispatcher_pid = scheduler.pid
        stop(scheduler)
        frozen_lease = owned(jid)['lease_expires_at']
        # A Dispatcher HTTP request already in flight can commit a single lease
        # renewal at the transition. Freeze after confirmed denial, never assert
        # a lease from before policy propagation remained byte-identical.
        until = frozen_lease + 1.5
        while time.time() < until:
            require(owned(jid)['lease_expires_at'] == frozen_lease, 'unobserved_lease_extended')
            require(job(jid)['stop_proven'] is False, 'stop_invented_during_network_loss')
            time.sleep(.25)
        restored = action('restore-worker-network', initial, require_workload_stopped=True)
        after_restore = wait(lambda: native(jid, stopped=True), 15)
        require(after_restore['pod']['status']['podIP'] == initial['pod']['status']['podIP'],
            'worker_ip_changed_during_network_restore')
        observed = after_restore['observed']
        require(observed['pod_uid'] == initial['allocation']['pod_uid'] and observed['put_count'] == 1
            and observed['native_launch']['nonce'] == initial['observed']['native_launch']['nonce']
            and not observed['active_uid_writers'] and observed['stopped']['all_writers_stopped'],
            'lease_watchdog_did_not_stop_original_workload')
        scheduler = start('dispatch')
        result = final_record('network', jid, initial, observed_before=initial['observed'],
            observed_after=observed, actual_timeouts=failures, lease_before_policy=before_lease,
            lease_after_confirmed_denial=frozen_lease, interrupted_dispatcher_pid=network_dispatcher_pid,
            replacement_dispatcher_pid=scheduler.pid,
            allowed_connections_during_policy_propagation=allowed_during_propagation,
            independent_denial=denied, independent_restore=restored)
        require(result['state'] == 'failed', 'network_interruption_not_failed')

        for name, code in [('bad-output', 'worker_output_invalid'), ('storage-error', 'synthetic_storage_enospc')]:
            jid = submit(name)
            observed = wait(lambda: native(jid, stopped=True))
            require(observed['observed']['put_count'] == 1
                and not observed['observed']['active_uid_writers']
                and observed['observed']['stopped']['all_writers_stopped'], 'failure_writers_not_stopped')
            if name == 'storage-error':
                require(observed['observed']['stopped']['actual_enospc'] is True, 'enospc_receipt_missing')
            result = final_record(name, jid, observed, observed_after=observed['observed'])
            require(result['state'] == 'failed' and result['error_code'] == code, 'specific_actual_failure_not_observed')
            require(request('GET', '/v1/jobs/' + jid + '/artifacts')['items'] == [], 'failed_candidate_exported')

        jid = submit('following')
        result = final_record('following', jid)
        require(result['state'] == 'completed', 'queue_not_usable_after_faults')
        require(len({row['allocation']['pod_uid'] for row in rows}) == len(rows), 'pod_uid_reused')
        require(len({row['allocation']['agent_uid'] for row in rows}) == len(rows), 'runtime_uid_reused')
        status['passed'] = True
        save('complete')
    except Exception as error:
        status['error'] = str(error) if type(error) is ProbeFailure else type(error).__name__
        save('failed')
    finally:
        for process in reversed(children):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        client.close()
        if adapter is not None:
            adapter.close()
        if store is not None:
            store.engine.dispose()
        atomic(STATE / 'status.json', status)
    # Keep private synthetic evidence readable until the outer operator copies
    # it and has positively observed/cleaned all owned container lifecycles.
    while True:
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('coordinator', 'api', 'dispatch', 'worker', 'status', 'ack', 'probe-local'))
    args = parser.parse_args()
    if args.mode == 'status':
        print(json.dumps(read(STATE / 'status.json') if (STATE / 'status.json').exists() else {'phase': 'starting'}))
        return
    if args.mode == 'ack':
        data = json.loads(sys.stdin.buffer.read(131073))
        require(type(data) is dict and len(json.dumps(data)) <= 131072
            and len(data.get('action_id', '')) == 32 and all(c in '0123456789abcdef' for c in data['action_id']),
            'invalid_action_ack')
        atomic(STATE / ('ack-' + data['action_id'] + '.json'), data, exclusive=True)
        return
    if args.mode == 'probe-local':
        import urllib.request
        from agent_service.worker_entry import _read_operator_json
        secret = _read_operator_json(Path('/run/agent-secrets/worker.json'), private=True)
        request = urllib.request.Request('http://127.0.0.1:8090/probe/state',
            headers={'Authorization': 'Bearer ' + secret['worker_token']})
        with urllib.request.urlopen(request, timeout=2) as response:
            print(response.read(131072).decode())
        return
    value = settings()
    {'coordinator': coordinator, 'api': serve_api, 'dispatch': dispatch, 'worker': worker}[args.mode](value)


if __name__ == '__main__':
    main()
