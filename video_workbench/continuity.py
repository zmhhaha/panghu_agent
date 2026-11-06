"""Project continuity is bounded data, separate from the frozen scene compilers.

Only reviewed links are public input. Checkpoints and numeric events below are
compiler output, rebuilt by the v3 snapshot boundary before a Job is admitted.
"""
from __future__ import annotations

from copy import deepcopy
import html
import json
import math
import re

from agent_service.contracts import ServiceError
from .motion import (FPS, GSAP_VERSION, HYPERFRAMES_VERSION, WIDTH, HEIGHT,
                     canonical, compiler_layout, compile_project, resolve_timeline,
                     semantic_digest, sha256, _safe_script_json)
from .motion_geometry import BOUNDS, connection_geometry, inflate, overlaps, path_text, segment_hits_box

CONTINUITY_COMPILER_VERSION = 'motion-continuity-compiler-2'
CONTINUITY_TEMPLATE_VERSION = 'continuous-diagrams-2'
CONTINUITY_COMPILER_TEMPLATES = {
    'motion-continuity-compiler-1': 'continuous-diagrams-1',
    CONTINUITY_COMPILER_VERSION: CONTINUITY_TEMPLATE_VERSION,
}
# Keep the historical diagram registry unchanged. New production snapshots
# explicitly select this separate process contract; old snapshots never do.
PROCESS_CONTINUITY_COMPILER_VERSION = 'motion-process-continuity-compiler-1'
ALL_CONTINUITY_COMPILER_TEMPLATES = {
    **CONTINUITY_COMPILER_TEMPLATES,
    PROCESS_CONTINUITY_COMPILER_VERSION: 'continuous-process-1',
}
TRANSITION_SECONDS = .6
TEXT_COORDINATE_EPSILON = .001
SCHEMA = 'video.continuity-plan.v1'
_LINK_FIELDS = {'from_scene_id', 'from_element_id', 'to_scene_id', 'to_element_id', 'mode'}
_POSE_KEYS = ('x', 'y', 'width', 'height', 'paddingTop', 'paddingBottom', 'opacity',
              'borderColor', 'backgroundColor', 'outlineWidth')


def _require(value, code='invalid_continuity_plan'):
    if not value:
        raise ServiceError(code, 422)


def _identity(scene_id, element_id):
    return 'ce_' + sha256(canonical([scene_id, element_id]))[:24]


def _scene_rows(scenes):
    _require(type(scenes) is list and 1 <= len(scenes) <= 24)
    result, seen = [], set()
    for scene in scenes:
        _require(type(scene) is dict and type(scene.get('id')) is str and 0 < len(scene['id']) <= 100
                 and scene['id'] not in seen and type(scene.get('motion')) is dict)
        plan = scene['motion'].get('plan')
        _require(not (type(plan) is dict and plan.get('schema') == 'video.process-plan.v1')
                 and not (type(scene['motion'].get('timeline')) is dict
                          and str(scene['motion']['timeline'].get('compiler', '')).startswith('motion-process-compiler-')),
                 'process_continuity_not_supported')
        _require(type(plan) is dict and plan.get('template') in {'flow', 'compare', 'focus'}
                 and type(plan.get('elements')) is list and 1 <= len(plan['elements']) <= 9)
        elements = {row['id']: row for row in plan['elements']}
        _require(len(elements) == len(plan['elements']))
        seen.add(scene['id'])
        result.append((scene, elements))
    return result


def _appearance(row):
    return row['kind'], row['label']


def _has_process(scenes):
    return type(scenes) is list and any(
        type(scene) is dict and type(scene.get('motion')) is dict
        and type(scene['motion'].get('plan')) is dict
        and scene['motion']['plan'].get('schema') == 'video.process-plan.v1'
        for scene in scenes)


def build_continuity_plan(scenes):
    """Suggest exact kind/label matches; changed details still need user review."""
    if _has_process(scenes):
        from .process_continuity import build_process_continuity_plan
        return build_process_continuity_plan(scenes)
    rows, links = _scene_rows(scenes), []
    for (before, old), (after, new) in zip(rows, rows[1:]):
        for target in new.values():
            matches = [source for source in old.values() if _appearance(source) == _appearance(target)]
            reverse = [item for item in new.values() if _appearance(item) == _appearance(target)]
            if len(matches) == len(reverse) == 1:
                links.append(dict(from_scene_id=before['id'], from_element_id=matches[0]['id'],
                                  to_scene_id=after['id'], to_element_id=target['id'], mode='retain'))
    return dict(schema=SCHEMA, links=links)


def validate_continuity_plan(plan, scenes):
    if _has_process(scenes):
        from .process_continuity import validate_process_continuity_plan
        return validate_process_continuity_plan(plan, scenes)
    rows = _scene_rows(scenes)
    _require(type(plan) is dict and set(plan) == {'schema', 'links'} and plan['schema'] == SCHEMA
             and type(plan['links']) is list and len(plan['links']) <= 23 * 9)
    adjacent = {(before[0]['id'], after[0]['id']): (before[1], after[1])
                for before, after in zip(rows, rows[1:])}
    sources, targets = set(), set()
    for link in plan['links']:
        _require(type(link) is dict and set(link) == _LINK_FIELDS
                 and all(type(value) is str for value in link.values()) and link['mode'] in {'retain', 'copy'})
        pair = link['from_scene_id'], link['to_scene_id']
        _require(pair in adjacent)
        before, after = adjacent[pair]
        source, target = link['from_element_id'], link['to_element_id']
        _require(source in before and target in after and _appearance(before[source]) == _appearance(after[target]))
        _require(link['mode'] != 'copy' or before[source]['detail'] == after[target]['detail'],
                 'continuity_copy_content_changed')
        old_key, new_key = (*pair, source), (*pair, target)
        _require(old_key not in sources and new_key not in targets, 'continuity_ambiguous_link')
        sources.add(old_key)
        targets.add(new_key)
    return deepcopy(plan)


