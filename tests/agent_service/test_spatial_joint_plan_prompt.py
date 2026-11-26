"""The offered result field must be authorable without conflicting contracts."""
import hashlib
import json

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_joint_plan as plain, video_joint_plan_skills as skills
from agent_service.spatial_rendering_contract import foundation_guidance, validate_rendering_intent
from test_joint_plan_profile import document, result
from test_joint_plan_skills_profile import stage


HANDLERS = [plain.VideoJointPlanProfile, skills.VideoJointPlanSkillsProfile]


@pytest.mark.parametrize('handler,prompt_sha256', [
    (plain.VideoJointPlanProfile, 'dd379bd196cc304e8869f1fd65b3ddcb1b8ce493c59ac58d4e36b5e31ae7b632'),
    (skills.VideoJointPlanSkillsProfile, '2a1165343c658150b4bd430f64f9416fa6c5b536a46940323393cdf3fb1b5fc0'),
])
def test_legacy_task_keeps_exact_prompt_bytes(handler, prompt_sha256):
    prompt = handler().build_prompt({'input_sha256': 'a' * 64})
    assert hashlib.sha256(prompt.encode()).hexdigest() == prompt_sha256
    assert 'Trusted host amendment' not in prompt
    assert plain.verified_bundle() and skills.verified_bundle()


@pytest.mark.parametrize('handler', HANDLERS)
@pytest.mark.parametrize('value', [None, True, False, 0, 2, 1.0, '1', [], {}])
def test_only_explicit_supported_integer_revision_is_admitted(handler, value):
    with pytest.raises(ServiceError, match='rendering_revision_invalid'):
        handler().build_prompt({'input_sha256': 'a' * 64, 'rendering_foundation_revision': value})
    with pytest.raises(ServiceError):
        handler().validate_task({'input_sha256': 'a' * 64, 'other': 1})


@pytest.mark.parametrize('handler', HANDLERS)
@pytest.mark.parametrize('offer,marker', [(True, False), (False, True), (True, True)])
def test_revision_and_frozen_offer_are_bound_before_model_execution(tmp_path, handler, offer, marker):
    instance = handler()
    if handler is skills.VideoJointPlanSkillsProfile:
        context, _, _, envelope, _ = stage(tmp_path, rendering_foundation=offer)
        task = envelope.task
    else:
        context = document()
        if offer:
            context['rendering_foundation'] = foundation_guidance()
        root = tmp_path / 'inputs'; root.mkdir()
        (root / plain.INPUT_NAME).write_bytes(canonical_bytes(context))
        task = {'input_sha256': digest(context)}
    if marker:
        task['rendering_foundation_revision'] = 1
    if offer != marker:
        with pytest.raises(ServiceError, match='rendering_revision_binding_mismatch'):
            instance.validate_inputs(tmp_path, task)
    else:
        instance.validate_inputs(tmp_path, task)


@pytest.mark.parametrize('handler', HANDLERS)
def test_trusted_amendment_resolves_old_exact_field_rule_with_a_valid_complete_example(handler):
    instance = handler()
    prompt = instance.build_prompt({'input_sha256': 'a' * 64, 'rendering_foundation_revision': 1})
    legacy = instance.build_prompt({'input_sha256': 'a' * 64})
    assert prompt.startswith(legacy)
    amendment = prompt[len(legacy):]
    assert 'amend the preceding exact output field list' in amendment
    assert 'REQUIRE one additional top-level field named rendering_intent' in amendment
    intent = json.loads(amendment.split('complete object with this shape:\n')[1].split('\n')[0])
    assert validate_rendering_intent(intent) == intent
    context = document(); context['rendering_foundation'] = foundation_guidance()
    output = result(context); output['rendering_intent'] = intent
    assert plain.validate_joint_plan_result(output, digest(context), context)['rendering_intent'] == intent


def test_skill_author_can_collect_the_additional_field_with_host_evidence(tmp_path):
    context, _, output, envelope, proof = stage(tmp_path, rendering_foundation=True)
    envelope.task['rendering_foundation_revision'] = 1
    handler = skills.VideoJointPlanSkillsProfile()
    prompt = handler.build_prompt(envelope.task)
    output['rendering_intent'] = json.loads(prompt.split('complete object with this shape:\n')[1].split('\n')[0])
    (tmp_path / 'output.json').write_bytes(canonical_bytes(output))
    observer = handler.create_tool_observer(tmp_path, envelope.task)
    collected = handler.collect_output_with_host_evidence(tmp_path, envelope, proof,
        observer.finish(envelope.attempt_id))
    assert json.loads(collected[0].content())['rendering_intent'] == output['rendering_intent']
