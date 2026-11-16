"""Candidates meet deterministic render limits before appearing successful."""
from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError, digest
from agent_service.profiles.video_references import preserve_plan_references
from video_workbench.motion import example_plan
from video_workbench.render_metadata import render_metadata
from video_workbench.explanation_store import visual_generation_description, scene_visual_binding
from test_motion_store import OWNER, state, start


URL = 'https://github.com/deepseek-ai/deepseek-harness/blob/8bd073392eef6f54e921a093b95076248eb08cbb/docs/architecture.md'


def test_nonrenderable_candidate_never_becomes_a_successful_version(state):
    _, motion, project_id, _ = state
    operation = start(state)
    plan = example_plan(operation['document']['beats'])
    card = next(row for row in plan['elements'] if row['kind'] == 'card')
    card.update(label='字' * 28, detail='字' * 64)
    with pytest.raises(ServiceError, match='motion_text_overflow'):
        motion.complete(operation['id'], dict(schema='video.motion-result.v1',
            input_sha256=operation['input_sha256'], plan=plan))
    project = motion.project(OWNER, project_id)
    assert project['scenes'][0]['motion']['versions'] == []
    assert project['scenes'][0]['motion']['head_version_id'] is None


def test_candidate_keeps_literal_source_and_records_derived_plan_identity(state):
    base, motion, project_id, scene_id = state
    project = base.project(OWNER, project_id)
    scene = project['scenes'][0]
    fields = {key: scene[key] for key in ('title', 'narration', 'visual_description', 'duration_seconds', 'format')}
    fields['visual_description'] += '\n来源：' + URL + '\n固定版本8bd0733；核对2026-11-04'
    base.save_scene(OWNER, project_id, fields, scene_id=scene_id, expected_revision=project['revision'])
    operation = start(state)
    plan = example_plan(operation['document']['beats'])
    original = deepcopy(plan)
    result = motion.complete(operation['id'], dict(schema='video.motion-result.v1',
        input_sha256=operation['input_sha256'], plan=plan))
    version = motion.version(OWNER, result['version_id'])
    assert version['plan']['sources'][0]['url'] == URL
    assert '核对2026-11-04' in version['plan']['sources'][0]['label']
    assert '固定版本8bd0733' in version['plan']['sources'][0]['label']
    assert version['agent_plan_sha256'] == digest(original)
    assert version['plan_sha256'] == digest(version['plan'])
    assert version['compiler_version'] == 'motion-compiler-6'
    assert plan == original
    assert motion.project(OWNER, project_id)['scenes'][0]['motion']['head_version_id'] is None


def test_source_metadata_preservation_is_idempotent():
    document = dict(visual_description=URL + '\n版本8bd0733；核对2026-11-04', instruction='')
    plan = dict(sources=[dict(url=URL, label='参考文档')])
    prepared = preserve_plan_references(plan, document)
    assert preserve_plan_references(prepared, document) == prepared


def test_frozen_continuity_draft_exposes_whether_its_exact_version_is_adopted():
    frozen = dict(id='s', title='示例', narration='介绍。', visual_mode='static',
        visual={'id': 'v'}, timing={'configured_seconds': 10})
    current = dict(id='s', narration='介绍。', duration_seconds=10, versions=[{'id': 'v'}])
    snapshot = dict(scenes=[frozen], purpose='draft', continuity={'version_id': 'c-old'})
    project = dict(scenes=[current], continuity_head_version_id='c-new')
    metadata = render_metadata(snapshot, project)
    assert metadata['content_state'] == 'current'
    assert metadata['continuity_current'] is False
    project['continuity_head_version_id'] = 'c-old'
    assert render_metadata(snapshot, project)['continuity_current'] is True


def test_adopted_visual_instructions_never_truncate_source_or_requirements():
    scene = dict(title='示例', narration='介绍。', format='svg', duration_seconds=10,
                 visual_description='说明' * 4000 + '\n' + URL)
    scene['explanation'] = dict(visual_binding_sha256=scene_visual_binding(scene),
        visual_strategy='flow', key_points=['关键点' * 40] * 4, visual_steps=['解释过程' * 60] * 6)
    with pytest.raises(ServiceError, match='explanation_visual_requires_split'):
        visual_generation_description(scene, enforce_limit=True)
    assert URL in visual_generation_description(scene)
    assert scene['visual_description'].endswith(URL)
