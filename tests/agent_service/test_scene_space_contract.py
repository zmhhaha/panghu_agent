"""Spatial invariants shared by styles, camera planning and learned recipes."""
from copy import deepcopy
import json
import math
from pathlib import Path

import pytest

from agent_service.contracts import ServiceError
from agent_service.scene_space_contract import (
    MAX_ANCHORS, MAX_LAYERS, MAX_LAYOUT_POINTS, MAX_OBJECTS, MAX_PARENT_DEPTH,
    MAX_REGIONS, MAX_RELATIONS, MAX_WORLD_BYTES, SceneWorldError,
    grid_point, layout_point, validate_world, world_geometry, world_sha256,
)


@pytest.fixture
def world():
    fixture = Path(__file__).parents[1] / 'fixtures' / 'scene_space' / 'world.json'
    return json.loads(fixture.read_bytes())


def compact(world):
    """A small valid world, independent of landscape-specific relations."""
    result = deepcopy(world)
    result['layers'] = [result['layers'][0]]
    result['objects'] = [deepcopy(result['objects'][0])]
    obj = result['objects'][0]
    obj.update(id='item', kind='mark', layer_id='far', parent_id=None,
               bounds=dict(x=0, y=0, width=10, height=10),
               transform=dict(x=0, y=0, scale_x=1, scale_y=1, rotation=0),
               anchors={'base': [0, 0]}, depth=dict(kind='unknown', value=None))
    result['layout'] = dict(grid=None, points=[], regions=[])
    result['relations'] = []
    return result


def rejected(world, code=None):
    with pytest.raises(ServiceError) as failure:
        validate_world(world)
    error = failure.value
    assert isinstance(error, SceneWorldError)
    assert error.status == 422
    assert isinstance(error.details['path'], str)
    assert isinstance(error.details['object_ids'], list)
    if code is not None:
        assert error.code == code
    return error


def test_normalization_is_detached_stable_and_does_not_modify_input(world):
    original = deepcopy(world)
    normalized = validate_world(world)
    assert world == original
    assert normalized['layers'][0]['opacity'] == 1
    assert normalized['layers'][0]['cache_policy'] == 'static'
    assert normalized['objects'][0]['recipe_ref'] is None
    assert normalized['layout']['points'][0]['depth'] == dict(kind='unknown', value=None)
    assert world_sha256(world) == world_sha256(normalized)
    normalized['objects'][0]['transform']['x'] += 1
    assert world == original
    assert world_sha256(world) != world_sha256(normalized)


def test_parent_transform_and_attached_anchors_are_in_world_coordinates(world):
    geometry = world_geometry(world)
    assert geometry['near-tree']['anchors']['root'] == geometry['shore']['anchors']['tree-base']
    assert geometry['leaf-group']['anchors']['base'] == [270, 130]
    assert geometry['leaf-group']['anchors']['base'] == geometry['near-tree']['anchors']['leaf-join']
    assert geometry['near-tree']['bounds']['y'] < geometry['far-mountain']['bounds']['y']
    assert geometry['near-tree']['depth']['value'] > geometry['far-mountain']['depth']['value']


def test_parent_affine_rotation_and_nonuniform_scale_are_composed(world):
    world = compact(world)
    parent = world['objects'][0]
    parent['transform'].update(x=100, y=100, scale_x=2, scale_y=3, rotation=90)
    child = deepcopy(parent)
    child.update(id='child', parent_id='item', anchors={'base': [1, 2]})
    child['transform'] = dict(x=4, y=5, scale_x=1, scale_y=1, rotation=0)
    world['objects'].append(child)
    geometry = world_geometry(world)
    assert geometry['child']['anchors']['base'] == pytest.approx([79, 110])
    assert geometry['child']['bounds'] == pytest.approx(dict(x=55, y=108, width=30, height=20))


