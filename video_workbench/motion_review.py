"""Verify observed semantic actions, not merely that an MP4 is nonempty.

Observations come from the pinned isolated browser after real HyperFrames seeks.
This module does not execute a browser, trust a model's quality assertion, or
mark timing or human satisfaction as verified. Planned holds remain valid.
"""
from __future__ import annotations

import math
import re

from agent_service.contracts import ServiceError
from .motion import FPS, HEIGHT, WIDTH, LAYOUTS, action_windows, sha256

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


def _layout(observation):
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
    for state in elements.values():
        _require(type(state) is dict and state.get('overflow') is False, 'motion_text_overflow')
        x, y, width, height = (_number(state.get(k)) for k in ('x', 'y', 'width', 'height'))
        opacity = _number(state.get('opacity'))
        _require(-.01 <= opacity <= 1.01)
        _require(x >= -1 and y >= -1 and width > 0 and height > 0
                 and x + width <= WIDTH + 1 and y + height <= HEIGHT + 1,
                 'motion_layout_outside_canvas')
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
    element_ids = {row['id'] for row in plan['elements']}
    relation_ids = {row['id'] for row in plan['relations']}
    positions = {row['id']: LAYOUTS[plan['template']][row['slot']][:2] for row in plan['elements']}
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
            _layout(sample)
            _require(set(sample['elements']) == element_ids
                     and type(sample.get('relations')) is dict and set(sample['relations']) == relation_ids,
                     'motion_observation_objects_missing')
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
                x, y = LAYOUTS[plan['template']][action['to_slot']][:2]
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
