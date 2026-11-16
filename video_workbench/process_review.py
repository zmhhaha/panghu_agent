"""Check observed process changes without granting timing or quality approval.

Inputs are captured DOM geometry and frame identities from the isolated native
HyperFrames seek observer. A successful report is technical evidence only.
"""
from __future__ import annotations

import math
import re

from agent_service.contracts import ServiceError
from agent_service.profiles.video_process_contract import (
    ACTION_FIELDS, STATE_TRANSITIONS, validate_process_plan,
)
from .motion import FPS, HEIGHT, WIDTH, sha256


_SHA = re.compile(r'[0-9a-f]{64}\Z')
_EPSILON = 1e-6


def _require(value, code='process_observation_invalid'):
    if not value:
        raise ServiceError(code, 422)


def _number(value):
    _require(type(value) in (int, float) and -1000000 <= value <= 1000000
             and math.isfinite(value))
    return float(value)


def _finite_tree(value, depth=0):
    """Reject non-finite or pathological numbers before hashing or arithmetic."""
    _require(depth <= 40, 'process_observation_invalid')
    if type(value) in (int, float):
        _number(value)
    elif type(value) is dict:
        for child in value.values():
            _finite_tree(child, depth + 1)
    elif type(value) in (list, tuple):
        for child in value:
            _finite_tree(child, depth + 1)


def _digest(value):
    _require(type(value) is str and _SHA.fullmatch(value))


def _bounds(value, *, positive=True):
    _require(type(value) is dict, 'process_bounds_missing')
    x, y, width, height = (_number(value.get(key)) for key in ('x', 'y', 'width', 'height'))
    _require((width > 0 and height > 0) if positive else (width >= 0 and height >= 0),
             'process_bounds_invalid')
    _require(x >= -_EPSILON and y >= -_EPSILON
             and x + width <= WIDTH + _EPSILON and y + height <= HEIGHT + _EPSILON,
             'process_layout_outside_canvas')
    return x, y, width, height


def _scroll(value):
    _require(value.get('overflow') is False, 'process_text_overflow')
    for scroll, client in (('scrollWidth', 'clientWidth'), ('scrollHeight', 'clientHeight')):
        _require(0 <= _number(value.get(scroll)) <= _number(value.get(client)),
                 'process_text_overflow')


def _text_bounds(state, required):
    boxes, area = state.get('text_boxes'), state.get('text_area')
    _require(type(boxes) is dict and set(boxes) == set(required)
             and type(area) is dict and set(area) == {'left', 'top', 'right', 'bottom'},
             'process_text_bounds_missing')
    x, y, width, height = _bounds(state)
    left, top, right, bottom = (_number(area.get(key)) for key in ('left', 'top', 'right', 'bottom'))
    _require(x <= left < right <= x + width and y <= top < bottom <= y + height,
             'process_text_overflow')
    for box in boxes.values():
        bx, by, bw, bh = _bounds(box)
        _scroll(box)
        _require(bx >= left - _EPSILON and by >= top - _EPSILON
                 and bx + bw <= right + _EPSILON and by + bh <= bottom + _EPSILON,
                 'process_text_overflow')


def _ease(value):
    value = min(1.0, max(0.0, value))
    return 4 * value ** 3 if value < .5 else 1 - (-2 * value + 2) ** 3 / 2


def _progress(time, start, duration):
    return _ease((time - start) / duration)


def _path_position(points, progress):
    """Independently walk a compiler-owned polyline by its arc length."""
    _require(type(points) in (list, tuple) and len(points) >= 2,
             'process_path_invalid')
    vertices = []
    for point in points:
        _require(type(point) in (list, tuple) and len(point) == 2, 'process_path_invalid')
        vertices.append(tuple(_number(value) for value in point))
    lengths = [math.dist(a, b) for a, b in zip(vertices, vertices[1:])]
    _require(all(length > 0 for length in lengths), 'process_path_invalid')
    distance = sum(lengths) * min(1.0, max(0.0, progress))
    for left, right, length in zip(vertices, vertices[1:], lengths):
        if distance <= length:
            fraction = distance / length
            return tuple(a + (b - a) * fraction for a, b in zip(left, right))
        distance -= length
    return vertices[-1]


