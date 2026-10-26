"""Deployment catalogs remain data, with explicit cross-record approval checks."""
from __future__ import annotations

import base64
from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest

from agent_service.api import create_app
from agent_service.config import ConfigurationError, load_configuration
from registry_fixtures import make_catalog


TOKEN = 'offline-registry-caller-token-00000000000000'
WORKER_TOKEN = 'offline-registry-worker-token-00000000000000'


def public(*, enabled=False):
    return {**make_catalog(enabled=enabled),
            'callers': [{'tenant': 'internal', 'caller': 'workbench',
                         'profiles': ['workspace-task.v1'], 'token_ref': 'workbench'}]}


def files(tmp_path, wire):
    config_path = tmp_path / 'config.json'
    secret_path = tmp_path / 'private.json'
    config_path.write_text(json.dumps(wire), encoding='utf-8')
    secret_path.write_text(json.dumps({
        'database_url': 'postgresql+psycopg://agent:private-database-password@invalid.internal/agent',
        'tokens': {'workbench': TOKEN, 'worker': WORKER_TOKEN},
        'receipt_keys': {'worker': base64.b64encode(b'offline-registry-receipt-key-00000000').decode()},
    }), encoding='utf-8')
    return config_path, secret_path


def test_catalogs_resolve_into_one_content_bound_execution_policy(tmp_path):
    wire = public()
    loaded = load_configuration(*files(tmp_path, wire))
    catalog = loaded.deployment_registry
    policy = loaded.registry.policy('workspace-task.v1', 'v1')
    assert catalog.profiles() is loaded.registry
    assert catalog.contract('workspace-task.v1', 'v1').task_schema == 'workspace-text.v1'
    assert catalog.bundle('workspace-instructions', 'bundle-v1').license == 'MIT'
    assert catalog.environment('workspace-tools', 'tools-v1').tools == ('read', 'write')
    assert catalog.binding('workspace-pi', 'binding-v1').model_policy.api_mode == 'openai-responses'
    pins = policy.execution_binding
    assert pins is not None and pins.profile == 'workspace-task.v1'
    assert pins.bundle_name == 'workspace-instructions' and pins.tool_environment_name == 'workspace-tools'
    summary = loaded.summary()
    assert summary['schema_version'] == 2 and summary['catalog'] == catalog.summary()
    assert summary['catalog']['task_contracts'] == summary['catalog']['execution_bindings'] == 1
    assert summary['catalog']['combinations'][0]['approval_record_present'] is False
    assert 'private-database-password' not in json.dumps(summary)
    assert TOKEN not in json.dumps(summary)


@pytest.mark.parametrize('version', [1, '2', True, 3])
def test_runtime_config_version_one_is_not_supported_or_silently_upgraded(tmp_path, version):
    wire = public()
    wire['schema_version'] = version
    with pytest.raises(ConfigurationError, match='configuration_invalid'):
        load_configuration(*files(tmp_path, wire))


def test_removed_combined_profile_configuration_is_rejected_even_with_version_two(tmp_path):
    wire = public()
    wire['profiles'] = [{'name': 'workspace-task.v1', 'engine': 'pi'}]
    with pytest.raises(ConfigurationError, match='configuration_invalid'):
        load_configuration(*files(tmp_path, wire))


@pytest.mark.parametrize('profiles', [['unknown-task.v1'], ['../contract'],
                                     ['workspace-task.v1', 'workspace-task.v1'], 'workspace-task.v1'])
def test_caller_scopes_must_be_registered_ordinary_contract_names(tmp_path, profiles):
    wire = public()
    wire['callers'][0]['profiles'] = profiles
    with pytest.raises(ConfigurationError, match='configuration_invalid'):
        load_configuration(*files(tmp_path, wire))


def test_empty_caller_scope_remains_an_explicit_revocation_state(tmp_path):
    wire = public()
    wire['callers'][0]['profiles'] = []
    loaded = load_configuration(*files(tmp_path, wire))
    assert loaded.api_settings.principals[TOKEN].profiles == frozenset()


def test_disabled_route_default_does_not_require_approval_but_preserves_catalog(tmp_path):
    wire = public()
    wire['routes'][0].pop('enabled')
    loaded = load_configuration(*files(tmp_path, wire))
    assert loaded.registry.policy('workspace-task.v1', 'v1').enabled is False
    assert loaded.deployment_registry.summary()['combinations'][0]['approval_record_present'] is False


@pytest.mark.parametrize('section', ['task_contracts', 'capability_bundles', 'tool_environments',
                                    'execution_bindings'])
