"""Explicit, bounded target-host validation; never a production execution path.

Prepare freezes existing image/source/Agent input and this operator before any
browser starts. Execute runs exactly two no-encode fixtures and one four-second
sound render. No downloads, model calls, TTS, deployment, or business writes.
"""
from copy import deepcopy
from datetime import datetime, timezone
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time
from uuid import uuid4
import wave

sys.dont_write_bytecode = True
MAXIMUM_ENCODINGS = 3
MAXIMUM_FIXTURES = 6
IMAGE = ('arm-cluster-master:5000/video-motion-renderer:latest@sha256:'
         '59ee5c356f4c742e515385db4e2b315c9393641bea7d76136f095b27214e79ca')
ORIGINAL = ('skill-method-render-s2/operations/'
            '4d3676705d744e8bc3b163c9355af0c88f211109ad74f103c4b8cc4a319fde69')
EVENTS = [dict(time=.5, duration=.4, frequency=440, gain=.2, waveform='sine'),
          dict(time=2.5, duration=.4, frequency=660, gain=.18, waveform='triangle')]
RUNTIME_FILES = ('video_workbench/animation_method_host.mjs',
                 'video_workbench/motion_renderer/hyperframes_project.mjs',
                 'video_workbench/motion_renderer/engine_runtime.mjs')
MEDIA_INPUT_ROOT = None
MEDIA_TOOL_CLEANUP = []


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    assert path.is_file() and not path.is_symlink(), str(path)
    return path.read_bytes()


def load(path):
    return json.loads(read(path))


def save(path, value):
    with path.open('xb') as stream:
        stream.write(json.dumps(value, sort_keys=True, indent=2).encode())


