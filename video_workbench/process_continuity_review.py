"""Independent DOM review for bounded process continuity.

The observer and screenshot bytes must be retained by the trusted caller.
These checks attest measured states, not narration accuracy or human approval.
The event evaluator here deliberately does not call the compiler's evaluator.
"""
from __future__ import annotations

from copy import deepcopy
import math

from agent_service.contracts import ServiceError
from .motion import FPS, HEIGHT, WIDTH, sha256
from .process_review import (
    _bounds, _digest, _finite_tree, _inside, _near, _number, _overlap,
    _path_position, _scroll, _text_bounds, _v2_card, _v2_relation,
)


COMPILER = 'motion-process-continuity-compiler-1'
WRAPPER_SCHEMA = 'video.process-continuity-scene.v1'
SEGMENT_SCHEMA = 'video.process-continuity-segment.v1'


def _require(value, code='process_continuity_observation_invalid'):
    if not value:
        raise ServiceError(code, 422)


def _segment_hits_box(start, end, box):
    """Slab intersection also covers diagonal archive moves; touching is safe."""
    lo, hi = 0.0, 1.0
    for origin, destination, lower, size in zip(start, end, box[:2], box[2:]):
        delta = destination - origin
        if delta == 0:
            if not lower < origin < lower + size:
                return False
            continue
        left, right = sorted(((lower - origin) / delta, (lower + size - origin) / delta))
        lo, hi = max(lo, left), min(hi, right)
        if lo >= hi:
            return False
    return lo < hi


def seek_sequence_times(timeline):
    """Independent counterpart of the observer's bounded seek permutation."""
    last = timeline['duration_seconds'] - 1 / FPS
    middles = [row['start'] + row['duration'] / 2 for row in timeline['actions']]
    count = min(24, len(middles))
    selected = [middles[math.floor(index * len(middles) / count)] for index in range(count)]
    return [last, 0, *reversed(selected), min(.375, last), min(.6, last), last, 0]


def expected_states(segment, time):
    """Evaluate committed numeric events and discrete state changes independently."""
    elements, relations = deepcopy(segment['initial_states']), deepcopy(segment['relations'])
    headers = dict(previous=dict(opacity=1.0 if segment['index'] else 0.0),
                   current=dict(opacity=0.0 if segment['index'] else 1.0))
    plates = {identity: dict(opacity=row['labelOpacity']) for identity, row in relations.items()}
    markers = {identity: dict(opacity=row['markerOpacity']) for identity, row in relations.items()}
    scopes = dict(element=elements, state=elements, detail=elements, relation=relations,
                  plate=plates, marker=markers, header=headers)
    for event in segment['timeline']['actions']:
        if time < event['start']:
            continue
        if event['kind'] == 'hold':
            continue
        target = scopes[event['scope']][event['target']]
        fraction = min(1.0, max(0.0, (time - event['start']) / event['duration']))
        eased = 4 * fraction ** 3 if fraction < .5 else 1 - (-2 * fraction + 2) ** 3 / 2
        if event['kind'] == 'state':
            target.update(event['after'])
            continue
        for key, end in event['after'].items():
            start = event['before'][key]
            if key == 'slot':
                if fraction >= 1:
                    target[key] = end
            elif type(start) in (int, float):
                target[key] = start + (end - start) * eased
            else:
                _require(start == end, 'process_continuity_event_invalid')
                target[key] = start
        if event['points']:
            point = _path_position(event['points'], eased)
            target.update(x=point[0] - target['width'] / 2, y=point[1] - target['height'] / 2)
    for identity, row in relations.items():
        row.update(labelOpacity=plates[identity]['opacity'], markerOpacity=markers[identity]['opacity'])
    return dict(elements=elements, relations=relations, headers=headers)


