"""Executor lifecycle/tamper tests with a fake Docker transport, no browser.

Synthetic image envelopes here test custody/bounds; they are not video evidence.
"""
from pathlib import Path
import json

import pytest

from video_workbench import animation_lab_capture as executor
from video_workbench.animation_lab_evidence import write_capture_inputs
from video_workbench.animation_lab_samples import compile_sample
from video_workbench.motion import canonical, sha256


RUNTIME = dict(schema='video.motion-runtime.v1', hyperframes='0.8.149',
    hyperframes_source='test-source', node='22.14.0', browser='unit-fixture', ffmpeg='unit-fixture',
    gsap='3.14.2', gsap_sha256='a' * 64, font_sha256='b' * 64,
    npm_lock_sha256='c' * 64, dpkg_sha256='d' * 64, image_digest='sha256:' + 'e' * 64)
IMAGE = 'registry.example/video-motion-renderer:latest@' + RUNTIME['image_digest']
IMAGE_ID = 'sha256:' + 'f' * 64
CONTAINER_ID = '1' * 64
OVERLAY = Path(executor.__file__).parent / 'motion_renderer'


def inputs(tmp_path):
    bundle = compile_sample('diagram-connections', 'candidate')
    prepared = write_capture_inputs(bundle, tmp_path / 'prepared', RUNTIME)[0]
    return Path(prepared['directory'])


class Docker:
    def __init__(self, root, failure=None):
        self.root, self.failure, self.calls = root, failure, []
        self.present = False
        self.metadata = None

    def __call__(self, args, timeout, *, log_path=None):
        self.calls.append(list(args))
        assert 0 < timeout <= 210
        if args[:2] == ['image', 'inspect']:
            digest = '0' * 64 if self.failure == 'image_digest' else 'e' * 64
            return dict(code=0, out=json.dumps(dict(image_id=IMAGE_ID,
                repo_digests=['registry.example/video-motion-renderer@sha256:' + digest])), err='')
        if args[:2] == ['container', 'create']:
            self.present = True
            labels = [args[index + 1] for index, value in enumerate(args) if value == '--label']
            labels = dict(value.split('=', 1) for value in labels)
            self.metadata = dict(id=CONTAINER_ID, name='/' + args[args.index('--name') + 1],
                image=IMAGE_ID, owner=labels[executor._OWNER], capture=labels[executor._CAPTURE],
                running=False, exit_code=0, oom_killed=False)
            if self.failure == 'create_timeout':
                raise TimeoutError('animation_lab_executor_fake_create_timeout')
            if self.failure == 'owner':
                self.metadata['owner'] = 'unrelated-container'
            return dict(code=0, out=CONTAINER_ID, err='')
        if args[:2] == ['container', 'inspect']:
            if self.present:
                return dict(code=0, out=json.dumps(self.metadata), err='')
            return dict(code=1, out='', err='Error: No such container: ' + args[-1])
        if args[:2] == ['container', 'start']:
            Path(log_path).write_bytes(b'synthetic test transport\n')
            if self.failure == 'start_timeout':
                raise TimeoutError('animation_lab_executor_fake_start_timeout')
            if self.failure == 'interrupt':
                raise KeyboardInterrupt()
            if self.failure == 'exit':
                self.metadata['exit_code'] = 12
                return dict(code=0, out='', err='')
            self.output()
            return dict(code=0, out='', err='')
        if args[:2] == ['container', 'rm']:
            assert args == ['container', 'rm', '--force', CONTAINER_ID]
            if self.failure != 'cleanup':
                self.present = False
            return dict(code=0, out=CONTAINER_ID, err='')
        raise AssertionError(args)

    def output(self):
        work = self.root / 'capture-work'
        request_raw = (work / 'capture-request.json').read_bytes()
        request = json.loads(request_raw)
        (work / 'observations').mkdir()
        raw = b'\xff\xd8synthetic-unit-test-not-browser-evidence\xff\xd9'
        (work / 'observations' / 'seek-first.jpg').write_bytes(raw)
        observed = canonical(dict(synthetic_unit_fixture=True))
        (work / 'observations.json').write_bytes(observed)
        receipt = dict(schema='video.animation-lab-capture-receipt.v1',
            capture_identity=request['capture_identity'], request_sha256=sha256(request_raw),
            runtime=RUNTIME, image_digest_source='coordinator_pinned_not_self_measured',
            capture_adapter_sha256=request['capture_adapter_sha256'], observer_sha256=request['observer_sha256'],
            runtime_collector_sha256=request['runtime_collector_sha256'],
            observations_sha256=sha256(observed), frame_count=1,
            frames=[dict(name='seek-first.jpg', sha256=sha256(raw), bytes=len(raw))],
            agent_calls=0, tts_calls=0, production_writes=0, output='sampled_browser_observations_not_mp4')
        if 'engine_assets' in request:
            receipt['engine_assets'] = request['engine_assets']
        if self.failure == 'receipt':
            receipt['request_sha256'] = '0' * 64
        if self.failure == 'work_input':
            (work / 'audio.wav').write_bytes(b'changed')
        if self.failure == 'original_input':
            (self.root / 'audio.wav').write_bytes(b'changed')
        (work / 'capture-receipt.json').write_bytes(canonical(receipt))