def _timeline(plan, timeline):
    from .motion_process import PROCESS_COMPILER_VERSIONS, process_paths

    _require(type(timeline) is dict and timeline.get('schema') == 'video.motion-timeline.v1'
             and type(timeline.get('compiler')) is str
             and timeline['compiler'] in PROCESS_COMPILER_VERSIONS,
             'process_timeline_invalid')
    _require(timeline.get('plan_sha256') == sha256(plan), 'process_plan_binding_mismatch')
    for key in ('semantic_sha256', 'alignment_sha256', 'audio_sha256', 'spoken_sha256'):
        _digest(timeline.get(key))
    duration = _number(timeline.get('duration_seconds'))
    audio = _number(timeline.get('audio_seconds'))
    leading = _number(timeline.get('leading_seconds'))
    trailing = _number(timeline.get('trailing_seconds'))
    _require(0 < duration <= 320 and .1 <= audio <= 300 and 0 <= leading <= 10 and 0 <= trailing <= 10
             and abs(duration - math.ceil((leading + audio + trailing) * FPS) / FPS) < _EPSILON,
             'process_timeline_invalid')
    _require(type(timeline.get('review_status')) is str
             and timeline['review_status'] in {'pending', 'verified'}
             and type(timeline.get('alignment_method')) is str
             and timeline['alignment_method'] in {'estimated', 'forced_alignment', 'manual_anchors'},
             'process_timeline_invalid')
    beats = timeline.get('beats')
    _require(type(beats) is list and 1 <= len(beats) <= 24, 'process_timeline_invalid')
    by_beat, previous_end = {}, leading
    for row in beats:
        _require(type(row) is dict and set(row) == {'beat_id', 'start', 'end'}
                 and type(row.get('beat_id')) is str and row['beat_id'] not in by_beat,
                 'process_timeline_invalid')
        start, end = _number(row.get('start')), _number(row.get('end'))
        _require(start >= previous_end - _EPSILON and end - start >= .12 - _EPSILON
                 and end <= leading + audio + _EPSILON,
                 'process_timeline_invalid')
        previous_end = end
        by_beat[row['beat_id']] = row
    validate_process_plan(plan, [dict(id=identity) for identity in by_beat])
    expected_paths = process_paths(plan, compiler_version=timeline['compiler'])
    _require(timeline.get('paths') == expected_paths, 'process_path_binding_mismatch')

    elements = {row['id']: row for row in plan['elements']}
    cards = {identity for identity, row in elements.items() if row['kind'] == 'card'}
    states = {identity: 'idle' for identity in cards}
    _require(timeline.get('initial_states') == states, 'process_state_binding_mismatch')
    first_visibility = {}
    for row in sorted(plan['actions'], key=lambda row: (by_beat[row['beat_id']]['start']
            + {'start': .03, 'middle': .38, 'end': .72}[row['placement']]
            * (by_beat[row['beat_id']]['end'] - by_beat[row['beat_id']]['start']))):
        if row['kind'] in {'reveal', 'hide'}:
            first_visibility.setdefault(row['target'], row['kind'])
    visibility = {identity: (0 if first_visibility.get(identity) == 'reveal' else 1)
                  if identity in first_visibility else (0 if row['kind'] == 'token' else 1)
                  for identity, row in elements.items()}
    _require(timeline.get('initial_visibility') == visibility, 'process_visibility_binding_mismatch')
    for opacity in timeline['initial_visibility'].values():
        _number(opacity)

    actions = timeline.get('actions')
    _require(type(actions) is list and len(actions) == len(plan['actions'])
             and all(type(row) is dict and type(row.get('ordinal')) is int for row in actions)
             and {row['ordinal'] for row in actions} == set(range(len(actions))),
             'process_action_binding_mismatch')
    _require(actions == sorted(actions, key=lambda row: (_number(row.get('start')), row['ordinal'])),
             'process_action_binding_mismatch')
    token_slot = next(row['slot'] for row in plan['elements'] if row['kind'] == 'token')
    ends, drawn, current_visibility = {}, {}, dict(visibility)
    for row in actions:
        original = plan['actions'][row['ordinal']]
        kind = original['kind']
        extra = {'ordinal', 'start', 'duration', 'until'}
        if kind == 'transfer':
            extra |= {'from_slot', 'to_slot', 'path_length'}
        elif kind == 'set_state':
            extra |= {'previous_state'}
        _require(set(row) == ACTION_FIELDS[kind] | extra
                 and all(row.get(key) == value for key, value in original.items()),
                 'process_action_binding_mismatch')
        beat = by_beat[row['beat_id']]
        span = beat['end'] - beat['start']
        start = _number(row.get('start'))
        seconds = _number(row.get('duration'))
        expected_start = beat['start'] + {'start': .03, 'middle': .38, 'end': .72}[row['placement']] * span
        pace = {'quick': .75, 'normal': 1.2, 'slow': 1.8} if kind == 'transfer' else {
            'quick': .25, 'normal': .45, 'slow': .7}
        expected_duration = min(pace[row['pace']], .24 * span)
        _require(abs(start - expected_start) <= 2e-6 and abs(seconds - expected_duration) <= 2e-6
                 and expected_duration >= 2 / FPS - 1e-12 and seconds >= 2 / FPS - _EPSILON
                 and start + seconds <= beat['end'] + 2e-6
                 and abs(_number(row.get('until')) - beat['end']) <= _EPSILON,
                 'process_action_timing_mismatch')
        _require(start + seconds <= duration - 1 / FPS + _EPSILON, 'process_final_hold_missing')
        prop = 'visibility' if kind in {'reveal', 'hide'} else kind
        key = row['target'], prop
        _require(start >= ends.get(key, -1) - _EPSILON, 'process_action_overlap')
        ends[key] = start + seconds
        if kind in {'reveal', 'hide'}:
            target_opacity = 1 if kind == 'reveal' else 0
            _require(current_visibility[row['target']] != target_opacity, 'process_visibility_noop')
            current_visibility[row['target']] = target_opacity
        elif kind == 'draw':
            _require(row['target'] not in drawn, 'process_path_binding_mismatch')
            drawn[row['target']] = start + seconds
        elif kind == 'transfer':
            path = expected_paths[row['relation_id']]
            points = path['points']
            length = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
            _require(row['from_slot'] == token_slot == 'token_' + path['source_slot']
                     and row['to_slot'] == 'token_' + path['target_slot']
                     and row['relation_id'] in drawn and drawn[row['relation_id']] <= start + _EPSILON
                     and abs(_number(row.get('path_length')) - length) <= 2e-6,
                     'process_transfer_binding_mismatch')
            token_slot = row['to_slot']
        elif kind == 'set_state':
            previous = states[row['target']]
            _require(row['previous_state'] == previous and row['state'] in STATE_TRANSITIONS[previous],
                     'process_state_binding_mismatch')
            states[row['target']] = row['state']
    for row in (action for action in actions if action['kind'] in {'transfer', 'set_state'}):
        identities = (row['target'],)
        if row['kind'] == 'transfer':
            relation = next(r for r in plan['relations'] if r['id'] == row['relation_id'])
            identities += (relation['source'], relation['target'])
        for identity in identities:
            visible = visibility[identity]
            for change in (a for a in actions if a['target'] == identity and a['kind'] in {'reveal', 'hide'}):
                if change['start'] + change['duration'] <= row['start'] + _EPSILON:
                    visible = 1 if change['kind'] == 'reveal' else 0
                elif change['start'] < row['start'] + row['duration'] - _EPSILON:
                    _require(False, 'process_transfer_invisible')
            _require(visible == 1, 'process_transfer_invisible')
    return elements


