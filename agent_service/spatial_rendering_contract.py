"""Inert rendering intent and honest, host-bound authoring guidance.

An intent requests features; it never admits an engine or claims a visual test.
Only a caller's trusted available-feature set may resolve those requests.
"""
from copy import deepcopy
import math
import re

from .contracts import ServiceError


INTENT_SCHEMA = 'video.spatial-rendering-intent.v1'
GUIDANCE_SCHEMA = 'video.spatial-rendering-guidance.v1'
QUALITY_SCHEMA = 'video.spatial-rendering-quality-resolution.v1'
FEATURES = frozenset({'form', 'scale', 'coverage', 'lighting', 'texture', 'atmosphere',
    'perspective_camera', 'viewpoint_change', 'depth_of_field', 'motion_blur', 'postprocessing'})
STRUCTURAL_FEATURES = frozenset({'form', 'scale', 'coverage'})
SURFACE_FUNCTIONS = frozenset({'evaluateLighting', 'sampleMaterial', 'sampleAtmosphere'})
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def _require(condition, code='spatial_rendering_intent_invalid', *, status=422, **details):
    if not condition:
        error = ServiceError(code, status)
        if details:
            error.details = details
        raise error


def _features(value):
    _require(type(value) is list and len(value) <= len(FEATURES))
    _require(all(type(item) is str and item in FEATURES for item in value),
        'spatial_rendering_feature_unknown')
    _require(len(set(value)) == len(value), 'spatial_rendering_feature_duplicate')
    return value


def validate_rendering_intent(value):
    """Validate requests, without inferring capabilities from engine or style."""
    required = {'schema', 'space_mode', 'engine', 'camera_mode', 'required_features',
        'optional_features', 'quality'}
    _require(type(value) is dict and required <= set(value) <= required | {'light'})
    _require(value['schema'] == INTENT_SCHEMA)
    _require(type(value['space_mode']) is str and value['space_mode'] in {'planar', 'layered', 'geometric'})
    _require(type(value['engine']) is str and value['engine'] in {'html-svg', 'canvas', 'three'})
    _require(type(value['camera_mode']) is str and value['camera_mode'] in {'static', 'animated'})
    wanted, optional = _features(value['required_features']), _features(value['optional_features'])
    _require(not set(wanted) & set(optional), 'spatial_rendering_feature_overlap')
    _require(not STRUCTURAL_FEATURES & set(optional), 'spatial_rendering_structural_feature_optional')
    _require(value['camera_mode'] != 'static' or 'viewpoint_change' not in wanted + optional,
        'spatial_rendering_camera_conflict')
    quality = value['quality']
    _require(type(quality) is dict and set(quality) == {'structural_errors', 'optional_degradation'})
    _require(quality['structural_errors'] == 'block'
        and type(quality['optional_degradation']) is str
        and quality['optional_degradation'] in {'declared', 'forbid'},
        'spatial_rendering_quality_invalid')
    light = value.get('light')
    if light is not None:
        _require(type(light) is dict and set(light) == {'space', 'direction', 'response'},
            'spatial_rendering_light_invalid')
        _require(light['space'] == 'world' and type(light['response']) is str
            and light['response'] in {'physical', 'artistic'}, 'spatial_rendering_light_invalid')
        direction = light['direction']
        _require(type(direction) is list and len(direction) == 3
            and all(type(n) in (int, float) and -1000000 <= n <= 1000000
                and math.isfinite(n) for n in direction)
            and math.hypot(*direction) > 1e-12, 'spatial_rendering_light_invalid')
    return deepcopy(value)


def resolve_quality(intent, available_features):
    """Use trusted actual availability; never estimate cost or relax structure."""
    checked = validate_rendering_intent(intent)
    _require(type(available_features) in (list, tuple, set, frozenset)
        and len(available_features) <= len(FEATURES)
        and all(type(item) is str and item in FEATURES for item in available_features),
        'spatial_rendering_capabilities_invalid')
    available = set(available_features)
    _require(len(available) == len(available_features), 'spatial_rendering_capabilities_invalid')
    missing = sorted(set(checked['required_features']) - available)
    _require(not missing, 'spatial_rendering_required_features_unavailable', status=409,
        missing_features=missing)
    absent = [item for item in checked['optional_features'] if item not in available]
    _require(not absent or checked['quality']['optional_degradation'] == 'declared',
        'spatial_rendering_optional_downgrade_forbidden', status=409, missing_features=absent)
    return dict(schema=QUALITY_SCHEMA, status='degraded' if absent else 'satisfied',
        enabled_features=[item for item in checked['required_features'] + checked['optional_features']
            if item in available],
        degraded_features=[dict(feature=item, reason='capability_unavailable') for item in absent],
        structural_errors='block', evidence_scope='host_declared_feature_availability_not_visual_acceptance')