def test_grid_and_named_points_are_usable_without_y_based_depth(world):
    assert grid_point(world, 2, 3) == [400, 600]
    result = layout_point(world, 'focus')
    assert result == dict(id='focus', position=[850, 300], depth=dict(kind='unknown', value=None))
    result['position'][0] = 999
    assert layout_point(world, 'focus')['position'][0] == 850
    world['layout']['points'][0]['depth'] = dict(kind='ordinal', value=9)
    assert layout_point(world, 'focus')['depth']['value'] == 9


@pytest.mark.parametrize('column,row', [(-1, 0), (7, 0), (0, 5), (True, 0), (0, .5)])
def test_grid_rejects_invalid_cell(world, column, row):
    with pytest.raises(SceneWorldError):
        grid_point(world, column, row)


def test_missing_layout_queries_are_located(world):
    with pytest.raises(SceneWorldError, match='scene_world_layout_point_missing'):
        layout_point(world, 'absent')
    world['layout']['grid'] = None
    with pytest.raises(SceneWorldError, match='scene_world_grid_missing'):
        grid_point(world, 0, 0)


@pytest.mark.parametrize('projection', ['planar', 'scattered', 'geometric'])
def test_one_layer_and_unknown_depth_are_supported_in_any_projection(world, projection):
    world = compact(world)
    world.update(style='neon-vector')
    world['space']['projection'] = projection
    world['layers'][0].update(parallax=0, opacity=.3, blur=0, cache_policy='none')
    normalized = validate_world(world)
    assert len(normalized['layers']) == 1
    assert normalized['objects'][0]['depth'] == dict(kind='unknown', value=None)
    assert normalized['layers'][0]['parallax'] == 0


@pytest.mark.parametrize('identity,relation', [('near-tree', 'root-on-bank'), ('leaf-group', 'leaf-on-tree')])
def test_floating_root_and_detached_leaf_fail_with_repair_locations(world, identity, relation):
    obj = next(obj for obj in world['objects'] if obj['id'] == identity)
    obj['transform']['x'] += 10
    error = rejected(world, 'scene_world_attachment_mismatch')
    assert identity in error.details['object_ids']
    assert error.details['relation_id'] == relation


def test_support_outside_shore_is_detected_independently_of_attachment(world):
    world['layout']['regions'][0]['bounds']['y'] = 700
    error = rejected(world, 'scene_world_support_mismatch')
    assert error.details['object_ids'] == ['near-tree']
    assert error.details['relation_id'] == 'tree-supported'


def test_parent_missing_and_cycle(world):
    world['objects'][-1]['parent_id'] = 'missing'
    rejected(world, 'scene_world_parent_missing')
    world['objects'][-1]['parent_id'] = 'near-tree'
    world['objects'][-2]['parent_id'] = 'leaf-group'
    error = rejected(world, 'scene_world_parent_cycle')
    assert 'near-tree' in error.details['object_ids']


@pytest.mark.parametrize('reverse', [False, True])
def test_parent_depth_limit_is_independent_of_object_traversal_order(world, reverse):
    world = compact(world)
    prototype = world['objects'][0]
    objects = []
    for index in range(MAX_PARENT_DEPTH + 1):
        obj = deepcopy(prototype)
        obj.update(id=f'n{index:03}', parent_id=f'n{index - 1:03}' if index else None)
        objects.append(obj)
    world['objects'] = list(reversed(objects)) if reverse else objects
    rejected(world, 'scene_world_graph_depth_exceeded')
    world['objects'] = objects[:MAX_PARENT_DEPTH]
    assert validate_world(world)['objects'][-1]['id'] == f'n{MAX_PARENT_DEPTH - 1:03}'


def test_attachment_cycle_is_rejected(world):
    world = compact(world)
    other = deepcopy(world['objects'][0]);other['id'] = 'other'
    world['objects'].append(other)
    world['relations'] = [
        dict(id='a', kind='attachment', object_id='item', anchor='base', target_object_id='other', target_anchor='base'),
        dict(id='b', kind='attachment', object_id='other', anchor='base', target_object_id='item', target_anchor='base'),
    ]
    error = rejected(world, 'scene_world_attachment_cycle')
    assert {'item', 'other'} <= set(error.details['object_ids'])