def _base_timing(scene, segment=None):
    value = ((segment or {}).get('base_timing') or scene.get('continuity_base_timing') or scene.get('timing'))
    _require(type(value) is dict and all(type(value.get(key)) in (int, float)
             and math.isfinite(value[key]) and value[key] >= 0
             for key in ('leading_seconds', 'trailing_seconds')), 'continuity_source_invalid')
    return {key: value[key] for key in ('leading_seconds', 'trailing_seconds')}


def _source_binding(scene, base):
    motion, audio = scene['motion'], scene['audio']
    return sha256(dict(scene_id=scene['id'], title=scene.get('title', ''), motion_id=motion['id'],
                       audio_id=audio['id'], audio_sha256=audio['sha256'], plan_sha256=sha256(motion['plan']),
                       beats_sha256=sha256(motion['beats']), alignment_sha256=sha256(motion['alignment']),
                       base_timing=base))


def _object(row, identity, box, *, copied_from=None):
    x, y, width, height = box
    pad = 0 if row['kind'] == 'label' else 6 if row['kind'] == 'token' else {'top': 7, 'note': 8}.get(row['slot'], 14)
    border = '#6de1d2' if row['kind'] == 'token' else '#33445e'
    background = '#174c4c' if row['kind'] == 'token' else '#16243a'
    if row['kind'] == 'label':
        border = background = 'rgba(0,0,0,0)'
    return dict(id=identity, kind=row['kind'], label=row['label'], detail=row['detail'], slot=row['slot'],
                copied_from=copied_from, x=x, y=y, width=width, height=height, paddingTop=pad,
                paddingBottom=pad, opacity=1.0, borderColor=border, backgroundColor=background,
                outlineWidth=0.0, detail_margin=4 if row['slot'] == 'note' else 6)


def _geometry(plan, layout):
    boxes = {row['id']: layout[row['slot']] for row in plan['elements']}
    reserved = [(72, 74, 1136, 112)]
    if not any(row['slot'] == 'caption' for row in plan['elements']):
        reserved.append((72, 636, 1136, 72))
    sweeps, positions = [], dict(boxes)
    for action in plan['actions']:
        if action['kind'] == 'move':
            a, b = positions[action['target']], layout[action['to_slot']]
            left, top = min(a[0], b[0]), min(a[1], b[1])
            sweeps.append((left, top, max(a[0]+a[2], b[0]+b[2])-left, max(a[1]+a[3], b[1]+b[3])-top))
            positions[action['target']] = b
    try:
        return boxes, connection_geometry(plan['relations'], boxes, reserved, sweeps)
    except ValueError:
        raise ServiceError('motion_relation_layout_unavailable', 422) from None


def _archive(source, layout, current, geometry, reserved):
    candidates = []
    for slot, (x, y, width, height) in layout.items():
        kind = 'token' if slot.startswith('token_') else 'label' if slot == 'caption' else 'card'
        if kind != source['kind']:
            continue
        box = (x + (width-source['width'])/2, y + (height-source['height'])/2, source['width'], source['height'])
        if not (BOUNDS[0] <= box[0] and BOUNDS[1] <= box[1]
                and box[0]+box[2] <= BOUNDS[2] and box[1]+box[3] <= BOUNDS[3]):
            continue
        obstacles = [*(tuple(item[key] for key in ('x','y','width','height')) for item in current.values()),
                     *(item['box'] for item in geometry.values()), *reserved,
                     (72, 74, 1136, 112), (72, 636, 1136, 72)]
        if any(overlaps(box, inflate(other, 8)) for other in obstacles):
            continue
        if any(segment_hits_box(a,b,inflate(box,4)) for item in geometry.values()
               for a,b in zip(item['points'],item['points'][1:])):
            continue
        candidates.append((abs(box[0]-source['x'])+abs(box[1]-source['y']), slot, box))
    _require(candidates, 'continuity_copy_archive_unavailable')
    _, slot, box = min(candidates)
    result = deepcopy(source)
    result.update(x=box[0], y=box[1], slot=slot)
    return result


def _checkpoint(objects, relations, title, takeaway):
    return dict(schema='video.continuity-checkpoint.v1',
                objects={key: deepcopy(value) for key, value in sorted(objects.items()) if value['opacity'] > .001},
                relations={key: deepcopy(value) for key, value in sorted(relations.items()) if value['opacity'] > .001},
                title=title, takeaway=takeaway)


def _safe_transition(initial, final, copies):
    allowed = {frozenset((row['source'],row['target'])) for row in copies}
    sweeps = {}
    for identity,destination in final.items():
        source = initial[identity]
        if not source['opacity'] and not destination['opacity']:
            continue
        left,top = min(source['x'],destination['x']),min(source['y'],destination['y'])
        sweeps[identity] = (left,top,max(source['x']+source['width'],destination['x']+destination['width'])-left,
                           max(source['y']+source['height'],destination['y']+destination['height'])-top)
    for index,(identity,box) in enumerate(sweeps.items()):
        for other,other_box in list(sweeps.items())[index+1:]:
            _require(not overlaps(box,other_box) or frozenset((identity,other)) in allowed,
                     'continuity_unsafe_transition')


def _event(events, scope, target, start, duration, before, after, *, reason='scene_action'):
    if before == after:
        reason = 'retained_already_visible' if reason == 'scene_action' else reason
    events.append(dict(ordinal=len(events), scope=scope, target=target, beat_id=None, kind='hold' if before == after else 'tween',
                       start=round(start, 6), duration=round(duration, 6), before=deepcopy(before),
                       after=deepcopy(after), reason=reason))


