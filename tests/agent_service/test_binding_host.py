"""Fixed catalog, real bundled bytes and capsule identity; no native/model I/O."""
import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest

from agent_service.contracts import ExecutionEnvelope, ServiceError
from agent_service.kubernetes_provider import KubernetesPool, KubernetesWorkerAdapter
from agent_service.registry import DeploymentRegistry, binding_fingerprint
from agent_service.worker_entry import (CapsuleScope, HostConfig, HostSecrets,
    WorkerConfigError, accepted_host, build_pi_driver, host_policy)
from registry_fixtures import make_catalog


ATTEMPT, POD, JOB = (str(UUID(int=value)) for value in (1, 2, 3))
IMAGE = 'sha256:' + 'b' * 64
REQUEST = 'a' * 64


def approve(catalog):
    c, b, e, x = (catalog[key][0] for key in ('task_contracts', 'capability_bundles',
                                           'tool_environments', 'execution_bindings'))
    x['acceptance'] = {'combination_sha256': binding_fingerprint(c, b, e, x),
        'suite': 'workspace-contract.v1',
        'report_sha256': hashlib.sha256(b'Offline synthetic approval only').hexdigest()}
    catalog['routes'][0]['enabled'] = True
    return catalog


def catalog():
    from agent_service.worker_entry import PROFILE_BUNDLE_REVISION, RESOURCE_ROOT
    value = make_catalog(image_digest=IMAGE)
    value.pop('schema_version')
    value['task_contracts'][0]['max_timeout_seconds'] = 60
    bundle = value['capability_bundles'][0]
    bundle['revision'] = PROFILE_BUNDLE_REVISION
    bundle['resources'] = [{'name': 'CONTRACT.md',
        'sha256': hashlib.sha256((RESOURCE_ROOT / 'CONTRACT.md').read_bytes()).hexdigest()}]
    binding = value['execution_bindings'][0]
    binding['bundle']['revision'] = PROFILE_BUNDLE_REVISION
    binding['model_policy'].update(provider='custom', model='fixed-model')
    return approve(value)


def config(value=None, **changes):
    wire = dict(schema_version=1, engine='pi', profile=dict(name='workspace-task.v1', revision='v1',
        runtime_revision='runtime-v1', image_digest=IMAGE, enabled=True, max_timeout_seconds=60),
        model=dict(provider='custom', model='fixed-model', api='openai-responses',
                   proxy_base_url='http://task-proxy.agent-service.svc.cluster.local:8080/v1'),
        execution_catalog=catalog() if value is None else value)
    wire.update(changes)
    return HostConfig.model_validate(wire)


def scope(c):
    return CapsuleScope(attempt_id=ATTEMPT, pod_uid=POD, request_sha256=REQUEST,
        agent_uid=1000001, preview_uid=1000002,
        execution_binding_sha256=host_policy(c).execution_binding.sha256)


def scope_env(c):
    return dict(AGENT_ATTEMPT_ID=ATTEMPT, AGENT_POD_UID=POD, AGENT_REQUEST_SHA256=REQUEST,
        AGENT_ENGINE='pi', AGENT_PROFILE_NAME='workspace-task.v1', AGENT_PROFILE_REVISION='v1',
        AGENT_RUNTIME_REVISION='runtime-v1', AGENT_RUNTIME_IMAGE_DIGEST=IMAGE,
        AGENT_RUNTIME_UID='1000001', AGENT_PREVIEW_UID='1000002',
        AGENT_EXECUTION_BINDING_SHA256=host_policy(c).execution_binding.sha256)


def secrets():
    return HostSecrets(worker_token='offline-worker-token-000000000000000000', receipt_key_hex='c' * 64,
        model_proxy_token='offline-model-proxy-token-0000000000000000',
        model_proxy_attempt_id=ATTEMPT, model_proxy_request_sha256=REQUEST,
        model_proxy_expires_at=200.0)


def test_host_policy_resolves_the_exact_fixed_combination():
    c = config()
    expected = DeploymentRegistry.from_config(**c.execution_catalog).profiles().policy('workspace-task.v1', 'v1')
    assert host_policy(c).pins() == expected.pins()
    assert host_policy(c).supports_inputs is False


@pytest.mark.parametrize('field,value', [('runtime_revision', 'different-runtime'),
    ('image_digest', 'sha256:' + 'd' * 64), ('enabled', False), ('max_timeout_seconds', 61)])
