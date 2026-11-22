"""Bounded layout diagnostics for inert scene worlds, independent of painting.

The SVG is an inspection artifact. It never resolves a recipe reference, draws
ink, observes video/audio, or turns geometric checks into artistic acceptance.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import html
import json
from pathlib import Path

from agent_service.contracts import ServiceError, canonical_bytes
from agent_service.scene_space_contract import validate_world, world_geometry, world_sha256
from video_workbench.scene_space_plan import compile_evaluator, freeze_plan


REPORT_SCHEMA = 'video.scene-space-layout-report.v1'
MAX_PREVIEW_BYTES = 4 * 1024 * 1024
MAX_PREVIEW_FRAMES = 256
MAX_PREVIEW_TOTAL_BYTES = 32 * 1024 * 1024
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
