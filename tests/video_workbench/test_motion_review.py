"""Semantic capture failures that a valid, changing MP4 alone cannot detect."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from agent_service.contracts import ServiceError
from video_workbench.motion import make_beats, example_plan, estimated_alignment, resolve_timeline, sha256, action_windows
from video_workbench.motion_review import _connector_states, _demo_expected_states, verify_observations
from video_workbench.motion_demo import DEMO_LAYOUT, resolve_demo_timeline


def fixture():
    beats = make_beats('请求到达。请求进入队列。执行节点开始工作。')
    plan = example_plan(beats)
    plan['elements'] = [row for row in plan['elements'] if row['id'] == 'request']
    plan['relations'] = []
    plan['actions'] = [dict(beat_id=beats['segments'][0]['id'], target='request',
                            kind='move', placement='middle', pace='slow', to_slot='token_queue')]
    timeline = resolve_timeline(plan, beats, estimated_alignment(beats, 'a' * 64, 15),
                                compiler_version='motion-compiler-1')
    times = action_windows(timeline)[0]
    rows = dict(ordinal=0)
    for label, x, stamp in [('before', 162, 'a'), ('middle', 371, 'b'), ('after', 580, 'c')]:
        rows[label] = dict(time=times[label], actual_time=times[label], frame_sha256=stamp * 64,
            elements={'request': dict(opacity=1, x=x, y=220, width=120, height=44, overflow=False)},
            relations={}, layout=dict(fontReady=True, relations=[],
                title=dict(x=72, y=74, width=1136, height=48, overflow=False),
                takeaway=dict(x=72, y=648, width=1136, height=30, overflow=False)))
    observations = dict(schema='video.motion-observations.v1', timeline_sha256=sha256(timeline),
        seek_protocol='hyperframes-core-renderSeek-exact', render_fps=24,
        windows=[rows], reverse_seek=dict(first_sha256='a' * 64, last_sha256='c' * 64,
                                         repeated_first_sha256='a' * 64, repeat_state_equal=True))
    return plan, timeline, observations


def test_actual_target_motion_is_required_but_quality_stays_pending():
    report = verify_observations(*fixture())
    assert report['technical_status'] == 'passed'
    assert report['visual_comprehension'] == report['voice_naturalness'] == 'pending_user_review'
    assert report['timing_review_status'] == 'pending'


def test_changing_global_frames_cannot_mask_a_frozen_target():
    plan, timeline, observations = fixture()
    for sample in observations['windows'][0].values():
        if isinstance(sample, dict):
            sample['elements']['request']['x'] = 162
    with pytest.raises(ServiceError, match='motion_expected_move_missing'):
        verify_observations(plan, timeline, observations)


def test_wrong_destination_is_rejected_even_when_motion_exists():
    plan, timeline, observations = fixture()
    observations['windows'][0]['after']['elements']['request']['x'] = 998
    with pytest.raises(ServiceError, match='motion_expected_move_missing'):
        verify_observations(plan, timeline, observations)


def test_midpoint_cannot_leave_the_planned_path():
    plan, timeline, observations = fixture()
    observations['windows'][0]['middle']['elements']['request']['y'] += 30
    with pytest.raises(ServiceError, match='motion_expected_move_missing'):
        verify_observations(plan, timeline, observations)


def test_wrong_plan_or_missing_non_target_objects_are_rejected():
    plan, timeline, observations = fixture()
    plan['title'] = '另一个计划'
    with pytest.raises(ServiceError):
        verify_observations(plan, timeline, observations)
    plan, timeline, observations = fixture()
    observations['windows'][0]['before']['elements']['unexpected'] = deepcopy(
        observations['windows'][0]['before']['elements']['request'])
    with pytest.raises(ServiceError, match='motion_observation_objects_missing'):
        verify_observations(plan, timeline, observations)


@pytest.mark.parametrize('failure', ['font', 'overflow', 'outside', 'wrong_time', 'wrong_actual_time',
                                  'wrong_protocol', 'wrong_fps', 'reverse', 'stale_timeline', 'missing_window', 'huge_number'])
def test_incomplete_or_misbound_observations_fail(failure):
    plan, timeline, observations = fixture()
    before = observations['windows'][0]['before']
    if failure == 'font':
        before['layout']['fontReady'] = False
    elif failure == 'overflow':
        before['elements']['request']['overflow'] = True
    elif failure == 'outside':
        before['elements']['request']['x'] = -40
    elif failure == 'wrong_time':
        before['time'] += 1
    elif failure == 'wrong_actual_time':
        before['actual_time'] += 1
    elif failure == 'wrong_protocol':
        observations['seek_protocol'] = 'pretend-hf-bridge'
    elif failure == 'wrong_fps':
        observations['render_fps'] = 30
    elif failure == 'reverse':
        observations['reverse_seek']['repeated_first_sha256'] = 'b' * 64
    elif failure == 'stale_timeline':
        observations['timeline_sha256'] = 'b' * 64
    elif failure == 'huge_number':
        before['elements']['request']['x'] = 10 ** 4000
    else:
        observations['windows'] = []
    with pytest.raises(ServiceError):
        verify_observations(plan, timeline, observations)


def test_snapshot_inputs_are_not_modified_by_review():
    args = fixture()
    original = deepcopy(args)
    verify_observations(*args)
    assert args == original


def v2_text_fixture(compiler='motion-compiler-2'):
    plan, timeline, observations = fixture()
    plan['elements'].append(dict(id='proof', kind='card', label='完整回答', detail='原记录继续保留', slot='source'))
    timeline.update(compiler=compiler, plan_sha256=sha256(plan))
    observations['timeline_sha256'] = sha256(timeline)
    for label in ('before', 'middle', 'after'):
        sample = observations['windows'][0][label]
        sample['elements']['proof'] = dict(opacity=1, x=72, y=292, width=300, height=156, overflow=False)
        for identity, state in sample['elements'].items():
            x, y, width, height = (state[k] for k in ('x', 'y', 'width', 'height'))
            area = dict(left=x + 14, top=y + 10, right=x + width - 14, bottom=y + height - 10)
            state['text_area'] = area
            keys = ('copy', 'label', 'detail') if identity == 'proof' else ('copy', 'label')
            state['text_boxes'] = {key: dict(x=x + 14, y=y + 12, width=width - 28, height=20,
                scrollWidth=width - 28, clientWidth=width - 28,
                scrollHeight=20, clientHeight=20, overflow=False) for key in keys}
    if compiler in {'motion-compiler-4', 'motion-compiler-5'}:
        add_initial_evidence(observations)
    return plan, timeline, observations


def add_initial_evidence(observations):
    initial = deepcopy(observations['windows'][0]['before'])
    initial.update(time=0, actual_time=0)
    observations['initial'] = initial
    observations['repeated_initial'] = deepcopy(initial)


@pytest.mark.parametrize('compiler', ['motion-compiler-2', 'motion-compiler-3', 'motion-compiler-4', 'motion-compiler-5'])
def test_versioned_text_checks_require_all_text_boxes_without_changing_v1_observations(compiler):
    assert verify_observations(*fixture())['technical_status'] == 'passed'
    assert verify_observations(*v2_text_fixture(compiler))['technical_status'] == 'passed'
    args = v2_text_fixture(compiler)
    del args[2]['windows'][0]['before']['elements']['proof']['text_boxes']['detail']
    with pytest.raises(ServiceError, match='motion_text_bounds_missing'):
        verify_observations(*args)


@pytest.mark.parametrize('key', ['copy', 'label', 'detail'])
@pytest.mark.parametrize('failure', ['above', 'below', 'left', 'right', 'scroll', 'reported_overflow'])
@pytest.mark.parametrize('compiler', ['motion-compiler-2', 'motion-compiler-3', 'motion-compiler-4', 'motion-compiler-5'])
def test_versioned_text_checks_reject_cropped_child_text_when_parent_claims_no_overflow(key, failure, compiler):
    args = v2_text_fixture(compiler)
    state = args[2]['windows'][0]['before']['elements']['proof']
    box, area = state['text_boxes'][key], state['text_area']
    if failure == 'above':
        box['y'] = area['top'] - 1
    elif failure == 'below':
        box['y'] = area['bottom'] - box['height'] + 1
    elif failure == 'left':
        box['x'] = area['left'] - 1
    elif failure == 'right':
        box['x'] = area['right'] - box['width'] + 1
    elif failure == 'scroll':
        box['scrollHeight'] = box['clientHeight'] + 1
    else:
        box['overflow'] = True
    assert state['overflow'] is False
    with pytest.raises(ServiceError, match='motion_text_overflow'):
        verify_observations(*args)


def draw_fixture(compiler='motion-compiler-4'):
    """Independent DOM samples for a single planned connector being drawn."""
    plan, _, observations = v2_text_fixture(compiler)
    beats = make_beats('请求到达。请求进入队列。执行节点开始工作。')
    plan['relations'] = [dict(id='link', source='request', target='proof', label='处理')]
    plan['actions'] = [dict(beat_id=beats['segments'][1]['id'], target='link', kind='draw',
                            placement='middle', pace='slow', to_slot=None)]
    timeline = resolve_timeline(plan, beats, estimated_alignment(beats, 'a' * 64, 15),
                                compiler_version=compiler)
    observations['timeline_sha256'] = sha256(timeline)
    times = action_windows(timeline)[0]
    elements = deepcopy(observations['windows'][0]['before']['elements'])
    for label, opacity, dash, marker in [('before', 0, 1, 0), ('middle', .5, .5, 0), ('after', 1, 0, 1)]:
        sample = observations['windows'][0][label]
        sample.update(time=times[label], actual_time=times[label])
        sample['elements'] = deepcopy(elements)
        sample['relations']['link'] = dict(length=200, strokeDashoffset=dash,
            opacity=opacity, labelOpacity=opacity, markerOpacity=marker, baseCount=0)
        sample['layout']['relations'] = [dict(id='link', opacity=opacity,
            x=200, y=200, width=36, height=25, overflow=False)]
        if compiler == 'motion-compiler-5':
            sample['layout']['relations'] = [dict(id='link', text='处理', opacity=opacity,
                x=400, y=200, width=54, height=34, overflow=False,
                plate=dict(x=400, y=200, width=54, height=34),
                ink=dict(x=409, y=209, width=36, height=20), overlapsNodes=[], overlapsLabels=[])]
        if compiler not in {'motion-compiler-4', 'motion-compiler-5'}:
            sample['relations']['link']['opacity'] = 1
            del sample['relations']['link']['baseCount']
            del sample['relations']['link']['labelOpacity']
    if compiler in {'motion-compiler-4', 'motion-compiler-5'}:
        add_initial_evidence(observations)
    return plan, timeline, observations


@pytest.mark.parametrize('compiler', ['motion-compiler-1', 'motion-compiler-2', 'motion-compiler-3',
                                     'motion-compiler-4', 'motion-compiler-5'])
def test_draw_review_preserves_legacy_evidence_and_admits_hidden_v4_connectors(compiler):
    assert verify_observations(*draw_fixture(compiler))['technical_status'] == 'passed'


def test_v4_draw_review_accepts_fade_progress_alongside_fractional_dash_progress():
    args = draw_fixture()
    assert args[2]['windows'][0]['middle']['relations']['link']['opacity'] == .5
    assert verify_observations(*args)['technical_status'] == 'passed'


@pytest.mark.parametrize('when', ['before', 'middle', 'after', 'initial', 'repeated_initial'])
@pytest.mark.parametrize('failure', ['missing_plate', 'missing_ink', 'missing_label', 'ink_outside',
                                     'reported_overflow', 'reported_overlap', 'actual_node_overlap'])
def test_v5_requires_real_label_bounds_and_no_collision_at_every_seek(when, failure):
    args = draw_fixture('motion-compiler-5')
    sample = args[2][when] if when in {'initial', 'repeated_initial'} else args[2]['windows'][0][when]
    label = sample['layout']['relations'][0]
    if failure == 'missing_plate':
        del label['plate']
    elif failure == 'missing_ink':
        del label['ink']
    elif failure == 'missing_label':
        sample['layout']['relations'] = []
    elif failure == 'ink_outside':
        label['ink']['x'] = label['plate']['x'] - 1
    elif failure == 'reported_overflow':
        label['overflow'] = True
    elif failure == 'reported_overlap':
        label['overlapsLabels'] = ['other']
    else:
        # A false "no overlap" flag must not bypass the measured rectangles.
        label['plate'].update(x=162, y=220)
        label['ink'].update(x=171, y=229)
    with pytest.raises(ServiceError):
        verify_observations(*args)


def test_v5_empty_relation_text_has_no_invented_ink_bounds():
    args = draw_fixture('motion-compiler-5')
    args[0]['relations'][0]['label'] = ''
    args[1]['plan_sha256'] = sha256(args[0])
    args[2]['timeline_sha256'] = sha256(args[1])
    samples = [args[2]['initial'], args[2]['repeated_initial'],
               *(args[2]['windows'][0][key] for key in ('before', 'middle', 'after'))]
    for sample in samples:
        label = sample['layout']['relations'][0]
        label.update(text='', width=0, height=0, ink=None)
        label['plate'].update(width=0, height=0)
    assert verify_observations(*args)['technical_status'] == 'passed'


def highlight_fixture(*, reveal_together=False, hidden_during_highlights=False):
    plan, _, observations = v2_text_fixture('motion-compiler-5')
    beats = make_beats('请求到达。请求进入队列。执行节点开始工作。')
    def action(beat, kind):
        return dict(beat_id=beats['segments'][beat]['id'], target='proof', kind=kind,
                    placement='start', pace='normal', to_slot=None)
    plan['actions'] = [action(0, 'highlight'), action(1, 'highlight')]
    if reveal_together:
        plan['actions'].append(action(0, 'reveal'))
    if hidden_during_highlights:
        plan['actions'].append(action(2, 'reveal'))
    timeline = resolve_timeline(plan, beats, estimated_alignment(beats, 'a' * 64, 15),
                                compiler_version='motion-compiler-5')
    observations['timeline_sha256'] = sha256(timeline)
    base = deepcopy(observations['windows'][0]['before'])
    def colors(sample, *, opacity, pulse, active):
        sample['elements']['proof'].update(opacity=opacity, highlightWidth=pulse,
            highlightColor='rgb(121, 228, 213)',
            borderColor='rgb(121, 228, 213)' if active else 'rgb(51, 68, 94)',
            backgroundColor='rgb(33, 67, 78)' if active else 'rgb(22, 36, 58)')
    initial = deepcopy(base)
    initial.update(time=0, actual_time=0)
    colors(initial, opacity=0 if reveal_together or hidden_during_highlights else 1, pulse=0, active=False)
    observations['initial'] = initial
    observations['repeated_initial'] = deepcopy(initial)
    observations['windows'] = []
    for item, times in zip(timeline['actions'], action_windows(timeline)):
        row = dict(ordinal=item['ordinal'])
        first_window = item['beat_id'] == beats['segments'][0]['id']
        for key, index, pulse in [('before', 0, 0), ('middle', 1, 8), ('after', 2, 0)]:
            sample = deepcopy(base)
            sample.update(time=times[key], actual_time=times[key], frame_sha256='abc'[index] * 64)
            visible = 1
            if hidden_during_highlights:
                visible = [0, .5, 1][index] if item['kind'] == 'reveal' else 0
            elif reveal_together and first_window:
                visible = [0, .5, 1][index]
            actual_pulse = pulse if item['kind'] == 'highlight' or first_window and reveal_together else 0
            colors(sample, opacity=visible, pulse=actual_pulse, active=not first_window or index > 0)
            row[key] = sample
        observations['windows'].append(row)
    return plan, timeline, observations


@pytest.mark.parametrize('reveal_together', [False, True])
def test_v5_repeated_highlights_use_real_pulses_and_accept_concurrent_reveal(reveal_together):
    args = highlight_fixture(reveal_together=reveal_together)
    second = next(row for row in args[2]['windows'] if row['ordinal'] == 1)
    assert second['before']['elements']['proof']['borderColor'] == second['after']['elements']['proof']['borderColor']
    report = verify_observations(*args)
    assert report['technical_status'] == 'passed'
    assert report['timing_review_status'] == 'pending'


def test_v5_a_target_hidden_throughout_highlights_still_fails():
    with pytest.raises(ServiceError, match='motion_invisible_highlight'):
        verify_observations(*highlight_fixture(hidden_during_highlights=True))


@pytest.mark.parametrize('when', ['before', 'middle', 'after', 'initial', 'repeated_initial'])
def test_v5_pulse_cannot_be_missing_or_leak_outside_its_window(when):
    args = highlight_fixture()
    sample = args[2][when] if when in {'initial', 'repeated_initial'} else args[2]['windows'][0][when]
    sample['elements']['proof']['highlightWidth'] = 0 if when == 'middle' else 8
    with pytest.raises(ServiceError, match='motion_expected_highlight_missing'):
        verify_observations(*args)


@pytest.mark.parametrize('key', ['opacity', 'labelOpacity', 'markerOpacity'])
@pytest.mark.parametrize('label,value', [('before', .001), ('before', 1),
                                        ('after', .97), ('after', 0)])
def test_v4_connectors_labels_and_arrowheads_cannot_leak_before_or_remain_faded_after_draw(key, label, value):
    args = draw_fixture()
    args[2]['windows'][0][label]['relations']['link'][key] = value
    with pytest.raises(ServiceError):
        verify_observations(*args)


@pytest.mark.parametrize('key', ['opacity', 'labelOpacity', 'markerOpacity'])
@pytest.mark.parametrize('value', [-.001, 1.001, None, '0.5', True])
def test_v4_connector_midpoint_evidence_requires_finite_bounded_opacity(key, value):
    args = draw_fixture()
    args[2]['windows'][0]['middle']['relations']['link'][key] = value
    with pytest.raises(ServiceError):
        verify_observations(*args)


@pytest.mark.parametrize('label', ['before', 'middle', 'after'])
@pytest.mark.parametrize('value', [None, False, 1, -1, '0'])
def test_v4_review_requires_absence_of_base_paths_as_explicit_browser_evidence(label, value):
    args = draw_fixture()
    state = args[2]['windows'][0][label]['relations']['link']
    if value is None:
        del state['baseCount']
    else:
        state['baseCount'] = value
    with pytest.raises(ServiceError):
        verify_observations(*args)


@pytest.mark.parametrize('key', ['opacity', 'labelOpacity', 'markerOpacity'])
@pytest.mark.parametrize('label', ['before', 'middle', 'after'])
def test_v4_review_requires_all_connector_visibility_fields_in_every_sample(key, label):
    args = draw_fixture()
    del args[2]['windows'][0][label]['relations']['link'][key]
    with pytest.raises(ServiceError):
        verify_observations(*args)


@pytest.mark.parametrize('label', ['initial', 'repeated_initial'])
@pytest.mark.parametrize('failure', ['missing', 'time', 'actual_time', 'frame_sha256',
                                   'line_visible', 'label_visible', 'marker_visible', 'base_path'])
def test_v4_zero_frame_and_reverse_seek_evidence_cannot_be_omitted_or_show_connectors(label, failure):
    args = draw_fixture()
    observations = args[2]
    if failure == 'missing':
        del observations[label]
    elif failure in {'time', 'actual_time'}:
        observations[label][failure] = .04
    elif failure == 'frame_sha256':
        observations[label]['frame_sha256'] = 'd' * 64
    else:
        key = {'line_visible': 'opacity', 'label_visible': 'labelOpacity',
               'marker_visible': 'markerOpacity', 'base_path': 'baseCount'}[failure]
        observations[label]['relations']['link'][key] = 1
    with pytest.raises(ServiceError):
        verify_observations(*args)


def with_undrawn_relation():
    plan, timeline, observations = draw_fixture()
    plan['relations'].append(dict(id='future', source='request', target='proof', label='以后再展示'))
    timeline['plan_sha256'] = sha256(plan)
    observations['timeline_sha256'] = sha256(timeline)
    samples = [observations['initial'], observations['repeated_initial'],
               *(observations['windows'][0][label] for label in ('before', 'middle', 'after'))]
    for sample in samples:
        sample['relations']['future'] = dict(length=200, strokeDashoffset=1,
            opacity=0, labelOpacity=0, markerOpacity=0, baseCount=0)
        sample['layout']['relations'].append(dict(id='future', opacity=0,
            x=200, y=250, width=120, height=25, overflow=False))
    return plan, timeline, observations


def test_v4_relations_without_draw_actions_stay_hidden_while_other_relations_animate():
    assert verify_observations(*with_undrawn_relation())['technical_status'] == 'passed'


@pytest.mark.parametrize('label', ['before', 'middle', 'after'])
@pytest.mark.parametrize('key', ['opacity', 'labelOpacity', 'markerOpacity', 'baseCount'])
def test_v4_review_checks_every_relation_in_each_window_including_undrawn_non_targets(label, key):
    args = with_undrawn_relation()
    args[2]['windows'][0][label]['relations']['future'][key] = 1
    with pytest.raises(ServiceError):
        verify_observations(*args)


def test_v4_connector_expected_states_match_pinned_gsap_across_delays_tips_redraws_and_reverse_seeks():
    gsap_path = Path('D:/github/research/.cache/hyperframes/registry/blocks/orbit-card/assets/gsap-3.14.2.min.js')
    node = shutil.which('node')
    if not node or not gsap_path.is_file():
        pytest.skip('Offline GSAP 3.14.2 reference is unavailable')
    plan = dict(relations=[dict(id=identity) for identity in ('link', 'future', 'undrawn')])
    timeline = dict(actions=[dict(kind='draw', target=identity, start=start, duration=duration)
        for identity, start, duration in [('link', 1, 1), ('future', 2.4, .65), ('link', 4, .35)]])
    times = [0, 1, 1.04166667, 1.25, 1.5, 1.85, 1.925, 2, 2.4, 2.5625,
             2.95, 3.001, 4, 4.175, 4.35, 5, 0, 1.5]
    script = r"""
