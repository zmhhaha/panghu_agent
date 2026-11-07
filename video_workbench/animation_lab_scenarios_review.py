"""Review bound native SVG/HTML observations against independent data oracles.

The capture coordinator must separately verify actual screenshot bytes and
renderer provenance. This checks measured DOM and finite requirements, never
approves aesthetics, real narration timing, or production admission.
"""
from __future__ import annotations

import math
import re

from agent_service.contracts import ServiceError
from .animation_lab_scenarios import (
    COMPILER_VERSION, expected_observations, family_for, recipe_for, validate_scenario_plan,
)
from .motion import FPS, sha256


_SHA = re.compile(r'[0-9a-f]{64}\Z')
_SVG_TAGS = {'svg', 'g', 'rect', 'circle', 'path', 'ellipse', 'text', 'tspan',
             'polygon', 'polyline', 'line'}
_HTML_TAGS = {'div', 'span', 'p', 'h1', 'h2', 'h3', 'h4', 'label', 'input',
              'button', 'ul', 'ol', 'li', 'section', 'article', 'aside', 'header',
              'footer', 'select', 'option', 'strong', 'small', 'table', 'thead',
              'tbody', 'tr', 'th', 'td', 'form', 'output', 'meter', 'progress',
              'a', 'b', 'em', 'i', 'fieldset', 'legend', 'main', 'nav', 'canvas'}


def _require(value, code='animation_lab_scenario_observations_invalid'):
    if not value:
        raise ServiceError(code, 422)


def _number(value, low=-1e9, high=1e9):
    _require(type(value) in (int, float) and math.isfinite(value) and low <= value <= high,
             'animation_lab_scenario_nonfinite_or_out_of_range')
    return float(value)


def _near(value, expected, tolerance=1/FPS+1e-6):
    _require(abs(_number(value)-expected) <= tolerance, 'animation_lab_scenario_seek_mismatch')


def _digest(value):
    _require(type(value) is str and _SHA.fullmatch(value), 'animation_lab_scenario_frame_digest_invalid')


def _finite_tree(value, depth=0):
    _require(depth <= 24, 'animation_lab_scenario_state_too_deep')
    if type(value) is float:
        _require(math.isfinite(value), 'animation_lab_scenario_nonfinite_or_out_of_range')
    elif type(value) is int:
        _require(abs(value) <= 10**100, 'animation_lab_scenario_nonfinite_or_out_of_range')
    elif type(value) is dict:
        _require(len(value) <= 2048 and all(type(key) is str for key in value),
                 'animation_lab_scenario_state_invalid')
        for child in value.values():
            _finite_tree(child, depth+1)
    elif type(value) is list:
        _require(len(value) <= 8192, 'animation_lab_scenario_state_invalid')
        for child in value:
            _finite_tree(child, depth+1)
    else:
        _require(value is None or type(value) in (str, bool), 'animation_lab_scenario_state_invalid')


def _bounds(value, opacity):
    _require(type(value) is dict and set(value) == {'x', 'y', 'width', 'height'},
             'animation_lab_scenario_bounds_invalid')
    x, y = _number(value['x']), _number(value['y'])
    width, height = _number(value['width'], 0), _number(value['height'], 0)
    # Empty labels and zero-length paths genuinely have zero DOM geometry.
    # Hidden objects may lie outside the stage, but their numbers remain finite.
    if opacity > .01 and (width > .001 or height > .001):
        _require(x >= -.5 and y >= -.5 and x+width <= 1280.5 and y+height <= 720.5,
                 'animation_lab_scenario_bounds_invalid')


def _oracle(plan, time, objects):
    try:
        expected = expected_observations(plan, time)
    except (ValueError, TypeError, KeyError, IndexError, OverflowError):
        raise ServiceError('animation_lab_scenario_semantic_oracle_invalid', 422) from None
    _require(type(expected) is dict and 1 <= len(expected) <= 100,
             'animation_lab_scenario_semantic_oracle_invalid')
    facts = 0
    for identity, condition in expected.items():
        _require(type(identity) is str and identity in objects and type(condition) is dict
                 and 1 <= len(condition) <= 4 and set(condition) <= {'text', 'attributes', 'value', 'disabled'},
                 'animation_lab_scenario_semantic_oracle_invalid')
        node = objects[identity]
        if 'text' in condition:
            _require(type(condition['text']) is str and node.get('text') == condition['text'],
                     'animation_lab_scenario_semantic_mismatch')
            facts += 1
        if 'attributes' in condition:
            attrs = condition['attributes']
            _require(type(attrs) is dict and 1 <= len(attrs) <= 30
                     and all(type(key) is str and type(value) is str for key, value in attrs.items()),
                     'animation_lab_scenario_semantic_oracle_invalid')
            _require(all(node['attributes'].get(key) == value for key, value in attrs.items()),
                     'animation_lab_scenario_semantic_mismatch')
            facts += len(attrs)
        if 'value' in condition:
            _require(type(condition['value']) is str and node.get('value') == condition['value'],
                     'animation_lab_scenario_semantic_mismatch')
            facts += 1
        if 'disabled' in condition:
            _require(type(condition['disabled']) is bool and node.get('disabled') is condition['disabled'],
                     'animation_lab_scenario_semantic_mismatch')
            facts += 1
    return facts


