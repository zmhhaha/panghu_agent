"""Snapshot-only protocol regression; no Chromium, Docker or quality claims."""
from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_animation_collaboration_contract as contract
from agent_service.profiles import video_animation_skill_method as skill_contract
from video_workbench.animation_knowledge import AnimationKnowledgeStore
from video_workbench.animation_method_renderer import MethodAnimationRenderer
from test_animation_method_renderer import IMAGE, SOURCE, document as legacy_document
from test_animation_collaboration import plan_document


def collaboration_document(phase='integrate'):
    plan = None if phase == 'plan' else plan_document()
    dependencies = []
    if phase == 'integrate':
        dependencies = [dict(component_id=row['id'], engine='canvas', interface=row['interface'],
            source='export function draw(ctx,seconds,shared) { return seconds; }') for row in plan['components']]
    return contract.build_input(phase=phase, goal='A synthetic scene with coordinated independent responsibilities.',
        criteria=[dict(id='coherent', description='Shared coordinates preserve the whole composition.')],
        constraints=dict(width=320, height=180, fps=24, duration_seconds=8, allowed_engines=['canvas']),
        assets={}, brief=plan, dependencies=dependencies,
        component_id='mountains' if phase == 'component' else None)


def test_integrated_collaboration_keeps_its_actual_schema_hash_and_hyperframes_request(tmp_path):
    renderer = MethodAnimationRenderer(tmp_path / 'renderer', IMAGE, hyperframes=True,
        legacy_bridge=True, candidate_engines=('canvas',))
    value = collaboration_document()
    original = canonical_bytes(value)
    source = dict(SOURCE, engine='canvas')
    binding = renderer._snapshot('real-integration-identity', source, value, [], 1800)
    assert canonical_bytes(value) == original
    assert canonical_bytes(binding['document']) == original
    assert binding['document']['schema'] == 'video.animation-collaboration-input.v1'
    assert binding['document']['phase'] == 'integrate'
    assert 'mode' not in binding['document']
    assert binding['input_sha256'] == digest(value)
    assert binding['request']['schema'] == 'video.animation-method-render-request.v3'
    assert binding['request']['project']['scenes'][0]['engine'] == 'canvas'
    assert binding['request']['legacy_bridge'] is True
    assert binding['request']['project_sha256'] == digest(binding['request']['project'])
    assert binding['operation_id'] == 'real-integration-identity'
    assert not renderer.root.exists()


@pytest.mark.parametrize('phase', ['plan', 'component'])
def test_intermediate_collaboration_cannot_render_or_create_a_render_ledger(tmp_path, phase):
    renderer = MethodAnimationRenderer(tmp_path / 'renderer', IMAGE, hyperframes=True,
        legacy_bridge=True, candidate_engines=('canvas',))
    value = collaboration_document(phase)
    with pytest.raises(ServiceError, match='animation_collaboration_final_phase_required'):
        renderer._snapshot('intermediate-' + phase, dict(SOURCE, engine='canvas'), value, [])
    assert not renderer.root.exists()


def test_integrate_still_requires_all_exact_component_dependencies(tmp_path):
    renderer = MethodAnimationRenderer(tmp_path / 'renderer', IMAGE, hyperframes=True,
        legacy_bridge=True, candidate_engines=('canvas',))
    value = collaboration_document()
    value['dependencies'].pop()
    with pytest.raises(ServiceError, match='animation_collaboration_integrate_dependencies_required'):
        renderer._snapshot('missing-part', dict(SOURCE, engine='canvas'), value, [])
    assert not renderer.root.exists()


def test_original_v1_v2_snapshots_keep_protocol_and_request_behavior(tmp_path):
    renderer = MethodAnimationRenderer(tmp_path / 'renderer', IMAGE, hyperframes=True, legacy_bridge=True)
    old = legacy_document()
    new = deepcopy(old)
    new.update(schema=skill_contract.INPUT_SCHEMA, research_records=[],
        knowledge_snapshot=AnimationKnowledgeStore(tmp_path / 'knowledge.sqlite3').snapshot('developer', page_ids=[]))
    first = renderer._snapshot('same-source-identity', SOURCE, old, [])
    second = renderer._snapshot('same-source-identity', SOURCE, new, [])
    assert first['document']['schema'] == 'video.animation-method-input.v1'
    assert second['document']['schema'] == 'video.animation-method-input.v2'
    assert first['input_sha256'] == digest(old)
    assert second['input_sha256'] == digest(new)
    assert first['request'] == second['request']
    assert first['request']['schema'] == 'video.animation-method-render-request.v3'
    assert not renderer.root.exists()
