"""Binding/tamper unit tests with synthetic DOM and JPEG sentinel bytes.

These tests do not start a browser and are never native-renderer evidence.
The semantic checks below execute the unchanged production reviewer.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from video_workbench.animation_lab_evidence import (
    capture_request, review_capture, write_capture_inputs,
)
from video_workbench.animation_lab_samples import compile_sample
from video_workbench.motion import (
    action_windows, canonical, compile_project, estimated_alignment, example_plan,
    make_beats, sha256,
)


RUNTIME = dict(schema='video.motion-runtime.v1', hyperframes='0.8.149',
    hyperframes_source='test-source', node='22.14.0', browser='unit-fixture', ffmpeg='unit-fixture',
    gsap='3.14.2', gsap_sha256='a' * 64, font_sha256='b' * 64,
    npm_lock_sha256='c' * 64, dpkg_sha256='d' * 64, image_digest='sha256:' + 'e' * 64)


def test_requests_bind_audio_runtime_and_capture_implementations(tmp_path):
    bundle = compile_sample('diagram-connections', 'candidate')
    request = capture_request(bundle, 'scene-1', RUNTIME)
    assert request['files']['audio.wav'] == sha256(bundle['clips'][0]['files']['audio.wav'])
    assert all(len(request[key]) == 64 for key in
               ('capture_adapter_sha256', 'observer_sha256', 'runtime_collector_sha256'))
    changed = dict(RUNTIME, browser='another-browser')
    assert capture_request(bundle, 'scene-1', changed)['capture_identity'] != request['capture_identity']
    changed_bundle = deepcopy(bundle)
    changed_bundle['clips'][0]['files']['audio.wav'] += b'changed'
    assert capture_request(changed_bundle, 'scene-1', RUNTIME)['capture_identity'] != request['capture_identity']
    root = tmp_path / 'fresh'
    prepared = write_capture_inputs(bundle, root, RUNTIME)
    assert prepared[0]['request_sha256'] == sha256((root / 'scene-1' / 'capture-request.json').read_bytes())
    with pytest.raises(ValueError, match='destination_exists'):
        write_capture_inputs(bundle, root, RUNTIME)


@pytest.mark.parametrize('change', ['missing', 'extra', 'version', 'image'])
def test_incomplete_or_unpinned_runtime_is_not_admitted(change):
    runtime = deepcopy(RUNTIME)
    if change == 'missing':
        runtime.pop('font_sha256')
    elif change == 'extra':
        runtime['unmeasured'] = 'yes'
    elif change == 'version':
        runtime['hyperframes'] = 'latest'
    else:
        runtime['image_digest'] = 'latest'
    with pytest.raises(ValueError, match='runtime_invalid'):
        capture_request(compile_sample('diagram-connections', 'candidate'), 'scene-1', runtime)


def _synthetic_capture(tmp_path):
    """Real historical compiled bytes, but explicitly synthetic DOM/image evidence."""
    beats = make_beats('请求到达。请求进入队列。执行节点开始工作。')
    plan = example_plan(beats)
    plan['elements'] = [row for row in plan['elements'] if row['id'] == 'request']
    plan['relations'] = []
    plan['actions'] = [dict(beat_id=beats['segments'][0]['id'], target='request',
        kind='move', placement='middle', pace='slow', to_slot='token_queue')]
    audio = b'unit-test-only-not-real-audio'
    alignment = estimated_alignment(beats, sha256(audio), 15)
    files = compile_project(plan, beats, alignment, compiler_version='motion-compiler-1')
    timeline = json.loads(files['timeline.json'])
    files['audio.wav'] = audio
    bundle = dict(case_id='unit-fixture', variant_id='synthetic',
        identity=sha256('synthetic-capture'), input_sha256=sha256(plan), artifact_sha256=sha256(
            {name: sha256(raw) for name, raw in files.items()}),
        clips=[dict(id='scene-1', duration_seconds=15, timeline=timeline, files=files)])
    prepared = write_capture_inputs(bundle, tmp_path / 'capture', RUNTIME)[0]
    root = Path(prepared['directory'])
    request = json.loads((root / 'capture-request.json').read_bytes())
    observations = dict(schema='video.motion-observations.v1', timeline_sha256=sha256(timeline),
        seek_protocol='hyperframes-core-renderSeek-exact', render_fps=24, windows=[])
    window = dict(ordinal=0)
    times = action_windows(timeline)[0]
    # Sentinel JPEG envelopes are sufficient for these binding unit tests.
    # They must not be published or described as genuine screenshots.
    image_bytes = {label: b'\xff\xd8' + label.encode() + b'\xff\xd9'
                   for label in ('before', 'middle', 'after')}
    for label, x in [('before', 162), ('middle', 371), ('after', 580)]:
        window[label] = dict(time=times[label], actual_time=times[label],
            frame_sha256=sha256(image_bytes[label]),
            elements=dict(request=dict(opacity=1, x=x, y=220, width=120, height=44, overflow=False)),
            relations={}, layout=dict(fontReady=True, relations=[],
                title=dict(x=72, y=74, width=1136, height=48, overflow=False),
                takeaway=dict(x=72, y=648, width=1136, height=30, overflow=False)))
    observations['windows'] = [window]
    observations['reverse_seek'] = dict(first_sha256=sha256(image_bytes['before']),
        last_sha256=sha256(image_bytes['after']), repeated_first_sha256=sha256(image_bytes['before']),
        repeat_state_equal=True)
    directory = root / 'observations'
    directory.mkdir()
    for label, raw in image_bytes.items():
        (directory / f'a0-{label}.jpg').write_bytes(raw)
    for label, source in [('first', 'before'), ('last', 'after'), ('repeat', 'before')]:
        (directory / f'seek-{label}.jpg').write_bytes(image_bytes[source])
    (root / 'observations.json').write_bytes(canonical(observations))
    receipt = dict(schema='video.animation-lab-capture-receipt.v1',
        capture_identity=request['capture_identity'], request_sha256=prepared['request_sha256'],
        runtime=RUNTIME, image_digest_source='coordinator_pinned_not_self_measured',
        capture_adapter_sha256=request['capture_adapter_sha256'], observer_sha256=request['observer_sha256'],
        runtime_collector_sha256=request['runtime_collector_sha256'],
        agent_calls=0, tts_calls=0, production_writes=0, output='sampled_browser_observations_not_mp4')
    _reseal(root, receipt)
    return bundle, root, observations


def _reseal(root, receipt=None):
    if receipt is None:
        receipt = json.loads((root / 'capture-receipt.json').read_bytes())
    receipt['observations_sha256'] = sha256((root / 'observations.json').read_bytes())
    receipt['frames'] = [dict(name=path.name, sha256=sha256(path.read_bytes()), bytes=path.stat().st_size)
                         for path in sorted((root / 'observations').iterdir())]
    receipt['frame_count'] = len(receipt['frames'])
    (root / 'capture-receipt.json').write_bytes(canonical(receipt))


def test_production_checks_preserve_pending_quality_and_label_times(tmp_path):
    bundle, root, observations = _synthetic_capture(tmp_path)
    report = review_capture(bundle, 'scene-1', root, RUNTIME)
    assert report['technical_status'] == 'passed'
    assert report['visual_quality'] == 'not_evaluated'
    assert report['timing_review'] == 'pending'
    assert report['production_adoption'] is False
    frames = {row['frame_id']: row for row in report['visual_frames']}
    first, before = frames['scene-1:seek-first'], frames['scene-1:a0-before']
    assert first['sha256'] == before['sha256']
    assert first['time_seconds'] == 0
    assert before['time_seconds'] == observations['windows'][0]['before']['time'] > 0


@pytest.mark.parametrize('failure', ['frozen', 'wrong_target', 'wrong_actual_time'])
def test_hash_changing_images_cannot_hide_semantic_failures(tmp_path, failure):
    bundle, root, observations = _synthetic_capture(tmp_path)
    window = observations['windows'][0]
    if failure == 'frozen':
        for label in ('before', 'middle', 'after'):
            window[label]['elements']['request']['x'] = 162
    elif failure == 'wrong_target':
        window['after']['elements']['request']['x'] = 998
    else:
        window['middle']['actual_time'] += 1
    (root / 'observations.json').write_bytes(canonical(observations))
    _reseal(root)
    report = review_capture(bundle, 'scene-1', root, RUNTIME)
    assert report['technical_status'] == 'failed'
    assert report['issues'][0]['severity'] == 'blocker'
    assert report['technical_review'] is None
    assert not report['production_adoption']


@pytest.mark.parametrize('tamper', ['input', 'runtime', 'request', 'receipt', 'observations',
                                   'image', 'extra_image', 'missing_reference', 'wrong_label'])
def test_mixed_or_corrupt_capture_never_becomes_technical_pass(tmp_path, tamper):
    bundle, root, observations = _synthetic_capture(tmp_path)
    runtime = RUNTIME
    if tamper == 'input':
        (root / 'audio.wav').write_bytes(b'changed')
    elif tamper == 'runtime':
        runtime = dict(RUNTIME, browser='another-browser')
    elif tamper == 'request':
        (root / 'capture-request.json').write_bytes(b'{}')
    elif tamper == 'receipt':
        receipt = json.loads((root / 'capture-receipt.json').read_bytes())
        receipt['production_writes'] = 1
        (root / 'capture-receipt.json').write_bytes(canonical(receipt))
    elif tamper == 'observations':
        (root / 'observations.json').write_bytes(b'{}')
    elif tamper == 'image':
        (root / 'observations' / 'a0-middle.jpg').write_bytes(b'corrupt')
    elif tamper == 'extra_image':
        (root / 'observations' / 'extra.jpg').write_bytes(b'\xff\xd8extra\xff\xd9')
    else:
        sample = observations['windows'][0]['middle']
        sample['frame_sha256'] = '0' * 64 if tamper == 'missing_reference' else observations['reverse_seek']['first_sha256']
        (root / 'observations.json').write_bytes(canonical(observations))
        _reseal(root)
    with pytest.raises(ValueError, match='animation_lab_capture_'):
        review_capture(bundle, 'scene-1', root, runtime)
