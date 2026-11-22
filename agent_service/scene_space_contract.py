"""Bounded inert scene worlds and spatial consistency, independent of rendering.

Coordinates use x-right/y-down scene units; transforms rotate in degrees.
``space.bounds`` is available artwork, not the output viewport. Depth is
declared independently from image Y. This module executes no recipes or source.
Scale relations measure world-axis bounding boxes (including area), so rotating
an object can change its measured projected size without changing local size.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re

from .contracts import ServiceError, canonical_bytes, digest

WORLD_SCHEMA = 'video.scene-world.v1'
MAX_WORLD_BYTES = 2 * 1024 * 1024
MAX_OBJECTS = 256
MAX_LAYERS = 32
MAX_RELATIONS = 1024
MAX_REGIONS = 128
MAX_LAYOUT_POINTS = 4096
MAX_REGION_POINTS = 256
MAX_ANCHORS = 32
MAX_TOTAL_POINTS = 16384
MAX_PARENT_DEPTH = 32
MAX_COORDINATE = 1_000_000
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_RESERVED = frozenset({'constructor', 'prototype', '__proto__'})
_EPS = 1e-7


class SceneWorldError(ServiceError):
    """Stable error code with bounded object/relation locations for repair."""
    def __init__(self, code, path, *, object_ids=(), relation_id=None):
        super().__init__(code, 422)
        self.details = dict(path=path[:240], object_ids=list(object_ids)[:8])
        if relation_id is not None:
            self.details['relation_id'] = relation_id


def _require(ok, code, path, **details):
    if not ok:
        raise SceneWorldError(code, path, **details)


def _fields(value, required, optional, path):
    _require(type(value) is dict and set(required) <= set(value)
             and set(value) <= set(required) | set(optional), 'scene_world_fields_invalid', path)


def _id(value, path):
    _require(type(value) is str and _ID.fullmatch(value) is not None
             and value not in _RESERVED, 'scene_world_id_invalid', path)
    return value


def _number(value, path, low=-MAX_COORDINATE, high=MAX_COORDINATE):
    _require(type(value) in (int, float) and low <= value <= high and math.isfinite(value),
             'scene_world_number_invalid', path)
    return value


def _integer(value, path, low, high):
    _require(type(value) is int and low <= value <= high, 'scene_world_integer_invalid', path)
    return value


def _text(value, path, maximum=500):
    _require(type(value) is str and bool(value.strip()) and len(value) <= maximum
             and not any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value),
             'scene_world_text_invalid', path)
    return value


def _point(value, path):
    _require(type(value) is list and len(value) == 2, 'scene_world_point_invalid', path)
    return [_number(n, path + '/' + str(i)) for i, n in enumerate(value)]


def _rect(value, path):
    _fields(value, ('x', 'y', 'width', 'height'), (), path)
    result = {k: _number(value[k], path + '/' + k) for k in ('x', 'y', 'width', 'height')}
    _require(result['width'] >= 1e-6 and result['height'] >= 1e-6, 'scene_world_rect_invalid', path)
    _number(result['x'] + result['width'], path + '/right')
    _number(result['y'] + result['height'], path + '/bottom')
    return result


def _inside(point, rect, tolerance=_EPS):
    return (rect['x'] - tolerance <= point[0] <= rect['x'] + rect['width'] + tolerance
            and rect['y'] - tolerance <= point[1] <= rect['y'] + rect['height'] + tolerance)


def _depth(value, path):
    _fields(value, ('kind', 'value'), (), path)
    _require(type(value['kind']) is str and value['kind'] in {'ordinal', 'geometric', 'unknown'},
             'scene_world_depth_invalid', path)
    if value['kind'] == 'unknown':
        _require(value['value'] is None, 'scene_world_depth_invalid', path + '/value')
    else:
        _number(value['value'], path + '/value')
    return deepcopy(value)


def _array(value, path, maximum, minimum=0):
    _require(type(value) is list and minimum <= len(value) <= maximum,
             'scene_world_count_invalid', path)
    return value


def _unique(rows, path):
    seen = set()
    for i, row in enumerate(rows):
        identity = row['id']
        _require(identity not in seen, 'scene_world_id_duplicate', path + '/' + str(i) + '/id')
        seen.add(identity)


def _acyclic(edges, path, code):
    state = {}
    depths = {}
    def visit(node, trail):
        _require(state.get(node) != 1, code, path, object_ids=trail[-7:] + [node])
        if state.get(node) == 2:
            return depths[node]
        state[node] = 1
        depth = 1
        for child in sorted(edges.get(node, ())):
            depth = max(depth, 1 + visit(child, trail + [node]))
        _require(depth <= MAX_PARENT_DEPTH, 'scene_world_graph_depth_exceeded', path,
                 object_ids=trail[-7:] + [node])
        state[node] = 2
        depths[node] = depth
        return depth
    for node in sorted(edges):
        visit(node, [])


def _matrix(transform):
    angle = math.radians(transform['rotation'])
    cosine, sine = math.cos(angle), math.sin(angle)
    return [cosine * transform['scale_x'], sine * transform['scale_x'],
            -sine * transform['scale_y'], cosine * transform['scale_y'],
            transform['x'], transform['y']]


def _multiply(left, right):
    a, b, c, d, e, f = left
    g, h, i, j, k, l = right
    return [a*g+c*h, b*g+d*h, a*i+c*j, b*i+d*j, a*k+c*l+e, b*k+d*l+f]


def _apply(matrix, point):
    a, b, c, d, e, f = matrix
    return [a * point[0] + c * point[1] + e, b * point[0] + d * point[1] + f]


def _corners(rect):
    x, y, w, h = (rect[k] for k in ('x', 'y', 'width', 'height'))
    return [[x, y], [x+w, y], [x+w, y+h], [x, y+h]]


def _geometry(world):
    objects = {row['id']: row for row in world['objects']}
    result = {}
    def resolve(identity):
        if identity in result:
            return result[identity]
        obj = objects[identity]
        matrix = _matrix(obj['transform'])
        if obj['parent_id'] is not None:
            matrix = _multiply(resolve(obj['parent_id'])['matrix'], matrix)
        for i, n in enumerate(matrix):
            _number(n, 'objects/' + identity + '/world_matrix/' + str(i), -1e9, 1e9)
        corners = [_apply(matrix, point) for point in _corners(obj['bounds'])]
        for i, point in enumerate(corners):
            _point(point, 'objects/' + identity + '/world_corners/' + str(i))
        xs, ys = [p[0] for p in corners], [p[1] for p in corners]
        width, height = max(xs)-min(xs), max(ys)-min(ys)
        _require(width >= _EPS and height >= _EPS, 'scene_world_geometry_degenerate',
                 'objects/' + identity + '/world_bounds', object_ids=[identity])
        result[identity] = dict(matrix=matrix,
            bounds=dict(x=min(xs), y=min(ys), width=width, height=height),
            corners=corners, anchors={key: _apply(matrix, point) for key, point in obj['anchors'].items()},
            depth=deepcopy(obj['depth']), layer_id=obj['layer_id'], order=obj['order'],
            parent_id=obj['parent_id'], allow_overflow=obj['allow_overflow'])
        return result[identity]
    for identity in objects:
        resolve(identity)
    return result


def _distance_to_segment(point, start, end):
    dx, dy = end[0]-start[0], end[1]-start[1]
    square = dx*dx + dy*dy
    if square == 0:
        return math.dist(point, start)
    t = max(0, min(1, ((point[0]-start[0])*dx + (point[1]-start[1])*dy) / square))
    return math.dist(point, [start[0]+t*dx, start[1]+t*dy])


def _cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def _segments_intersect(a, b, c, d):
    products = (_cross(a,b,c), _cross(a,b,d), _cross(c,d,a), _cross(c,d,b))
    if products[0]*products[1] < -_EPS and products[2]*products[3] < -_EPS:
        return True
    return any(abs(cross) <= _EPS and _distance_to_segment(point,start,end) <= _EPS
               for cross,point,start,end in ((products[0],c,a,b),(products[1],d,a,b),
                                             (products[2],a,c,d),(products[3],b,c,d)))


def _in_region(point, region, tolerance):
    if region['kind'] == 'rect':
        return _inside(point, region['bounds'], tolerance)
    points = region['points']
    if any(_distance_to_segment(point, a, b) <= tolerance + _EPS
           for a,b in zip(points, points[1:] + points[:1])):
        return True
    inside = False
    x, y = point
    for a,b in zip(points, points[1:] + points[:1]):
        if (a[1] > y) != (b[1] > y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1]) + a[0]:
            inside = not inside
    return inside


def validate_world(value):
    """Validate and normalize a world without mutating input or evaluating source.

    Unknown depth stays unknown. Larger comparable depth means nearer; region
    geometry and object transforms stay explicit and do not infer depth from Y.
    ``SceneWorldError.details`` locates the first structural/spatial failure.
    """
    _fields(value, ('schema','id','revision','seed','style','space','layers','objects','layout','relations'), (), '')
    try:
        _require(len(canonical_bytes(value)) <= MAX_WORLD_BYTES, 'scene_world_too_large', '')
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise SceneWorldError('scene_world_json_invalid', '') from None
    world = deepcopy(value)
    _require(world['schema'] == WORLD_SCHEMA, 'scene_world_schema_invalid', 'schema')
    _id(world['id'], 'id');_integer(world['revision'], 'revision', 1, 1_000_000)
    _integer(world['seed'], 'seed', 0, 2**32-1);_text(world['style'], 'style', 120)
    _fields(world['space'], ('projection','bounds'), (), 'space')
    _require(world['space']['projection'] in ('planar','scattered','geometric'),
             'scene_world_projection_invalid', 'space/projection')
    available = _rect(world['space']['bounds'], 'space/bounds')
    layers = _array(world['layers'], 'layers', MAX_LAYERS, 1)
    for i, layer in enumerate(layers):
        path='layers/'+str(i)
        _fields(layer, ('id','order','parallax'), ('opacity','blur','cache_policy','mask_region_id'), path)
        _id(layer['id'], path+'/id');_integer(layer['order'], path+'/order', -4096, 4096)
        _number(layer['parallax'], path+'/parallax', 0, 2)
        _number(layer.setdefault('opacity', 1), path+'/opacity', 0, 1)
        _number(layer.setdefault('blur', 0), path+'/blur', 0, 128)
        _require(layer.setdefault('cache_policy', 'static') in ('static','dynamic','none'),
                 'scene_world_cache_policy_invalid', path+'/cache_policy')
        mask=layer.setdefault('mask_region_id', None)
        if mask is not None:_id(mask, path+'/mask_region_id')
    _unique(layers, 'layers')
    _require(len({r['order']for r in layers})==len(layers), 'scene_world_layer_order_duplicate', 'layers')
    layer_map={r['id']:r for r in layers}
    objects=_array(world['objects'], 'objects', MAX_OBJECTS, 1)
    point_count=0
    for i,obj in enumerate(objects):
        path='objects/'+str(i)
        _fields(obj, ('id','kind','layer_id','parent_id','bounds','transform','depth','anchors'),
                ('order','allow_overflow','overflow_reason','recipe_ref'), path)
        _id(obj['id'], path+'/id');_id(obj['kind'], path+'/kind');_id(obj['layer_id'], path+'/layer_id')
        _require(obj['layer_id'] in layer_map, 'scene_world_layer_missing', path+'/layer_id', object_ids=[obj['id']])
        if obj['parent_id'] is not None:_id(obj['parent_id'], path+'/parent_id')
        rect=_rect(obj['bounds'], path+'/bounds')
        transform=obj['transform'];_fields(transform, ('x','y','scale_x','scale_y','rotation'), (), path+'/transform')
        for key in ('x','y'):_number(transform[key], path+'/transform/'+key)
        for key in ('scale_x','scale_y'):_number(transform[key], path+'/transform/'+key, .0001, 1000)
        _number(transform['rotation'], path+'/transform/rotation', -360, 360)
        _depth(obj['depth'], path+'/depth')
        _require(type(obj['anchors']) is dict and len(obj['anchors'])<=MAX_ANCHORS,
                 'scene_world_anchors_invalid', path+'/anchors')
        for key,point in obj['anchors'].items():
            _id(key, path+'/anchors');_point(point, path+'/anchors/'+key)
            _require(_inside(point,rect), 'scene_world_anchor_outside_object', path+'/anchors/'+key, object_ids=[obj['id']])
        point_count+=len(obj['anchors'])
        _integer(obj.setdefault('order',0), path+'/order', -4096,4096)
        _require(type(obj.setdefault('allow_overflow',False))is bool, 'scene_world_overflow_invalid', path+'/allow_overflow')
        reason=obj.setdefault('overflow_reason',None)
        if obj['allow_overflow']:_text(reason,path+'/overflow_reason')
        else:_require(reason is None, 'scene_world_overflow_invalid', path+'/overflow_reason')
        recipe=obj.setdefault('recipe_ref',None)
        if recipe is not None:
            _fields(recipe, ('id','sha256'), (), path+'/recipe_ref');_id(recipe['id'],path+'/recipe_ref/id')
            _require(type(recipe['sha256'])is str and _SHA.fullmatch(recipe['sha256']),
                     'scene_world_recipe_reference_invalid', path+'/recipe_ref/sha256')
    _unique(objects,'objects');object_map={r['id']:r for r in objects}
    parents={r['id']:set() for r in objects}
    for i,obj in enumerate(objects):
        parent=obj['parent_id']
        if parent is not None:
            _require(parent in object_map, 'scene_world_parent_missing','objects/'+str(i)+'/parent_id',object_ids=[obj['id']])
            parents[obj['id']].add(parent)
    _acyclic(parents,'objects','scene_world_parent_cycle')
    layout=world['layout'];_fields(layout,('grid','points','regions'),(), 'layout')
    grid=layout['grid']
    if grid is not None:
        _fields(grid,('origin','spacing','columns','rows'),(), 'layout/grid')
        origin=_point(grid['origin'],'layout/grid/origin');spacing=_point(grid['spacing'],'layout/grid/spacing')
        _require(all(n>0 for n in spacing),'scene_world_grid_invalid','layout/grid/spacing')
        for key in ('columns','rows'):_integer(grid[key],'layout/grid/'+key,1,128)
        count=grid['columns']*grid['rows'];point_count+=count
        _require(count<=MAX_LAYOUT_POINTS,'scene_world_grid_budget_exceeded','layout/grid')
        last=[origin[0]+spacing[0]*(grid['columns']-1),origin[1]+spacing[1]*(grid['rows']-1)]
        _require(_inside(origin,available)and _inside(last,available),'scene_world_grid_outside','layout/grid')
    points=_array(layout['points'],'layout/points',MAX_LAYOUT_POINTS)
    for i,row in enumerate(points):
        path='layout/points/'+str(i);_fields(row,('id','position'),('depth',),path)
        _id(row['id'],path+'/id');_point(row['position'],path+'/position')
        _require(_inside(row['position'],available),'scene_world_layout_point_outside',path+'/position')
        _depth(row.setdefault('depth',dict(kind='unknown',value=None)),path+'/depth')
    _unique(points,'layout/points');point_count+=len(points)
    _require(point_count<=MAX_TOTAL_POINTS,'scene_world_point_budget_exceeded','layout')
    regions=_array(layout['regions'],'layout/regions',MAX_REGIONS)
    for i,row in enumerate(regions):
        path='layout/regions/'+str(i)
        _require(type(row)is dict,'scene_world_fields_invalid',path)
        kind=row.get('kind');_require(kind in ('rect','polygon'),'scene_world_region_invalid',path+'/kind')
        _fields(row,('id','kind','bounds')if kind=='rect'else('id','kind','points'),(),path)
        _id(row['id'],path+'/id')
        if kind=='rect':
            rect=_rect(row['bounds'],path+'/bounds');vertices=_corners(rect)
            point_count+=len(vertices)
            _require(point_count<=MAX_TOTAL_POINTS,'scene_world_point_budget_exceeded',path)
        else:
            vertices=_array(row['points'],path+'/points',MAX_REGION_POINTS,3)
            point_count+=len(vertices)
            _require(point_count<=MAX_TOTAL_POINTS,'scene_world_point_budget_exceeded',path)
            for j,point in enumerate(vertices):_point(point,path+'/points/'+str(j))
            _require(len({tuple(p)for p in vertices})==len(vertices),'scene_world_polygon_invalid',path)
            area=sum(a[0]*b[1]-a[1]*b[0]for a,b in zip(vertices,vertices[1:]+vertices[:1]))
            _require(abs(area)>_EPS,'scene_world_polygon_invalid',path)
            n=len(vertices)
            for a in range(n):
                for b in range(a+1,n):
                    if b==a+1 or(a==0 and b==n-1):continue
                    _require(not _segments_intersect(vertices[a],vertices[(a+1)%n],vertices[b],vertices[(b+1)%n]),
                             'scene_world_polygon_invalid',path)
        _require(all(_inside(p,available)for p in vertices),'scene_world_region_outside',path)
    _unique(regions,'layout/regions');region_map={r['id']:r for r in regions}
    _require(point_count<=MAX_TOTAL_POINTS,'scene_world_point_budget_exceeded','layout')
    for i,layer in enumerate(layers):
        _require(layer['mask_region_id']is None or layer['mask_region_id']in region_map,
                 'scene_world_mask_region_missing','layers/'+str(i)+'/mask_region_id')
    geometry=_geometry(world)
    for identity,item in geometry.items():
        if item['allow_overflow']:
            box=item['bounds'];left=max(box['x'],available['x']);top=max(box['y'],available['y'])
            right=min(box['x']+box['width'],available['x']+available['width'])
            bottom=min(box['y']+box['height'],available['y']+available['height'])
            _require(right>left and bottom>top,'scene_world_object_outside','objects/'+identity,object_ids=[identity])
        else:
            _require(all(_inside(p,available)for p in item['corners']),
                     'scene_world_object_outside','objects/'+identity,object_ids=[identity])
    relations=_array(world['relations'],'relations',MAX_RELATIONS)
    occlusion={};attachments={key:set(values)for key,values in parents.items()}
    anchor_claims=set()
    for i,row in enumerate(relations):
        path='relations/'+str(i);_require(type(row)is dict,'scene_world_fields_invalid',path)
        kind=row.get('kind');_require(kind in ('attachment','support','scale','occlusion'),
             'scene_world_relation_invalid',path+'/kind')
        required={'attachment':('id','kind','object_id','anchor','target_object_id','target_anchor'),
            'support':('id','kind','object_id','anchor','region_id'),
            'scale':('id','kind','object_id','reference_object_id','metric','min_ratio','max_ratio'),
            'occlusion':('id','kind','front_id','back_id')}[kind]
        _fields(row,required,('tolerance',)if kind in ('attachment','support')else(),path)
        _id(row['id'],path+'/id')
        names=[row['front_id'],row['back_id']]if kind=='occlusion'else[row['object_id']]
        if kind=='attachment':names.append(row['target_object_id'])
        if kind=='scale':names.append(row['reference_object_id'])
        for name in names:
            _id(name,path);_require(name in object_map,'scene_world_object_missing',path,object_ids=[name],relation_id=row['id'])
        if len(names)==2:
            _require(names[0]!=names[1],'scene_world_self_relation',path,object_ids=names,relation_id=row['id'])
        details=dict(object_ids=names,relation_id=row['id'])
        if kind in ('attachment','support'):
            _id(row['anchor'],path+'/anchor')
            _require(row['anchor']in geometry[names[0]]['anchors'],'scene_world_anchor_missing',path+'/anchor',**details)
            claim=(kind,names[0],row['anchor'])
            _require(claim not in anchor_claims,'scene_world_anchor_relation_duplicate',path,**details);anchor_claims.add(claim)
            tolerance=_number(row.setdefault('tolerance',min(available['width'],available['height'])*1e-6),
                path+'/tolerance',0,min(available['width'],available['height'])*.05)
            point=geometry[names[0]]['anchors'][row['anchor']]
            if kind=='attachment':
                _id(row['target_anchor'],path+'/target_anchor')
                _require(row['target_anchor']in geometry[names[1]]['anchors'],'scene_world_anchor_missing',path+'/target_anchor',**details)
                target=geometry[names[1]]['anchors'][row['target_anchor']]
                _require(math.dist(point,target)<=tolerance+_EPS,'scene_world_attachment_mismatch',path,**details)
                attachments[names[0]].add(names[1])
            else:
                _id(row['region_id'],path+'/region_id')
                _require(row['region_id']in region_map,'scene_world_region_missing',path+'/region_id',**details)
                _require(_in_region(point,region_map[row['region_id']],tolerance),'scene_world_support_mismatch',path,**details)
        elif kind=='scale':
            _require(row['metric']in ('width','height','area'),'scene_world_scale_metric_invalid',path+'/metric',**details)
            low=_number(row['min_ratio'],path+'/min_ratio',.000001,1e6)
            high=_number(row['max_ratio'],path+'/max_ratio',.000001,1e6)
            _require(low<=high,'scene_world_scale_range_invalid',path,**details)
            def measure(identity):
                rect=geometry[identity]['bounds']
                return rect['width']*rect['height']if row['metric']=='area'else rect[row['metric']]
            ratio=measure(names[0])/measure(names[1])
            _require(low-_EPS<=ratio<=high+_EPS,'scene_world_scale_mismatch',path,**details)
        else:
            front,back=(geometry[name]for name in names)
            front_order=(layer_map[front['layer_id']]['order'],front['order'])
            back_order=(layer_map[back['layer_id']]['order'],back['order'])
            _require(front_order>back_order,'scene_world_occlusion_order_invalid',path,**details)
            if front['depth']['kind']==back['depth']['kind']and front['depth']['kind']!='unknown':
                _require(front['depth']['value']>=back['depth']['value'],
                         'scene_world_occlusion_depth_invalid',path,**details)
            occlusion.setdefault(names[0],set()).add(names[1])
    _unique(relations,'relations')
    _acyclic(attachments,'relations','scene_world_attachment_cycle')
    _acyclic(occlusion,'relations','scene_world_occlusion_cycle')
    return world


def world_sha256(value):
    """Hash the validated, normalized semantic world, never a live instance."""
    return digest(validate_world(value))


def world_geometry(value):
    """Return keyed world-space affine geometry after full world validation."""
    return _geometry(validate_world(value))


def grid_point(value, column, row):
    """Resolve a zero-based grid position in scene units, without invented depth."""
    world = validate_world(value)
    grid = world['layout']['grid']
    _require(grid is not None, 'scene_world_grid_missing', 'layout/grid')
    _integer(column, 'layout/grid/column', 0, grid['columns'] - 1)
    _integer(row, 'layout/grid/row', 0, grid['rows'] - 1)
    return [grid['origin'][0] + column * grid['spacing'][0],
            grid['origin'][1] + row * grid['spacing'][1]]


def layout_point(value, identity):
    """Return a named position and declared depth as fresh inert data."""
    world = validate_world(value)
    _id(identity, 'layout/points/id')
    for point in world['layout']['points']:
        if point['id'] == identity:
            return deepcopy(point)
    raise SceneWorldError('scene_world_layout_point_missing', 'layout/points/' + identity)


__all__ = ['WORLD_SCHEMA', 'SceneWorldError', 'validate_world', 'world_sha256', 'world_geometry',
    'grid_point', 'layout_point',
    'MAX_WORLD_BYTES', 'MAX_OBJECTS', 'MAX_LAYERS', 'MAX_RELATIONS', 'MAX_REGIONS',
    'MAX_LAYOUT_POINTS', 'MAX_REGION_POINTS', 'MAX_ANCHORS', 'MAX_TOTAL_POINTS',
    'MAX_PARENT_DEPTH', 'MAX_COORDINATE']
