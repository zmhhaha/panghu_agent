"""Actual media decoding and honest observation claims, without model calls."""
from copy import deepcopy
import hashlib
import json
import shutil
import subprocess
from uuid import UUID

import pytest

from agent_service.contracts import ServiceError
from video_workbench import animation_observation as observation


@pytest.fixture
def media(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        pytest.skip('Media decoder is unavailable')
    path = tmp_path / 'technical-fixture.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
        'color=c=blue:s=320x180:r=12:d=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
        '-threads', '1', str(path)], check=True, timeout=30, capture_output=True)
    return path


def prepare(media, tmp_path, **kwargs):
    return observation.prepare_sampled_media(media, tmp_path / 'evidence',
        clip_id='technical-fixture', goal='Inspect the supplied visual evidence.', sample_fps=2,
        **kwargs)


def transport_receipt(document, completed=True):
    return dict(schema='agent-model-image-evidence.v1',job_id=str(UUID(int=1)),
        attempt_id=str(UUID(int=2)),request_sha256='c'*64,evidence_scope='completed_upstream_transport',
        images=[dict(sha256=f['sha256'], model_request_completed=completed,
            model_request_sha256='d'*64,media_type='image/jpeg',byte_length=100,
            input_item_index=i,part_index=0) for i,f in enumerate(document['observation']['frames'])])


def test_decode_actual_media_into_a_loadable_evidence_bundle(media, tmp_path):
    result = prepare(media, tmp_path)
    profile, document, files = observation.load_bundle(tmp_path / 'evidence')
    assert profile == 'video.animation-observe.v1'
    assert result['receipt']['frames'] == 4
    assert result['receipt']['model_calls'] == 0
    assert result['receipt']['observation_status'] == 'prepared_not_observed'
    assert document['media']['sha256'] == observation.file_sha256(media)
    assert [f['time_seconds'] for f in document['observation']['frames']] == [0, .5, 1, 1.5]
    assert len(files) == 5 and all(files[f['file_name']][:2] == b'\xff\xd8'
                                   for f in document['observation']['frames'])
    assert document['observation']['audio_available'] is False


def test_long_reference_requires_windows_instead_of_silent_truncation(media, tmp_path):
    with pytest.raises(ServiceError, match='select_shorter_windows'):
        observation.prepare_sampled_media(media, tmp_path / 'too-many',
            clip_id='fixture', goal='Observe', sample_fps=12, frame_limit=4)
    assert not (tmp_path / 'too-many').exists()


def test_review_binds_source_and_selected_window(media, tmp_path):
    result = prepare(media, tmp_path, source_sha256='a' * 64,
        windows=[{'start_seconds': .5, 'end_seconds': 1.5}])
    profile, doc, _ = observation.load_bundle(tmp_path / 'evidence')
    assert profile == 'video.animation-review.v1'
    assert doc['source_sha256'] == 'a' * 64
    assert [r['time_seconds'] for r in doc['observation']['frames']] == [.5, 1]
    assert result['receipt']['frames'] == 2


def test_tampered_frame_cannot_be_uploaded(media, tmp_path):
    result = prepare(media, tmp_path)
    frame = result['document']['observation']['frames'][0]['file_name']
    (tmp_path / 'evidence' / 'inputs' / frame).write_bytes(b'changed')
    with pytest.raises(ServiceError):
        observation.load_bundle(tmp_path / 'evidence')


def test_preparation_never_overwrites_prior_evidence(media, tmp_path):
    prepare(media, tmp_path)
    with pytest.raises(ServiceError, match='no_overwrite'):
        prepare(media, tmp_path)


def test_input_frames_are_not_proof_of_model_observation(media, tmp_path):
    document = prepare(media, tmp_path)['document']
    with pytest.raises(ServiceError, match='evidence_missing'):
        observation.validate_model_frame_receipt(document, {'observed_frame_ids':['frame-000']})
    receipt = transport_receipt(document, completed=False)
    with pytest.raises(ServiceError, match='evidence_incomplete'):
        observation.validate_model_frame_receipt(document, receipt)
    for item in receipt['images']:
        item['model_request_completed'] = True
    proof = observation.validate_model_frame_receipt(document, receipt)
    assert proof['whole_video_watched'] is False and proof['audio_observed'] is False
    assert proof['model_understanding_verified'] is False
    assert proof['observed_frame_ids'] == [f['frame_id'] for f in document['observation']['frames']]


def test_frame_proof_rejects_missing_actual_input(media, tmp_path):
    doc = prepare(media, tmp_path)['document']
    changed = deepcopy(doc)
    changed['observation']['frames'][0]['sha256'] = 'b' * 64
    receipt = transport_receipt(doc)
    with pytest.raises(ServiceError, match='evidence_incomplete'):
        observation.validate_model_frame_receipt(changed, receipt)


@pytest.mark.parametrize('field,value', [('sha256',[]),('byte_length',True),
    ('input_item_index',-1),('model_request_sha256','self-reported'),('media_type','video/mp4')])
