"""Synthetic review contracts; these fixtures do not execute a browser."""
from copy import deepcopy
import json

import pytest

from agent_service.contracts import ServiceError
from video_workbench.animation_lab_scenarios import (
    SCENARIOS, compile_scenario_project, example_scenario_plan, expected_observations,
)
from video_workbench.animation_lab_scenarios_review import verify_scenario_observations
from video_workbench.motion import FPS, estimated_alignment, make_beats, sha256


def fixture(identity='chart-compute-backlog', variant='candidate'):
    """Illustrative observation-shaped data, explicitly not native evidence."""
    plan = example_scenario_plan(identity)
    beats = make_beats(plan['narrative'])
    alignment = estimated_alignment(beats, 'a'*64, plan['duration_seconds'])
    timeline = json.loads(compile_scenario_project(plan, beats, alignment, variant=variant)['timeline.json'])

    def sample(time):
        oracle = expected_observations(plan, time)
        objects = []
        for key in timeline['object_ids']:
            node = dict(id=key, tag='g' if plan['engine'] == 'svg' else 'div', opacity=1,
                text='synthetic fixture', bounds=dict(x=100+time, y=180, width=80, height=24),
                attributes={'transform': f'translate({time} 0)',
                            'd': f'M0 0L{time+1} 2', 'stroke-dashoffset': str(time)})
            for name, value in oracle.get(key, {}).items():
                if name == 'attributes':
                    node['attributes'].update(value)
                else:
                    node[name] = value
            if 'value' in node:
                node['tag'] = 'input'
            objects.append(node)
        if identity == 'interface-shop-filter':
            products = plan['data']['products']
            visible = [row for row in products if (time < 2 or row['wireless'])
                       and (time < 6 or row['price'] <= plan['data']['limit'])]
            if time >= 10:
                visible.sort(key=lambda row: row['price'])
            order = [row['id'] for row in visible]
            for node in objects:
                if node['id'].startswith('shop-row-'):
                    key = node['id'].removeprefix('shop-row-')
                    if key in order:
                        node['bounds'].update(y=220+order.index(key)*49, width=320, height=49)
                    else:
                        node['opacity'] = 0
                        node['bounds'].update(y=220+len(order)*49, width=320, height=0)
        return dict(time=time, actual_time=time,
            frame_sha256=sha256(dict(synthetic_time=time, scenario=identity)),
            engine_state=dict(engine='scenario-effects', context=plan['engine'],
                scenario_id=identity, width=1280, height=720, time=time, deterministic=True,
                objects=objects,
                text_boxes=[dict(text=text, opacity=1, font_size=22,
                                 bounds=dict(x=48, y=y, width=220, height=28))
                            for text, y in [('synthetic title', 28), ('synthetic takeaway', 642)]],
                semantic=dict(source='synthetic_contract_fixture_not_browser_execution')))

    initial, last = sample(0), sample(plan['duration_seconds']-1/FPS)
    observations = dict(schema='video.animation-lab-engine-observations.v1', engine='scenario-effects',
        rendering='svg_dom' if plan['engine'] == 'svg' else 'html_dom',
        seek_protocol='hyperframes-core-renderSeek-exact', render_fps=FPS, page_errors=[],
        timeline_sha256=sha256(timeline), initial=initial, repeated_initial=deepcopy(initial), last=last,
        reverse_seek=dict(first_sha256=initial['frame_sha256'], last_sha256=last['frame_sha256'],
                          repeated_first_sha256=initial['frame_sha256'], repeat_state_equal=True),
        windows=[dict(ordinal=action['ordinal'], before=sample(max(0, action['start']-1/FPS)),
                      middle=sample(action['start']+action['duration']/2),
                      after=sample(min(plan['duration_seconds']-1/FPS, action['start']+action['duration']+1/FPS)))
                 for action in timeline['actions']])
    return plan, timeline, observations


@pytest.mark.parametrize('identity', SCENARIOS)
@pytest.mark.parametrize('variant', ['baseline', 'candidate'])
def test_contract_fixtures_cover_all_domain_oracles_without_approving_aesthetics(identity, variant):
    result = verify_scenario_observations(*fixture(identity, variant))
    assert result['technical_status'] == 'passed'
    assert result['checked_data_facts'] >= 15
    assert result['visual_comprehension'] == 'pending_developer_review'
    assert result['production_admitted'] is False
    assert result['narration_timing'] == 'not_evaluated'
    assert 'independent_data_oracle' in result['checks']