def _spatial_relationships(plan, time, objects):
    """Verify visible layout semantics rather than trusting CSS/order labels."""
    if plan['scenario_id'] != 'interface-shop-filter':
        return 0
    products = plan['data']['products']
    visible = [row for row in products if (time < 2 or row['wireless'])
               and (time < 6 or row['price'] <= plan['data']['limit'])]
    if time >= 10:
        visible.sort(key=lambda row: row['price'])
    visible_ids = {row['id'] for row in visible}
    for product in products:
        identity = 'shop-row-'+product['id']
        _require(identity in objects, 'animation_lab_scenario_semantic_geometry_missing')
        node = objects[identity]
        if product['id'] in visible_ids:
            _require(node['opacity'] > .99 and node['bounds']['width'] >= 1 and node['bounds']['height'] >= 1,
                     'animation_lab_scenario_semantic_visibility_mismatch')
        else:
            _require(node['opacity'] < .01, 'animation_lab_scenario_semantic_visibility_mismatch')
    for before, after in zip(visible, visible[1:]):
        a, b = (objects['shop-row-'+row['id']]['bounds'] for row in (before, after))
        _require(a['y']+a['height'] <= b['y']+.5,
                 'animation_lab_scenario_semantic_order_geometry_mismatch')
    return len(products)+max(0, len(visible)-1)


def _sample(plan, timeline, sample, time):
    _require(type(sample) is dict, 'animation_lab_scenario_sample_missing')
    _near(sample.get('time'), time, 1e-6)
    _near(sample.get('actual_time'), time)
    _digest(sample.get('frame_sha256'))
    state = sample.get('engine_state')
    _require(type(state) is dict and state.get('engine') == 'scenario-effects'
             and state.get('context') == plan['engine'] and state.get('deterministic') is True
             and state.get('scenario_id') == plan['scenario_id']
             and state.get('width') == 1280 and state.get('height') == 720,
             'animation_lab_scenario_context_invalid')
    _near(state.get('time'), time)
    rows = state.get('objects')
    _require(type(rows) is list and len(rows) == len(timeline['object_ids'])
             and all(type(row) is dict and type(row.get('id')) is str for row in rows)
             and len({row['id'] for row in rows}) == len(rows)
             and {row['id'] for row in rows} == set(timeline['object_ids']),
             'animation_lab_scenario_objects_invalid')
    tags = _SVG_TAGS if plan['engine'] == 'svg' else _HTML_TAGS | _SVG_TAGS
    for row in rows:
        opacity = _number(row.get('opacity'), 0, 1)
        _bounds(row.get('bounds'), opacity)
        _require(row.get('tag') in tags and type(row.get('text')) is str and len(row['text']) <= 12000
                 and type(row.get('attributes')) is dict and len(row['attributes']) <= 48
                 and all(type(key) is str and type(value) is str and len(value) <= 16000
                         for key, value in row['attributes'].items()), 'animation_lab_scenario_dom_invalid')
        if 'value' in row:
            _require(type(row['value']) is str and len(row['value']) <= 4000, 'animation_lab_scenario_dom_invalid')
        if 'disabled' in row:
            _require(type(row['disabled']) is bool, 'animation_lab_scenario_dom_invalid')
    texts = state.get('text_boxes')
    _require(type(texts) is list and 2 <= len(texts) <= 512, 'animation_lab_scenario_text_missing')
    for row in texts:
        _require(type(row) is dict and type(row.get('text')) is str and len(row['text']) <= 12000,
                 'animation_lab_scenario_text_invalid')
        opacity = _number(row.get('opacity'), 0, 1)
        _number(row.get('font_size'), 14, 128)
        _bounds(row.get('bounds'), opacity if row['text'] else 0)
    _require(type(state.get('semantic')) is dict, 'animation_lab_scenario_semantic_missing')
    objects = {row['id']: row for row in rows}
    native_facts = 0
    if plan['engine'] in {'canvas', 'three'}:
        _require(type(state.get('native_state')) is dict, 'animation_lab_scenario_native_missing')
        try:
            native_facts = family_for(plan).verify_native_observation(
                plan, time, timeline['variant'], state['native_state'])
        except (ServiceError, ValueError, TypeError, KeyError, IndexError, OverflowError, AttributeError):
            raise ServiceError('animation_lab_scenario_native_mismatch', 422) from None
        _require(type(native_facts) is int and native_facts > 0,
                 'animation_lab_scenario_native_oracle_invalid')
    return objects, native_facts+_oracle(plan, time, objects)+_spatial_relationships(plan, time, objects)


