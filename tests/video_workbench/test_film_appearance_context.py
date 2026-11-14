"""Exact source delivery tests, not animation synthesis or visual acceptance."""
from copy import deepcopy
import hashlib
import json

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_animation_film_module_contract as contract
from video_workbench import film_appearance_context as appearance
from video_workbench.creative_film_module_contract import predecessor_from_package
from test_creative_film_module_contract import stage, collected, raw_result, core
from test_film_creation_workflow import env, OWNER, start_module, module_request


def package(tmp_path, *, source=None, states=None):
    staged = stage(tmp_path)
    raw = raw_result(staged[1])
    if source is not None:
        raw['module']['files'][raw['module']['entry']] = source
    if states is not None:
        raw['module']['states']['exit'] = states
    return collected(staged, raw)[2]


def instruction_reference(value):
    text = value['scene']['instruction']
    return json.loads(text.split(appearance.BEGIN, 1)[1].removesuffix(appearance.END))


def test_exact_original_source_exit_states_and_asset_bindings_reach_unchanged_v1_input(tmp_path):
    source = '// 中文注释：颜色与阴影不可重新解释。\r\nexport const material = "warm white";\r\n'
    first = package(tmp_path, source=source, states={'subject': dict(x=1,
        appearance=dict(fill='#c29a39', marks=[[-6, 0], [6, 0]], shadow=dict(offset=[0, 4], opacity=.09)))})
    task = core(1, predecessor_from_package(first))
    task['scene']['instruction'] = 'Keep the explanation easy to follow.'
    before = deepcopy(task)
    enhanced = appearance.attach_predecessor_appearance(task, first)
    reference = instruction_reference(enhanced)
    assert reference['schema'] == appearance.SCHEMA
    assert reference['files'] == first['result']['module']['files']
    assert reference['files'][reference['entry']].encode() == source.encode()
    assert reference['files_sha256'][reference['entry']] == hashlib.sha256(source.encode()).hexdigest()
    assert reference['exit_states'] == first['result']['module']['states']['exit']
    assert reference['module_sha256'] == task['film_context']['predecessor']['module_sha256']
    assert reference['input_sha256'] == first['input_sha256']
    assert reference['result_sha256'] == first['result_sha256']
    assert reference['sha256'] == digest({key: value for key, value in reference.items() if key != 'sha256'})
    assert enhanced['scene']['instruction'].startswith(task['scene']['instruction'] + '\n\n')
    assert contract.validate_core_task(enhanced) == enhanced
    assert task == before  # Neither prior source nor caller data is edited.
    assert {key: value for key, value in enhanced.items() if key != 'scene'} == {
        key: value for key, value in task.items() if key != 'scene'}
    assert len(enhanced['scene']['instruction']) <= 16000
    assert len(canonical_bytes(enhanced)) < contract.INPUT_MAX_BYTES


def test_reference_cannot_be_substituted_from_another_predecessor_or_altered_source(tmp_path):
    first = package(tmp_path)
    task = core(1, predecessor_from_package(first))
    changed = deepcopy(first)
    changed['result']['module']['files'][changed['result']['module']['entry']] += '// forged\n'
    with pytest.raises(ServiceError):
        appearance.attach_predecessor_appearance(task, changed)
    task['film_context']['predecessor']['input_sha256'] = 'f' * 64
    with pytest.raises(ServiceError, match='predecessor_mismatch'):
        appearance.attach_predecessor_appearance(task, first)


def test_complete_oversized_source_or_cumulative_instruction_fails_without_truncation(tmp_path):
    first = package(tmp_path, source='// source reference\n' + 'x' * 16000)
    task = core(1, predecessor_from_package(first))
    with pytest.raises(ServiceError, match='instruction_too_large') as raised:
        appearance.attach_predecessor_appearance(task, first)
    assert raised.value.status == 413
    small = package(tmp_path, source='// a complete small source\n')
    task = core(1, predecessor_from_package(small))
    task['scene']['instruction'] = '原有用户意见' * 2600
    assert len(task['scene']['instruction']) <= 16000
    with pytest.raises(ServiceError, match='instruction_too_large'):
        appearance.attach_predecessor_appearance(task, small)


def test_predecessor_source_changes_frozen_task_and_skill_catalog_identity(tmp_path):
    first = package(tmp_path)
    task = core(1, predecessor_from_package(first))
    enhanced = appearance.attach_predecessor_appearance(task, first)
    assert digest(enhanced) != digest(task)
    assert appearance.attach_predecessor_appearance(task, first) == enhanced


def test_actual_store_freezes_previous_source_before_skill_selection_or_paid_claim(env):
    first = start_module(env)
    env.workflow.tick()
    first = env.creation.operation('module', first['id'])
    second = start_module(env, 1, first['version_id'])
    frozen = env.creation.operation('module', second['id'])
    reference = instruction_reference(frozen['document']['core_task'])
    prior = env.creation.module_package(OWNER, first['version_id'])
    assert reference['files'] == prior['result']['module']['files']
    assert reference['exit_states'] == prior['result']['module']['states']['exit']
    assert not frozen['submit_attempted'] and frozen['input_sha256'] is None
    assert env.gateway.calls.count(('submit', second['id'])) == 0
    env.workflow.tick()
    delivered = env.gateway.documents[second['id']]
    assert instruction_reference(delivered) == reference
    assert delivered['skill_catalog']['task_sha256'] == digest(frozen['document']['core_task'])
    assert env.creation.operation('module', second['id'])['status'] == 'completed'


def test_oversized_combined_reference_stops_before_next_operation_or_gateway_upload(env):
    first = start_module(env)
    env.workflow.tick()
    first = env.creation.operation('module', first['id'])
    calls = deepcopy(env.gateway.calls)
    request = module_request(env, 1, first['version_id'])
    request['instruction'] = 'a' * 15900
    with pytest.raises(ServiceError, match='instruction_too_large'):
        env.workflow.start_module(OWNER, env.project['id'], env.project['scenes'][1]['id'],
            request, 'too-large-next-module')
    assert env.gateway.calls == calls
    assert not env.creation.active_operations()