def _contract(wrapper, timeline):
    _require(type(wrapper) is dict and wrapper.get('schema') == WRAPPER_SCHEMA)
    segment = wrapper.get('segment')
    _require(type(segment) is dict and segment.get('schema') == SEGMENT_SCHEMA
             and segment.get('compiler') == COMPILER
             and segment.get('scene_id') == wrapper.get('scene_id')
             and segment.get('sha256') == sha256({key: value for key, value in segment.items() if key != 'sha256'}),
             'process_continuity_segment_invalid')
    _require(timeline == dict(segment['timeline'], plan_sha256=sha256(wrapper))
             and timeline.get('compiler') == COMPILER
             and timeline.get('schema') == 'video.motion-timeline.v1',
             'process_continuity_timeline_mismatch')
    _require(type(segment.get('index')) is int and 0 <= segment['index'] < 24)
    _require(type(segment.get('entity_map')) is dict
             and set(segment['entity_map']) == {row['id'] for row in wrapper['base_plan']['elements']}
             and set(segment['entity_map'].values()) <= set(segment['initial_states']))
    for value in (segment['source_binding_sha256'], timeline['audio_sha256'], timeline['semantic_sha256'],
                  timeline['alignment_sha256'], timeline['spoken_sha256']):
        _digest(value)
    _require(timeline['audio_sha256'] == wrapper['audio_sha256'])
    _require(type(timeline.get('actions')) is list and 1 <= len(timeline['actions']) <= 256
             and {row.get('ordinal') for row in timeline['actions']} == set(range(len(timeline['actions']))))
    duration = _number(timeline['duration_seconds'])
    _require(1 / FPS <= duration <= 60)
    expected_fields = {'ordinal', 'scope', 'target', 'beat_id', 'kind', 'start', 'duration',
                       'before', 'after', 'reason', 'process_kind', 'relation_id', 'points'}
    allowed = dict(element={'x', 'y', 'opacity', 'slot'}, state={'state'}, detail={'detail'},
                   relation={'opacity', 'strokeDashoffset'}, plate={'opacity'}, marker={'opacity'}, header={'opacity'})
    for event in timeline['actions']:
        _require(type(event) is dict and set(event) == expected_fields
                 and event['scope'] in allowed and event['kind'] in {'hold', 'tween', 'state'},
                 'process_continuity_event_invalid')
        scope, target = event['scope'], event['target']
        targets = ({'previous', 'current'} if scope == 'header' else segment['relations']
                   if scope in {'relation', 'plate', 'marker'} else segment['initial_states'])
        _require(target in targets and type(event['before']) is dict and type(event['after']) is dict
                 and set(event['before']) == set(event['after'])
                 and bool(event['before']) and set(event['before']) <= allowed[scope],
                 'process_continuity_event_invalid')
        start, seconds = _number(event['start']), _number(event['duration'])
        _require(start >= 0 and seconds > 0 and start + seconds <= duration - 1 / FPS + 1e-6,
                 'process_continuity_event_invalid')
        points = event['points']
        _require(type(points) is list and (not points or scope == 'element'))
        if points:
            _require(len(points) >= 2 and all(type(point) in (list, tuple) and len(point) == 2
                     and all(type(value) in (int, float) and math.isfinite(value) for value in point)
                     for point in points), 'process_continuity_path_invalid')
            _require((event['kind'] == 'hold' and event['before'] == event['after']
                      and all(point == points[0] for point in points))
                     or all(math.dist(a, b) > 0 for a, b in zip(points, points[1:])),
                     'process_continuity_path_invalid')
            obj = segment['initial_states'][target]
            for data, point in ((event['before'], points[0]), (event['after'], points[-1])):
                _require(all(abs(data[axis] + obj[dimension] / 2 - point[index]) < 1e-6
                             for index, axis, dimension in ((0, 'x', 'width'), (1, 'y', 'height'))),
                         'process_continuity_path_invalid')
            if event['process_kind'] == 'transfer':
                if event['reason'] == 'scene_action':
                    _require(event['relation_id'] in timeline['paths']
                             and points == timeline['paths'][event['relation_id']]['points'],
                             'process_continuity_path_invalid')
                else:
                    _require(event['reason'] in {'copy', 'archive_copy_source'} and event['relation_id'] is None,
                             'process_continuity_path_invalid')
    return segment


def _text_and_state(state, wanted):
    from .motion_process import STATE_PRESENTATIONS

    _require(state.get('label') == wanted['label'] and state.get('detail') == wanted['detail']
             and state.get('copied_from') == wanted['copied_from']
             and state.get('slot') == wanted['slot'], 'process_continuity_identity_mismatch')
    text = {'element-label'} | ({'element-detail'} if wanted['detail'] else set())
    if wanted['kind'] == 'card':
        visual = STATE_PRESENTATIONS[wanted['state']]
        _require(state.get('state') == wanted['state'] and state.get('stateLabel') == visual['label']
                 and state.get('stateSymbol') == visual['symbol'] and state.get('statePath') == visual['path'],
                 'process_continuity_state_mismatch')
        layers = state.get('stateOpacities')
        _require(type(layers) is dict and set(layers) == set(STATE_PRESENTATIONS),
                 'process_continuity_state_mismatch')
        for name, value in layers.items():
            _near(value, int(name == wanted['state']), tolerance=.002, code='process_continuity_state_mismatch')
        text |= {'state-label', 'state-symbol'}
    else:
        _require(all(state.get(key) is None for key in ('state', 'stateLabel', 'stateSymbol', 'statePath'))
                 and state.get('stateOpacities') == {} and state.get('docking_bay') is None,
                 'process_continuity_state_mismatch')
    _text_bounds(state, text)


