"""Admin protocol tests with synthetic kernels/model responses, not acceptance.

The real isolated Store/ledger and real wall-clock grant expiry are exercised.
MockTransport and fake stop controllers never claim actual Linux/model health.
"""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest

from agent_service.contracts import ServiceError, digest
from agent_service.deploy import runtime_preflight as runtime
from agent_service.pi_cage import PI_CAGE_REPOSITORY, PI_CAGE_REVISION, PI_VERSION
from agent_service.profiles import implementation
from agent_service.worker_entry import HostConfig
from test_acceptance_probe import config, BOOTSTRAP, IMAGE
from test_catalog import image_packages  # Explicit synthetic package fixture.


ECHO = {'status': 'completed', 'output': [{'type': 'message', 'content': [
    {'type': 'output_text', 'text': 'CANDIDATE_MODEL_OK'}]}]}


@pytest.fixture
def authority(image_packages, tmp_path):
    instances = []

    def make(profile='ppt.revise.v1', *, origin=None):
        value = config(profile).model_dump(mode='json')
        if origin is not None:
            value['model']['proxy_base_url'] = origin + '/v1'
        instance = runtime.RuntimePreflightAuthority(HostConfig.model_validate(value),
            tmp_path / str(len(instances)), 'offline-upstream-caller-token', BOOTSTRAP,
            'runtime-worker', 'agent-workers', 2100000000)
        instance.proxy.client.close()
        instance.upstream_requests = []

        def respond(request):
            instance.upstream_requests.append(json.loads(request.content))
            return httpx.Response(200, json=ECHO)

        instance.proxy.client = httpx.Client(transport=httpx.MockTransport(respond))
        instances.append(instance)
        return instance

    yield make
    for instance in instances:
        instance.proxy.client.close()
        instance.store.close()


def observations(value, response):
    return dict(harness_sha256=runtime.harness_sha256(),
        base_harness_sha256=runtime.base.harness_sha256(),
        capability=dict(engine='pi', pi_version=PI_VERSION, node_version='22.19.0',
            package_measured=True, pi_cage_repository=PI_CAGE_REPOSITORY,
            pi_cage_revision=PI_CAGE_REVISION,
            allowed_tools=list(implementation(value.policy.name).required_tools)),
        model_round_trip=dict(status=200, api_mode='responses',
            response_sha256=hashlib.sha256(response.content).hexdigest()),
        isolation=dict(**{key: True for key in runtime.ISOLATION},
                       uid=[value.uid + 2] * 3, groups=[]))


def stop_evidence(value):
    import time
    return dict(all_writers_stopped=True, observed_at=time.time(),
        resource_ids=['pod:' + value.registered, 'pid:[12345]', 'uid:' + str(value.uid),
                      'uid:' + str(value.uid + 1), 'attempt:' + value.envelope.attempt_id])


@pytest.mark.parametrize('profile', sorted(runtime.PROFILES))
def test_every_target_has_admin_task_and_never_makes_ppt_confirmations(authority, monkeypatch, profile):
    def no_business_fixture(*args, **kwargs):
        pytest.fail('A runtime preflight must not construct or validate a domain task')

    monkeypatch.setattr(runtime.base, 'fixture', no_business_fixture)
    from agent_service.profiles.ppt import PPTProfile
    monkeypatch.setattr(PPTProfile, 'validate_task', no_business_fixture)
    value = authority(profile)
    request = json.loads((value.directory / 'runtime-preflight-request.json').read_bytes())
    envelope = value.envelope
    assert request['request_kind'] == 'runtime_prerequisites_only'
    assert envelope.profile == profile and envelope.task_schema == runtime.TASK_SCHEMA
    assert envelope.task == dict(schema_version=runtime.TASK_SCHEMA, target_profile=profile,
        target_profile_revision=value.policy.revision,
        target_combination_sha256=envelope.execution_binding.combination_sha256,
        domain_execution_performed=False, business_workflow_verified=False)
    assert envelope.inputs == envelope.checkpoint_inputs == [] and envelope.checkpoint_ref is None
    assert value.policy.enabled is False and value.binding.acceptance is None
    assert all(not row['enabled'] for row in value.config.execution_catalog['routes'])
    assert not any(value.directory.glob('acceptance*'))


