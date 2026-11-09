"""Actual media decoding and honest observation claims, without model calls."""
from copy import deepcopy
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
