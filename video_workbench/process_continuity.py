"""Independent, bounded continuity for the controlled process2 renderer.

Source process compilers and ordinary continuity compilers stay immutable.
Only reviewed identities cross a boundary; inherited states and token locations
are checked again rather than silently resetting a completed process.
"""
from __future__ import annotations

from copy import deepcopy
import html
import json
import math
import re

from agent_service.contracts import ServiceError
from agent_service.profiles.video_process_contract import STATE_TRANSITIONS, validate_process_plan
from .motion import (FPS, GSAP_VERSION, HEIGHT, HYPERFRAMES_VERSION, WIDTH, canonical,
                     semantic_digest, sha256, _safe_script_json)
from .motion_process import (STATE_PRESENTATIONS, compile_process_project, process_layout,
                             process_paths, resolve_process_timeline)


PROCESS_CONTINUITY_COMPILER_VERSION = 'motion-process-continuity-compiler-1'
PROCESS_CONTINUITY_TEMPLATE_VERSION = 'continuous-process-1'
PROCESS_CONTINUITY_COMPILER_TEMPLATES = {
    PROCESS_CONTINUITY_COMPILER_VERSION: PROCESS_CONTINUITY_TEMPLATE_VERSION,
}
SCHEMA = 'video.continuity-plan.v1'
TRANSITION_SECONDS = .6
ARCHIVE_BOXES = ((136, 164, 136, 44), (572, 164, 136, 44), (1008, 164, 136, 44))
_LINK_FIELDS = {'from_scene_id', 'from_element_id', 'to_scene_id', 'to_element_id', 'mode'}


def _require(value, code='invalid_process_continuity_plan'):
    if not value:
        raise ServiceError(code, 422)


def _rows(scenes):
    _require(type(scenes) is list and 1 <= len(scenes) <= 24)
    result, seen = [], set()
    for scene in scenes:
        _require(type(scene) is dict and type(scene.get('id')) is str
                 and 0 < len(scene['id']) <= 100 and scene['id'] not in seen)
        motion = scene.get('motion')
        _require(type(motion) is dict and type(motion.get('plan')) is dict,
                 'process_continuity_requires_process')
        plan = motion['plan']
        _require(plan.get('schema') == 'video.process-plan.v1',
                 'process_continuity_mixed_not_supported')
        version = (motion.get('timeline') or {}).get('compiler', motion.get('compiler_version'))
        _require(version in (None, 'motion-process-compiler-2'),
                 'process_continuity_compiler_unsupported')
        beats = motion.get('beats')
        if beats is None:
            beats = [dict(id=value) for value in dict.fromkeys(
                row.get('beat_id') for row in plan.get('actions', []) if type(row) is dict)]
        validate_process_plan(plan, beats)
        result.append((scene, {row['id']: row for row in plan['elements']}))
        seen.add(scene['id'])
    return result


def _ordered(scene):
    plan, beats = scene['motion']['plan'], scene['motion'].get('beats')
    ids = [row['id'] for row in beats['segments']] if beats else list(dict.fromkeys(
        row['beat_id'] for row in plan['actions']))
    order = {identity: index for index, identity in enumerate(ids)}
    return sorted(plan['actions'], key=lambda row: (order[row['beat_id']],
        {'start': 0, 'middle': 1, 'end': 2}[row['placement']], plan['actions'].index(row)))


def _initial(scene):
    elements = scene['motion']['plan']['elements']
    visible = {row['id']: int(row['kind'] == 'card') for row in elements}
    seen = set()
    for action in _ordered(scene):
        if action['kind'] in {'reveal', 'hide'} and action['target'] not in seen:
            seen.add(action['target'])
            visible[action['target']] = int(action['kind'] == 'hide')
    return {row['id']: dict(visible=visible[row['id']],
        state='idle' if row['kind'] == 'card' else None, slot=row['slot']) for row in elements}


def _semantics(rows, links):
    """Audio-independent state machine, including all inherited boundaries."""
    result, previous = [], None
    for scene, elements in rows:
        initial = _initial(scene)
        incoming = {link['to_element_id']: link for link in links
                    if link['to_scene_id'] == scene['id']}
        for identity, link in incoming.items():
            source = previous['final'][link['from_element_id']]
            _require(source['visible'], 'process_continuity_source_not_visible')
            if link['mode'] == 'retain':
                _require(source['slot'] == initial[identity]['slot'], 'process_continuity_slot_conflict')
                initial[identity] = deepcopy(source)
            else:
                initial[identity]['visible'] = 1
        states = deepcopy(initial)
        relations = {row['id']: row for row in scene['motion']['plan']['relations']}
        for action in _ordered(scene):
            kind, target = action['kind'], action['target']
            if kind in {'reveal', 'hide'}:
                states[target]['visible'] = int(kind == 'reveal')
            elif kind == 'set_state':
                old, new = states[target]['state'], action['state']
                _require(new == old or new in STATE_TRANSITIONS[old], 'process_continuity_state_conflict')
                states[target]['state'] = new
            elif kind == 'transfer':
                relation = relations[action['relation_id']]
                source_slot = 'token_' + elements[relation['source']]['slot']
                _require(states[target]['slot'] == source_slot, 'process_continuity_slot_conflict')
                states[target]['slot'] = 'token_' + elements[relation['target']]['slot']
        previous = dict(initial=initial, final=states)
        result.append(previous)
    return result


def _validate(plan, rows):
    _require(type(plan) is dict and set(plan) == {'schema', 'links'} and plan['schema'] == SCHEMA
             and type(plan['links']) is list and len(plan['links']) <= 23 * 4)
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
        _require(source in before and target in after)
        old, new = before[source], after[target]
        _require((old['kind'], old['label']) == (new['kind'], new['label']))
        if link['mode'] == 'copy':
            _require(old['kind'] == 'token', 'process_continuity_card_copy_not_supported')
            _require(old['detail'] == new['detail'], 'process_continuity_copy_content_changed')
        else:
            _require(old['kind'] != 'card' or old['slot'] == new['slot'], 'process_continuity_slot_conflict')
        old_key, new_key = (*pair, source), (*pair, target)
        _require(old_key not in sources and new_key not in targets, 'continuity_ambiguous_link')
        sources.add(old_key)
        targets.add(new_key)
    return _semantics(rows, plan['links'])


