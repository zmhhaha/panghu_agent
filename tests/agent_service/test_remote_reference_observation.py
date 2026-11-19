"""Remote read requests preserve role scope and do not fabricate observation."""
from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError, digest
from agent_service.profiles import video_picture_learning as picture
from agent_service.profiles import video_reference_learning as video
from agent_service import reference_observation_contract as observation


def document(protocol, mode='create'):
    source = dict(reference_id='remote-1', source_type='video-url',
        source_url='https://www.bilibili.com/video/BV1BxWu6SESe/',
        source_snapshot_sha256='a' * 64, source_identity_scope='provider_metadata',
        start_seconds=21, end_seconds=28, width=1920, height=1080, duration_seconds=90)
    frame = dict(frame_id='reference-1', file_name='reference.jpg', sha256='b' * 64,
        purpose='reference', source_snapshot_sha256='a' * 64,
        source_identity_scope='provider_metadata', requested_time=21, actual_pts=21,
        clip_time=0, pts_ticks=504, time_base=[1, 24])
    frames = [frame]
    if mode != 'create' and mode != 'analyze':
        frames.append(dict(file_name='output.jpg', sha256='c' * 64, purpose='output',
            media_sha256='d' * 64, requested_time=0, actual_pts=0, clip_time=0))
    constraints = dict(width=1280, height=720, allowed_engines=['canvas'])
    if protocol is video:
        constraints.update(fps=24, duration_seconds=7)
    return dict(schema=protocol.INPUT_SCHEMA, mode=mode, project_id='project-1',
        experiment_id='experiment-1', work_kind='reproduction', goal='Study ink edges.',
        criteria=[], constraints=constraints, observation=dict(frames=frames),
        base_source=None, feedback='', knowledge_snapshot=None, prior_review=None,
        learned_method=None, reference_sources=[source])


def request_result(protocol, context):
    return dict(schema=protocol.RESULT_SCHEMA, input_sha256=digest(context), mode=context['mode'],
        source=None, review=None, transfer_task=None, method=None,
        evidence_scope='sampled_frames_only' if protocol is video else 'static_only',
        observation_request=dict(reference_id='remote-1', times=[22, 24], quality='detail',
            crop=dict(x=.2, y=.1, width=.4, height=.5), reason='Inspect the irregular dry brush edge.'))


@pytest.mark.parametrize('protocol', [picture, video])
@pytest.mark.parametrize('mode', ['create', 'review'])
def test_remote_identity_and_scoped_stopped_handoff_are_valid(protocol, mode):
    context = document(protocol, mode)
    assert protocol.validate_input(context) == context
    result = request_result(protocol, context)
    assert protocol.validate_result(result, digest(context), context) == result
    # No invented authored source or review accompanies the request.
    assert result['source'] is result['review'] is None


def test_source_analysis_can_request_detail_without_creating_an_animation():
    context = document(video, 'analyze')
    result = request_result(video, context)
    assert video.validate_result(result, digest(context), context) == result


@pytest.mark.parametrize('protocol', [picture, video])
@pytest.mark.parametrize('changes', [
    {'reference_id': 'another-owner-reference'}, {'times': [28]}, {'times': [20.9]},
    {'times': [float('nan')]}, {'times': [24, 22]}, {'times': [22, 22]},
    {'times': [21 + i / 10 for i in range(9)]}, {'quality': '4k-claimed'},
    {'crop': {'x': .9, 'y': 0, 'width': .2, 'height': 1}},
    {'source_url': 'http://127.0.0.1/private'},
])
def test_request_cannot_escape_the_frozen_source_or_bounds(protocol, changes):
    context = document(protocol)
    result = request_result(protocol, context)
    result['observation_request'].update(changes)
    with pytest.raises(ServiceError):
        protocol.validate_result(result, digest(context), context)


@pytest.mark.parametrize('protocol', [picture, video])
def test_descriptor_hash_is_never_a_complete_media_hash(protocol):
    context = document(protocol)
    context['observation']['frames'][0]['media_sha256'] = 'a' * 64
    with pytest.raises(ServiceError, match='identity_invalid'):
        protocol.validate_input(context)
    del context['observation']['frames'][0]['media_sha256']
    context['observation']['frames'][0]['source_snapshot_sha256'] = 'e' * 64
    with pytest.raises(ServiceError, match='identity_invalid'):
        protocol.validate_input(context)


@pytest.mark.parametrize('protocol', [picture, video])
def test_request_limit_cannot_restart_the_experiment_budget(protocol):
    context = document(protocol)
    request = request_result(protocol, context)['observation_request']
    context['observation_request_history'] = [dict(request_id=f'request-{i}',
        reference_id='remote-1', request=deepcopy(request), frame_ids=[f'extra-{i}'],
        source_snapshot_sha256='a' * 64, scope='sampled_frames_only') for i in range(4)]
    result = request_result(protocol, context)
    with pytest.raises(ServiceError, match='request_limit'):
        protocol.validate_result(result, digest(context), context)


@pytest.mark.parametrize('protocol', [picture, video])
def test_a_read_request_cannot_claim_a_review_was_completed(protocol):
    context = document(protocol, 'review')
    result = request_result(protocol, context)
    result['review'] = dict(score=100, summary='Not actually observed.', issues=[], stop_recommended=True)
    with pytest.raises(ServiceError, match='mixed_result'):
        protocol.validate_result(result, digest(context), context)


def test_pts_metadata_must_match_its_time_base():
    context = document(video)
    context['observation']['frames'][0]['pts_ticks'] = 505
    with pytest.raises(ServiceError):
        video.validate_input(context)


def test_transfer_cannot_load_the_original_remote_source():
    context = document(picture)
    context['work_kind'] = 'transfer'
    context['observation']['frames'] = []
    with pytest.raises(ServiceError, match='transfer_forbidden'):
        picture.validate_input(context)


def test_build_prompts_explain_asynchronous_handoff_without_new_network_tools():
    for profile in (picture.VideoPictureLearningProfile(), video.VideoReferenceLearningProfile()):
        prompt = profile.build_prompt(dict(input_sha256='f' * 64))
        assert 'observation_request' in prompt
        assert 'does NOT synchronously return pixels' in prompt
        assert profile.required_tools == ('read_inputs', 'skill_search', 'skill_read', 'write')