def test_fresh_state_and_same_registered_pod_are_required(authority):
    value = authority()
    with pytest.raises(FileExistsError):
        runtime.RuntimePreflightAuthority(value.config, value.directory, 'offline-upstream', BOOTSTRAP,
            'replacement-worker', 'agent-workers', value.uid + 3)
    uid = str(uuid4())
    assert value.register(uid) == value.register(uid)
    with pytest.raises(ServiceError):
        value.register(str(uuid4()))
    assert value.proxy.authorize(value.grant)['binding'] == value.envelope.execution_binding.sha256


@pytest.mark.parametrize('profile,engine', [
    ('research.revise.v1', 'pi'), ('ppt.revise.v1', 'hermes')])
def test_unrelated_profiles_or_engines_cannot_reuse_protocol(image_packages, profile, engine):
    with pytest.raises(ServiceError):
        runtime.preflight_config(config(profile, engine).model_dump(mode='json'))


def test_only_one_exact_echo_is_available_and_replays_spend_once(authority):
    value = authority()
    value.register(str(uuid4()))
    headers = {'Authorization': 'Bearer ' + value.grant}
    with TestClient(runtime.proxy_app(value)) as client:
        changed = dict(runtime.echo_body(value.config.model.model), input='arbitrary domain prompt')
        assert client.post('/v1/responses', headers=headers, json=changed).status_code == 422
        tool = dict(runtime.echo_body(value.config.model.model), tools=[{'type': 'function', 'name': 'write'}])
        assert client.post('/v1/responses', headers=headers, json=tool).status_code == 422
        assert client.post('/v1/tools/academic', headers=headers, json={}).status_code == 404
        first = client.post('/v1/responses', headers=headers, json=runtime.echo_body(value.config.model.model))
        second = client.post('/v1/responses', headers=headers, json=runtime.echo_body(value.config.model.model))
        assert first.status_code == second.status_code == 200 and first.content == second.content
        assert value.upstream_requests == [runtime.echo_body(value.config.model.model)]


def test_final_evidence_needs_real_http_denial_after_actual_lease_expiry(authority):
    value = authority()
    before = value.config.model_dump(mode='json')
    admin = {'Authorization': 'Bearer ' + BOOTSTRAP}
    headers = {'Authorization': 'Bearer ' + value.grant}
    with TestClient(runtime.proxy_app(value)) as client:
        uid = str(uuid4())
        assert client.post('/preflight/register', headers=admin, json={'pod_uid': uid}).status_code == 200
        echo = client.post('/v1/responses', headers=headers, json=runtime.echo_body(value.config.model.model))
        document = dict(observations=observations(value, echo), stop_evidence=stop_evidence(value))
        result = client.post('/preflight/result', headers=admin, json=document)
        assert result.status_code == 200 and result.json()['lease_expiry_rejected'] is True
        assert not (value.directory / 'runtime-preflight-evidence.json').exists()
        assert client.get('/preflight/evidence', headers=admin).json()['status'] == 'pending'
        wrong = client.post('/v1/responses', headers={'Authorization': 'Bearer unknown'}, json={})
        assert wrong.status_code == 401 and value.recorded is None
        expired = client.post('/v1/responses', headers=headers, json=runtime.echo_body(value.config.model.model))
        assert expired.status_code == 401 and expired.json()['error']['code'] == 'model_grant_revoked'
        evidence = client.get('/preflight/evidence', headers=admin).json()
        assert evidence == json.loads((value.directory / 'runtime-preflight-evidence.json').read_bytes())
        assert evidence['schema_version'] == runtime.SCHEMA and evidence['profile'] == 'ppt.revise.v1'
        assert evidence['expired_grant_http_status'] == 401 and evidence['all_uid_scopes_stopped'] is True
        assert all(evidence['checks'].values())
        for key in ('domain_execution_performed', 'business_workflow_verified',
                    'production_acceptance_created', 'production_routes_changed', 'publication_authorized'):
            assert evidence[key] is False
        assert evidence['domain_tool_calls'] == {} and evidence['domain_model_requests'] == 0
        assert 'result_state' not in evidence and 'result' not in evidence and 'artifacts' not in evidence
        assert evidence['model_calls'] == [dict(body_sha256=digest(runtime.echo_body(value.config.model.model)),
            state='complete', status=200, media_type='application/json',
            response_sha256=hashlib.sha256(echo.content).hexdigest())]
        assert value.config.model_dump(mode='json') == before
        assert len(value.upstream_requests) == 1
        assert client.post('/preflight/result', headers=admin, json=document).status_code == 422


