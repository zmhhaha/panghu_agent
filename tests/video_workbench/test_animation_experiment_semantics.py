"""Input-rule oracles and corrupted actual observations, without model claims."""
from copy import deepcopy

import pytest

from video_workbench.animation_experiment_definitions import versioned_constraints
from video_workbench.animation_experiment_semantics import (
    OBSERVER_REVISION, state_at, validate_spec,
)
from video_workbench.animation_experiment_render import validate_observations


def row(identity, text='', *, geometry=None, entity=None):
    return dict(id=identity, visible=True, rendered=True, text=text,
        text_status='measured' if text else 'missing', entity_attachment=entity,
        bounds=dict(x=100, y=100, width=80, height=30), geometry=geometry)


def observation(constraints):
    """Synthetic host data only: not execution, browser evidence or aesthetics."""
    spec = constraints['semantic']
    samples = []
    for index in range(int(constraints['duration_seconds'] * 24)):
        time = index / 24
        expected = state_at(spec, time)
        values = dict(expected['text'])
        values.update({key: '未知' if value is None else str(round(value, 4))
                       for key, value in expected['numeric'].items()})
        markers = []
        for identity, text in values.items():
            attached = ('entity-' + identity.split('-', 1)[1]
                        if identity.startswith(('identity-', 'value-', 'selection-')) else None)
            markers.append(row(identity, text, entity=attached))
        if spec['kind'] == 'cohort-statistics':
            markers.extend(row('entity-' + entity['id']) for entity in spec['data']['entities'])
        for identity, target in expected['geometry'].items():
            markers.append(row(identity, geometry=deepcopy(target), entity=(
                'entity-' + identity.split('-', 1)[1] if identity.startswith('point-') else None)))
        samples.append(dict(frame_index=index, time=time, frame_sha256=str(index),
                            objects=[], semantics=markers))
    return dict(schema='video.animation-experiment-observations.v1', samples=samples,
        semantic_observer_revision=OBSERVER_REVISION, reverse_initial_sha256='0',
        reverse_initial_objects=[], reverse_initial_semantics=deepcopy(samples[0]['semantics']))


def get(evidence, identity, frame=-1):
    return next(r for r in evidence['samples'][frame]['semantics'] if r['id'] == identity)


@pytest.mark.parametrize('kind', ['cohort-statistics', 'parameter-geometry', 'event-delay'])
@pytest.mark.parametrize('role', ['development', 'held_out'])
def test_three_distinct_oracles_validate_synthetic_observations_with_separate_scope(kind, role):
    constraints = versioned_constraints(kind, role)
    validate_spec(constraints['semantic'], constraints['duration_seconds'])
    result = validate_observations(observation(constraints), constraints)
    assert result['status'] == 'passed'
    assert result['scope'] == 'visible_semantic_text_and_svg_geometry_v1'
    assert not result['aesthetic_approval']
    assert result['metrics']['frames_checked'] == 192


def test_cohort_count_and_weighted_denominator_keep_missing_values_unknown():
    spec = versioned_constraints('cohort-statistics')['semantic']
    initial = state_at(spec, 0)
    assert initial['numeric'] == {'metric-count': 5, 'metric-known_count': 4,
        'metric-weight_sum': 7, 'metric-mean': 6.5, 'metric-weighted_mean': pytest.approx(46 / 7),
        'value-A': 2, 'value-B': 8, 'value-D': 6, 'value-E': 10}
    assert initial['text']['value-C'] == initial['text']['selection-C'] == '未知'
    final = state_at(spec, 4)
    assert final['numeric']['metric-count'] == 3
    assert final['numeric']['metric-mean'] == 8
    assert final['numeric']['metric-weighted_mean'] == pytest.approx(44 / 6)
    assert final['numeric']['metric-weight_sum'] == 6
    assert final['text']['selection-A'] == '排除'
    transition = state_at(spec, 2)
    assert 'metric-count' not in transition['numeric']
    assert 'identity-A' in transition['text']


def test_all_unknown_or_all_excluded_aggregates_remain_undefined():
    spec = versioned_constraints('cohort-statistics')['semantic']
    spec['data']['threshold'] = 100
    result = state_at(spec, 5)['numeric']
    assert result['metric-count'] == result['metric-weight_sum'] == 0
    assert result['metric-mean'] is result['metric-weighted_mean'] is None
    for entity in spec['data']['entities']:
        entity['value'] = None
    assert state_at(spec, 0)['numeric']['metric-known_count'] == 0
    assert state_at(spec, 0)['numeric']['metric-mean'] is None


def test_geometry_uses_current_matrix_including_flip_and_singular_area():
    spec = versioned_constraints('parameter-geometry', 'held_out')['semantic']
    final = state_at(spec, 7)
    assert final['numeric'] == {'metric-determinant': pytest.approx(-.9), 'metric-area_scale': pytest.approx(.9)}
    assert final['geometry']['point-P']['point'] == pytest.approx([523, 320.25])
    spec['data']['matrix'] = [[1, 2], [2, 4]]
    assert state_at(spec, 7)['numeric']['metric-area_scale'] == 0
    assert state_at(spec, 0)['numeric']['metric-determinant'] == 1


