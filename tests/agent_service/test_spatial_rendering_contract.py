"""Requested rendering meaning is distinct from admission and scoped evidence."""
from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from agent_service.contracts import ServiceError
from agent_service.spatial_rendering_contract import (
    FEATURES, INTENT_SCHEMA, SURFACE_FUNCTIONS, foundation_guidance,
    resolve_quality, validate_rendering_intent,
)


@pytest.fixture
def intent():
    return dict(schema=INTENT_SCHEMA, space_mode='layered', engine='canvas',
        camera_mode='animated', required_features=['form', 'scale', 'coverage'],
        optional_features=['lighting', 'texture', 'atmosphere'],
        quality=dict(structural_errors='block', optional_degradation='declared'),
        light=dict(space='world', direction=[2, 3, 4], response='artistic'))


def test_intent_preserves_authored_direction_and_is_detached_without_inference(intent):
    original = deepcopy(intent)
    checked = validate_rendering_intent(intent)
    assert checked == original == intent
    checked['required_features'].append('lighting')
    checked['light']['direction'][0] = 17
    assert intent == original
    # Canvas may use fixed orthographic authored geometry; it is not Three.
    intent.update(space_mode='geometric', camera_mode='static')
    assert validate_rendering_intent(intent)['engine'] == 'canvas'
    intent.update(engine='three', space_mode='planar')
    assert validate_rendering_intent(intent)['space_mode'] == 'planar'


@pytest.mark.parametrize('field,value', [
    ('space_mode', 'unknown'), ('engine', 'webgpu'), ('camera_mode', True),
    ('schema', 'video.spatial-rendering-intent.v2'), ('audio_mode', 'silent'),
    ('style', 'ink'), ('runtime_capabilities', {'admitted': True}),
])
def test_strict_intent_does_not_accept_keywords_or_author_admission(intent, field, value):
    intent[field] = value
    with pytest.raises(ServiceError):
        validate_rendering_intent(intent)


@pytest.mark.parametrize('required,optional,code', [
    (['coverage', 'coverage'], [], 'spatial_rendering_feature_duplicate'),
    (['lighting'], ['lighting'], 'spatial_rendering_feature_overlap'),
    (['all_seven'], [], 'spatial_rendering_feature_unknown'),
    (['form'], ['scale'], 'spatial_rendering_structural_feature_optional'),
    (['scale'], ['coverage'], 'spatial_rendering_structural_feature_optional'),
    (['coverage'], ['form'], 'spatial_rendering_structural_feature_optional'),
])
def test_feature_identity_and_structural_non_degradation(intent, required, optional, code):
    intent.update(required_features=required, optional_features=optional)
    with pytest.raises(ServiceError, match=code):
        validate_rendering_intent(intent)


@pytest.mark.parametrize('direction', [[0, 0, 0], [True, 1, 0], [float('nan'), 0, 1],
    [float('inf'), 0, 1], [10**1000, 1, 2], [1, 2], [1e-15, 0, 0]])
def test_world_light_rejects_nonfinite_boolean_degenerate_and_unbounded_vectors(intent, direction):
    intent['light']['direction'] = direction
    with pytest.raises(ServiceError, match='spatial_rendering_light_invalid'):
        validate_rendering_intent(intent)


def test_light_and_camera_semantics_are_explicit(intent):
    intent['light']['space'] = 'screen'
    with pytest.raises(ServiceError, match='spatial_rendering_light_invalid'):
        validate_rendering_intent(intent)
    intent.pop('light')
    assert validate_rendering_intent(intent).get('light') is None
    intent['required_features'].append('viewpoint_change')
    intent['camera_mode'] = 'static'
    with pytest.raises(ServiceError, match='spatial_rendering_camera_conflict'):
        validate_rendering_intent(intent)
    intent['required_features'].remove('viewpoint_change')
    intent['optional_features'].append('viewpoint_change')
    with pytest.raises(ServiceError, match='spatial_rendering_camera_conflict'):
        validate_rendering_intent(intent)
    intent['optional_features'].remove('viewpoint_change')
    intent['required_features'].append('viewpoint_change')
    intent['camera_mode'] = 'animated'
    # A valid request still needs routing admission; engine names do not grant it.
    assert 'viewpoint_change' in validate_rendering_intent(intent)['required_features']
    with pytest.raises(ServiceError) as failure:
        resolve_quality(intent, ['form', 'scale', 'coverage'])
    assert failure.value.code == 'spatial_rendering_required_features_unavailable'
    assert failure.value.status == 409
    assert failure.value.details == {'missing_features': ['viewpoint_change']}


