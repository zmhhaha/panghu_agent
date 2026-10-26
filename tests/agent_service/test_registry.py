"""Behavioral admission checks for reviewed, independent capability bindings."""
from copy import deepcopy

import pytest

from agent_service.contracts import (ExecutionBindingPins, JobRequest, Principal,
                                     ServiceError)
from agent_service.registry import DeploymentRegistry, binding_fingerprint
from registry_fixtures import make_catalog


def registry(value):
    return DeploymentRegistry.from_config(**{k: v for k, v in value.items() if k != 'schema_version'})


def policy(value):
    return registry(value).profiles().policy('workspace-task.v1', 'v1')


def approve(value):
    binding = value['execution_bindings'][0]
    binding['acceptance'] = {
        'combination_sha256': binding_fingerprint(value['task_contracts'][0],
            value['capability_bundles'][0], value['tool_environments'][0], binding),
        'suite': 'workspace-contract.v1', 'report_sha256': '1' * 64}


def test_bundle_is_independent_of_engine_and_immutable_after_resolution():
    pi, hermes = make_catalog('pi', True), make_catalog('hermes', True)
    a, b = policy(pi), policy(hermes)
    assert a.execution_binding.bundle_sha256 == b.execution_binding.bundle_sha256
    assert a.execution_binding.contract_sha256 == b.execution_binding.contract_sha256
    assert a.execution_binding.combination_sha256 != b.execution_binding.combination_sha256
    before = a.pins()
    pi['capability_bundles'][0]['resources'][0]['sha256'] = 'b' * 64
    pi['execution_bindings'][0]['model_policy']['model'] = 'a-different-model'
    assert a.pins() == before
    with pytest.raises(ValueError):
        a.execution_binding.bundle_sha256 = 'c' * 64
    saved = registry(hermes).bundle('workspace-instructions', 'bundle-v1')
    assert isinstance(saved.resources, tuple)
    with pytest.raises(ValueError):
        saved.resources[0].sha256 = 'c' * 64


@pytest.mark.parametrize('change', [
    lambda c: c['capability_bundles'][0]['resources'][0].update(sha256='e' * 64),
    lambda c: c['capability_bundles'][0].update(source='another-reviewed-source'),
    lambda c: c['tool_environments'][0].update(image_digest='sha256:' + 'b' * 64),
    lambda c: c['tool_environments'][0].update(runtime_revision='another-runtime'),
    lambda c: c['execution_bindings'][0].update(adapter_revision='adapter-v2'),
    lambda c: c['execution_bindings'][0].update(engine='hermes'),
    lambda c: c['execution_bindings'][0]['model_policy'].update(model='new-model'),
    lambda c: c['execution_bindings'][0]['model_policy'].update(provider='new-proxy'),
    lambda c: c['task_contracts'][0].update(max_timeout_seconds=300),
])
def test_acceptance_cannot_be_reused_after_combination_changes(change):
    value = make_catalog(enabled=True)
    change(value)
    with pytest.raises(ValueError, match='Stale combination acceptance'):
        registry(value)


def test_acceptance_reference_changes_bound_identity_but_not_combination():
    value = make_catalog(enabled=True)
    before = policy(value).execution_binding
    value['execution_bindings'][0]['acceptance']['report_sha256'] = '2' * 64
    after = policy(value).execution_binding
    assert before.combination_sha256 == after.combination_sha256
    assert before.acceptance_sha256 != after.acceptance_sha256
    assert before.sha256 != after.sha256


def test_disabled_registration_is_distinct_from_an_approved_enabled_route():
    value = make_catalog()
    catalog = registry(value)
    assert not policy(value).enabled
    assert catalog.summary()['combinations'][0]['approval_record_present'] is False
    value['routes'][0]['enabled'] = True
    with pytest.raises(ValueError, match='requires reviewed acceptance'):
        registry(value)
    approve(value)
    assert policy(value).enabled
    assert policy(value).supports_inputs is False


@pytest.mark.parametrize('change', [
    lambda c: c['execution_bindings'][0]['model_policy'].update(capabilities=['text']),
    lambda c: c['tool_environments'][0].update(tools=['read']),
    lambda c: c['task_contracts'][0].update(required_tools=['read', 'execute']),
    lambda c: c['execution_bindings'][0]['bundle'].update(revision='missing'),
    lambda c: c['execution_bindings'][0]['tool_environment'].update(name='missing'),
    lambda c: c['routes'][0].update(binding_id='missing'),
    lambda c: c['capability_bundles'][0]['resources'][0].update(name='../instructions.txt'),
    lambda c: c['capability_bundles'][0]['resources'].append(deepcopy(c['capability_bundles'][0]['resources'][0])),
    lambda c: c['execution_bindings'][0]['model_policy'].update(api_mode='chat-completions'),
    lambda c: c['execution_bindings'][0]['model_policy'].update(api_key='do-not-accept-keys'),
    lambda c: c['task_contracts'].append(deepcopy(c['task_contracts'][0])),
    lambda c: c['execution_bindings'].append(deepcopy(c['execution_bindings'][0])),
    lambda c: c['routes'].append(deepcopy(c['routes'][0])),
])
def test_incompatible_or_executable_registry_material_is_rejected(change):
    value = make_catalog()
    change(value)
    with pytest.raises(ValueError):
        registry(value)


@pytest.mark.parametrize('name,schema', [
    ('ppt.generate.v1', 'workspace-text.v1'),
    ('research.generate.v1', 'workspace-text.v1'),
    ('workspace-task.v1', 'arbitrary.module:validate'),
    ('workspace-task.v1', 'ppt-generate.v1'),
])
def test_unimplemented_domain_contract_cannot_be_activated_as_text(name, schema):
    value = make_catalog()
    value['task_contracts'][0].update(name=name, task_schema=schema)
    value['execution_bindings'][0]['profile'] = name
    value['routes'][0].update(profile=name, enabled=True)
    with pytest.raises(ValueError):
        registry(value)


def test_contract_selects_its_reviewed_task_validator_and_rejects_inputs():
    catalog = registry(make_catalog(enabled=True)).profiles()
    principal = Principal('internal', 'business', frozenset({'workspace-task.v1'}))
    request = JobRequest(profile='workspace-task.v1', profile_revision='v1',
                         business_ref='phase-one', task={'prompt': 'A bounded text request'})
    assert catalog.resolve(principal, request).execution_binding is not None
    for task in ({'prompt': 'x', 'command': 'sh'}, {'slides': []}, {'prompt': True}):
        with pytest.raises(ServiceError, match='invalid_workspace_task'):
            catalog.resolve(principal, request.model_copy(update={'task': task}))


def test_binding_hashes_authenticate_both_approval_and_complete_combination():
    original = policy(make_catalog(enabled=True)).execution_binding
    data = original.model_dump()
    data['bundle_sha256'] = '9' * 64
    with pytest.raises(ValueError):
        ExecutionBindingPins.model_validate(data)
    data = original.model_dump()
    data['acceptance_sha256'] = '9' * 64
    with pytest.raises(ValueError):
        ExecutionBindingPins.model_validate(data)


def test_semantically_unordered_capabilities_have_stable_fingerprints():
    first = make_catalog(enabled=True)
    second = deepcopy(first)
    for k in ('required_tools', 'required_model_capabilities'):
        second['task_contracts'][0][k].reverse()
    second['tool_environments'][0]['tools'].reverse()
    second['execution_bindings'][0]['model_policy']['capabilities'].reverse()
    assert policy(first).pins() == policy(second).pins()