def validate_process_continuity_plan(plan, scenes):
    _validate(plan, _rows(scenes))
    return deepcopy(plan)


def build_process_continuity_plan(scenes):
    rows = _rows(scenes)
    plan = dict(schema=SCHEMA, links=[])
    for (before, old), (after, new) in zip(rows, rows[1:]):
        for target in new.values():
            same = lambda row: (row['kind'], row['label']) == (target['kind'], target['label'])
            matches, reverse = [row for row in old.values() if same(row)], [row for row in new.values() if same(row)]
            if len(matches) != 1 or len(reverse) != 1:
                continue
            link = dict(from_scene_id=before['id'], from_element_id=matches[0]['id'],
                        to_scene_id=after['id'], to_element_id=target['id'], mode='retain')
            for mode in ('retain', 'copy') if target['kind'] == 'token' else ('retain',):
                link['mode'] = mode
                candidate = dict(schema=SCHEMA, links=[*plan['links'], deepcopy(link)])
                try:
                    _validate(candidate, rows)
                except ServiceError:
                    continue
                plan = candidate
                break
    return plan


def _summary(scene, semantics, standalone):
    elements = []
    for row in scene['motion']['plan']['elements']:
        identity = row['id']
        item = dict(deepcopy(row), initial_visible=bool(semantics['initial'][identity]['visible']),
                    final_visible=bool(semantics['final'][identity]['visible']))
        key = 'state' if row['kind'] == 'card' else 'slot'
        for prefix in ('initial', 'final'):
            item[prefix + '_' + key] = semantics[prefix][identity][key]
            item['standalone_' + prefix + '_' + key] = standalone[prefix][identity][key]
        elements.append(item)
    return dict(scene_id=scene['id'], title=scene.get('title', ''), diagram_kind='process', elements=elements)


def process_scene_summary(scene):
    rows = _rows([scene])
    state = _semantics(rows, [])[0]
    return _summary(scene, state, state)


def process_continuity_summaries(scenes, plan):
    rows = _rows(scenes)
    states = _validate(plan, rows)
    return [_summary(scene, state, _semantics([(scene, elements)], [])[0])
            for (scene, elements), state in zip(rows, states)]


def _identity(scene, element):
    return 'ce_' + sha256(canonical([scene, element]))[:24]


def _base_timing(scene, segment=None):
    value = (segment or {}).get('base_timing') or scene.get('continuity_base_timing') or scene.get('timing')
    _require(type(value) is dict and all(type(value.get(key)) in (int, float)
             and math.isfinite(value[key]) and 0 <= value[key] <= 10
             for key in ('leading_seconds', 'trailing_seconds')), 'continuity_source_invalid')
    return {key: value[key] for key in ('leading_seconds', 'trailing_seconds')}


def _source_binding(scene, base):
    motion, audio = scene['motion'], scene['audio']
    return sha256(dict(scene_id=scene['id'], title=scene.get('title', ''), motion_id=motion['id'],
        audio_id=audio['id'], audio_sha256=audio['sha256'], plan_sha256=sha256(motion['plan']),
        beats_sha256=sha256(motion['beats']), alignment_sha256=sha256(motion['alignment']), base_timing=base))


def _object(row, identity, semantic):
    x, y, width, height = process_layout()[semantic['slot']]
    return dict(id=identity, kind=row['kind'], label=row['label'], detail=row['detail'],
        slot=semantic['slot'], x=x, y=y, width=width, height=height, opacity=float(semantic['visible']),
        state=semantic['state'], copied_from=None)


def _checkpoint(objects, relations, title, takeaway):
    return dict(schema='video.process-continuity-checkpoint.v1',
        objects={key: deepcopy(value) for key, value in sorted(objects.items()) if value['opacity'] > .001},
        relations={key: deepcopy(value) for key, value in sorted(relations.items()) if value['opacity'] > .001},
        title=title, takeaway=takeaway)


def _event(events, scope, target, start, duration, before, after, *, process_kind='',
           reason='scene_action', beat_id=None, relation_id=None, points=None):
    kind = 'hold' if before == after else 'state' if scope in {'state', 'detail'} else 'tween'
    events.append(dict(ordinal=len(events), scope=scope, target=target, beat_id=beat_id, kind=kind,
        start=round(start, 6), duration=round(duration, 6), before=deepcopy(before), after=deepcopy(after),
        reason=reason, process_kind=process_kind, relation_id=relation_id, points=deepcopy(points or [])))


def _center(obj):
    return [obj['x'] + obj['width'] / 2, obj['y'] + obj['height'] / 2]


def _archive_path(origin, destination):
    cx, cy = _center(origin)
    gap = min((422, 858), key=lambda x: abs(cx - x))
    end = _center(destination)
    return [[cx, cy], [gap, cy], [gap, 238], [end[0], 238], end]


def _boundary_path(origin, destination):
    start, end = _center(origin), _center(destination)
    if start == end:
        return [start, end]
    return [start, [start[0], 598], [end[0], 598], end]


def _clear_path(points, width=136, height=44, obstacles=()):
    from .motion_process import process_text_areas
    forbidden = [box for regions in process_text_areas().values() for box in regions.values()]
    forbidden += [(64, 44, 1152, 94), (64, 664, 1152, 48), *obstacles]
    for a, b in zip(points, points[1:]):
        left, top = min(a[0], b[0]) - width / 2, min(a[1], b[1]) - height / 2
        right, bottom = max(a[0], b[0]) + width / 2, max(a[1], b[1]) + height / 2
        _require(0 <= left < right <= WIDTH and 0 <= top < bottom <= HEIGHT,
                 'process_continuity_unsafe_transition')
        _require(not any(left < x+w and x < right and top < y+h and y < bottom
                         for x, y, w, h in forbidden), 'process_continuity_unsafe_transition')


