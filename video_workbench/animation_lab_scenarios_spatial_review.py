"""Independent bounded geometry and clock oracle for native spatial scenes.

The capture coordinator binds screenshot bytes and installed renderer sources.
This verifier checks measured Three.js matrices, actual material clipping,
render calls and a GPU pixel probe. It cannot establish aesthetic quality or
validate any engineering/physics claims about the illustrative models.
"""
from __future__ import annotations

import math
import re

from agent_service.contracts import ServiceError
from .animation_lab_scenarios_spatial import plan_for, spatial_state


def _require(value, code='animation_lab_spatial_native_invalid'):
    if not value:
        raise ServiceError(code, 422)


def _number(value):
    _require(type(value) in (int, float) and math.isfinite(value), 'animation_lab_spatial_nonfinite')
    return float(value)


def _near(value, expected, tolerance=2e-5):
    _require(abs(_number(value)-expected) <= tolerance, 'animation_lab_spatial_geometry_mismatch')


def _vector(value, expected, tolerance=2e-5):
    _require(type(value) is list and len(value) == len(expected), 'animation_lab_spatial_geometry_missing')
    for actual, wanted in zip(value, expected):
        _near(actual, wanted, tolerance)


def _dot(a, b):
    return sum(x*y for x,y in zip(a,b))


def _cross(a, b):
    return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]


def _normalize(a):
    length = math.sqrt(_dot(a,a))
    return [v/length for v in a]


def camera_oracle(zoom=1):
    position, target = [9,7,11], [0,.3,0]
    z = _normalize([a-b for a,b in zip(position,target)])
    x = _normalize(_cross([0,1,0],z))
    y = _cross(z,x)
    world = [*x,0,*y,0,*z,0,*position,1]
    inverse = [x[0],y[0],z[0],0,x[1],y[1],z[1],0,x[2],y[2],z[2],0,
               -_dot(x,position),-_dot(y,position),-_dot(z,position),1]
    f = zoom/math.tan(math.radians(40)/2)
    projection = [f/(880/500),0,0,0,0,f,0,0,0,0,(100+.1)/(.1-100),-1,0,0,2*100*.1/(.1-100),0]
    return dict(position=position,target=target,basis=[x,y,z],matrix_world=world,
                matrix_world_inverse=inverse,projection_matrix=projection)


def projected_oracle(position, zoom=1):
    camera = camera_oracle(zoom)
    relative = [a-b for a,b in zip(position,camera['position'])]
    cx,cy,cz = [_dot(basis,relative) for basis in camera['basis']]
    f = zoom/math.tan(math.radians(40)/2)
    ndc = [(f/(880/500))*cx/-cz, f*cy/-cz,
           (((100+.1)/(.1-100))*cz+2*100*.1/(.1-100))/-cz]
    return ndc, [24+(ndc[0]+1)*440,100+(1-ndc[1])*250]


def translation_matrix(position):
    return [1,0,0,0,0,1,0,0,0,0,1,0,*position,1]


def part_bounds(part):
    shape = part['shape']
    if shape in {'box','plate'}:
        half = [part['width']/2,part['height']/2,part['depth']/2]
    elif shape == 'cylinder':
        points = [i*2*math.pi/part['segments'] for i in range(part['segments'])]
        half = [part['radius']*max(math.sin(a) for a in points),part['height']/2,
                part['radius']*max(math.cos(a) for a in points)]
    elif shape == 'ring':
        half = [part['radius']+part['tube'],part['tube'],part['radius']+part['tube']]
    else:
        half = [part['radius'],part['height']/2,part['radius']*math.sqrt(3)/2]
    return [-v for v in half], half


