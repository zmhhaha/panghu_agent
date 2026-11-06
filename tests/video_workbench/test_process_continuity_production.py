"""Process continuity crosses the frozen production boundary without new audio."""
import base64
import hashlib
import json

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.continuity import build_continuity_plan, compile_continuity_project
from video_workbench.continuity_snapshot import freeze_continuity_snapshot, verify_motion_snapshot
from video_workbench.motion import estimated_alignment
from video_workbench.motion_render_k8s import production_resources
from video_workbench.motion_renderer.job import freeze_input
from video_workbench.process_continuity import PROCESS_CONTINUITY_COMPILER_VERSION as COMPILER
from test_motion_snapshot import RUNTIME, freeze
from test_process_continuity_core import process_scenes
from test_process_production import pcm


def frozen_process(*, copy=False):
    scenes = process_scenes(count=2, repeat=copy)
    for index, scene in enumerate(scenes):
        raw = pcm(3, 4096 if index == 0 else -4096)
        checksum = hashlib.sha256(raw).hexdigest()
        beats = scene['motion']['beats']
        scene.pop('timing')
        scene.update(visual_mode='motion', narration=beats['source_text'])
        scene['audio'].update(data=raw, sha256=checksum, duration_seconds=3,
            spoken_text=beats['spoken_text'], origin='tts', model='kokoro-rk3588', voice='zm_yunjian')
        scene['motion']['alignment'] = estimated_alignment(beats, checksum, 3)
    source = freeze(scenes)
    plan = build_continuity_plan(source['scenes'])
    result = freeze_continuity_snapshot(source, dict(id='process-continuity', plan=plan, compiler=COMPILER))
    return source, result


@pytest.mark.parametrize('copy', [False, True])
def test_frozen_process_v3_rebuild_retains_original_source_audio_and_pending_review(copy):
    source, snapshot = frozen_process(copy=copy)
    assert verify_motion_snapshot(json.loads(json.dumps(snapshot))) == snapshot
    assert verify_motion_snapshot(source) == source
    assert snapshot['continuity']['resolution']['compiler'] == COMPILER
    assert snapshot['scenes'][0]['continuity']['final_checkpoint'] == snapshot['scenes'][1]['continuity']['initial_checkpoint']
    for before, after in zip(source['scenes'], snapshot['scenes']):
        assert after['audio'] == before['audio']
        assert after['motion'] == before['motion']
        assert after['continuity_base_timing'] == before['timing']
        assert after['motion']['alignment']['review_status'] == 'pending'
    token_link = next(row for row in snapshot['continuity']['plan']['links'] if row['to_element_id'] == 'request')
    assert token_link['mode'] == ('copy' if copy else 'retain')


@pytest.mark.parametrize('target', ['checkpoint', 'source', 'compiled_files', 'audio', 'timing', 'compiler'])
def test_resigned_outer_hash_cannot_forge_process_snapshot(target):
    _, snapshot = frozen_process(copy=True)
    scene = snapshot['scenes'][1]
    if target == 'checkpoint':
        obj = next(iter(scene['continuity']['initial_checkpoint']['objects'].values()))
        obj['x'] += 10
        segment = scene['continuity']
        segment['sha256'] = digest({k: v for k, v in segment.items() if k != 'sha256'})
    elif target == 'source':
        scene['motion']['plan']['title'] = 'forged'
    elif target == 'compiled_files':
        scene['continuity_project_files_sha256']['index.html'] = '0' * 64
    elif target == 'audio':
        scene['audio']['data_base64'] = base64.b64encode(pcm(3, 0)).decode()
    elif target == 'compiler':
        snapshot['continuity']['resolution']['compiler'] = 'motion-continuity-compiler-2'
    else:
        scene['timing']['leading_seconds'] += .1
    snapshot['sha256'] = digest({k: v for k, v in snapshot.items() if k != 'sha256'})
    with pytest.raises(ServiceError):
        verify_motion_snapshot(snapshot)


@pytest.mark.parametrize('copy', [False, True])
def test_production_job_recompiles_process_contract_and_rejects_extra_html(tmp_path, copy):
    _, snapshot = frozen_process(copy=copy)
    scene = snapshot['scenes'][1]
    files = compile_continuity_project(scene, scene['continuity'], runtime_fingerprint=RUNTIME)
    for name, raw in {**files, 'audio.wav': base64.b64decode(scene['audio']['data_base64'])}.items():
        (tmp_path / name).write_bytes(raw)
    bundle = freeze_input(tmp_path, image_digest=RUNTIME['image_digest'])
    assert bundle['unbound_experiment'] is False
    image = 'registry.local/video-motion-renderer:latest@' + RUNTIME['image_digest']
    resources = production_resources(tmp_path, image=image, release_id='process-continuity-test',
        namespace='video-motion-render', name='process-continuity-test', binding='b' * 64)
    assert resources[1]['kind'] == 'Job'
    (tmp_path / 'index.html').write_bytes(files['index.html'] + b'<script>alert(1)</script>')
    with pytest.raises(ValueError, match='input_not_compiler_output'):
        freeze_input(tmp_path, image_digest=RUNTIME['image_digest'])


def test_selected_candidate_cannot_claim_a_different_compiler():
    source, snapshot = frozen_process()
    version = dict(id='bad-compiler', plan=snapshot['continuity']['plan'], compiler='motion-continuity-compiler-2')
    with pytest.raises(ServiceError, match='continuity_compiler_version_unsupported'):
        freeze_continuity_snapshot(source, version, compiler_version=COMPILER)