@pytest.mark.parametrize('change', ['stop_uid', 'capability', 'echo_hash', 'harness', 'isolation', 'domain_result'])
def test_unmeasured_or_wrong_profile_runtime_claims_cannot_be_recorded(authority, change):
    value = authority()
    value.register(str(uuid4()))
    headers = {'Authorization': 'Bearer ' + value.grant}
    admin = {'Authorization': 'Bearer ' + BOOTSTRAP}
    with TestClient(runtime.proxy_app(value)) as client:
        response = client.post('/v1/responses', headers=headers, json=runtime.echo_body(value.config.model.model))
        document = dict(observations=observations(value, response), stop_evidence=stop_evidence(value))
        if change == 'stop_uid':
            document['stop_evidence']['resource_ids'][-2] = 'uid:10000'
        elif change == 'capability':
            document['observations']['capability']['pi_cage_revision'] = '0' * 40
        elif change == 'echo_hash':
            document['observations']['model_round_trip']['response_sha256'] = '0' * 64
        elif change == 'harness':
            document['observations']['base_harness_sha256'] = '0' * 64
        elif change == 'isolation':
            document['observations']['isolation']['detached_writers_stopped'] = False
        else:
            document['result'] = {'state': 'completed', 'artifacts': []}
        assert client.post('/preflight/result', headers=admin, json=document).status_code == 422
        assert value.recorded is value.pending is None
        assert not (value.directory / 'runtime-preflight-evidence.json').exists()


def test_admin_boundaries_do_not_leak_secrets_and_reject_oversize(authority, monkeypatch):
    value = authority()
    admin = {'Authorization': 'Bearer ' + BOOTSTRAP}
    with TestClient(runtime.proxy_app(value)) as client:
        assert client.post('/preflight/register', json={'pod_uid': str(uuid4())}).status_code == 401
        assert client.get('/preflight/evidence').status_code == 401
        assert client.post('/preflight/register', headers=admin, content=b'{').status_code == 422
        monkeypatch.setattr(runtime, 'MAX_CONTROL_BYTES', 64)
        response = client.post('/preflight/result', headers=admin, content=b'x' * 65)
        assert response.status_code == 413
        assert not any(secret in response.text for secret in (BOOTSTRAP, value.grant,
                                                             'offline-upstream-caller-token'))


def test_render_projects_git_script_and_preserves_exact_isolation_and_images(image_packages):
    value = config('ppt.revise.v1')
    original = deepcopy(value.model_dump(mode='json'))
    manifest = runtime.render_preflight(value, name='runtime-revise-unique', worker_image=IMAGE,
        node='reviewed-node', uid_start=2100000000, bootstrap_secret='runtime-bootstrap',
        upstream_secret='runtime-upstream')
    assert value.model_dump(mode='json') == original
    assert not any(item['kind'] in {'Secret', 'Deployment'} for item in manifest['items'])
    maps = [item for item in manifest['items'] if item['kind'] == 'ConfigMap']
    pods = [item for item in manifest['items'] if item['kind'] == 'Pod']
    assert len(maps) == len(pods) == 2
    for configmap in maps:
        code = configmap['data']['runtime_preflight.py']
        assert hashlib.sha256(code.encode()).hexdigest() == runtime.harness_sha256()
    for pod in pods:
        container = pod['spec']['containers'][0]
        assert container['image'] == IMAGE
        assert container['command'][-1] == '/probe/runtime_preflight.py'
        assert pod['spec']['automountServiceAccountToken'] is False
        assert pod['spec']['hostPID'] is False and pod['spec']['restartPolicy'] == 'Never'
        mounts = {row['mountPath']: row for row in container['volumeMounts']}
        assert mounts['/probe/runtime_preflight.py']['readOnly'] is True
        assert mounts['/opt/agent-service/agent_service/deploy/acceptance_probe.py']['readOnly'] is True
        assert not any('hostPath' in row for row in pod['spec']['volumes'])
    worker = next(row for row in pods if row['metadata']['namespace'] == 'agent-workers')
    assert 'upstream' not in {row['name'] for row in worker['spec']['volumes']}
    assert worker['spec']['containers'][0]['securityContext']['appArmorProfile'] == {
        'type': 'Localhost', 'localhostProfile': 'agent-capsule-inputs'}