def test_event_order_does_not_create_stock_and_equal_timestamp_is_atomic():
    spec = versioned_constraints('event-delay')['semantic']
    before = state_at(spec, 1)
    assert before['numeric']['metric-stock'] == 4
    assert before['numeric']['metric-in_transit'] == 3
    assert before['text']['event-A'] == '在途'
    assert state_at(spec, 4)['numeric']['metric-stock'] == 5
    assert state_at(spec, 6)['numeric']['metric-stock'] == 4
    equal = state_at(versioned_constraints('event-delay', 'held_out')['semantic'], 3)
    assert equal['numeric']['metric-stock'] == 3
    assert equal['numeric']['metric-cumulative_arrivals'] == 2
    assert equal['numeric']['metric-cumulative_demand'] == 5


@pytest.mark.parametrize('identity,change,issue', [
    ('metric-weighted_mean', {'text': '8'}, 'semantic_numeric_mismatch'),
    ('value-C', {'text': '0'}, 'semantic_text_mismatch'),
    ('identity-A', {'text': 'B'}, 'semantic_text_mismatch'),
    ('selection-A', {'text': '保留'}, 'semantic_text_mismatch'),
    ('identity-A', {'entity_attachment': 'entity-B'}, 'semantic_entity_attachment_mismatch'),
    ('metric-count', {'visible': False}, 'semantic_text_not_visible'),
])
def test_cohort_rejects_wrong_denominator_missing_zero_identity_swap_and_hidden_claims(identity, change, issue):
    constraints = versioned_constraints('cohort-statistics')
    evidence = observation(constraints)
    get(evidence, identity).update(change)
    evidence['claims'] = {'all_checks_passed': True}
    result = validate_observations(evidence, constraints)
    assert result['status'] == 'failed'
    assert issue + ':' + identity in result['issues']


@pytest.mark.parametrize('identity,change,issue', [
    ('point-P', {'geometry': {'kind': 'point', 'point': [0, 0]}}, 'semantic_point_mismatch'),
    ('basis-x', {'geometry': {'kind': 'line', 'points': [[640, 390], [730, 390]]}}, 'semantic_geometry_mismatch'),
    ('unit-area', {'geometry': {'kind': 'polygon', 'points': [[0, 0]] * 4}}, 'semantic_geometry_mismatch'),
    ('metric-determinant', {'text': '1'}, 'semantic_numeric_mismatch'),
    ('point-P', {'geometry': None}, 'semantic_geometry_not_rendered'),
])
def test_geometry_rejects_correct_claims_with_wrong_actual_points_or_polygon(identity, change, issue):
    constraints = versioned_constraints('parameter-geometry')
    evidence = observation(constraints)
    get(evidence, identity).update(change)
    assert issue + ':' + identity in validate_observations(evidence, constraints)['issues']


def test_event_rejects_early_arrival_and_double_count_even_with_self_reported_pass():
    constraints = versioned_constraints('event-delay')
    evidence = observation(constraints)
    get(evidence, 'event-A', 48)['text'] = '已到货'
    get(evidence, 'metric-stock', 48)['text'] = '5'
    result = validate_observations(evidence, constraints)
    assert 'semantic_text_mismatch:event-A' in result['issues']
    assert 'semantic_numeric_mismatch:metric-stock' in result['issues']


def test_duplicate_markers_missing_measurements_and_reverse_change_fail_closed():
    constraints = versioned_constraints('event-delay')
    evidence = observation(constraints)
    evidence['samples'][-1]['semantics'].append(deepcopy(get(evidence, 'metric-stock')))
    assert 'semantic_marker_duplicated_or_invalid' in validate_observations(evidence, constraints)['issues']
    evidence = observation(constraints)
    evidence.pop('semantic_observer_revision')
    evidence['reverse_initial_semantics'][0]['text'] = 'wrong'
    result = validate_observations(evidence, constraints)
    assert 'semantic_observer_revision_missing' in result['issues']
    assert 'reverse_seek_semantics_differ' in result['issues']


@pytest.mark.parametrize('kind,mutation', [
    ('cohort-statistics', lambda d: d['entities'][0].update(weight=0)),
    ('parameter-geometry', lambda d: d.update(matrix=[[float('nan'), 0], [0, 1]])),
    ('event-delay', lambda d: d['events'][0].update(arrival_time=.5)),
    ('event-delay', lambda d: d.update(initial_stock=0)),
])
def test_invalid_rules_are_rejected_before_rendering(kind, mutation):
    constraints = versioned_constraints(kind)
    mutation(constraints['semantic']['data'])
    with pytest.raises(ValueError, match='semantic_'):
        validate_spec(constraints['semantic'], constraints['duration_seconds'])
