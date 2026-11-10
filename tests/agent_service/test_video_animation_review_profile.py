"""Real local-frame evidence, scope and provenance boundaries for visual Agent roles."""
import base64
from copy import deepcopy
import hashlib
import json
import os
import shutil
from types import SimpleNamespace

import pytest

from agent_service.contracts import InlineFile, ServiceError, canonical_bytes
from agent_service.engines import StopEvidence
from agent_service.profiles import implementation
from agent_service.profiles import video_animation_review as visual


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aS1sAAAAASUVORK5CYII=')
ATTEMPT = 'visual-agent-attempt'


def input_document(review=True):
    document = dict(schema=visual.REVIEW_INPUT_SCHEMA if review else visual.OBSERVE_INPUT_SCHEMA,
        clip_id='arbitrary-expression-fragment', goal='Keep the moving object traceable through a crossing.',
        media=dict(sha256='a' * 64, duration_seconds=19, width=1280, height=720),
        observation=dict(coverage='sampled_frames', audio_available=False,
            windows=[dict(start_seconds=0, end_seconds=4)],
            frames=[dict(frame_id=f'frame-{index}', file_name=f'frame-{index}.png',
                         time_seconds=time, sha256=hashlib.sha256(PNG).hexdigest())
                    for index, time in ((1, .5), (2, 1.5))],
            limitations=['Only a sampled window; the remaining 15 seconds were not observed.']),
        prompt_revision='review-r1')
    if review:
        document['source_sha256'] = 'b' * 64
    return document


def opinion(review=True):
    result = dict(summary='A visible overlap makes the two objects hard to distinguish.',
                  limitations=['Sampled frames cannot establish continuous playback smoothness.'])
    if review:
        result['issues'] = [dict(issue_id='crossing-overlap', severity='major', kind='motion',
            start_seconds=.5, end_seconds=1.5, frame_ids=['frame-1', 'frame-2'],
            problem='The moving objects overlap at the crossing.',
            change_target='Separate their trajectories while preserving identity and timing.')]
    else:
        result['candidates'] = [dict(candidate_id='crossing-fragment', title='Trackable crossing',
            start_seconds=.5, end_seconds=1.5, frame_ids=['frame-1', 'frame-2'],
            observable_mechanism='The object remains in the same region across the supplied frames.',
            adaptation_ideas=['Use a spatial crossing to explain a change of ownership.'])]
    return result


def prepare(tmp_path, review=True, document=None, profile_name=None):
    profile = implementation(profile_name or (visual.REVIEW_PROFILE if review else visual.OBSERVE_PROFILE))
    document = document or input_document(review)
    raw = canonical_bytes(document)
    root = tmp_path / 'inputs'
    root.mkdir()
    (root / profile.input_name).write_bytes(raw)
    inputs = [InlineFile.from_bytes(profile.input_name, raw)]
    for frame in document['observation']['frames']:
        (root / frame['file_name']).write_bytes(PNG)
        inputs.append(InlineFile.from_bytes(frame['file_name'], PNG))
    (tmp_path / 'output.json').write_bytes(canonical_bytes(opinion(review)))
    envelope = SimpleNamespace(profile=profile.name, profile_revision='1', attempt_id=ATTEMPT,
        task=dict(input_sha256=hashlib.sha256(raw).hexdigest()), inputs=inputs,
        checkpoint_inputs=[], checkpoint_ref=None)
    return profile, envelope


def collect(profile, workspace, envelope, proof=None):
    return profile.collect_output(workspace, envelope,
        proof or StopEvidence(ATTEMPT, True, ('stopped-visual-agent',)))


@pytest.mark.parametrize('review', [False, True])
def test_visual_agent_collects_real_local_images_and_scope_bound_opinions(tmp_path, review):
    profile, envelope = prepare(tmp_path, review)
    assert profile.required_tools == ('read', 'write')
    assert profile.required_model_capabilities == ('text', 'tool_calls', 'vision')
    profile.validate_inputs(tmp_path, envelope.task)
    result = json.loads(collect(profile, tmp_path, envelope)[0].content())
    validator = visual.validate_review_result if review else visual.validate_observation_result
    assert validator(result, envelope.task['input_sha256'], input_document(review)) == result
    assert result['coverage'] == 'sampled_frames'
    assert result['evidence_scope'] == 'visual_only'
    assert result['frame_evidence_status'] == 'supplied_only'
    assert result['observed_frame_ids'] == ['frame-1', 'frame-2']
    assert input_document(review)['observation']['limitations'][0] in result['limitations']
    assert 'approved' not in result and 'passed' not in result
    assert collect(profile, tmp_path, envelope)[0].name == profile.result_name
    prompt = profile.build_prompt(envelope.task)
    assert 'Use read on every declared local PNG/JPEG frame' in prompt
    assert 'Do not claim complete viewing' in prompt
    assert 'audio quality' in prompt