def resolve_process_continuity(scenes, plan, *, compiler_version=PROCESS_CONTINUITY_COMPILER_VERSION):
    _require(compiler_version == PROCESS_CONTINUITY_COMPILER_VERSION, 'process_continuity_compiler_unsupported')
    rows = _rows(scenes)
    semantics = _validate(plan, rows)
    previous, previous_map, segments, frame = None, {}, [], 0
    for index, ((scene, elements), semantic) in enumerate(zip(rows, semantics)):
        motion, source_plan = scene['motion'], scene['motion']['plan']
        base, transition = _base_timing(scene), TRANSITION_SECONDS if index else 0
        source = resolve_process_timeline(source_plan, motion['beats'], motion['alignment'],
            compiler_version='motion-process-compiler-2', leading_seconds=base['leading_seconds'] + transition,
            trailing_seconds=base['trailing_seconds'])
        _require(source['duration_seconds'] <= 60, 'continuity_segment_budget_exceeded')
        links = [row for row in plan['links'] if row['to_scene_id'] == scene['id']]
        incoming, entity_map, copies = {}, {}, []
        for link in links:
            origin = previous_map[link['from_element_id']]
            identity = origin if link['mode'] == 'retain' else _identity(scene['id'], link['to_element_id'])
            entity_map[link['to_element_id']] = identity
            incoming[identity] = (origin, link['mode'])
            if link['mode'] == 'copy':
                copies.append(dict(source=origin, target=identity))
        for row in elements.values():
            entity_map.setdefault(row['id'], _identity(scene['id'], row['id']))
        desired = {entity_map[key]: _object(row, entity_map[key], semantic['initial'][key])
                   for key, row in elements.items()}
        initial = deepcopy(previous['objects']) if previous else {}
        events, detail_updates = [], {}
        if previous:
            desired.update({identity: deepcopy(obj) for identity, obj in previous['objects'].items()
                            if obj['slot'].startswith('archive_')})
        for identity, obj in list(desired.items()):
            if obj['slot'].startswith('archive_'):
                continue
            if identity in incoming:
                origin, mode = incoming[identity]
                original = deepcopy(previous['objects'][origin])
                _require(original['opacity'] == 1, 'process_continuity_source_not_visible')
                if mode == 'retain':
                    initial[identity] = original
                    obj['copied_from'] = original['copied_from']
                    if original['detail'] != obj['detail']:
                        detail_updates[identity] = dict(before=original['detail'], after=obj['detail'])
                        _event(events, 'detail', identity, .3, .15,
                               {'detail': original['detail']}, {'detail': obj['detail']}, reason='detail_update')
                else:
                    _require(len(copies) == 1, 'process_continuity_copy_archive_unavailable')
                    occupied = {row['slot'] for row in desired.values()}
                    available = [i for i in range(len(ARCHIVE_BOXES)) if 'archive_' + str(i) not in occupied]
                    _require(available, 'process_continuity_copy_archive_unavailable')
                    archive_index = available[0]
                    archive_box = ARCHIVE_BOXES[archive_index]
                    archive = dict(original, x=archive_box[0], y=archive_box[1], slot='archive_' + str(archive_index))
                    path = _archive_path(original, archive)
                    archives = [tuple(row[key] for key in ('x', 'y', 'width', 'height'))
                                for row in desired.values() if row['slot'].startswith('archive_')]
                    _clear_path(path, obstacles=archives)
                    _event(events, 'element', origin, .15, .45,
                        {key: original[key] for key in ('x', 'y', 'slot')},
                        {key: archive[key] for key in ('x', 'y', 'slot')},
                        reason='archive_copy_source', process_kind='transfer', points=path)
                    desired[origin] = archive
                    initial[identity] = dict(original, id=identity, opacity=0.0, copied_from=origin)
                    obj['copied_from'] = origin
                    _event(events, 'element', identity, .15, .1, {'opacity': 0.0}, {'opacity': 1.0},
                           reason='copy_reveal')
                    path = _boundary_path(original, obj)
                    _clear_path(path)
                    _event(events, 'element', identity, .15, .45,
                        {key: original[key] for key in ('x', 'y', 'slot')},
                        {key: obj[key] for key in ('x', 'y', 'slot')},
                        reason='copy', process_kind='transfer', points=path)
            else:
                initial[identity] = dict(deepcopy(obj), opacity=0.0 if transition else obj['opacity'])
                if transition and obj['opacity']:
                    _event(events, 'element', identity, .45, .15, {'opacity': 0.0}, {'opacity': obj['opacity']},
                           reason='scene_default_visible')
        if previous:
            for identity, obj in previous['objects'].items():
                if identity not in desired:
                    _event(events, 'element', identity, 0, .15, {'opacity': obj['opacity']}, {'opacity': 0.0},
                           reason='retire')
        relations = deepcopy(previous['relations']) if previous else {}
        for identity, relation in relations.items():
            _event(events, 'relation', identity, 0, .15, {'opacity': relation['opacity']}, {'opacity': 0.0}, reason='retire_relation')
            _event(events, 'plate', identity, 0, .15, {'opacity': relation['labelOpacity']}, {'opacity': 0.0}, reason='retire_relation')
            _event(events, 'marker', identity, 0, .15, {'opacity': relation['markerOpacity']}, {'opacity': 0.0}, reason='retire_relation')
        relation_map, final_relations = {}, {}
        for row in source_plan['relations']:
            identity = 'cr_' + sha256(canonical([scene['id'], row['id']]))[:24]
            relation_map[row['id']] = identity
            item = dict(deepcopy(source['paths'][row['id']]), id=identity, label=row['label'],
                source=entity_map[row['source']], target=entity_map[row['target']], opacity=0.0,
                strokeDashoffset=1.0, labelOpacity=0.0, markerOpacity=0.0)
            relations[identity] = item
            final_relations[identity] = deepcopy(item)
        states = deepcopy(desired)
        for action in source['actions']:
            kind, start, span = action['kind'], action['start'], action['duration']
            if kind == 'draw':
                identity = relation_map[action['target']]
                _event(events, 'relation', identity, start, span,
                    {'opacity': 0.0, 'strokeDashoffset': 1.0}, {'opacity': 1.0, 'strokeDashoffset': 0.0},
                    process_kind=kind, beat_id=action['beat_id'])
                if final_relations[identity]['label'].strip():
                    _event(events, 'plate', identity, start, span, {'opacity': 0.0}, {'opacity': 1.0},
                        process_kind=kind, beat_id=action['beat_id'])
                    final_relations[identity]['labelOpacity'] = 1.0
                _event(events, 'marker', identity, start + span * .85, span * .15,
                    {'opacity': 0.0}, {'opacity': 1.0}, process_kind=kind, beat_id=action['beat_id'])
                final_relations[identity].update(opacity=1.0, strokeDashoffset=0.0, markerOpacity=1.0)
                continue
            identity = entity_map[action['target']]
            state = states[identity]
            if kind in {'reveal', 'hide'}:
                end = float(kind == 'reveal')
                _event(events, 'element', identity, start, span, {'opacity': state['opacity']}, {'opacity': end},
                    process_kind=kind, beat_id=action['beat_id'],
                    reason='retained_already_visible' if state['opacity'] == end else 'scene_action')
                state['opacity'] = end
            elif kind == 'set_state':
                _event(events, 'state', identity, start, span, {'state': state['state']}, {'state': action['state']},
                    process_kind=kind, beat_id=action['beat_id'])
                state['state'] = action['state']
            elif kind == 'transfer':
                relation_id = relation_map[action['relation_id']]
                path = relations[relation_id]
                _require(state['slot'] == 'token_' + path['source_slot'], 'process_continuity_slot_conflict')
                destination = process_layout()['token_' + path['target_slot']]
                after = dict(x=destination[0], y=destination[1], slot='token_' + path['target_slot'])
                _event(events, 'element', identity, start, span,
                    {key: state[key] for key in ('x', 'y', 'slot')}, after, process_kind=kind,
                    beat_id=action['beat_id'], relation_id=relation_id, points=path['points'])
                state.update(after)
        if previous:
            _event(events, 'header', 'previous', 0, .15, {'opacity': 1.0}, {'opacity': 0.0}, reason='chapter_transition')
            _event(events, 'header', 'current', .15, .45, {'opacity': 0.0}, {'opacity': 1.0}, reason='chapter_transition')
        checkpoint = _checkpoint(states, final_relations, source_plan['title'], source_plan['takeaway'])
        timeline = dict(source, compiler=compiler_version, plan_sha256=sha256(plan),
            initial_visibility={identity: obj['opacity'] for identity, obj in initial.items()},
            initial_states={identity: obj['state'] for identity, obj in initial.items() if obj['kind'] == 'card'},
            paths=deepcopy(relations), actions=sorted(events, key=lambda row: (row['start'], row['ordinal'])))
        segment = dict(schema='video.process-continuity-segment.v1', compiler=compiler_version,
            scene_id=scene['id'], index=index, plan_sha256=sha256(plan), source_binding_sha256=_source_binding(scene, base),
            base_timing=base, leading_transition_seconds=transition, global_start_frame=frame,
            global_end_frame=frame + round(source['duration_seconds'] * FPS), entity_map=entity_map,
            copy_lineage=copies, detail_updates=detail_updates,
            initial_checkpoint=deepcopy(previous) if previous else _checkpoint(initial, relations, source_plan['title'], source_plan['takeaway']),
            final_checkpoint=checkpoint, initial_states=initial, relations=relations, timeline=timeline)
        segment['sha256'] = sha256(segment)
        segments.append(segment)
        previous, previous_map, frame = checkpoint, entity_map, segment['global_end_frame']
    return dict(schema='video.continuity-resolution.v1', compiler=compiler_version,
        plan_sha256=sha256(plan), duration_seconds=frame / FPS, total_frames=frame, segments=segments)


