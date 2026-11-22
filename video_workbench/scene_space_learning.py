"""Versioned spatial hypotheses carried beside strictly preserved source files.

Authors describe a method and its uncertainty. Host layout/render receipts are
separate evidence; this data cannot certify artistic quality, motion or audio.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re

from agent_service.contracts import ServiceError, canonical_bytes
from agent_service.scene_space_contract import WORLD_SCHEMA, validate_world, world_sha256
from .scene_space_plan import PLAN_SCHEMA, validate_plan

METHOD_SCHEMA = 'video.scene-space-method.v1'
MAX_METHOD_BYTES = 1024 * 1024
MAX_ROWS = 32
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_SCOPES = {'scene_data_only', 'static_image', 'sampled_frames_only'}
_STAGES = {'observe', 'spatial_hypothesis', 'reproduce', 'compare', 'temporal', 'transfer'}
_STATUSES = {'proposed', 'inferred', 'pending', 'failed', 'excluded'}


def _require(ok, code='scene_space_method_invalid', status=422):
    if not ok:
        raise ServiceError(code, status)


def _fields(value, keys):
    _require(type(value) is dict and set(value) == set(keys))


def _id(value):
    _require(type(value) is str and _ID.fullmatch(value) is not None)
    return value


def _text(value):
    _require(type(value) is str and bool(value.strip()) and len(value) <= 4000
        and not any((ord(c) < 32 and c not in '\n\r\t') or ord(c) == 127
                    or 0xD800 <= ord(c) <= 0xDFFF for c in value))


def _texts(value):
    _require(type(value) is list and len(value) <= MAX_ROWS)
    for item in value:
        _text(item)


def _ids(value):
    _require(type(value) is list and len(value) <= 128)
    for item in value:
        _id(item)
    _require(len(set(value)) == len(value))


def _references(value):
    _require(type(value) is list and len(value) <= 128)
    seen = set()
    for row in value:
        _fields(row, ('id', 'sha256'))
        _id(row['id'])
        _require(type(row['sha256']) is str and _SHA.fullmatch(row['sha256']) is not None)
        _require(row['id'] not in seen)
        seen.add(row['id'])
    return {(row['id'], row['sha256']) for row in value}


def _scope(value):
    # The picture service's old observation name remains unchanged outside
    # this new carrier. Its explicit spatial-source label is static_image.
    value = 'static_image' if value == 'static_only' else value
    _require(type(value) is str and value in _SCOPES)
    return value


def validate_scene_method(value, *, source_scope=None, source_refs=None, frame_ids=None):
    """Normalize inert hypotheses and optionally bind actual supplied sources.

    With no source context this performs structural validation only. It does
    not upgrade saved metadata into observed evidence. Callers accepting an
    author result must supply the frozen scope, references and frame IDs.
    """
    _fields(value, ('schema', 'world', 'plan', 'source', 'hypotheses',
                    'principles', 'stages', 'limitations'))
    try:
        _require(len(canonical_bytes(value)) <= MAX_METHOD_BYTES,
            'scene_space_method_too_large', 413)
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise ServiceError('scene_space_method_invalid', 422) from None
    _require(value['schema'] == METHOD_SCHEMA)
    result = deepcopy(value)
    source = result['source']
    _fields(source, ('scope', 'references', 'frame_ids', 'motion_observed',
                     'audio_observed', 'whole_video_watched'))
    source['scope'] = _scope(source['scope'])
    references = _references(source['references'])
    _ids(source['frame_ids'])
    _require({row['id'] for row in source['references']} == set(source['frame_ids']),
        'scene_space_method_frame_source_unbound', 409)
    _require(all(source[key] is False for key in
        ('motion_observed', 'audio_observed', 'whole_video_watched')),
        'scene_space_method_observation_not_admitted')
    if source_scope is not None:
        _require(source['scope'] == _scope(source_scope), 'scene_space_method_source_scope_changed', 409)
    if source_refs is not None:
        _require(references <= _references(source_refs), 'scene_space_method_source_changed', 409)
    if frame_ids is not None:
        _ids(frame_ids)
        _require(set(source['frame_ids']) <= set(frame_ids), 'scene_space_method_frame_not_supplied', 409)
    if source['scope'] == 'scene_data_only':
        _require(not references and not source['frame_ids'], 'scene_space_method_source_scope_changed', 409)
    if result['world'] is not None:
        result['world'] = validate_world(result['world'])
    if result['plan'] is not None:
        result['plan'] = validate_plan(result['plan'])
        if result['world'] is not None:
            matching = [world for world in result['plan']['worlds'] if world['id'] == result['world']['id']]
            _require(len(matching) == 1 and world_sha256(matching[0]) == world_sha256(result['world']),
                'scene_space_method_world_plan_changed', 409)
    _require(type(result['hypotheses']) is list and len(result['hypotheses']) <= MAX_ROWS)
    seen = set()
    for row in result['hypotheses']:
        _fields(row, ('id', 'claim', 'confidence', 'evidence_frame_ids', 'alternatives', 'unknowns'))
        _id(row['id']); _require(row['id'] not in seen); seen.add(row['id'])
        _text(row['claim'])
        confidence = row['confidence']
        _require(type(confidence) in (int, float) and 0 <= confidence <= 1 and math.isfinite(confidence))
        _ids(row['evidence_frame_ids'])
        _require(set(row['evidence_frame_ids']) <= set(source['frame_ids']),
            'scene_space_method_evidence_frame_changed', 409)
        _texts(row['alternatives']); _texts(row['unknowns'])
    _texts(result['principles']); _texts(result['limitations'])
    _require(type(result['stages']) is list and len(result['stages']) <= len(_STAGES))
    seen = set()
    for row in result['stages']:
        _fields(row, ('stage', 'status', 'notes', 'evidence_frame_ids', 'limitations'))
        _require(type(row['stage']) is str and row['stage'] in _STAGES and row['stage'] not in seen)
        seen.add(row['stage'])
        _require(type(row['status']) is str and row['status'] in _STATUSES,
            'scene_space_method_unearned_verification')
        _text(row['notes']); _ids(row['evidence_frame_ids']); _texts(row['limitations'])
        _require(set(row['evidence_frame_ids']) <= set(source['frame_ids']),
            'scene_space_method_evidence_frame_changed', 409)
    return result


def transferable_scene_method(method):
    """Keep old method semantics; remove this carrier's reference-specific data.

    Legacy methods without a versioned spatial carrier are returned unchanged.
    This is used only for explicitly admitted v1 spatial methods, never to
    reinterpret an old arbitrary method.scene_space field automatically.
    """
    if (type(method) is not dict or type(method.get('scene_space')) is not dict
            or method['scene_space'].get('schema') != METHOD_SCHEMA):
        return deepcopy(method)
    checked = validate_scene_method(method['scene_space'])
    result = deepcopy(method)
    result['scene_space'] = dict(schema=METHOD_SCHEMA, world=None, plan=None,
        source=dict(scope='scene_data_only', references=[], frame_ids=[], motion_observed=False,
            audio_observed=False, whole_video_watched=False), hypotheses=[],
        principles=deepcopy(checked['principles']), limitations=deepcopy(checked['limitations']),
        stages=[dict(stage='transfer', status='pending',
            notes='Apply the extracted principles to independently planned new content.',
            evidence_frame_ids=[], limitations=['Reproduction does not establish successful transfer.'])])
    return result


def scene_method_guidance(scope):
    """A data-only author offer, kept inside existing constraints semantics."""
    scope = _scope(scope)
    return dict(schema='video.scene-space-learning-offer.v1', method_schema=METHOD_SCHEMA,
        result_field='method.scene_space', source_scope=scope,
        world_schema=WORLD_SCHEMA, plan_schema=PLAN_SCHEMA,
        required_fields=['schema', 'world', 'plan', 'source', 'hypotheses', 'principles', 'stages', 'limitations'],
        source_fields=['scope', 'references', 'frame_ids', 'motion_observed', 'audio_observed', 'whole_video_watched'],
        hypothesis_fields=['id', 'claim', 'confidence', 'evidence_frame_ids', 'alternatives', 'unknowns'],
        stage_fields=['stage', 'status', 'notes', 'evidence_frame_ids', 'limitations'],
        stages=sorted(_STAGES), statuses=sorted(_STATUSES),
        world_contract=dict(required=['schema','id','revision','seed','style','space','layers',
            'objects','layout','relations'],
            space=dict(projection='planar | scattered | geometric',bounds='rect{x,y,width,height}'),
            layer=dict(required=['id','order','parallax'],optional=['opacity','blur','cache_policy','mask_region_id']),
            object=dict(required=['id','kind','layer_id','parent_id','bounds','transform','depth','anchors'],
                transform=['x','y','scale_x','scale_y','rotation'],
                depth=dict(kind='ordinal | geometric | unknown',value='number; null for unknown'),
                anchors='map of anchor name to local [x,y]',optional=['order','allow_overflow','overflow_reason','recipe_ref']),
            layout=dict(grid='null or {origin:[x,y],spacing:[dx,dy],columns,rows}',
                points='[{id,position:[x,y],depth:{kind,value}}]',regions='[{id,kind:rect,bounds}] or polygon points'),
            relations=dict(
                attachment=['id','kind','object_id','anchor','target_object_id','target_anchor'],
                support=['id','kind','object_id','anchor','region_id'],
                scale=['id','kind','object_id','reference_object_id','metric','min_ratio','max_ratio'],
                occlusion=['id','kind','front_id','back_id'])),
        plan_contract=dict(required=['schema','id','revision','fps','duration_frames','viewport','worlds',
            'shots','budget','provenance'],fps=24,
            camera='fixed principal direction; x/y are world-space view centre; zoom in [.25,8]',
            shots=dict(required=['id','world_id','world_revision','start_frame','end_frame','camera','local_animation'],
                interval='half-open start_frame/end_frame partitions',
                camera='{max_zoom,keyframes:[{frame,x,y,zoom,easing}]}',
                local_animation='[{id,target_id,property:x|y|rotation|scale|opacity,keyframes:[{frame,value,easing}]}]',
                transition='optional cut | continuous'),
            provenance=dict(origin='generated',source_refs=[],evidence_scope='scene_data_only',
                motion_observed=False,audio_observed=False,whole_video_watched=False),
            budget=dict(model=dict(limit_calls=16,used_calls=0,limit_tokens=1000000,used_tokens=0),
                render=dict(limit_seconds=3600,used_seconds=0,max_cached_pixels=16777216,max_temporary_bytes=134217728),
                revisions=dict(limit=4,used=0))),
        instructions=[
            'The currently admitted Canvas compositor accepts only static layer cache_policy. Paint each object once; express supported local changes with absolute-frame transform/opacity tracks. Other cache policies remain descriptive metadata until separately admitted.',
            'When useful, record spatial, layer, scale, anchor and occlusion hypotheses in method.scene_space; source files retain their exact contract.',
            'Use explicit declared depth; image Y does not establish distance. Grid and named points assist object layout, not every brush stroke.',
            'Copy source references and frame IDs only from the host offer. Confidence describes a hypothesis, not verified geometry or artistic acceptance.',
            'One image or sampled frames do not establish actual camera movement, three-dimensional reconstruction, continuous motion, audio or full-video observation. Keep all three observation flags false.',
            'world and plan may be null for a partial learning stage. A plan must use the common world/plan schema and match the actual output viewport and frame count.',
            'A world-only hypothesis uses ordinary source rendering and separate layout diagnostics. A non-null plan selects the admitted scene-space compositor and requires its artwork API.',
            'For the admitted Canvas compositor, scene.html must provide canvas[data-scene-space-output] styled to fill the viewport, and scene.js must register window.__sceneSpaceArtwork={paintObject(ctx,object,world){...}}. Paint only each object in its local bounds; the host applies parent/layer/camera transforms, opacity and masking. Use the declared seed for reproducible artwork. Do not define a nonconfigurable window.__animationExperiment; the host owns the frame clock.',
            'ink.renderCanvas resets its internal Canvas transform. For direct object painting use bounds.x=bounds.y=0, match the InkScene document width/height to object.bounds, and use clear:false; express world placement in object.transform. For nonzero local bounds, render into an admitted local Canvas and drawImage it back into bounds, within the context and pixel budgets.',
            'For a still use exactly one task frame. For video use exactly duration_seconds*24 frames. A static camera may have one key at shot start; interpolated tracks begin at start_frame and end at the exclusive end_frame. Use absolute task frames and linear or smoothstep easing.',
            'Camera movement, local animation, fixed-camera animation and complete stillness are distinct valid choices. Preserve the reference objective and label ambiguous motion as a hypothesis.',
            'Do not claim completed or verified stages. Host layout/render/comparison receipts are separate; report pending, failed, excluded and unknown parts honestly.',
            'Changed-content transfer receives general principles only. Create a new world and layout; do not carry reference assets, geometry or a reproduction baseline.',
        ])
