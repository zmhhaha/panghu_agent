"""Technical review of saved developer Canvas/WebGL seek observations.

State samples alone cannot prove their provenance. The caller must bind the
observer, input files, runtime and real screenshot bytes separately. This
checker establishes recorded technical properties, not visual quality or
production admission.
"""
from __future__ import annotations

import math
import re

from agent_service.contracts import ServiceError
from .motion import FPS, sha256


_SHA = re.compile(r'[0-9a-f]{64}\Z')
_COMPILERS = {'canvas': 'animation-lab-canvas-compiler-1', 'three': 'animation-lab-three-compiler-1'}
_EPS = 1 / FPS + 1e-6


def _require(value, code='animation_lab_engine_observations_invalid'):
    if not value:
        raise ServiceError(code, 422)


def _number(value, low=-1e9, high=1e9):
    _require(type(value) in (int, float) and math.isfinite(value) and low <= value <= high,
             'animation_lab_engine_nonfinite_or_out_of_range')
    return float(value)


def _digest(value):
    _require(type(value) is str and _SHA.fullmatch(value), 'animation_lab_engine_frame_digest_invalid')


def _near(value, expected, tolerance=_EPS):
    _require(abs(_number(value) - expected) <= tolerance, 'animation_lab_engine_seek_mismatch')


def _finite_tree(value, depth=0):
    _require(depth <= 20, 'animation_lab_engine_state_too_deep')
    if type(value) is float:
        _require(math.isfinite(value), 'animation_lab_engine_nonfinite_or_out_of_range')
    elif type(value) is dict:
        _require(len(value) <= 1024 and all(type(key) is str for key in value), 'animation_lab_engine_state_invalid')
        for item in value.values():
            _finite_tree(item, depth+1)
    elif type(value) is list:
        _require(len(value) <= 4096, 'animation_lab_engine_state_invalid')
        for item in value:
            _finite_tree(item, depth+1)
    else:
        _require(value is None or type(value) in (str, int, bool), 'animation_lab_engine_state_invalid')


def _vector(value, size):
    _require(type(value) is list and len(value) == size, 'animation_lab_engine_vector_invalid')
    for number in value:
        _number(number)


def _bounds(value):
    _require(type(value) is dict and set(value) == {'x', 'y', 'width', 'height'},
             'animation_lab_engine_bounds_invalid')
    x, y = _number(value['x'], -.5, 1280.5), _number(value['y'], -.5, 720.5)
    width, height = _number(value['width'], .001, 1280.5), _number(value['height'], .001, 720.5)
    _require(x+width <= 1280.5 and y+height <= 720.5, 'animation_lab_engine_bounds_invalid')


def _rgba(value):
    _require(type(value) is list and len(value) == 4
             and all(type(channel) is int and 0 <= channel <= 255 for channel in value),
             'animation_lab_engine_bitmap_invalid')


def _bitmap(state):
    samples = state.get('bitmap_samples')
    _require(type(samples) is list and 3 <= len(samples) <= 64, 'animation_lab_engine_bitmap_missing')
    locations, colors = set(), set()
    for row in samples:
        _require(type(row) is dict and set(row) == {'x', 'y', 'rgba'}, 'animation_lab_engine_bitmap_invalid')
        x, y = _number(row['x'], 0, 1279), _number(row['y'], 0, 719)
        _require((x, y) not in locations, 'animation_lab_engine_bitmap_invalid')
        locations.add((x, y))
        _rgba(row['rgba'])
        colors.add(tuple(row['rgba']))
    _require(len(colors) >= 2 and any(any(color[:3]) and color[3] > 0 for color in colors),
             'animation_lab_engine_bitmap_blank')


def _canvas_state(plan, state):
    _require(state.get('context') == '2d' and state.get('width') == 1280 and state.get('height') == 720
             and state.get('deterministic') is True and state.get('seed') == plan.get('seed'),
             'animation_lab_engine_context_invalid')
    objects = state['objects']
    count = plan.get('packet_count')
    _require(type(count) is int and 3 <= count <= 12 and len(objects) == count
             and {row['id'] for row in objects} == {f'packet-{index+1}' for index in range(count)},
             'animation_lab_engine_objects_invalid')
    for row in objects:
        x, y, radius = _number(row.get('x'), 0, 1280), _number(row.get('y'), 0, 720), _number(row.get('radius'), 1, 60)
        _require(row.get('status') in {'incoming', 'queued', 'transferring', 'processing', 'done'},
                 'animation_lab_engine_objects_invalid')
        _rgba(row.get('center_pixel'))
        for key, wanted in [('x', x-radius), ('y', y-radius), ('width', radius*2), ('height', radius*2)]:
            _near(row['bounds'][key], wanted, 1e-5)
    _require(state.get('queue_count') == sum(row['status'] == 'queued' for row in objects)
             and state.get('completed_count') == sum(row['status'] == 'done' for row in objects),
             'animation_lab_engine_count_mismatch')


