"""Offline administrative protocol tests, never native or production acceptance.

The synthetic receipt below tests cryptographic recording only. MockTransport
does not establish actual model connectivity, kernel isolation or engine health.
"""
from copy import deepcopy
import base64
import hashlib
import json
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest

from agent_service import catalog
from agent_service.contracts import ServiceError, WorkerResult, sign_receipt
from agent_service.deploy import acceptance_probe as probe
from agent_service.profiles import implementation
from agent_service.worker_entry import HostConfig, HostSecrets, host_policy
from test_catalog import image_packages  # Explicit synthetic image fixture.


IMAGE = 'registry.invalid/agent-pi@sha256:' + 'a' * 64
BOOTSTRAP = 'offline-administrator-token-' + 'a' * 32


def config(profile='ppt.plan_initial.v1', engine='pi'):
    _, hosts, _ = catalog.generate(pi_image=IMAGE,
        hermes_image='registry.invalid/agent-hermes@sha256:' + 'b' * 64,
        engine='pi', runtime_revision='runtime-v1')
    return HostConfig.model_validate(next(value for value in hosts.values()
        if value['profile']['name'] == profile and value['engine'] == engine))


@pytest.fixture
def authority(image_packages, tmp_path):
    instances = []
    def make(profile='ppt.plan_initial.v1', engine='pi'):
        value = probe.ProbeAuthority(config(profile, engine), tmp_path / str(len(instances)),
            'offline-upstream-caller-token', BOOTSTRAP, 'candidate-worker', 'agent-workers', 2100000000)
        value.proxy.client.close()
        value.proxy.client = httpx.Client(transport=httpx.MockTransport(
            lambda request: pytest.fail('No real upstream access in administrative protocol tests')))
        instances.append(value)
        return value
    yield make
    for value in instances:
        value.proxy.client.close()
        value.store.close()


@pytest.mark.parametrize('profile', sorted(probe.FIXTURE_PROFILES))
def test_fixture_matches_actual_profile_contract(profile):
    task, files = probe.fixture(profile)
    assert implementation(profile).validate_task(task) is None
    assert files and all(type(raw) is bytes for raw in files.values())
    if profile == 'research.revise.v1':
        from scientific_agent.pi_revision_validation import validate_revision_input
        material = json.loads(files['revision-materials.json'])
        verified = validate_revision_input(material['report'], material['items'],
            {key: base64.b64decode(raw) for key, raw in material['artifacts'].items()},
            task['section_id'], task['base_sha256'])
        assert verified['section']['sha256'] == task['section_sha256']


@pytest.mark.parametrize('profile', sorted(probe.PROFILES))
@pytest.mark.parametrize('engine', ['pi', 'hermes'])
def test_candidate_is_exact_disabled_binding_without_production_approval(image_packages, profile, engine):
    original = config(profile, engine).model_dump(mode='json')
    value, policy, binding = probe.candidate_config(deepcopy(original))
    assert value.model_dump(mode='json') == original
    assert policy.enabled is False and binding.acceptance is None
    assert all(not route['enabled'] for route in value.execution_catalog['routes'])
    assert host_policy(value).pins() == policy.pins()


@pytest.mark.parametrize('change', ['enabled_host', 'enabled_route', 'accepted', 'unsupported', 'model'])
def test_admin_factory_refuses_production_or_unrelated_inputs(image_packages, change):
    value = config().model_dump(mode='json')
    if change == 'enabled_host':
        value['profile']['enabled'] = True
    elif change == 'enabled_route':
        # An unrelated route is also forbidden; candidate mode admits no routes.
        value['execution_catalog']['routes'][-1]['enabled'] = True
    elif change == 'accepted':
        from agent_service.registry import binding_fingerprint
        registry = catalog.DeploymentRegistry.from_config(**value['execution_catalog'])
        binding = registry.binding(value['binding_id'], value['binding_revision'])
        contract = registry.contract(binding.profile, binding.profile_revision)
        bundle = registry.bundle(binding.bundle.name, binding.bundle.revision)
        environment = registry.environment(binding.tool_environment.name, binding.tool_environment.revision)
        row = next(row for row in value['execution_catalog']['execution_bindings']
            if row['id'] == binding.id and row['revision'] == binding.revision)
        row['acceptance'] = dict(combination_sha256=binding_fingerprint(contract, bundle, environment, binding),
            suite='offline-fixture.v1', report_sha256='c' * 64)
    elif change == 'unsupported':
        value['profile']['name'] = 'unsupported-profile.v1'
    else:
        value['model']['model'] = 'caller-selected-model'
    with pytest.raises((ServiceError, ValueError, RuntimeError)):
        probe.candidate_config(value)


