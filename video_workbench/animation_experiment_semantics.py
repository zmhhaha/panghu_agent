"""Independent numeric oracles for bounded animation capability experiments.

Expected values come only from frozen inputs. Observed values come from visible
DOM text and SVG geometry measured by the isolated renderer host, never from a
generated scene's state object, data-value attribute or self-check.
"""
from __future__ import annotations

import math
import re


SCHEMA = 'video.animation-experiment-semantic.v1'
OBSERVER_REVISION = 'visible-semantic-geometry-v1'
KINDS = frozenset({'cohort-statistics', 'parameter-geometry', 'event-delay'})
NUMBER = re.compile(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z')
IDENTIFIER = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,31}\Z')


def _require(value, code='invalid'):
    if not value:
        raise ValueError('animation_experiment_semantic_' + code)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and abs(value) <= 1e9


def _ids(rows):
    _require(type(rows) is list and 1 <= len(rows) <= 16)
    identifiers = []
    for row in rows:
        _require(type(row) is dict and type(row.get('id')) is str
                 and IDENTIFIER.fullmatch(row['id']))
        identifiers.append(row['id'])
    _require(len(identifiers) == len(set(identifiers)))


def validate_spec(spec, duration):
    _require(type(spec) is dict and spec.get('schema') == SCHEMA
             and spec.get('kind') in KINDS and type(spec.get('data')) is dict)
    data, kind = spec['data'], spec['kind']
    _require(_number(duration) and duration > 0)
    if kind == 'cohort-statistics':
        rows = data.get('entities')
        _ids(rows)
        _require(_number(data.get('threshold')))
        for row in rows:
            _require((row.get('value') is None or _number(row['value']))
                     and _number(row.get('weight')) and row['weight'] > 0
                     and type(row.get('group')) is str and 0 < len(row['group']) <= 32)
        _require(_number(spec.get('filter_complete_seconds'))
                 and 1 < spec['filter_complete_seconds'] < duration)
    elif kind == 'parameter-geometry':
        matrix = data.get('matrix')
        _require(type(matrix) is list and len(matrix) == 2
                 and all(type(row) is list and len(row) == 2
                         and all(_number(v) for v in row) for row in matrix))
        _ids(data.get('points'))
        _require(type(data.get('origin')) is list and len(data['origin']) == 2
                 and all(_number(v) for v in data['origin'])
                 and _number(data.get('unit_px')) and data['unit_px'] > 0)
        for row in data['points']:
            _require(type(row.get('xy')) is list and len(row['xy']) == 2
                     and all(_number(v) for v in row['xy']))
        _require(_number(spec.get('stationary_until_seconds'))
                 and _number(spec.get('movement_complete_seconds'))
                 and 0 <= spec['stationary_until_seconds'] < spec['movement_complete_seconds'] < duration)
    else:
        _require(_number(data.get('initial_stock')) and data['initial_stock'] >= 0)
        _ids(data.get('events'))
        _ids(data.get('demands'))
        for row in data['events']:
            _require(_number(row.get('quantity')) and row['quantity'] > 0
                     and _number(row.get('order_time')) and _number(row.get('arrival_time'))
                     and 0 <= row['order_time'] < row['arrival_time'] < duration)
        for row in data['demands']:
            _require(_number(row.get('quantity')) and row['quantity'] > 0
                     and _number(row.get('time')) and 0 <= row['time'] < duration)
        for time in sorted({row['time'] for row in data['demands']}):
            balance = data['initial_stock'] + sum(row['quantity'] for row in data['events']
                if row['arrival_time'] <= time) - sum(row['quantity'] for row in data['demands']
                if row['time'] <= time)
            _require(balance >= 0, 'unsupported_stockout')
    return spec