def _expected(plan, timeline, time):
    from .motion_process import process_layout

    geometry = process_layout(timeline['compiler'])

    states = {row['id']: dict(x=geometry[row['slot']][0], y=geometry[row['slot']][1],
                             opacity=timeline['initial_visibility'][row['id']],
                             state='idle' if row['kind'] == 'card' else None)
              for row in plan['elements']}
    connectors = {row['id']: dict(opacity=0, labelOpacity=0, markerOpacity=0, strokeDashoffset=1)
                  for row in plan['relations']}
    relations = {row['id']: row for row in plan['relations']}
    for row in timeline['actions']:
        if time < row['start']:
            continue
        progress = _progress(time, row['start'], row['duration'])
        if row['kind'] in {'reveal', 'hide'}:
            state = states[row['target']]
            target = 1 if row['kind'] == 'reveal' else 0
            state['opacity'] += (target - state['opacity']) * progress
        elif row['kind'] == 'transfer':
            center = _path_position(timeline['paths'][row['relation_id']]['points'], progress)
            _, _, width, height = geometry[row['from_slot']]
            states[row['target']].update(x=center[0] - width / 2, y=center[1] - height / 2)
        elif row['kind'] == 'set_state':
            states[row['target']]['state'] = row['state']
        elif row['kind'] == 'draw':
            marker = 0
            marker_start = row['start'] + .85 * row['duration']
            if time >= marker_start:
                marker = _progress(time, marker_start, .15 * row['duration'])
            connectors[row['target']] = dict(opacity=progress,
                labelOpacity=progress if relations[row['target']]['label'].strip() else 0,
                markerOpacity=marker, strokeDashoffset=1 - progress)
    return states, connectors