def test_host_summary_cannot_override_the_catalog(field, value):
    c = config()
    bad = config(profile={**c.profile.model_dump(), field: value})
    with pytest.raises(WorkerConfigError):
        host_policy(bad)


def test_engine_must_match_catalog_and_catalog_must_be_complete():
    with pytest.raises(WorkerConfigError):
        host_policy(config(engine='hermes'))
    for key in catalog():
        incomplete = catalog()
        del incomplete[key]
        with pytest.raises(ValueError):
            config(incomplete)
    with pytest.raises(ValueError):
        config({**catalog(), 'module': 'arbitrary.code'})


def test_binding_scope_is_mandatory_and_exact_in_downward_environment():
    c = config()
    assert CapsuleScope.from_env(c, scope_env(c)) == scope(c)
    for value in (None, '0' * 64):
        env = scope_env(c)
        if value is None:
            del env['AGENT_EXECUTION_BINDING_SHA256']
        else:
            env['AGENT_EXECUTION_BINDING_SHA256'] = value
        with pytest.raises(WorkerConfigError):
            CapsuleScope.from_env(c, env)


def test_no_catalog_fixture_cannot_claim_a_new_binding():
    c = config(execution_catalog=None)
    assert host_policy(c).execution_binding is None
    with pytest.raises(WorkerConfigError):
        CapsuleScope.from_env(c, scope_env(config()))
    bound = scope(config())
    assert build_pi_driver(c, secrets(), bound, object(), acceptance=True, now=lambda: 100).ready is False


def test_catalog_grant_alone_cannot_replace_capsule_binding_scope():
    c = config()
    old_scope = CapsuleScope(attempt_id=ATTEMPT, pod_uid=POD, request_sha256=REQUEST)
    assert build_pi_driver(c, secrets(), old_scope, object(), acceptance=True, now=lambda: 100).ready is False


def test_operator_acceptance_must_cover_binding_beyond_old_base_pins(monkeypatch, tmp_path):
    import agent_service.worker_entry as module
    c = config()
    evidence = tmp_path / 'acceptance-evidence.json'
    evidence.write_bytes(b'{"offline_fixture_only":true}')
    record = dict(schema_version=1, engine='pi', profile=c.profile.name,
        profile_revision=c.profile.revision, runtime_revision=c.profile.runtime_revision,
        image_digest=IMAGE, model_api='openai-responses', checks=dict(capability=True,
        model_round_trip=True, isolation=True, model_proxy_lease=True),
        evidence_sha256=hashlib.sha256(evidence.read_bytes()).hexdigest())
    values = {'acceptance.json': record, 'acceptance-evidence.json': {'offline_fixture_only': True}}
    monkeypatch.setattr(module, '_read_operator_json', lambda path: values[Path(path).name])
    path = tmp_path / 'acceptance.json'
    assert accepted_host(c, path) is False
    record['execution_binding_sha256'] = host_policy(c).execution_binding.sha256
    assert accepted_host(c, path) is True
    record['execution_binding_sha256'] = 'd' * 64
    assert accepted_host(c, path) is False


def fake_runtime(monkeypatch):
    captured = []
    class Runtime:
        def __init__(self, settings, *, profile):
            captured.append((settings, profile))
        def health(self):
            return {'ready': False, 'fixture_mode': False}
    monkeypatch.setattr('agent_service.worker_entry.PiRuntime', Runtime)
    return captured


def test_bound_driver_uses_measured_builtin_bundle_and_dedicated_proxy(monkeypatch):
    c = config()
    captured = fake_runtime(monkeypatch)
    driver = build_pi_driver(c, secrets(), scope(c), object(), acceptance=True, now=lambda: 100)
    assert driver.ready is False  # Synthetic acceptance never replaces runtime health.
    settings, profile = captured[0]
    assert settings.skill_bundle_revision == 'workspace-text-v1'
    assert settings.model_api == 'openai-responses'
    assert (settings.provider, settings.model) == ('custom', 'fixed-model')
    assert set(settings.model_env) == {'AGENT_MODEL_API_KEY'}
    assert profile.allowed_tools == ('read', 'write') and profile.load_skills is False


@pytest.mark.parametrize('change', ['resource_hash', 'resource_name', 'extra_resource',
    'bundle_revision', 'provider', 'model', 'adapter_revision', 'vision'])