def resolve_continuity(scenes, plan, *, compiler_version=None):
    if compiler_version == PROCESS_CONTINUITY_COMPILER_VERSION or (
            compiler_version is None and _has_process(scenes)):
        from .process_continuity import resolve_process_continuity
        return resolve_process_continuity(scenes, plan,
            compiler_version=PROCESS_CONTINUITY_COMPILER_VERSION)
    compiler_version = CONTINUITY_COMPILER_VERSION if compiler_version is None else compiler_version
    _require(type(compiler_version) is str and compiler_version in CONTINUITY_COMPILER_TEMPLATES,
             'continuity_compiler_version_unsupported')
    # An explicitly frozen ordinary compiler must retain its original schema
    # boundary even after the public planner gains process support.
    _scene_rows(scenes)
    plan = validate_continuity_plan(plan, scenes)
    segments, previous, previous_map, frame = [], None, {}, 0
    for index, scene in enumerate(scenes):
        base_plan, motion = scene['motion']['plan'], scene['motion']
        base, transition = _base_timing(scene), 0 if index == 0 else TRANSITION_SECONDS
        base_timeline = resolve_timeline(base_plan, motion['beats'], motion['alignment'],
            compiler_version='motion-compiler-5', leading_seconds=base['leading_seconds']+transition,
            trailing_seconds=base['trailing_seconds'])
        # A checkpoint must be a settled *encoded* last frame, not the unencoded
        # theoretical state at duration. At most a final frame of extra hold is needed.
        final_action = max(row['start']+row['duration'] for row in base_timeline['actions'])
        duration = max(base_timeline['duration_seconds'], math.ceil((final_action+1/FPS)*FPS)/FPS)
        _require(duration <= 60, 'continuity_segment_budget_exceeded')
        trailing = max(base['trailing_seconds'], duration-base_timeline['leading_seconds']-base_timeline['audio_seconds'])
        base_timeline['trailing_seconds'], base_timeline['duration_seconds'] = trailing, duration
        layout = compiler_layout(base_plan, 'motion-compiler-5')
        boxes, geometry = _geometry(base_plan, layout)
        links = [row for row in plan['links'] if row['to_scene_id'] == scene['id']]
        entity_map, incoming, copies = {}, {}, []
        for link in links:
            identity = previous_map[link['from_element_id']]
            _require(identity in previous['objects'], 'continuity_source_not_visible')
            target = identity if link['mode'] == 'retain' else _identity(scene['id'], link['to_element_id'])
            entity_map[link['to_element_id']] = target
            incoming[target] = (identity, link['mode'])
            if link['mode'] == 'copy':
                copies.append(dict(source=identity, target=target))
        for row in base_plan['elements']:
            entity_map.setdefault(row['id'], _identity(scene['id'], row['id']))
        current = {entity_map[row['id']]: _object(row, entity_map[row['id']], boxes[row['id']],
                    copied_from=incoming.get(entity_map[row['id']], (None, None))[0]
                    if incoming.get(entity_map[row['id']], (None, None))[1] == 'copy' else None)
                   for row in base_plan['elements']}
        initial = deepcopy(previous['objects']) if previous else {}
        final, events, archived, detail_updates = deepcopy(current), [], [], {}
        first_visibility = {}
        for action in base_timeline['actions']:
            if action['kind'] in {'reveal', 'hide'}:
                first_visibility.setdefault(entity_map[action['target']], action['kind'])
        for identity, obj in current.items():
            obj['opacity'] = 0.0 if first_visibility.get(identity) == 'reveal' else 1.0
            if identity in incoming:
                source, mode = incoming[identity]
                origin = deepcopy(previous['objects'][source])
                origin['id'], origin['copied_from'] = identity, source if mode == 'copy' else origin['copied_from']
                initial.setdefault(identity, dict(origin, opacity=0.0 if mode == 'copy' else 1.0))
                final[identity].update(opacity=1.0, borderColor=origin['borderColor'], backgroundColor=origin['backgroundColor'])
                if mode == 'copy':
                    destination = _archive(previous['objects'][source], layout, final, geometry, archived)
                    archived.append(tuple(destination[key] for key in ('x','y','width','height')))
                    final[source] = destination
                    _event(events, 'element', source, .15, .45,
                           {key: previous['objects'][source][key] for key in ('x','y')},
                           {key: destination[key] for key in ('x','y')}, reason='archive_copy_source')
                    _event(events, 'element', identity, .15, .1, {'opacity':0.0}, {'opacity':1.0}, reason='copy_reveal')
                if origin['detail'] != final[identity]['detail']:
                    detail_updates[identity] = dict(before=origin['detail'],after=final[identity]['detail'],
                        before_margin=origin['detail_margin'],after_margin=final[identity]['detail_margin'],
                        target_width=final[identity]['width'])
                    _event(events,'detail_old',identity,.15,.15,{'opacity':1.0},{'opacity':0.0},reason='detail_update')
                    _event(events,'detail_box',identity,.3,.15,{'progress':0.0},{'progress':1.0},reason='detail_update')
                    _event(events,'detail_new',identity,.45,.15,{'opacity':0.0},{'opacity':1.0},reason='detail_update')
                    _event(events,'element',identity,.15,.45,{key:origin[key] for key in ('x','y')},
                           {key:final[identity][key] for key in ('x','y')},reason=mode)
                    pose = ('width','height','paddingTop','paddingBottom')
                    _event(events,'element',identity,.3,.15,{key:origin[key] for key in pose},
                           {key:final[identity][key] for key in pose},reason=mode)
                else:
                    pose = ('x','y','width','height','paddingTop','paddingBottom')
                    _event(events, 'element', identity, .15, .45,
                           {key: origin[key] for key in pose}, {key: final[identity][key] for key in pose}, reason=mode)
                    _event(events, 'detail', identity, .15, .45,
                           {'marginTop':origin['detail_margin']}, {'marginTop':final[identity]['detail_margin']}, reason=mode)
            else:
                initial[identity] = dict(deepcopy(obj), opacity=0.0 if transition else obj['opacity'])
                final[identity]['opacity'] = obj['opacity']
                if transition and obj['opacity']:
                    _event(events, 'element', identity, .45, .15, {'opacity':0.0}, {'opacity':1.0}, reason='scene_default_visible')
        survivors = set(current) | {row['source'] for row in copies}
        if previous:
            _safe_transition(initial,final,copies)
        if previous:
            for identity, obj in previous['objects'].items():
                if identity not in survivors:
                    _event(events, 'element', identity, 0, .15, {'opacity':obj['opacity']}, {'opacity':0.0}, reason='retire')
        relations = deepcopy(previous['relations']) if previous else {}
        final_relations = {}
        for identity, relation in relations.items():
            _event(events, 'relation', identity, 0, .15, {'opacity':1.0}, {'opacity':0.0}, reason='retire_relation')
            _event(events, 'plate', identity, 0, .15, {'opacity':1.0}, {'opacity':0.0}, reason='retire_relation')
            _event(events, 'marker', identity, 0, .15, {'opacity':1.0}, {'opacity':0.0}, reason='retire_relation')
        relation_map = {}
        for row in base_plan['relations']:
            identity = 'cr_' + sha256(canonical([scene['id'], row['id']]))[:24]
            relation_map[row['id']] = identity
            item = dict(deepcopy(geometry[row['id']]), id=identity, label=row['label'],
                        source=entity_map[row['source']], target=entity_map[row['target']],
                        opacity=0.0, strokeDashoffset=1.0, markerOpacity=0.0, labelOpacity=0.0)
            relations[identity] = item
            final_relations[identity] = deepcopy(item)
        states = deepcopy(final)
        for action in base_timeline['actions']:
            kind, start, span = action['kind'], action['start'], action['duration']
            if kind == 'draw':
                identity = relation_map[action['target']]
                _event(events, 'relation', identity, start, span, {'opacity':0.0,'strokeDashoffset':1.0},
                       {'opacity':1.0,'strokeDashoffset':0.0})
                _event(events, 'plate', identity, start, span, {'opacity':0.0}, {'opacity':1.0})
                _event(events, 'marker', identity, start+span*.85, span*.15, {'opacity':0.0}, {'opacity':1.0})
                final_relations[identity].update(opacity=1.0, strokeDashoffset=0.0, markerOpacity=1.0, labelOpacity=1.0)
                continue
            identity = entity_map[action['target']]
            state = states[identity]
            if kind in {'reveal', 'hide'}:
                end = 1.0 if kind == 'reveal' else 0.0
                _event(events, 'element', identity, start, span, {'opacity':state['opacity']}, {'opacity':end})
                state['opacity'] = end
            elif kind == 'move':
                destination = layout[action['to_slot']]
                _event(events, 'element', identity, start, span, {key:state[key] for key in ('x','y')},
                       dict(x=destination[0],y=destination[1]))
                state.update(x=destination[0],y=destination[1],slot=action['to_slot'])
            elif kind == 'highlight':
                _event(events, 'element', identity, start, span,
                       {key:state[key] for key in ('borderColor','backgroundColor')},
                       dict(borderColor='#79e4d5',backgroundColor='#21434e'))
                _event(events, 'element', identity, start, span/2, {'outlineWidth':0.0}, {'outlineWidth':8.0})
                _event(events, 'element', identity, start+span/2, span/2, {'outlineWidth':8.0}, {'outlineWidth':0.0})
                state.update(borderColor='#79e4d5',backgroundColor='#21434e')
        if previous:
            _event(events, 'header', 'previous', 0, .15, {'opacity':1.0}, {'opacity':0.0}, reason='chapter_transition')
            _event(events, 'header', 'current', .15, .45, {'opacity':0.0}, {'opacity':1.0}, reason='chapter_transition')
        checkpoint = _checkpoint(states, final_relations, base_plan['title'], base_plan['takeaway'])
        initial_checkpoint = deepcopy(previous) if previous else _checkpoint(initial, relations, base_plan['title'], base_plan['takeaway'])
        timeline = dict(base_timeline, compiler=compiler_version, plan_sha256=sha256(plan),
                        actions=sorted(events, key=lambda row:(row['start'],row['ordinal'])))
        segment = dict(schema='video.continuity-segment.v1', compiler=compiler_version,
            scene_id=scene['id'], index=index, plan_sha256=sha256(plan), source_binding_sha256=_source_binding(scene,base),
            base_timing=base, leading_transition_seconds=transition, global_start_frame=frame,
            global_end_frame=frame+round(duration*FPS), entity_map=entity_map, copy_lineage=copies,
            detail_updates=detail_updates,
            initial_checkpoint=initial_checkpoint, final_checkpoint=checkpoint,
            initial_states=initial, relations=relations, timeline=timeline)
        segment['sha256'] = sha256(segment)
        segments.append(segment)
        previous, previous_map, frame = checkpoint, entity_map, segment['global_end_frame']
    return dict(schema='video.continuity-resolution.v1', compiler=compiler_version,
                plan_sha256=sha256(plan), duration_seconds=frame/FPS, total_frames=frame, segments=segments)