def _near(actual, expected, *, tolerance=.06, code='process_expected_state_missing'):
    _require(abs(_number(actual) - expected) <= tolerance, code)


def _overlap(left, right):
    return (left[0] < right[0] + right[2] and right[0] < left[0] + left[2]
            and left[1] < right[1] + right[3] and right[1] < left[1] + left[3])


def _inside(inner, outer):
    return (inner[0] >= outer[0] - _EPSILON and inner[1] >= outer[1] - _EPSILON
            and inner[0] + inner[2] <= outer[0] + outer[2] + _EPSILON
            and inner[1] + inner[3] <= outer[1] + outer[3] + _EPSILON)


def _v2_card(state, row, wanted):
    """Bind the real card's text and docking bay to their separate purposes."""
    from .motion_process import process_text_areas

    x, y, width, height = _bounds(state)
    areas = process_text_areas('motion-process-compiler-2')[row['slot']]
    for name, box in state['text_boxes'].items():
        area = areas['state' if name in {'state-label', 'state-symbol'} else name]
        _require(_inside(_bounds(box), area), 'process_text_region_mismatch')
    bay = state.get('docking_bay')
    actual = _bounds(bay)
    for value, expected in zip(actual, (x + 14, y + 204, 252, 56)):
        _near(value, expected, code='process_docking_bay_mismatch')
    _require(_inside(actual, (x, y, width, height)) and bay.get('owner') == row['id'],
             'process_docking_bay_mismatch')
    _near(bay.get('opacity'), wanted['opacity'], tolerance=.002, code='process_docking_bay_mismatch')