def foundation_guidance(capabilities=None, *, expected_surface_sha256=None, surface_functions=()):
    """Describe common meaning; advertise helpers only against trusted frozen bytes.

    expected_surface_sha256 and surface_functions must come from the application's
    own current implementation, never an author artifact or requested intent.
    Matching a pin proves availability, not that the author used or validated it.
    """
    source = capabilities if type(capabilities) is dict else {}
    scene = source.get('scene_space', source)
    scene = scene if type(scene) is dict else {}
    pinned = (scene.get('admitted') is True and type(scene.get('engines')) is list
        and 'canvas' in scene['engines'] and type(scene.get('artwork_api_revision')) is int
        and scene['artwork_api_revision'] >= 3 and type(expected_surface_sha256) is str
        and _SHA.fullmatch(expected_surface_sha256) is not None
        and scene.get('surface_runtime_sha256') == expected_surface_sha256)
    exported = (set(surface_functions) & SURFACE_FUNCTIONS
        if type(surface_functions) in (list, tuple, set, frozenset)
        and all(type(name) is str for name in surface_functions) else set())
    descriptions = {
        'evaluateLighting': 'Evaluate declared world-space surface-to-light direction and style-configurable facing response; transform authored normals into the same space first.',
        'sampleMaterial': 'Evaluate bounded seeded object/surface-coordinate material distribution; keep that domain stable under camera movement.',
        'sampleAtmosphere': 'Evaluate authored distance-related color and contrast guidance; this is not silhouette coverage or a new depth reconstruction.',
    }
    signatures = {
        'evaluateLighting': 'evaluateLighting(normal,{light:[x,y,z],mode?:"diffuse"|"ink",ambient?,lightRange?:[min,max],inkRange?:[min,max]})',
        'sampleMaterial': 'sampleMaterial(positionXYZ,{seed,space?:"object"|"world",scales?:[low,medium,high],weights?:[low,medium,high],base?,amplitude?,roughness?,roughnessVariation?,normalStrength?})',
        'sampleAtmosphere': 'sampleAtmosphere(distance,{near?,far?,density?,strength?,maxAmount?,height?,baseHeight?,heightFalloff?,color?:[r,g,b]})',
    }
    result_scopes = {
        'evaluateLighting': 'dot/diffuse/lit/shade/ink_density are authored directional cues, not cast shadows, PBR or coverage.',
        'sampleMaterial': 'variation/value/roughness/normal_delta are stable three-scale cues, not a finished material, geometry or coverage.',
        'sampleAtmosphere': 'amount/color/transmittance are distance/height color-mixing cues; never use transmittance as solid object alpha.',
    }
    helpers = {name: dict(namespace='tools.surface', description=descriptions[name],
        signature=signatures[name], result_scope=result_scopes[name],
        implementation_sha256=expected_surface_sha256, implemented=True, authorable=True,
        admitted=True, verified=False,
        evidence_scope='trusted_export_and_frozen_host_pin_not_execution_or_quality')
        for name in sorted(exported) if pinned}
    return dict(schema=GUIDANCE_SCHEMA, intent_schema=INTENT_SCHEMA,
        intent_fields=['schema', 'space_mode', 'engine', 'camera_mode', 'required_features',
            'optional_features', 'quality', 'light (optional)'],
        space_modes=['planar', 'layered', 'geometric'], engines=['html-svg', 'canvas', 'three'],
        camera_modes=['static', 'animated'], features=sorted(FEATURES),
        structural_features=sorted(STRUCTURAL_FEATURES),
        quality=dict(structural_errors='block', optional_degradation='declared | forbid'),
        light=dict(space='world', direction='finite nonzero [x,y,z], from surface toward the light source',
            response='physical | artistic'),
        capability_levels=dict(implemented='Trusted implementation exists; not runtime admission.',
            authorable='An author can express the method using the declared tools; not proof of execution.',
            admitted='The exact host/backend and dependency bytes are actually admitted for this task.',
            verified='Independent evidence covers the actual result and stated scope; availability alone is insufficient.'),
        surface_helpers=helpers,
        backend_boundaries=dict(
            canvas='Layer composition and optional fixed-view XYZ projection do not admit changing viewpoint, orbit, hidden backsides or Three.js execution.',
            html_svg='Use admitted groups, paths, clips and masks; a shared intent does not create native depth or physical shading.',
            three='World geometry, perspective and changed viewpoints require the actual admitted Three backend, authored geometry and reconstruction from absolute time; an engine name does not verify photographic effects.'),
        instructions=[
            'Choose space, engine and camera from the actual objective and frozen capabilities, not narration presence or style keywords. Stillness is a valid choice.',
            'Preserve object identity, anchor/support, scale and silhouette coverage before optional lighting, texture or atmosphere detail. Structural errors block; optional downgrades need an explicit record.',
            'Keep opaque silhouette coverage separate from pigment, highlight and texture alpha. Intentional object/layer transparency is applied once; pale solids still cover lower scenery.',
            'Declare light in world space and transform local normals and camera-dependent values consistently. Physical and artistic responses are choices; do not silently reverse one declared light across objects.',
            'Bind texture and stroke density/direction to stable object or surface coordinates and seed. Atmosphere expresses declared depth and local space; neither effect repairs wrong shape, scale or coverage.',
            'An empty surface_helpers offer grants no new helper calls. Only use a listed function under the exact frozen host pin; source examples, intents and author claims cannot expand admission.',
            'Keep common intent in a host-offered field only. Do not add it or drawing arrays to historical strict method/world/plan schemas; retain allowed scene source files for authored data.',
            'A correction may revise geometry, layout, light or camera within the current budget; principal changes require a traceable plan revision and affected checks/cache invalidation, never a silent reset.',
            'Do not infer that all seven photographic effects are implemented, admitted or verified. Depth of field, motion blur and postprocessing remain optional and require their own actual capability evidence.',
            'Technical results, sampled-window image opinions, continuous human viewing and explicit version acceptance remain distinct. Do not claim model video/audio observation or start automatic changed-content work.',
        ])