def _geometry(part, measured):
    types = dict(box='BoxGeometry',cylinder='CylinderGeometry',ring='TorusGeometry',
                 plate='ExtrudeGeometry',nut='ExtrudeGeometry')
    _require(type(measured) is dict and measured.get('type') == types[part['shape']]
             and measured.get('position_item_size') == 3, 'animation_lab_spatial_geometry_missing')
    count, indices = measured.get('position_count'), measured.get('index_count')
    _require(type(count) is int and count >= 24 and type(indices) is int and indices >= 0,
             'animation_lab_spatial_geometry_missing')
    bounds = measured.get('bounds',{})
    lo,hi = part_bounds(part)
    _vector(bounds.get('min'),lo)
    _vector(bounds.get('max'),hi)
    params = measured.get('parameters',{})
    _require(type(params) is dict, 'animation_lab_spatial_geometry_missing')
    if part['shape'] == 'box':
        _require(count == 24 and indices == 36, 'animation_lab_spatial_geometry_missing')
        for name in ('width','height','depth'):
            _near(params.get(name),part[name])
    elif part['shape'] == 'cylinder':
        _require(indices >= part['segments']*12 and count >= part['segments']*2,
                 'animation_lab_spatial_geometry_missing')
        _near(params.get('radiusTop'),part['radius'])
        _near(params.get('radiusBottom'),part['radius'])
        _near(params.get('height'),part['height'])
        _require(params.get('radialSegments') == part['segments'], 'animation_lab_spatial_geometry_missing')
    elif part['shape'] == 'ring':
        _near(params.get('radius'),part['radius'])
        _near(params.get('tube'),part['tube'])
        _require(count == 297 and indices == 1536, 'animation_lab_spatial_geometry_missing')
    else:
        _near(params.get('depth'),part['height'])
        _require(measured.get('shape_holes') == 1 and count >= 100 and indices == 0,
                 'animation_lab_spatial_hole_missing')
        radii = measured.get('hole_radii')
        _require(type(radii) is list and len(radii) == 1 and type(radii[0]) is list and len(radii[0]) == 1,
                 'animation_lab_spatial_hole_missing')
        _vector(radii[0][0],[part['hole'],part['hole']])
    return 1


