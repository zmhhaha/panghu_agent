"""Real CPU decoding through the shared service, with no render or model call."""
from copy import deepcopy
import hashlib
from pathlib import Path
import shutil
import subprocess
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError
from agent_service.profiles.video_animation_review import REVIEW_PACKED_PROFILE
from video_workbench.animation_creation_service import AnimationCreationService, _FIXED_GUIDANCE
from video_workbench.animation_observation import prepare_temporal_media
from video_workbench.visual_sampling import VisualSamplingService


def test_creation_preserves_temporal_strategy_and_exact_original_bundle(tmp_path):
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        pytest.skip('FFmpeg required for a real decoder comparison')
    media = tmp_path / 'source.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=64x48:rate=24',
        '-t', '2', '-pix_fmt', 'yuv420p', str(media)], check=True, timeout=30)
    directory = tmp_path / 'run'; directory.mkdir()
    source_hash = 'c' * 64
    run = dict(owner='alice', id=str(uuid4()), deadline_at=time.time()+3600,
        frozen=dict(review_profile=REVIEW_PACKED_PROFILE, creation_input=dict(goal='Original visual goal',
            constraints=dict(duration_seconds=2))),
        operations=dict(initial_creation=dict(result=dict(source_sha256=source_hash)),
            initial_render=dict(id='original-render')),
        versions=[dict(sha256=hashlib.sha256(media.read_bytes()).hexdigest())])
    saved = []
    service = AnimationCreationService.__new__(AnimationCreationService)
    service.sampling_service = VisualSamplingService(tmp_path / 'sampler')
    service.renderer = SimpleNamespace(media_path=lambda _: media)
    service._directory = lambda _: directory
    service._save = lambda current: saved.append(deepcopy(current))
    service._cancelled = lambda _: False
    actual = service._review_directory(run)
    expected = tmp_path / 'original-policy'
    prepare_temporal_media(media, expected, clip_id='fragment-'+run['id'],
        goal=run['frozen']['creation_input']['goal']+'\n'+_FIXED_GUIDANCE,
        source_sha256=source_hash, sampling_layers=[dict(start_seconds=0, end_seconds=2, sample_fps=3)],
        frame_limit=24, review_profile=REVIEW_PACKED_PROFILE)
    members = lambda root: {path.relative_to(root).as_posix(): path.read_bytes()
                            for path in root.rglob('*') if path.is_file()}
    assert members(actual) == members(expected)
    assert saved[0]['sampling_intent']['policy']['strategy'] == 'temporal_layers'
    reference = run['sampling_ref']
    sampler = service.sampling_service
    frames = sampler.read_frames('alice', reference['operation_id'])
    assert len(frames) == 6
    assert all(row['clock_scope']=='measured_ffmpeg_normalized_pts_not_original_container_timeline'
               for row in frames)
    assert service._review_directory(run) == actual  # retained read, no decode
    frame = next((actual/'inputs').glob('*.jpg'))
    frame.write_bytes(b'tampered')
    with pytest.raises(ServiceError, match='changed|mismatch|integrity'):
        service._review_directory(run)


def test_creation_sampler_failure_keeps_render_and_blocks_local_fallback(tmp_path, monkeypatch):
    directory=tmp_path/'run';directory.mkdir()
    media=tmp_path/'source.mp4';media.write_bytes(b'not-a-decodable-video')
    run=dict(owner='alice',id=str(uuid4()),deadline_at=time.time()+3600,
        frozen=dict(review_profile=REVIEW_PACKED_PROFILE,creation_input=dict(goal='Original',
            constraints=dict(duration_seconds=2))),
        operations=dict(initial_creation=dict(result=dict(source_sha256='c'*64)),
            initial_render=dict(id='original-render')),
        versions=[dict(sha256=hashlib.sha256(media.read_bytes()).hexdigest())])
    service=AnimationCreationService.__new__(AnimationCreationService)
    service.sampling_service=VisualSamplingService(tmp_path/'sampler')
    service.renderer=SimpleNamespace(media_path=lambda _:media)
    service._directory=lambda _:directory
    service._save=lambda current:current
    service._cancelled=lambda _:False
    monkeypatch.setattr('video_workbench.animation_creation_service.prepare_temporal_media',
        lambda *a,**k:pytest.fail('a local decoder fallback is forbidden'))
    with pytest.raises(ServiceError):service._review_directory(run)
    intent=deepcopy(run['sampling_intent'])
    first=service.sampling_service.get('alice',intent['operation_id'])
    assert first['status']=='failed' and media.read_bytes()==b'not-a-decodable-video'
    with pytest.raises(ServiceError):service._review_directory(run)
    assert run['sampling_intent']==intent
    assert service.sampling_service.get('alice',intent['operation_id'])==first
    assert not (directory/'review-evidence').exists()