const fs=require('node:fs'),vm=require('node:vm');
const sandbox={exports:{},module:{},setTimeout,clearTimeout,console};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),sandbox);
const gsap=sandbox.exports.gsap;if(gsap.version!=='3.14.2')throw Error('reference_version');
const d=JSON.parse(fs.readFileSync(0,'utf8')),nodes={};
for(const r of d.plan.relations)nodes[r.id]={line:{opacity:0,strokeDashoffset:1},label:{opacity:0},marker:{opacity:0}};
const tl=gsap.timeline({paused:true,defaults:{ease:'power2.inOut'}});
for(const a of d.timeline.actions){const n=nodes[a.target],duration=a.duration,when=a.start;
tl.fromTo(n.line,{strokeDashoffset:1,opacity:0},{strokeDashoffset:0,opacity:1,duration,autoRound:false,immediateRender:false},when);
tl.fromTo(n.label,{opacity:0},{opacity:1,duration,immediateRender:false},when);
tl.fromTo(n.marker,{opacity:0},{opacity:1,duration:duration*.15,immediateRender:false},when+duration*.85);}
tl.set({},{},6);
const rows=d.times.map(time=>{tl.seek(time,false);return Object.fromEntries(Object.entries(nodes).map(([id,n])=>[id,
{opacity:n.line.opacity,strokeDashoffset:n.line.strokeDashoffset,labelOpacity:n.label.opacity,markerOpacity:n.marker.opacity}]));});
console.log(JSON.stringify(rows));gsap.ticker.sleep();
"""
    result = subprocess.run([node, '-e', script, str(gsap_path)],
        input=json.dumps(dict(plan=plan, timeline=timeline, times=times)),
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    for time, actual in zip(times, json.loads(result.stdout)):
        expected = _connector_states(plan, timeline, time)
        for identity, state in expected.items():
            for property_name, value in state.items():
                assert abs(actual[identity][property_name] - value) < .00001, (time, identity, property_name)


def demonstration_fixture(kind='document', action_kind='move'):
    beats = make_beats('先看这条记录。它移动到左侧。最后查看解释。')
    slots = {'document': ('log_center', 'log_left'),
             'record': ('answer_center', 'answer_right'),
             'attempt': ('attempt_center', 'attempt_left')}
    source, destination = slots[kind]
    plan = dict(schema='video.motion-plan.v1', template='demonstration', title='一份记录的演示',
                takeaway='移动窗口，保留原记录。', relations=[],
                elements=[dict(id='source', kind=kind, label='原记录', detail='', slot=source),
                          dict(id='terminal', kind='terminal', label='上下文', detail='', slot='context_right'),
                          dict(id='explanation', kind='label', label='说明', detail='', slot='result_left')],
                actions=[dict(beat_id=beats['segments'][1]['id'], target='source', kind=action_kind,
                              placement='middle', pace='slow', to_slot=destination if action_kind == 'move' else None)])
    timeline = resolve_demo_timeline(plan, beats, estimated_alignment(beats, 'a' * 64, 15))
    times = action_windows(timeline)[0]
    x, y, width, height = DEMO_LAYOUT[source]
    dx, dy = DEMO_LAYOUT[destination][:2]
    row = dict(ordinal=0)
    for label, progress, stamp in [('before', 0, 'a'), ('middle', .5, 'b'), ('after', 1, 'c')]:
        elements = {}
        for element in plan['elements']:
            ex, ey, ew, eh = DEMO_LAYOUT[element['slot']]
            elements[element['id']] = dict(opacity=1,
                x=ex, y=ey, width=ew, height=eh, overflow=False)
        if action_kind == 'move':
            elements['source'].update(x=x + (dx - x) * progress, y=y + (dy - y) * progress)
        else:
            elements['source']['opacity'] = progress if action_kind == 'reveal' else 1 - progress
        row[label] = dict(time=times[label], actual_time=times[label], frame_sha256=stamp * 64,
                         elements=elements, relations={}, layout=dict(fontReady=True, relations=[],
                         title=dict(x=72, y=74, width=1136, height=48, overflow=False),
                         takeaway=dict(x=72, y=672, width=1136, height=28, overflow=False)))
    observations = dict(schema='video.motion-observations.v1', timeline_sha256=sha256(timeline),
        seek_protocol='hyperframes-core-renderSeek-exact', render_fps=24,
        windows=[row], reverse_seek=dict(first_sha256='a' * 64, last_sha256='c' * 64,
                                        repeated_first_sha256='a' * 64, repeat_state_equal=True))
    return plan, timeline, observations


@pytest.mark.parametrize('kind', ['document', 'record', 'attempt'])
def test_demonstration_checks_finite_same_size_window_moves(kind):
    report = verify_observations(*demonstration_fixture(kind))
    assert report['technical_status'] == 'passed'
    assert report['visual_comprehension'] == report['voice_naturalness'] == 'pending_user_review'


@pytest.mark.parametrize('kind', ['reveal', 'hide'])
def test_demonstration_requires_real_opacity_changes(kind):
    args = demonstration_fixture(action_kind=kind)
    verify_observations(*args)
    for label in ('before', 'middle', 'after'):
        args[2]['windows'][0][label]['elements']['source']['opacity'] = 1
    with pytest.raises(ServiceError, match='motion_demo_opacity_mismatch'):
        verify_observations(*args)


@pytest.mark.parametrize('label', ['before', 'middle', 'after'])
@pytest.mark.parametrize('failure', ['outside_hidden', 'resize_hidden', 'resize_moving', 'fixed_position',
                                   'invisible', 'wrong_time', 'off_path'])
def test_demonstration_checks_every_window_even_hidden_non_targets(label, failure):
    plan, timeline, observations = demonstration_fixture()
    sample = observations['windows'][0][label]
    if failure == 'outside_hidden':
        sample['elements']['explanation']['x'] = 1300
    elif failure == 'resize_hidden':
        sample['elements']['explanation']['height'] += 5
    elif failure == 'resize_moving':
        sample['elements']['source']['width'] += 10
    elif failure == 'fixed_position':
        sample['elements']['terminal']['x'] -= 10
    elif failure == 'invisible':
        sample['elements']['source']['opacity'] = 0
    elif failure == 'wrong_time':
        sample['actual_time'] += .2
    else:
        sample['elements']['source']['x'] += 10
    with pytest.raises(ServiceError):
        verify_observations(plan, timeline, observations)


@pytest.mark.parametrize('failure', ['compiler', 'verb', 'destination', 'cross_kind'])
def test_demonstration_cannot_use_unplanned_compilers_actions_or_slots(failure):
    plan, timeline, observations = demonstration_fixture()
    if failure == 'compiler':
        timeline['compiler'] = 'motion-compiler-1'
    elif failure == 'verb':
        timeline['actions'][0]['kind'] = 'highlight'
    elif failure == 'destination':
        timeline['actions'][0]['to_slot'] = 'log_center'
    else:
        plan['actions'][0]['to_slot'] = 'answer_right'
        timeline['actions'][0]['to_slot'] = 'answer_right'
        timeline['plan_sha256'] = sha256(plan)
    observations['timeline_sha256'] = sha256(timeline)
    with pytest.raises(ServiceError):
        verify_observations(plan, timeline, observations)


def copy_demonstration_fixture():
    """Independent endpoint samples; actions here never overlap in time."""
    beats = make_beats('先把原记录移到日志里。复制后带进上下文。最后解释两者区别。')
    ids = [row['id'] for row in beats['segments']]
    plan = dict(schema='video.motion-plan.v1', template='demonstration', title='复制记录，保留原文',
        takeaway='原记录仍留在左侧日志中。', relations=[], elements=[
            dict(id='answer', kind='record', label='完整回答', detail='', slot='answer_center'),
            dict(id='copy', kind='record', label='完整回答', detail='', slot='answer_left'),
            dict(id='context', kind='terminal', label='下一轮上下文', detail='', slot='context_right'),
            dict(id='note', kind='label', label='原记录保留', detail='', slot='result_left')],
        actions=[dict(beat_id=beat, target=target, kind=kind, placement=placement, pace='slow', to_slot=slot)
                 for beat, target, kind, placement, slot in [
                     (ids[0], 'answer', 'move', 'middle', 'answer_left'),
                     (ids[1], 'copy', 'reveal', 'start', None),
                     (ids[1], 'copy', 'move', 'middle', 'answer_right'),
                     (ids[1], 'context', 'reveal', 'end', None),
                     (ids[2], 'note', 'reveal', 'middle', None)]])
    timeline = resolve_demo_timeline(plan, beats, estimated_alignment(beats, 'a' * 64, 30))
    current = {}
    for e in plan['elements']:
        x, y, width, height = DEMO_LAYOUT[e['slot']]
        current[e['id']] = dict(x=x, y=y, width=width, height=height, overflow=False,
                                opacity=1 if e['id'] == 'answer' else 0)
    windows = []
    for action, times in zip(timeline['actions'], action_windows(timeline)):
        identity = action['target']
        before, middle, after = deepcopy(current), deepcopy(current), deepcopy(current)
        if action['kind'] == 'move':
            x, y = DEMO_LAYOUT[action['to_slot']][:2]
            middle[identity].update(x=(current[identity]['x'] + x) / 2,
                                    y=(current[identity]['y'] + y) / 2)
            after[identity].update(x=x, y=y)
        else:
            middle[identity]['opacity'] = .5
            after[identity]['opacity'] = 1
        row = dict(ordinal=action['ordinal'])
        for label, elements, stamp in [('before', before, 'a'), ('middle', middle, 'b'), ('after', after, 'c')]:
            row[label] = dict(time=times[label], actual_time=times[label], frame_sha256=stamp * 64,
                elements=elements, relations={}, layout=dict(fontReady=True, relations=[],
                title=dict(x=72, y=82, width=1136, height=58, overflow=False),
                takeaway=dict(x=72, y=672, width=1136, height=32, overflow=False)))
        windows.append(row)
        current = after
    observations = dict(schema='video.motion-observations.v1', timeline_sha256=sha256(timeline),
        seek_protocol='hyperframes-core-renderSeek-exact', render_fps=24, windows=windows,
        reverse_seek=dict(first_sha256='a' * 64, last_sha256='c' * 64,
                          repeated_first_sha256='a' * 64, repeat_state_equal=True))
    return plan, timeline, observations


def test_demo_copy_keeps_source_left_and_visible_through_all_later_windows():
    args = copy_demonstration_fixture()
    report = verify_observations(*args)
    assert report['technical_status'] == 'passed' and len(report['checks']) == 5
    for row in args[2]['windows'][1:]:
        for label in ('before', 'middle', 'after'):
            assert row[label]['elements']['answer']['x'] == 112
            assert row[label]['elements']['answer']['opacity'] == 1


@pytest.mark.parametrize('window', [1, 2, 3, 4])
@pytest.mark.parametrize('label', ['before', 'middle', 'after'])
@pytest.mark.parametrize('failure', ['source_disappears', 'source_moves_right'])
def test_demo_cannot_pass_when_original_changes_after_its_own_action(window, label, failure):
    plan, timeline, observations = copy_demonstration_fixture()
    state = observations['windows'][window][label]['elements']['answer']
    if failure == 'source_disappears':
        state['opacity'] = 0
    else:
        state['x'] = 728
    with pytest.raises(ServiceError, match='motion_demo_(opacity|position)_mismatch'):
        verify_observations(plan, timeline, observations)


def test_demo_does_not_reveal_or_move_a_future_copy_early():
    for property_name, value in [('opacity', 1), ('x', 420)]:
        plan, timeline, observations = copy_demonstration_fixture()
        observations['windows'][0]['middle']['elements']['copy'][property_name] = value
        with pytest.raises(ServiceError, match='motion_demo_(opacity|position)_mismatch'):
            verify_observations(plan, timeline, observations)


def test_demo_expected_states_match_real_pinned_gsap_on_overlapping_actions():
    # Offline integration when the reviewed upstream checkout is available.
    # Core retention/rejection tests above do not depend on this checkout.
    gsap_path = Path('D:/github/research/.cache/hyperframes/registry/blocks/orbit-card/assets/gsap-3.14.2.min.js')
    node = shutil.which('node')
    if not node or not gsap_path.is_file():
        pytest.skip('Offline GSAP 3.14.2 reference is unavailable')
    plan, timeline, _ = copy_demonstration_fixture()
    # Check off-midpoint values while another element is still in a slow move.
    timeline['actions'][0].update(start=1, duration=1)
    timeline['actions'][1].update(start=1, duration=.35)
    timeline['actions'][2].update(start=2.4, duration=.65)
    timeline['actions'][3].update(start=1, duration=.65)
    timeline['actions'][4].update(start=3.5, duration=1)
    timeline['actions'].sort(key=lambda a: (a['start'], a['ordinal']))
    times = [0, 1, 1.04166667, 1.175, 1.325, 1.5, 1.65, 1.9, 2, 2.4, 2.5625, 2.8875, 3.05, 4.5, 1.175]
    data = dict(plan=plan, timeline=timeline, layout=DEMO_LAYOUT, times=times)
    script = r"""