def test_real_store_allocation_and_grant_are_separate_and_registration_is_one_pod(authority):
    value = authority()
    assert value.directory.joinpath('candidate.sqlite3').is_file()
    grant = value.proxy.authorize(value.grant)
    assert grant['model'] == value.config.model.model
    assert 'offline-upstream-caller-token' not in value.grant
    uid = str(uuid4())
    one = value.register(uid)
    assert value.register(uid) == one
    HostSecrets.model_validate(one['secrets'])
    assert value.store.get_worker_allocation(value.envelope.attempt_id)['pod_uid'] == uid
    with pytest.raises(ServiceError):
        value.register(str(uuid4()))
    assert value.store.get_job('candidate-probe', 'administrator', value.envelope.job_id)['state'] == 'running'
    assert value.lease()['lease_expires_at'] > time.time()


def test_nonempty_probe_state_cannot_reset_paid_execution(authority):
    value = authority()
    with pytest.raises(FileExistsError):
        probe.ProbeAuthority(value.config, value.directory, 'other-upstream', BOOTSTRAP,
            'new-worker', 'agent-workers', value.uid + 3)


@pytest.mark.parametrize('engine', ['pi', 'hermes'])
def test_signed_result_is_only_candidate_and_actual_lease_expiry_revokes_grant(authority, engine):
    value = authority(engine=engine)
    value.register(str(uuid4()))
    result = WorkerResult(state='failed', error_code='offline_synthetic_result')
    result = result.model_copy(update={'stop_receipt': sign_receipt(value.envelope, result, value.receipt_key,
        ('offline-synthetic-writer-proof',), time.time())})
    document = dict(result=result.model_dump(mode='json'),
                    observations={'harness_sha256': probe.harness_sha256(), 'offline_fixture_only': True})
    before = value.config.model_dump(mode='json')
    assert value.result(document) == {'recorded': True, 'lease_expiry_rejected': True}
    with pytest.raises(ServiceError) as failure:
        value.proxy.authorize(value.grant)
    assert failure.value.code == 'model_grant_revoked' and failure.value.status == 401
    evidence = json.loads((value.directory / 'candidate-evidence.json').read_bytes())
    assert evidence['candidate_only'] is True
    assert evidence['production_acceptance_created'] is evidence['production_routes_changed'] is False
    assert evidence['result_state'] == 'failed' and evidence['model_calls'] == []
    assert evidence['pod_termination'] == 'external_operator_observation_required'
    assert evidence['harness_sha256'] == probe.harness_sha256()
    assert value.config.model_dump(mode='json') == before
    assert value.store.get_job('candidate-probe', 'administrator', value.envelope.job_id)['state'] == 'running'
    assert value.store.get_worker_allocation(value.envelope.attempt_id)['stop_observed_at'] is None
    assert not list(value.directory.glob('acceptance.json'))


def test_admin_endpoints_authenticate_and_bound_input_without_leaking_tokens(authority, monkeypatch):
    value = authority()
    with TestClient(probe.proxy_app(value)) as client:
        response = client.post('/probe/register', json={'pod_uid': str(uuid4())})
        assert response.status_code == 401
        assert BOOTSTRAP not in response.text and 'offline-upstream-caller-token' not in response.text
        headers = {'Authorization': 'Bearer ' + BOOTSTRAP}
        assert client.post('/probe/register', headers=headers, json=[]).status_code == 422
        assert client.post('/probe/register', headers=headers, json={}).status_code == 422
        assert client.post('/probe/register', headers=headers, json={'pod_uid': 'invalid'}).status_code == 422
        assert client.post('/probe/register', headers=headers, content=b'{').status_code == 422
        monkeypatch.setattr(probe, 'MAX_WIRE', 64)
        assert client.post('/probe/register', headers=headers, content=b'x' * 65).status_code == 413
        assert client.get('/probe/evidence', headers=headers).json()['candidate_only'] is True


def test_candidate_rejection_diagnostic_excludes_credentials_query_and_content(authority, capsys):
    value = authority()
    value.register(str(uuid4()))
    with TestClient(probe.proxy_app(value)) as client:
        response = client.post('/v1/responses?private=do-not-log-query',
            headers={'Authorization': 'Bearer ' + value.grant},
            json={'model': value.config.model.model, 'input': 'do-not-log-body', 'max_output_tokens': 0})
    assert response.status_code == 422
    assert response.json() == {'error': {'code': 'model_output_bound_invalid'}}
    output = capsys.readouterr().out
    assert json.loads(output) == {'event': 'candidate_request_rejected', 'route': '/v1/responses',
                                 'status': 422, 'code': 'model_output_bound_invalid'}
    assert not any(secret in output for secret in (value.grant, BOOTSTRAP,
        'offline-upstream-caller-token', 'do-not-log-query', 'do-not-log-body'))
    with value.store.engine.connect() as conn:
        assert not conn.execute(value.request_table.select()).all()


