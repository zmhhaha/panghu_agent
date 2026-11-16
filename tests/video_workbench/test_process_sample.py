"""The reviewable sample demonstrates retry without claiming real narration."""
import hashlib
import io
import json
import subprocess
import sys
import wave

import pytest

from video_workbench.examples.build_process_sample import build, SAMPLE_SECONDS
from video_workbench.motion_process import PROCESS_COMPILER_VERSION


def test_sample_keeps_one_request_through_failure_return_and_retry(tmp_path):
    target = tmp_path / 'sample'
    report = build(target)
    plan = json.loads((target / 'plan.json').read_bytes())
    timeline = json.loads((target / 'timeline.json').read_bytes())
    transfers = [row for row in timeline['actions'] if row['kind'] == 'transfer']
    assert [row['relation_id'] for row in transfers] == [
        'source_queue', 'queue_worker', 'worker_queue', 'queue_worker']
    assert {row['target'] for row in transfers} == {'request'}
    assert len([row for row in plan['elements'] if row['kind'] == 'token']) == 1
    assert [row['state'] for row in timeline['actions']
            if row['kind'] == 'set_state' and row['target'] == 'worker'] == [
                'running', 'failed', 'running', 'completed']
    assert report['production_integrated'] is False
    assert timeline['alignment_method'] == 'estimated'
    assert timeline['review_status'] == 'pending'
    assert report['visual_comprehension'] == 'pending_user_review'
    assert report['agent_calls'] == report['tts_calls'] == 0
    assert report['compiler'] == timeline['compiler'] == PROCESS_COMPILER_VERSION


def test_timing_wav_is_explicitly_silence_and_bound_to_generated_inputs(tmp_path):
    target = tmp_path / 'sample'
    report = build(target)
    audio = (target / 'audio.wav').read_bytes()
    alignment = json.loads((target / 'alignment.json').read_bytes())
    assert report['audio_kind'] == 'synthetic_silence_not_narration'
    assert report['voice_naturalness'] == 'not_evaluated'
    assert alignment['audio_sha256'] == hashlib.sha256(audio).hexdigest()
    with wave.open(io.BytesIO(audio)) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 24000)
        assert wav.getnframes() == SAMPLE_SECONDS * 24000
        assert not any(wav.readframes(wav.getnframes()))
    for name, digest in report['files'].items():
        assert hashlib.sha256((target / name).read_bytes()).hexdigest() == digest


def test_build_refuses_to_overwrite_previous_evidence(tmp_path):
    target = tmp_path / 'sample'
    first = build(target)
    before = {path.name: path.read_bytes() for path in target.iterdir()}
    with pytest.raises(ValueError, match='already exists'):
        build(target)
    assert before == {path.name: path.read_bytes() for path in target.iterdir()}
    assert json.loads((target / 'sample.json').read_bytes()) == first


def test_explicit_old_sample_and_default_new_sample_share_the_same_recording(tmp_path):
    old, new = tmp_path / 'old', tmp_path / 'new'
    before = build(old, compiler_version='motion-process-compiler-1')
    after = build(new)
    assert before['compiler'] == 'motion-process-compiler-1'
    assert after['compiler'] == 'motion-process-compiler-2'
    for name in ('plan.json', 'beats.json', 'alignment.json', 'audio.wav'):
        assert (old / name).read_bytes() == (new / name).read_bytes()
    for name in ('index.html', 'timeline.json', 'manifest.json'):
        assert (old / name).read_bytes() != (new / name).read_bytes()
    assert before['audio_kind'] == after['audio_kind'] == 'synthetic_silence_not_narration'


def test_cli_can_rebuild_an_explicit_historical_compiler(tmp_path):
    target = tmp_path / 'cli-v1'
    result = subprocess.run([sys.executable, '-m', 'video_workbench.examples.build_process_sample',
                             '--output', str(target), '--compiler-version', 'motion-process-compiler-1'],
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)['compiler'] == 'motion-process-compiler-1'
    assert json.loads((target / 'manifest.json').read_bytes())['template'] == 'controlled-process-1'


def test_unknown_compiler_does_not_create_a_partial_sample(tmp_path):
    from agent_service.contracts import ServiceError

    target = tmp_path / 'unsupported'
    with pytest.raises(ServiceError, match='unknown_process_compiler'):
        build(target, compiler_version='motion-process-compiler-999')
    assert not target.exists()