def _token_clearance(segment, actual, labels, layout, time):
    """Reserve card text; label obstacles follow their explicit visibility life.

    A boundary archive may cross a retired label's former location before the
    next label is drawn. Visibility probes include every plate-event endpoint,
    so a short reveal inside a movement cannot hide between its three samples.
    """
    obstacles = [_bounds(box) for identity, row in segment['initial_states'].items() if row['kind'] == 'card'
                 for box in actual[identity]['text_boxes'].values()]
    label_boxes = {identity: _bounds(row) for identity, row in labels.items() if row['text'].strip()}
    headers = layout.get('headers', {})
    obstacles += [_bounds(row[key]) for row in headers.values() for key in ('title', 'takeaway')]
    if not headers:
        obstacles += [_bounds(layout[key]) for key in ('title', 'takeaway')]
    tokens = {identity: row for identity, row in segment['initial_states'].items() if row['kind'] == 'token'}
    for identity, row in tokens.items():
        rectangle = _bounds(actual[identity])
        now = [*obstacles, *(box for key, box in label_boxes.items() if labels[key]['opacity'] > .02)]
        _require(not any(_overlap(rectangle, box) for box in now), 'process_token_obstructed')
        width, height = rectangle[2:]
        for event in segment['timeline']['actions']:
            if event['target'] != identity or not event['points']:
                continue
            times = {event['start'], event['start'] + event['duration']}
            for other in segment['timeline']['actions']:
                if other['scope'] == 'plate':
                    for value in (other['start'], other['start'] + other['duration']):
                        if event['start'] <= value <= event['start'] + event['duration']:
                            times.add(value)
            snapshots = [expected_states(segment, value)['relations'] for value in times]
            swept_obstacles = [*obstacles, *(box for key, box in label_boxes.items()
                                  if any(snapshot[key]['labelOpacity'] > .02 for snapshot in snapshots))]
            for a, b in zip(event['points'], event['points'][1:]):
                for box in swept_obstacles:
                    expanded = (box[0] - width / 2 - 8, box[1] - height / 2 - 8,
                                box[2] + width + 16, box[3] + height + 16)
                    _require(not _segment_hits_box(a, b, expanded), 'process_token_obstructed')
        center = rectangle[0] + width / 2, rectangle[1] + height / 2
        for card_id, card in segment['initial_states'].items():
            if card['kind'] != 'card':
                continue
            bay = _bounds(actual[card_id]['docking_bay'])
            dock = bay[0] + bay[2] / 2, bay[1] + bay[3] / 2
            if math.dist(center, dock) <= .06:
                _require(_inside(rectangle, bay), 'process_docking_bay_mismatch')
    exceptions = {frozenset((row['source'], row['target'])) for row in segment['copy_lineage']} if time <= .6 else set()
    visible = [(identity, _bounds(actual[identity])) for identity in tokens if actual[identity]['opacity'] > .02]
    for index, (identity, box) in enumerate(visible):
        for other, other_box in visible[index + 1:]:
            _require(not _overlap(box, other_box) or frozenset((identity, other)) in exceptions,
                     'process_continuity_token_overlap')