def _position(points, progress):
    lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
    distance = sum(lengths) * progress
    for a, b, length in zip(points, points[1:], lengths):
        if distance <= length and length:
            fraction = distance / length
            return [a[0] + (b[0]-a[0])*fraction, a[1] + (b[1]-a[1])*fraction]
        distance -= length
    return points[-1]


def expected_process_continuity_states(segment, time):
    elements, relations = deepcopy(segment['initial_states']), deepcopy(segment['relations'])
    headers = {'previous': dict(opacity=float(bool(segment['index']))),
               'current': dict(opacity=float(not segment['index']))}
    for event in segment['timeline']['actions']:
        if time < event['start'] or event['kind'] == 'hold':
            continue
        fraction = min(1, max(0, (time-event['start']) / event['duration']))
        eased = 4*fraction**3 if fraction < .5 else 1-4*(1-fraction)**3
        scope, identity = event['scope'], event['target']
        if scope == 'header':
            target = headers[identity]
        elif scope in {'relation', 'plate', 'marker'}:
            target = relations[identity]
        else:
            target = elements[identity]
        if event['process_kind'] == 'transfer' and event['kind'] != 'hold':
            cx, cy = _position(event['points'], eased)
            target.update(x=cx-target['width']/2, y=cy-target['height']/2)
            if time >= event['start'] + event['duration']:
                target['slot'] = event['after']['slot']
            continue
        for key, end in event['after'].items():
            name = 'labelOpacity' if scope == 'plate' else 'markerOpacity' if scope == 'marker' else key
            start = event['before'][key]
            target[name] = end if event['kind'] == 'state' else start+(end-start)*eased
    return dict(elements=elements, relations=relations, headers=headers)