def _validate_segment(scene, segment):
    _require(type(segment) is dict and segment.get('schema') == 'video.continuity-segment.v1'
             and type(segment.get('compiler')) is str and segment['compiler'] in CONTINUITY_COMPILER_TEMPLATES
             and segment.get('scene_id') == scene['id']
             and segment.get('sha256') == sha256({key:value for key,value in segment.items() if key != 'sha256'}),
             'continuity_segment_invalid')
    _require(segment['source_binding_sha256'] == _source_binding(scene,_base_timing(scene,segment)),
             'continuity_source_stale')
    _require(len(segment['initial_states']) <= 27 and len(segment['timeline']['actions']) <= 256
             and segment['timeline']['duration_seconds'] <= 60, 'continuity_segment_budget_exceeded')
    timeline = segment['timeline']
    _require(timeline.get('schema') == 'video.motion-timeline.v1'
             and timeline.get('compiler') == segment['compiler']
             and timeline.get('audio_sha256') == scene['audio']['sha256'] == scene['motion']['alignment']['audio_sha256']
             and timeline.get('audio_seconds') == scene['motion']['alignment']['audio_seconds']
             and timeline.get('leading_seconds') == segment['base_timing']['leading_seconds']+segment['leading_transition_seconds']
             and segment['leading_transition_seconds'] == (0 if segment['index'] == 0 else TRANSITION_SECONDS),
             'continuity_segment_invalid')
    for identity, obj in sorted(segment['initial_states'].items()):
        _require(re.fullmatch(r'ce_[0-9a-f]{24}', identity) and obj['id'] == identity
                 and obj['kind'] in {'card','token','label'} and len(obj['label']) <= 28 and len(obj['detail']) <= 64,
                 'continuity_segment_invalid')
        for key in ('x','y','width','height','opacity','paddingTop','paddingBottom','outlineWidth'):
            _require(type(obj[key]) in (int,float) and math.isfinite(obj[key]), 'continuity_segment_invalid')
        _require(0 <= obj['x'] and 0 <= obj['y'] and obj['width'] > 0 and obj['height'] > 0
                 and obj['x']+obj['width'] <= WIDTH and obj['y']+obj['height'] <= HEIGHT,
                 'continuity_segment_invalid')
    ranges = {'x':(0,WIDTH),'y':(0,HEIGHT),'width':(.01,WIDTH),'height':(.01,HEIGHT),
              'paddingTop':(0,24),'paddingBottom':(0,24),'opacity':(0,1),'outlineWidth':(0,8),
              'strokeDashoffset':(0,1),'marginTop':(0,10),'progress':(0,1)}
    permitted = {'element':set(_POSE_KEYS),'relation':{'opacity','strokeDashoffset'},
                 'plate':{'opacity'},'marker':{'opacity'},'header':{'opacity'},'detail':{'marginTop'},
                 'detail_old':{'opacity'},'detail_new':{'opacity'},'detail_box':{'progress'}}
    _require(type(segment.get('detail_updates')) is dict
             and set(segment['detail_updates']) <= set(segment['initial_states']), 'continuity_segment_invalid')
    desired_details = {segment['entity_map'][row['id']]:row['detail'] for row in scene['motion']['plan']['elements']}
    for identity,update in segment['detail_updates'].items():
        _require(type(update) is dict and set(update) == {'before','after','before_margin','after_margin','target_width'}
                 and update['before'] == segment['initial_states'][identity]['detail']
                 and update['after'] == desired_details.get(identity)
                 and type(update['before']) is str and type(update['after']) is str
                 and len(update['before']) <= 64 and len(update['after']) <= 64
                 and type(update['target_width']) in (int,float) and 0 < update['target_width'] <= WIDTH
                 and all(type(update[key]) in (int,float) and 0 <= update[key] <= 10
                         for key in ('before_margin','after_margin')),
                 'continuity_segment_invalid')
    _require({event.get('ordinal') for event in timeline['actions']} == set(range(len(timeline['actions']))),
             'continuity_segment_invalid')
    for event in timeline['actions']:
        _require(set(event) == {'ordinal','scope','target','beat_id','kind','start','duration','before','after','reason'}
                 and type(event['scope']) is str and event['scope'] in permitted
                 and event['kind'] in {'hold','tween'} and event['beat_id'] is None,
                 'continuity_segment_invalid')
        scope,target = event['scope'],event['target']
        identities = ({'previous','current'} if scope == 'header' else segment['initial_states'] if scope in {'element','detail'}
                      else segment['detail_updates'] if scope in {'detail_old','detail_new','detail_box'} else segment['relations'])
        _require(type(target) is str and target in identities
                 and type(event['before']) is dict and type(event['after']) is dict
                 and set(event['before']) == set(event['after']) and set(event['before'])
                 and set(event['before']) <= permitted[scope], 'continuity_segment_invalid')
        _require(all(type(event[key]) in (int,float) and math.isfinite(event[key]) for key in ('start','duration'))
                 and 0 <= event['start'] and 0 < event['duration'] <= 10
                 and event['start']+event['duration'] <= timeline['duration_seconds']+1e-6,
                 'continuity_segment_invalid')
        for values in (event['before'],event['after']):
            for key,value in values.items():
                if key in {'borderColor','backgroundColor'}:
                    _require(type(value) is str and re.fullmatch(r'#[0-9a-f]{6}|rgba\(0,0,0,0\)',value),
                             'continuity_segment_invalid')
                else:
                    _require(type(value) in (int,float) and math.isfinite(value)
                             and ranges[key][0] <= value <= ranges[key][1], 'continuity_segment_invalid')
    for identity,item in segment['relations'].items():
        _require(type(identity) is str and re.fullmatch(r'cr_[0-9a-f]{24}',identity)
                 and item.get('id') == identity and item.get('source') in segment['initial_states']
                 and item.get('target') in segment['initial_states'] and type(item.get('label')) is str
                 and len(item['label']) <= 18 and type(item.get('lines')) is list
                 and ''.join(item['lines']) == item['label'] and len(item['lines']) <= 5,
                 'continuity_segment_invalid')
        points = item.get('points')
        _require(type(points) in (list,tuple) and 2 <= len(points) <= 128
                 and all(type(point) in (list,tuple) and len(point) == 2
                         and all(type(value) in (int,float) and math.isfinite(value) for value in point)
                         and 0 <= point[0] <= WIDTH and 0 <= point[1] <= HEIGHT for point in points)
                 and all(a[0] == b[0] or a[1] == b[1] for a,b in zip(points,points[1:]))
                 and item.get('path') == path_text(points), 'continuity_segment_invalid')


