"""Choose an implementation from explicit intent, never prose or audio mode.

Admission and planning evidence do not establish generated pixel quality.
"""
from copy import deepcopy

from agent_service.contracts import ServiceError, digest
from agent_service.spatial_rendering_contract import validate_rendering_intent, resolve_quality

SCENE_FEATURES = frozenset({'form', 'scale', 'coverage'})
APPEARANCE_FEATURES = frozenset({'lighting', 'texture', 'atmosphere'})
FILM_FEATURES = {
    'html-svg': frozenset({'scale', 'coverage'}),
    'canvas': frozenset({'scale', 'coverage'}),
    'three': frozenset({'form', 'scale', 'coverage', 'perspective_camera', 'viewpoint_change'}),
}


def film_features(capabilities, engine, *, frozen=False):
    from .spatial_three_library import available_features
    return FILM_FEATURES[engine] | available_features(capabilities, engine, frozen=frozen)


def preferred_route(intent):
    value = validate_rendering_intent(intent)
    features = set(value['required_features'])
    fixed_view = not features.intersection({'perspective_camera', 'viewpoint_change'})
    return ('scene-space' if value['engine'] == 'canvas'
            and value['space_mode'] in {'layered', 'geometric'} and fixed_view else 'hyperframes-film')


def admit_route(intent, configuration, *, audio_mode):
    value = validate_rendering_intent(intent)
    preferred = preferred_route(value)
    route = configuration.get('renderer_mode')
    allowed = {preferred, 'hyperframes-film'}
    if configuration.get('available') is not True or route not in allowed:
        raise ServiceError('spatial_rendering_route_unavailable', 503)
    if route == 'scene-space':
        cap = configuration.get('render_capability', {})
        if (type(cap) is not dict or cap.get('admitted') is not True
                or type(cap.get('engines')) is not list or value['engine'] not in cap['engines']):
            raise ServiceError('spatial_rendering_engine_unavailable', 409)
        if audio_mode not in configuration.get('audio_modes', []):
            raise ServiceError('spatial_rendering_audio_mode_not_admitted', 409)
        available = {'scale'}
        api=cap.get('artwork_api_revision',0)
        extensions=cap.get('artwork_extensions',{})
        if type(api) is int and api>=2 and type(extensions) is dict and extensions.get('coverage'):
            available.add('coverage')
        if type(api) is int and api>=3 and cap.get('geometry_runtime_sha256'):
            available.add('form')
        appearance = cap.get('appearance', {})
        if (type(appearance) is dict and appearance.get('schema') == 'video.scene-appearance-capability.v1'
                and appearance.get('surface_runtime_sha256') == cap.get('surface_runtime_sha256')):
            available.update(set(appearance.get('features', [])) & APPEARANCE_FEATURES)
        evidence = 'admitted_fixed_view_canvas_runtime_not_artistic_acceptance'
    else:
        caps = configuration.get('runtime_capabilities', {})
        engines = caps.get('allowed_engines', configuration.get('allowed_engines', []))
        if value['engine'] not in engines:
            raise ServiceError('spatial_rendering_engine_unavailable', 409)
        available = set(film_features(caps, value['engine']))
        evidence = 'admitted_module_engine_authoring_not_pixel_or_artistic_proof'
    quality = resolve_quality(value, available)
    return dict(schema='video.spatial-rendering-route.v1', intent=deepcopy(value),
        intent_sha256=digest(value), route=route, engine=value['engine'],
        reason='explicit_space_engine_and_camera_requirements', audio_mode=audio_mode,
        preferred_route=preferred,
        configuration_sha256=configuration['configuration_sha256'], quality=quality,
        evidence_scope=evidence, visual_acceptance=False)


def validate_scene_intent_plan(intent, plan):
    """Keep camera promises; a baked painting cannot relight rotating geometry."""
    value=validate_rendering_intent(intent)
    for shot in plan['shots']:
        if value['camera_mode']=='static':
            views={(key['x'],key['y'],key['zoom']) for key in shot['camera']['keyframes']}
            if len(views)>1:
                raise ServiceError('spatial_rendering_camera_intent_changed',409)
        physical=(value.get('light') or {}).get('response')=='physical'
        if physical and 'lighting' in value['required_features']:
            for track in shot['local_animation']:
                if track['property']=='rotation' and any(key['value']!=0 for key in track['keyframes']):
                    raise ServiceError('spatial_rendering_cached_light_motion_not_admitted',409)
    return deepcopy(plan)