@pytest.mark.parametrize('identity', SCENARIOS)
def test_false_actual_dom_cannot_pass_by_claiming_correct_semantics(identity):
    plan, timeline, observed = fixture(identity)
    sample = observed['windows'][0]['middle']
    state = sample['engine_state']
    oracle = expected_observations(plan, sample['time'])
    key = next(iter(oracle))
    target = next(row for row in state['objects'] if row['id'] == key)
    condition = oracle[key]
    if 'text' in condition:
        target['text'] = 'wrong actual DOM'
    elif 'attributes' in condition:
        target['attributes'][next(iter(condition['attributes']))] = 'wrong actual attribute'
    elif 'value' in condition:
        target['value'] = 'wrong actual input'
    else:
        target['disabled'] = not condition['disabled']
    state['semantic'] = dict(everything_correct=True, expected=oracle)
    with pytest.raises(ServiceError, match='semantic_mismatch'):
        verify_scenario_observations(plan, timeline, observed)


@pytest.mark.parametrize('failure', ['context', 'timeline', 'engine', 'rendering', 'clock', 'digest', 'window',
                                    'object', 'duplicate_object', 'small_text', 'clipped_text', 'negative_bounds',
                                    'nonfinite_hidden', 'semantic_missing', 'page_error', 'reset_pixels', 'reset_state'])
def test_incomplete_boundaries_and_false_reset_fail_closed(failure):
    plan, timeline, observed = fixture()
    middle = observed['windows'][0]['middle']
    if failure == 'context':
        middle['engine_state']['context'] = 'html'
    elif failure == 'timeline':
        timeline['actions'].pop()
        observed['timeline_sha256'] = sha256(timeline)
    elif failure == 'engine':
        observed['engine'] = 'svg-effects'
    elif failure == 'rendering':
        observed['rendering'] = 'html_dom'
    elif failure == 'clock':
        middle['actual_time'] += .5
    elif failure == 'digest':
        middle['frame_sha256'] = 'not-a-digest'
    elif failure == 'window':
        observed['windows'].pop()
    elif failure == 'object':
        middle['engine_state']['objects'].pop()
    elif failure == 'duplicate_object':
        middle['engine_state']['objects'][-1] = deepcopy(middle['engine_state']['objects'][0])
    elif failure == 'small_text':
        middle['engine_state']['text_boxes'][0]['font_size'] = 10
    elif failure == 'clipped_text':
        middle['engine_state']['text_boxes'][0]['bounds']['x'] = 1250
    elif failure == 'negative_bounds':
        middle['engine_state']['objects'][0]['bounds']['width'] = -1
    elif failure == 'nonfinite_hidden':
        middle['engine_state']['objects'][0]['opacity'] = 0
        middle['engine_state']['objects'][0]['bounds']['x'] = float('nan')
    elif failure == 'semantic_missing':
        middle['engine_state'].pop('semantic')
    elif failure == 'page_error':
        observed['page_errors'] = ['ReferenceError: missing']
    elif failure == 'reset_pixels':
        observed['repeated_initial']['frame_sha256'] = 'f'*64
        observed['reverse_seek']['repeated_first_sha256'] = 'f'*64
    else:
        observed['repeated_initial']['engine_state']['semantic']['source'] = 'mutated reset'
    with pytest.raises(ServiceError):
        verify_scenario_observations(plan, timeline, observed)


def test_unrelated_frame_and_semantic_changes_do_not_prove_named_target_motion():
    plan, timeline, observed = fixture()
    window = observed['windows'][1]
    target = timeline['actions'][1]['target']
    before = next(row for row in window['before']['engine_state']['objects'] if row['id'] == target)
    after = window['after']['engine_state']['objects']
    index = next(index for index, row in enumerate(after) if row['id'] == target)
    # This group is not itself a numeric oracle entry; its separately observed
    # child value stays correct so this reaches the named-action guard.
    after[index] = deepcopy(before)
    window['after']['engine_state']['semantic'] = {'unrelated_object_changed': True}
    with pytest.raises(ServiceError, match='target_change_missing'):
        verify_scenario_observations(plan, timeline, observed)