def compile_continuity_project(scene, continuity_segment, *, animated=True, runtime_fingerprint=None):
    if (type(continuity_segment) is dict
            and continuity_segment.get('compiler') == PROCESS_CONTINUITY_COMPILER_VERSION):
        from .process_continuity import compile_process_continuity_project
        return compile_process_continuity_project(scene, continuity_segment,
            animated=animated, runtime_fingerprint=runtime_fingerprint)
    _scene_rows([scene])
    segment = deepcopy(continuity_segment)
    _validate_segment(scene, segment)
    _require(animated is True, 'continuity_requires_animation')
    wrapper = dict(schema='video.continuity-scene.v1', scene_id=scene['id'], scene_title=scene.get('title',''),
                   motion_id=scene['motion']['id'], audio_id=scene['audio']['id'], audio_sha256=scene['audio']['sha256'],
                   base_timing=segment['base_timing'], base_plan=scene['motion']['plan'], segment=segment)
    return _compile(wrapper, scene['motion']['beats'], scene['motion']['alignment'], runtime_fingerprint)


def recompile_continuity_project(plan_document, beats, alignment, *, animated=True, runtime_fingerprint=None):
    if type(plan_document) is dict and plan_document.get('schema') == 'video.process-continuity-scene.v1':
        from .process_continuity import recompile_process_continuity_project
        return recompile_process_continuity_project(plan_document, beats, alignment,
            animated=animated, runtime_fingerprint=runtime_fingerprint)
    _require(type(plan_document) is dict and set(plan_document) == {'schema','scene_id','scene_title','motion_id',
        'audio_id','audio_sha256','base_timing','base_plan','segment'}
        and plan_document['schema'] == 'video.continuity-scene.v1', 'continuity_scene_invalid')
    scene = dict(id=plan_document['scene_id'],title=plan_document['scene_title'],
                 audio=dict(id=plan_document['audio_id'],sha256=plan_document['audio_sha256']),
                 motion=dict(id=plan_document['motion_id'],plan=plan_document['base_plan'],beats=beats,alignment=alignment),
                 timing=plan_document['base_timing'])
    return compile_continuity_project(scene,plan_document['segment'],animated=animated,runtime_fingerprint=runtime_fingerprint)