def verify_native_observation(plan, time, variant, state):
    """Check actual native state; generic DOM/seek/frame checks run separately."""
    _require(type(plan) is dict and plan == plan_for(plan.get('scenario_id'))
             and variant in {'baseline','candidate'}, 'animation_lab_spatial_plan_invalid')
    _require(type(state) is dict and state.get('engine') == 'three'
             and state.get('source') == 'actual_three_scene_and_webgl', 'animation_lab_spatial_native_missing')
    expected = spatial_state(plan,time)
    _near(state.get('time'),expected['time'],1e-6)
    renderer = state.get('renderer',{})
    _require(renderer.get('is_webgl_renderer') is True and renderer.get('local_clipping_enabled') is True
             and renderer.get('context_lost') is False and renderer.get('width') == 880
             and renderer.get('height') == 500, 'animation_lab_spatial_renderer_invalid')
    zoom = 1+1.2*expected['assembly_metric'] if plan['scenario_id'] == 'spatial-bolt-assembly' else 1
    camera, wanted_camera = state.get('camera',{}), camera_oracle(zoom)
    _near(camera.get('zoom',1),zoom)
    _require(camera.get('type') == 'PerspectiveCamera', 'animation_lab_spatial_camera_invalid')
    for key in ('position','target','matrix_world','matrix_world_inverse','projection_matrix'):
        _vector(camera.get(key),wanted_camera[key])
    for key,value in dict(fov=40,aspect=880/500,near=.1,far=100).items():
        _near(camera.get(key),value)
    rectangle = state.get('canvas_rect',{})
    for key,value in dict(x=24,y=100,width=880,height=500).items():
        _near(rectangle.get(key),value,.05)
    parts = state.get('parts')
    _require(type(parts) is list and len(parts) == len(plan['data']['parts'])
             and all(type(p) is dict and type(p.get('id')) is str for p in parts)
             and len({p['id'] for p in parts}) == len(parts), 'animation_lab_spatial_parts_missing')
    actual = {p['id']:p for p in parts}
    _require(set(actual) == {p['id'] for p in plan['data']['parts']}, 'animation_lab_spatial_parts_missing')
    normal = [0,0,0]
    normal[plan['data']['axis']] = -1
    facts = 0
    for index,part in enumerate(plan['data']['parts']):
        row = actual[part['id']]
        _require(row.get('is_mesh') is True and row.get('visible') is True, 'animation_lab_spatial_parts_missing')
        position = expected['parts'][part['id']]
        _vector(row.get('position'),position)
        _vector(row.get('rotation'),[0,0,0])
        _vector(row.get('scale'),[1,1,1])
        _vector(row.get('matrix_world'),translation_matrix(position))
        facts += _geometry(part,row.get('geometry'))
        material = row.get('material',{})
        _require(material.get('type') == 'MeshStandardMaterial' and material.get('side') == 2,
                 'animation_lab_spatial_clipping_missing')
        _near(material.get('opacity'),1)
        planes = material.get('clipping_planes')
        _require(type(planes) is list and len(planes) == 1, 'animation_lab_spatial_clipping_missing')
        _vector(planes[0].get('normal'),normal)
        _near(planes[0].get('constant'),expected['constant'])
        ndc,pixel = projected_oracle(position,zoom)
        projection = row.get('projection',{})
        _vector(projection.get('ndc'),ndc)
        _vector(projection.get('pixel'),pixel,.05)
        _require(all(-.95 <= v <= .95 for v in ndc[:2]), 'animation_lab_spatial_projection_out_of_bounds')
        marker = row.get('marker',{})
        _require(marker.get('text') == str(index+1), 'animation_lab_spatial_label_mismatch')
        _near(marker.get('font_size'),14)
        marker_opacity = (.15 if position[plan['data']['axis']] > expected['constant'] else .92) if part['id'] == expected['focus'] else 0
        _near(marker.get('opacity'),marker_opacity)
        marker_bounds = marker.get('bounds',{})
        for key,value in dict(x=pixel[0]-11,y=pixel[1]-11,width=22,height=22).items():
            _near(marker_bounds.get(key),value,.06)
        facts += 7
    caps = state.get('caps')
    cap_parts = [p for p in plan['data']['parts'] if p['shape'] == 'box']
    _require(type(caps) is list and len(caps) == len(cap_parts)
             and all(type(p) is dict and type(p.get('id')) is str for p in caps)
             and len({p['id'] for p in caps}) == len(caps), 'animation_lab_spatial_caps_missing')
    cap_map = {p['id']:p for p in caps}
    _require(set(cap_map) == {p['id'] for p in cap_parts}, 'animation_lab_spatial_caps_missing')
    cap_count = 0
    for part in cap_parts:
        cap = cap_map[part['id']]
        axis = plan['data']['axis']
        position = expected['parts'][part['id']][:]
        half = (part['width'] if axis == 0 else part['depth'])/2
        visible = position[axis]-half+.001 < expected['constant'] < position[axis]+half-.001
        _require(cap.get('visible') is visible, 'animation_lab_spatial_caps_missing')
        position[axis] = expected['constant']-.002
        _vector(cap.get('position'),position)
        matrix = ([0,0,-1,0,0,1,0,0,1,0,0,0,*position,1] if axis == 0 else translation_matrix(position))
        _vector(cap.get('matrix_world'),matrix)
        geometry = cap.get('geometry',{})
        _require(geometry.get('type') == 'PlaneGeometry' and geometry.get('position_count') == 4
                 and geometry.get('index_count') == 6, 'animation_lab_spatial_caps_missing')
        width = part['depth'] if axis == 0 else part['width']
        _near(geometry.get('parameters',{}).get('width'),width)
        _near(geometry.get('parameters',{}).get('height'),part['height'])
        cap_count += visible
        facts += 3
    _require(type(renderer.get('calls')) is int and renderer['calls'] == len(parts)+cap_count
             and type(renderer.get('triangles')) is int and renderer['triangles'] >= len(parts)*12,
             'animation_lab_spatial_render_calls_missing')
    probe = state.get('pixel_probe',{})
    _require(probe.get('source') == 'webgl_readPixels' and probe.get('sample_count') == 1100
             and type(probe.get('foreground_samples')) is int and 25 <= probe['foreground_samples'] <= 1100
             and type(probe.get('unique_colors')) is int and 6 <= probe['unique_colors'] <= 1100
             and type(probe.get('checksum')) is str and re.fullmatch('[0-9a-f]{8}',probe['checksum'])
             and probe.get('gl_error') == 0, 'animation_lab_spatial_pixels_missing')
    return facts+8
