"""A planner may repartition material, but cannot change custody or verbatim text."""
from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service.contracts import InlineFile, ServiceError, canonical_bytes, digest
from agent_service.engines import StopEvidence
from agent_service.profiles import implementation
from agent_service.profiles import video_joint_plan as profile


def document():
    return dict(schema_version='video-joint-plan-input.v1',
        project=dict(title='先解释现象', script='先看水滴。再解释扩散。', style='', voice='zm_yunjian'),
        scenes=[], brief=dict(audience='', goal='理解扩散', tone='conversational'),
        instruction='', preserve_script=False, method_delivery=None, allowed_engines=['html-svg', 'canvas'])


def result(context):
    return dict(schema_version='video-joint-plan-result.v1', input_sha256=digest(context),
        summary='从水滴走向扩散。', understanding_goal='理解扩散。', visual_style='克制的蓝色水面。',
        continuity='同一滴水逐渐展开。', scenes=[dict(segment_id='s1', source_scene_ids=[],
            title='扩散', narration=context['project']['script'], visual_description='水滴落下后波纹展开。',
            duration_seconds=8, pronunciation={}, key_points=['扩散'], expression='连续水波模拟',
            visual_steps=['水滴下落', '波纹向外传播'], delivery_note='', source_quotes=['先看水滴。'])],
        method_decision=None, warnings=[])


def test_zero_scene_input_supports_free_expression_and_autonomous_segmentation():
    context = document()
    checked = profile.validate_joint_plan_result(result(context), digest(context), context)
    assert checked['scenes'][0]['expression'] == '连续水波模拟'
    assert checked['warnings']
    assert profile.validate_joint_plan_input(context) == context
    assert implementation(profile.PROFILE).required_tools == ('read_inputs', 'write')
    assert set(profile.verified_bundle()) == profile.BUNDLE_FILES


def test_verbatim_checks_exact_concatenation_including_whitespace():
    context = document(); context['preserve_script'] = True
    context['project']['script'] = '先看水滴。\n再解释扩散。'
    output = result(context)
    second = deepcopy(output['scenes'][0]); second['segment_id'] = 's2'
    output['scenes'][0]['narration'] = '先看水滴。\n'
    second['narration'] = '再解释扩散。'
    output['scenes'].append(second)
    assert len(profile.validate_joint_plan_result(output, digest(context), context)['scenes']) == 2
    output['scenes'][0]['narration'] = '先看水滴。'
    with pytest.raises(ServiceError, match='script_not_preserved'):
        profile.validate_joint_plan_result(output, digest(context), context)


@pytest.mark.parametrize('change', ['unknown-source', 'duplicate-id', 'quote', 'invented-url', 'title-url', 'spoken-url', 'speech-expand', 'binding'])
def test_invalid_provenance_or_speech_is_rejected_before_adoption(change):
    context = document(); output = result(context)
    if change == 'unknown-source': output['scenes'][0]['source_scene_ids'] = ['invented']
    elif change == 'duplicate-id': output['scenes'].append(deepcopy(output['scenes'][0]))
    elif change == 'quote': output['scenes'][0]['source_quotes'] = ['不曾出现的事实']
    elif change == 'invented-url': output['summary'] += ' https://invented.invalid/'
    elif change == 'title-url': output['scenes'][0]['title'] = 'https://invented.invalid/'
    elif change == 'spoken-url': output['scenes'][0]['pronunciation'] = {'水滴': 'https://invented.invalid/'}
    elif change == 'speech-expand':
        output['scenes'][0]['narration'] = '水滴' * 100
        output['scenes'][0]['pronunciation'] = {'水滴': '解释' * 100}
    else: output['input_sha256'] = 'a' * 64
    with pytest.raises(ServiceError): profile.validate_joint_plan_result(output, digest(context), context)


def test_reference_markers_are_preserved_and_validation_is_idempotent():
    context = document(); context['instruction'] = 'https://example.org/source 版本：2026-11-12'
    checked = profile.validate_joint_plan_result(result(context), digest(context), context)
    assert 'https://example.org/source' in checked['scenes'][0]['visual_description']
    assert '版本：2026-11-12' in checked['scenes'][0]['visual_description']
    assert profile.validate_joint_plan_result(checked, digest(context), context) == checked


@pytest.mark.parametrize('value', [[], {}, None, 12])
def test_bad_enum_types_are_contract_errors_not_server_errors(value):
    context = document(); context['brief']['tone'] = value
    with pytest.raises(ServiceError): profile.validate_joint_plan_input(context)


@pytest.mark.parametrize('script', ['字' * 48001, '标签<svg>。', '代码```内容'],
                         ids=['too-long', 'markup', 'code-fence'])
def test_impossible_verbatim_input_is_rejected_before_model_admission(script):
    context = document(); context.update(preserve_script=True)
    context['project']['script'] = script
    with pytest.raises(ServiceError): profile.validate_joint_plan_input(context)


def test_stopped_artifact_custody_and_canonical_input(tmp_path):
    context = document(); raw = canonical_bytes(context)
    (tmp_path / 'inputs').mkdir()
    (tmp_path / 'inputs' / profile.INPUT_NAME).write_bytes(raw)
    (tmp_path / 'output.json').write_bytes(canonical_bytes(result(context)))
    identity = str(uuid4())
    envelope = SimpleNamespace(profile=profile.PROFILE, profile_revision='1', attempt_id=identity,
        task={'input_sha256': digest(context)}, inputs=[InlineFile.from_bytes(profile.INPUT_NAME, raw)],
        checkpoint_inputs=[], checkpoint_ref=None)
    proof = StopEvidence(execution_id=identity, all_writers_stopped=True, resource_ids=('fixture-only',))
    handler = profile.VideoJointPlanProfile()
    output = handler.collect_output(tmp_path, envelope, proof)[0]
    assert output.name == profile.RESULT_NAME and json.loads(output.content())['warnings']
    stopped = StopEvidence(execution_id=identity, all_writers_stopped=False, resource_ids=('fixture-only',))
    with pytest.raises(ServiceError, match='writers_not_stopped'):
        handler.collect_output(tmp_path, envelope, stopped)
    (tmp_path / 'inputs' / profile.INPUT_NAME).write_text(json.dumps(context, indent=2), encoding='utf-8')
    with pytest.raises(ServiceError, match='input_binding_mismatch'):
        handler.validate_inputs(tmp_path, envelope.task)