def verify_scenario_observations(plan, timeline, observations):
    for value in (plan, timeline, observations):
        _finite_tree(value)
    _require(type(timeline) is dict, 'animation_lab_scenario_recipe_mismatch')
    try:
        plan = validate_scenario_plan(plan)
        recipe = recipe_for(plan, timeline.get('variant'))
    except (ValueError, TypeError, KeyError, IndexError):
        raise ServiceError('animation_lab_scenario_recipe_mismatch', 422) from None
    actions = [dict(row, ordinal=index) for index, row in enumerate(recipe['actions'])]
    duration = plan['duration_seconds']
    _require(timeline.get('schema') == 'video.motion-timeline.v1' and timeline.get('compiler') == COMPILER_VERSION
             and timeline.get('engine') == 'scenario-effects' and timeline.get('rendering') == plan['engine']
             and timeline.get('fps') == FPS and timeline.get('width') == 1280 and timeline.get('height') == 720
             and timeline.get('duration_seconds') == duration and timeline.get('plan_sha256') == sha256(plan)
             and timeline.get('object_ids') == recipe['object_ids'] and timeline.get('actions') == actions
             and timeline.get('template') == plan['scenario_id'] and timeline.get('deterministic') is True,
             'animation_lab_scenario_timeline_mismatch')
    _require(type(observations) is dict and observations.get('schema') == 'video.animation-lab-engine-observations.v1'
             and observations.get('timeline_sha256') == sha256(timeline)
             and observations.get('engine') == 'scenario-effects'
             and observations.get('seek_protocol') == 'hyperframes-core-renderSeek-exact'
             and observations.get('render_fps') == FPS
             and observations.get('rendering') == {'svg':'svg_dom', 'html':'html_dom',
                 'canvas':'canvas_2d', 'three':'webgl_software_requested'}[plan['engine']]
             and observations.get('page_errors') == [], 'animation_lab_scenario_observations_binding_mismatch')
    windows = observations.get('windows')
    _require(type(windows) is list and len(windows) == len(actions)
             and all(type(row) is dict and type(row.get('ordinal')) is int for row in windows)
             and {row['ordinal'] for row in windows} == set(range(len(actions))),
             'animation_lab_scenario_windows_missing')
    by_ordinal = {row['ordinal']: row for row in windows}
    facts = 0
    for action in actions:
        window = by_ordinal[action['ordinal']]
        _require(set(window) == {'ordinal', 'before', 'middle', 'after'}, 'animation_lab_scenario_windows_missing')
        times = dict(before=max(0, action['start']-1/FPS), middle=action['start']+action['duration']/2,
                     after=min(duration-1/FPS, action['start']+action['duration']+1/FPS))
        states = {}
        for name, time in times.items():
            states[name], count = _sample(plan, timeline, window[name], time)
            facts += count
        target = action['target']
        _require(states['before'][target] != states['after'][target]
                 and window['before']['frame_sha256'] != window['after']['frame_sha256'],
                 'animation_lab_scenario_target_change_missing')
        if action['kind'] in {'draw', 'morph'}:
            attribute = 'd' if action['kind'] == 'morph' else 'stroke-dashoffset'
            before, after = (states[key][target]['attributes'].get(attribute) for key in ('before', 'after'))
            _require(before is not None and after is not None and before != after,
                     'animation_lab_scenario_path_change_missing')
    initial, repeated, last = observations.get('initial'), observations.get('repeated_initial'), observations.get('last')
    for sample, time in ((initial, 0), (repeated, 0), (last, duration-1/FPS)):
        _, count = _sample(plan, timeline, sample, time)
        facts += count
    reverse = observations.get('reverse_seek')
    _require(type(reverse) is dict and reverse.get('repeat_state_equal') is True
             and reverse.get('first_sha256') == initial['frame_sha256']
             and reverse.get('last_sha256') == last['frame_sha256']
             and reverse.get('repeated_first_sha256') == repeated['frame_sha256']
             and initial['frame_sha256'] == repeated['frame_sha256']
             and initial['engine_state'] == repeated['engine_state'], 'animation_lab_scenario_reverse_seek_mismatch')
    return dict(schema='video.animation-lab-scenario-review.v1', technical_status='passed',
        scenario_id=plan['scenario_id'], domain=plan['domain'], checked_action_windows=len(actions),
        checked_data_facts=facts, plan_sha256=sha256(plan), timeline_sha256=sha256(timeline),
        observation_sha256=sha256(observations),
        checks=['bound_cross_domain_requirement', 'native_exact_seek',
                'actual_'+{'canvas':'canvas_2d','three':'webgl'}.get(plan['engine'],plan['engine']+'_dom'),
                'bounded_visible_geometry_and_text', 'independent_data_oracle',
                'actual_target_and_frame_changes', 'strict_reverse_seek'],
        visual_comprehension='pending_developer_review', narration_timing='not_evaluated',
        production_admitted=False)