def test_occlusion_checks_declared_order_and_comparable_depth(world):
    world['objects'][-2]['depth']['value'] = 0
    error = rejected(world, 'scene_world_occlusion_depth_invalid')
    assert error.details['relation_id'] == 'tree-before-peak'
    world['objects'][-2]['depth'] = dict(kind='unknown', value=None)
    validate_world(world)
    world['layers'][-1]['order'] = -1
    rejected(world, 'scene_world_occlusion_order_invalid')


def test_scale_constraints_measure_transformed_world_axis_bounds(world):
    world = compact(world)
    other = deepcopy(world['objects'][0]);other['id'] = 'other'
    other['transform']['rotation'] = 45
    world['objects'].append(other)
    relation = dict(id='scale', kind='scale', object_id='other', reference_object_id='item',
                    metric='area', min_ratio=1.999999, max_ratio=2.000001)
    world['relations'] = [relation]
    validate_world(world)
    relation.update(metric='width', min_ratio=math.sqrt(2)-1e-6, max_ratio=math.sqrt(2)+1e-6)
    validate_world(world)
    relation.update(min_ratio=.1, max_ratio=1)
    error = rejected(world, 'scene_world_scale_mismatch')
    assert error.details['relation_id'] == 'scale'


def test_artwork_extent_is_distinct_from_viewport_and_requires_reason_for_overflow(world):
    world = compact(world)
    obj = world['objects'][0]
    obj['transform']['x'] = 1495
    rejected(world, 'scene_world_object_outside')
    obj['allow_overflow'] = True
    rejected(world, 'scene_world_text_invalid')
    obj['overflow_reason'] = 'Intentional crop at artwork edge'
    validate_world(world)
    obj['transform']['x'] = 1501
    rejected(world, 'scene_world_object_outside')
    obj['transform']['x'] = 1400
    obj.update(allow_overflow=False, overflow_reason=None)
    assert world_geometry(world)['item']['bounds']['x'] == 1400


def test_concave_support_region_accepts_inside_but_not_cutout(world):
    world = compact(world)
    world['layout']['regions'] = [dict(id='land', kind='polygon', points=[
        [0, 0], [20, 0], [20, 5], [5, 5], [5, 20], [0, 20]])]
    world['relations'] = [dict(id='standing', kind='support', object_id='item', anchor='base', region_id='land', tolerance=0)]
    world['objects'][0]['transform'].update(x=2, y=12)
    validate_world(world)
    world['objects'][0]['transform'].update(x=12, y=12)
    rejected(world, 'scene_world_support_mismatch')


@pytest.mark.parametrize('points', [
    [[0, 0], [10, 10], [0, 10], [10, 0]],
    [[0, 0], [10, 0], [10, 10], [0, 0]],
    [[0, 0], [10, 0], [20, 0]],
])
def test_invalid_polygon_regions_are_rejected(world, points):
    world = compact(world)
    world['layout']['regions'] = [dict(id='land', kind='polygon', points=points)]
    rejected(world, 'scene_world_polygon_invalid')


def test_recipe_references_are_inert_and_extra_executable_fields_rejected(world):
    world = compact(world)
    obj = world['objects'][0]
    obj['recipe_ref'] = dict(id='ink-stroke', sha256='a' * 64)
    assert validate_world(world)['objects'][0]['recipe_ref'] == obj['recipe_ref']
    obj['recipe_ref']['source'] = 'fetch("https://example.invalid")'
    rejected(world, 'scene_world_fields_invalid')
    del obj['recipe_ref']['source']
    obj['script'] = 'alert(1)'
    rejected(world, 'scene_world_fields_invalid')


@pytest.mark.parametrize('bad_id', ['__proto__', 'constructor', 'prototype', '../file', '', '<script>'])
def test_unsafe_ids_are_rejected(world, bad_id):
    world = compact(world)
    world['objects'][0]['id'] = bad_id
    rejected(world, 'scene_world_id_invalid')