@pytest.mark.parametrize('change', ['sha', 'replace_frame', 'fake_png', 'extra_frame', 'wrong_envelope_sha',
    'omit_envelope_frame', 'noncanonical', 'wrong_revision', 'wrong_profile', 'checkpoint', 'writer_active',
    'wrong_attempt', 'no_stop_resources'])
def test_review_rejects_replaced_evidence_and_unstopped_collectors(tmp_path, change):
    profile, envelope = prepare(tmp_path)
    proof = None
    if change == 'sha':
        envelope.task['input_sha256'] = '0' * 64
    elif change == 'replace_frame':
        (tmp_path / 'inputs/frame-1.png').write_bytes(PNG + b'changed')
    elif change == 'fake_png':
        document = input_document()
        document['observation']['frames'][0]['sha256'] = hashlib.sha256(b'not-png').hexdigest()
        raw = canonical_bytes(document)
        (tmp_path / 'inputs' / profile.input_name).write_bytes(raw)
        (tmp_path / 'inputs/frame-1.png').write_bytes(b'not-png')
        envelope.task['input_sha256'] = hashlib.sha256(raw).hexdigest()
    elif change == 'extra_frame':
        (tmp_path / 'inputs/frame-unbound.png').write_bytes(PNG)
    elif change == 'wrong_envelope_sha':
        envelope.inputs[1] = InlineFile.from_bytes('frame-1.png', b'another-frame')
    elif change == 'omit_envelope_frame':
        envelope.inputs.pop()
    elif change == 'noncanonical':
        (tmp_path / 'inputs' / profile.input_name).write_text(json.dumps(input_document(), indent=2))
    elif change == 'wrong_revision':
        envelope.profile_revision = '2'
    elif change == 'wrong_profile':
        envelope.profile = visual.OBSERVE_PROFILE
    elif change == 'checkpoint':
        envelope.checkpoint_ref = object()
    elif change == 'writer_active':
        proof = StopEvidence(ATTEMPT, False, ('writer',))
    elif change == 'wrong_attempt':
        proof = StopEvidence('different-attempt', True, ('writer',))
    else:
        proof = StopEvidence(ATTEMPT, True, ())
    with pytest.raises(ServiceError):
        collect(profile, tmp_path, envelope, proof)


@pytest.mark.parametrize('change', ['unseen_frame', 'one_motion_frame', 'out_of_window', 'time_mismatch',
    'reversed_frames', 'duplicate_issue', 'approval', 'invented_coverage', 'voice_issue'])
def test_model_cannot_invent_evidence_or_approve_output(tmp_path, change):
    profile, envelope = prepare(tmp_path)
    output = opinion()
    row = output['issues'][0]
    if change == 'unseen_frame':
        row['frame_ids'] = ['frame-1', 'unseen-frame']
    elif change == 'one_motion_frame':
        row['frame_ids'] = ['frame-1']
    elif change == 'out_of_window':
        row['end_seconds'] = 18
    elif change == 'time_mismatch':
        row['start_seconds'] = 1
    elif change == 'reversed_frames':
        row['frame_ids'].reverse()
    elif change == 'duplicate_issue':
        output['issues'].append(deepcopy(row))
    elif change == 'approval':
        output['approved'] = True
    elif change == 'invented_coverage':
        output['coverage'] = 'whole_video'
    else:
        row['kind'] = 'voice-naturalness'
    (tmp_path / 'output.json').write_bytes(canonical_bytes(output))
    with pytest.raises(ServiceError):
        collect(profile, tmp_path, envelope)


@pytest.mark.parametrize('change', ['full_coverage', 'audio_available', 'outside_window', 'duplicate_time',
    'path', 'unknown_extension', 'too_many_frames', 'nan', 'bad_hash', 'overlapping_windows'])
