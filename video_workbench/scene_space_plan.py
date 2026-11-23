"""Bounded inert scene plans and deterministic frame geometry.

This module does not paint, invoke a model, inspect pixels, grant runtimes or
publish knowledge.  Its conclusions are about declared data and geometry only.
Camera coordinates are world-space view centres; the principal viewing
direction is fixed.  Intervals are half-open and retain the global frame clock.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re

from agent_service.contracts import ServiceError, canonical_bytes, digest

PLAN_SCHEMA = 'video.scene-space-plan.v1'
FROZEN_SCHEMA = 'video.scene-space-frozen-plan.v1'
FRAME_SCHEMA = 'video.scene-space-frame.v1'
REVISION_SCHEMA = 'video.scene-space-plan-revision.v1'
FPS = 24
MAX_DURATION_FRAMES = 300 * FPS
MAX_WORLDS = 16
MAX_SHOTS = 16
MAX_CAMERA_KEYS = 16
MAX_LOCAL_TRACKS = 64
MAX_LOCAL_KEYS = 32
MAX_PREVIEW_FRAMES = 512
MAX_PLAN_BYTES = 8 * 1024 * 1024
MAX_VALIDATION_WORK_UNITS = 5_000_000
DEFAULT_CAPS = dict(model_calls=1000, model_tokens=100_000_000,
    render_seconds=3600, cached_pixels=64 * 1024 * 1024,
    temporary_bytes=512 * 1024 * 1024, revisions=32)
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_PROPERTIES = frozenset({'x', 'y', 'rotation', 'scale', 'opacity'})
_EASINGS = frozenset({'linear', 'smoothstep'})


def _require(value, code='scene_space_plan_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def _fields(value, required, optional=()):
    _require(type(value) is dict and set(required) <= set(value)
        and set(value) <= set(required) | set(optional))


def _number(value, minimum=-1_000_000, maximum=1_000_000):
    # Bound integers before math.isfinite, which converts huge ints to float.
    _require(type(value) in (int, float) and minimum <= value <= maximum
        and math.isfinite(value))
    return value


def _integer(value, minimum, maximum):
    _require(type(value) is int and minimum <= value <= maximum)
    return value


def _identifier(value):
    _require(type(value) is str and _ID.fullmatch(value) is not None)
    return value


def _text(value, maximum=4000):
    _require(type(value) is str and 0 < len(value.strip()) <= maximum
        and not any((ord(c) < 32 and c not in '\n\r\t') or ord(c) == 127
                    or 0xD800 <= ord(c) <= 0xDFFF for c in value))
    return value


def _world_api():
    from agent_service.scene_space_contract import validate_world, world_sha256, world_geometry
    return validate_world, world_sha256, world_geometry


def _budget(value, caps=None):
    _fields(value, {'model', 'render', 'revisions'})
    limits = dict(DEFAULT_CAPS)
    if caps is not None:
        _require(type(caps) is dict and set(caps) <= set(limits))
        for key, item in caps.items():
            _number(item, 0, limits[key])
            limits[key] = item
    model, render, revisions = value['model'], value['render'], value['revisions']
    _fields(model, {'limit_calls', 'used_calls', 'limit_tokens', 'used_tokens'})
    _integer(model['limit_calls'], 0, int(limits['model_calls']))
    _integer(model['used_calls'], 0, model['limit_calls'])
    _integer(model['limit_tokens'], 0, int(limits['model_tokens']))
    if model['used_tokens'] is not None:
        _integer(model['used_tokens'], 0, int(limits['model_tokens']))
    _require(model['used_tokens'] is None
        or model['used_tokens'] <= model['limit_tokens'], 'scene_space_model_budget_exceeded')
    _fields(render, {'limit_seconds', 'used_seconds', 'max_cached_pixels', 'max_temporary_bytes'})
    _number(render['limit_seconds'], 0, limits['render_seconds'])
    _number(render['used_seconds'], 0, render['limit_seconds'])
    _integer(render['max_cached_pixels'], 1, int(limits['cached_pixels']))
    _integer(render['max_temporary_bytes'], 1, int(limits['temporary_bytes']))
    _fields(revisions, {'limit', 'used'})
    _integer(revisions['limit'], 0, int(limits['revisions']))
    _integer(revisions['used'], 0, revisions['limit'])
    return deepcopy(value)


def default_budget(*, model_calls=16, render_seconds=3600,
                   cached_pixels=16 * 1024 * 1024, temporary_bytes=128 * 1024 * 1024,
                   revisions=4, model_tokens=1_000_000):
    """Explicit finite caps for a new plan with no model calls or rendering."""
    return _budget(dict(model=dict(limit_calls=model_calls, used_calls=0,
        limit_tokens=model_tokens, used_tokens=0), render=dict(
        limit_seconds=render_seconds, used_seconds=0, max_cached_pixels=cached_pixels,
        max_temporary_bytes=temporary_bytes), revisions=dict(limit=revisions, used=0)))


def _provenance(value):
    _fields(value, {'origin', 'source_refs', 'evidence_scope', 'motion_observed',
                    'audio_observed', 'whole_video_watched'})
    _require(type(value['origin']) is str and value['origin'] in {'authored', 'generated', 'inferred', 'observed'})
    _require(type(value['evidence_scope']) is str and value['evidence_scope'] in {'scene_data_only', 'static_image', 'sampled_frames_only',
        'timestamped_sequence', 'observed_bounded_clip', 'whole_video'})
    _require(type(value['source_refs']) is list and len(value['source_refs']) <= 128)
    seen = set()
    for row in value['source_refs']:
        _fields(row, {'id', 'sha256'})
        _identifier(row['id'])
        _require(type(row['sha256']) is str and _SHA.fullmatch(row['sha256']) is not None)
        identity = row['id'], row['sha256']
        _require(identity not in seen)
        seen.add(identity)
    # Phase one has no observation adapter.  It may retain source-scope labels,
    # but does not certify playback, motion or sound by describing a camera.
    _require(all(value[key] is False for key in
        ('motion_observed', 'audio_observed', 'whole_video_watched')),
        'scene_space_observation_not_admitted')
    if value['origin'] in {'inferred', 'observed'}:
        _require(bool(value['source_refs']), 'scene_space_source_evidence_required')
    return deepcopy(value)


def default_provenance():
    return dict(origin='generated', source_refs=[], evidence_scope='scene_data_only',
        motion_observed=False, audio_observed=False, whole_video_watched=False)


def _keys(rows, start, end, *, camera):
    _require(type(rows) is list and 1 <= len(rows) <= (MAX_CAMERA_KEYS if camera else MAX_LOCAL_KEYS))
    previous = start - 1
    result = []
    for row in rows:
        _fields(row, {'frame', 'x', 'y', 'zoom'} if camera else {'frame', 'value'}, {'easing'})
        _integer(row['frame'], start, end)
        _require(row['frame'] > previous, 'scene_space_keyframe_order_invalid')
        previous = row['frame']
        normalized = dict(row, easing=row.get('easing', 'linear'))
        _require(type(normalized['easing']) is str and normalized['easing'] in _EASINGS)
        if camera:
            _number(row['x']); _number(row['y']); _number(row['zoom'], .25, 8)
        else:
            _number(row['value'])
        result.append(normalized)
    _require(result[0]['frame'] == start, 'scene_space_keyframe_start_missing')
    _require(len(result) == 1 or result[-1]['frame'] == end,
        'scene_space_keyframe_end_missing')
    return result


def _interpolate(rows, frame, names):
    if frame <= rows[0]['frame'] or len(rows) == 1:
        return {name: rows[0][name] for name in names}
    if frame >= rows[-1]['frame']:
        return {name: rows[-1][name] for name in names}
    after = next(i for i, row in enumerate(rows) if row['frame'] > frame)
    left, right = rows[after - 1], rows[after]
    t = (frame - left['frame']) / (right['frame'] - left['frame'])
    if right['easing'] == 'smoothstep':
        t = t * t * (3 - 2 * t)
    return {name: left[name] + (right[name] - left[name]) * t for name in names}


def _camera(shot, frame):
    return _interpolate(shot['camera']['keyframes'], frame, ('x', 'y', 'zoom'))


def _track_values(shot, frame):
    return {(row['target_id'], row['property']):
        _interpolate(row['keyframes'], frame, ('value',))['value']
        for row in shot['local_animation']}


def _layer_transform(world, layer, camera, viewport):
    bounds = world['space']['bounds']
    origin = bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2
    parallax = layer['parallax']
    scale = 1 + (camera['zoom'] - 1) * parallax
    _require(scale > 0, 'scene_space_projection_singular')
    cx = origin[0] + (camera['x'] - origin[0]) * parallax
    cy = origin[1] + (camera['y'] - origin[1]) * parallax
    return [scale, 0., 0., scale, viewport['width'] / 2 - cx * scale,
            viewport['height'] / 2 - cy * scale]


def _multiply(a, b):
    return [a[0]*b[0]+a[2]*b[1], a[1]*b[0]+a[3]*b[1],
        a[0]*b[2]+a[2]*b[3], a[1]*b[2]+a[3]*b[3],
        a[0]*b[4]+a[2]*b[5]+a[4], a[1]*b[4]+a[3]*b[5]+a[5]]


def _point(matrix, point):
    x, y = point
    return [matrix[0]*x+matrix[2]*y+matrix[4], matrix[1]*x+matrix[3]*y+matrix[5]]


def _aabb(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return dict(x=min(xs), y=min(ys), width=max(xs)-min(xs), height=max(ys)-min(ys))


def _inside(inner, outer, epsilon=1e-7):
    return (inner['x'] >= outer['x']-epsilon and inner['y'] >= outer['y']-epsilon
        and inner['x']+inner['width'] <= outer['x']+outer['width']+epsilon
        and inner['y']+inner['height'] <= outer['y']+outer['height']+epsilon)


def _intersects(a, b):
    return (a['x'] <= b['x']+b['width'] and b['x'] <= a['x']+a['width']
        and a['y'] <= b['y']+b['height'] and b['y'] <= a['y']+a['height'])


def _spatial_failure(code, shot, frame, *, relation_id=None, object_ids=()):
    error = ServiceError(code, 422)
    error.details = dict(shot_id=shot['id'], frame=frame, object_ids=list(object_ids)[:8])
    if relation_id is not None:
        error.details['relation_id'] = relation_id
    raise error


def _projection_relations(world, geometry, matrices, shot, frame):
    """Check declared ties and size ratios after each layer's camera projection.

    Support regions live in the supported object's layer, so projecting both
    by that layer preserves the world-space support check. Occlusion ordering
    is immutable in this version and is checked by validate_world. These are
    geometric constraints, never evidence of painted pixels or visible overlap.
    """
    for obj in world['objects']:
        if obj['parent_id'] is not None:
            child_matrix = matrices[obj['layer_id']]
            parent_matrix = matrices[geometry[obj['parent_id']]['layer_id']]
            if any(not math.isclose(a, b, rel_tol=0, abs_tol=1e-8)
                   for a, b in zip(child_matrix, parent_matrix)):
                _spatial_failure('scene_space_parent_projection_mismatch', shot, frame,
                    object_ids=[obj['id'], obj['parent_id']])
    for relation in world['relations']:
        kind = relation['kind']
        if kind == 'attachment':
            obj = geometry[relation['object_id']]
            target = geometry[relation['target_object_id']]
            left_matrix, right_matrix = matrices[obj['layer_id']], matrices[target['layer_id']]
            left = _point(left_matrix, obj['anchors'][relation['anchor']])
            right = _point(right_matrix, target['anchors'][relation['target_anchor']])
            tolerance = relation['tolerance'] * max(left_matrix[0], right_matrix[0])
            if math.dist(left, right) > tolerance + 1e-7:
                _spatial_failure('scene_space_projected_attachment_mismatch', shot, frame,
                    relation_id=relation['id'], object_ids=[relation['object_id'], relation['target_object_id']])
        elif kind == 'scale':
            def measure(identity):
                obj = geometry[identity]
                scale = matrices[obj['layer_id']][0]
                rect = obj['bounds']
                return (rect['width'] * rect['height'] * scale * scale
                    if relation['metric'] == 'area' else rect[relation['metric']] * scale)
            ratio = measure(relation['object_id']) / measure(relation['reference_object_id'])
            if not relation['min_ratio'] - 1e-7 <= ratio <= relation['max_ratio'] + 1e-7:
                _spatial_failure('scene_space_projected_scale_mismatch', shot, frame,
                    relation_id=relation['id'], object_ids=[relation['object_id'], relation['reference_object_id']])


def validate_plan(value, *, caps=None, _check_coverage=True):
    """Validate a complete automatic plan without altering its source object."""
    _fields(value, {'schema', 'id', 'revision', 'fps', 'duration_frames', 'viewport',
                    'worlds', 'shots', 'budget', 'provenance'})
    _require(value['schema'] == PLAN_SCHEMA)
    _identifier(value['id']); _integer(value['revision'], 1, 1_000_000)
    _require(type(value['fps']) is int and value['fps'] == FPS)
    _integer(value['duration_frames'], 1, MAX_DURATION_FRAMES)
    _fields(value['viewport'], {'width', 'height'})
    _integer(value['viewport']['width'], 1, 1280)
    _integer(value['viewport']['height'], 1, 720)
    _require(type(value['worlds']) is list and 1 <= len(value['worlds']) <= MAX_WORLDS)
    validate_world, _, _ = _world_api()
    result = deepcopy(value)
    result['worlds'] = [validate_world(world) for world in value['worlds']]
    worlds = {world['id']: world for world in result['worlds']}
    _require(len(worlds) == len(result['worlds']), 'scene_space_world_identity_duplicate')
    result['budget'] = _budget(value['budget'], caps)
    result['provenance'] = _provenance(value['provenance'])
    _require(type(value['shots']) is list and 1 <= len(value['shots']) <= MAX_SHOTS)
    shots, seen, cursor = [], set(), 0
    for item in value['shots']:
        _fields(item, {'id', 'world_id', 'world_revision', 'start_frame', 'end_frame',
                       'camera', 'local_animation'}, {'transition'})
        _identifier(item['id']); _require(item['id'] not in seen)
        seen.add(item['id'])
        _identifier(item['world_id'])
        _require(item['world_id'] in worlds, 'scene_space_world_reference_missing')
        world = worlds[item['world_id']]
        _integer(item['world_revision'], 1, 1_000_000)
        _require(item['world_revision'] == world['revision'], 'scene_space_world_revision_changed', 409)
        _integer(item['start_frame'], 0, value['duration_frames']-1)
        _integer(item['end_frame'], item['start_frame']+1, value['duration_frames'])
        _require(item['start_frame'] == cursor, 'scene_space_shot_partition_invalid')
        cursor = item['end_frame']
        shot = deepcopy(item)
        shot['transition'] = item.get('transition', 'cut')
        _require(type(shot['transition']) is str and shot['transition'] in {'cut', 'continuous'})
        _fields(item['camera'], {'keyframes', 'max_zoom'})
        _number(item['camera']['max_zoom'], .25, 8)
        shot['camera']['keyframes'] = _keys(item['camera']['keyframes'],
            item['start_frame'], item['end_frame'], camera=True)
        _require(all(row['zoom'] <= item['camera']['max_zoom'] for row in shot['camera']['keyframes']),
            'scene_space_magnification_exceeded')
        _require(type(item['local_animation']) is list and len(item['local_animation']) <= MAX_LOCAL_TRACKS)
        objects = {obj['id'] for obj in world['objects']}
        tracks, identities, properties = [], set(), set()
        for track in item['local_animation']:
            _fields(track, {'id', 'target_id', 'property', 'keyframes'})
            _identifier(track['id'])
            _identifier(track['target_id'])
            _require(track['id'] not in identities and track['target_id'] in objects
                and type(track['property']) is str and track['property'] in _PROPERTIES,
                'scene_space_animation_target_invalid')
            identities.add(track['id'])
            target = track['target_id'], track['property']
            _require(target not in properties, 'scene_space_animation_property_conflict')
            properties.add(target)
            normalized = deepcopy(track)
            normalized['keyframes'] = _keys(track['keyframes'], item['start_frame'], item['end_frame'], camera=False)
            if track['property'] == 'scale':
                _require(all(.01 <= key['value'] <= 16 for key in normalized['keyframes']))
            if track['property'] == 'opacity':
                _require(all(0 <= key['value'] <= 1 for key in normalized['keyframes']))
            tracks.append(normalized)
        shot['local_animation'] = tracks
        if shots and shot['transition'] == 'continuous':
            before = shots[-1]
            _require(before['world_id'] == shot['world_id'], 'scene_space_continuity_world_changed')
            left = _camera(before, before['end_frame'])
            right = _camera(shot, shot['start_frame'])
            _require(all(math.isclose(left[key], right[key], abs_tol=1e-8)
                for key in ('x', 'y', 'zoom')), 'scene_space_camera_discontinuity')
            old_tracks, new_tracks = _track_values(before, before['end_frame']), _track_values(shot, shot['start_frame'])
            for key in old_tracks.keys() | new_tracks.keys():
                default = 1 if key[1] in {'scale', 'opacity'} else 0
                _require(math.isclose(old_tracks.get(key, default), new_tracks.get(key, default), abs_tol=1e-8),
                    'scene_space_local_animation_discontinuity')
        shots.append(shot)
    _require(cursor == value['duration_frames'], 'scene_space_shot_partition_invalid')
    result['shots'] = shots
    _require(set(worlds) == {shot['world_id'] for shot in shots}, 'scene_space_unused_world')
    try:
        _require(len(canonical_bytes(result)) <= MAX_PLAN_BYTES, 'scene_space_plan_bytes_exceeded')
    except (TypeError, ValueError, RecursionError):
        raise ServiceError('scene_space_plan_invalid', 422) from None
    _representative_frames(result, MAX_PREVIEW_FRAMES)
    if _check_coverage:
        _required_coverage(result)
    return result


def _select_shot(plan, frame):
    if frame == plan['duration_frames']:
        return plan['shots'][-1]
    return next(shot for shot in plan['shots'] if shot['start_frame'] <= frame < shot['end_frame'])


def _split_intervals(plan, max_seconds):
    _number(max_seconds, 1/FPS, 60)
    length = max_seconds * FPS
    _require(int(length) == length, 'scene_space_interval_clock_invalid')
    length = int(length)
    worlds = {world['id']: world for world in plan['worlds']}
    # These worlds and the plan are already normalized. Hash once regardless of
    # whether execution uses five long intervals or thousands of short ones.
    world_hashes = {identity: digest(world) for identity, world in worlds.items()}
    plan_hash = digest(plan)
    result = []
    for shot in plan['shots']:
        start = shot['start_frame']
        while start < shot['end_frame']:
            end = min(start + length, shot['end_frame'])
            result.append(dict(id=f"{shot['id']}-part-{len(result)+1}",
                shot_id=shot['id'], world_id=shot['world_id'],
                world_revision=shot['world_revision'], world_sha256=world_hashes[shot['world_id']],
                plan_id=plan['id'], plan_revision=plan['revision'], plan_sha256=plan_hash,
                start_frame=start, end_frame=end, fps=FPS,
                absolute_clock=True, duration_frames=end-start))
            start = end
    return result


def split_intervals(plan, max_seconds=60):
    return _split_intervals(validate_plan(plan), max_seconds)


def _representative_frames(plan, limit):
    _integer(limit, 1, MAX_PREVIEW_FRAMES)
    mandatory = {0, plan['duration_frames']-1}
    for shot in plan['shots']:
        mandatory.update((shot['start_frame'], shot['end_frame']-1))
        for row in shot['camera']['keyframes']:
            mandatory.add(min(row['frame'], shot['end_frame']-1))
        for track in shot['local_animation']:
            for row in track['keyframes']:
                mandatory.add(min(row['frame'], shot['end_frame']-1))
    for interval in _split_intervals(plan, 60):
        mandatory.update((interval['start_frame'], interval['end_frame']-1))
    _require(len(mandatory) <= limit, 'scene_space_preview_frame_budget_exceeded')
    return sorted(mandatory)


def representative_frames(plan, limit=MAX_PREVIEW_FRAMES):
    checked = _resolved_plan(plan)[0]
    return _representative_frames(checked, limit)


def _validation_work(plan):
    """Conservative operation units, not measured CPU time or runtime grants.

    Dynamic geometry currently uses full world validation. Account explicitly
    for its polygon self-intersection comparisons and support-region scans so
    a tiny animation cannot multiply complex static topology by 7200 unnoticed.
    """
    costs = {}
    for world in plan['worlds']:
        regions = {region['id']: region for region in world['layout']['regions']}
        polygons = [region['points'] for region in regions.values() if region['kind'] == 'polygon']
        topology = sum(len(points) * max(0, len(points) - 3) // 2 for points in polygons)
        support = sum(len(regions[row['region_id']]['points'])
            if regions[row['region_id']]['kind'] == 'polygon' else 4
            for row in world['relations'] if row['kind'] == 'support')
        anchors = sum(len(obj['anchors']) for obj in world['objects'])
        # Full world_geometry validates and resolves geometry twice; each
        # polygon intersection requires several cross products and distances.
        geometry = (8*len(world['objects']) + 2*anchors + 4*len(world['relations'])
            + 4*topology + support + sum(len(points) for points in polygons)
            + 4*len(world['layout']['points']) + 4*len(regions)
            + (len(canonical_bytes(world)) + 63)//64)
        projection = 4*len(world['layers']) + len(world['objects']) + 4*len(world['relations'])
        costs[world['id']] = geometry, projection
    # Include a few bounded structural/normalization passes as well as the scan.
    total = sum(4*geometry for geometry, _ in costs.values())
    for shot in plan['shots']:
        geometry, projection = costs[shot['world_id']]
        frames = shot['end_frame'] - shot['start_frame'] + 1
        dynamic = any(track['property'] != 'opacity' for track in shot['local_animation'])
        total += frames * (projection + (geometry if dynamic else 0))
    if total > MAX_VALIDATION_WORK_UNITS:
        error = ServiceError('scene_space_validation_work_budget_exceeded', 422)
        error.details = dict(estimated_work_units=total, limit_work_units=MAX_VALIDATION_WORK_UNITS,
            measured=False, repair='reduce_geometry_animation_frames_or_scene_complexity')
        raise error
    return total


def _required_coverage(plan):
    validation_work = _validation_work(plan)
    worlds = {world['id']: world for world in plan['worlds']}
    _, _, world_geometry = _world_api()
    static_geometry = {identity: world_geometry(world) for identity, world in worlds.items()}
    rows = {}
    for shot in plan['shots']:
        world = worlds[shot['world_id']]
        moving_geometry = any(track['property'] != 'opacity' for track in shot['local_animation'])
        for layer in world['layers']:
            identity = world['id'], layer['id']
            rows.setdefault(identity, dict(world_id=world['id'], layer_id=layer['id'],
                cache_policy=layer['cache_policy'], min_x=math.inf, min_y=math.inf,
                max_x=-math.inf, max_y=-math.inf, max_scale=0))
        # Bound all discrete task frames and the ending boundary. A camera can
        # reveal uncovered artwork or break cross-layer ties between keyframes.
        for frame in range(shot['start_frame'], shot['end_frame']+1):
            camera = _camera(shot, frame)
            matrices = {}
            for layer in world['layers']:
                row = rows[(world['id'], layer['id'])]
                matrix = _layer_transform(world, layer, camera, plan['viewport'])
                matrices[layer['id']] = matrix
                scale, tx, ty = matrix[0], matrix[4], matrix[5]
                rect = dict(x=-tx/scale, y=-ty/scale,
                    width=plan['viewport']['width']/scale, height=plan['viewport']['height']/scale)
                _require(_inside(rect, world['space']['bounds']), 'scene_space_camera_outside_artwork')
                row['min_x'] = min(row['min_x'], rect['x'])
                row['min_y'] = min(row['min_y'], rect['y'])
                row['max_x'] = max(row['max_x'], rect['x'] + rect['width'])
                row['max_y'] = max(row['max_y'], rect['y'] + rect['height'])
                row['max_scale'] = max(row['max_scale'], scale)
            if moving_geometry:
                animated, _ = _animated_world(world, shot, frame)
                try:
                    geometry = world_geometry(animated)
                except ServiceError as error:
                    error.details = dict(getattr(error, 'details', {}), shot_id=shot['id'], frame=frame)
                    raise
            else:
                geometry = static_geometry[world['id']]
            _projection_relations(world, geometry, matrices, shot, frame)
    result, cached_pixels = [], 0
    viewport_pixels = plan['viewport']['width'] * plan['viewport']['height']
    for row in rows.values():
        min_x, min_y = row.pop('min_x'), row.pop('min_y')
        rect = dict(x=min_x, y=min_y, width=row.pop('max_x') - min_x,
            height=row.pop('max_y') - min_y)
        width, height = math.ceil(rect['width']*row['max_scale']), math.ceil(rect['height']*row['max_scale'])
        pixels = width*height
        row.update(bounds=rect, raster_width=width, raster_height=height,
                   pixels=pixels, static_cache_pixels=pixels if row['cache_policy']=='static' else 0)
        cached_pixels += row['static_cache_pixels']
        result.append(row)
    # RGBA estimate: one largest overscan work surface, viewport composition and
    # display targets. Renderer/codec/source memory is explicitly unmeasured.
    temporary_bytes = (max(row['pixels'] for row in result) + 2 * viewport_pixels) * 4
    _require(cached_pixels <= plan['budget']['render']['max_cached_pixels'],
        'scene_space_cache_budget_exceeded')
    _require(temporary_bytes <= plan['budget']['render']['max_temporary_bytes'],
        'scene_space_temporary_budget_exceeded')
    return dict(schema='video.scene-space-coverage.v1', layers=result,
        total_static_cached_pixels=cached_pixels, bounded_temporary_bytes=temporary_bytes,
        estimated_temporary_bytes=temporary_bytes, temporary_memory_measured=False,
        estimated_validation_work_units=validation_work,
        max_validation_work_units=MAX_VALIDATION_WORK_UNITS, validation_work_measured=False,
        scope='declared_layer_view_coverage_not_measured_render_memory',
        sampled_task_frames=plan['duration_frames'], spatial_task_frames_checked=True,
        continuous_between_frames_verified=False)


def required_coverage(plan):
    return _required_coverage(_resolved_plan(plan)[0])


def _world_bindings(plan):
    return {world['id']: dict(revision=world['revision'], sha256=digest(world)) for world in plan['worlds']}


def freeze_plan(value, *, max_interval_seconds=60):
    plan = validate_plan(value, _check_coverage=False)
    body = dict(schema=FROZEN_SCHEMA, plan=plan, plan_sha256=digest(plan),
        world_bindings=_world_bindings(plan), intervals=_split_intervals(plan, max_interval_seconds),
        coverage=_required_coverage(plan), max_interval_seconds=max_interval_seconds)
    return dict(body, sha256=digest(body))


def _resolved_plan(value):
    if type(value) is dict and value.get('schema') == FROZEN_SCHEMA:
        _fields(value, {'schema', 'plan', 'plan_sha256', 'world_bindings', 'intervals',
                        'coverage', 'max_interval_seconds', 'sha256'})
        try:
            actual_sha256 = digest({k:v for k,v in value.items() if k!='sha256'})
        except (TypeError, ValueError, OverflowError, RecursionError):
            raise ServiceError('scene_space_plan_invalid', 422) from None
        _require(value['sha256'] == actual_sha256,
            'scene_space_frozen_plan_changed', 409)
        _require(digest(value['plan']) == value['plan_sha256'],
            'scene_space_frozen_plan_changed', 409)
        checked = validate_plan(value['plan'], _check_coverage=False)
        _require(digest(checked) == value['plan_sha256'] and _world_bindings(checked) == value['world_bindings'],
            'scene_space_frozen_plan_changed', 409)
        _require(_split_intervals(checked, value['max_interval_seconds']) == value['intervals']
            and _required_coverage(checked) == value['coverage'], 'scene_space_frozen_plan_changed', 409)
        return checked, deepcopy(value['intervals'])
    checked = validate_plan(value)
    return checked, _split_intervals(checked, 60)


def _animated_world(world, shot, frame):
    animated = deepcopy(world)
    values = _track_values(shot, frame)
    opacity = {}
    for obj in animated['objects']:
        transform = obj['transform']
        for name in ('x', 'y', 'rotation'):
            transform[name] += values.get((obj['id'], name), 0)
        scale = values.get((obj['id'], 'scale'), 1)
        transform['scale_x'] *= scale
        transform['scale_y'] *= scale
        opacity[obj['id']] = values.get((obj['id'], 'opacity'), 1)
    return animated, opacity


class SceneEvaluator:
    """Validate once, then evaluate independent absolute frames without drawing."""
    def __init__(self, value):
        self._plan, self._intervals = _resolved_plan(value)
        self._worlds = {world['id']: world for world in self._plan['worlds']}
        self._bindings = _world_bindings(self._plan)
        _, _, world_geometry = _world_api()
        self._static_geometry = {identity: world_geometry(world) for identity, world in self._worlds.items()}
        self.plan_sha256 = digest(self._plan)

    def frame(self, frame):
        _integer(frame, 0, self._plan['duration_frames'])
        shot = _select_shot(self._plan, frame)
        world = self._worlds[shot['world_id']]
        camera = _camera(shot, frame)
        if any(track['property'] != 'opacity' for track in shot['local_animation']):
            animated, opacity = _animated_world(world, shot, frame)
            _, _, world_geometry = _world_api()
            geometry = world_geometry(animated)
        else:
            values = _track_values(shot, frame)
            opacity = {obj['id']: values.get((obj['id'], 'opacity'), 1) for obj in world['objects']}
            geometry = self._static_geometry[world['id']]
        viewport = self._plan['viewport']
        screen = dict(x=0, y=0, width=viewport['width'], height=viewport['height'])
        layers, by_layer = [], {}
        regions = {r['id']:r for r in world['layout']['regions']}
        for layer in sorted(world['layers'], key=lambda r:r['order']):
            matrix = _layer_transform(world, layer, camera, viewport)
            mask = deepcopy(regions.get(layer['mask_region_id']))
            row = dict(layer, screen_matrix=matrix, mask_region=mask)
            layers.append(row); by_layer[layer['id']] = row
        objects = []
        source_objects = {obj['id']: obj for obj in world['objects']}
        inherited_opacity = {}
        def object_opacity(identity):
            if identity not in inherited_opacity:
                parent = source_objects[identity]['parent_id']
                inherited_opacity[identity] = opacity[identity] * (object_opacity(parent) if parent else 1)
            return inherited_opacity[identity]
        for obj in sorted(world['objects'], key=lambda o:(by_layer[o['layer_id']]['order'],o['order'],o['id'])):
            item = geometry[obj['id']]
            layer = by_layer[obj['layer_id']]
            matrix = _multiply(layer['screen_matrix'], item['matrix'])
            bounds = obj['bounds']
            points = [[bounds['x'],bounds['y']], [bounds['x']+bounds['width'],bounds['y']],
                [bounds['x']+bounds['width'],bounds['y']+bounds['height']], [bounds['x'],bounds['y']+bounds['height']]]
            corners = [_point(matrix, p) for p in points]
            screen_bounds = _aabb(corners)
            screen_anchors = {name:_point(layer['screen_matrix'],p) for name,p in item['anchors'].items()}
            objects.append(dict(id=obj['id'], kind=obj['kind'], layer_id=obj['layer_id'],
                parent_id=obj['parent_id'], depth=deepcopy(obj['depth']), order=obj['order'],
                world_matrix=deepcopy(item['matrix']), screen_matrix=matrix,
                screen_bounds=screen_bounds, screen_corners=corners, screen_anchors=screen_anchors,
                opacity=layer['opacity']*object_opacity(obj['id']),
                potentially_visible=_intersects(screen_bounds,screen) and layer['opacity']*object_opacity(obj['id'])>0,
                visibility_scope='bounds_only', pixels_observed=False))
        interval = next((row for row in self._intervals
            if row['start_frame']<=frame<row['end_frame']), self._intervals[-1])
        return dict(schema=FRAME_SCHEMA, frame=frame, time_seconds=frame/FPS, fps=FPS,
            plan_id=self._plan['id'], plan_revision=self._plan['revision'], plan_sha256=self.plan_sha256,
            world_id=world['id'], world_revision=world['revision'], shot_id=shot['id'],
            world_sha256=self._bindings[world['id']]['sha256'],
            interval_id=interval['id'], viewport=deepcopy(viewport), camera=camera,
            layers=layers, objects=objects, geometry_scope='declared_scene_geometry_only',
            visual_observed=False, motion_observed=False, audio_observed=False)

    def frames(self, frames):
        _require(type(frames) in (list, tuple) and 1 <= len(frames) <= MAX_PREVIEW_FRAMES)
        return [self.frame(frame) for frame in frames]


def compile_evaluator(plan_or_frozen):
    return SceneEvaluator(plan_or_frozen)


def evaluate_frame(plan_or_frozen, frame):
    return SceneEvaluator(plan_or_frozen).frame(frame)


def evaluate_frames(plan_or_frozen, frames):
    return SceneEvaluator(plan_or_frozen).frames(frames)


def automatic_plan(worlds, *, id, viewport, duration_frames, intents, budget, provenance=None):
    """Compile host-selected creative intents into complete shots automatically.

    Intents specify a world, relative weight, a focal area and an allowed view
    choice.  The compiler derives complete numerical camera keys and intervals;
    it supplies no compulsory pan, fog, birds or local-animation template.
    This is an internal data API, not a per-shot user-editing endpoint.
    """
    _require(type(worlds) is list and 1 <= len(worlds) <= MAX_WORLDS)
    validate_world, _, _ = _world_api()
    normalized = [validate_world(world) for world in worlds]
    by_id = {world['id']:world for world in normalized}
    _require(type(intents) is list and 1 <= len(intents) <= MAX_SHOTS)
    _integer(duration_frames, len(intents), MAX_DURATION_FRAMES)
    _fields(viewport, {'width','height'})
    _integer(viewport['width'],1,1280); _integer(viewport['height'],1,720)
    weights=[]
    for item in intents:
        _fields(item, {'world_id','weight','view','focus'},
            {'end_focus','zoom','end_zoom','max_zoom','local_animation','transition'})
        _identifier(item['world_id'])
        _require(item['world_id'] in by_id and type(item['view']) is str
            and item['view'] in {'hold','pan','push','pull','pan-zoom'})
        weights.append(_number(item['weight'],.001,1_000_000))
        _fields(item['focus'],{'x','y'})
        _number(item['focus']['x']); _number(item['focus']['y'])
        if 'end_focus' in item:
            _fields(item['end_focus'],{'x','y'})
            _number(item['end_focus']['x']); _number(item['end_focus']['y'])
        for key in ('zoom', 'end_zoom', 'max_zoom'):
            if key in item:
                _number(item[key], .25, 8)
        if 'transition' in item:
            _require(type(item['transition']) is str and item['transition'] in {'cut', 'continuous'})
        tracks = item.get('local_animation', [])
        _require(type(tracks) is list and len(tracks) <= MAX_LOCAL_TRACKS)
        for track in tracks:
            _fields(track, {'id', 'target_id', 'property', 'keyframes'})
            _identifier(track['id']); _identifier(track['target_id'])
            _require(type(track['property']) is str and track['property'] in _PROPERTIES)
            _require(type(track['keyframes']) is list
                and 1 <= len(track['keyframes']) <= MAX_LOCAL_KEYS)
            for key in track['keyframes']:
                _fields(key, {'at', 'value'}, {'easing'})
                _number(key['at'], 0, 1); _number(key['value'])
                if 'easing' in key:
                    _require(type(key['easing']) is str and key['easing'] in _EASINGS)
    # Give every intent one frame, then use the largest remainder rule.  The
    # exact total survives execution splitting without inventing new worlds.
    spare=duration_frames-len(intents)
    ideals=[spare*w/sum(weights)for w in weights]
    lengths=[1+math.floor(v)for v in ideals]
    for index in sorted(range(len(intents)),key=lambda i:(-(ideals[i]-math.floor(ideals[i])),i))[:duration_frames-sum(lengths)]:
        lengths[index]+=1
    shots=[];cursor=0
    for index,(intent,length)in enumerate(zip(intents,lengths)):
        world=by_id[intent['world_id']];focus=intent['focus']
        zoom=intent.get('zoom',1);_number(zoom,.25,8)
        finish=intent.get('end_focus',focus)
        final_zoom=intent.get('end_zoom',zoom)
        if intent['view']=='hold':
            _require(finish==focus and final_zoom==zoom,'scene_space_hold_intent_conflict')
        if intent['view']=='pan':
            _require(final_zoom==zoom,'scene_space_pan_intent_conflict')
        if intent['view']in {'push','pull'}:
            _require(finish==focus,'scene_space_zoom_intent_conflict')
            _require(final_zoom>zoom if intent['view']=='push'else final_zoom<zoom,
                'scene_space_zoom_intent_conflict')
        end=cursor+length
        keys=[dict(frame=cursor,x=focus['x'],y=focus['y'],zoom=zoom,easing='linear')]
        if finish!=focus or final_zoom!=zoom:
            keys.append(dict(frame=end,x=finish['x'],y=finish['y'],zoom=final_zoom,easing='smoothstep'))
        tracks=deepcopy(intent.get('local_animation',[]))
        # Intent keyframe positions are normalized fractions, so changing the
        # allocation does not leave stale absolute clocks in local tracks.
        for track in tracks:
            for key in track.get('keyframes',[]):
                fraction=key.pop('at',None)
                _number(fraction,0,1)
                key['frame']=cursor+round(fraction*length)
        shots.append(dict(id=f'shot-{index+1}',world_id=world['id'],world_revision=world['revision'],
            start_frame=cursor,end_frame=end,camera=dict(keyframes=keys,max_zoom=intent.get('max_zoom',max(zoom,final_zoom))),
            local_animation=tracks,transition=intent.get('transition','cut')))
        cursor=end
    return validate_plan(dict(schema=PLAN_SCHEMA,id=id,revision=1,fps=FPS,duration_frames=duration_frames,
        viewport=deepcopy(viewport),worlds=normalized,shots=shots,budget=deepcopy(budget),
        provenance=deepcopy(provenance)if provenance is not None else default_provenance()))


def _same_budget_lineage(old, new):
    for section, keys in [('model',('limit_calls','limit_tokens')),('render',
        ('limit_seconds','max_cached_pixels','max_temporary_bytes')),('revisions',('limit',))]:
        _require(all(old[section][key]==new[section][key]for key in keys),
            'scene_space_budget_cap_changed',409)
    for section,key in [('model','used_calls'),('model','used_tokens'),('render','used_seconds')]:
        before,after=old[section][key],new[section][key]
        _require((before is None and after is None)
            or (before is not None and after is not None and after>=before),
            'scene_space_budget_history_reset',409)
    _require(new['revisions']['used']==old['revisions']['used']+1,
        'scene_space_revision_budget_reset',409)


def revise_plan(original, replacement, *, reason):
    before = _resolved_plan(original)[0]
    _text(reason)
    after = validate_plan(replacement)
    _require(after['id']==before['id'] and after['revision']==before['revision']+1,
        'scene_space_plan_revision_invalid',409)
    _same_budget_lineage(before['budget'],after['budget'])
    _require(after['provenance']==before['provenance'], 'scene_space_source_evidence_changed',409)
    old_worlds={w['id']:w for w in before['worlds']};new_worlds={w['id']:w for w in after['worlds']}
    changed_worlds=sorted(key for key in old_worlds.keys()|new_worlds.keys()
        if old_worlds.get(key)!=new_worlds.get(key))
    changed_objects=[];changed_layers=[]
    for world_id in changed_worlds:
        old=old_worlds.get(world_id,{});new=new_worlds.get(world_id,{})
        if old and new:
            _require(new['revision']==old['revision']+1,'scene_space_world_revision_invalid',409)
        for kind,out in [('objects',changed_objects),('layers',changed_layers)]:
            a={r['id']:r for r in old.get(kind,[])};b={r['id']:r for r in new.get(kind,[])}
            out.extend(world_id+'/'+key for key in sorted(a.keys()|b.keys())if a.get(key)!=b.get(key))
    old_shots={s['id']:s for s in before['shots']};new_shots={s['id']:s for s in after['shots']}
    global_change = any(before[key] != after[key] for key in ('viewport', 'fps', 'duration_frames'))
    affected=sorted(key for key in old_shots.keys()|new_shots.keys()
        if global_change or old_shots.get(key)!=new_shots.get(key)
        or old_shots.get(key,{}).get('world_id')in changed_worlds
        or new_shots.get(key,{}).get('world_id')in changed_worlds)
    # Parent, mask and relation edits can affect more than the directly edited
    # object. Invalidate the changed world's layer caches conservatively.
    caches=sorted({world_id+'/'+layer['id'] for world_id in changed_worlds
        for world in (old_worlds.get(world_id, {}), new_worlds.get(world_id, {}))
        for layer in world.get('layers', [])})
    if global_change or any(old_shots.get(key)!=new_shots.get(key)for key in affected):
        shot_worlds={s['world_id']for s in before['shots']+after['shots']if s['id']in affected}
        caches=sorted(set(caches)|{w['id']+'/'+layer['id']for w in before['worlds']+after['worlds']
            if w['id']in shot_worlds for layer in w['layers']})
    report=dict(schema=REVISION_SCHEMA,plan_id=after['id'],from_revision=before['revision'],
        to_revision=after['revision'],from_sha256=digest(before),to_sha256=digest(after),reason=reason,
        changed_world_ids=changed_worlds,changed_object_ids=changed_objects,changed_layer_ids=changed_layers,
        affected_object_ids=sorted({world_id+'/'+obj['id'] for world_id in changed_worlds
            for world in (old_worlds.get(world_id, {}), new_worlds.get(world_id, {}))
            for obj in world.get('objects', [])}),
        global_geometry_changed=global_change,
        affected_shot_ids=affected,invalidated_cache_ids=caches,
        required_rechecks=['spatial','coverage','deterministic_frames','visual_preview']if affected else [],
        reusable_world_ids=sorted(set(new_worlds)-set(changed_worlds)),
        budget_history_preserved=True,visual_acceptance=False)
    return dict(plan=after,revision=report,frozen=freeze_plan(after))


def technical_retry(frozen, expected_plan_sha256):
    _require(type(frozen)is dict and frozen.get('schema')==FROZEN_SCHEMA,
        'scene_space_frozen_plan_required',409)
    checked,_=_resolved_plan(frozen)
    _require(type(expected_plan_sha256)is str and _SHA.fullmatch(expected_plan_sha256)
        and digest(checked)==expected_plan_sha256,'scene_space_retry_plan_changed',409)
    return deepcopy(frozen)
