"""Optional v1 spatial intent offer for newly frozen composable-module inputs.

This carries host-declared requirements, not new renderer admission or proof of
pixels. Historical module bundles and documents without this field are exact.
"""
from copy import deepcopy

from ..contracts import ServiceError, canonical_bytes, digest
from ..spatial_rendering_contract import resolve_quality, validate_rendering_intent


OFFER_SCHEMA = 'video.film-spatial-rendering-offer.v1'
OFFER_REVISION = 1
OFFER_SCOPE = 'host_frozen_requirements_not_execution_or_visual_acceptance'
INSTRUCTIONS = (
    'Use exactly intent.engine for the scene and any authored transition module. '
        'The singleton constraints.allowed_engines is authoritative for module collection.',
    'Honor intent.space_mode and intent.camera_mode using only the actual admitted backend. '
        'An animated camera must derive its state from absolute time; a static camera is a valid deliberate choice.',
    'Implement required_features and enabled optional features; degraded_features are explicitly unavailable. '
        'Do not silently enable or claim unsupported photographic features. Structural errors block.',
    'When intent.light is present, its direction points from a surface toward the light in world XYZ. '
        'Transform local normals into that same basis before directional response; preserve the same light across objects. '
        'physical and artistic are response choices, not permission to invert the declared direction.',
    'Keep opaque silhouette coverage separate from pigment, highlight and material opacity. '
        'Anchor texture to stable object or surface coordinates with an explicit seed; use declared depth for atmosphere.',
    'This offer grants no tools.surface or other helper imports to composable film modules. '
        'Use only listed assets and the frozen drawing backend. Engine availability is authorability, '
        'not a claim that a generated effect ran or passed visual acceptance.',
)


def _require(condition, code='film_module_spatial_offer_invalid'):
    if not condition:
        raise ServiceError(code, 422)


def make_spatial_offer(intent, quality):
    intent = validate_rendering_intent(intent)
    _require(type(quality) is dict and type(quality.get('enabled_features')) is list)
    # Recompute consistency, not availability: only the application knows which
    # features its trusted renderer admits. Required features cannot be dropped.
    normalized_quality = resolve_quality(intent, quality['enabled_features'])
    _require(quality == normalized_quality, 'film_module_spatial_quality_mismatch')
    body = dict(schema=OFFER_SCHEMA, revision=OFFER_REVISION, intent=intent, intent_sha256=digest(intent),
        quality=deepcopy(quality), quality_sha256=digest(quality),
        instructions=list(INSTRUCTIONS), evidence_scope=OFFER_SCOPE)
    return dict(body, sha256=digest(body))


def validate_spatial_offer(value):
    _require(type(value) is dict and set(value) == {'schema', 'revision', 'intent', 'intent_sha256',
        'quality', 'quality_sha256', 'instructions', 'evidence_scope', 'sha256'})
    _require(type(value['revision']) is int and value['revision'] == OFFER_REVISION)
    _require(value == make_spatial_offer(value['intent'], value['quality']))
    return deepcopy(value)


def spatial_instruction(offer):
    value = validate_spatial_offer(offer)
    return ('Host-frozen spatial rendering requirements, version 1. Read and implement '
        'constraints.spatial_rendering; its requirements and quality limits bind this module.\n'
        'APP-FROZEN-SPATIAL-RENDERING\n' + canonical_bytes(value).decode('utf-8')
        + '\nEND-APP-FROZEN-SPATIAL-RENDERING')
