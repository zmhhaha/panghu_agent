"""Synthetic DOM fixtures exercise the reviewer; these are not browser proof."""
from copy import deepcopy
import math

import pytest

from agent_service.contracts import ServiceError
from video_workbench.motion import estimated_alignment, make_beats, sha256
from video_workbench.motion_process import resolve_process_timeline
from video_workbench.process_review import verify_process_observations


# Expected glyphs and schedule deliberately do not come from reviewer helpers.
STATES = {
    'idle': ('待命', 'circle', 'M12 3 A9 9 0 1 1 11.99 3 Z'),
    'waiting': ('等待', 'queue', 'M4 6 H20 M4 12 H20 M4 18 H20'),
    'running': ('处理中', 'triangle', 'M7 3 L20 12 L7 21 Z'),
    'completed': ('完成', 'check', 'M4 12 L10 18 L21 5'),
    'failed': ('失败', 'cross', 'M5 5 L19 19 M19 5 L5 19'),
}


def _box(x, y, width, height):
    return dict(x=x, y=y, width=width, height=height, scrollWidth=width, clientWidth=width,
                scrollHeight=height, clientHeight=height, overflow=False)


def _cubic(time, start, duration):
    progress = max(0, min(1, (time - start) / duration))
    return 4 * progress ** 3 if progress <= .5 else 1 - 4 * (1 - progress) ** 3