def command(arguments, *, timeout=60, log=None):
    if arguments[0] in ('ffmpeg', 'ffprobe') and shutil.which(arguments[0]) is None:
        # Some target hosts only have the tools in the already installed image.
        # This separate container only reads an existing MP4 and decodes/probes;
        # it has no browser invocation, encoding output or writable media mount.
        assert MEDIA_INPUT_ROOT is not None
        name = 'hf-existing-media-' + uuid4().hex[:16]
        rewritten = [('/media/' + str(Path(item).relative_to(MEDIA_INPUT_ROOT)))
                     if item.startswith(str(MEDIA_INPUT_ROOT) + '/') else item
                     for item in arguments[1:]]
        create = ['docker', 'container', 'create', '--name', name, '--pull=never',
            '--network=none', '--read-only', '--user=10001:10001', '--cpus=1', '--memory=256m',
            '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit=64',
            '--mount', 'type=bind,source=' + str(MEDIA_INPUT_ROOT) + ',target=/media,readonly',
            '--entrypoint=/usr/bin/' + arguments[0], IMAGE, *rewritten]
        created = False
        try:
            container = command(create).decode().strip()
            created = True
            return command(['docker', 'container', 'start', '--attach', container], timeout=timeout, log=log)
        finally:
            if created:
                command(['docker', 'container', 'rm', '--force', name])
            absent = subprocess.run(['docker', 'container', 'inspect', name],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0
            MEDIA_TOOL_CLEANUP.append(dict(container_name=name, confirmed_absent=absent))
            assert absent
    completed = subprocess.run(arguments, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=timeout)
    if log:
        with log.open('xb') as stream:
            stream.write(completed.stdout)
    assert completed.returncode == 0, completed.stdout.decode(errors='replace')[-2000:]
    return completed.stdout


def setup_modules(ops, source):
    sys.path[:0] = [str(source), '/root/agent-service-deploy-20261026/deploy-python']
    from video_workbench.animation_method_renderer import MethodAnimationRenderer
    from agent_service.contracts import canonical_bytes
    from agent_service.profiles.video_animation_create import source_sha256
    return MethodAnimationRenderer, canonical_bytes, source_sha256


def prepare(ops, root, uploaded_test):
    assert not root.exists() and not root.is_symlink()
    plan = load(ops / 'hyperframes-h2-plan-0e0eb0e-r2/plan.json')
    source = Path(plan['source'])
    origins = load(ops / 'hyperframes-h2-plan-0e0eb0e-r2/origins.json')
    original = Path(origins['html-svg']['root'])
    assert original == ops / ORIGINAL
    binding = load(original / 'binding.json')
    assert binding['request']['constraints']['duration_seconds'] == 4
    assert binding['request']['sound_events'] == []
    assets = Path('/root/video-process-20261105/feedback-20261106/'
                  'animation-capability-redesign/semantic-capabilities-20261109/assets')
    admission = Path(plan['admission'])
    inspected = json.loads(command(['docker', 'image', 'inspect', IMAGE]))[0]
    assert IMAGE.split('@')[0].removesuffix(':latest') + '@' + IMAGE.split('@')[1] in inspected['RepoDigests']
    root.mkdir(mode=0o755)
    fixture_source = root / 'fixture-source'
    for name in RUNTIME_FILES:
        destination = fixture_source / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(read(source / name))
    destination = fixture_source / 'tests/video_workbench/hyperframes-native-host.test.mjs'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(read(uploaded_test))
    for path in fixture_source.rglob('*'):
        path.chmod(0o755 if path.is_dir() else 0o444)
    MethodAnimationRenderer, _, source_sha256 = setup_modules(ops, source)
    # The admission refers to existing ledger rows. Verify those through their
    # real read-only store, rather than copying or inventing historical rows.
    renderer = MethodAnimationRenderer(Path('/var/lib/video-animation-creation/method-renders'),
        IMAGE, assets_dir=assets,
        enabled=True, timeout=180, engine_admission_file=admission,
        hyperframes=True, legacy_bridge=True)
    caps = renderer.capabilities()
    assert caps['allowed_engines'] == ['html-svg', 'canvas', 'three']
    frozen = dict(schema='video.hyperframes-random-sound-validation-input.v1',
        operator_sha256=sha(read(Path(__file__))), maximum_encodings=MAXIMUM_ENCODINGS,
        maximum_no_encode_fixtures=MAXIMUM_FIXTURES, planned_encodings=1,
        planned_no_encode_fixtures=2, model_requests=0, tts_requests=0,
        downloads=0, deployments=0, automatic_retry=False, image=IMAGE,
        actual_image_id=inspected['Id'], source=str(source), assets=str(assets),
        original=str(original), original_binding_sha256=sha(read(original / 'binding.json')),
        original_media_sha256=sha(read(original / 'work/output.mp4')),
        original_source_sha256=source_sha256(binding['source']),
        asset_sha256={name: sha(read(assets / name)) for name in caps['assets']},
        admission=str(admission), admission_sha256=sha(read(admission)),
        fixture_files_sha256={str(path.relative_to(fixture_source)): sha(read(path))
                              for path in fixture_source.rglob('*') if path.is_file()},
        sound_events=EVENTS, original_duration_seconds=4,
        started_at=datetime.now(timezone.utc).isoformat())
    save(root / 'frozen-input.json', frozen)
    save(root / 'budget.json', dict(frozen_input_sha256=sha(read(root / 'frozen-input.json')),
        maximum_encodings=MAXIMUM_ENCODINGS, maximum_no_encode_fixtures=MAXIMUM_FIXTURES))
    return frozen


def verify_frozen(root):
    frozen = load(root / 'frozen-input.json')
    assert load(root / 'budget.json')['frozen_input_sha256'] == sha(read(root / 'frozen-input.json'))
    assert sha(read(Path(__file__))) == frozen['operator_sha256']
    original = Path(frozen['original'])
    assert sha(read(original / 'binding.json')) == frozen['original_binding_sha256']
    assert sha(read(original / 'work/output.mp4')) == frozen['original_media_sha256']
    assert sha(read(Path(frozen['admission']))) == frozen['admission_sha256']
    for name, expected in frozen['asset_sha256'].items():
        assert sha(read(Path(frozen['assets']) / name)) == expected
    for name, expected in frozen['fixture_files_sha256'].items():
        assert sha(read(root / 'fixture-source' / name)) == expected
    for name in RUNTIME_FILES:
        assert sha(read(Path(frozen['source']) / name)) == frozen['fixture_files_sha256'][name]
    return frozen


def random_fixtures(root, frozen):
    save(root / 'random-attempt-once.json', dict(no_encode_fixtures=2,
        encodings=0, frozen_input_sha256=sha(read(root / 'frozen-input.json'))))
    evidence = root / 'random-evidence'
    evidence.mkdir(mode=0o777)
    evidence.chmod(0o777)
    name = 'hf-random-sound-' + uuid4().hex[:16]
    arguments = ['docker', 'container', 'create', '--name', name, '--pull=never',
        '--network=none', '--read-only', '--user=10001:10001', '--cpus=2', '--memory=1g',
        '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit=256',
        '--shm-size=128m', '--tmpfs=/tmp:rw,nosuid,nodev,size=256m', '--init',
        '--env', 'ANIMATION_TEST_PLAYWRIGHT_MODULE=/opt/renderer/node_modules/playwright-core',
        '--env', 'HYPERFRAMES_BROWSER_PATH=/opt/browser/headless_shell',
        '--env', 'HYPERFRAMES_TEST_DIST=/opt/renderer/node_modules/hyperframes/dist',
        '--env', 'ANIMATION_HYPERFRAMES_EVIDENCE_DIR=/evidence',
        '--mount', 'type=bind,source=' + str(root / 'fixture-source') + ',target=/fixture,readonly',
        '--mount', 'type=bind,source=' + str(evidence) + ',target=/evidence',
        '--entrypoint=node', frozen['actual_image_id'], '--test',
        '--test-name-pattern=random', '/fixture/tests/video_workbench/hyperframes-native-host.test.mjs']
    created = False
    started = time.monotonic()
    try:
        container = command(arguments).decode().strip()
        created = True
        command(['docker', 'container', 'start', '--attach', container], timeout=90,
                log=root / 'random-tests.log')
        inspected = json.loads(command(['docker', 'container', 'inspect', container]))[0]
        assert inspected['Image'] == frozen['actual_image_id']
        assert inspected['State']['ExitCode'] == 0 and not inspected['State']['OOMKilled']
    finally:
        if created:
            command(['docker', 'container', 'rm', '--force', name])
        absent = subprocess.run(['docker', 'container', 'inspect', name],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0
        save(root / 'random-cleanup.json', dict(container_name=name, confirmed_absent=absent,
            elapsed_seconds=time.monotonic() - started, cpu_limit=2, memory_limit_bytes=1024 ** 3))
        assert absent
    captures = [load(path) for path in evidence.glob('*/capture.json')]
    rejections = [load(path) for path in evidence.glob('*/rejected.json')]
    assert len(captures) == 1 and len(rejections) == 1
    assert 'seek_mismatch' in rejections[0]['error']
    assert captures[0]['project_runtime']['legacy_bridge'] is False
    assert captures[0]['seek_checks']['fresh_load_verified']
    save(root / 'random-completed.json', dict(status='passed', no_encode_fixtures=2,
        fixed_seed_all_seek_checks=True, unseeded_state_rejected=True,
        source_scope='synthetic_protocol_fixtures_not_agent_artwork', core_invoked=True))


def expected_pcm(events, duration):
    samples = [0.0] * (duration * 48000)
    for event in events:
        start = math.floor(event['time'] * 48000 + .5)
        count = math.floor(event['duration'] * 48000 + .5)
        for index in range(count):
            phase = index * event['frequency'] / 48000
            value = (math.sin(2 * math.pi * phase) if event['waveform'] == 'sine'
                     else 1 - 4 * abs(math.floor(phase + .5) - phase))
            envelope = min(1, index / 240, (count - 1 - index) / 720)
            samples[start + index] += value * event['gain'] * max(0, envelope)
    return [math.floor(max(-1, min(1, value)) * 32767 + .5) for value in samples]


def rms(samples, start, end):
    window = samples[round(start * 48000):round(end * 48000)]
    return math.sqrt(sum(value * value for value in window) / len(window))


def verify_audio(root, directory, events):
    global MEDIA_INPUT_ROOT
    MEDIA_INPUT_ROOT = directory / 'work'
    wav = directory / 'work/sound-effects.wav'
    with wave.open(str(wav), 'rb') as stream:
        assert (stream.getnchannels(), stream.getsampwidth(), stream.getframerate(),
                stream.getnframes()) == (1, 2, 48000, 192000)
        pcm_raw = stream.readframes(stream.getnframes())
    pcm = struct.unpack('<192000h', pcm_raw)
    expected = expected_pcm(events, 4)
    deviations = [abs(actual - target) for actual, target in zip(pcm, expected)]
    assert max(deviations) <= 1
    probe = json.loads(command(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
        '-of', 'json', str(directory / 'work/output.mp4')]))
    save(root / 'independent-ffprobe.json', probe)
    kinds = {item['codec_type']: item for item in probe['streams']}
    assert set(kinds) == {'video', 'audio'}
    assert int(kinds['video']['nb_frames']) == 96
    assert kinds['audio']['sample_rate'] == '48000' and kinds['audio']['channels'] == 1
    assert float(kinds['video']['start_time']) == float(kinds['audio']['start_time']) == 0
    assert float(kinds['video']['duration']) == float(kinds['audio']['duration']) == 4
    command(['ffmpeg', '-v', 'error', '-i', str(directory / 'work/output.mp4'),
             '-f', 'null', '-'], timeout=60, log=root / 'independent-full-decode.log')
    decoded = command(['ffmpeg', '-v', 'error', '-i', str(directory / 'work/output.mp4'),
                       '-map', '0:a:0', '-f', 's16le', '-ac', '1', '-ar', '48000', '-'])
    decoded_samples = struct.unpack('<' + str(len(decoded) // 2) + 'h', decoded)
    assert len(decoded_samples) >= len(pcm)
    aac_errors = [actual - target for actual, target in zip(decoded_samples, pcm)]
    active = [rms(decoded_samples, .55, .85), rms(decoded_samples, 2.55, 2.85)]
    quiet = [rms(decoded_samples, .1, .3), rms(decoded_samples, 1.2, 2.2),
             rms(decoded_samples, 3.3, 3.8)]
    assert min(active) > 500 and max(quiet) < 5
    frequency_checks = []
    event_boundaries = []
    for event in events:
        start, end = round((event['time'] + .05) * 48000), round((event['time'] + .35) * 48000)
        window = decoded_samples[start:end]
        amplitudes = {}
        for frequency in (event['frequency'], event['frequency'] + 100, event['frequency'] - 100):
            real = sum(value * math.cos(2 * math.pi * frequency * index / 48000)
                       for index, value in enumerate(window))
            imag = sum(value * math.sin(2 * math.pi * frequency * index / 48000)
                       for index, value in enumerate(window))
            amplitudes[frequency] = 2 * math.hypot(real, imag) / len(window)
        assert amplitudes[event['frequency']] > max(value for key, value in amplitudes.items()
                                                   if key != event['frequency']) * 10
        frequency_checks.append(amplitudes)
        search_start = round(max(0, event['time'] - .05) * 48000)
        search_end = round((event['time'] + event['duration'] + .05) * 48000)
        audible = [index for index in range(search_start, search_end)
                   if abs(decoded_samples[index]) > 512]
        onset, ending = min(audible) / 48000, (max(audible) + 1) / 48000
        assert abs(onset - event['time']) < .03
        assert abs(ending - event['time'] - event['duration']) < .03
        event_boundaries.append(dict(requested_start=event['time'], decoded_onset=onset,
            requested_end=event['time'] + event['duration'], decoded_ending=ending,
            measurement='absolute_pcm_threshold_512_with_envelope_and_aac_tolerance_30ms'))
    save(root / 'independent-audio-check.json', dict(status='passed',
        original_pcm_samples=192000, actual_aac_decoded_samples=len(decoded_samples),
        aac_padding_samples=len(decoded_samples) - 192000,
        aac_effective_max_lsb_error=max(abs(value) for value in aac_errors),
        aac_effective_rms_lsb_error=math.sqrt(sum(value * value for value in aac_errors) / len(aac_errors)),
        independent_pcm_max_lsb_error=max(deviations),
        independent_pcm_different_samples=sum(value != 0 for value in deviations),
        pcm_sha256=sha(pcm_raw), wav_sha256=sha(read(wav)),
        decoded_pcm_sha256=sha(decoded), active_window_rms=active, silent_window_rms=quiet,
        event_frequency_checks=frequency_checks, decoded_event_boundaries=event_boundaries, events=events,
        independent_media_tool_cleanup=list(MEDIA_TOOL_CLEANUP),
        media_sha256=sha(read(directory / 'work/output.mp4')),
        observation='technical_audio_decode_not_human_listening_or_aesthetic_approval'))


def execute(ops, root):
    frozen = verify_frozen(root)
    save(root / 'execution-once.json', dict(frozen_input_sha256=sha(read(root / 'frozen-input.json')),
        encodings=1, no_encode_fixtures=2, automatic_retry=False))
    random_fixtures(root, frozen)
    verify_frozen(root)
    MethodAnimationRenderer, _, source_sha256 = setup_modules(ops, Path(frozen['source']))
    original = load(Path(frozen['original']) / 'binding.json')
    # The new technical SVG operation has an independent ledger. Its default
    # HTML/SVG identity is explicit; no shared business ledger is modified.
    renderer = MethodAnimationRenderer(root / 'renders', IMAGE, assets_dir=Path(frozen['assets']),
        enabled=True, timeout=180,
        hyperframes=True, legacy_bridge=True)
    document = deepcopy(original['document'])
    document['constraints']['runtime_identity'] = deepcopy(renderer.capabilities()['runtime_identity'])
    save(root / 'sound-attempt-once.json', dict(encodings=1, no_encode_fixtures=0,
        source_sha256=source_sha256(original['source']), sound_events=frozen['sound_events'],
        frozen_input_sha256=sha(read(root / 'frozen-input.json'))))
    result = renderer.render('hyperframes-random-sound-existing-svg-r1', original['source'],
                             document, frozen['sound_events'], deadline_at=time.time() + 180)
    assert result is not None and result['technical_passed'] is True
    directory = renderer._directory('hyperframes-random-sound-existing-svg-r1')
    binding = load(directory / 'binding.json')
    assert binding['source'] == original['source']
    assert source_sha256(binding['source']) == frozen['original_source_sha256']
    assert binding['request']['sound_events'] == frozen['sound_events']
    receipt = load(directory / 'work/render-receipt.json')
    assert receipt['project_runtime']['legacy_bridge'] is True
    assert receipt['project_runtime']['core_seek_count'] > 0
    assert result['media']['audio_sha256'] == sha(read(directory / 'work/sound-effects.wav'))
    assert renderer.lookup('hyperframes-random-sound-existing-svg-r1') == result
    verify_audio(root, directory, frozen['sound_events'])
    verify_frozen(root)
    save(root / 'completed.json', dict(status='passed', actual_encodings=1,
        actual_no_encode_fixtures=2, model_requests=0, tts_requests=0, downloads=0,
        deployments=0, source_unchanged=True, original_media_unchanged=True,
        result=result, sound_render_directory=str(directory),
        human_audio_approval=False, business_skill_published=False,
        elapsed_scope='per_container_start_end_and_limits_not_peak_cpu_rss'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('prepare', 'execute'))
    parser.add_argument('--ops', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--uploaded-test', type=Path)
    arguments = parser.parse_args()
    assert arguments.root.parent == arguments.ops and arguments.root.name.startswith('hyperframes-random-sound-')
    try:
        if arguments.mode == 'prepare':
            assert arguments.uploaded_test is not None
            prepare(arguments.ops, arguments.root, arguments.uploaded_test)
        else:
            execute(arguments.ops, arguments.root)
    except BaseException as error:
        if arguments.root.is_dir():
            target = arguments.root / (arguments.mode + '-failed.json')
            if not target.exists():
                save(target, dict(status='stopped_no_retry', error=str(error)[:2000],
                    exception=type(error).__name__, model_requests=0, deployments=0))
        raise
