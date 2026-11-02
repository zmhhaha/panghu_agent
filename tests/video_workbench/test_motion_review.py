"""Semantic capture failures that a valid, changing MP4 alone cannot detect."""
from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError
from video_workbench.motion import make_beats, example_plan, estimated_alignment, resolve_timeline, sha256, action_windows
from video_workbench.motion_review import verify_observations


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