def fixture(*, first_beat_seconds=6, leading_seconds=0):
    beats = make_beats('请求输入。请求处理。请求完成。')
    ids = [row['id'] for row in beats['segments']]
    elements = [dict(id=name, kind='card', slot=slot, label=label, detail='说明文字')
                for name, slot, label in [('input', 'source', '入口'), ('queue', 'queue', '队列'),
                                          ('worker', 'worker', '执行器')]]
    elements.append(dict(id='token', kind='token', slot='token_source', label='请求', detail=''))
    actions = [
        dict(kind='draw', target='forward', beat_id=ids[0], placement='start', pace='normal'),
        dict(kind='reveal', target='token', beat_id=ids[0], placement='middle', pace='quick'),
        dict(kind='transfer', target='token', relation_id='forward', beat_id=ids[1], placement='start', pace='slow'),
        dict(kind='set_state', target='queue', state='waiting', beat_id=ids[1], placement='end', pace='normal'),
        dict(kind='set_state', target='queue', state='running', beat_id=ids[2], placement='start', pace='normal'),
        dict(kind='set_state', target='queue', state='completed', beat_id=ids[2], placement='middle', pace='normal'),
    ]
    plan = dict(schema='video.process-plan.v1', template='flow', title='请求经过队列', takeaway='对象移动，状态同步变化。',
                elements=elements, relations=[dict(id='forward', source='input', target='queue', label='排队')], actions=actions)
    alignment = estimated_alignment(beats, 'a' * 64, 18)
    alignment['segments'][0]['end'] = first_beat_seconds
    timeline = resolve_process_timeline(plan, beats, alignment, leading_seconds=leading_seconds,
                                        compiler_version='motion-process-compiler-1')
    drawing_start, reveal_start = [round(leading_seconds + first_beat_seconds * fraction, 6)
                                   for fraction in (.03, .38)]
    drawing_duration, reveal_duration = [round(min(pace, first_beat_seconds * .24), 6) for pace in (.45, .25)]
    assert [a['start'] for a in timeline['actions']] == [drawing_start, reveal_start] + [
        round(leading_seconds + start, 6) for start in (6.18, 10.32, 12.18, 14.28)]
    assert [a['duration'] for a in timeline['actions']] == [drawing_duration, reveal_duration, 1.44, .45, .45, .45]

    def sample(time):
        states = {}
        for identity, x in [('input', 64), ('queue', 480), ('worker', 896)]:
            state = 'idle'
            if identity == 'queue':
                for boundary, name in [(10.32, 'waiting'), (12.18, 'running'), (14.28, 'completed')]:
                    if time >= leading_seconds + boundary:
                        state = name
            label, symbol, path = STATES[state]
            states[identity] = dict(_box(x, 452, 320, 188), opacity=1, state=state, stateLabel=label,
                stateSymbol=symbol, statePath=path,
                stateOpacities={name: int(name == state) for name in STATES},
                text_area=dict(left=x + 20, top=470, right=x + 300, bottom=622),
                text_boxes={'element-label': _box(x + 22, 472, 276, 30),
                            'element-detail': _box(x + 22, 510, 276, 24),
                            'state-label': _box(x + 56, 594, 54, 28),
                            'state-symbol': _box(x + 22, 596, 24, 24)})
        # Independently specified source->queue dogleg: 88 up, 416 right, 88 down.
        distance = 592 * _cubic(time, leading_seconds + 6.18, 1.44)
        if distance <= 88:
            cx, cy = 224, 328 - distance
        elif distance <= 504:
            cx, cy = 224 + distance - 88, 240
        else:
            cx, cy = 640, 240 + distance - 504
        x, y = cx - 68, cy - 22
        states['token'] = dict(_box(x, y, 136, 44), opacity=_cubic(time, reveal_start, reveal_duration),
            state=None, stateLabel=None, stateSymbol=None, statePath=None, stateOpacities={},
            text_area=dict(left=x, top=y, right=x + 136, bottom=y + 44),
            text_boxes={'element-label': _box(x + 50, y + 9, 36, 26)})
        drawing = _cubic(time, drawing_start, drawing_duration)
        layout = dict(fontReady=True, title=_box(64, 44, 1152, 47), takeaway=_box(64, 664, 1152, 24),
            relations=[dict(id='forward', text='排队', x=312, y=180, width=240, height=28,
                            ink=dict(x=416, y=183, width=32, height=22), opacity=drawing, overflow=False)])
        return dict(time=time, actual_time=time, frame_sha256=sha256({'frame-time': round(time, 9)}),
            elements=states, layout=layout,
            relations={'forward': dict(opacity=drawing, strokeDashoffset=1 - drawing, length=592,
                                      markerOpacity=_cubic(time, drawing_start + .85 * drawing_duration,
                                                           .15 * drawing_duration), labelOpacity=drawing, baseCount=0)})

    windows = []
    for action in timeline['actions']:
        times = dict(before=max(0, action['start'] - 1 / 24), middle=action['start'] + action['duration'] / 2,
                     after=min(leading_seconds + 18 - 1 / 24, action['start'] + action['duration'] + 1 / 24))
        windows.append(dict(ordinal=action['ordinal'], **{key: sample(time) for key, time in times.items()}))
    initial = sample(0)
    observations = dict(schema='video.motion-observations.v1', timeline_sha256=sha256(timeline),
        seek_protocol='hyperframes-core-renderSeek-exact', render_fps=24, windows=windows,
        initial=initial, repeated_initial=deepcopy(initial),
        reverse_seek=dict(first_sha256=initial['frame_sha256'],
                          last_sha256=sample(leading_seconds + 18 - 1 / 24)['frame_sha256'],
                          repeated_first_sha256=initial['frame_sha256'], repeat_state_equal=True))
    return plan, timeline, observations


def test_valid_observed_process_keeps_human_timing_and_quality_pending():
    report = verify_process_observations(*fixture())
    assert report['technical_status'] == 'passed'
    assert report['timing_review_status'] == 'pending'
    assert report['visual_comprehension'] == report['voice_naturalness'] == 'pending_user_review'
    assert report['checked_action_windows'] == 6


