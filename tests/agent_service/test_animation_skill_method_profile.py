"""Skill authorship contracts; all experiences are explicit synthetic fixtures."""
from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service.animation_skills_contract import build_skill_package
from agent_service.contracts import ServiceError, InlineFile, canonical_bytes, digest
from agent_service.engines import StopEvidence
from agent_service.profiles import video_animation_skill_method as contract
from video_workbench.animation_knowledge import AnimationKnowledgeStore
from test_animation_method_profile import document as old_document, result as old_result, review_context


def context(tmp_path, mode='create'):
    wiki = AnimationKnowledgeStore(tmp_path / 'knowledge.sqlite3')
    experience = wiki.record_experience('fixture-owner', 'brief', 'fixture-request',
        {'goal': 'Synthetic test goal; no rendered or human approved sample.'})
    snapshot = wiki.snapshot('fixture-owner', experience_ids=[experience['id']])
    value = old_document(mode)
    value.update(schema=contract.INPUT_SCHEMA, knowledge_snapshot=snapshot, research_records=[])
    return value


def output(value):
    result = old_result(value)
    result.update(schema=contract.RESULT_SCHEMA, knowledge_proposals=[])
    result['methods'][0].update(schema='video.animation-method.v2', research_record_ids=[],
        skill_package=build_skill_package(skill_id='fixture-skill', version=1, kind='expression',
            files={'SKILL.md': '---\nname: fixture-causality\ndescription: Preserve the visible intervention.\n---\n'
                              'Show a cause before its effect; adapt the entities and relation structure.'},
            runtime_requirements={'engines': ['html-svg']}))
    return result


def test_separate_roles_and_verified_resource_identity_do_not_reinterpret_v1():
    from agent_service.profiles import video_animation_method as old
    author, reviewer = contract.VideoAnimationSkillMethodProfile(), contract.VideoAnimationSkillMethodProfile(True)
    assert author.name == contract.AUTHOR_PROFILE != old.AUTHOR_PROFILE
    assert reviewer.name == contract.REVIEW_PROFILE != old.REVIEW_PROFILE
    assert author.task_schema == 'video-animation-method-author.v2'
    assert reviewer.output_schema == 'video-animation-method-review-output.v2'
    assert author.required_tools == ('read_inputs', 'write') and 'vision' in reviewer.required_model_capabilities
    assert set(contract.verified_bundle()) == contract.BUNDLE_FILES
    assert old.BUNDLE_REVISION == 'animation-method-v2'
    assert author.limits.max_input_bytes < 2 * 1024 * 1024
    assert author.limits.max_input_total_bytes == 32 * 1024 * 1024


def test_complete_skill_and_real_frozen_knowledge_are_required(tmp_path):
    value = context(tmp_path)
    result = output(value)
    assert contract.validate_result(result, digest(value), value) == result
    changed = deepcopy(result)
    changed['methods'][0].pop('skill_package')
    with pytest.raises(ServiceError):
        contract.validate_result(changed, digest(value), value)
    value['knowledge_snapshot']['experiences'][0]['document']['goal'] = 'Substituted raw experience.'
    with pytest.raises(ServiceError, match='record_changed'):
        contract.validate_input(value)


def test_collector_computes_standard_package_hashes_from_agent_authored_files(tmp_path):
    value = context(tmp_path)
    result = output(value)
    package = result['methods'][0]['skill_package']
    result['methods'][0]['skill_package'] = {key: deepcopy(package[key]) for key in
        ('skill_id', 'version', 'kind', 'files', 'runtime_requirements')}
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    raw = canonical_bytes(value)
    (inputs / contract.INPUT_NAME).write_bytes(raw)
    (tmp_path / 'output.json').write_bytes(canonical_bytes(result))
    attempt = str(uuid4())
    envelope = SimpleNamespace(profile=contract.AUTHOR_PROFILE, profile_revision='1', attempt_id=attempt,
        task={'input_sha256': digest(value)}, inputs=[InlineFile.from_bytes(contract.INPUT_NAME, raw)],
        checkpoint_inputs=[], checkpoint_ref=None)
    proof = StopEvidence(execution_id=attempt, all_writers_stopped=True, resource_ids=('fixture-stopped-writer',))
    collected = contract.VideoAnimationSkillMethodProfile().collect_output(tmp_path, envelope, proof)
    standard = json.loads(collected[0].content())['methods'][0]['skill_package']
    assert standard == package
    assert 'publication' not in standard
    envelope.inputs = [InlineFile.from_bytes(contract.INPUT_NAME, b'substituted')]
    with pytest.raises(ServiceError, match='input_binding_mismatch'):
        contract.VideoAnimationSkillMethodProfile().collect_output(tmp_path, envelope, proof)


def test_wiki_candidates_must_use_actual_frozen_experience_not_invented_success(tmp_path):
    value = context(tmp_path, 'plan')
    result = output(value)
    candidate = dict(page_id='observed-principle', expected_revision=0,
        body=dict(title='A provisional principle', content='A hypothesis, not rendered success.'),
        evidence_ids=[value['knowledge_snapshot']['experiences'][0]['id']],
        change_note='Derived from the actual requested brief; remains provisional.')
    result['knowledge_proposals'] = [candidate]
    assert contract.validate_result(result, digest(value), value) == result
    candidate['evidence_ids'] = ['invented-current-render-success']
    with pytest.raises(ServiceError, match='snapshot_mismatch'):
        contract.validate_result(result, digest(value), value)


@pytest.mark.parametrize('dimension', ['requires_motion', 'requires_audio'])
def test_wiki_does_not_upgrade_sampled_images_into_observed_motion_or_audio(tmp_path, dimension):
    value, result = review_context()
    knowledge = context(tmp_path)['knowledge_snapshot']
    value.update(schema=contract.INPUT_SCHEMA, knowledge_snapshot=knowledge, research_records=[])
    value['criteria'][0][dimension] = True
    result.update(schema=contract.RESULT_SCHEMA, input_sha256=digest(value), knowledge_proposals=[])
    result['criteria'] = value['criteria']
    with pytest.raises(ServiceError, match='unobserved_quality_claim'):
        contract.validate_result(result, digest(value), value)
    result['review']['criteria_results'][0]['status'] = 'insufficient_evidence'
    assert contract.validate_result(result, digest(value), value) == result


def test_full_main_json_budget_rejects_large_knowledge_before_reader_or_submission(tmp_path):
    value = context(tmp_path)
    value['reference'] = {'raw': 'x' * contract.INPUT_MAX_BYTES}
    with pytest.raises(ServiceError):
        contract.validate_input(value)