def test_missing_cross_record_dependencies_rejected_even_when_route_disabled(tmp_path, section):
    wire = public()
    wire[section] = []
    with pytest.raises(ConfigurationError, match='configuration_invalid'):
        load_configuration(*files(tmp_path, wire))


@pytest.mark.parametrize('mutate', [
    lambda wire: wire['capability_bundles'][0].update(loader='private.module:load'),
    lambda wire: wire['capability_bundles'][0]['resources'][0].update(name='../instructions.txt'),
    lambda wire: wire['tool_environments'][0].update(tools=['read']),
    lambda wire: wire['tool_environments'][0].update(capabilities=[]),
    lambda wire: wire['task_contracts'][0].update(required_model_capabilities=['text', 'tool_calls', 'vision']),
    lambda wire: wire['execution_bindings'][0]['model_policy'].update(capabilities=['text']),
    lambda wire: wire['execution_bindings'][0]['model_policy'].update(endpoint='https://private-upstream.invalid'),
    lambda wire: wire['execution_bindings'][0]['model_policy'].update(api_key='private-provider-token'),
    lambda wire: wire['execution_bindings'][0]['model_policy'].update(api_mode='openai-completions'),
    lambda wire: wire['task_contracts'][0].update(task_schema='ppt-five-stage.v1'),
    lambda wire: wire['task_contracts'][0].update(name='ppt.generate.v1'),
])
def test_registry_receives_whole_records_and_rejects_incompatible_or_unreviewed_code(tmp_path, mutate):
    wire = public()
    mutate(wire)
    with pytest.raises(ConfigurationError) as error:
        load_configuration(*files(tmp_path, wire))
    assert error.value.code == 'configuration_invalid'
    assert 'private-provider-token' not in str(error.value)


def test_enabled_route_requires_matching_combination_approval(tmp_path):
    wire = public()
    wire['routes'][0]['enabled'] = True
    with pytest.raises(ConfigurationError, match='configuration_invalid'):
        load_configuration(*files(tmp_path, wire))
    approved = public(enabled=True)
    loaded = load_configuration(*files(tmp_path, approved))
    assert loaded.registry.policy('workspace-task.v1', 'v1').enabled is True
    # The local fixture's approval record is deployment data, not a live Worker
    # capability, physical isolation report, or native model acceptance.
    with TestClient(create_app(loaded.api_settings, None, loaded.registry)) as client:
        capabilities = client.get('/v1/capabilities', headers={'Authorization': 'Bearer ' + TOKEN}).json()['profiles'][0]
    assert capabilities['configured'] is True and capabilities['ready'] is False
    assert capabilities['publication_authorized'] is False


@pytest.mark.parametrize('mutate', [
    lambda wire: wire['capability_bundles'][0]['resources'][0].update(sha256='b' * 64),
    lambda wire: wire['capability_bundles'][0].update(license='Apache-2.0'),
    lambda wire: wire['capability_bundles'][0].update(source='other-reviewed-source'),
    lambda wire: wire['tool_environments'][0].update(image_digest='sha256:' + 'b' * 64),
    lambda wire: wire['tool_environments'][0].update(runtime_revision='other-runtime'),
    lambda wire: wire['execution_bindings'][0].update(adapter_revision='other-adapter'),
    lambda wire: wire['execution_bindings'][0]['model_policy'].update(provider='other-provider'),
    lambda wire: wire['execution_bindings'][0]['model_policy'].update(model='other-model'),
])
def test_old_acceptance_is_invalid_after_any_execution_resource_changes(tmp_path, mutate):
    wire = public(enabled=True)
    mutate(wire)
    with pytest.raises(ConfigurationError, match='configuration_invalid'):
        load_configuration(*files(tmp_path, wire))


@pytest.mark.parametrize('section', ['task_contracts', 'capability_bundles', 'tool_environments',
                                    'execution_bindings', 'routes'])
def test_catalog_arrays_are_bounded_before_registry_resolution(tmp_path, section):
    wire = public()
    wire[section] = [{} for _ in range(101)]
    with pytest.raises(ConfigurationError, match='configuration_invalid'):
        load_configuration(*files(tmp_path, wire))


def test_runtime_config_checks_registered_but_unrouted_contract_scope(tmp_path):
    wire = public()
    wire['routes'] = []
    loaded = load_configuration(*files(tmp_path, wire))
    assert loaded.registry.policies() == ()
    assert loaded.api_settings.principals[TOKEN].profiles == frozenset({'workspace-task.v1'})
    assert loaded.deployment_registry.contract('workspace-task.v1', 'v1') is not None
