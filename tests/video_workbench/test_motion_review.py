"""Semantic capture failures that a valid, changing MP4 alone cannot detect."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from agent_service.contracts import ServiceError
from video_workbench.motion import make_beats, example_plan, estimated_alignment, resolve_timeline, sha256, action_windows
from video_workbench.motion_review import _demo_expected_states, verify_observations
from video_workbench.motion_demo import DEMO_LAYOUT, resolve_demo_timeline


def fixture():
    beats = make_beats('请求到达。请求进入队列。执行节点开始工作。')
    plan = example_plan(beats)
    plan['elements'] = [row for row in plan['elements'] if row['id'] == 'request']
    plan['relations'] = []
    plan['actions'] = [dict(beat_id=beats['segments'][0]['id'], target='request',
                            kind='move', placement='middle', pace='slow', to_slot='token_queue')]
    timeline = resolve_timeline(plan, beats, estimated_alignment(beats, 'a' * 64, 15))
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