def _three_state(plan, state):
    _require(state.get('three_revision') == '170', 'animation_lab_engine_three_revision_mismatch')
    webgl = state.get('webgl')
    _require(type(webgl) is dict and type(webgl.get('version')) is str and 'WebGL 2' in webgl['version']
             and webgl.get('context_lost') is False and webgl.get('width') == 1280 and webgl.get('height') == 720,
             'animation_lab_engine_context_invalid')
    for name in ('draw_calls', 'triangles'):
        _require(type(webgl.get(name)) is int and webgl[name] > 0, 'animation_lab_engine_webgl_draw_missing')
    camera = state.get('camera')
    _require(type(camera) is dict, 'animation_lab_engine_camera_invalid')
    for key, size in [('position', 3), ('target', 3), ('matrix_world', 16), ('projection_matrix', 16)]:
        _vector(camera.get(key), size)
    layers = plan.get('layers')
    _require(type(layers) is list and 3 <= len(layers) <= 5
             and all(type(row) is dict and type(row.get('id')) is str and row['id'] for row in layers)
             and len({row['id'] for row in layers}) == len(layers)
             and {row['id'] for row in state['objects']} == {row['id'] for row in layers},
             'animation_lab_engine_objects_invalid')
    for row in state['objects']:
        _require(row.get('visible') is True, 'animation_lab_engine_object_hidden')
        for key, size in [('position', 3), ('rotation', 3), ('scale', 3), ('matrix_world', 16)]:
            _vector(row.get(key), size)
        _require(all(value > 0 for value in row['scale']), 'animation_lab_engine_vector_invalid')


def _sample(plan, timeline, sample, expected):
    _require(type(sample) is dict, 'animation_lab_engine_sample_missing')
    _near(sample.get('time'), expected, 1e-6)
    _near(sample.get('actual_time'), expected)
    _digest(sample.get('frame_sha256'))
    state = sample.get('engine_state')
    _require(type(state) is dict and state.get('engine') == timeline['engine'],
             'animation_lab_engine_context_invalid')
    _near(state.get('time'), expected)
    objects = state.get('objects')
    _require(type(objects) is list and 1 <= len(objects) <= 64
             and all(type(row) is dict and type(row.get('id')) is str and row['id'] for row in objects)
             and len({row['id'] for row in objects}) == len(objects), 'animation_lab_engine_objects_invalid')
    for row in objects:
        _number(row.get('opacity'), 0, 1)
        _bounds(row.get('bounds'))
    _bitmap(state)
    if timeline['engine'] == 'canvas':
        _canvas_state(plan, state)
    else:
        _three_state(plan, state)
    return state


def _scene_change(before, after):
    # Ignore frame-local draw telemetry and pixels when checking whether the
    # recorded geometry itself changed: a changing screenshot alone is weaker.
    keys = ('id', 'x', 'y', 'bounds', 'opacity', 'position', 'rotation', 'scale', 'matrix_world')
    geometry = lambda state: [{key: row[key] for key in keys if key in row} for row in state['objects']]
    return (geometry(before), before.get('camera')) != (geometry(after), after.get('camera'))


def _three_target_change(target, before, after):
    if target == 'camera':
        keys = ('position', 'target', 'matrix_world', 'projection_matrix')
        return any(before['camera'][key] != after['camera'][key] for key in keys)
    if target == 'layers':
        # Projected bounds also change when only the camera moves. A declared
        # layer explosion must instead change an actual mesh world transform.
        worlds = lambda state: {row['id']: row['matrix_world'] for row in state['objects']}
        return worlds(before) != worlds(after)
    return False


