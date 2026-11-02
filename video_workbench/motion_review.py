"""Verify observed semantic actions, not merely that an MP4 is nonempty.

Observations come from the pinned isolated browser after real HyperFrames seeks.
This module does not execute a browser, trust a model's quality assertion, or
mark timing or human satisfaction as verified. Planned holds remain valid.
"""
from __future__ import annotations

import math
import re

from agent_service.contracts import ServiceError
from agent_service.profiles.video_motion_contract import validate_motion_plan
from .motion import COMPILER_TEMPLATES, FPS, HEIGHT, WIDTH, LAYOUTS, action_windows, sha256
from .motion_demo import DEMO_COMPILER_VERSION, DEMO_LAYOUT

_SHA = re.compile(r'[0-9a-f]{64}\Z')


def _require(value, code='motion_observation_invalid'):
    if not value:
        raise ServiceError(code, 422)


def _number(value):
    _require(type(value) in (int, float) and -1000000 <= value <= 1000000 and math.isfinite(value))
    return float(value)


def _digest(value):
    _require(type(value) is str and _SHA.fullmatch(value))


def _visible(state):
    return _number(state.get('opacity')) >= .98


def _demo_expected_states(plan, timeline, time):
    """Evaluate the pinned demo compiler's GSAP 3.14.2 property schedule.

    ``power2.inOut`` is cubic in GSAP (Power1 is quadratic). fromTo tweens
    use explicit previous endpoints and immediateRender=False; future tweens
    must not move or reveal a declared copy ahead of their start. The compiler
    owns the ease and all coordinates, and same-property windows cannot overlap.
    """
    states = {row['id']: dict(x=DEMO_LAYOUT[row['slot']][0], y=DEMO_LAYOUT[row['slot']][1], opacity=1.0)
              for row in plan['elements']}
    first_visibility = set()
    for action in timeline['actions']:
        identity = action['target']
        if action['kind'] in {'reveal', 'hide'} and identity not in first_visibility:
            first_visibility.add(identity)
            states[identity]['opacity'] = 0.0 if action['kind'] == 'reveal' else 1.0
    for action in timeline['actions']:
        start, duration = _number(action.get('start')), _number(action.get('duration'))
        _require(start >= 0 and duration > 0, 'motion_demo_contract_mismatch')
        if time < start:
            continue
        progress = min(1.0, (time - start) / duration)
        eased = 4 * progress ** 3 if progress < .5 else 1 - (-2 * progress + 2) ** 3 / 2
        state = states[action['target']]
        if action['kind'] == 'move':
            x, y = DEMO_LAYOUT[action['to_slot']][:2]
            state['x'] += (x - state['x']) * eased
            state['y'] += (y - state['y']) * eased
        else:
            opacity = 1.0 if action['kind'] == 'reveal' else 0.0
            state['opacity'] += (opacity - state['opacity']) * eased
    return states


def _text_bounds(state, kind):
    """Require visible text boxes even when parent scroll metrics look safe."""
    boxes, area = state.get('text_boxes'), state.get('text_area')
    required = {'copy', 'label', 'detail'} if kind == 'card' else {'copy', 'label'}
    _require(type(boxes) is dict and set(boxes) == required and type(area) is dict
             and set(area) == {'left', 'top', 'right', 'bottom'}, 'motion_text_bounds_missing')
    left, top, right, bottom = (_number(area.get(key)) for key in ('left', 'top', 'right', 'bottom'))
    _require(state['x'] <= left < right <= state['x'] + state['width']
             and state['y'] <= top < bottom <= state['y'] + state['height'], 'motion_text_overflow')
    for box in boxes.values():
        _require(type(box) is dict and box.get('overflow') is False, 'motion_text_overflow')
        x, y, width, height = (_number(box.get(key)) for key in ('x', 'y', 'width', 'height'))
        # The tiny epsilon covers floating-point subtraction of DOMRect edges;
        # the original integer parent scroll checks remain exact and unchanged.
        _require(width >= 0 and height >= 0 and x >= left - 1e-6 and y >= top - 1e-6
                 and x + width <= right + 1e-6 and y + height <= bottom + 1e-6,
                 'motion_text_overflow')
        for scroll, client in (('scrollWidth', 'clientWidth'), ('scrollHeight', 'clientHeight')):
            _require(0 <= _number(box.get(scroll)) <= _number(box.get(client)), 'motion_text_overflow')


