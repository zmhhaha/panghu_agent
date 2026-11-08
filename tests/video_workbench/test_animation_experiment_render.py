"""Boundary and independent-goal checks; fake Docker media is never browser evidence."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from video_workbench import animation_experiment_render as renderer


IMAGE = 'registry.example/renderer:latest@sha256:' + 'a' * 64
IMAGE_ID = 'sha256:' + 'b' * 64
CONTAINER = 'c' * 64


def document(objects=None):
    return dict(schema='video.animation-experiment-input.v1', experiment_id='tracking',
        capability='identity', goal='Follow A', assets={}, prompt_revision='v1', guidance='',
        constraints=dict(width=320, height=180, fps=24, duration_seconds=1,
            **({'objects': objects} if objects is not None else {})))


def prepare(tmp_path, doc=None):
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    (bundle / 'scene.html').write_text('<!doctype html><div>A</div>', encoding='utf-8')
    root = tmp_path / 'render'
    renderer.prepare_render(bundle, root, image=IMAGE, input_document=doc or document())
    return root


def test_preparation_freezes_only_source_and_never_runs_docker(tmp_path, monkeypatch):
    monkeypatch.setattr(renderer.docker, '_command', lambda *a, **k: pytest.fail('No Docker during preparation'))
    root = prepare(tmp_path)
    plan = json.loads((root / 'render-plan.json').read_bytes())
    assert plan['network'] == 'none' and plan['read_only_root'] and not plan['automatic_retry']
    assert (root / 'input' / 'scene' / 'scene.html').read_text() == '<!doctype html><div>A</div>'
    assert not (root / 'work').exists()
    with pytest.raises(ValueError, match='no_overwrite'):
        renderer.prepare_render(tmp_path / 'bundle', root, image=IMAGE, input_document=document())


@pytest.mark.parametrize('image', ['registry.example/renderer:latest',
    'registry.example/renderer:version1@sha256:' + 'a' * 64])
def test_unpinned_or_version_tag_is_rejected(tmp_path, image):
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    (bundle / 'scene.html').write_text('test')
    with pytest.raises(ValueError, match='image_pin_required'):
        renderer.prepare_render(bundle, tmp_path / 'render', image=image, input_document=document())
    assert not (tmp_path / 'render').exists()


def test_extra_files_and_asset_hash_mismatch_are_rejected_before_output(tmp_path):
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    (bundle / 'scene.html').write_text('test')
    (bundle / 'secret.txt').write_text('must not mount')
    with pytest.raises(ValueError, match='bundle_files_invalid'):
        renderer.prepare_render(bundle, tmp_path / 'render', image=IMAGE, input_document=document())
    (bundle / 'secret.txt').unlink()
    assets = tmp_path / 'assets'
    assets.mkdir()
    (assets / 'gsap.min.js').write_text('not declared bytes')
    doc = document()
    doc['assets'] = {'gsap.min.js': '0' * 64}
    with pytest.raises(ValueError, match='asset_digest_mismatch'):
        renderer.prepare_render(bundle, tmp_path / 'render', image=IMAGE, input_document=doc, assets_dir=assets)
    assert not (tmp_path / 'render').exists()


@pytest.mark.parametrize('change', [dict(duration_seconds=11), dict(fps=30),
    dict(width=1920), dict(duration_seconds=float('nan')), dict(fps=True)])
def test_render_frame_and_resource_budgets_are_frozen_before_execution(tmp_path, change):
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    (bundle / 'scene.html').write_text('test')
    doc = document()
    doc['constraints'].update(change)
    with pytest.raises(ValueError, match='budget_invalid'):
        renderer.prepare_render(bundle, tmp_path / 'render', image=IMAGE, input_document=doc)


def measured():
    constraints = dict(width=1280, height=720, fps=24, duration_seconds=8,
        stationary_until_seconds=1, movement_complete_seconds=6.5,
        validation=dict(position_tolerance_px=2, max_step_px=112),
        objects=[dict(id='A', label='A', width=112, height=112, initial=[320, 360], final=[960, 360])])
    samples = []
    for index in range(192):
        time = index / 24
        progress = max(0, min(1, (time - 1) / 5.5))
        samples.append(dict(frame_index=index, time=time, frame_sha256=str(index),
            objects=[dict(id='A', text='A', visible=True,
                bounds=dict(x=264 + 640 * progress, y=304, width=112, height=112))]))
    evidence = dict(schema='video.animation-experiment-observations.v1', samples=samples,
        reverse_initial_sha256='0', reverse_initial_objects=deepcopy(samples[0]['objects']))
    return constraints, evidence


def test_independent_goal_checks_use_measured_coordinates_and_reverse_frames():
    constraints, evidence = measured()
    result = renderer.validate_observations(evidence, constraints)
    assert result['status'] == 'passed' and result['metrics']['travelled_px']['A'] == pytest.approx(640)
    assert not result['aesthetic_approval']
    evidence['claims'] = {'all_targets_reached': True, 'all_checks_passed': True}
    evidence['samples'][-1]['objects'][0]['bounds']['x'] = 0
    result = renderer.validate_observations(evidence, constraints)
    assert result['status'] == 'failed'
    assert 'initial_or_final_position_mismatch' in result['issues']
    assert 'movement_discontinuity' in result['issues']


@pytest.mark.parametrize('failure,expected', [('identity', 'identity_missing_or_duplicated'),
    ('label', 'object_identity_not_visible'), ('reverse', 'reverse_seek_pixels_differ'),
    ('stationary', 'initial_or_final_position_mismatch')])
def test_identity_labels_initial_hold_and_reverse_pixels_are_not_self_reported(failure, expected):
    constraints, evidence = measured()
    if failure == 'identity':
        evidence['samples'][50]['objects'].append(deepcopy(evidence['samples'][50]['objects'][0]))
    elif failure == 'label':
        evidence['samples'][50]['objects'][0]['text'] = 'B'
    elif failure == 'reverse':
        evidence['reverse_initial_sha256'] = 'different real frame'
    else:
        evidence['samples'][5]['objects'][0]['bounds']['x'] += 30
    assert expected in renderer.validate_observations(evidence, constraints)['issues']


class Docker:
    """Synthetic custody protocol only; fabricated bytes do not prove encoding."""
    def __init__(self, root, failure=None):
        self.root, self.failure, self.calls, self.present = root, failure, [], False
        self.metadata = None

    def __call__(self, args, timeout, *, log_path=None):
        self.calls.append(args)
        if args[:2] == ['image', 'inspect']:
            return dict(code=0, out=json.dumps(dict(image_id=IMAGE_ID,
                repo_digests=['registry.example/renderer@sha256:' + 'a' * 64])), err='')
        if args[:2] == ['container', 'create']:
            self.present = True
            labels = dict(args[index + 1].split('=', 1) for index, arg in enumerate(args) if arg == '--label')
            self.metadata = dict(id=CONTAINER, name='/' + args[args.index('--name') + 1], image=IMAGE_ID,
                owner=labels[renderer.docker._OWNER], capture=labels[renderer.docker._CAPTURE],
                running=False, exit_code=0, oom_killed=False)
            if self.failure == 'create_timeout':
                raise TimeoutError('fake_create_timeout')
            return dict(code=0, out=CONTAINER, err='')
        if args[:2] == ['container', 'inspect']:
            return dict(code=0, out=json.dumps(self.metadata), err='') if self.present else dict(code=1, out='', err='No such container')
        if args[:2] == ['container', 'start']:
            Path(log_path).write_bytes(b'unit transport, not media evidence')
            self.output()
            return dict(code=0, out='', err='')
        if args[:2] == ['container', 'rm']:
            self.present = self.failure == 'cleanup'
            return dict(code=0, out='', err='')
        raise AssertionError(args)

    def output(self):
        root = self.root / 'work'
        request_raw = (self.root / 'input' / 'request.json').read_bytes()
        request = json.loads(request_raw)
        (root / 'frames').mkdir()
        samples, frames = [], []
        for index in range(25):
            name = 'frame-%06d.jpg' % index if index < 24 else 'reverse-initial.jpg'
            content = b'\xff\xd8fake-unit-frame\xff\xd9'
            (root / 'frames' / name).write_bytes(content)
            frames.append(dict(name=name, sha256=renderer.digest(content), bytes=len(content)))
            if index < 24:
                samples.append(dict(frame_index=index, time=index / 24,
                    frame_sha256=renderer.digest(content), objects=[]))
        observed = renderer.canonical(dict(schema='video.animation-experiment-observations.v1',
            samples=samples, reverse_initial_sha256=frames[0]['sha256'], reverse_initial_objects=[]))
        (root / 'observations.json').write_bytes(observed)
        media = b'0000ftypfake-unit-media'
        (root / 'output.mp4').write_bytes(media)
        receipt = dict(schema='video.animation-experiment-render-receipt.v1',
            request_sha256=renderer.digest(request_raw), request_identity=request['identity'],
            host_sha256=request['host_sha256'], status='rendered', decode_verified=True,
            output_sha256=renderer.digest(media), observations_sha256=renderer.digest(observed), frames=frames)
        if self.failure == 'receipt':
            receipt['request_sha256'] = '0' * 64
        (root / 'render-receipt.json').write_bytes(renderer.canonical(receipt))


def install(monkeypatch, root, failure=None):
    transport = Docker(root, failure)
    monkeypatch.setattr(renderer.docker, '_command', transport)
    monkeypatch.setattr(renderer.docker, '_ownership', lambda *a, **k: None)
    monkeypatch.setattr(renderer.sys, 'platform', 'linux')
    return transport


def test_one_render_uses_separate_readonly_sources_and_owned_cleanup(tmp_path, monkeypatch):
    root = prepare(tmp_path)
    transport = install(monkeypatch, root)
    outcome = renderer.execute_render(root)
    assert outcome['status'] == 'rendered' and not outcome['production_adoption']
    assert outcome['technical']['status'] == 'pending_goal_review'
    assert outcome['cleanup']['status'] == 'removed_and_confirmed_absent'
    create = next(args for args in transport.calls if args[:2] == ['container', 'create'])
    for option in ('--network=none', '--read-only', '--pull=never', '--cap-drop=ALL', '--user=10001:10001'):
        assert option in create
    mounts = [create[index + 1] for index, arg in enumerate(create) if arg == '--mount']
    assert len(mounts) == 3 and sum(item.endswith('readonly') for item in mounts) == 2
    assert not any('docker.sock' in item or '.docker' in item for item in mounts)
    with pytest.raises(ValueError, match='no_retry'):
        renderer.execute_render(root)


@pytest.mark.parametrize('failure,error', [('create_timeout', 'fake_create_timeout'),
    ('receipt', 'receipt_invalid'), ('cleanup', 'cleanup_unconfirmed')])
def test_failure_is_durable_and_never_adds_an_attempt(tmp_path, monkeypatch, failure, error):
    root = prepare(tmp_path)
    transport = install(monkeypatch, root, failure)
    with pytest.raises(renderer.RenderExecutionError, match=error) as caught:
        renderer.execute_render(root)
    assert json.loads((root / 'render-outcome.json').read_bytes()) == caught.value.outcome
    assert len([args for args in transport.calls if args[:2] == ['container', 'create']]) == 1
    assert caught.value.outcome['status'] == 'failed'
    with pytest.raises(ValueError, match='no_retry'):
        renderer.execute_render(root)


def test_source_tampering_after_preparation_is_rejected_before_docker(tmp_path, monkeypatch):
    root = prepare(tmp_path)
    install(monkeypatch, root)
    (root / 'input' / 'scene' / 'scene.html').write_text('different output')
    with pytest.raises(ValueError, match='scene_changed'):
        renderer.execute_render(root)
    assert not (root / 'render-start.json').exists()