def fixture_v2():
    """Independent straight-through bays and bottom return, not browser proof."""
    plan, _, observations = fixture()
    plan['relations'] += [dict(id='dispatch', source='queue', target='worker', label='交给执行节点'),
                          dict(id='retry', source='worker', target='queue', label='失败后返回')]
    beats = make_beats('请求输入。请求处理。请求完成。')
    timeline = resolve_process_timeline(plan, beats, estimated_alignment(beats, 'a' * 64, 18),
                                        compiler_version='motion-process-compiler-2')
    observations['timeline_sha256'] = sha256(timeline)
    samples = [observations['initial'], observations['repeated_initial']] + [
        w[key] for w in observations['windows'] for key in ('before', 'middle', 'after')]
    for sample in samples:
        for identity, x in [('input', 64), ('queue', 500), ('worker', 936)]:
            state = sample['elements'][identity]
            state.update(_box(x, 260, 280, 268),
                text_area=dict(left=x + 22, top=282, right=x + 258, bottom=448),
                text_boxes={'element-label': _box(x + 22, 282, 236, 64),
                            'element-detail': _box(x + 22, 352, 236, 48),
                            'state-label': _box(x + 56, 418, 54, 28),
                            'state-symbol': _box(x + 22, 420, 24, 24)},
                docking_bay=dict(x=x + 14, y=464, width=252, height=56, owner=identity, opacity=1))
        # Four collinear token points include two bay centers and two borders.
        # Physical request travel is 436px; the visible external edge is 156px.
        x, y = 136 + 436 * _cubic(sample['time'], 6.18, 1.44), 470
        token = sample['elements']['token']
        token.update(_box(x, y, 136, 44), docking_bay=None,
            text_area=dict(left=x, top=y, right=x + 136, bottom=y + 44),
            text_boxes={'element-label': _box(x + 50, y + 9, 36, 26)})
        sample['layout']['relations'] = []
        for identity, text, plate, ink, source, target, middle, distance, path in [
            ('forward', '排队', (352, 410, 140, 44), (404, 422, 36, 20), (344, 492), (500, 492),
             (422, 492), 156, 'M 344 492 L 500 492'),
            ('dispatch', '交给执行节点', (788, 410, 140, 44), (812, 422, 84, 20), (780, 492), (936, 492),
             (858, 492), 156, 'M 780 492 L 936 492'),
            ('retry', '失败后返回', (746, 634, 224, 24), (818, 636, 70, 20), (1076, 528), (640, 528),
             (858, 598), 576, 'M 1076 528 L 1076 598 L 640 598 L 640 528'),
        ]:
            state = sample['relations'].setdefault(identity, dict(opacity=0, labelOpacity=0, markerOpacity=0,
                                                                  strokeDashoffset=1, baseCount=0))
            state.update(length=distance, pathD=path, sourcePort=dict(zip(('x', 'y'), source)),
                         targetPort=dict(zip(('x', 'y'), target)), middlePoint=dict(zip(('x', 'y'), middle)))
            px, py, pw, ph = plate
            ix, iy, iw, ih = ink
            sample['layout']['relations'].append(dict(id=identity, text=text, x=px, y=py, width=pw, height=ph,
                ink=dict(x=ix, y=iy, width=iw, height=ih), opacity=state['labelOpacity'], overflow=False))
    return plan, timeline, observations


def test_v2_docking_and_distinct_visible_edge_length_pass_without_promoting_quality():
    args = fixture_v2()
    report = verify_process_observations(*args)
    assert report['technical_status'] == 'passed'
    assert report['timing_review_status'] == 'pending'
    assert report['visual_comprehension'] == 'pending_user_review'
    assert 'actual_svg_node_border_ports' in report['checks']
    assert 'swept_text_clearance_8px' in report['checks']
    assert args[1]['paths']['forward']['length'] == 436
    assert args[1]['paths']['forward']['relation_length'] == 156
    assert verify_process_observations(*fixture())['technical_status'] == 'passed'


@pytest.mark.parametrize('failure', ['source', 'target', 'return_port', 'return_middle', 'path', 'length',
                                   'missing_port', 'same_length_floating_path', 'timeline_port'])
def test_v2_real_svg_ports_and_visible_path_must_meet_actual_node_borders(failure):
    plan, timeline, observations = fixture_v2()
    sample = observations['windows'][2]['middle']
    relation = sample['relations']['forward']
    if failure == 'source':
        relation['sourcePort']['x'] -= 10
    elif failure == 'target':
        relation['targetPort']['y'] -= 124
    elif failure == 'return_port':
        sample['relations']['retry']['targetPort']['x'] -= 140
    elif failure == 'return_middle':
        sample['relations']['retry']['middlePoint']['y'] = 458
    elif failure == 'path':
        relation['pathD'] = 'M 344 492 L 500 400'
    elif failure == 'length':
        relation['length'] = 436
    elif failure == 'missing_port':
        del relation['targetPort']
    elif failure == 'same_length_floating_path':
        relation.update(pathD='M 344 328 L 500 328', sourcePort=dict(x=344, y=328),
                        targetPort=dict(x=500, y=328), middlePoint=dict(x=422, y=328))
    else:
        timeline['paths']['forward']['source_port'] = [344, 328]
        observations['timeline_sha256'] = sha256(timeline)
    with pytest.raises(ServiceError):
        verify_process_observations(plan, timeline, observations)