def _v2_token_clear(plan, timeline, actual, layout, labels):
    """Permit the docking channel, retain full-box clearance from all text.

    Check every swept path segment against the measured text, including hidden
    future text. Checking only three token centers could miss an obstruction.
    """
    token = next(row for row in plan['elements'] if row['kind'] == 'token')
    state = actual[token['id']]
    rectangle = _bounds(state)
    obstacles = [_bounds(box) for row in plan['elements'] if row['kind'] == 'card'
                 for box in actual[row['id']]['text_boxes'].values()]
    obstacles += [_bounds(labels[row['id']]) for row in plan['relations'] if row['label'].strip()]
    obstacles += [_bounds(layout[key]) for key in ('title', 'takeaway')]
    width, height = rectangle[2:]
    for path in timeline['paths'].values():
        for a, b in zip(path['points'], path['points'][1:]):
            swept = (min(a[0], b[0]) - width / 2 - 8, min(a[1], b[1]) - height / 2 - 8,
                     abs(b[0] - a[0]) + width + 16, abs(b[1] - a[1]) + height + 16)
            _require(not any(_overlap(swept, box) for box in obstacles), 'process_token_obstructed')
    # An exact stationary dock establishes ownership. During transfer the same
    # token is allowed to cross the reserved channel and its boundary ports.
    center = rectangle[0] + width / 2, rectangle[1] + height / 2
    for row in plan['elements']:
        if row['kind'] == 'card':
            bay = _bounds(actual[row['id']]['docking_bay'])
            dock = bay[0] + bay[2] / 2, bay[1] + bay[3] / 2
            if math.dist(center, dock) <= .06:
                _require(_inside(rectangle, bay), 'process_docking_bay_mismatch')


def _v2_relation(path, state, relation, elements, actual):
    """Observe actual SVG endpoints on the actual node borders."""
    _require(state.get('pathD') == path['path'], 'process_connection_geometry_mismatch')
    source, target = actual[relation['source']], actual[relation['target']]
    sx, sy, sw, sh = _bounds(source)
    tx, ty, tw, th = _bounds(target)
    is_return = elements[relation['source']]['slot'] == 'worker'
    ports = ((sx + sw / 2, sy + sh), (tx + tw / 2, ty + th)) if is_return else (
        (sx + sw, sy + 232), (tx, ty + 232))
    for name, key, expected in zip(('sourcePort', 'targetPort'), ('source_port', 'target_port'), ports):
        port = state.get(name)
        _require(type(port) is dict and set(port) == {'x', 'y'}, 'process_connection_port_mismatch')
        for axis, value, recorded in zip(('x', 'y'), expected, path[key]):
            _near(port.get(axis), value, code='process_connection_port_mismatch')
            _near(recorded, value, code='process_connection_port_mismatch')
    middle = state.get('middlePoint')
    _require(type(middle) is dict and set(middle) == {'x', 'y'}, 'process_connection_geometry_mismatch')
    for axis, value in zip(('x', 'y'), _path_position(path['relation_points'], .5)):
        _near(middle.get(axis), value, code='process_connection_geometry_mismatch')


