"""Bounded layout diagnostics for inert scene worlds, independent of painting.

The SVG is an inspection artifact. It never resolves a recipe reference, draws
ink, observes video/audio, or turns geometric checks into artistic acceptance.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import html
import json
import math
from pathlib import Path

from agent_service.contracts import ServiceError, canonical_bytes
from agent_service.scene_space_contract import validate_world, world_geometry, world_sha256
from video_workbench.scene_space_plan import (
    FROZEN_SCHEMA, compile_evaluator, freeze_plan, technical_retry,
)


REPORT_SCHEMA = 'video.scene-space-layout-report.v1'
MAX_PREVIEW_BYTES = 4 * 1024 * 1024
MAX_PREVIEW_FRAMES = 256
MAX_PREVIEW_TOTAL_BYTES = 32 * 1024 * 1024
PRODUCTION_PREVIEW_SCHEMA = 'video.scene-space-production-preview-plan.v1'
MAX_PRODUCTION_STATIC_FRAMES = 3
MAX_PRODUCTION_CLIPS = 2
MAX_PRODUCTION_CLIP_FRAMES = 48
PALETTE = ('#366b86', '#8c6541', '#547a59', '#80688c', '#a37b28', '#566879')


def inspect_world(world):
    """Report data/geometry validity without elevating it to visual evidence."""
    try:
        checked = validate_world(world)
        geometry = world_geometry(checked)
    except ServiceError as error:
        return dict(schema=REPORT_SCHEMA, status='invalid', world_sha256=None,
            checks=dict(contract='failed', spatial='not_completed'),
            issues=[dict(code=error.code, details=deepcopy(getattr(error, 'details', {})))],
            evidence_scope='scene_data_and_geometry_only', visual_quality='not_assessed',
            continuous_motion='not_observed', audio='not_observed', recipes_executed=0)
    return dict(schema=REPORT_SCHEMA, status='valid', world_id=checked['id'],
        world_revision=checked['revision'], world_sha256=world_sha256(checked),
        checks=dict(contract='passed', spatial='passed'), issues=[],
        object_count=len(checked['objects']), layer_count=len(checked['layers']),
        relation_count=len(checked['relations']),
        evidence_scope='scene_data_and_geometry_only', visual_quality='not_assessed',
        continuous_motion='not_observed', audio='not_observed', recipes_executed=0)


def _number(value):
    return format(float(value), '.12g')


def _points(points):
    return ' '.join(','.join(_number(value) for value in point) for point in points)


def _rect(rect, attributes=''):
    return '<rect x="{}" y="{}" width="{}" height="{}" {}/>'.format(
        *(_number(rect[key]) for key in ('x', 'y', 'width', 'height')), attributes)


def _text(x, y, text, *, color='#25333c', size=12):
    return '<text x="{}" y="{}" fill="{}" font-size="{}">{}</text>'.format(
        _number(x), _number(y), color, size, html.escape(str(text)))


def layout_svg(world, *, show_grid=False, show_anchors=True):
    """Draw object extents/anchors in world coordinates; masks are outlined.

    Layer colors distinguish identities in the diagnostic. They do not define
    the final style, ink density, layer count, opacity, or atmospheric depth.
    """
    if type(show_grid) is not bool or type(show_anchors) is not bool:
        raise ServiceError('scene_space_preview_options_invalid', 422)
    checked = validate_world(world)
    geometry = world_geometry(checked)
    bounds = checked['space']['bounds']
    x, y, width, height = (bounds[key] for key in ('x', 'y', 'width', 'height'))
    display_scale = min(1.0, 1280/width, 720/height)
    metadata = dict(schema='video.scene-space-layout-svg.v1', world_id=checked['id'],
        world_sha256=world_sha256(checked), scope='layout_only_masks_outlined',
        recipes_executed=0, visual_quality='not_assessed')
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="{} {} {} {}"'
             ' width="{}" height="{}" role="img">'.format(
                 *(_number(value) for value in (x, y, width, height,
                                                width*display_scale, height*display_scale))),
        '<title>{}</title>'.format(html.escape('Spatial layout: '+checked['id'])),
        '<metadata>{}</metadata>'.format(html.escape(canonical_bytes(metadata).decode('utf-8'))),
        _rect(bounds, 'fill="#f6f4ee"'),
        '<g font-family="sans-serif" stroke-linejoin="round">']
    regions = checked['layout']['regions']
    for region in regions:
        shape = region['kind']
        attrs = 'fill="none" stroke="#aaa69c" stroke-dasharray="6 5"'
        if shape == 'rect':
            parts.append(_rect(region['bounds'], attrs))
        elif shape == 'polygon':
            parts.append('<polygon points="{}" {}/>'.format(_points(region['points']), attrs))
    # The grid remains an opt-in diagnostic, outside the final artwork.
    grid = checked['layout'].get('grid')
    if show_grid and grid:
        origin, step = grid['origin'], grid['spacing']
        for column in range(grid['columns']):
            gx = origin[0] + column * step[0]
            parts.append('<path d="M {} {} V {}" stroke="#ddd9cd" fill="none"/>'.format(
                _number(gx), _number(origin[1]), _number(origin[1]+(grid['rows']-1)*step[1])))
        for row in range(grid['rows']):
            gy = origin[1] + row * step[1]
            parts.append('<path d="M {} {} H {}" stroke="#ddd9cd" fill="none"/>'.format(
                _number(origin[0]), _number(gy), _number(origin[0]+(grid['columns']-1)*step[0])))
    layers = sorted(checked['layers'], key=lambda layer: (layer['order'], layer['id']))
    for index, layer in enumerate(layers):
        color = PALETTE[index % len(PALETTE)]
        parts.append('<g data-layer-id="{}">'.format(html.escape(layer['id'], quote=True)))
        objects = sorted((obj for obj in checked['objects'] if obj['layer_id']==layer['id']),
                         key=lambda obj: (obj.get('order', 0), obj['id']))
        for obj in objects:
            item = geometry[obj['id']]
            matrix = ' '.join(_number(value) for value in item['matrix'])
            parts.append('<g data-object-id="{}" transform="matrix({})">'.format(
                html.escape(obj['id'], quote=True), matrix))
            parts.append(_rect(obj['bounds'],
                'fill="{}" fill-opacity="0.16" stroke="{}" stroke-width="1.5"'.format(color, color)))
            parts.append('</g>')
            box = item['bounds']
            parts.append(_text(box['x']+3, box['y']+14, obj['id'], color=color))
            if show_anchors:
                for name, point in sorted(item['anchors'].items()):
                    parts.append('<circle data-object-id="{}" data-anchor-id="{}" '
                        'cx="{}" cy="{}" r="3" fill="{}"/>'.format(
                            html.escape(obj['id'], quote=True), html.escape(name, quote=True),
                            _number(point[0]), _number(point[1]), color))
                    parts.append(_text(point[0]+5, point[1]-5, name, color=color, size=10))
        parts.append('</g>')
    parts.extend(['</g>', '</svg>'])
    result = '\n'.join(parts)+'\n'
    if len(result.encode('utf-8')) > MAX_PREVIEW_BYTES:
        raise ServiceError('scene_space_preview_size_exceeded', 413)
    return result


def write_world_preview(world, directory, *, show_grid=False):
    """Write deterministic inspection artifacts, never an executable scene."""
    out = Path(directory)
    report = inspect_world(world)
    raw = layout_svg(world, show_grid=show_grid).encode('utf-8') if report['status']=='valid' else None
    try:
        out.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise ServiceError('scene_space_preview_destination_exists', 409) from None
    if raw is not None:
        report = dict(report, artifact=dict(path='layout.svg', sha256=hashlib.sha256(raw).hexdigest()))
        (out/'layout.svg').write_bytes(raw)
    (out/'layout-report.json').write_bytes(canonical_bytes(report)+b'\n')
    return report


def preview_frames(plan, *, max_frames=MAX_PREVIEW_FRAMES):
    """Select boundaries and trajectory samples, never claim continuous proof.

    Required samples are not silently dropped to fit a budget. A caller must
    reduce the plan or explicitly allocate a larger bounded preview budget.
    """
    return _preview_frames(freeze_plan(plan), max_frames)


def _preview_frames(frozen, max_frames):
    if type(max_frames) is not int or not 1 <= max_frames <= MAX_PREVIEW_FRAMES:
        raise ServiceError('scene_space_preview_frame_budget_invalid', 422)
    checked = frozen['plan']
    selected = {0, checked['duration_frames']-1}
    for shot in checked['shots']:
        selected.update((shot['start_frame'], shot['end_frame']-1))
        tracks = [shot['camera']['keyframes']]
        tracks.extend(track['keyframes'] for track in shot['local_animation'])
        for keys in tracks:
            for key in keys:
                selected.add(min(key['frame'], shot['end_frame']-1))
            for left, right in zip(keys, keys[1:]):
                selected.add((left['frame']+right['frame'])//2)
    for interval in frozen['intervals']:
        selected.update((interval['start_frame'], interval['end_frame']-1))
    if len(selected) > max_frames:
        error = ServiceError('scene_space_preview_frame_budget_exceeded', 422)
        error.details = dict(required_frames=len(selected), max_frames=max_frames)
        raise error
    return sorted(selected)


def _key_values(keys, frame, names):
    if frame <= keys[0]['frame'] or len(keys) == 1:
        return {name: keys[0][name] for name in names}
    if frame >= keys[-1]['frame']:
        return {name: keys[-1][name] for name in names}
    right_index = next(index for index, key in enumerate(keys) if key['frame'] > frame)
    left, right = keys[right_index - 1], keys[right_index]
    fraction = (frame - left['frame']) / (right['frame'] - left['frame'])
    if right['easing'] == 'smoothstep':
        fraction = fraction * fraction * (3 - 2 * fraction)
    return {name: left[name] + fraction * (right[name] - left[name]) for name in names}


def _camera_change(before, after, viewport):
    return (abs(after['x'] - before['x']) / viewport['width']
        + abs(after['y'] - before['y']) / viewport['height']
        + abs(math.log(after['zoom'] / before['zoom'])))


def _local_change(before, after, track, objects):
    prop = track['property']
    if prop == 'scale':
        return abs(math.log(after / before))
    divisor = (objects[track['target_id']]['bounds']['width'] if prop == 'x' else
        objects[track['target_id']]['bounds']['height'] if prop == 'y' else
        180 if prop == 'rotation' else 1)
    return abs(after - before) / divisor


def _production_clip_candidates(checked):
    """Rank declared keyframe rates and cuts, without inspecting any pixels."""
    worlds = {world['id']: world for world in checked['worlds']}
    changes, boundaries = [], []
    viewport = checked['viewport']
    previous = None
    for shot in checked['shots']:
        objects = {obj['id']: obj for obj in worlds[shot['world_id']]['objects']}
        camera = shot['camera']['keyframes']
        for left, right in zip(camera, camera[1:]):
            magnitude = _camera_change(left, right, viewport)
            if magnitude:
                rate = magnitude / (right['frame'] - left['frame'])
                rate *= 1.5 if right['easing'] == 'smoothstep' else 1
                changes.append((rate, (left['frame'] + right['frame']) // 2, 'camera_change'))
        for track in shot['local_animation']:
            for left, right in zip(track['keyframes'], track['keyframes'][1:]):
                magnitude = _local_change(left['value'], right['value'], track, objects)
                if magnitude:
                    rate = magnitude / (right['frame'] - left['frame'])
                    rate *= 1.5 if right['easing'] == 'smoothstep' else 1
                    changes.append((rate, (left['frame'] + right['frame']) // 2,
                        'local_animation_change'))
        if previous is not None:
            before = _key_values(previous['camera']['keyframes'], previous['end_frame'] - 1,
                ('x', 'y', 'zoom'))
            after = _key_values(camera, shot['start_frame'], ('x', 'y', 'zoom'))
            local_jump = 0
            if previous['world_id'] == shot['world_id']:
                before_tracks = {(track['target_id'], track['property']): track
                    for track in previous['local_animation']}
                after_tracks = {(track['target_id'], track['property']): track
                    for track in shot['local_animation']}
                for identity in sorted(before_tracks.keys() | after_tracks.keys()):
                    left, right = before_tracks.get(identity), after_tracks.get(identity)
                    default = 1 if identity[1] in {'scale', 'opacity'} else 0
                    before_value = (_key_values(left['keyframes'], previous['end_frame'] - 1,
                        ('value',))['value'] if left else default)
                    after_value = (_key_values(right['keyframes'], shot['start_frame'],
                        ('value',))['value'] if right else default)
                    local_jump += _local_change(before_value, after_value, left or right, objects)
            risk = (previous['world_id'] != shot['world_id'], shot['transition'] == 'cut',
                _camera_change(before, after, viewport) + local_jump)
            boundaries.append((risk, shot['start_frame'], 'shot_boundary'))
        previous = shot
    # Equal scores use the earliest frame and a stable reason as tie-breakers.
    changes.sort(key=lambda row: (-row[0], row[1], row[2]))
    boundaries.sort(key=lambda row: (tuple(-part for part in row[0]), row[1]))
    return changes, boundaries


def _production_clips(checked, max_clip_frames):
    duration = checked['duration_frames']
    length = min(duration, max_clip_frames)
    changes, boundaries = _production_clip_candidates(checked)
    selected = []

    def add(candidate):
        _, center, reason = candidate
        start = max(0, min(center - length // 2, duration - length))
        end = start + length
        for clip in selected:
            covered = (clip['start_frame'] < center < clip['end_frame'] if reason == 'shot_boundary'
                else clip['start_frame'] <= start and end <= clip['end_frame'])
            if covered:
                if reason not in clip['reason'].split(','):
                    clip['reason'] += ',' + reason
                return
        if len(selected) < MAX_PRODUCTION_CLIPS:
            selected.append(dict(start_frame=start, end_frame=end, reason=reason))

    if changes:
        add(changes[0])
    # Reserve the other window for a boundary when both motion and cuts exist.
    for candidate in boundaries:
        add(candidate)
        if len(selected) == MAX_PRODUCTION_CLIPS:
            break
    for candidate in changes[1:]:
        if len(selected) == MAX_PRODUCTION_CLIPS:
            break
        add(candidate)
    if not selected:
        selected.append(dict(start_frame=0, end_frame=length, reason='static_plan'))
    return sorted(selected, key=lambda clip: (clip['start_frame'], clip['end_frame']))


def production_preview_plan(plan, *, max_static_frames=MAX_PRODUCTION_STATIC_FRAMES,
        max_clip_frames=MAX_PRODUCTION_CLIP_FRAMES):
    """Plan bounded production previews; do not render or authorize execution.

    Accept a raw plan or its complete frozen envelope. Required static samples
    cover start, greatest rendered camera zoom, end and every world's first
    appearance. An insufficient budget fails without silently omitting worlds.
    Clips are half-open global-frame windows, at most two and 48 frames each.
    Their selection uses declared keyframe rates and boundaries, not measured
    pixel motion, occlusion, artistic quality or an instruction to add movement.
    """
    if type(max_static_frames) is not int or not 1 <= max_static_frames <= MAX_PRODUCTION_STATIC_FRAMES:
        raise ServiceError('scene_space_preview_static_budget_invalid', 422)
    if type(max_clip_frames) is not int or not 2 <= max_clip_frames <= MAX_PRODUCTION_CLIP_FRAMES:
        raise ServiceError('scene_space_preview_clip_budget_invalid', 422)
    frozen = (technical_retry(plan, plan.get('plan_sha256'))
        if type(plan) is dict and plan.get('schema') == FROZEN_SCHEMA else freeze_plan(plan))
    checked = frozen['plan']
    last = checked['duration_frames'] - 1
    camera_frames = {min(key['frame'], shot['end_frame'] - 1)
        for shot in checked['shots'] for key in shot['camera']['keyframes']}
    camera_frames.update(shot['end_frame'] - 1 for shot in checked['shots'])
    # Piecewise linear/smoothstep camera tracks are monotone between keys.
    # Evaluate the actual drawable frame, rather than the exclusive end key.
    def zoom_at(frame):
        shot = next(shot for shot in checked['shots']
            if shot['start_frame'] <= frame < shot['end_frame'])
        return _key_values(shot['camera']['keyframes'], frame, ('zoom',))['zoom']
    greatest = max(camera_frames, key=lambda frame: (zoom_at(frame), frame))
    required, seen = {0, greatest, last}, set()
    for shot in checked['shots']:
        if shot['world_id'] not in seen:
            required.add(shot['start_frame'])
            seen.add(shot['world_id'])
    if len(required) > max_static_frames:
        error = ServiceError('scene_space_preview_coverage_budget_exceeded', 409)
        error.details = dict(required_frames=sorted(required), world_ids=sorted(seen),
            max_preview_frames=max_static_frames, retained_plan_sha256=frozen['plan_sha256'])
        raise error
    static_frames = sorted(required)
    clips = _production_clips(checked, max_clip_frames)
    report_frames = sorted(required | {frame for clip in clips
        for frame in (clip['start_frame'], clip['end_frame'] - 1)})
    evaluator = compile_evaluator(frozen)
    frames = []
    for frame in report_frames:
        state = evaluator.frame(frame)
        frames.append(dict(frame=frame, world_id=state['world_id'], shot_id=state['shot_id'],
            camera=state['camera'], object_count=len(state['objects']),
            potentially_visible_count=sum(obj['potentially_visible'] for obj in state['objects']),
            visibility_scope='bounds_only', pixels_observed=False))
    layout_report = dict(schema='video.scene-space-production-layout-report.v1', status='valid',
        plan_sha256=frozen['plan_sha256'], checks=dict(contract='passed', spatial='passed'),
        worlds=[inspect_world(world) for world in checked['worlds']],
        coverage=deepcopy(frozen['coverage']), frames=frames, issues=[],
        evidence_scope='declared_scene_geometry_only', visibility_scope='bounds_only',
        pixel_occlusion='not_assessed', visual_quality='not_assessed',
        continuous_motion='not_observed', audio='not_observed', recipes_executed=0)
    result = dict(schema=PRODUCTION_PREVIEW_SCHEMA, plan_sha256=frozen['plan_sha256'],
        static_frames=static_frames, clips=clips, layout_report=layout_report,
        selection_scope='declared_keyframe_rates_and_shot_boundaries_not_observed_visual_risk',
        rendered=False)
    if len(canonical_bytes(result)) > MAX_PREVIEW_BYTES:
        raise ServiceError('scene_space_preview_size_exceeded', 413)
    return result


def frame_layout_svg(plan, frame):
    """Project declared object bounds and anchors through the common camera.

    This intentionally does not paint recipes, apply masks or measure painted
    pixel visibility. The renderer adapter must verify those in the next stage.
    """
    frozen = freeze_plan(plan)
    state = compile_evaluator(frozen).frame(frame)
    return _frame_layout_svg(frozen['plan'], state)


def _frame_layout_svg(checked, state):
    frame = state['frame']
    width, height = (checked['viewport'][key] for key in ('width', 'height'))
    world = next(world for world in checked['worlds'] if world['id'] == state['world_id'])
    objects = {obj['id']: obj for obj in world['objects']}
    scale = min(1.0, 1280/width, 720/height)
    metadata = dict(schema='video.scene-space-frame-layout.v1', frame=frame,
        plan_sha256=state['plan_sha256'], world_id=state['world_id'],
        shot_id=state['shot_id'], interval_id=state['interval_id'],
        scope='declared_bounds_and_anchors_masks_not_applied', visibility_scope='bounds_only',
        recipes_executed=0, visual_quality='not_assessed',
        continuous_motion='not_observed', audio='not_observed')
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {} {}" '
             'width="{}" height="{}" role="img">'.format(
                 *(_number(value) for value in (width, height, width*scale, height*scale))),
        '<title>{}</title>'.format(html.escape('Layout frame '+str(frame))),
        '<metadata>{}</metadata>'.format(html.escape(canonical_bytes(metadata).decode('utf-8'))),
        '<defs><clipPath id="viewport"><rect width="{}" height="{}"/></clipPath></defs>'.format(
            _number(width), _number(height)),
        _rect(dict(x=0,y=0,width=width,height=height), 'fill="#f6f4ee"'),
        '<g clip-path="url(#viewport)" font-family="sans-serif">']
    layers = sorted(world['layers'], key=lambda layer: (layer['order'], layer['id']))
    colors = {layer['id']: PALETTE[index % len(PALETTE)] for index, layer in enumerate(layers)}
    for item in state['objects']:
        obj = objects[item['id']]
        color = colors[item['layer_id']]
        matrix = ' '.join(_number(value) for value in item['screen_matrix'])
        parts.append('<g data-object-id="{}" transform="matrix({})">'.format(
            html.escape(item['id'], quote=True), matrix))
        parts.append(_rect(obj['bounds'],
            'fill="{}" fill-opacity="0.16" stroke="{}" stroke-width="1.5"'.format(color,color)))
        parts.append('</g>')
        box = item['screen_bounds']
        parts.append(_text(box['x']+3, box['y']+14, item['id'], color=color))
        for name, point in sorted(item['screen_anchors'].items()):
            parts.append('<circle data-object-id="{}" data-anchor-id="{}" '
                'cx="{}" cy="{}" r="3" fill="{}"/>'.format(
                    html.escape(item['id'], quote=True), html.escape(name, quote=True),
                    _number(point[0]), _number(point[1]), color))
    parts.extend(['</g>', '</svg>'])
    result = '\n'.join(parts)+'\n'
    if len(result.encode('utf-8')) > MAX_PREVIEW_BYTES:
        raise ServiceError('scene_space_preview_size_exceeded', 413)
    return result


def write_plan_preview(plan, directory, *, max_frames=MAX_PREVIEW_FRAMES):
    """Export bounded frame layouts plus explicit evidence/coverage scope."""
    try:
        frozen = freeze_plan(plan)
        frames = _preview_frames(frozen, max_frames)
        evaluator = compile_evaluator(frozen)
        rendered, total_bytes = [], 0
        for frame in frames:
            raw = _frame_layout_svg(frozen['plan'],evaluator.frame(frame)).encode('utf-8')
            total_bytes += len(raw)
            if total_bytes > MAX_PREVIEW_TOTAL_BYTES:
                raise ServiceError('scene_space_preview_total_size_exceeded',413)
            rendered.append((frame,raw))
        report = dict(schema='video.scene-space-plan-preview.v1', status='valid',
            plan_sha256=frozen['plan_sha256'], checks=dict(contract='passed', spatial='passed'),
            coverage=frozen['coverage'], frames=frames, artifacts=[], issues=[])
    except ServiceError as error:
        frozen, frames = None, []
        report = dict(schema='video.scene-space-plan-preview.v1', status='invalid',
            plan_sha256=None, checks=dict(contract='failed',spatial='not_completed'),
            frames=[], artifacts=[], issues=[dict(code=error.code,
                details=deepcopy(getattr(error,'details',{})))])
    report.update(evidence_scope='scene_data_and_sampled_geometry_only',
        visibility_scope='bounds_only', visual_quality='not_assessed',
        continuous_motion='not_observed', audio='not_observed', recipes_executed=0)
    out = Path(directory)
    try:
        out.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise ServiceError('scene_space_preview_destination_exists',409) from None
    for frame, raw in rendered if frozen is not None else []:
        name = 'frame-{:05d}.svg'.format(frame)
        (out/name).write_bytes(raw)
        report['artifacts'].append(dict(frame=frame,path=name,sha256=hashlib.sha256(raw).hexdigest()))
    if frozen is not None:
        (out/'frozen-plan.json').write_bytes(canonical_bytes(frozen)+b'\n')
    (out/'plan-preview-report.json').write_bytes(canonical_bytes(report)+b'\n')
    return report


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('world', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--grid', action='store_true')
    parser.add_argument('--plan', action='store_true', help='Input is a scene plan rather than a world')
    args = parser.parse_args()
    document = json.loads(args.world.read_text(encoding='utf-8'))
    report = (write_plan_preview(document,args.output) if args.plan
              else write_world_preview(document,args.output,show_grid=args.grid))
    print(json.dumps(report, ensure_ascii=True))