def test_renderer_never_inherits_business_source_or_parent_mounts(image_packages):
    with pytest.raises(ServiceError) as failure:
        runtime.render_preflight(config('ppt.revise.v1'), scenario='a-business-scenario.json')
    assert failure.value.code == 'runtime_preflight_has_no_business_scenario'


def test_worker_only_constructs_driver_and_stops_both_scopes(authority, monkeypatch, capsys):
    origin = 'http://runtime-probe.agent-service.svc.cluster.local:8080'
    value = authority(origin=origin)
    pod_uid = str(uuid4())
    monkeypatch.setenv('AGENT_POD_UID', pod_uid)
    monkeypatch.setattr(runtime.base, 'credential', lambda path: BOOTSTRAP)
    from agent_service import confinement, linux_stop
    monkeypatch.setattr(confinement, 'ensure_controller_confinement', lambda **kwargs: None)
    response_bytes = httpx.Response(200, json=ECHO)
    measured = observations(value, response_bytes)
    monkeypatch.setattr(runtime.base, 'isolation_preflight', lambda scope: measured['isolation'])
    scopes, constructed = [], []

    class SyntheticStopper:
        def __init__(self, path, attempt, pod, request, *, agent_uid):
            self.attempt, self.pod, self.uid, self.consumed = attempt, pod, agent_uid, False
            scopes.append(self)

        def initialize(self):
            pass

        def stop(self, identity):
            assert identity == self.attempt
            self.consumed = True

        def prove_stopped(self, identity):
            assert self.consumed
            return SimpleNamespace(all_writers_stopped=True, resource_ids=(
                'pod:' + self.pod, 'pid:[12345]', 'uid:' + str(self.uid), 'attempt:' + self.attempt))

        def _uid_processes(self):
            return []

    def construct(config, credentials, scope, stopper, observations):
        constructed.append(config.profile.name)
        observations['capability'] = measured['capability']

        def forbidden(*args, **kwargs):
            pytest.fail('Preflight must never execute the constructed domain driver')

        return SimpleNamespace(execute=forbidden, start=forbidden)

    monkeypatch.setattr(linux_stop, 'LinuxUidStopController', SyntheticStopper)
    monkeypatch.setattr(runtime.base, 'build_candidate_driver', construct)
    with TestClient(runtime.proxy_app(value)) as client:
        class ClientAdapter:
            def __init__(self, **kwargs):
                pass

            def post(self, url, **kwargs):
                kwargs.pop('timeout', None)
                return client.post(url.removeprefix(origin), **kwargs)

            def get(self, url, **kwargs):
                return client.get(url.removeprefix(origin), **kwargs)

            def close(self):
                pass

        monkeypatch.setattr(httpx, 'Client', ClientAdapter)
        assert runtime.run_worker(origin) == 0
        assert constructed == ['ppt.revise.v1']
        assert [scope.uid for scope in scopes] == [value.uid, value.uid + 1]
        assert all(scope.consumed for scope in scopes)
        assert value.recorded['domain_execution_performed'] is False
        output = json.loads(capsys.readouterr().out)
        assert output['status'] == 'runtime_preflight_finished'
        assert BOOTSTRAP not in json.dumps(output) and value.grant not in json.dumps(output)