def _sample(plan, timeline, sample, time):
    from .motion_process import process_layout, STATE_PRESENTATIONS

    geometry = process_layout(timeline['compiler'])
    v2 = timeline['compiler'] == 'motion-process-compiler-2'
    elements = {row['id']: row for row in plan['elements']}

    _require(type(sample) is dict, 'process_observation_sample_missing')
    _near(sample.get('time'), time, tolerance=1e-5, code='process_seek_mismatch')
    _near(sample.get('actual_time'), time, tolerance=1e-5, code='process_seek_mismatch')
    _digest(sample.get('frame_sha256'))
    actual, relations, layout = sample.get('elements'), sample.get('relations'), sample.get('layout')
    _require(type(actual) is dict and set(actual) == {row['id'] for row in plan['elements']}
             and type(relations) is dict and set(relations) == {row['id'] for row in plan['relations']},
             'process_observation_objects_missing')
    _require(type(layout) is dict and layout.get('fontReady') is True, 'process_font_not_ready')
    for key in ('title', 'takeaway'):
        _bounds(layout.get(key))
        _scroll(layout[key])
    expected, connectors = _expected(plan, timeline, time)
    for row in plan['elements']:
        identity, kind = row['id'], row['kind']
        state = actual[identity]
        _require(type(state) is dict, 'process_observation_objects_missing')
        rectangle = _bounds(state)
        _scroll(state)
        wanted = expected[identity]
        for key in ('x', 'y', 'opacity'):
            _near(state.get(key), wanted[key])
        for key, size in zip(('width', 'height'), geometry[row['slot']][2:]):
            _near(state.get(key), size)
        text = {'element-label'} | ({'element-detail'} if row['detail'] else set())
        if kind == 'card':
            visual = STATE_PRESENTATIONS[wanted['state']]
            _require(state.get('state') == wanted['state']
                     and state.get('stateLabel') == visual['label']
                     and state.get('stateSymbol') == visual['symbol']
                     and state.get('statePath') == visual['path'], 'process_state_presentation_mismatch')
            layers = state.get('stateOpacities')
            _require(type(layers) is dict and set(layers) == set(STATE_PRESENTATIONS),
                     'process_state_presentation_mismatch')
            for name, opacity in layers.items():
                _near(opacity, 1 if name == wanted['state'] else 0, tolerance=.002,
                      code='process_state_presentation_mismatch')
            text |= {'state-label', 'state-symbol'}
        else:
            _require(all(state.get(key) is None for key in ('state', 'stateLabel', 'stateSymbol', 'statePath')),
                     'process_state_presentation_mismatch')
            _require(state.get('stateOpacities') == {}, 'process_state_presentation_mismatch')
            # Validate the entire measured token, even when it is initially hidden.
            if v2:
                _require('docking_bay' in state and state['docking_bay'] is None,
                         'process_docking_bay_mismatch')
            else:
                obstacles = [geometry[e['slot']] for e in plan['elements'] if e['kind'] == 'card']
                obstacles += [timeline['paths'][r['id']]['label_box'] for r in plan['relations'] if r['label'].strip()]
                _require(not any(_overlap(rectangle, box) for box in obstacles), 'process_token_obstructed')
        _text_bounds(state, text)
        if v2 and kind == 'card':
            _v2_card(state, row, wanted)
    label_rows = layout.get('relations')
    _require(type(label_rows) is list and len(label_rows) == len(plan['relations'])
             and all(type(row) is dict and type(row.get('id')) is str for row in label_rows)
             and {row['id'] for row in label_rows} == set(relations), 'process_relation_bounds_missing')
    labels = {row['id']: row for row in label_rows}
    for row in plan['relations']:
        identity = row['id']
        state, wanted, path, label = relations[identity], connectors[identity], timeline['paths'][identity], labels[identity]
        _require(type(state) is dict and type(state.get('baseCount')) is int and state['baseCount'] == 0,
                 'process_connection_visible_early')
        for key, value in wanted.items():
            _near(state.get(key), value, tolerance=.002, code='process_connection_schedule_mismatch')
        _near(state.get('length'), path['relation_length'] if v2 else path['length'],
              tolerance=.1, code='process_connection_geometry_mismatch')
        if v2:
            _v2_relation(path, state, row, elements, actual)
        _require(label.get('text') == row['label'] and label.get('overflow') is False,
                 'process_relation_text_overflow')
        _near(label.get('opacity'), wanted['labelOpacity'], tolerance=.002,
              code='process_connection_schedule_mismatch')
        plate = _bounds(label)
        for value, size in zip(plate, path['label_box']):
            _near(value, size, code='process_connection_geometry_mismatch')
        ink = _bounds(label.get('ink'), positive=False)
        _require(ink[0] >= plate[0] - _EPSILON and ink[1] >= plate[1] - _EPSILON
                 and ink[0] + ink[2] <= plate[0] + plate[2] + _EPSILON
                 and ink[1] + ink[3] <= plate[1] + plate[3] + _EPSILON,
                 'process_relation_text_overflow')
    if v2:
        _v2_token_clear(plan, timeline, actual, layout, labels)