def state_at(spec, time):
    """Recompute target state from input rules, independent of generated code."""
    data, kind = spec['data'], spec['kind']
    text, numeric, geometry = {}, {}, {}
    if kind == 'cohort-statistics':
        rows = data['entities']
        filtered = time >= spec['filter_complete_seconds']
        stable = time <= 1 or filtered
        selected = [r for r in rows if not filtered or
                    (r['value'] is not None and r['value'] >= data['threshold'])]
        known = [r for r in selected if r['value'] is not None]
        weights = sum(r['weight'] for r in known)
        if stable:
            numeric.update(count=len(selected), known_count=len(known), weight_sum=weights,
                mean=sum(r['value'] for r in known) / len(known) if known else None,
                weighted_mean=sum(r['value'] * r['weight'] for r in known) / weights if known else None)
        for row in rows:
            identity = row['id']
            text['identity-' + identity] = identity
            if row['value'] is None:
                text['value-' + identity] = '未知'
            else:
                numeric['value-' + identity] = row['value']
            if stable:
                text['selection-' + identity] = ('未知' if row['value'] is None else
                    '保留' if row in selected else '排除')
        numeric = {key if key.startswith('value-') else 'metric-' + key: value
                   for key, value in numeric.items()}
    elif kind == 'parameter-geometry':
        start, end = spec['stationary_until_seconds'], spec['movement_complete_seconds']
        progress = max(0, min(1, (time - start) / (end - start)))
        matrix = [[(1 if i == j else 0) * (1 - progress) + data['matrix'][i][j] * progress
                   for j in range(2)] for i in range(2)]
        a, b = matrix[0]
        c, d = matrix[1]
        determinant = a * d - b * c
        numeric.update({'metric-determinant': determinant, 'metric-area_scale': abs(determinant)})
        origin, unit = data['origin'], data['unit_px']
        def screen(x, y):
            return [origin[0] + unit * (a * x + b * y), origin[1] - unit * (c * x + d * y)]
        geometry['origin'] = dict(kind='point', point=origin)
        for row in data['points']:
            geometry['point-' + row['id']] = dict(kind='point', point=screen(*row['xy']))
            text['identity-' + row['id']] = row['id']
        geometry['basis-x'] = dict(kind='line', points=[origin, screen(1, 0)])
        geometry['basis-y'] = dict(kind='line', points=[origin, screen(0, 1)])
        geometry['unit-area'] = dict(kind='polygon', points=[screen(0, 0), screen(1, 0), screen(1, 1), screen(0, 1)])
    else:
        arrived = sum(r['quantity'] for r in data['events'] if time >= r['arrival_time'])
        demand = sum(r['quantity'] for r in data['demands'] if time >= r['time'])
        transit = sum(r['quantity'] for r in data['events'] if r['order_time'] <= time < r['arrival_time'])
        numeric.update({'metric-stock': data['initial_stock'] + arrived - demand,
            'metric-in_transit': transit, 'metric-cumulative_arrivals': arrived,
            'metric-cumulative_demand': demand})
        for row in data['events']:
            text['event-' + row['id']] = ('未下单' if time < row['order_time'] else
                '在途' if time < row['arrival_time'] else '已到货')
        for row in data['demands']:
            text['demand-' + row['id']] = '待发生' if time < row['time'] else '已消耗'
    return dict(text=text, numeric=numeric, geometry=geometry)


def _bounds(row, constraints, *, degenerate=False):
    box = row.get('bounds', {})
    if not all(_number(box.get(k)) for k in ('x', 'y', 'width', 'height')):
        return False
    return (row.get('rendered') is True and (degenerate or row.get('visible') is True)
        and box['width'] >= 0 and box['height'] >= 0
        and box['x'] >= -2 and box['y'] >= -2
        and box['x'] + box['width'] <= constraints['width'] + 2
        and box['y'] + box['height'] <= constraints['height'] + 2)


def _numeric_text(value):
    if type(value) is not str:
        return None
    clean = value.strip().replace('\u2212', '-')
    if NUMBER.fullmatch(clean) is None:
        return None
    number = float(clean)
    return number if math.isfinite(number) else None