def _sample(sample, time, segment):
    _require(type(sample) is dict, 'process_continuity_sample_missing')
    for key in ('time', 'actual_time'):
        _near(sample.get(key), time, tolerance=1e-5, code='process_seek_mismatch')
    _digest(sample.get('frame_sha256'))
    expected = expected_states(segment, time)
    actual, relations, layout = sample.get('elements'), sample.get('relations'), sample.get('layout')
    _require(type(actual) is dict and set(actual) == set(expected['elements'])
             and type(relations) is dict and set(relations) == set(expected['relations']),
             'process_continuity_objects_missing')
    metadata = sample.get('continuity')
    _require(metadata == {key: segment[key] for key in ('scene_id', 'index', 'entity_map', 'copy_lineage')},
             'process_continuity_metadata_mismatch')
    for identity, wanted in expected['elements'].items():
        state = actual[identity]
        _bounds(state)
        _scroll(state)
        for key in ('x', 'y', 'width', 'height'):
            _near(state.get(key), wanted[key], tolerance=.08, code='process_continuity_pose_mismatch')
        _near(state.get('opacity'), wanted['opacity'], tolerance=.002, code='process_continuity_opacity_mismatch')
        _text_and_state(state, wanted)
        if wanted['kind'] == 'card':
            _v2_card(state, dict(id=identity, slot=wanted['slot']), wanted)
    _require(type(layout) is dict and layout.get('fontReady') is True, 'process_font_not_ready')
    for key in ('title', 'takeaway'):
        _bounds(layout.get(key))
        _scroll(layout[key])
    headers = layout.get('headers')
    _require(type(headers) is dict and set(headers) == {'previous', 'current'},
             'process_continuity_header_missing')
    for key, wanted in expected['headers'].items():
        header = headers[key]
        _near(header.get('opacity'), wanted['opacity'], tolerance=.002, code='process_continuity_header_mismatch')
        checkpoint = segment['initial_checkpoint'] if key == 'previous' else segment['final_checkpoint']
        for label in ('title', 'takeaway'):
            _bounds(header.get(label))
            _scroll(header[label])
            _require(header[label].get('text') == checkpoint[label], 'process_continuity_header_mismatch')
    label_rows = layout.get('relations')
    _require(type(label_rows) is list and len(label_rows) == len(relations)
             and {row.get('id') for row in label_rows if type(row) is dict} == set(relations),
             'process_relation_bounds_missing')
    labels = {row['id']: row for row in label_rows}
    for identity, wanted in expected['relations'].items():
        state, label = relations[identity], labels[identity]
        _require(type(state.get('baseCount')) is int and state['baseCount'] == 0,
                 'process_connection_visible_early')
        for key in ('opacity', 'strokeDashoffset', 'markerOpacity', 'labelOpacity'):
            _near(state.get(key), wanted[key], tolerance=.002, code='process_connection_schedule_mismatch')
        _near(state.get('length'), wanted['relation_length'], tolerance=.1,
              code='process_connection_geometry_mismatch')
        _v2_relation(wanted, state, wanted, segment['initial_states'], actual)
        _require(label.get('text') == wanted['label'] and label.get('overflow') is False,
                 'process_relation_text_overflow')
        _near(label.get('opacity'), wanted['labelOpacity'], tolerance=.002,
              code='process_connection_schedule_mismatch')
        plate = _bounds(label)
        for value, size in zip(plate, wanted['label_box']):
            _near(value, size, code='process_connection_geometry_mismatch')
        ink = _bounds(label.get('ink'), positive=False)
        _require(_inside(ink, plate), 'process_relation_text_overflow')
    _token_clearance(segment, actual, labels, layout, time)
    return expected


def _checkpoint(sample, checkpoint):
    visible = {identity: row for identity, row in sample['elements'].items() if row['opacity'] > .02}
    _require(set(visible) == set(checkpoint['objects']), 'process_continuity_checkpoint_mismatch')
    for identity, wanted in checkpoint['objects'].items():
        state = visible[identity]
        for key in ('x', 'y', 'width', 'height', 'opacity'):
            _near(state.get(key), wanted[key], tolerance=.08, code='process_continuity_checkpoint_mismatch')
        _require(all(state.get(key) == wanted[key] for key in ('label', 'detail', 'state', 'copied_from', 'slot')),
                 'process_continuity_checkpoint_mismatch')
    visible_relations = {identity: row for identity, row in sample['relations'].items() if row['opacity'] > .02}
    _require(set(visible_relations) == set(checkpoint['relations']), 'process_continuity_checkpoint_mismatch')
    for identity, wanted in checkpoint['relations'].items():
        for key in ('opacity', 'strokeDashoffset', 'labelOpacity', 'markerOpacity'):
            _near(visible_relations[identity].get(key), wanted[key], tolerance=.002,
                  code='process_continuity_checkpoint_mismatch')