const fs=require('node:fs'),vm=require('node:vm');
const sandbox={exports:{},module:{},setTimeout,clearTimeout,console};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),sandbox);
const gsap=sandbox.exports.gsap;
if(gsap.version!=='3.14.2')throw Error('reference_version');
const d=JSON.parse(fs.readFileSync(0,'utf8')),nodes={},position={},opacity={},first=new Map();
for(const a of d.timeline.actions)if(a.kind!=='move'&&!first.has(a.target))first.set(a.target,a.kind);
for(const e of d.plan.elements){const [x,y]=d.layout[e.slot];nodes[e.id]={x,y,opacity:first.get(e.id)==='reveal'?0:1};position[e.id]={x,y};opacity[e.id]=nodes[e.id].opacity;}
const tl=gsap.timeline({paused:true,defaults:{ease:'power2.inOut'}});
for(const a of d.timeline.actions){const target=nodes[a.target];if(a.kind==='move'){const [x,y]=d.layout[a.to_slot];tl.fromTo(target,position[a.target],{x,y,duration:a.duration,immediateRender:false},a.start);position[a.target]={x,y};}else{const next=a.kind==='reveal'?1:0;tl.fromTo(target,{opacity:opacity[a.target]},{opacity:next,duration:a.duration,immediateRender:false},a.start);opacity[a.target]=next;}}
tl.set({},{},5);
const rows=d.times.map(time=>{tl.seek(time,false);return Object.fromEntries(Object.entries(nodes).map(([id,n])=>[id,{x:n.x,y:n.y,opacity:n.opacity}]));});
console.log(JSON.stringify(rows));gsap.ticker.sleep();
"""
    result = subprocess.run([node, '-e', script, str(gsap_path)], input=json.dumps(data),
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    for time, actual in zip(times, json.loads(result.stdout)):
        expected = _demo_expected_states(plan, timeline, time)
        for identity in expected:
            for property_name in ('x', 'y', 'opacity'):
                assert abs(actual[identity][property_name] - expected[identity][property_name]) < .00001
