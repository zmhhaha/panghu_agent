"""Actual browser observations for the independent continuity compiler."""
from __future__ import annotations

from copy import deepcopy
import math
import re

from agent_service.contracts import ServiceError
from .continuity import CONTINUITY_COMPILER_VERSION, TEXT_COORDINATE_EPSILON
from .motion import FPS, HEIGHT, WIDTH, action_windows, sha256


def _require(value, code='continuity_observation_invalid'):
    if not value:
        raise ServiceError(code, 422)


def _number(value):
    _require(type(value) in (int,float) and math.isfinite(value))
    return value


def _rgb(value):
    if re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        return tuple(int(value[index:index+2],16) for index in (1,3,5))
    match = re.fullmatch(r'rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*[\d.]+)?\)', value)
    _require(match is not None)
    return tuple(map(int,match.groups()))


def expected_states(segment, time):
    elements = deepcopy(segment['initial_states'])
    relations = deepcopy(segment['relations'])
    headers = {'previous':dict(opacity=1.0 if segment['index'] else 0.0),
               'current':dict(opacity=0.0 if segment['index'] else 1.0)}
    details = {identity:dict(marginTop=item['detail_margin']) for identity,item in elements.items()}
    scopes = {'element':elements,'relation':relations,'header':headers,'detail':details}
    plates = {identity:dict(opacity=item['labelOpacity']) for identity,item in relations.items()}
    markers = {identity:dict(opacity=item['markerOpacity']) for identity,item in relations.items()}
    scopes.update(plate=plates,marker=markers)
    old_details = {identity:dict(opacity=1.0) for identity in segment['detail_updates']}
    new_details = {identity:dict(opacity=0.0) for identity in segment['detail_updates']}
    detail_boxes = {identity:dict(progress=0.0) for identity in segment['detail_updates']}
    scopes.update(detail_old=old_details,detail_new=new_details,detail_box=detail_boxes)
    for event in segment['timeline']['actions']:
        if time < event['start']:
            continue
        fraction = min(1,max(0,(time-event['start'])/event['duration'])) if event['duration'] else 1
        eased = 4*fraction**3 if fraction < .5 else 1-(-2*fraction+2)**3/2
        target = scopes[event['scope']][event['target']]
        for key, end in event['after'].items():
            start = event['before'][key]
            if type(start) in (int,float):
                target[key] = start+(end-start)*eased
            else:
                target[key] = tuple(a+(b-a)*eased for a,b in zip(_rgb(start),_rgb(end)))
    for identity,item in relations.items():
        item['labelOpacity'],item['markerOpacity'] = plates[identity]['opacity'],markers[identity]['opacity']
    updates = {}
    for identity,update in segment['detail_updates'].items():
        old_opacity,new_opacity = old_details[identity]['opacity'],new_details[identity]['opacity']
        elements[identity]['detail'] = update['before'] if old_opacity > .02 else update['after'] if new_opacity > .02 else ''
        updates[identity] = dict(before_opacity=old_opacity,after_opacity=new_opacity,progress=detail_boxes[identity]['progress'])
    return dict(elements=elements,relations=relations,headers=headers,detail_updates=updates)


def _bounds(state):
    _require(type(state) is dict and state.get('overflow') is False, 'motion_text_overflow')
    x,y,width,height = (_number(state.get(key)) for key in ('x','y','width','height'))
    _require(-1 <= x and -1 <= y and width >= 0 and height >= 0
             and x+width <= WIDTH+1 and y+height <= HEIGHT+1, 'motion_layout_outside_canvas')