@pytest.mark.parametrize('failure', ['bay_missing', 'bay_owner', 'bay_bounds', 'bay_opacity', 'title_in_bay',
                                   'detail_in_bay', 'state_in_bay', 'token_in_body', 'label_route', 'footer_route'])
def test_v2_bay_permits_ownership_but_never_token_obstruction(failure):
    args = fixture_v2()
    sample = args[2]['windows'][2]['middle']
    card = sample['elements']['queue']
    if failure == 'bay_missing':
        del card['docking_bay']
    elif failure == 'bay_owner':
        card['docking_bay']['owner'] = 'worker'
    elif failure == 'bay_bounds':
        card['docking_bay']['y'] -= 20
    elif failure == 'bay_opacity':
        card['docking_bay']['opacity'] = 0
    elif failure in {'title_in_bay', 'detail_in_bay', 'state_in_bay'}:
        key = {'title_in_bay': 'element-label', 'detail_in_bay': 'element-detail', 'state_in_bay': 'state-label'}[failure]
        card['text_area']['bottom'] = 520
        card['text_boxes'][key]['y'] = 470
    elif failure == 'token_in_body':
        sample['elements']['token']['y'] = 360
    elif failure == 'label_route':
        sample['layout']['relations'][0]['y'] = 470
    else:
        sample['layout']['takeaway']['y'] = 590
    with pytest.raises(ServiceError):
        verify_process_observations(*args)


@pytest.mark.parametrize('to_v2', [True, False])
def test_observations_cannot_mix_compiler_versions_or_geometry(to_v2):
    plan, timeline, observations = fixture() if to_v2 else fixture_v2()
    timeline['compiler'] = 'motion-process-compiler-2' if to_v2 else 'motion-process-compiler-1'
    observations['timeline_sha256'] = sha256(timeline)
    with pytest.raises(ServiceError, match='process_path_binding_mismatch'):
        verify_process_observations(plan, timeline, observations)


def test_v2_hidden_future_label_requires_full_eight_pixel_clearance():
    args = fixture_v2()
    sample = args[2]['windows'][0]['before']
    # The return token's lower edge is 620; an eight-pixel buffer reaches 628.
    # Moving the conclusion to627 overlaps even though its ink remains in-canvas.
    sample['layout']['takeaway']['y'] = 627
    with pytest.raises(ServiceError, match='process_token_obstructed'):
        verify_process_observations(*args)


@pytest.mark.parametrize('failure', ['frozen', 'midpoint', 'destination', 'size', 'bounds', 'hidden'])
def test_transfer_checks_actual_complete_token_not_global_frame_changes(failure):
    args = fixture()
    window = args[2]['windows'][2]
    if failure == 'frozen':
        window['middle']['elements']['token'] = deepcopy(window['before']['elements']['token'])
    elif failure == 'midpoint':
        window['middle']['elements']['token']['y'] += 20
    elif failure == 'destination':
        window['after']['elements']['token']['x'] += 416
    elif failure == 'size':
        window['middle']['elements']['token']['width'] = 1
    elif failure == 'bounds':
        window['middle']['elements']['token']['x'] = -1
    else:
        window['middle']['elements']['token']['opacity'] = 0
    with pytest.raises(ServiceError):
        verify_process_observations(*args)


@pytest.mark.parametrize('failure', ['early_state', 'label', 'symbol', 'path', 'other_card', 'extra_layer', 'missing_layer'])
def test_state_presentation_and_all_inactive_layers_are_checked(failure):
    args = fixture()
    target = args[2]['windows'][3]['after']['elements']['queue']
    if failure == 'early_state':
        args[2]['initial']['elements']['queue']['state'] = 'waiting'
    elif failure == 'label':
        target['stateLabel'] = '运行'
    elif failure == 'symbol':
        target['stateSymbol'] = 'check'
    elif failure == 'path':
        target['statePath'] = 'M0 0'
    elif failure == 'other_card':
        args[2]['windows'][3]['after']['elements']['worker']['state'] = 'running'
    elif failure == 'extra_layer':
        args[2]['initial']['elements']['queue']['stateOpacities']['failed'] = 1
    else:
        del target['stateOpacities']['idle']
    with pytest.raises(ServiceError, match='process_state_presentation_mismatch'):
        verify_process_observations(*args)


