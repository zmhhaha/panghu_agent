"""Offline model admission for native read-image roles; no provider calls."""
from copy import deepcopy

import pytest

from agent_service.catalog import _contract
from agent_service.profiles import implementation
from agent_service.registry import (TaskContract, binding_fingerprint,
    required_model_capabilities)
from registry_fixtures import make_catalog


@pytest.mark.parametrize('name', ['video.animation-observe.v1', 'video.animation-review.v1'])
def test_read_write_visual_roles_cannot_be_registered_as_text_only(name):
    handler = implementation(name)
    assert handler.required_tools == ('read', 'write')
    assert set(required_model_capabilities(handler)) == {'text', 'tool_calls', 'vision'}
    contract = dict(name=name, revision='1', task_schema=handler.task_schema,
        output_schema=handler.output_schema, required_tools=['read', 'write'],
        required_model_capabilities=['text', 'tool_calls'])
    with pytest.raises(ValueError, match='requires a vision-capable model policy'):
        TaskContract.model_validate(contract)
    contract['required_model_capabilities'].append('vision')
    parsed = TaskContract.model_validate(contract)
    assert parsed.required_tools == ('read', 'write')
    assert parsed.required_model_capabilities == ('text', 'tool_calls', 'vision')
    assert _contract(name).required_model_capabilities == parsed.required_model_capabilities


@pytest.mark.parametrize('name', ['video.animation-observe.v1', 'video.animation-review.v1'])
def test_visual_contract_rejects_text_only_model_binding_without_widening_tools(name):
    handler = implementation(name)
    configured = make_catalog()
    contract = deepcopy(configured['task_contracts'][0])
    contract.update(name=name, revision='1', task_schema=handler.task_schema,
        output_schema=handler.output_schema, required_model_capabilities=['text', 'tool_calls', 'vision'])
    bundle = configured['capability_bundles'][0]
    environment = configured['tool_environments'][0]
    environment['capabilities'] = [handler.output_schema]
    binding = configured['execution_bindings'][0]
    binding.update(profile=name, profile_revision='1')
    with pytest.raises(ValueError, match='Incompatible execution combination'):
        binding_fingerprint(contract, bundle, environment, binding)
    binding['model_policy']['capabilities'].append('vision')
    assert len(binding_fingerprint(contract, bundle, environment, binding)) == 64
    assert environment['tools'] == ['read', 'write']


@pytest.mark.parametrize('name,expected', [
    ('workspace-task.v1', {'text', 'tool_calls'}),
    ('video.animation-experiment.v1', {'text', 'tool_calls'}),
    ('video.animation-create.v2', {'text', 'tool_calls'}),
    ('ppt.generate.v1', {'text', 'tool_calls', 'vision'}),
    ('ppt.revise.v1', {'text', 'tool_calls', 'vision'}),
])
def test_legacy_capability_bindings_remain_compatible(name, expected):
    assert set(required_model_capabilities(implementation(name))) == expected