def _close(left, right, tolerance=2):
    return (type(left) is list and len(left) == 2 and all(_number(v) for v in left)
            and math.dist(left, right) <= tolerance)


def validate_observations(observations, constraints, runtime_issues=()):
    spec = validate_spec(constraints['semantic'], constraints['duration_seconds'])
    issues = list(runtime_issues)
    if observations.get('semantic_observer_revision') != OBSERVER_REVISION:
        issues.append('semantic_observer_revision_missing')
    samples = observations['samples']
    if observations.get('reverse_initial_semantics') != samples[0].get('semantics'):
        issues.append('reverse_seek_semantics_differ')
    checked = 0
    for sample in samples:
        actual = sample.get('semantics')
        if type(actual) is not list or len(actual) > 128:
            issues.append('semantic_observations_missing')
            continue
        ids = [r.get('id') for r in actual if type(r) is dict]
        if len(ids) != len(actual) or any(type(k) is not str for k in ids) or len(set(ids)) != len(ids):
            issues.append('semantic_marker_duplicated_or_invalid')
            continue
        by_id = {row['id']: row for row in actual}
        expected = state_at(spec, sample['time'])
        for key, target in {**expected['text'], **expected['numeric']}.items():
            row = by_id.get(key, {})
            if not _bounds(row, constraints) or row.get('text_status') != 'measured':
                issues.append('semantic_text_not_visible:' + key)
                continue
            if key.startswith(('identity-', 'value-', 'selection-')):
                identity = key.split('-', 1)[1]
                if row.get('entity_attachment') != 'entity-' + identity:
                    issues.append('semantic_entity_attachment_mismatch:' + key)
            if key in expected['text']:
                if row.get('text') != target:
                    issues.append('semantic_text_mismatch:' + key)
            elif target is None:
                if row.get('text') != '未知':
                    issues.append('semantic_unknown_coerced_to_number:' + key)
            else:
                number = _numeric_text(row.get('text'))
                if number is None or abs(number - target) > max(.02, abs(target) * .0001):
                    issues.append('semantic_numeric_mismatch:' + key)
            checked += 1
        if spec['kind'] == 'cohort-statistics':
            for entity in spec['data']['entities']:
                key = 'entity-' + entity['id']
                if not _bounds(by_id.get(key, {}), constraints):
                    issues.append('semantic_entity_not_visible:' + key)
        for key, target in expected['geometry'].items():
            row = by_id.get(key, {})
            geometry = row.get('geometry')
            if not _bounds(row, constraints, degenerate=target['kind'] != 'point') or type(geometry) is not dict:
                issues.append('semantic_geometry_not_rendered:' + key)
                continue
            if key.startswith('point-') and row.get('entity_attachment') != 'entity-' + key.split('-', 1)[1]:
                issues.append('semantic_entity_attachment_mismatch:' + key)
            if geometry.get('kind') != target['kind']:
                issues.append('semantic_geometry_kind_mismatch:' + key)
            elif target['kind'] == 'point':
                if not _close(geometry.get('point'), target['point']):
                    issues.append('semantic_point_mismatch:' + key)
            else:
                points = geometry.get('points')
                if type(points) is not list or len(points) != len(target['points']) or not all(
                        _close(left, right) for left, right in zip(points or [], target['points'])):
                    issues.append('semantic_geometry_mismatch:' + key)
            checked += 1
    return dict(status='failed' if issues else 'passed', issues=sorted(set(issues)),
        metrics=dict(semantic_kind=spec['kind'], frames_checked=len(samples),
                     independently_checked_values=checked, expected_values_source='frozen_input_rules',
                     actual_values_source='isolated_world_visible_text_and_svg_geometry'),
        scope='visible_semantic_text_and_svg_geometry_v1', aesthetic_approval=False)