@pytest.mark.parametrize('key', ['opacity', 'markerOpacity', 'labelOpacity', 'strokeDashoffset', 'baseCount'])
def test_initial_connections_and_arrows_cannot_appear_early(key):
    args = fixture()
    args[2]['initial']['relations']['forward'][key] = 1 if key != 'strokeDashoffset' else 0
    with pytest.raises(ServiceError):
        verify_process_observations(*args)


@pytest.mark.parametrize('failure', ['font', 'parent', 'child_scroll', 'child_crop', 'hidden_child_crop',
                                   'missing_box', 'collapsed_text', 'state_crop', 'relation_crop'])
def test_fonts_and_real_text_bounds_include_hidden_objects(failure):
    args = fixture()
    sample = args[2]['windows'][0]['before']
    target = sample['elements']['input']
    if failure == 'font':
        sample['layout']['fontReady'] = False
    elif failure == 'parent':
        target['overflow'] = True
    elif failure == 'child_scroll':
        target['text_boxes']['element-detail']['scrollHeight'] += 1
    elif failure == 'child_crop':
        target['text_boxes']['element-detail']['x'] -= 5
    elif failure == 'hidden_child_crop':
        sample['elements']['token']['text_boxes']['element-label']['x'] = 0
    elif failure == 'missing_box':
        del target['text_boxes']['element-detail']
    elif failure == 'collapsed_text':
        target['text_boxes']['element-detail']['height'] = 0
    elif failure == 'state_crop':
        target['text_boxes']['state-label']['y'] += 1
    else:
        sample['layout']['relations'][0]['ink']['width'] = 999
    with pytest.raises(ServiceError):
        verify_process_observations(*args)


@pytest.mark.parametrize('failure', ['nan', 'infinity', 'huge', 'boolean', 'plan_sha', 'timeline_sha',
                                   'action_binding', 'seek', 'actual_seek', 'fps', 'protocol', 'missing_window',
                                   'duplicate_window', 'reverse_frame', 'reverse_state', 'missing_initial'])
def test_observations_must_be_finite_bound_complete_and_reversible(failure):
    plan, timeline, observations = fixture()
    sample = observations['windows'][0]['before']
    if failure in {'nan', 'infinity', 'huge', 'boolean'}:
        sample['time'] = {'nan': math.nan, 'infinity': math.inf, 'huge': 10 ** 4000, 'boolean': False}[failure]
    elif failure == 'plan_sha':
        plan['title'] = '另一个标题'
    elif failure == 'timeline_sha':
        observations['timeline_sha256'] = 'b' * 64
    elif failure == 'action_binding':
        timeline['actions'][2]['from_slot'] = 'token_queue'
        observations['timeline_sha256'] = sha256(timeline)
    elif failure in {'seek', 'actual_seek'}:
        sample['time' if failure == 'seek' else 'actual_time'] += .1
    elif failure == 'fps':
        observations['render_fps'] = True
    elif failure == 'protocol':
        observations['seek_protocol'] = 'fake_bridge'
    elif failure == 'missing_window':
        observations['windows'].pop()
    elif failure == 'duplicate_window':
        observations['windows'][-1] = deepcopy(observations['windows'][0])
    elif failure == 'reverse_frame':
        observations['reverse_seek']['repeated_first_sha256'] = 'b' * 64
    elif failure == 'reverse_state':
        observations['repeated_initial']['elements']['input']['borderColor'] = 'red'
    else:
        del observations['initial']
    with pytest.raises(ServiceError):
        verify_process_observations(plan, timeline, observations)