def _compile(wrapper, beats, alignment, runtime_fingerprint):
    # Reuse the frozen compiler's CSS and assets, never its state or page script.
    # All previous compiler outputs and admission paths remain unchanged.
    base = compile_project(wrapper['base_plan'], beats, alignment, compiler_version='motion-compiler-5',
                           runtime_fingerprint=runtime_fingerprint, **wrapper['base_timing'])
    style = base['index.html'].decode().split('<style>',1)[1].split('</style>',1)[0]
    segment, timeline = wrapper['segment'], deepcopy(wrapper['segment']['timeline'])
    timeline['plan_sha256'] = sha256(wrapper)
    payload = dict(segment=segment,timeline=timeline)
    rows = []
    for identity, obj in sorted(segment['initial_states'].items()):
        update = segment['detail_updates'].get(identity)
        detail = (f'<div class="detail-transition"><div class="element-detail detail-before">{html.escape(update["before"])}</div>'
                  f'<div class="element-detail detail-after">{html.escape(update["after"])}</div><div class="detail-empty"></div></div>' if update else
                  f'<div class="element-detail">{html.escape(obj["detail"])}</div>')
        rows.append(f'<div id="el-{identity}" class="element {obj["kind"]}" data-motion-element="{identity}">'
                    f'<div class="element-copy"><div class="element-label">{html.escape(obj["label"])}</div>'
                    +detail+'</div></div>')
    paths, plates, arrows = [], [], []
    for identity, relation in sorted(segment['relations'].items()):
        arrows.append(f'<marker id="arrow-{identity}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">'
                      f'<path id="tip-{identity}" class="arrow-tip" d="M 0 0 L 10 5 L 0 10 z" fill="#67ded0"/></marker>')
        paths.append(f'<path id="rel-{identity}" class="relation-active" d="{html.escape(relation["path"],quote=True)}" pathLength="1" marker-end="url(#arrow-{identity})"/>')
        x,y,width,height = relation['box']
        text = ''.join(f'<tspan x="{x+width/2:g}" y="{y+4+20+index*26:g}">{html.escape(line)}</tspan>'
                       for index,line in enumerate(relation['lines']))
        plates.append(f'<g id="label-{identity}" class="relation-label"><rect class="relation-plate" x="{x:g}" y="{y:g}" width="{width:g}" height="{height:g}" rx="7"/>'
                      f'<text text-anchor="middle">{text}</text></g>')
    old = segment['initial_checkpoint']
    headers = (f'<div id="header-previous"><h1>{html.escape(old["title"])}</h1><div class="takeaway">{html.escape(old["takeaway"])}</div></div>'
               f'<div id="header-current"><h1>{html.escape(wrapper["base_plan"]["title"])}</h1><div class="takeaway">{html.escape(wrapper["base_plan"]["takeaway"])}</div></div>')
    compiler_version = segment['compiler']
    source_script = _CONTINUITY_JS_V1 if compiler_version == 'motion-continuity-compiler-1' else _CONTINUITY_JS
    script = (source_script.replace('__PAYLOAD__', _safe_script_json(payload))
              .replace('__TIMELINE_SHA__',sha256(timeline))
              .replace('__TEXT_COORDINATE_EPSILON__',str(TEXT_COORDINATE_EPSILON)))
    page = ('<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=1280,height=720">'
            '<script src="assets/gsap.min.js"></script><style>'+style+_CONTINUITY_CSS+'</style></head><body>'
            f'<div id="motion-root" data-composition-id="video-motion" data-width="1280" data-height="720" data-start="0" data-duration="{timeline["duration_seconds"]}" data-fps="24">'
            '<div class="eyebrow">技术解说 · 连续图解</div>'+headers+
            '<svg id="connections" viewBox="0 0 1280 720" aria-hidden="true"><defs>'+''.join(arrows)+'</defs>'+''.join(paths+plates)+'</svg>'+
            ''.join(rows)+'</div><script>'+script+'</script></body></html>')
    files = {'index.html':page.encode(),'plan.json':canonical(wrapper),'beats.json':canonical(beats),
             'alignment.json':canonical(alignment),'timeline.json':canonical(timeline)}
    manifest = dict(schema='video.motion-project.v1',compiler=compiler_version,
        template=CONTINUITY_COMPILER_TEMPLATES[compiler_version],hyperframes=HYPERFRAMES_VERSION,gsap=GSAP_VERSION,width=WIDTH,height=HEIGHT,fps=FPS,
        animated=True,plan_sha256=sha256(wrapper),semantic_sha256=semantic_digest(beats),timeline_sha256=sha256(timeline),
        audio_sha256=alignment['audio_sha256'],runtime_fingerprint=runtime_fingerprint,
        files={name:sha256(raw) for name,raw in sorted(files.items())},
        required_assets=['assets/gsap.min.js','assets/NotoSansCJK-Regular.ttc'])
    files['manifest.json'] = canonical(manifest)
    return files