def _layout(observation, *, text_kinds=None):
    layout = observation.get('layout')
    _require(type(layout) is dict and layout.get('fontReady') is True,
             'motion_fonts_not_ready')
    for key in ('title', 'takeaway'):
        item = layout.get(key)
        _require(type(item) is dict and item.get('overflow') is False,
                 'motion_text_overflow')
        x, y, width, height = (_number(item.get(k)) for k in ('x', 'y', 'width', 'height'))
        _require(x >= -1 and y >= -1 and width > 0 and height > 0
                 and x + width <= WIDTH + 1 and y + height <= HEIGHT + 1,
                 'motion_layout_outside_canvas')
    labels = layout.get('relations')
    _require(type(labels) is list and all(type(row) is dict and row.get('overflow') is False
                                        for row in labels), 'motion_text_overflow')
    for item in labels:
        x, y, width, height = (_number(item.get(k)) for k in ('x', 'y', 'width', 'height'))
        _require(x >= -1 and y >= -1 and width >= 0 and height >= 0
                 and x + width <= WIDTH + 1 and y + height <= HEIGHT + 1,
                 'motion_layout_outside_canvas')
    elements = observation.get('elements')
    _require(type(elements) is dict)
    for identity, state in elements.items():
        _require(type(state) is dict and state.get('overflow') is False, 'motion_text_overflow')
        x, y, width, height = (_number(state.get(k)) for k in ('x', 'y', 'width', 'height'))
        opacity = _number(state.get('opacity'))
        _require(-.01 <= opacity <= 1.01)
        _require(x >= -1 and y >= -1 and width > 0 and height > 0
                 and x + width <= WIDTH + 1 and y + height <= HEIGHT + 1,
                 'motion_layout_outside_canvas')
        if text_kinds is not None and identity in text_kinds:
            _text_bounds(state, text_kinds[identity])
    return elements