def test_accepted_metadata_does_not_expand_shipped_driver_capabilities(monkeypatch, change):
    value = catalog()
    bundle, binding = value['capability_bundles'][0], value['execution_bindings'][0]
    if change == 'resource_hash':
        bundle['resources'][0]['sha256'] = 'd' * 64
    elif change == 'resource_name':
        bundle['resources'][0]['name'] = 'different.txt'
    elif change == 'extra_resource':
        bundle['resources'].append({'name': 'extra.txt', 'sha256': 'd' * 64})
    elif change == 'bundle_revision':
        bundle['revision'] = 'different-bundle'
        binding['bundle']['revision'] = bundle['revision']
    elif change in {'provider', 'model'}:
        binding['model_policy'][change] = 'different-value'
    elif change == 'adapter_revision':
        binding[change] = 'unimplemented-adapter'
    elif change == 'vision':
        value['task_contracts'][0]['required_model_capabilities'].append('vision')
        binding['model_policy']['capabilities'].append('vision')
    c = config(approve(value))
    captured = fake_runtime(monkeypatch)
    with pytest.raises(WorkerConfigError):
        build_pi_driver(c, secrets(), scope(c), object(), acceptance=True, now=lambda: 100)
    assert captured == []


@pytest.mark.parametrize('update_manifest', [False, True])
def test_image_file_bytes_are_checked_against_descriptor_and_binding(monkeypatch, tmp_path, update_manifest):
    import agent_service.worker_entry as module
    c = config()
    old_manifest = json.loads((module.RESOURCE_ROOT / 'manifest.json').read_text())
    (tmp_path / 'CONTRACT.md').write_bytes(b'Changed image instructions\n')
    if update_manifest:
        old_manifest['local_files'][0]['sha256'] = hashlib.sha256((tmp_path / 'CONTRACT.md').read_bytes()).hexdigest()
    (tmp_path / 'manifest.json').write_text(json.dumps(old_manifest))
    monkeypatch.setattr(module, 'RESOURCE_ROOT', tmp_path)
    captured = fake_runtime(monkeypatch)
    with pytest.raises(WorkerConfigError):
        build_pi_driver(c, secrets(), scope(c), object(), acceptance=True, now=lambda: 100)
    assert captured == []


class Kube:
    def __init__(self, c):
        self.config = c
    def request(self, method, path, *args, **kwargs):
        assert method == 'GET' and '/configmaps/' in path
        return {'immutable': True, 'data': {'config.json': json.dumps(self.config.model_dump(mode='json'))}}


def adapter(c):
    pool = KubernetesPool('pi-pool', host_policy(c), 'agent-service', 'registry.example/pi@' + IMAGE,
        'fixed-worker', ('10.244.0.0/16',), enabled=True, uid_start=1000001)
    return KubernetesWorkerAdapter(object(), pool, Kube(c), 'offline-pool-token-000000000000000000', b'c' * 32)


def envelope(c, *, bound=True):
    pins = host_policy(c).pins()
    if not bound:
        pins.pop('execution_binding')
    return ExecutionEnvelope(job_id=JOB, attempt_id=ATTEMPT, fence=1,
        **pins, deadline_at=200, lease_expires_at=150, task={'prompt': 'offline'})


def test_kubernetes_host_catalog_matches_full_pool_binding():
    c = config()
    a = adapter(c)
    a._ensure_host_config()
    changed = catalog()
    changed['execution_bindings'][0]['model_policy']['model'] = 'different-model'
    a.kube.config = config(approve(changed))
    with pytest.raises(ServiceError) as error:
        a._ensure_host_config()
    assert error.value.code == 'worker_host_config_invalid'
    a.kube.config = config(execution_catalog=None)
    with pytest.raises(ServiceError):
        a._ensure_host_config()


def test_kubernetes_manifest_pins_binding_in_scope_and_rejects_legacy_envelope():
    c = config()
    a = adapter(c)
    e = envelope(c)
    pod, secret = a._manifest(e)
    env = {item['name']: item.get('value') for item in pod['spec']['containers'][0]['env']}
    assert env['AGENT_EXECUTION_BINDING_SHA256'] == e.execution_binding.sha256
    assert 'AGENT_MODEL_API_KEY' not in env
    assert set(secret['data']) == {'worker.json'}
    with pytest.raises(ServiceError) as error:
        a._manifest(envelope(c, bound=False))
    assert error.value.code == 'execution_pins_mismatch'