def verify_process_observations(plan, timeline, observations):
    """Check native seek evidence for the local process compiler only.

    DOM samples cannot prove their own provenance. The caller must retain the
    observer, project, image and screenshot artifacts beside this report.
    This does not authorize production rendering or human quality approval.
    """
    for value in (plan, timeline, observations):
        _finite_tree(value)
    _require(type(plan) is dict and type(observations) is dict)
    _timeline(plan, timeline)
    _require(observations.get('schema') == 'video.motion-observations.v1'
             and observations.get('timeline_sha256') == sha256(timeline), 'process_timeline_binding_mismatch')
    _require(observations.get('seek_protocol') == 'hyperframes-core-renderSeek-exact'
             and type(observations.get('render_fps')) is int and observations['render_fps'] == FPS,
             'process_seek_protocol_invalid')
    windows = observations.get('windows')
    _require(type(windows) is list and len(windows) == len(timeline['actions'])
             and all(type(row) is dict and type(row.get('ordinal')) is int for row in windows)
             and {row['ordinal'] for row in windows} == {row['ordinal'] for row in timeline['actions']},
             'process_observation_windows_missing')
    by_ordinal = {row['ordinal']: row for row in windows}
    for action in timeline['actions']:
        window = by_ordinal[action['ordinal']]
        _require(set(window) == {'ordinal', 'before', 'middle', 'after'}, 'process_observation_windows_missing')
        times = dict(before=max(0, action['start'] - 1 / FPS),
                     middle=action['start'] + action['duration'] / 2,
                     after=min(timeline['duration_seconds'] - 1 / FPS,
                               action['start'] + action['duration'] + 1 / FPS))
        for key, time in times.items():
            _sample(plan, timeline, window[key], time)
        _require(window['before']['frame_sha256'] != window['after']['frame_sha256'],
                 'process_changed_frame_missing')
        if action['kind'] in {'reveal', 'hide'}:
            before = window['before']['elements'][action['target']]
            after = window['after']['elements'][action['target']]
            _require(abs(before['opacity'] - after['opacity']) > .99, 'process_visibility_change_missing')
        elif action['kind'] == 'transfer':
            before = window['before']['elements'][action['target']]
            after = window['after']['elements'][action['target']]
            _require(math.hypot(after['x'] - before['x'], after['y'] - before['y']) > 1,
                     'process_transfer_change_missing')
        if action['kind'] == 'set_state':
            _require(window['before']['elements'][action['target']]['opacity'] > .99
                     and window['after']['elements'][action['target']]['opacity'] > .99,
                     'process_state_change_invisible')
    initial, repeated = observations.get('initial'), observations.get('repeated_initial')
    _sample(plan, timeline, initial, 0)
    _sample(plan, timeline, repeated, 0)
    reverse = observations.get('reverse_seek')
    _require(type(reverse) is dict and reverse.get('repeat_state_equal') is True
             and reverse.get('first_sha256') == initial['frame_sha256']
             and reverse.get('repeated_first_sha256') == repeated['frame_sha256']
             and initial['frame_sha256'] == repeated['frame_sha256'], 'process_reverse_seek_mismatch')
    _digest(reverse.get('last_sha256'))
    _require(all(initial.get(key) == repeated.get(key) for key in ('elements', 'relations', 'layout')),
             'process_reverse_seek_mismatch')
    checks = ['native_exact_seek', 'complete_token_bounds', 'path_arc_length_progress',
              'state_text_and_symbol', 'no_early_connections', 'font_and_text_bounds',
              'reverse_seek', 'bound_plan_and_timeline']
    if timeline['compiler'] == 'motion-process-compiler-2':
        checks += ['actual_svg_node_border_ports', 'distinct_relation_and_transfer_paths',
                   'docking_bay_ownership', 'swept_text_clearance_8px']
    return dict(schema='video.process-review.v1', technical_status='passed',
                plan_sha256=sha256(plan), timeline_sha256=sha256(timeline),
                observation_sha256=sha256(observations), checked_action_windows=len(windows),
                timing_review_status=timeline['review_status'], visual_comprehension='pending_user_review',
                voice_naturalness='pending_user_review',
                checks=checks)