@pytest.mark.parametrize('label_text', ['', ' ', '   ', '\u2003'])
def test_empty_or_whitespace_label_stays_hidden_after_draw(label_text):
    plan, timeline, observations = fixture()
    plan['relations'][0]['label'] = label_text
    timeline['plan_sha256'] = sha256(plan)
    observations['timeline_sha256'] = sha256(timeline)
    for sample in [observations['initial'], observations['repeated_initial']] + [
            w[key] for w in observations['windows'] for key in ('before', 'middle', 'after')]:
        sample['relations']['forward']['labelOpacity'] = 0
        label = sample['layout']['relations'][0]
        label.update(text=label_text, opacity=0, ink=dict(x=432, y=183, width=0, height=0))
    assert verify_process_observations(plan, timeline, observations)['technical_status'] == 'passed'
    observations['windows'][0]['after']['relations']['forward']['labelOpacity'] = 1
    with pytest.raises(ServiceError):
        verify_process_observations(plan, timeline, observations)


@pytest.mark.parametrize('leading_seconds', [0, 8])
def test_exact_two_frame_action_is_accepted_after_compiler_rounding(leading_seconds):
    args = fixture(first_beat_seconds=25 / 72, leading_seconds=leading_seconds)
    assert args[1]['actions'][0]['duration'] == .083333 < 2 / 24
    if leading_seconds:
        beat = args[1]['beats'][0]
        # Subtracting the padded absolute times loses a few floating-point
        # bits despite the source window being exactly two frames long.
        reconstructed = .24 * (beat['end'] - beat['start'])
        assert 0 < 2 / 24 - reconstructed < 1e-12
    assert verify_process_observations(*args)['technical_status'] == 'passed'


@pytest.mark.parametrize('leading_seconds', [0, 8])
def test_genuinely_short_source_window_is_rejected_even_when_it_rounds_to_two_frames(leading_seconds):
    plan, timeline, observations = fixture(first_beat_seconds=25 / 72, leading_seconds=leading_seconds)
    timeline['beats'][0]['end'] -= .0000005
    beat = timeline['beats'][0]
    span = beat['end'] - beat['start']
    assert .24 * span < 2 / 24 - 1e-12
    assert round(.24 * span, 6) == .083333
    for action, fraction in zip(timeline['actions'][:2], (.03, .38)):
        action.update(start=round(beat['start'] + fraction * span, 6), until=beat['end'])
    observations['timeline_sha256'] = sha256(timeline)
    with pytest.raises(ServiceError, match='process_action_timing_mismatch'):
        verify_process_observations(plan, timeline, observations)


@pytest.mark.parametrize('leading_seconds', [0, 8])
def test_actual_duration_below_rounding_tolerance_is_still_rejected(leading_seconds):
    plan, timeline, observations = fixture(first_beat_seconds=25 / 72, leading_seconds=leading_seconds)
    timeline['actions'][0]['duration'] = .083332
    observations['timeline_sha256'] = sha256(timeline)
    with pytest.raises(ServiceError, match='process_action_timing_mismatch'):
        verify_process_observations(plan, timeline, observations)


def test_review_does_not_mutate_inputs():
    args = fixture()
    original = deepcopy(args)
    verify_process_observations(*args)
    assert args == original


def test_noop_reveal_cannot_be_masked_by_another_changing_object():
    plan, timeline, observations = fixture()
    original = plan['actions'][-1]
    action = dict(kind='reveal', target='token', beat_id=original['beat_id'], placement='middle', pace='normal')
    plan['actions'][-1] = action
    resolved = timeline['actions'][-1]
    timeline['actions'][-1] = dict(action, **{key: resolved[key] for key in ('ordinal', 'start', 'duration', 'until')})
    timeline['plan_sha256'] = sha256(plan)
    observations['timeline_sha256'] = sha256(timeline)
    with pytest.raises(ServiceError, match='process_visibility_(noop|change_required)'):
        verify_process_observations(plan, timeline, observations)


def test_actions_must_finish_before_last_observable_frame():
    plan, timeline, observations = fixture()
    timeline['beats'][-1]['start'] = 17.65
    for action, placement, start in [(timeline['actions'][-2], 'start', 17.6605),
                                     (timeline['actions'][-1], 'end', 17.902)]:
        plan['actions'][action['ordinal']]['placement'] = placement
        action.update(placement=placement, start=start, duration=.084)
    timeline['plan_sha256'] = sha256(plan)
    observations['timeline_sha256'] = sha256(timeline)
    with pytest.raises(ServiceError, match='process_final_hold_missing'):
        verify_process_observations(plan, timeline, observations)