def test_malformed_proxy_image_metadata_is_not_observation_evidence(media, tmp_path, field, value):
    doc = prepare(media,tmp_path)['document']
    receipt = transport_receipt(doc)
    receipt['images'][0][field] = value
    with pytest.raises(ServiceError, match='evidence_invalid'):
        observation.validate_model_frame_receipt(doc,receipt)


def test_temporal_plan_combines_overview_and_detail_without_changing_budget():
    layers = [dict(start_seconds=0, end_seconds=8, sample_fps=1),
              dict(start_seconds=2, end_seconds=3, sample_fps=8)]
    plan = observation.temporal_sampling_plan(8, layers)
    assert plan['windows'] == [dict(start_seconds=0, end_seconds=8)]
    assert plan['requested_times'] == sorted({*range(8), *[2 + i / 8 for i in range(8)]})
    assert plan['frame_limit'] == 24
    assert layers == plan['layers']  # The explicit allocation is not rewritten.
    with pytest.raises(ServiceError, match='select_shorter_windows'):
        observation.temporal_sampling_plan(8, layers, frame_limit=8)
    with pytest.raises(ServiceError):
        observation.temporal_sampling_plan(8, layers, frame_limit=25)


def test_temporal_plan_preserves_disjoint_coverage_and_rejects_invalid_layers():
    layers = [dict(start_seconds=1, end_seconds=1.5, sample_fps=2),
              dict(start_seconds=0, end_seconds=.5, sample_fps=2)]
    plan = observation.temporal_sampling_plan(2, layers)
    assert plan['windows'] == [dict(start_seconds=0, end_seconds=.5),
                               dict(start_seconds=1, end_seconds=1.5)]
    assert plan['requested_times'] == [0, 1]
    for bad in ([], [dict(start_seconds=0, end_seconds=2, sample_fps=True)],
                [dict(start_seconds=0, end_seconds=3, sample_fps=2)],
                [dict(start_seconds=0, end_seconds=1, sample_fps=float('nan'))]):
        with pytest.raises(ServiceError):
            observation.temporal_sampling_plan(2, bad)


def test_temporal_media_uses_actual_pts_and_discloses_presentation_aliases(media, tmp_path):
    layers = [dict(start_seconds=0, end_seconds=2, sample_fps=1),
              dict(start_seconds=.01, end_seconds=.3, sample_fps=12),
              dict(start_seconds=.02, end_seconds=.3, sample_fps=12)]
    root = tmp_path / 'temporal'
    result = observation.prepare_temporal_media(media, root, clip_id='technical-fixture',
        goal='Inspect full coverage and the explicitly selected detail window.', sampling_layers=layers,
        source_sha256='a' * 64)
    profile, doc, files = observation.load_bundle(root)
    plan_raw = (root / 'sampling-plan.json').read_bytes()
    plan = json.loads(plan_raw)
    frames = doc['observation']['frames']
    assert profile == 'video.animation-review.v1'
    assert result['receipt']['requested_samples'] == 10
    assert result['receipt']['aliased_samples'] == 4
    assert len(frames) == result['receipt']['frames'] == 6
    assert [frame['time_seconds'] for frame in frames] == [0, 1/12, 2/12, 3/12, 4/12, 1]
    assert plan['requests'][1]['requested_time_seconds'] == .01
    assert plan['requests'][1]['time_seconds'] == 1/12
    assert any(row['alias'] for row in plan['requests'])
    assert 'Temporal sampling plan SHA256: ' + hashlib.sha256(plan_raw).hexdigest() in doc['observation']['limitations']
    assert any('4 aliases' in text for text in doc['observation']['limitations'])
    assert len(files) == 7
    # Equal pixels are not deduplicated: still images at distinct PTS remain
    # separate observations, unlike two requests that selected the same frame.
    assert len({frame['sha256'] for frame in frames}) == 1


def test_temporal_media_budget_is_rejected_before_creating_evidence(media, tmp_path):
    root = tmp_path / 'over-budget'
    with pytest.raises(ServiceError, match='select_shorter_windows'):
        observation.prepare_temporal_media(media, root, clip_id='technical-fixture',
            goal='Inspect', sampling_layers=[dict(start_seconds=0, end_seconds=2, sample_fps=12)],
            frame_limit=4)
    assert not root.exists()


def test_temporal_frame_outside_selected_scope_is_not_relabeled_as_in_scope(media, tmp_path):
    with pytest.raises(ServiceError, match='sampling_outside_window'):
        observation.prepare_temporal_media(media, tmp_path / 'outside', clip_id='technical-fixture',
            goal='Inspect', sampling_layers=[dict(start_seconds=.01, end_seconds=.02, sample_fps=1)])


def test_temporal_plan_is_immutable_when_reloaded(media, tmp_path):
    root = tmp_path / 'temporal'
    observation.prepare_temporal_media(media, root, clip_id='technical-fixture', goal='Inspect',
        sampling_layers=[dict(start_seconds=0, end_seconds=2, sample_fps=1)])
    (root / 'sampling-plan.json').write_text('{}', encoding='utf-8')
    with pytest.raises(ServiceError, match='sampling_plan_changed'):
        observation.load_bundle(root)