def _validate_segment(scene, segment):
    _require(type(segment) is dict and segment.get('schema') == 'video.process-continuity-segment.v1'
        and segment.get('compiler') == PROCESS_CONTINUITY_COMPILER_VERSION
        and segment.get('scene_id') == scene['id']
        and segment.get('sha256') == sha256({key: value for key, value in segment.items() if key != 'sha256'}),
        'process_continuity_segment_invalid')
    _require(segment['source_binding_sha256'] == _source_binding(scene, _base_timing(scene, segment)),
             'continuity_source_stale')
    _require(type(segment['index']) is int and 0 <= segment['index'] < 24
        and len(segment['initial_states']) <= 12 and len(segment['relations']) <= 6
        and len(segment['timeline']['actions']) <= 128
        and 0 < segment['timeline']['duration_seconds'] <= 60, 'continuity_segment_budget_exceeded')
    _require(segment['timeline']['audio_sha256'] == scene['audio']['sha256'] == scene['motion']['alignment']['audio_sha256']
        and segment['timeline']['audio_seconds'] == scene['motion']['alignment']['audio_seconds']
        and segment['leading_transition_seconds'] == (TRANSITION_SECONDS if segment['index'] else 0)
        and segment['timeline']['leading_seconds'] == segment['base_timing']['leading_seconds'] + segment['leading_transition_seconds'],
        'process_continuity_segment_invalid')
    slots = set(process_layout()) | {'archive_' + str(index) for index in range(len(ARCHIVE_BOXES))}
    identity_pattern = r'ce_[0-9a-f]{24}'
    _require(type(segment.get('entity_map')) is dict
        and set(segment['entity_map']) == {row['id'] for row in scene['motion']['plan']['elements']}
        and len(set(segment['entity_map'].values())) == len(segment['entity_map'])
        and set(segment['entity_map'].values()) <= set(segment['initial_states']),
        'process_continuity_segment_invalid')
    for identity, obj in segment['initial_states'].items():
        _require(type(identity) is str and re.fullmatch(r'ce_[0-9a-f]{24}', identity)
            and obj['id'] == identity and obj['kind'] in {'card', 'token'}
            and type(obj['slot']) is str and obj['slot'] in slots
            and (obj['copied_from'] is None or type(obj['copied_from']) is str
                 and re.fullmatch(identity_pattern, obj['copied_from']))
            and (type(obj['state']) is str if obj['kind'] == 'card' else obj['state'] is None)
            and obj['state'] in (STATE_TRANSITIONS if obj['kind'] == 'card' else {None})
            and type(obj['label']) is str and len(obj['label']) <= 18
            and type(obj['detail']) is str and len(obj['detail']) <= 28,
            'process_continuity_segment_invalid')
        for key in ('x', 'y', 'width', 'height', 'opacity'):
            _require(type(obj[key]) in (int, float) and math.isfinite(obj[key]), 'process_continuity_segment_invalid')
        _require(0 <= obj['opacity'] <= 1 and 0 <= obj['x'] and 0 <= obj['y']
            and obj['width'] > 0 and obj['height'] > 0 and obj['x']+obj['width'] <= WIDTH
            and obj['y']+obj['height'] <= HEIGHT, 'process_continuity_segment_invalid')
        if obj['kind'] == 'card':
            _require(obj['slot'] in {'source', 'queue', 'worker'}
                and tuple(obj[key] for key in ('x', 'y', 'width', 'height')) == tuple(process_layout()[obj['slot']]),
                'process_continuity_segment_invalid')
        else:
            _require((obj['width'], obj['height']) == (136, 44) and obj['detail'] == '',
                     'process_continuity_segment_invalid')
    for identity, relation in segment['relations'].items():
        _require(type(identity) is str and re.fullmatch(r'cr_[0-9a-f]{24}', identity)
            and relation['id'] == identity and relation['source'] in segment['initial_states']
            and relation['target'] in segment['initial_states']
            and type(relation['label']) is str and len(relation['label']) <= 12
            and type(relation['source_slot']) is str and type(relation['target_slot']) is str,
            'process_continuity_segment_invalid')
        source_obj, target_obj = (segment['initial_states'][relation[key]] for key in ('source', 'target'))
        _require(source_obj['kind'] == target_obj['kind'] == 'card'
            and source_obj['slot'] == relation['source_slot'] and target_obj['slot'] == relation['target_slot'],
            'process_continuity_segment_invalid')
        geometry_plan = dict(elements=[dict(id=slot, kind='card', slot=slot) for slot in ('source', 'queue', 'worker')]
            + [dict(id='token', kind='token', slot='token_source')],
            relations=[dict(id='path', source=relation['source_slot'], target=relation['target_slot'], label=relation['label'])])
        geometry = process_paths(geometry_plan)['path']
        _require(all(relation.get(key) == value for key, value in geometry.items()),
                 'process_continuity_path_binding_mismatch')
        for key in ('opacity', 'strokeDashoffset', 'markerOpacity', 'labelOpacity'):
            _require(type(relation[key]) in (int, float) and math.isfinite(relation[key])
                and 0 <= relation[key] <= 1, 'process_continuity_segment_invalid')
    allowed = {'element': {'x', 'y', 'slot', 'opacity'}, 'state': {'state'}, 'detail': {'detail'},
               'relation': {'opacity', 'strokeDashoffset'}, 'plate': {'opacity'}, 'marker': {'opacity'}, 'header': {'opacity'}}
    for action in segment['timeline']['actions']:
        _require(set(action) == {'ordinal','scope','target','beat_id','kind','start','duration','before','after',
            'reason','process_kind','relation_id','points'} and action['scope'] in allowed
            and action['kind'] in {'hold', 'tween', 'state'}
            and set(action['before']) == set(action['after']) and bool(action['before'])
            and set(action['before']) <= allowed[action['scope']], 'process_continuity_segment_invalid')
        collection = ({'previous', 'current'} if action['scope'] == 'header' else segment['relations']
            if action['scope'] in {'relation', 'plate', 'marker'} else segment['initial_states'])
        _require(action['target'] in collection and all(type(action[key]) in (int, float)
            and math.isfinite(action[key]) for key in ('start', 'duration'))
            and 0 <= action['start'] and 0 < action['duration'] <= 10
            and action['start']+action['duration'] <= segment['timeline']['duration_seconds']-1/FPS+1e-6,
            'process_continuity_segment_invalid')
        _require(action['kind'] != 'state' or action['scope'] in {'state', 'detail'},
                 'process_continuity_segment_invalid')
        for values in (action['before'], action['after']):
            for key, value in values.items():
                if key in {'state', 'slot', 'detail'}:
                    _require(type(value) is str, 'process_continuity_segment_invalid')
                    _require(value in STATE_TRANSITIONS if key == 'state' else value in slots
                        if key == 'slot' else len(value) <= 28 and not any(ord(char) < 32 or ord(char) == 127 for char in value),
                        'process_continuity_segment_invalid')
                else:
                    _require(type(value) in (int, float) and math.isfinite(value)
                        and 0 <= value <= (WIDTH if key == 'x' else HEIGHT if key == 'y' else 1),
                        'process_continuity_segment_invalid')
        if action['process_kind'] == 'transfer':
            _require(action['scope'] == 'element' and type(action['points']) is list and 2 <= len(action['points']) <= 12
                and set(action['before']) == {'x', 'y', 'slot'}
                and all(type(point) in (list, tuple) and len(point) == 2
                    and all(type(number) in (int, float) and math.isfinite(number) for number in point)
                    for point in action['points']),
                     'process_continuity_segment_invalid')
            obj = segment['initial_states'][action['target']]
            _require(obj['kind'] == 'token' and action['points'][0] == [action['before']['x']+obj['width']/2,
                action['before']['y']+obj['height']/2] and action['points'][-1] ==
                [action['after']['x']+obj['width']/2, action['after']['y']+obj['height']/2],
                'process_continuity_path_binding_mismatch')
            if action['reason'] == 'scene_action':
                _require(action['relation_id'] in segment['relations']
                    and action['points'] == segment['relations'][action['relation_id']]['points'],
                    'process_continuity_path_binding_mismatch')
            else:
                _require(action['relation_id'] is None and action['reason'] in {'copy', 'archive_copy_source'},
                         'process_continuity_path_binding_mismatch')
            _clear_path(action['points'])
        else:
            _require(action['points'] == [] and action['relation_id'] is None,
                     'process_continuity_segment_invalid')
    _require(set(action['ordinal'] for action in segment['timeline']['actions']) ==
             set(range(len(segment['timeline']['actions']))), 'process_continuity_segment_invalid')
    final = expected_process_continuity_states(segment, segment['timeline']['duration_seconds']-1/FPS)
    expected = _checkpoint(final['elements'], final['relations'], scene['motion']['plan']['title'],
                           scene['motion']['plan']['takeaway'])
    _require(expected == segment['final_checkpoint'], 'process_continuity_checkpoint_mismatch')
    initial = segment['initial_checkpoint']
    _require(type(initial.get('title')) is str and len(initial['title']) <= 34
        and type(initial.get('takeaway')) is str and len(initial['takeaway']) <= 90
        and initial == _checkpoint(segment['initial_states'], segment['relations'], initial['title'], initial['takeaway']),
        'process_continuity_checkpoint_mismatch')