def test_identical_screenshots_fail_even_when_target_state_claims_motion():
    plan, timeline, observed = fixture()
    observed['windows'][0]['after']['frame_sha256'] = observed['windows'][0]['before']['frame_sha256']
    with pytest.raises(ServiceError, match='target_change_missing'):
        verify_scenario_observations(plan, timeline, observed)


@pytest.mark.parametrize('identity,key,attribute', [
    ('chart-compute-backlog', 'compute-backlog-path', 'd'),
    ('chart-energy-balance', 'energy-solar-0', 'height'),
    ('chart-logistics-stock', 'logistics-in', 'd'),
    ('chart-learning-gains', 'learning-after-1', 'y'),
])
def test_correct_metric_labels_cannot_hide_incorrect_actual_chart_geometry(identity, key, attribute):
    plan, timeline, observed = fixture(identity)
    # The initial oracle covers the first energy stack as well as all selected
    # charts; all displayed numeric labels and semantic claims remain correct.
    node = next(row for row in observed['initial']['engine_state']['objects'] if row['id'] == key)
    node['attributes'][attribute] = 'M0 0L1 1' if attribute == 'd' else '999'
    with pytest.raises(ServiceError, match='semantic_mismatch'):
        verify_scenario_observations(plan, timeline, observed)


def test_empty_geometry_and_hidden_offscreen_objects_are_allowed_but_finite():
    plan, timeline, observed = fixture()
    middle = observed['windows'][0]['middle']['engine_state']
    middle['objects'][0]['bounds'] = dict(x=0, y=0, width=0, height=0)
    middle['objects'][1]['opacity'] = 0
    middle['objects'][1]['bounds'] = dict(x=-500, y=-100, width=80, height=20)
    assert verify_scenario_observations(plan, timeline, observed)['technical_status'] == 'passed'


def test_zero_width_visible_line_still_must_be_inside_the_stage():
    plan, timeline, observed = fixture()
    observed['windows'][0]['middle']['engine_state']['objects'][0]['bounds'] = dict(x=1800, y=200, width=0, height=100)
    with pytest.raises(ServiceError, match='bounds_invalid'):
        verify_scenario_observations(plan, timeline, observed)


def test_html_input_property_is_verified_separately_from_value_attribute():
    plan, timeline, observed = fixture('interface-library-search')
    target = next(row for row in observed['windows'][0]['middle']['engine_state']['objects'] if row['id'] == 'library-query')
    target['value'] = 'modified property, unchanged attribute'
    with pytest.raises(ServiceError, match='semantic_mismatch'):
        verify_scenario_observations(plan, timeline, observed)


def test_price_sorted_marker_cannot_hide_reversed_real_product_positions():
    plan, timeline, observed = fixture('interface-shop-filter')
    state = observed['last']['engine_state']
    cheap = next(row for row in state['objects'] if row['id'] == 'shop-row-p4')
    expensive = next(row for row in state['objects'] if row['id'] == 'shop-row-p1')
    cheap['bounds']['y'], expensive['bounds']['y'] = expensive['bounds']['y'], cheap['bounds']['y']
    with pytest.raises(ServiceError, match='order_geometry_mismatch'):
        verify_scenario_observations(plan, timeline, observed)


def test_filter_count_cannot_hide_a_still_visible_rejected_product():
    plan, timeline, observed = fixture('interface-shop-filter')
    rejected = next(row for row in observed['last']['engine_state']['objects'] if row['id'] == 'shop-row-p2')
    rejected.update(opacity=1)
    rejected['bounds'].update(height=49)
    with pytest.raises(ServiceError, match='visibility_mismatch'):
        verify_scenario_observations(plan, timeline, observed)


def test_visible_product_rows_must_not_overlap_even_when_in_correct_order():
    plan, timeline, observed = fixture('interface-shop-filter')
    expensive = next(row for row in observed['last']['engine_state']['objects'] if row['id'] == 'shop-row-p1')
    expensive['bounds']['y'] -= 20
    with pytest.raises(ServiceError, match='order_geometry_mismatch'):
        verify_scenario_observations(plan, timeline, observed)


@pytest.mark.parametrize('timeline', [None, [], 'not-a-timeline'])
def test_malformed_timeline_cannot_escape_as_uncaught_attribute_error(timeline):
    plan, _, observed = fixture()
    with pytest.raises(ServiceError, match='recipe_mismatch'):
        verify_scenario_observations(plan, timeline, observed)
