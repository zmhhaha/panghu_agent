"""One-attempt evidence custody and actual deterministic sound synthesis.

Container receipts in adapter tests are deliberately synthetic. They exercise
custody and state transitions, not a claim of real Chromium/MP4 acceptance.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import threading
import wave

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_animation_method as contract
from video_workbench import animation_method_renderer as renderer


IMAGE = 'registry.example/renderer:latest@sha256:' + 'a' * 64
SOURCE = dict(engine='html-svg', strategy=dict(description='表达群体变化', tradeoffs='保持对象可跟随'),
    files={'scene.html': '<html><body><script>window.__animationExperiment={seek(t){'
           'document.body.dataset.t=String(t);}};</script></body></html>'})


def document(duration=1):
    return dict(schema=contract.INPUT_SCHEMA, mode='create', goal='解释群体筛选与统计之间的因果',
        criteria=[dict(id='causal', description='留下的群体与数值联系清楚', requires_motion=True)],
        methods=[], constraints=dict(width=320, height=180, fps=24,
            duration_seconds=duration, allowed_engines=['html-svg']),
        feedback='', base_source=None, reference=None, comparison=None, assets={},
        observation=dict(frames=[], audio_observed=False, motion_observed=False,
                         limitations=['没有实际连续视听输入']))


class SyntheticExecution:
    def __init__(self, failure=None):
        self.failure, self.calls = failure, 0
        self.entered = self.release = None

    def __call__(self, directory):
        self.calls += 1
        if self.entered:
            self.entered.set()
            assert self.release.wait(10)
        root = Path(directory)
        binding = json.loads((root / 'binding.json').read_bytes())
        request = binding['request']
        if self.failure == 'unknown':
            raise TimeoutError('synthetic ambiguous result')
        failed = self.failure in {'known', 'cleanup'}
        outcome = dict(schema='video.animation-method-render-outcome.v1',
            request_identity=request['identity'], request_sha256=binding['request_sha256'],
            image=IMAGE, actual_image_id='sha256:' + 'b' * 64, container_id='c' * 64,
            cleanup=dict(status='unconfirmed' if self.failure == 'cleanup' else 'removed_and_confirmed_absent',
                         container_id='c' * 64),
            status='failed' if failed else 'rendered',
            error='synthetic_failure' if failed else None, automatic_retry=False)
        if not failed:
            work = root / 'work'
            (work / 'frames').mkdir(parents=True)
            frame = b'\xff\xd8synthetic-not-real-jpeg\xff\xd9'
            frames, count = [], int(request['constraints']['duration_seconds'] * 24)
            for index in range(count + 1):
                name = f'frame-{index:06d}.jpg' if index < count else 'reverse-initial.jpg'
                (work / 'frames' / name).write_bytes(frame)
                frames.append(dict(name=name, timestamp_seconds=index / 24 if index < count else 0,
                    sha256=hashlib.sha256(frame).hexdigest(), bytes=len(frame)))
            media = b'0000ftypSYNTHETIC-NOT-ENCODED-MEDIA'
            (work / 'output.mp4').write_bytes(media)
            audio_sha = None
            if request['sound_events']:
                audio_file = io.BytesIO()
                with wave.open(audio_file, 'wb') as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(48000)
                    wav.writeframes(b'\0\0' * round(request['constraints']['duration_seconds'] * 48000))
                raw = audio_file.getvalue()
                (work / 'sound-effects.wav').write_bytes(raw)
                audio_sha = hashlib.sha256(raw).hexdigest()
            streams = [dict(codec_type='video', codec_name='h264',
                width=request['constraints']['width'], height=request['constraints']['height'],
                avg_frame_rate='24/1')]
            if audio_sha:
                streams.append(dict(codec_type='audio', codec_name='aac'))
            receipt = dict(schema='video.animation-method-render-receipt.v1', status='rendered',
                request_identity=request['identity'], request_sha256=binding['request_sha256'],
                host_sha256=request['host_sha256'], image_digest=binding['image_digest'],
                decode_verified=True, reverse_seek_verified=True, frames=frames, frame_count=count,
                output_sha256=hashlib.sha256(media).hexdigest(), output_bytes=len(media),
                audio_sha256=audio_sha, sound_events_sha256=digest(request['sound_events']),
                probe=dict(streams=streams,
                           format=dict(duration=str(request['constraints']['duration_seconds']))))
            (work / 'render-receipt.json').write_bytes(canonical_bytes(receipt))
        (root / 'render-outcome.json').write_bytes(canonical_bytes(outcome))
        return outcome


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    execution = SyntheticExecution()
    monkeypatch.setattr(renderer, 'execute_method_render', execution)
    value = renderer.MethodAnimationRenderer(tmp_path / 'render', IMAGE, enabled=True)
    return value, execution


def test_disabled_default_and_unadmitted_engines_never_execute(tmp_path, monkeypatch):
    monkeypatch.setattr(renderer, 'execute_method_render', lambda *_: pytest.fail('must not execute'))
    root = tmp_path / 'disabled'
    value = renderer.MethodAnimationRenderer(root, IMAGE)
    with pytest.raises(ServiceError, match='renderer_disabled'):
        value.render('one', SOURCE, document())
    assert not root.exists()
    enabled = renderer.MethodAnimationRenderer(root, IMAGE, enabled=True)
    for engine in ('canvas', 'three'):
        source = dict(SOURCE, engine=engine)
        with pytest.raises(ServiceError, match='engine_not_admitted'):
            enabled.render('one', source, document())
    assert not root.exists()


def test_complete_fragment_can_exceed_old_ten_second_limit(adapter):
    value, execution = adapter
    result = value.render('full-passage', SOURCE, document(14))
    assert result['technical']['status'] == 'passed'
    assert result['media']['duration_seconds'] == 14
    assert result['frame_count'] == 336
    assert result['media']['audio_sha256'] is None
    assert result['runtime']['admission']['status'] == 'admitted'
    assert result['whole_video_watched'] is False and result['audio_observed'] is False
    samples = value.frames('full-passage', max_frames=12)
    assert len(samples) == 12 and samples[0]['timestamp_seconds'] == 0
    assert samples[-1]['timestamp_seconds'] == 335 / 24
    assert all(row['raw'].startswith(b'\xff\xd8') for row in samples)
    restarted = renderer.MethodAnimationRenderer(value.root, IMAGE)
    assert restarted.lookup('full-passage') == result
    assert value.render('full-passage', SOURCE, document(14)) == result
    assert execution.calls == 1


def test_sound_event_binding_is_preserved_and_changed_replay_rejected(adapter):
    value, execution = adapter
    events = [dict(time=.1, duration=.2, frequency=440, gain=.2, waveform='sine')]
    result = value.render('sound-demo', SOURCE, document(), events)
    assert result['media']['audio_sha256']
    assert result['technical']['status'] == 'passed'
    assert value.media_path('sound-demo').is_file()
    with pytest.raises(ServiceError, match='operation_conflict'):
        value.render('sound-demo', SOURCE, document(), [])
    assert execution.calls == 1
    (value._directory('sound-demo') / 'work/sound-effects.wav').write_bytes(b'changed')
    with pytest.raises(ServiceError, match='audio_changed'):
        value.lookup('sound-demo')


def test_fractional_sound_values_bind_original_python_request_bytes(adapter):
    value, _ = adapter
    events = [dict(time=.125, duration=.25, frequency=440.0, gain=1e-9, waveform='triangle')]
    result = value.render('fractional-sound', SOURCE, document(1.5), events)
    assert result['technical']['status'] == 'passed'
    request = json.loads((value._directory('fractional-sound') / 'input/request.json').read_bytes())
    assert request['sound_events_sha256'] == digest(events)


def test_container_adapter_grants_frozen_input_read_access_and_bounds_actual_timeout(tmp_path, monkeypatch):
    """Exercise the actual launch adapter with synthetic Docker CLI replies."""
    root = tmp_path / 'launch'
    (root / 'input/scene/assets').mkdir(parents=True)
    (root / 'input/scene/scene.html').write_bytes(b'<html></html>')
    (root / 'host.mjs').write_bytes(b'// synthetic trusted host')
    request = dict(identity='d' * 64)
    request_raw = canonical_bytes(request)
    (root / 'input/request.json').write_bytes(request_raw)
    plan = dict(directory=str(root), image=IMAGE, repository='registry.example/renderer',
                image_digest='sha256:' + 'a' * 64, request_sha256=digest(request),
                timeout_seconds=60, deadline_at=renderer.time.time() + 45)
    (root / 'render-plan.json').write_bytes(canonical_bytes(plan))
    ownership, calls, timeouts = [], [], []
    monkeypatch.setattr(renderer.sys, 'platform', 'linux')
    monkeypatch.setattr(renderer.docker, '_ownership',
                        lambda path, directory=False: ownership.append((Path(path), directory)))

    def checked(args, deadline):
        calls.append(args)
        if args[:2] == ['image', 'inspect']:
            return json.dumps(dict(image_id='sha256:' + 'b' * 64,
                repo_digests=['registry.example/renderer@sha256:' + 'a' * 64]))
        assert args[:2] == ['container', 'create']
        assert '--network=none' in args and '--read-only' in args
        assert '--user=10001:10001' in args
        return 'c' * 64

    monkeypatch.setattr(renderer.docker, '_checked', checked)
    monkeypatch.setattr(renderer.docker, '_container', lambda *_: dict(
        running=False, exit_code=0, oom_killed=False))
    monkeypatch.setattr(renderer.docker, '_owned', lambda *args, **kwargs: True)
    def command(args, timeout, **kwargs):
        timeouts.append(timeout)
        return dict(code=0)
    monkeypatch.setattr(renderer.docker, '_command', command)
    monkeypatch.setattr(renderer.docker, '_cleanup', lambda **kwargs: dict(
        status='removed_and_confirmed_absent', container_id='c' * 64))
    result = renderer.execute_method_render(root)
    assert result['status'] == 'rendered'
    assert result['cleanup']['status'] == 'removed_and_confirmed_absent'
    assert 30 < result['execution_timeout_seconds'] <= 45
    # Fifteen seconds are reserved for verified cleanup inside this remaining
    # commission budget; the render command never receives the global 60s.
    assert len(timeouts) == 1 and 15 < timeouts[0] <= 30
    assert (root / 'input', True) in ownership
    assert (root / 'input/scene/scene.html', False) in ownership
    assert (root / 'input/request.json', False) in ownership
    assert (root / 'host.mjs', False) in ownership
    assert (root / 'work', True) in ownership
    assert len(calls) == 2


def test_commission_deadline_freezes_first_timeout_without_breaking_replay(adapter, monkeypatch):
    value, execution = adapter
    now = [1_000_000.0]
    monkeypatch.setattr(renderer.time, 'time', lambda: now[0])
    deadline = now[0] + 45
    first = value.render('budgeted', SOURCE, document(), deadline_at=deadline)
    plan = json.loads((value._directory('budgeted') / 'render-plan.json').read_bytes())
    assert plan['timeout_seconds'] == 45 and plan['deadline_at'] == deadline
    now[0] += 60
    assert value.render('budgeted', SOURCE, document(), deadline_at=deadline) == first
    assert execution.calls == 1
    with pytest.raises(ServiceError, match='operation_conflict'):
        value.render('budgeted', SOURCE, document(), deadline_at=deadline + 10)


def test_insufficient_commission_time_rejects_before_durable_claim_or_container(adapter):
    value, execution = adapter
    with pytest.raises(ServiceError, match='deadline_insufficient'):
        value.render('too-late', SOURCE, document(), deadline_at=renderer.time.time() + 29)
    assert not value.root.exists() and execution.calls == 0


def test_late_start_deadline_is_a_known_failure_without_any_docker_call(tmp_path, monkeypatch):
    root = tmp_path / 'late'
    (root / 'input').mkdir(parents=True)
    request = dict(identity='e' * 64)
    (root / 'input/request.json').write_bytes(canonical_bytes(request))
    plan = dict(directory=str(root), image=IMAGE, repository='registry.example/renderer',
        image_digest='sha256:' + 'a' * 64, request_sha256=digest(request),
        timeout_seconds=60, deadline_at=renderer.time.time() + 29)
    (root / 'render-plan.json').write_bytes(canonical_bytes(plan))
    monkeypatch.setattr(renderer.sys, 'platform', 'linux')
    monkeypatch.setattr(renderer.docker, '_checked', lambda *args: pytest.fail('No container may start'))
    monkeypatch.setattr(renderer.docker, '_ownership', lambda *args, **kwargs: pytest.fail('No preparation after expiry'))
    result = renderer.execute_method_render(root)
    assert result['status'] == 'failed' and result['cleanup']['status'] == 'not_created'
    assert 'deadline_insufficient' in result['error']


@pytest.mark.parametrize('failure,expected', [('unknown', None), ('cleanup', None), ('known', False)])
def test_unknown_or_failed_runs_never_reexecute_after_restart(adapter, failure, expected):
    value, execution = adapter
    execution.failure = failure
    result = value.render('one-attempt', SOURCE, document())
    if expected is False:
        assert result['technical']['status'] == 'failed'
        assert result['media'] is None
    else:
        assert result is None
    restarted = renderer.MethodAnimationRenderer(value.root, IMAGE, enabled=True)
    assert restarted.render('one-attempt', SOURCE, document()) == result
    assert execution.calls == 1


def test_simultaneous_callers_share_a_single_external_claim(adapter):
    value, execution = adapter
    execution.entered, execution.release = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(value.render, 'shared', SOURCE, document())
        assert execution.entered.wait(10)
        assert value.render('shared', SOURCE, document()) is None
        execution.release.set()
        assert first.result(10)['technical']['status'] == 'passed'
    assert execution.calls == 1


@pytest.mark.parametrize('duration', [0, 60.1, 1.01])
def test_unadmitted_duration_budget_rejected_before_creating_state(adapter, duration):
    value, execution = adapter
    with pytest.raises(ServiceError):
        value.render('bad-duration', SOURCE, document(duration))
    assert execution.calls == 0
    assert not value.root.exists()


@pytest.mark.parametrize('change', [
    dict(time=.9, duration=.2), dict(gain=.81), dict(frequency=float('nan')),
    dict(waveform='external-file'), dict(duration=6),
])
def test_sound_events_are_bounded_in_the_same_clock(adapter, change):
    value, execution = adapter
    event = dict(time=.1, duration=.2, frequency=440, gain=.2, waveform='sine')
    event.update(change)
    with pytest.raises(ServiceError, match='sound_event_invalid'):
        value.render('bad-sound', SOURCE, document(), [event])
    assert execution.calls == 0


def test_actual_host_sound_synthesis_is_deterministic_and_keeps_event_offsets():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node runtime is unavailable')
    module = renderer.HOST.as_uri()
    events = [dict(time=.25, duration=.1, frequency=440, gain=.2, waveform='sine'),
              dict(time=.6, duration=.1, frequency=400, gain=.15, waveform='noise')]
    script = ('import {synthesizeSoundEvents} from ' + json.dumps(module) + ';'
              'process.stdout.write(synthesizeSoundEvents(' + json.dumps(events) + ',1));')
    results = [subprocess.run([node, '--input-type=module', '-e', script],
        check=True, capture_output=True, timeout=10).stdout for _ in range(2)]
    assert results[0] == results[1]
    with wave.open(io.BytesIO(results[0]), 'rb') as wav:
        assert wav.getframerate() == 48000 and wav.getnchannels() == 1
        assert wav.getsampwidth() == 2 and wav.getnframes() == 48000
        pcm = wav.readframes(48000)
    assert set(pcm[:round(.25 * 48000) * 2]) == {0}
    assert any(pcm[round(.26 * 48000) * 2:round(.34 * 48000) * 2])
    assert set(pcm[round(.36 * 48000) * 2:round(.59 * 48000) * 2]) == {0}
    assert any(pcm[round(.61 * 48000) * 2:round(.69 * 48000) * 2])