def test_duplicate_identity_and_unknown_references_are_rejected(world):
    world['objects'][1]['id'] = world['objects'][0]['id']
    rejected(world, 'scene_world_id_duplicate')
    world['objects'][1]['id'] = 'main-peak'
    world['objects'][1]['layer_id'] = 'absent'
    rejected(world, 'scene_world_layer_missing')
    world['objects'][1]['layer_id'] = 'middle'
    world['layers'][0]['mask_region_id'] = 'absent'
    rejected(world, 'scene_world_mask_region_missing')


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf'), True, 10**1000, '1', None, [], {}])
def test_malformed_numbers_never_leak_python_exceptions(world, value):
    world['objects'][0]['transform']['x'] = value
    rejected(world)


@pytest.mark.parametrize('section,value', [('space', []), ('layers', {}), ('objects', None),
    ('layout', []), ('relations', {}), ('style', ['ink']), ('seed', True)])
def test_malformed_top_level_values_produce_service_errors(world, section, value):
    world[section] = value
    rejected(world)


@pytest.mark.parametrize('dimension', [0, -1, 5e-324, 1e-9])
def test_tiny_or_degenerate_local_geometry_is_rejected(world, dimension):
    world = compact(world)
    world['objects'][0]['bounds']['width'] = dimension
    rejected(world, 'scene_world_rect_invalid')


def test_parent_scale_underflow_cannot_create_zero_division_in_scale_relation(world):
    world = compact(world)
    parent = world['objects'][0]
    parent['transform'].update(x=100, y=100, scale_x=.0001, scale_y=.0001)
    child = deepcopy(parent)
    child.update(id='child', parent_id='item')
    child['transform'].update(x=0, y=0)
    grandchild = deepcopy(child)
    grandchild.update(id='grandchild', parent_id='child')
    world['objects'].extend([child, grandchild])
    rejected(world, 'scene_world_geometry_degenerate')


@pytest.mark.parametrize('section,maximum', [('objects', MAX_OBJECTS), ('layers', MAX_LAYERS),
    ('relations', MAX_RELATIONS)])
def test_collection_budgets_fail_before_processing_rows(world, section, maximum):
    world[section] = [{}] * (maximum + 1)
    rejected(world, 'scene_world_count_invalid')


@pytest.mark.parametrize('section,maximum', [('points', MAX_LAYOUT_POINTS), ('regions', MAX_REGIONS)])
def test_layout_collection_budgets(world, section, maximum):
    world['layout'][section] = [{}] * (maximum + 1)
    rejected(world, 'scene_world_count_invalid')


def test_encoded_byte_budget(world):
    world['style'] = 'x' * MAX_WORLD_BYTES
    rejected(world, 'scene_world_too_large')


def test_anchor_budget(world):
    world['objects'][0]['anchors'] = {f'a{i}': [0, 0] for i in range(MAX_ANCHORS + 1)}
    rejected(world, 'scene_world_anchors_invalid')


def test_total_point_budget_counts_objects_grid_named_points_and_regions(world):
    world = compact(world)
    prototype = world['objects'][0]
    prototype['anchors'] = {f'a{i}': [0, 0] for i in range(MAX_ANCHORS)}
    world['objects'] = []
    for index in range(MAX_OBJECTS):
        obj = deepcopy(prototype);obj['id'] = f'item{index}'
        world['objects'].append(obj)
    world['layout'].update(
        grid=dict(origin=[0, 0], spacing=[1, 1], columns=64, rows=64),
        points=[dict(id=f'p{i}', position=[0, 0]) for i in range(MAX_LAYOUT_POINTS)])
    validate_world(world)
    world['layout']['regions'] = [dict(id='one-too-many', kind='rect', bounds=dict(x=0, y=0, width=1, height=1))]
    rejected(world, 'scene_world_point_budget_exceeded')