def _verify(wrapper, timeline, observations):
    for value in (wrapper, timeline, observations):
        _finite_tree(value)
    segment = _contract(wrapper, timeline)
    _require(type(observations) is dict and observations.get('schema') == 'video.motion-observations.v1'
             and observations.get('seek_protocol') == 'hyperframes-core-renderSeek-exact'
             and type(observations.get('render_fps')) is int and observations['render_fps'] == FPS
             and observations.get('timeline_sha256') == sha256(timeline),
             'process_continuity_observation_binding_mismatch')
    windows = observations.get('windows')
    _require(type(windows) is list and len(windows) == len(timeline['actions'])
             and all(type(row) is dict and type(row.get('ordinal')) is int for row in windows)
             and {row['ordinal'] for row in windows} == {row['ordinal'] for row in timeline['actions']},
             'process_continuity_windows_missing')
    by_ordinal = {row['ordinal']: row for row in windows}
    for event in timeline['actions']:
        row = by_ordinal[event['ordinal']]
        _require(set(row) == {'ordinal', 'before', 'middle', 'after'}, 'process_continuity_windows_missing')
        times = dict(before=max(0, event['start'] - 1 / FPS), middle=event['start'] + event['duration'] / 2,
                     after=min(timeline['duration_seconds'] - 1 / FPS, event['start'] + event['duration'] + 1 / FPS))
        for key, time in times.items():
            _sample(row[key], time, segment)
        if event['kind'] != 'hold':
            _require(len({row[key]['frame_sha256'] for key in ('before', 'middle', 'after')}) >= 2,
                     'process_continuity_frozen_frames')
        if event['process_kind'] == 'transfer' and event['reason'] == 'scene_action':
            _require(all(row[key]['elements'][event['target']]['opacity'] > .99 for key in times),
                     'process_continuity_invisible_transfer')
    boundary = observations.get('continuity_boundary')
    _require(type(boundary) is dict, 'process_continuity_boundary_missing')
    for key, time in (('initial', 0), ('final', timeline['duration_seconds'] - 1 / FPS), ('repeated_initial', 0)):
        _sample(boundary.get(key), time, segment)
    _checkpoint(boundary['initial'], segment['initial_checkpoint'])
    _checkpoint(boundary['final'], segment['final_checkpoint'])
    if segment['index']:
        for key, time in (('transition_middle', .375), ('transition_end', .6)):
            _sample(boundary.get(key), time, segment)
        for lineage in segment['copy_lineage']:
            for sample in (boundary['transition_middle'], boundary['transition_end']):
                _require(all(sample['elements'][identity]['opacity'] > .99 for identity in (lineage['source'], lineage['target'])),
                         'process_continuity_copy_source_missing')
            _require(boundary['final']['elements'][lineage['source']]['opacity'] > .99,
                     'process_continuity_copy_source_missing')
    reverse = observations.get('reverse_seek')
    _require(type(reverse) is dict and reverse.get('repeat_state_equal') is True
             and reverse.get('first_sha256') == boundary['initial']['frame_sha256']
             and reverse.get('repeated_first_sha256') == boundary['repeated_initial']['frame_sha256']
             and reverse.get('last_sha256') == boundary['final']['frame_sha256']
             and reverse['first_sha256'] == reverse['repeated_first_sha256'],
             'process_continuity_reverse_seek_failed')
    _require(all(boundary['initial'][key] == boundary['repeated_initial'][key]
                 for key in ('elements', 'relations', 'layout', 'continuity')), 'process_continuity_reverse_seek_failed')
    sequence = observations.get('seek_sequence')
    times = seek_sequence_times(timeline)
    _require(type(sequence) is dict and sequence.get('schema') == 'video.process-continuity-seek-sequence.v1'
             and type(sequence.get('samples')) is list and len(sequence['samples']) == len(times),
             'process_continuity_seek_sequence_missing')
    repeated = {}
    for sample, time in zip(sequence['samples'], times):
        _sample(sample, time, segment)
        state = {key: sample[key] for key in ('elements', 'relations', 'layout', 'continuity', 'frame_sha256')}
        if time in repeated:
            _require(state == repeated[time], 'process_continuity_seek_sequence_mismatch')
        repeated[time] = state
    _require(repeated[0]['frame_sha256'] == boundary['initial']['frame_sha256']
             and repeated[timeline['duration_seconds'] - 1 / FPS]['frame_sha256'] == boundary['final']['frame_sha256'],
             'process_continuity_seek_sequence_mismatch')
    return dict(schema='video.motion-review.v1', technical_status='passed',
        checks=[dict(ordinal=row['ordinal'], target=row['target'], kind=row['kind'], passed=True) for row in timeline['actions']],
        plan_sha256=sha256(wrapper), timeline_sha256=sha256(timeline), observation_sha256=sha256(observations),
        timing_review_status=timeline['review_status'], visual_comprehension_status='pending', voice_review_status='pending',
        continuity=dict(scene_id=segment['scene_id'], initial_checkpoint_sha256=sha256(segment['initial_checkpoint']),
                        final_checkpoint_sha256=sha256(segment['final_checkpoint']),
                        initial_frame_sha256=boundary['initial']['frame_sha256'], final_frame_sha256=boundary['final']['frame_sha256'],
                        boundary_status='passed', copy_lineage=deepcopy(segment['copy_lineage']),
                        seek_sequence_samples=len(times)))


def verify_process_continuity_observations(wrapper, timeline, observations):
    """Validate measured browser evidence without granting human acceptance."""
    try:
        return _verify(wrapper, timeline, observations)
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError, ZeroDivisionError):
        raise ServiceError('process_continuity_observation_invalid', 422) from None
