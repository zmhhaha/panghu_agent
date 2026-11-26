"""The new planner reads the same frozen packages through observed tools."""
from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service.animation_skills_contract import build_skill_package, bundle_files, freeze_library_skill_catalog
from agent_service.contracts import InlineFile, ServiceError, canonical_bytes, digest
from agent_service.animation_wikiskill_contract import EVIDENCE_SCOPE, COMPILATION_LIMITATION
from agent_service.engines import StopEvidence
from agent_service.profiles import video_joint_plan as previous
from agent_service.profiles import video_joint_plan_skills as profile
from agent_service.profiles.video_joint_plan_contract import planning_method_task
from test_joint_plan_profile import document, result


def stage(tmp_path, *, rendering_foundation=False):
    context = document()
    if rendering_foundation:
        from agent_service.spatial_rendering_contract import foundation_guidance
        context['rendering_foundation'] = foundation_guidance()
    packages = [build_skill_package(skill_id=f'lesson-{i}', version=1, kind='foundation' if i == 0 else 'expression',
        files={'SKILL.md': f'---\nname: lesson-{i}\ndescription: Show relationships through a changing object.\n---\n'
            'Keep object identity while demonstrating the process.'}, runtime_requirements={'engines': ['html-svg']})
        for i in range(6)]
    release = dict(schema='video.animation-wikiskill-release.v1', id=str(uuid4()), owner='publisher',
        sequence=1, job_id=str(uuid4()), snapshot_id=str(uuid4()), snapshot_sha256='a' * 64,
        input_sha256='b' * 64, created_at=1000, summary='A synthetic whole library.', packages=packages,
        index=[dict(skill_id=row['skill_id'], parent_skill_id=None if i == 0 else 'lesson-0',
                    source_record_ids=['source']) for i, row in enumerate(packages)],
        exclusions=[], limitations=[COMPILATION_LIMITATION], evidence_scope=EVIDENCE_SCOPE)
    release['sha256'] = digest(release)
    catalog = freeze_library_skill_catalog(release, recipient_owner='alice', consumer_id='planning',
        task_sha256=digest(planning_method_task(context)), allowed_engines=context['allowed_engines'])
    context['skill_catalog'] = catalog
    files = [InlineFile.from_bytes(profile.INPUT_NAME, canonical_bytes(context)), *bundle_files(catalog, packages)]
    for file in files:
        path = tmp_path / 'inputs' / file.name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(file.content())
    output = result(context)
    output['skill_decisions'] = []
    (tmp_path / 'output.json').write_bytes(canonical_bytes(output))
    attempt = str(uuid4())
    envelope = SimpleNamespace(profile=profile.PROFILE, profile_revision='1', attempt_id=attempt,
        task={'input_sha256': digest(context)}, inputs=files, checkpoint_inputs=[], checkpoint_ref=None)
    return context, packages, output, envelope, StopEvidence(attempt, True, ('synthetic-writer',))


def observe(observer, tool, params, identity):
    observer.observe(dict(type='tool_execution_start', toolName=tool, toolCallId=identity, args=params))
    observer.observe(dict(type='tool_execution_end', toolName=tool, toolCallId=identity,
        isError=False, result=observer.tools.tool_result(tool, params)))


def test_new_profile_bundle_preserves_v1_and_uses_skill_tools_without_top_three_limit(tmp_path):
    context, packages, _, envelope, proof = stage(tmp_path)
    handler = profile.VideoJointPlanSkillsProfile()
    assert handler.name == 'video.joint-plan.v2'
    assert previous.VideoJointPlanProfile.required_tools == ('read_inputs', 'write')
    assert handler.required_tools == ('read_inputs', 'skill_search', 'skill_read', 'write')
    assert previous.verified_bundle() and profile.verified_bundle()
    assert len(context['skill_catalog']['skills']) == 6
    observer = handler.create_tool_observer(tmp_path, envelope.task)
    observe(observer, 'skill_search', {'query': 'relationships'}, 'search')
    result_file, evidence_file = handler.collect_output_with_host_evidence(
        tmp_path, envelope, proof, observer.finish(envelope.attempt_id))
    checked, evidence = json.loads(result_file.content()), json.loads(evidence_file.content())
    assert checked['skill_decisions'] == [] and checked['skill_tool_evidence_sha256'] == evidence['sha256']
    assert checked['method_decision'] is None


@pytest.mark.parametrize('actual_read', [False, True])
def test_planned_use_requires_actual_root_read_and_exact_segment_target(tmp_path, actual_read):
    _, packages, output, envelope, proof = stage(tmp_path)
    handler = profile.VideoJointPlanSkillsProfile()
    observer = handler.create_tool_observer(tmp_path, envelope.task)
    package = packages[0]
    observe(observer, 'skill_search', {'query': 'relationships'}, 'search')
    if actual_read:
        observe(observer, 'skill_read', {'package_sha256': package['sha256']}, 'read')
    output['skill_decisions'] = [dict(package_sha256=package['sha256'], action='adapt',
        reason='Keep the droplet identity while its influence expands.', segment_ids=['s1'])]
    (tmp_path / 'output.json').write_bytes(canonical_bytes(output))
    if actual_read:
        artifacts = handler.collect_output_with_host_evidence(tmp_path, envelope, proof, observer.finish(envelope.attempt_id))
        assert len(json.loads(artifacts[0].content())['skill_decisions']) == 1
    else:
        with pytest.raises(ServiceError, match='decisions_incomplete'):
            handler.collect_output_with_host_evidence(tmp_path, envelope, proof, observer.finish(envelope.attempt_id))


def test_agent_cannot_supply_host_read_evidence(tmp_path):
    _, _, output, envelope, proof = stage(tmp_path)
    handler = profile.VideoJointPlanSkillsProfile()
    observer = handler.create_tool_observer(tmp_path, envelope.task)
    output['skill_tool_evidence_sha256'] = 'f' * 64
    (tmp_path / 'output.json').write_bytes(canonical_bytes(output))
    with pytest.raises(ServiceError, match='evidence_forbidden'):
        handler.collect_output_with_host_evidence(tmp_path, envelope, proof, observer.finish(envelope.attempt_id))