_CONTINUITY_CSS = '''
.element{left:0;top:0}.card .element-copy{display:block}.card .element-label{line-height:38px}
.card .element-detail{line-height:27px}.token{padding-left:10px;padding-right:10px}
.card{padding-left:14px;padding-right:14px}.element.label{padding:0}
.label .element-label{line-height:31px}.card-index{display:none}
#header-current,#header-previous{position:absolute;inset:0;pointer-events:none}
.detail-transition{position:relative;overflow:hidden}
.detail-transition>.element-detail{position:absolute;top:0;left:0;right:0;margin-top:0!important}
.detail-empty{position:absolute;top:0;left:0;width:100%;height:0}
.token .detail-transition,.label .detail-transition{display:none}
'''

_CONTINUITY_JS_V1 = r'''
'use strict';
(()=>{
const data=__PAYLOAD__,segment=data.segment,tl=gsap.timeline({paused:true,defaults:{ease:'power2.inOut'}});
const textCoordinateEpsilon=__TEXT_COORDINATE_EPSILON__;
const node=id=>document.getElementById('el-'+id);
const detailBoxes=Object.fromEntries(Object.keys(segment.detail_updates).map(id=>[id,{oldHeight:0,newHeight:0}]));
const updateDetailBox=id=>{const state=detailBoxes[id],update=segment.detail_updates[id],box=node(id).querySelector('.detail-transition');
 box.style.height=state.oldHeight+'px';box.style.marginTop=update.before_margin+'px';};
const targets={element:node,detail:id=>node(id).querySelector('.element-detail'),
relation:id=>document.getElementById('rel-'+id),plate:id=>document.getElementById('label-'+id),
marker:id=>document.getElementById('tip-'+id),header:id=>document.getElementById('header-'+id),
detail_old:id=>node(id).querySelector('.detail-before'),detail_new:id=>node(id).querySelector('.detail-after'),detail_box:id=>node(id).querySelector('.detail-transition')};
for(const [id,obj] of Object.entries(segment.initial_states)){
 const pose=Object.fromEntries(['x','y','width','height','paddingTop','paddingBottom','opacity','borderColor','backgroundColor','outlineWidth'].map(key=>[key,obj[key]]));
 gsap.set(node(id),pose);gsap.set(targets.detail(id),{marginTop:obj.detail_margin});
 if(segment.detail_updates[id]){gsap.set(targets.detail_old(id),{opacity:1});gsap.set(targets.detail_new(id),{opacity:0});}
}
for(const [id,item] of Object.entries(segment.relations)){
 gsap.set(targets.relation(id),{opacity:item.opacity,strokeDashoffset:item.strokeDashoffset});
 gsap.set(targets.plate(id),{opacity:item.labelOpacity});gsap.set(targets.marker(id),{opacity:item.markerOpacity});
}
gsap.set(targets.header('previous'),{opacity:segment.index?1:0});gsap.set(targets.header('current'),{opacity:segment.index?0:1});
window.__timelines=window.__timelines||{};window.__timelines['video-motion']=tl;
const metrics=el=>{
 const css=getComputedStyle(el),r=el.getBoundingClientRect(),px=key=>parseFloat(css[key])||0;
 const result={opacity:Number(css.opacity),x:r.x,y:r.y,width:r.width,height:r.height,
 borderColor:css.borderColor,backgroundColor:css.backgroundColor,highlightWidth:px('outlineWidth'),
 scrollWidth:el.scrollWidth,clientWidth:el.clientWidth,scrollHeight:el.scrollHeight,clientHeight:el.clientHeight,
 overflow:el.scrollWidth>el.clientWidth||el.scrollHeight>el.clientHeight,text:el.textContent};
 if(el.classList.contains('element')){
  const area={left:r.left+px('borderLeftWidth')+px('paddingLeft'),top:r.top+px('borderTopWidth')+px('paddingTop'),
  right:r.right-px('borderRightWidth')-px('paddingRight'),bottom:r.bottom-px('borderBottomWidth')-px('paddingBottom')};
  const boxes={};for(const [key,selector] of [['copy','.element-copy'],['label','.element-label'],['detail','.element-detail']]){
   let child=el.querySelector(selector);
   if(key==='detail'&&segment.detail_updates[el.dataset.motionElement]){
    const old=el.querySelector('.detail-before'),next=el.querySelector('.detail-after');
    child=Number(getComputedStyle(old).opacity)>.02?old:Number(getComputedStyle(next).opacity)>.02?next:el.querySelector('.detail-empty');
    if(getComputedStyle(el.querySelector('.detail-transition')).display==='none')continue;
   }
   if(!child||getComputedStyle(child).display==='none')continue;
   const c=child.getBoundingClientRect();boxes[key]={x:c.x,y:c.y,width:c.width,height:c.height,
   scrollWidth:child.scrollWidth,clientWidth:child.clientWidth,scrollHeight:child.scrollHeight,clientHeight:child.clientHeight,
   overflow:child.scrollWidth>child.clientWidth||child.scrollHeight>child.clientHeight||c.left<area.left-textCoordinateEpsilon||c.top<area.top-textCoordinateEpsilon||c.right>area.right+textCoordinateEpsilon||c.bottom>area.bottom+textCoordinateEpsilon};
  }
  result.text_area=area;result.text_boxes=boxes;result.overflow=result.overflow||Object.values(boxes).some(box=>box.overflow);
  result.label=el.querySelector('.element-label').textContent;result.detail=el.querySelector('.element-detail').textContent;
  if(segment.detail_updates[el.dataset.motionElement]){
   const id=el.dataset.motionElement,old=targets.detail_old(id),next=targets.detail_new(id),wrapper=el.querySelector('.detail-transition'),bounds=wrapper.getBoundingClientRect();
   const layer=child=>{const box=child.getBoundingClientRect();return{text:child.textContent,opacity:Number(getComputedStyle(child).opacity),
    x:box.x,y:box.y,width:box.width,height:box.height,scrollWidth:child.scrollWidth,clientWidth:child.clientWidth,
    scrollHeight:child.scrollHeight,clientHeight:child.clientHeight};};
   result.detailLayers={before:layer(old),after:layer(next)};
   result.detailBox={x:bounds.x,y:bounds.y,width:bounds.width,height:bounds.height,marginTop:parseFloat(getComputedStyle(wrapper).marginTop),...detailBoxes[id]};
   result.detail=result.detailLayers.before.opacity>.02?old.textContent:result.detailLayers.after.opacity>.02?next.textContent:'';
  }
 }
 return result;
};
const inspect=()=>Object.fromEntries(Object.keys(segment.initial_states).map(id=>[id,metrics(node(id))]));
const inspectRelations=()=>Object.fromEntries(Object.keys(segment.relations).map(id=>{
 const el=targets.relation(id);return[id,{opacity:Number(getComputedStyle(el).opacity),strokeDashoffset:parseFloat(getComputedStyle(el).strokeDashoffset),
 length:el.getTotalLength(),labelOpacity:Number(getComputedStyle(targets.plate(id)).opacity),markerOpacity:Number(getComputedStyle(targets.marker(id)).opacity),baseCount:0}];
}));
const inspectLayout=()=>({title:metrics(targets.header('current').querySelector('h1')),takeaway:metrics(targets.header('current').querySelector('.takeaway')),
 headers:Object.fromEntries(['previous','current'].map(id=>[id,{opacity:Number(getComputedStyle(targets.header(id)).opacity),title:metrics(targets.header(id).querySelector('h1')),takeaway:metrics(targets.header(id).querySelector('.takeaway'))}])),
 relations:Object.entries(segment.relations).map(([id,item])=>{
  const group=targets.plate(id),rect=group.querySelector('rect').getBBox(),text=group.querySelector('text'),ink=item.label?text.getBBox():null;
  const plate={x:rect.x,y:rect.y,width:rect.width,height:rect.height},intersects=box=>rect.x<box.x+box.width&&box.x<rect.x+rect.width&&rect.y<box.y+box.height&&box.y<rect.y+rect.height;
  return {id,text:item.label,x:rect.x,y:rect.y,width:rect.width,height:rect.height,plate,
  opacity:Number(getComputedStyle(group).opacity),ink:ink?{x:ink.x,y:ink.y,width:ink.width,height:ink.height}:null,
  overflow:!!ink&&(ink.x<rect.x-1e-6||ink.y<rect.y-1e-6||ink.x+ink.width>rect.x+rect.width+1e-6||ink.y+ink.height>rect.y+rect.height+1e-6),
  overlapsNodes:Object.keys(segment.initial_states).filter(key=>Number(getComputedStyle(node(key)).opacity)>.02&&intersects(node(key).getBoundingClientRect())),
  overlapsLabels:Object.keys(segment.relations).filter(key=>key!==id&&Number(getComputedStyle(targets.plate(key)).opacity)>.02&&intersects(targets.plate(key).querySelector('rect').getBBox()))};
 }),fontReady:document.fonts.status==='loaded'&&document.fonts.check('20px VideoNoto')});
const ready=document.fonts.ready.then(()=>{
 for(const [id,update] of Object.entries(segment.detail_updates)){
  const old=targets.detail_old(id),next=targets.detail_new(id),state=detailBoxes[id];
  state.oldHeight=old.getBoundingClientRect().height;
  const css=getComputedStyle(node(id)),contentWidth=update.target_width-(parseFloat(css.borderLeftWidth)||0)-(parseFloat(css.borderRightWidth)||0)-(parseFloat(css.paddingLeft)||0)-(parseFloat(css.paddingRight)||0);
  next.style.width=contentWidth+'px';state.newHeight=next.getBoundingClientRect().height;next.style.width='';updateDetailBox(id);
 }
 for(const event of data.timeline.actions){
  let before=event.before,after=event.after;
  if(event.scope==='detail_box'){
   const state=detailBoxes[event.target],update=segment.detail_updates[event.target];
   before={height:state.oldHeight,marginTop:update.before_margin};after={height:state.newHeight,marginTop:update.after_margin};
  }
  tl.fromTo(targets[event.scope](event.target),before,{...after,duration:event.duration,autoRound:false,immediateRender:false},event.start);
 }
 tl.set({}, {}, data.timeline.duration_seconds);tl.seek(0,false);
 return true;
});
window.__videoMotion={version:'motion-continuity-compiler-1',planSha256:data.timeline.plan_sha256,timelineSha256:'__TIMELINE_SHA__',
duration:data.timeline.duration_seconds,inspect,inspectRelations,inspectLayout,ready};
})();
'''

# Compiler 1 is immutable, including the early registration rejected by strict
# encoding. New output is admitted only after the asynchronous build finishes.
_TIMELINE_REGISTRATION = "window.__timelines=window.__timelines||{};window.__timelines['video-motion']=tl;"
_CONTINUITY_JS = (_CONTINUITY_JS_V1.replace(_TIMELINE_REGISTRATION+'\n','')
    .replace(' tl.set({}, {}, data.timeline.duration_seconds);tl.seek(0,false);\n',
             ' tl.set({}, {}, data.timeline.duration_seconds);tl.seek(0,false);\n '+_TIMELINE_REGISTRATION+'\n')
    .replace("version:'motion-continuity-compiler-1'", "version:'motion-continuity-compiler-2'"))