@pytest.mark.parametrize('quality', [
    {'structural_errors': 'ignore', 'optional_degradation': 'declared'},
    {'structural_errors': 'block', 'optional_degradation': True},
    {'structural_errors': 'block', 'optional_degradation': 'declared', 'new_budget': 3600},
])
def test_quality_cannot_relax_structure_or_request_another_budget(intent, quality):
    intent['quality'] = quality
    with pytest.raises(ServiceError):
        validate_rendering_intent(intent)


def test_only_missing_optional_capabilities_are_reported_as_degraded(intent):
    original = deepcopy(intent)
    result = resolve_quality(intent, ['form', 'scale', 'coverage', 'texture'])
    assert result['status'] == 'degraded'
    assert result['enabled_features'] == ['form', 'scale', 'coverage', 'texture']
    assert result['degraded_features'] == [
        {'feature': 'lighting', 'reason': 'capability_unavailable'},
        {'feature': 'atmosphere', 'reason': 'capability_unavailable'}]
    assert result['structural_errors'] == 'block'
    assert 'seconds' not in result and 'gpu' not in result
    assert intent == original
    assert resolve_quality(intent, FEATURES)['status'] == 'satisfied'
    with pytest.raises(ServiceError) as failure:
        resolve_quality(intent, ['form', 'scale', 'texture', 'atmosphere', 'lighting'])
    assert failure.value.code == 'spatial_rendering_required_features_unavailable'
    assert failure.value.details['missing_features'] == ['coverage']


def test_forbidden_optional_downgrade_and_untrusted_feature_sets_fail(intent):
    intent['quality']['optional_degradation'] = 'forbid'
    with pytest.raises(ServiceError, match='spatial_rendering_optional_downgrade_forbidden'):
        resolve_quality(intent, ['form', 'scale', 'coverage'])
    for value in ({'coverage': True}, ['coverage', 'coverage'], ['invented_effect'], 'coverage'):
        with pytest.raises(ServiceError, match='spatial_rendering_capabilities_invalid'):
            resolve_quality(intent, value)


def test_static_guidance_is_semantic_not_a_callable_or_verified_capability():
    offer = foundation_guidance()
    assert offer['surface_helpers'] == {}
    assert set(offer['capability_levels']) == {'implemented', 'authorable', 'admitted', 'verified'}
    assert 'fixed-view' in offer['backend_boundaries']['canvas']
    assert 'viewpoints' in offer['backend_boundaries']['three']
    assert 'Depth of field' in ' '.join(offer['instructions'])
    offer['features'].append('invented')
    assert 'invented' not in foundation_guidance()['features']


def test_helper_offer_requires_actual_exports_and_matching_application_owned_frozen_pin():
    pin = 'a' * 64
    cap = dict(admitted=True, engines=['canvas'], artwork_api_revision=3,
        surface_runtime_sha256=pin)
    assert foundation_guidance(cap)['surface_helpers'] == {}
    assert foundation_guidance(cap, expected_surface_sha256=pin)['surface_helpers'] == {}
    for change in ({'admitted': False}, {'engines': ['three']},
            {'artwork_api_revision': True}, {'surface_runtime_sha256': 'b' * 64}):
        assert foundation_guidance(dict(cap, **change), expected_surface_sha256=pin,
            surface_functions=SURFACE_FUNCTIONS)['surface_helpers'] == {}
    result = foundation_guidance({'scene_space': cap}, expected_surface_sha256=pin,
        surface_functions=['evaluateLighting'])['surface_helpers']
    assert set(result) == {'evaluateLighting'}
    assert result['evaluateLighting']['implemented'] is True
    assert result['evaluateLighting']['authorable'] is True
    assert result['evaluateLighting']['admitted'] is True
    assert result['evaluateLighting']['verified'] is False
    assert result['evaluateLighting']['implementation_sha256'] == pin


def test_common_learning_offer_does_not_upgrade_an_older_surface_pin():
    from video_workbench.scene_space_learning import scene_method_guidance
    from video_workbench.scene_space_renderer import SURFACE_RUNTIME
    current = hashlib.sha256(Path(SURFACE_RUNTIME).read_bytes()).hexdigest()
    cap = dict(admitted=True, engines=['canvas'], artwork_api_revision=3,
        surface_runtime_sha256=current)
    fresh = scene_method_guidance('static_image', runtime_capabilities=cap)
    names = fresh['rendering_foundation']['surface_helpers']
    assert set(names) == SURFACE_FUNCTIONS
    assert scene_method_guidance('static_image')['rendering_foundation']['surface_helpers'] == {}
    cap['surface_runtime_sha256'] = '0' * 64
    assert scene_method_guidance('sampled_frames_only',
        runtime_capabilities=cap)['rendering_foundation']['surface_helpers'] == {}
    assert fresh['method_schema'] == 'video.scene-space-method.v1'
    assert fresh['required_fields'] == ['schema', 'world', 'plan', 'source',
        'hypotheses', 'principles', 'stages', 'limitations']