def test_sampling_manifest_cannot_overclaim_coverage_or_escape_local_inputs(change):
    document = input_document()
    observation = document['observation']
    if change == 'full_coverage':
        observation['coverage'] = 'complete_video'
    elif change == 'audio_available':
        observation['audio_available'] = True
    elif change == 'outside_window':
        observation['frames'][1]['time_seconds'] = 10
    elif change == 'duplicate_time':
        observation['frames'][1]['time_seconds'] = .5
    elif change == 'path':
        observation['frames'][0]['file_name'] = '../frame-1.png'
    elif change == 'unknown_extension':
        observation['frames'][0]['file_name'] = 'frame-1.svg'
    elif change == 'too_many_frames':
        observation['frames'] *= 13
    elif change == 'nan':
        document['media']['duration_seconds'] = float('nan')
    elif change == 'bad_hash':
        observation['frames'][0]['sha256'] = 'invalid'
    else:
        observation['windows'].append(dict(start_seconds=2, end_seconds=5))
    with pytest.raises(ServiceError):
        visual.validate_review_input(document)


def test_no_visual_issues_does_not_become_user_acceptance(tmp_path):
    profile, envelope = prepare(tmp_path)
    (tmp_path / 'output.json').write_bytes(canonical_bytes(dict(
        summary='No concrete issue is established by these samples.', issues=[], limitations=[])))
    result = json.loads(collect(profile, tmp_path, envelope)[0].content())
    assert result['issues'] == []
    assert result['limitations'] == input_document()['observation']['limitations']
    assert 'passed' not in result and 'approval' not in result


def test_result_rejects_source_tampering_and_removed_sampling_limitations(tmp_path):
    profile, envelope = prepare(tmp_path)
    result = json.loads(collect(profile, tmp_path, envelope)[0].content())
    tampered = deepcopy(result)
    tampered['source_sha256'] = 'c' * 64
    with pytest.raises(ServiceError):
        visual.validate_review_result(tampered, envelope.task['input_sha256'], input_document())
    result['limitations'] = []
    with pytest.raises(ServiceError, match='animation_review_limitations_removed'):
        visual.validate_review_result(result, envelope.task['input_sha256'], input_document())


def test_collector_cannot_claim_model_read_receipts(tmp_path):
    profile, envelope = prepare(tmp_path)
    result = json.loads(collect(profile, tmp_path, envelope)[0].content())
    result['frame_evidence_status'] = 'model_read_verified'
    with pytest.raises(ServiceError, match='animation_visual_evidence_scope'):
        visual.validate_review_result(result, envelope.task['input_sha256'], input_document())


def test_visual_bundle_is_pinned_independently_from_frozen_experiment(tmp_path, monkeypatch):
    assert set(visual.verified_bundle()) == visual.BUNDLE_FILES
    copied = tmp_path / visual.BUNDLE_REVISION
    shutil.copytree(visual.BUNDLE_ROOT, copied)
    (copied / 'CONTRACT.md').write_bytes(b'changed instructions')
    monkeypatch.setattr(visual, 'BUNDLE_ROOT', copied)
    with pytest.raises(ServiceError, match='animation_review_bundle_integrity'):
        implementation(visual.REVIEW_PROFILE).build_prompt({'input_sha256': 'a' * 64})


def test_frame_hardlink_is_rejected(tmp_path):
    profile, envelope = prepare(tmp_path)
    try:
        os.link(tmp_path / 'inputs/frame-1.png', tmp_path / 'linked-frame.png')
    except OSError:
        pytest.skip('Host does not support hardlink creation')
    with pytest.raises(ServiceError, match='invalid_video_file'):
        collect(profile, tmp_path, envelope)


@pytest.mark.parametrize('profile_name', [visual.OBSERVE_PROFILE, visual.REVIEW_PROFILE])
def test_visual_probe_requires_explicit_real_evidence_scenario(profile_name):
    from agent_service.deploy import acceptance_probe
    assert profile_name in acceptance_probe.PROFILES
    with pytest.raises(ServiceError, match='candidate_scenario_required'):
        acceptance_probe.fixture(profile_name)


@pytest.mark.parametrize('name,checksum', [
    (visual.OBSERVE_PROFILE, '36439743647eae0f52b93ed868e24bb06038519967edd845a251f2c357cbfe8a'),
    (visual.REVIEW_PROFILE, '59f0a606528a80cb028b44e4070dd73f94a46c13baadfb05bc09c90a9df62502'),
])
def test_opt_in_sequence_procedure_keeps_frozen_v1_prompt_bytes(name, checksum):
    # Existing combinations retain their actual prompts, not just profile names.
    prompt = implementation(name).build_prompt({'input_sha256': 'a' * 64})
    assert hashlib.sha256(prompt.encode()).hexdigest() == checksum
    assert visual.BUNDLE_MANIFEST_SHA256 == '165e4bc85eeae28a1fd30028f555b5d5a832f4dfdcfed09904bfd49859126f29'