def verify_engine_observations(plan, timeline, observations):
    for value in (plan, timeline, observations):
        _finite_tree(value)
    _require(type(plan) is dict and type(timeline) is dict and type(observations) is dict)
    engine = timeline.get('engine')
    _require(engine in _COMPILERS and timeline.get('compiler') == _COMPILERS[engine]
             and timeline.get('schema') == 'video.motion-timeline.v1'
             and timeline.get('fps') == FPS and timeline.get('width') == 1280 and timeline.get('height') == 720,
             'animation_lab_engine_timeline_invalid')
    _require(timeline.get('plan_sha256') == sha256(plan), 'animation_lab_engine_plan_binding_mismatch')
    duration = _number(timeline.get('duration_seconds'), 3, 60)
    actions = timeline.get('actions')
    _require(type(actions) is list and 1 <= len(actions) <= 64
             and all(type(row) is dict and type(row.get('ordinal')) is int for row in actions)
             and {row['ordinal'] for row in actions} == set(range(len(actions))),
             'animation_lab_engine_actions_invalid')
    for action in actions:
        _number(action.get('start'), 0, duration)
        _number(action.get('duration'), .001, duration)
        _require(action['start']+action['duration'] <= duration+1e-6, 'animation_lab_engine_actions_invalid')
    _require(observations.get('schema') == 'video.animation-lab-engine-observations.v1'
             and observations.get('timeline_sha256') == sha256(timeline) and observations.get('engine') == engine,
             'animation_lab_engine_timeline_binding_mismatch')
    _require(observations.get('seek_protocol') == 'hyperframes-core-renderSeek-exact'
             and type(observations.get('render_fps')) is int and observations['render_fps'] == FPS,
             'animation_lab_engine_seek_protocol_invalid')
    _require(observations.get('page_errors') == [], 'animation_lab_engine_page_errors')
    _require(observations.get('rendering') == ('canvas_2d' if engine == 'canvas' else 'webgl_software_requested'),
             'animation_lab_engine_context_invalid')
    windows = observations.get('windows')
    _require(type(windows) is list and len(windows) == len(actions)
             and all(type(row) is dict and type(row.get('ordinal')) is int for row in windows)
             and {row['ordinal'] for row in windows} == {row['ordinal'] for row in actions},
             'animation_lab_engine_windows_missing')
    by_ordinal = {row['ordinal']: row for row in windows}
    for action in actions:
        window = by_ordinal[action['ordinal']]
        _require(set(window) == {'ordinal', 'before', 'middle', 'after'}, 'animation_lab_engine_windows_missing')
        times = dict(before=max(0, action['start']-1/FPS), middle=action['start']+action['duration']/2,
                     after=min(duration-1/FPS, action['start']+action['duration']+1/FPS))
        samples = {key: _sample(plan, timeline, window[key], expected) for key, expected in times.items()}
        _require(window['before']['frame_sha256'] != window['after']['frame_sha256']
                 and _scene_change(samples['before'], samples['after']), 'animation_lab_engine_change_missing')
        if engine == 'three':
            _require(_three_target_change(action.get('target'), samples['before'], samples['after']),
                     'animation_lab_engine_target_change_missing')
        if engine == 'canvas' and action.get('kind') in {'move', 'reveal'}:
            before = next((row for row in samples['before']['objects'] if row['id'] == action.get('target')), None)
            after = next((row for row in samples['after']['objects'] if row['id'] == action.get('target')), None)
            _require(before is not None and after is not None, 'animation_lab_engine_objects_invalid')
            if action['kind'] == 'move':
                _require(math.hypot(after['x']-before['x'], after['y']-before['y']) > 1,
                         'animation_lab_engine_change_missing')
            else:
                _require(before['opacity'] < .01 and after['opacity'] > .99,
                         'animation_lab_engine_change_missing')
    initial, repeated, last = observations.get('initial'), observations.get('repeated_initial'), observations.get('last')
    _sample(plan, timeline, initial, 0)
    _sample(plan, timeline, repeated, 0)
    _sample(plan, timeline, last, duration-1/FPS)
    reverse = observations.get('reverse_seek')
    _require(type(reverse) is dict and reverse.get('repeat_state_equal') is True
             and reverse.get('first_sha256') == initial['frame_sha256']
             and reverse.get('repeated_first_sha256') == repeated['frame_sha256']
             and reverse.get('last_sha256') == last['frame_sha256']
             and initial['frame_sha256'] == repeated['frame_sha256']
             and initial['engine_state'] == repeated['engine_state'], 'animation_lab_engine_reverse_seek_mismatch')
    if engine == 'canvas':
        _require(last['engine_state']['completed_count'] == plan['packet_count'],
                 'animation_lab_engine_completion_missing')
    return dict(schema='video.animation-lab-engine-review.v1', technical_status='passed', engine=engine,
        plan_sha256=sha256(plan), timeline_sha256=sha256(timeline), observation_sha256=sha256(observations),
        checked_action_windows=len(windows), visual_comprehension='pending_user_review',
        checks=['bound_plan_and_timeline', 'native_exact_seek', 'actual_engine_context', 'bounded_visible_objects',
                'nonblank_bitmap_samples', 'declared_action_changes', 'reverse_seek'],
        production_admitted=False)