def _text_bounds(state, kind):
    """DOMRect may lose a few float32 bits; integer scroll overflow stays exact."""
    boxes,area = state.get('text_boxes'),state.get('text_area')
    required = {'copy','label','detail'} if kind == 'card' else {'copy','label'}
    _require(type(boxes) is dict and set(boxes) == required and type(area) is dict
             and set(area) == {'left','top','right','bottom'}, 'motion_text_bounds_missing')
    left,top,right,bottom = (_number(area.get(key)) for key in ('left','top','right','bottom'))
    _require(state['x'] <= left < right <= state['x']+state['width']
             and state['y'] <= top < bottom <= state['y']+state['height'], 'motion_text_overflow')
    epsilon = TEXT_COORDINATE_EPSILON
    for box in boxes.values():
        _require(type(box) is dict and box.get('overflow') is False, 'motion_text_overflow')
        x,y,width,height = (_number(box.get(key)) for key in ('x','y','width','height'))
        _require(width >= 0 and height >= 0 and x >= left-epsilon and y >= top-epsilon
                 and x+width <= right+epsilon and y+height <= bottom+epsilon, 'motion_text_overflow')
        for scroll,client in (('scrollWidth','clientWidth'),('scrollHeight','clientHeight')):
            _require(0 <= _number(box.get(scroll)) <= _number(box.get(client)), 'motion_text_overflow')


def _sample(sample, time, segment):
    _require(type(sample) is dict and abs(_number(sample.get('time'))-time) < 1e-5
             and abs(_number(sample.get('actual_time'))-time) < 1e-5, 'motion_seek_time_mismatch')
    _require(type(sample.get('frame_sha256')) is str and re.fullmatch(r'[0-9a-f]{64}',sample['frame_sha256']))
    expected = expected_states(segment,time)
    actual, relations, layout = sample.get('elements'),sample.get('relations'),sample.get('layout')
    _require(type(actual) is dict and set(actual) == set(expected['elements'])
             and type(relations) is dict and set(relations) == set(expected['relations']),
             'motion_observation_objects_missing')
    for identity,item in expected['elements'].items():
        state = actual[identity]
        _bounds(state)
        _text_bounds(state,item['kind'])
        _require(state.get('label') == item['label'] and state.get('detail') == item['detail'],
                 'continuity_object_text_changed')
        for key in ('x','y','width','height'):
            _require(abs(_number(state.get(key))-_number(item[key])) < 1, 'continuity_object_pose_mismatch')
        _require(abs(_number(state.get('opacity'))-item['opacity']) <= .01, 'continuity_object_opacity_mismatch')
        width = _number(state.get('highlightWidth'))
        _require((width == 0 if item['outlineWidth'] == 0 else abs(width-item['outlineWidth']) <= 1.01),
                 'motion_expected_highlight_missing')
        for key in ('borderColor','backgroundColor'):
            wanted = item[key] if type(item[key]) is tuple else _rgb(item[key])
            _require(max(abs(a-b) for a,b in zip(_rgb(state.get(key,'')),wanted)) <= 1.1,
                     'continuity_object_style_mismatch')
        if identity in segment['detail_updates']:
            update,wanted = segment['detail_updates'][identity],expected['detail_updates'][identity]
            layers,box = state.get('detailLayers'),state.get('detailBox')
            _require(type(layers) is dict and set(layers) == {'before','after'} and type(box) is dict,
                     'continuity_detail_observation_missing')
            old_height,new_height = _number(box.get('oldHeight')),_number(box.get('newHeight'))
            _require(0 <= old_height <= HEIGHT and 0 <= new_height <= HEIGHT)
            _require(abs(_number(box.get('height'))-(old_height+(new_height-old_height)*wanted['progress'])) < 1
                     and abs(_number(box.get('marginTop'))-(update['before_margin']+
                         (update['after_margin']-update['before_margin'])*wanted['progress'])) < .02,
                     'continuity_detail_resize_mismatch')
            for key in ('before','after'):
                layer = layers[key]
                _require(type(layer) is dict and layer.get('text') == update[key]
                         and abs(_number(layer.get('opacity'))-wanted[key+'_opacity']) <= .01,
                         'continuity_detail_content_mismatch')
                if item['kind'] == 'card' and layer['opacity']*state['opacity'] > .02:
                    area = state['text_area']
                    epsilon = TEXT_COORDINATE_EPSILON
                    _require(layer['x'] >= area['left']-epsilon and layer['y'] >= area['top']-epsilon
                             and layer['x']+layer['width'] <= area['right']+epsilon
                             and layer['y']+layer['height'] <= area['bottom']+epsilon
                             and layer['y'] >= box['y']-epsilon
                             and layer['y']+layer['height'] <= box['y']+box['height']+epsilon
                             and layer['scrollWidth'] <= layer['clientWidth']
                             and layer['scrollHeight'] <= layer['clientHeight'], 'motion_text_overflow')
    # A deliberate copy begins over its source. That one lineage pair may
    # overlap during the bounded transition; unrelated visible objects may not.
    exceptions = {frozenset((row['source'],row['target'])) for row in segment['copy_lineage']} if time <= .6 else set()
    visible = [(identity,state) for identity,state in actual.items() if state['opacity'] > .02]
    for index,(identity,a) in enumerate(visible):
        for other,b in visible[index+1:]:
            intersect = a['x'] < b['x']+b['width']-1e-6 and b['x'] < a['x']+a['width']-1e-6
            intersect = intersect and a['y'] < b['y']+b['height']-1e-6 and b['y'] < a['y']+a['height']-1e-6
            _require(not intersect or frozenset((identity,other)) in exceptions, 'continuity_object_overlap')
    _require(type(layout) is dict and layout.get('fontReady') is True, 'motion_fonts_not_ready')
    for key in ('title','takeaway'):
        _bounds(layout.get(key))
    headers = layout.get('headers')
    _require(type(headers) is dict and set(headers) == {'previous','current'})
    for key,wanted in expected['headers'].items():
        header = headers[key]
        _require(abs(_number(header.get('opacity'))-wanted['opacity']) <= .01, 'continuity_header_reset')
        _bounds(header.get('title'))
        _bounds(header.get('takeaway'))
        checkpoint = segment['initial_checkpoint'] if key == 'previous' else segment['final_checkpoint']
        _require(header['title'].get('text') == checkpoint['title']
                 and header['takeaway'].get('text') == checkpoint['takeaway'], 'continuity_header_reset')
    labels = layout.get('relations')
    _require(type(labels) is list and len(labels) == len(relations)
             and {row.get('id') for row in labels} == set(relations))
    for label in labels:
        _bounds(label)
        state,wanted = relations[label['id']],expected['relations'][label['id']]
        _require(_number(state.get('length')) > 0)
        for key in ('opacity','strokeDashoffset','labelOpacity','markerOpacity'):
            _require(abs(_number(state.get(key))-wanted[key]) <= .011, 'continuity_relation_state_mismatch')
        plate = label.get('plate')
        _require(type(plate) is dict and set(plate) == {'x','y','width','height'})
        if label.get('text'):
            ink = label.get('ink')
            _require(type(ink) is dict and ink['x'] >= plate['x']-1e-6 and ink['y'] >= plate['y']-1e-6
                     and ink['x']+ink['width'] <= plate['x']+plate['width']+1e-6
                     and ink['y']+ink['height'] <= plate['y']+plate['height']+1e-6, 'motion_text_overflow')
        else:
            _require(label.get('ink') is None)
        if wanted['labelOpacity'] > .02:
            _require(label.get('overlapsNodes') == [] and label.get('overlapsLabels') == [], 'motion_relation_overlap')
    return expected