def compile_process_continuity_project(scene, continuity_segment, *, animated=True, runtime_fingerprint=None):
    _rows([scene])
    _require(animated is True, 'continuity_requires_animation')
    segment = deepcopy(continuity_segment)
    _validate_segment(scene, segment)
    wrapper = dict(schema='video.process-continuity-scene.v1', scene_id=scene['id'],
        scene_title=scene.get('title', ''), motion_id=scene['motion']['id'], audio_id=scene['audio']['id'],
        audio_sha256=scene['audio']['sha256'], base_timing=segment['base_timing'],
        base_plan=scene['motion']['plan'], segment=segment)
    source = compile_process_project(scene['motion']['plan'], scene['motion']['beats'], scene['motion']['alignment'],
        compiler_version='motion-process-compiler-2', **segment['base_timing'])
    style = source['index.html'].decode().split('<style>', 1)[1].split('</style>', 1)[0]
    return _compile(wrapper, scene['motion']['beats'], scene['motion']['alignment'], style, runtime_fingerprint)


def recompile_process_continuity_project(plan_document, beats, alignment, *, animated=True, runtime_fingerprint=None):
    _require(type(plan_document) is dict and set(plan_document) == {'schema', 'scene_id', 'scene_title',
        'motion_id', 'audio_id', 'audio_sha256', 'base_timing', 'base_plan', 'segment'}
        and plan_document['schema'] == 'video.process-continuity-scene.v1', 'process_continuity_scene_invalid')
    scene = dict(id=plan_document['scene_id'], title=plan_document['scene_title'],
        audio=dict(id=plan_document['audio_id'], sha256=plan_document['audio_sha256']),
        motion=dict(id=plan_document['motion_id'], plan=plan_document['base_plan'], beats=beats, alignment=alignment),
        timing=plan_document['base_timing'])
    return compile_process_continuity_project(scene, plan_document['segment'], animated=animated,
                                               runtime_fingerprint=runtime_fingerprint)