@pytest.mark.parametrize('status,content,expected', [
    (200, {'status': 'completed', 'output': [{'type': 'message', 'content': [
        {'type': 'output_text', 'text': 'CANDIDATE_MODEL_OK'}]}]}, True),
    (200, {'status': 'incomplete', 'output': [{'type': 'message', 'content': [
        {'type': 'output_text', 'text': 'CANDIDATE_MODEL_OK'}]}]}, False),
    (200, {'error': 'CANDIDATE_MODEL_OK'}, False),
    (200, {'status': 'completed', 'output': [{'type': 'message', 'content': [
        {'type': 'output_text', 'text': 'prefix CANDIDATE_MODEL_OK'}]}]}, False),
    (401, {'status': 'completed', 'output': []}, False),
])
def test_model_probe_requires_actual_completed_exact_output(status, content, expected):
    assert probe.response_completed(httpx.Response(status, json=content)) is expected


def test_render_isolated_one_shot_pods_never_emit_secrets_or_overlap_production(image_packages):
    value = config()
    before = value.model_dump(mode='json')
    manifest = probe.render_probe(value, name='candidate-unique', worker_image=IMAGE,
        node='reviewed-node', uid_start=2100000000, bootstrap_secret='probe-bootstrap',
        upstream_secret='probe-upstream')
    assert value.model_dump(mode='json') == before
    assert not any(item['kind'] in {'Secret', 'Deployment'} for item in manifest['items'])
    pods = [item for item in manifest['items'] if item['kind'] == 'Pod']
    assert len(pods) == 2
    proxy_pod = next(item for item in pods if item['metadata']['namespace'] == 'agent-service')
    worker = next(item for item in pods if item['metadata']['namespace'] == 'agent-workers')
    assert proxy_pod['metadata']['labels']['app'] == 'agent-model-proxy'
    assert proxy_pod['metadata']['labels']['app.kubernetes.io/name'] != 'agent-execution-service'
    assert worker['metadata']['labels']['app'] == 'agent-capsule'
    assert 'upstream' not in {volume['name'] for volume in worker['spec']['volumes']}
    assert 'upstream' in {volume['name'] for volume in proxy_pod['spec']['volumes']}
    for pod in pods:
        assert pod['spec']['automountServiceAccountToken'] is False
        assert pod['spec']['restartPolicy'] == 'Never'
        assert all(pod['spec'][key] is False for key in ('hostPID', 'hostIPC', 'hostNetwork', 'shareProcessNamespace'))
        security = pod['spec']['containers'][0]['securityContext']
        assert security.get('privileged', False) is False
        assert security['readOnlyRootFilesystem'] is True
        assert pod['spec']['securityContext']['seccompProfile']['type'] == 'RuntimeDefault'
    security = worker['spec']['containers'][0]['securityContext']
    assert security['appArmorProfile'] == {'type': 'Localhost', 'localhostProfile': 'agent-capsule-inputs'}
    assert security['capabilities']['drop'] == ['ALL'] and 'SYS_ADMIN' in security['capabilities']['add']
    assert worker['spec']['nodeSelector'] == {'kubernetes.io/hostname': 'reviewed-node'}
    for item in manifest['items']:
        if item['kind'] == 'ConfigMap':
            mounted = json.loads(item['data']['config.json'])
            assert mounted['model']['proxy_base_url'] == 'http://candidate-unique.agent-service.svc.cluster.local:8080/v1'
            assert all(not route['enabled'] for route in mounted['execution_catalog']['routes'])
            assert hashlib.sha256(item['data']['acceptance_probe.py'].encode()).hexdigest() == probe.harness_sha256()


@pytest.mark.parametrize('field,value', [('worker_image', 'registry.invalid/pi:latest'),
    ('worker_image', IMAGE.replace('a' * 64, 'c' * 64)), ('uid_start', 0), ('name', 'production/proxy')])
def test_render_requires_exact_image_and_explicit_reserved_scope(image_packages, field, value):
    options = dict(name='candidate-unique', worker_image=IMAGE, node='reviewed-node', uid_start=2100000000,
        bootstrap_secret='probe-bootstrap', upstream_secret='probe-upstream')
    options[field] = value
    with pytest.raises(ServiceError):
        probe.render_probe(config(), **options)
