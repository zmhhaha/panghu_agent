"""Technical checks for real, saved SVG/DOM effect observations.

The capture coordinator separately binds browser/frame provenance. These checks
cover measured times, object/text bounds, actual target changes and restoration;
they do not approve aesthetic quality or production use.
"""
from __future__ import annotations

from agent_service.contracts import ServiceError
from .animation_lab_effects import COMPILER_VERSION, recipe_for, validate_effect_plan
from .animation_lab_engine_review import _finite_tree, _number, _digest, _near, _bounds
from .motion import FPS, sha256


def _require(value, code='animation_lab_effect_observations_invalid'):
    if not value:
        raise ServiceError(code, 422)


def _sample(plan, timeline, sample, time):
    _require(type(sample) is dict, 'animation_lab_effect_sample_missing')
    _near(sample.get('time'), time, 1e-6)
    _near(sample.get('actual_time'), time)
    _digest(sample.get('frame_sha256'))
    state = sample.get('engine_state')
    _require(type(state) is dict and state.get('engine') == 'svg-effects'
             and state.get('context') == 'svg' and state.get('deterministic') is True
             and state.get('effect') == plan['effect'] and state.get('width') == 1280 and state.get('height') == 720,
             'animation_lab_effect_context_invalid')
    _near(state.get('time'), time)
    objects = state.get('objects')
    _require(type(objects) is list and len(objects) == len(timeline['object_ids'])
             and all(type(row) is dict and type(row.get('id')) is str for row in objects)
             and {row['id'] for row in objects} == set(timeline['object_ids']),
             'animation_lab_effect_objects_invalid')
    for row in objects:
        _number(row.get('opacity'), 0, 1)
        _bounds(row.get('bounds'))
        _require(row.get('tag') in {'g', 'rect', 'circle', 'path', 'ellipse', 'text', 'polygon', 'polyline'}
                 and type(row.get('text')) is str and len(row['text']) <= 4000
                 and type(row.get('attributes')) is dict and len(row['attributes']) <= 20
                 and all(type(key) is str and type(value) is str and len(value) <= 8000
                         for key, value in row['attributes'].items()), 'animation_lab_effect_dom_invalid')
    texts = state.get('text_boxes')
    _require(type(texts) is list and 2 <= len(texts) <= 128, 'animation_lab_effect_text_missing')
    for row in texts:
        _require(type(row) is dict and type(row.get('text')) is str and len(row['text']) <= 1000,
                 'animation_lab_effect_text_invalid')
        _number(row.get('opacity'), 0, 1)
        _number(row.get('font_size'), 14, 64)
        if row['text'] and row['opacity'] > .01:
            _bounds(row.get('bounds'))
    _require(type(state.get('semantic')) is dict, 'animation_lab_effect_semantic_missing')
    return {row['id']: row for row in objects}


def verify_effect_observations(plan, timeline, observations):
    for value in (plan, timeline, observations):
        _finite_tree(value)
    try:
        validate_effect_plan(plan)
        recipe = recipe_for(plan, timeline.get('variant'))
    except (ValueError, TypeError, KeyError):
        raise ServiceError('animation_lab_effect_recipe_mismatch', 422) from None
    expected_actions = [dict(row, ordinal=index) for index, row in enumerate(recipe['actions'])]
    _require(timeline.get('schema') == 'video.motion-timeline.v1' and timeline.get('compiler') == COMPILER_VERSION
             and timeline.get('engine') == 'svg-effects' and timeline.get('fps') == FPS
             and timeline.get('width') == 1280 and timeline.get('height') == 720
             and timeline.get('duration_seconds') == 18 and timeline.get('plan_sha256') == sha256(plan)
             and timeline.get('object_ids') == recipe['object_ids'] and timeline.get('actions') == expected_actions,
             'animation_lab_effect_timeline_mismatch')
    _require(type(observations) is dict and observations.get('schema') == 'video.animation-lab-engine-observations.v1'
             and observations.get('timeline_sha256') == sha256(timeline) and observations.get('engine') == 'svg-effects'
             and observations.get('seek_protocol') == 'hyperframes-core-renderSeek-exact'
             and observations.get('render_fps') == FPS and observations.get('rendering') == 'svg_dom'
             and observations.get('page_errors') == [], 'animation_lab_effect_observations_binding_mismatch')
    windows = observations.get('windows')
    _require(type(windows) is list and len(windows) == len(expected_actions)
             and all(type(row) is dict and type(row.get('ordinal')) is int for row in windows)
             and {row['ordinal'] for row in windows} == set(range(len(expected_actions))),
             'animation_lab_effect_windows_missing')
    by_ordinal = {row['ordinal']: row for row in windows}
    for action in expected_actions:
        window = by_ordinal[action['ordinal']]
        _require(set(window) == {'ordinal', 'before', 'middle', 'after'}, 'animation_lab_effect_windows_missing')
        times = dict(before=max(0, action['start']-1/FPS), middle=action['start']+action['duration']/2,
                     after=min(18-1/FPS, action['start']+action['duration']+1/FPS))
        states = {key: _sample(plan, timeline, window[key], time) for key, time in times.items()}
        target = action['target']
        _require(states['before'][target] != states['after'][target]
                 and window['before']['frame_sha256'] != window['after']['frame_sha256'],
                 'animation_lab_effect_target_change_missing')
        if action['kind'] in {'draw', 'morph'}:
            key = 'd' if action['kind'] == 'morph' else 'stroke-dashoffset'
            before, after = (states[when][target]['attributes'].get(key) for when in ('before', 'after'))
            _require(before is not None and after is not None and before != after,
                     'animation_lab_effect_path_change_missing')
    initial, repeated, last = observations.get('initial'), observations.get('repeated_initial'), observations.get('last')
    _sample(plan, timeline, initial, 0)
    _sample(plan, timeline, repeated, 0)
    _sample(plan, timeline, last, 18-1/FPS)
    reverse = observations.get('reverse_seek')
    _require(type(reverse) is dict and reverse.get('repeat_state_equal') is True
             and reverse.get('first_sha256') == initial['frame_sha256']
             and reverse.get('last_sha256') == last['frame_sha256']
             and reverse.get('repeated_first_sha256') == repeated['frame_sha256']
             and initial['frame_sha256'] == repeated['frame_sha256']
             and initial['engine_state'] == repeated['engine_state'], 'animation_lab_effect_reverse_seek_mismatch')
    return dict(schema='video.animation-lab-effects-review.v1', technical_status='passed', effect=plan['effect'],
        checked_action_windows=len(windows), plan_sha256=sha256(plan), timeline_sha256=sha256(timeline),
        observation_sha256=sha256(observations), checks=['bound_fixed_recipe', 'native_exact_seek',
            'actual_svg_dom', 'bounded_objects_and_text', 'actual_target_and_frame_changes', 'reverse_seek'],
        visual_comprehension='pending_developer_review', production_admitted=False)