def verify_observations(plan, timeline, observations):
    """Reject missing/bogus seeks, overflow, no-op moves and frozen timelines.

    This checks declared action windows; it deliberately makes no global
    requirement for every adjacent frame to differ. Returned quality statuses
    preserve the need for timing, visual comprehension and voice review.
    """
    _require(type(observations) is dict
             and observations.get('schema') == 'video.motion-observations.v1'
             and observations.get('timeline_sha256') == sha256(timeline)
             and timeline.get('plan_sha256') == sha256(plan))
    _require(observations.get('seek_protocol') == 'hyperframes-core-renderSeek-exact'
             and type(observations.get('render_fps')) is int and observations['render_fps'] == FPS,
             'motion_seek_protocol_invalid')
    demonstration = plan.get('template') == 'demonstration'
    if demonstration:
        _require(timeline.get('compiler') == DEMO_COMPILER_VERSION,
                 'motion_demo_contract_mismatch')
        beat_rows = timeline.get('beats')
        _require(type(beat_rows) is list and all(type(row) is dict and 'beat_id' in row for row in beat_rows),
                 'motion_demo_contract_mismatch')
        validate_motion_plan(plan, [dict(id=row['beat_id']) for row in beat_rows],
                             allowed_templates={'demonstration'})
        # A timeline cannot smuggle a different verb/destination into a valid
        # plan. Ordinals retain the plan's original order after time sorting.
        actions = timeline.get('actions')
        _require(type(actions) is list and len(actions) == len(plan['actions'])
                 and all(type(row) is dict and type(row.get('ordinal')) is int for row in actions)
                 and {row['ordinal'] for row in actions} == set(range(len(actions))),
                 'motion_demo_contract_mismatch')
        for row in actions:
            _require(type(row) is dict and type(row.get('ordinal')) is int
                     and all(row.get(key) == value for key, value in plan['actions'][row['ordinal']].items()),
                     'motion_demo_contract_mismatch')
        layout = DEMO_LAYOUT
    else:
        _require(plan.get('template') in LAYOUTS)
        _require(type(timeline.get('compiler')) is str and timeline['compiler'] in COMPILER_TEMPLATES,
                 'motion_compiler_version_unsupported')
        layout = LAYOUTS[plan['template']]
    text_kinds = ({row['id']: row['kind'] for row in plan['elements']}
                  if timeline.get('compiler') in {'motion-compiler-2', 'motion-compiler-3'} else None)
    element_ids = {row['id'] for row in plan['elements']}
    relation_ids = {row['id'] for row in plan['relations']}
    positions = {row['id']: layout[row['slot']][:2] for row in plan['elements']}
    dimensions = {row['id']: layout[row['slot']][2:] for row in plan['elements']}
    fixed_ids = {row['id'] for row in plan['elements'] if row['kind'] in {'terminal', 'label'}}
    rows = observations.get('windows')
    _require(type(rows) is list and len(rows) == len(timeline['actions']))
    by_ordinal = {}
    for row in rows:
        _require(type(row) is dict and type(row.get('ordinal')) is int
                 and row['ordinal'] not in by_ordinal)
        by_ordinal[row['ordinal']] = row
    checks = []
    for action, times in zip(timeline['actions'], action_windows(timeline)):
        row = by_ordinal.get(action['ordinal'])
        _require(row is not None)
        for label in ('before', 'middle', 'after'):
            sample = row.get(label)
            _require(type(sample) is dict
                     and abs(_number(sample.get('time')) - times[label]) < .00001
                     and abs(_number(sample.get('actual_time')) - times[label]) < .00001,
                     'motion_seek_time_mismatch')
            _digest(sample.get('frame_sha256'))
            _layout(sample, text_kinds=text_kinds)
            _require(set(sample['elements']) == element_ids
                     and type(sample.get('relations')) is dict and set(sample['relations']) == relation_ids,
                     'motion_observation_objects_missing')
            if demonstration:
                # Moving document/record/attempt windows keep their declared
                # size; this applies to every element, including hidden copies.
                expected_states = _demo_expected_states(plan, timeline, times[label])
                for identity, state in sample['elements'].items():
                    width, height = dimensions[identity]
                    _require(abs(_number(state.get('width')) - width) < 1
                             and abs(_number(state.get('height')) - height) < 1,
                             'motion_demo_size_mismatch')
                    if identity in fixed_ids:
                        _require(math.dist((_number(state['x']), _number(state['y'])), positions[identity]) < 1,
                                 'motion_demo_fixed_window_moved')
                    expected = expected_states[identity]
                    _require(math.dist((_number(state['x']), _number(state['y'])),
                                       (expected['x'], expected['y'])) < 1,
                             'motion_demo_position_mismatch')
                    _require(abs(_number(state['opacity']) - expected['opacity']) <= .01,
                             'motion_demo_opacity_mismatch')
            labels = sample['layout']['relations']
            _require({item.get('id') for item in labels} == relation_ids
                     and len(labels) == len(relation_ids), 'motion_observation_objects_missing')
        before, middle, after = (row[key] for key in ('before', 'middle', 'after'))
        target, kind = action['target'], action['kind']
        if kind == 'draw':
            states = []
            for sample in (before, middle, after):
                relations = sample.get('relations')
                _require(type(relations) is dict and target in relations)
                state = relations[target]
                _require(type(state) is dict and _number(state.get('length')) > 0
                         and _visible(state))
                states.append(_number(state.get('strokeDashoffset')))
            _require(states[0] >= .98 and states[2] <= .02
                     and states[2] < states[1] < states[0], 'motion_expected_draw_missing')
        else:
            _require(all(target in sample['elements'] for sample in (before, middle, after)))
            a, b, c = (sample['elements'][target] for sample in (before, middle, after))
            if kind in {'reveal', 'hide'}:
                values = [_number(state.get('opacity')) for state in (a, b, c)]
                if kind == 'reveal':
                    _require(values[0] <= .02 and values[2] >= .98
                             and values[0] < values[1] < values[2], 'motion_expected_reveal_missing')
                else:
                    _require(values[0] >= .98 and values[2] <= .02
                             and values[2] < values[1] < values[0], 'motion_expected_hide_missing')
            elif kind == 'move':
                _require(_visible(a) and _visible(b) and _visible(c), 'motion_invisible_move')
                x, y = layout[action['to_slot']][:2]
                if demonstration:
                    _require(layout[action['to_slot']][2:] == dimensions[target],
                             'motion_demo_size_mismatch')
                start = (_number(a['x']), _number(a['y']))
                mid = (_number(b['x']), _number(b['y']))
                end = (_number(c['x']), _number(c['y']))
                total = math.dist(start, end)
                halfway = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
                _require(total > 2 and math.dist(start, positions[target]) < 1
                         and math.dist(end, (x, y)) < 1 and math.dist(mid, halfway) < 1,
                         'motion_expected_move_missing')
                positions[target] = (x, y)
            elif kind == 'highlight':
                _require(_visible(a) and _visible(b) and _visible(c), 'motion_invisible_highlight')
                keys = ('borderColor', 'backgroundColor')
                colors = [tuple(state.get(k) for k in keys) for state in (a, b, c)]
                _require(all(type(v) is str for group in colors for v in group))
                _require(colors[0] != colors[2] and colors[1] != colors[0]
                         and colors[2] == ('rgb(121, 228, 213)', 'rgb(33, 67, 78)'),
                         'motion_expected_highlight_missing')
            else:
                raise ServiceError('motion_observation_invalid', 422)
        _require(len({sample['frame_sha256'] for sample in (before, middle, after)}) >= 2,
                 'motion_frozen_frames')
        checks.append(dict(ordinal=action['ordinal'], target=target, kind=kind, passed=True))
    reverse = observations.get('reverse_seek')
    _require(type(reverse) is dict and reverse.get('repeat_state_equal') is True,
             'motion_reverse_seek_failed')
    for key in ('first_sha256', 'last_sha256', 'repeated_first_sha256'):
        _digest(reverse.get(key))
    _require(reverse['first_sha256'] == reverse['repeated_first_sha256']
             and reverse['first_sha256'] != reverse['last_sha256'], 'motion_reverse_seek_failed')
    return dict(schema='video.motion-review.v1', technical_status='passed', checks=checks,
                seek_protocol=observations['seek_protocol'], render_fps=observations['render_fps'],
                timeline_sha256=sha256(timeline), timing_review_status=timeline['review_status'],
                visual_comprehension='pending_user_review', voice_naturalness='pending_user_review')