def _compile(wrapper, beats, alignment, style, runtime):
    segment = wrapper['segment']
    timeline = dict(deepcopy(segment['timeline']), plan_sha256=sha256(wrapper))
    objects = []
    for identity, obj in sorted(segment['initial_states'].items()):
        layers = ''
        if obj['kind'] == 'card':
            for state, visual in STATE_PRESENTATIONS.items():
                layers += (f'<div id="state-{identity}-{state}" class="state-layer" data-state="{state}" '
                    f'data-symbol="{visual["symbol"]}" style="opacity:{int(state == obj["state"])};color:{visual["color"]}">'
                    f'<svg viewBox="0 0 24 24"><path d="{visual["path"]}"/></svg>'
                    f'<span class="state-label">{visual["label"]}</span></div>')
            layers = '<div class="state-badge">' + layers + '</div><div class="docking-bay"></div>'
        objects.append(f'<div id="el-{identity}" class="element {obj["kind"]}" data-motion-element="{identity}" '
            f'data-slot="{html.escape(obj["slot"])}" data-copied-from="{obj["copied_from"] or ""}" '
            f'style="left:{obj["x"]}px;top:{obj["y"]}px;width:{obj["width"]}px;height:{obj["height"]}px;opacity:{obj["opacity"]}">'
            f'<div class="element-label">{html.escape(obj["label"])}</div>'
            f'<div class="element-detail">{html.escape(obj["detail"])}</div>{layers}</div>')
    relations = []
    for identity, relation in sorted(segment['relations'].items()):
        x, y, width, height = relation['label_box']
        label = relation['label']
        limit = max(1, int((width-16)//14))
        chunks = [label[index:index+limit] for index in range(0, len(label), limit)] or ['']
        baseline = y + height/2 - (len(chunks)-1)*9 + 5
        text = ''.join(f'<tspan x="{x+width/2}" y="{baseline+index*18}">{html.escape(chunk)}</tspan>'
                       for index, chunk in enumerate(chunks))
        relations.append(f'<g><defs><marker id="arrow-{identity}" markerWidth="10" markerHeight="10" '
            f'refX="8" refY="5" orient="auto" markerUnits="userSpaceOnUse">'
            f'<path id="tip-{identity}" d="M0 0 L10 5 L0 10 Z" fill="#72d9e2" opacity="{relation["markerOpacity"]}"/>'
            f'</marker></defs><path id="rel-{identity}" class="relation-active" d="{relation["path"]}" '
            f'pathLength="1" marker-end="url(#arrow-{identity})" '
            f'style="opacity:{relation["opacity"]};stroke-dashoffset:{relation["strokeDashoffset"]}"/>'
            f'<g id="label-{identity}" class="relation-label" style="opacity:{relation["labelOpacity"]}">'
            f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="8"/>'
            f'<text text-anchor="middle">{text}</text></g></g>')
    before = segment['initial_checkpoint']
    current = wrapper['base_plan']
    headers = ''.join(f'<div id="head-{name}" style="opacity:{opacity}">'
        f'<h1>{html.escape(plan["title"])}</h1><div class="takeaway">{html.escape(plan["takeaway"])}</div></div>'
        for name, plan, opacity in [('previous', before, int(bool(segment['index']))),
                                    ('current', current, int(not segment['index']))])
    payload = dict(segment=segment, timeline=timeline)
    page = ('<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=1280,height=720"><title>连续过程演示</title>'
        '<script src="assets/gsap.min.js"></script><style>' + style +
        '.token .element-detail{display:none}.element{transform:none}</style></head><body>'
        f'<div id="motion-root" data-composition-id="video-motion" data-width="1280" data-height="720" '
        f'data-start="0" data-duration="{timeline["duration_seconds"]}" data-fps="24">' + headers +
        '<svg id="connections" viewBox="0 0 1280 720">' + ''.join(relations) + '</svg>' + ''.join(objects) +
        '</div><script>' + _SCRIPT.replace('__PAYLOAD__', _safe_script_json(payload)).replace('__TIMELINE_SHA__', sha256(timeline)) +
        '</script></body></html>')
    files = {'index.html': page.encode(), 'plan.json': canonical(wrapper), 'beats.json': canonical(beats),
             'alignment.json': canonical(alignment), 'timeline.json': canonical(timeline)}
    manifest = dict(schema='video.motion-project.v1', compiler=PROCESS_CONTINUITY_COMPILER_VERSION,
        template=PROCESS_CONTINUITY_TEMPLATE_VERSION, hyperframes=HYPERFRAMES_VERSION, gsap=GSAP_VERSION,
        width=WIDTH, height=HEIGHT, fps=FPS, animated=True, plan_sha256=sha256(wrapper),
        semantic_sha256=semantic_digest(beats), timeline_sha256=sha256(timeline), audio_sha256=alignment['audio_sha256'],
        runtime_fingerprint=deepcopy(runtime), files={name: sha256(raw) for name, raw in sorted(files.items())},
        required_assets=['assets/gsap.min.js', 'assets/NotoSansCJK-Regular.ttc'])
    files['manifest.json'] = canonical(manifest)
    return files


_SCRIPT = r'''(()=>{'use strict';
const data=__PAYLOAD__,s=data.segment,tl=gsap.timeline({paused:true,defaults:{ease:'power2.inOut',autoRound:false}});
const node=id=>document.getElementById('el-'+id),rel=id=>document.getElementById('rel-'+id);
const cubic=t=>t<.5?4*t*t*t:1-4*Math.pow(1-t,3),inverse=t=>t<.5?Math.cbrt(t/4):1-Math.cbrt((1-t)/4);
const vars=(value,id)=>Object.fromEntries(Object.entries(value).filter(([key])=>key!=='slot').map(([key,value])=>[key,(key==='x'||key==='y')?value-s.initial_states[id][key]:value]));
for(const a of data.timeline.actions){
 if(a.kind==='hold')continue;
 const when=a.start,duration=a.duration;
 if(a.scope==='state'){
  tl.fromTo(document.getElementById('state-'+a.target+'-'+a.before.state),{opacity:1},{opacity:0,duration:0,immediateRender:false},when);
  tl.fromTo(document.getElementById('state-'+a.target+'-'+a.after.state),{opacity:0},{opacity:1,duration:0,immediateRender:false},when);
 }else if(a.scope==='detail'){
  tl.fromTo(node(a.target).querySelector('.element-detail'),{textContent:a.before.detail},{textContent:a.after.detail,duration:0,immediateRender:false},when);
 }else if(a.process_kind==='transfer'){
  const el=node(a.target),base=s.initial_states[a.target],points=a.points,lengths=points.slice(1).map((p,i)=>Math.hypot(p[0]-points[i][0],p[1]-points[i][1])),total=lengths.reduce((a,b)=>a+b,0);
  let distance=0;
  if(total>0)for(let i=1;i<points.length;i++){
   const length=lengths[i-1];if(!length)continue;
   const a0=distance/total,a1=(distance+length)/total,lo=inverse(a0),hi=inverse(a1);
   tl.fromTo(el,{x:points[i-1][0]-base.width/2-base.x,y:points[i-1][1]-base.height/2-base.y},
    {x:points[i][0]-base.width/2-base.x,y:points[i][1]-base.height/2-base.y,duration:duration*(hi-lo),
     ease:u=>(cubic(lo+u*(hi-lo))-a0)/(a1-a0),immediateRender:false},when+duration*lo);
   distance+=length;
  }
  tl.fromTo(el,{attr:{'data-slot':a.before.slot}},{attr:{'data-slot':a.after.slot},duration:0,immediateRender:false},when+duration);
 }else{
  const el=a.scope==='element'?node(a.target):a.scope==='relation'?rel(a.target):a.scope==='plate'?document.getElementById('label-'+a.target):a.scope==='marker'?document.getElementById('tip-'+a.target):document.getElementById('head-'+a.target);
  tl.fromTo(el,vars(a.before,a.target),{...vars(a.after,a.target),duration,immediateRender:false},when);
 }
}
tl.set({}, {},data.timeline.duration_seconds);window.__timelines=window.__timelines||{};window.__timelines['video-motion']=tl;
const rect=el=>{const r=el.getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height};};
const boxMetrics=el=>({...rect(el),scrollWidth:el.scrollWidth,clientWidth:el.clientWidth,scrollHeight:el.scrollHeight,clientHeight:el.clientHeight,overflow:el.scrollWidth>el.clientWidth||el.scrollHeight>el.clientHeight});
const metrics=el=>{
 const st=getComputedStyle(el),box=rect(el),layers=Array.from(el.querySelectorAll('.state-layer')),layer=layers.find(x=>Number(getComputedStyle(x).opacity)>.5),bay=el.querySelector('.docking-bay');
 const card=el.classList.contains('card'),children=Array.from(el.querySelectorAll(card?'.element-label,.element-detail':'.element-label'));
 const text_boxes=Object.fromEntries(children.map(c=>[c.className,boxMetrics(c)]));
 if(layer){text_boxes['state-label']=boxMetrics(layer.querySelector('.state-label'));text_boxes['state-symbol']=boxMetrics(layer.querySelector('svg'));}
 const text_area=card?{left:box.x+22,top:box.y+22,right:box.x+258,bottom:box.y+188}:{left:box.x,top:box.y,right:box.x+box.width,bottom:box.y+box.height};
 return{...box,opacity:Number(st.opacity),label:el.querySelector('.element-label').textContent,detail:card?el.querySelector('.element-detail').textContent:'',slot:el.dataset.slot,copied_from:el.dataset.copiedFrom||null,
  text_boxes,text_area,scrollWidth:el.scrollWidth,clientWidth:el.clientWidth,scrollHeight:el.scrollHeight,clientHeight:el.clientHeight,
  overflow:el.scrollWidth>el.clientWidth||el.scrollHeight>el.clientHeight||Object.values(text_boxes).some(r=>r.overflow||r.x<text_area.left-.001||r.y<text_area.top-.001||r.x+r.width>text_area.right+.001||r.y+r.height>text_area.bottom+.001),
  stateOpacities:Object.fromEntries(layers.map(x=>[x.dataset.state,Number(getComputedStyle(x).opacity)])),state:layer?.dataset.state??null,stateLabel:layer?.querySelector('.state-label').textContent??null,stateSymbol:layer?.dataset.symbol??null,statePath:layer?.querySelector('path').getAttribute('d')??null,
  docking_bay:bay?{...rect(bay),owner:el.dataset.motionElement,opacity:Number(getComputedStyle(bay).opacity)*Number(st.opacity)}:null};
};
const port=(id,fraction)=>{const path=rel(id),p=path.getPointAtLength(path.getTotalLength()*fraction),q=new DOMPoint(p.x,p.y).matrixTransform(path.getScreenCTM());return{x:q.x,y:q.y};};
window.__videoMotion={version:'motion-process-continuity-compiler-1',planSha256:data.timeline.plan_sha256,timelineSha256:'__TIMELINE_SHA__',duration:data.timeline.duration_seconds,
 inspect:()=>Object.fromEntries(Object.keys(s.initial_states).map(id=>[id,metrics(node(id))])),
 inspectRelations:()=>Object.fromEntries(Object.keys(s.relations).map(id=>[id,{opacity:Number(getComputedStyle(rel(id)).opacity),strokeDashoffset:parseFloat(getComputedStyle(rel(id)).strokeDashoffset),length:rel(id).getTotalLength(),pathD:rel(id).getAttribute('d'),sourcePort:port(id,0),targetPort:port(id,1),middlePoint:port(id,.5),markerOpacity:Number(getComputedStyle(document.getElementById('tip-'+id)).opacity),labelOpacity:Number(getComputedStyle(document.getElementById('label-'+id)).opacity),baseCount:0}])),
 inspectLayout:()=>({fontReady:document.fonts.status==='loaded'&&document.fonts.check('20px VideoNoto'),title:boxMetrics(document.querySelector('#head-current h1')),takeaway:boxMetrics(document.querySelector('#head-current .takeaway')),
  headers:Object.fromEntries(['previous','current'].map(name=>{const head=document.getElementById('head-'+name),title=head.querySelector('h1'),takeaway=head.querySelector('.takeaway');return[name,{opacity:Number(getComputedStyle(head).opacity),title:{...boxMetrics(title),text:title.textContent},takeaway:{...boxMetrics(takeaway),text:takeaway.textContent}}];})),
  relations:Object.keys(s.relations).map(id=>{const el=document.getElementById('label-'+id),plate=rect(el.querySelector('rect')),ink=rect(el.querySelector('text'));return{id,text:el.querySelector('text').textContent,...plate,plate,ink,opacity:Number(getComputedStyle(el).opacity),overflow:ink.x<plate.x-.5||ink.y<plate.y-.5||ink.x+ink.width>plate.x+plate.width+.5||ink.y+ink.height>plate.y+plate.height+.5};})}),
 inspectContinuity:()=>({scene_id:s.scene_id,index:s.index,entity_map:s.entity_map,copy_lineage:s.copy_lineage}),ready:document.fonts.ready.then(()=>true)};
tl.seek(0,false);
})();'''