def test_sequence_review_is_explicitly_selected_and_retains_evidence_contract(tmp_path):
    profile, envelope = prepare(tmp_path, profile_name=visual.REVIEW_SEQUENCE_PROFILE)
    assert profile.name == 'video.animation-review.v2'
    assert profile.task_schema == 'video-animation-review.v2'
    assert profile.output_schema == 'video-animation-review-output.v2'
    assert profile.required_tools == ('read', 'write')
    assert profile.required_model_capabilities == ('text', 'tool_calls', 'vision')
    assert profile.limits.max_input_files == 25
    profile.validate_inputs(tmp_path, envelope.task)
    result = json.loads(collect(profile, tmp_path, envelope)[0].content())
    assert visual.validate_review_result(result, envelope.task['input_sha256'], input_document()) == result
    assert result['schema'] == visual.REVIEW_RESULT_SCHEMA
    assert result['frame_evidence_status'] == 'supplied_only'
    assert result['evidence_scope'] == 'visual_only'
    assert 'approval' not in result and 'passed' not in result
    prompt = profile.build_prompt(envelope.task)
    assert visual.verified_sequence_bundle()['review-procedure/SKILL.md'] in prompt
    assert visual.verified_bundle()['review-procedure/SKILL.md'] not in prompt
    assert 'do not look for a separate CONTRACT.md file' in prompt
    # This is a procedure-selection check, not evidence of model understanding.
    assert 'intermediate states' in prompt and 'stationary' in prompt
    assert 'Empty issues are valid' in prompt


@pytest.mark.parametrize('change', ['old_profile', 'wrong_revision', 'one_motion_frame',
                                   'invented_coverage', 'unknown_frame', 'audio_claim'])
def test_sequence_role_does_not_relax_identity_or_visual_scope(tmp_path, change):
    profile, envelope = prepare(tmp_path, profile_name=visual.REVIEW_SEQUENCE_PROFILE)
    output = opinion()
    if change == 'old_profile':
        envelope.profile = visual.REVIEW_PROFILE
    elif change == 'wrong_revision':
        envelope.profile_revision = '2'
    elif change == 'one_motion_frame':
        output['issues'][0]['frame_ids'] = ['frame-1']
    elif change == 'invented_coverage':
        output['coverage'] = 'whole_video'
    elif change == 'unknown_frame':
        output['issues'][0]['frame_ids'][1] = 'unseen-frame'
    else:
        output['issues'][0]['kind'] = 'voice-naturalness'
    (tmp_path / 'output.json').write_bytes(canonical_bytes(output))
    with pytest.raises(ServiceError):
        collect(profile, tmp_path, envelope)


@pytest.mark.parametrize('path', ['manifest.json', 'CONTRACT.md', 'review-procedure/SKILL.md'])
def test_sequence_bundle_tampering_is_rejected_without_changing_v1(tmp_path, monkeypatch, path):
    copied = tmp_path / visual.SEQUENCE_BUNDLE_REVISION
    shutil.copytree(visual.SEQUENCE_BUNDLE_ROOT, copied)
    (copied / path).write_bytes(b'changed instructions')
    monkeypatch.setattr(visual, 'SEQUENCE_BUNDLE_ROOT', copied)
    with pytest.raises(ServiceError, match='animation_review_bundle_integrity'):
        implementation(visual.REVIEW_SEQUENCE_PROFILE).build_prompt({'input_sha256': 'a' * 64})
    assert implementation(visual.REVIEW_PROFILE).build_prompt({'input_sha256': 'a' * 64})


def test_sequence_collector_checks_its_own_bundle_after_writer_stop(tmp_path, monkeypatch):
    profile, envelope = prepare(tmp_path, profile_name=visual.REVIEW_SEQUENCE_PROFILE)
    copied = tmp_path / visual.SEQUENCE_BUNDLE_REVISION
    shutil.copytree(visual.SEQUENCE_BUNDLE_ROOT, copied)
    (copied / 'review-procedure/SKILL.md').write_bytes(b'changed after execution')
    monkeypatch.setattr(visual, 'SEQUENCE_BUNDLE_ROOT', copied)
    with pytest.raises(ServiceError, match='animation_review_bundle_integrity'):
        collect(profile, tmp_path, envelope)
