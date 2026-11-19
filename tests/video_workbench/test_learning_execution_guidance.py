"""Creator cost context crosses both learning contracts without widening them."""
from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError, digest
from agent_service.profiles.video_animation_create import source_sha256
from agent_service.profiles import video_picture_learning as picture
from agent_service.profiles import video_reference_learning as video
from video_workbench.learning_execution_guidance import learning_execution_guidance


def document(protocol):
    frame = dict(file_name='reference.jpg', sha256='a' * 64, purpose='reference',
        media_sha256='b' * 64, requested_time=0, actual_pts=0, clip_time=0)
    return dict(schema=protocol.INPUT_SCHEMA, mode='create', project_id='project',
        experiment_id='experiment', work_kind='reproduction', goal='Learn the supplied appearance.',
        criteria=['Keep the spatial relationships readable.'], constraints=dict(width=320,
            height=180, fps=24, duration_seconds=2, allowed_engines=['canvas'],
            capability_selection=dict(creation=['canvas'], motion=['native-js'])),
        observation=dict(frames=[frame]), base_source=None, feedback='', knowledge_snapshot=None,
        prior_review=None, learned_method=None,
        knowledge_binding=dict(use_wikiskill=False, release_id=None))


@pytest.mark.parametrize('protocol', [picture, video])
def test_guidance_fits_real_learning_contract_and_preserves_explicit_selection(protocol):
    original = document(protocol)
    selected = deepcopy(original['constraints'])
    guided = deepcopy(original)
    guided['constraints']['execution_guidance'] = learning_execution_guidance()
    accepted = protocol.validate_input(guided)
    # Real protocol validation, rather than a source declaration, still enforces
    # the exact allowed engine after receiving the broader technique context.
    source = dict(engine='three', strategy=dict(description='Use a spatial scene.', tradeoffs=''),
        files={'scene.html': '<!doctype html><html><body></body></html>'}, source_sha256='0' * 64)
    source['source_sha256'] = source_sha256(source)
    with pytest.raises(ServiceError, match='engine_not_allowed'):
        protocol.validate_source(source, constraints=accepted['constraints'])
    assert {k: accepted['constraints'][k] for k in selected} == selected
    assert accepted['goal'] == original['goal'] and accepted['criteria'] == original['criteria']
    assert accepted['knowledge_binding'] == original['knowledge_binding']
    assert original == document(protocol)
    assert digest(accepted) != digest(original)


def test_guidance_caller_cannot_change_later_operation_environment_or_policy():
    first = learning_execution_guidance()
    first['environment']['browser_renderer']['cpu_limit'] = 99
    first['capability_boundaries'].clear()
    second = learning_execution_guidance()
    assert second['environment']['browser_renderer'] == dict(cpu_limit=2,
        memory_limit_bytes=1073741824, gpu_acceleration=False,
        three_webgl_backend='ANGLE/SwiftShader on CPU')
    assert second['capability_boundaries']
    assert second['environment']['evidence_scope'] == 'configured_renderer_policy_not_a_performance_measurement'