def _checkpoint_sample(sample, checkpoint):
    visible = {identity:state for identity,state in sample['elements'].items() if state['opacity'] > .02}
    _require(set(visible) == set(checkpoint['objects']), 'continuity_checkpoint_mismatch')
    for identity,wanted in checkpoint['objects'].items():
        state = visible[identity]
        for key in ('x','y','width','height'):
            _require(abs(state[key]-wanted[key]) < 1, 'continuity_checkpoint_mismatch')
        _require(abs(state['opacity']-wanted['opacity']) <= .01
                 and state['label'] == wanted['label'] and state['detail'] == wanted['detail'],
                 'continuity_checkpoint_mismatch')
    visible_relations = {identity:state for identity,state in sample['relations'].items() if state['opacity'] > .02}
    _require(set(visible_relations) == set(checkpoint['relations']), 'continuity_checkpoint_mismatch')


def verify_continuity_observations(plan_document, timeline, observations):
    _require(type(plan_document) is dict and plan_document.get('schema') == 'video.continuity-scene.v1')
    segment = plan_document['segment']
    expected_timeline = dict(segment['timeline'],plan_sha256=sha256(plan_document))
    _require(timeline == expected_timeline and timeline.get('compiler') == CONTINUITY_COMPILER_VERSION,
             'continuity_timeline_mismatch')
    _require(type(observations) is dict and observations.get('schema') == 'video.motion-observations.v1'
             and observations.get('seek_protocol') == 'hyperframes-core-renderSeek-exact'
             and observations.get('render_fps') == FPS
             and observations.get('timeline_sha256') == sha256(timeline))
    rows = observations.get('windows')
    _require(type(rows) is list and len(rows) == len(timeline['actions']))
    by_ordinal = {row.get('ordinal'):row for row in rows if type(row) is dict}
    _require(len(by_ordinal) == len(rows))
    for action,times in zip(timeline['actions'],action_windows(timeline)):
        row = by_ordinal.get(action['ordinal'])
        _require(type(row) is dict)
        for key in ('before','middle','after'):
            _sample(row.get(key),times[key],segment)
        if action['kind'] != 'hold':
            _require(len({row[key]['frame_sha256'] for key in ('before','middle','after')}) >= 2,
                     'motion_frozen_frames')
        if action['scope'] == 'element' and action['reason'] == 'scene_action':
            if set(action['after']) == {'x','y'}:
                _require(all(row[key]['elements'][action['target']]['opacity'] >= .98
                             for key in ('before','middle','after')), 'motion_invisible_move')
            if action['before'].get('outlineWidth') == 0 and action['after'].get('outlineWidth') == 8:
                following = by_ordinal.get(action['ordinal']+1)
                _require(type(following) is dict and max(
                    sample[key]['elements'][action['target']]['opacity'] * sample[key]['elements'][action['target']]['highlightWidth']
                    for sample in (row,following) for key in ('before','middle','after')) >= 2,
                    'motion_invisible_highlight')
    boundary = observations.get('continuity_boundary')
    _require(type(boundary) is dict, 'continuity_boundary_observation_missing')
    _sample(boundary.get('initial'),0,segment)
    _sample(boundary.get('final'),timeline['duration_seconds']-1/FPS,segment)
    _sample(boundary.get('repeated_initial'),0,segment)
    _checkpoint_sample(boundary['initial'],segment['initial_checkpoint'])
    _checkpoint_sample(boundary['final'],segment['final_checkpoint'])
    if segment['index']:
        sample = boundary.get('transition_middle')
        _sample(sample,.375,segment)
        _sample(boundary.get('transition_end'),.6,segment)
        for lineage in segment['copy_lineage']:
            _require(all(sample['elements'][identity]['opacity'] >= .98
                         for identity in (lineage['source'],lineage['target'])), 'continuity_copy_source_missing')
    reverse = observations.get('reverse_seek')
    _require(type(reverse) is dict and reverse.get('repeat_state_equal') is True
             and reverse.get('first_sha256') == boundary['initial']['frame_sha256']
             and reverse.get('repeated_first_sha256') == boundary['repeated_initial']['frame_sha256']
             and reverse['first_sha256'] == reverse['repeated_first_sha256']
             and reverse.get('last_sha256') == boundary['final']['frame_sha256'], 'motion_reverse_seek_failed')
    return dict(schema='video.motion-review.v1',technical_status='passed',
        checks=[dict(ordinal=row['ordinal'],target=row['target'],kind=row['kind'],passed=True) for row in timeline['actions']],
        timeline_sha256=sha256(timeline),timing_review_status=timeline['review_status'],
        visual_comprehension_status='pending',voice_review_status='pending',
        continuity=dict(scene_id=segment['scene_id'],initial_checkpoint_sha256=sha256(segment['initial_checkpoint']),
                        final_checkpoint_sha256=sha256(segment['final_checkpoint']),
                        initial_frame_sha256=boundary['initial']['frame_sha256'],final_frame_sha256=boundary['final']['frame_sha256'],
                        boundary_status='passed',copy_lineage=deepcopy(segment['copy_lineage'])))