@pytest.mark.parametrize('profile', ['video.animation-review.v2', 'video.animation-review.v3'])
def test_sequence_review_profile_requires_explicit_opt_in_and_a_source(media, tmp_path, profile):
    root = tmp_path / 'sequence'
    result = observation.prepare_temporal_media(media, root, clip_id='technical-fixture', goal='Inspect',
        sampling_layers=[dict(start_seconds=0, end_seconds=2, sample_fps=1)],
        source_sha256='a' * 64, review_profile=profile)
    assert result['receipt']['profile'] == observation.load_bundle(root)[0] == profile
    with pytest.raises(ServiceError, match='profile_invalid'):
        observation.prepare_temporal_media(media, tmp_path / 'no-source', clip_id='technical-fixture',
            goal='Inspect', sampling_layers=[dict(start_seconds=0, end_seconds=2, sample_fps=1)],
            review_profile=profile)
    assert not (tmp_path / 'no-source').exists()


def test_temporal_times_follow_variable_rate_presentation_frames(media, tmp_path):
    source = tmp_path / 'variable-rate.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
        'color=c=blue:s=320x180:r=8:d=1', '-vf', r'select=not(mod(n\,3))',
        '-fps_mode', 'vfr', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-threads', '1',
        str(source)], check=True, timeout=30, capture_output=True)
    probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', str(source)],
        check=True, capture_output=True, timeout=30)
    expected = [float(frame['best_effort_timestamp_time']) for frame in json.loads(probe.stdout)['frames']]
    result = observation.prepare_temporal_media(source, tmp_path / 'variable-evidence',
        clip_id='variable-rate', goal='Inspect selected frame intervals.',
        sampling_layers=[dict(start_seconds=0, end_seconds=.75, sample_fps=8)])
    assert expected == [0, .375, .75]
    assert [row['time_seconds'] for row in result['document']['observation']['frames']] == expected
    assert result['receipt']['requested_samples'] == 6
    assert result['receipt']['aliased_samples'] == 3


@pytest.mark.parametrize('mutation', ['pts', 'alias', 'layers'])
def test_resealed_inconsistent_temporal_plan_is_rejected(media, tmp_path, mutation):
    root = tmp_path / 'temporal'
    observation.prepare_temporal_media(media, root, clip_id='technical-fixture', goal='Inspect',
        sampling_layers=[dict(start_seconds=0, end_seconds=2, sample_fps=1)])
    plan = json.loads((root / 'sampling-plan.json').read_bytes())
    receipt = json.loads((root / 'evidence.json').read_bytes())
    name = receipt['input_name']
    document = json.loads((root / 'inputs' / name).read_bytes())
    if mutation == 'pts':
        plan['requests'][0]['pts'] += 1
    elif mutation == 'alias':
        plan['requests'][0]['alias'] = True
    else:
        plan['layers'][0]['sample_fps'] = 2
    raw = observation.canonical_bytes(plan)
    new_sha = hashlib.sha256(raw).hexdigest()
    document['observation']['limitations'] = [text.replace(receipt['sampling_plan_sha256'], new_sha)
        for text in document['observation']['limitations']]
    (root / 'sampling-plan.json').write_bytes(raw)
    raw = observation.canonical_bytes(document)
    (root / 'inputs' / name).write_bytes(raw)
    receipt['sampling_plan_sha256'] = new_sha
    receipt['input_sha256'] = hashlib.sha256(raw).hexdigest()
    (root / 'evidence.json').write_bytes(observation.canonical_bytes(receipt))
    with pytest.raises(ServiceError, match='sampling_plan_changed'):
        observation.load_bundle(root)


@pytest.mark.parametrize('log', [
    b'No measured timestamp here',
    b'[Parsed_showinfo_2 @ 123] config in time_base: 1/12\n'
        b'[Parsed_showinfo_3 @ 456] n: 0 pts: 1 pts_time:0.083333\n',
    b'[Parsed_showinfo_2 @ 123] config in time_base: 1/12\n'
        b'[Parsed_showinfo_2 @ 123] config in time_base: 1/12\n'
        b'[Parsed_showinfo_2 @ 123] n: 0 pts: 1 pts_time:0.083333\n',
    b'[Parsed_showinfo_2 @ 123] config in time_base: 1/12\n'
        b'[Parsed_showinfo_2 @ 123] n: 0 pts: 0 pts_time:0\n',
])
def test_temporal_timestamp_requires_one_matching_decoder_record(tmp_path, monkeypatch, log):
    monkeypatch.setattr(observation.subprocess, 'run', lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, b'', log))
    with pytest.raises(ServiceError, match='timestamp_unverified'):
        observation._decode_presentation_frame(tmp_path / 'source.mp4', tmp_path / 'output.jpg',
                                                .01, ffmpeg='ffmpeg')