def install(monkeypatch, root, failure=None):
    transport = Docker(root, failure)
    monkeypatch.setattr(executor, '_command', transport)
    monkeypatch.setattr(executor, '_ownership', lambda path, **kwargs: None)
    monkeypatch.setattr(executor.sys, 'platform', 'linux')
    return transport


def test_plan_and_cli_default_never_execute_or_write(tmp_path, monkeypatch, capsys):
    root = inputs(tmp_path)
    transport = install(monkeypatch, root)
    runtime_path = tmp_path / 'runtime.json'
    runtime_path.write_bytes(canonical(RUNTIME))
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    assert executor.main(['--directory', str(root), '--runtime', str(runtime_path),
        '--image', IMAGE, '--overlay-dir', str(OVERLAY)]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan['network'] == 'none' and plan['pull'] == 'never'
    assert plan['automatic_retry'] is plan['automatic_adoption'] is False
    assert transport.calls == []
    assert {path.name: path.read_bytes() for path in root.iterdir()} == before


@pytest.mark.parametrize('timeout', [0, 29, 211, float('nan'), True])
def test_excessive_or_invalid_timeout_fails_before_docker(tmp_path, timeout):
    with pytest.raises(ValueError, match='timeout_invalid'):
        executor.capture_plan(inputs(tmp_path), RUNTIME, IMAGE, OVERLAY, timeout)


@pytest.mark.parametrize('image', ['registry.example/renderer:latest',
    'registry.example/renderer:v1@' + RUNTIME['image_digest'],
    'registry.example/renderer:latest@sha256:' + '0' * 64])
def test_missing_latest_digest_or_wrong_runtime_pin_is_refused(tmp_path, image):
    with pytest.raises(ValueError, match='image_mismatch'):
        executor.capture_plan(inputs(tmp_path), RUNTIME, image, OVERLAY)


def test_one_bounded_capture_uses_real_image_id_and_owned_cleanup(tmp_path, monkeypatch):
    root = inputs(tmp_path)
    transport = install(monkeypatch, root)
    original = (root / 'audio.wav').read_bytes()
    outcome = executor.capture_directory(root, RUNTIME, IMAGE, OVERLAY)
    assert outcome['status'] == 'captured' and outcome['actual_image_id'] == IMAGE_ID
    assert outcome['cleanup']['status'] == 'removed_and_confirmed_absent'
    assert outcome['technical_review'] == 'not_run' and not outcome['production_adoption']
    assert (root / 'capture-start.json').exists() and (root / 'capture-outcome.json').exists()
    assert (root / 'capture-receipt.json').exists()
    assert (root / 'audio.wav').read_bytes() == original
    assert (root / 'capture-work' / 'audio.wav').read_bytes() == original
    creates = [args for args in transport.calls if args[:2] == ['container', 'create']]
    starts = [args for args in transport.calls if args[:2] == ['container', 'start']]
    assert len(creates) == len(starts) == 1
    create = creates[0]
    assert IMAGE_ID in create and IMAGE not in create
    for option in ('--pull=never', '--network=none', '--read-only', '--user=10001:10001',
                   '--cap-drop=ALL', '--security-opt=no-new-privileges', '--cpus=2', '--memory=1g'):
        assert option in create
    mounts = [create[index + 1] for index, value in enumerate(create) if value == '--mount']
    assert len(mounts) == 5 and len([row for row in mounts if row.endswith(',readonly')]) == 4
    assert all('/var/run/docker.sock' not in row and '.docker' not in row for row in mounts)
    assert not transport.present
    # Even a successful directory is immutable as an experiment; there is no retry.
    count = len(transport.calls)
    with pytest.raises(ValueError, match='no_overwrite'):
        executor.capture_directory(root, RUNTIME, IMAGE, OVERLAY)
    assert len(transport.calls) == count


@pytest.mark.parametrize('case', ['canvas-queue', 'three-layers'])
def test_engine_capture_mounts_every_bound_observer_and_dependency(tmp_path, monkeypatch, case):
    # This checks the container boundary with synthetic transport/resources;
    # actual Canvas/WebGL execution is verified by separate native captures.
    from video_workbench import animation_lab_samples as samples
    from video_workbench import animation_lab_three as three
    assets = {name: ('// synthetic dependency '+name).encode() for name in three.THREE_ASSET_SHA256}
    monkeypatch.setattr(three, 'THREE_ASSET_SHA256', {name: sha256(raw) for name, raw in assets.items()})
    samples._compiled_case.cache_clear()
    try:
        bundle = compile_sample(case, 'candidate')
        root = Path(write_capture_inputs(bundle, tmp_path / 'prepared', RUNTIME)[0]['directory'])
    finally:
        samples._compiled_case.cache_clear()
    overlay = tmp_path / 'overlay'
    overlay.mkdir()
    for name in [*executor._ADAPTERS, 'engine_observe.mjs']:
        (overlay / name).write_bytes((OVERLAY / name).read_bytes())
    for name, raw in assets.items():
        (overlay / name).write_bytes(raw)
    transport = install(monkeypatch, root)
    outcome = executor.capture_directory(root, RUNTIME, IMAGE, overlay)
    assert outcome['status'] == 'captured'
    create = next(args for args in transport.calls if args[:2] == ['container', 'create'])
    mounts = [create[index+1] for index, value in enumerate(create) if value == '--mount']
    required = {*executor._ADAPTERS, 'engine_observe.mjs'}
    if case == 'three-layers':
        required.update(assets)
    assert len(mounts) == len(required)+1
    for name in required:
        assert any(row.endswith(',target=/opt/renderer/'+name+',readonly') for row in mounts)
    assert outcome['cleanup']['status'] == 'removed_and_confirmed_absent'


@pytest.mark.parametrize('failure,error', [
    ('image_digest', 'actual_image_mismatch'), ('create_timeout', 'fake_create_timeout'),
    ('start_timeout', 'fake_start_timeout'), ('interrupt', 'keyboardinterrupt'),
    ('exit', 'container_exit_failed'), ('receipt', 'receipt_mismatch'),
    ('work_input', 'work_input_changed'), ('original_input', 'original_input_changed'),
    ('cleanup', 'cleanup_not_confirmed'), ('owner', 'created_container_mismatch'),
])
def test_failures_keep_durable_outcome_and_never_promote_or_retry(tmp_path, monkeypatch, failure, error):
    root = inputs(tmp_path)
    transport = install(monkeypatch, root, failure)
    with pytest.raises(executor.CaptureExecutionError, match=error) as raised:
        executor.capture_directory(root, RUNTIME, IMAGE, OVERLAY)
    outcome = raised.value.outcome
    assert outcome == json.loads((root / 'capture-outcome.json').read_bytes())
    assert outcome['status'] == 'failed'
    assert (root / 'capture-start.json').exists()
    assert not (root / 'capture-receipt.json').exists()
    assert not outcome['production_adoption']
    starts = [args for args in transport.calls if args[:2] == ['container', 'start']]
    assert len(starts) <= 1
    if failure == 'image_digest':
        assert outcome['cleanup']['status'] == 'not_created'
        assert not any(args[:2] == ['container', 'create'] for args in transport.calls)
    elif failure in ('cleanup', 'owner'):
        assert outcome['cleanup']['status'] == 'unconfirmed'
        if failure == 'owner':
            assert not any(args[:2] == ['container', 'rm'] for args in transport.calls)
    else:
        assert outcome['cleanup']['status'] == 'removed_and_confirmed_absent'
        assert not transport.present


def test_adapter_or_input_tampering_is_refused_before_a_start_record(tmp_path, monkeypatch):
    root = inputs(tmp_path)
    transport = install(monkeypatch, root)
    (root / 'audio.wav').write_bytes(b'changed')
    with pytest.raises(ValueError, match='input_mismatch'):
        executor.capture_directory(root, RUNTIME, IMAGE, OVERLAY)
    assert transport.calls == [] and not (root / 'capture-start.json').exists()
