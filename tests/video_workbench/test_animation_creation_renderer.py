"""Durability/binding tests with synthetic artifacts, never real scene execution."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import threading

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_animation_create as creation
from video_workbench import animation_creation_renderer as adapter
from video_workbench import animation_experiment_render as isolated
from test_animation_review_gateway import creation_document, result_document


IMAGE = 'registry.example/renderer:latest@sha256:' + 'a' * 64
CONTAINER = 'b' * 64
IMAGE_ID = 'sha256:' + 'c' * 64


def input_pair():
    document = creation_document()
    document['constraints']['duration_seconds'] = 1
    return document, result_document(creation.PROFILE, document)


class SyntheticExecutor:
    """Actual freeze/output validators, fabricated bytes; no encoding claim."""
    def __init__(self, *, failure=None):
        self.calls = 0
        self.failure = failure
        self.entered = self.release = None

    def __call__(self, source, output, *, image, input_document, timeout, assets_dir):
        self.calls += 1
        plan = isolated.prepare_render(source, output, image=image, input_document=input_document,
                                       timeout=timeout, assets_dir=assets_dir)
        request = isolated._frozen(plan)
        token = 'd' * 32
        name = 'animation-experiment-' + request['identity'][:12] + '-' + token[:12]
        start = dict(schema='video.animation-experiment-render-start.v1', started_at='2026-11-10T00:00:00+00:00',
                     container_name=name, owner_token=token, plan=plan)
        (output / 'render-start.json').write_bytes(canonical_bytes(start))
        if self.entered:
            self.entered.set()
            assert self.release.wait(10)
        if self.failure == 'unknown':
            raise RuntimeError('Synthetic lost response, no second render allowed')
        failed = self.failure in ('known_failure', 'cleanup_unknown')
        receipt = technical = None
        if not failed:
            work = output / 'work'
            (work / 'frames').mkdir(parents=True)
            content = b'\xff\xd8synthetic-frame-not-real-media-evidence\xff\xd9'
            frame_sha = hashlib.sha256(content).hexdigest()
            samples, frames = [], []
            count = int(input_document['constraints']['duration_seconds'] * 24)
            targets = input_document['constraints'].get('objects', [])
            for index in range(count + 1):
                frame_name = 'frame-%06d.jpg' % index if index < count else 'reverse-initial.jpg'
                (work / 'frames' / frame_name).write_bytes(content)
                frames.append(dict(name=frame_name, sha256=frame_sha, bytes=len(content)))
                if index < count:
                    objects = [dict(id=target['id'], text=target['label'], visible=True,
                        bounds=dict(x=target['initial'][0] - target['width']/2,
                                    y=target['initial'][1] - target['height']/2,
                                    width=target['width'], height=target['height'])) for target in targets]
                    samples.append(dict(frame_index=index, time=index/24,
                                        frame_sha256=frame_sha, objects=objects))
            if self.failure == 'semantic':
                samples[2]['objects'][0]['text'] = 'Wrong visible identity'
            observations = dict(schema='video.animation-experiment-observations.v1', samples=samples,
                reverse_initial_sha256=frame_sha, reverse_initial_objects=deepcopy(samples[0]['objects']))
            if self.failure == 'reverse':
                observations['reverse_initial_objects'] = [{'different': 'geometry'}]
            observed_raw = canonical_bytes(observations)
            (work / 'observations.json').write_bytes(observed_raw)
            media = b'0000ftypfabricated-unit-media'
            (work / 'output.mp4').write_bytes(media)
            receipt = dict(schema='video.animation-experiment-render-receipt.v1',
                request_sha256=plan['request_sha256'], request_identity=request['identity'],
                host_sha256=plan['host_sha256'], status='rendered', decode_verified=True,
                image_digest=plan['image_digest'], output_sha256=hashlib.sha256(media).hexdigest(),
                observations_sha256=hashlib.sha256(observed_raw).hexdigest(), frames=frames,
                frame_count=count, output_bytes=len(media), output='silent_native_frames_and_mp4',
                production_admitted=False, probe=dict(streams=[dict(codec_type='video', codec_name='h264',
                    width=input_document['constraints']['width'], height=input_document['constraints']['height'],
                    avg_frame_rate='24/1')], format=dict(duration=str(input_document['constraints']['duration_seconds']))))
            if self.failure == 'probe':
                receipt['probe']['streams'][0]['width'] += 1
            (work / 'render-receipt.json').write_bytes(canonical_bytes(receipt))
            _, technical = isolated._outputs(output, request, plan)
        cleanup = (dict(status='unconfirmed', container_id=CONTAINER) if self.failure == 'cleanup_unknown'
                   else dict(status='not_created', container_id=None) if failed
                   else dict(status='removed_and_confirmed_absent', container_id=CONTAINER))
        outcome = dict(schema='video.animation-experiment-render-outcome.v1',
            request_identity=request['identity'], input_sha256=plan['input_sha256'],
            status='failed' if failed else 'rendered', error='synthetic_failure' if failed else None,
            cleanup=cleanup, started_at=start['started_at'], ended_at='2026-11-10T00:00:01+00:00',
            elapsed_seconds=1, image=image, actual_image_id=None if self.failure == 'known_failure' else IMAGE_ID,
            container_name=name, container_id=None if self.failure == 'known_failure' else CONTAINER,
            renderer=plan['renderer'], receipt=receipt, technical=technical,
            visual_quality='not_evaluated', production_adoption=False, automatic_retry=False)
        (output / 'render-outcome.json').write_bytes(canonical_bytes(outcome))
        if failed:
            raise isolated.RenderExecutionError(outcome)
        return outcome


@pytest.fixture
def setup(tmp_path, monkeypatch):
    executor = SyntheticExecutor()
    monkeypatch.setattr(isolated, 'render_scene', executor)
    document, source = input_pair()
    root = tmp_path / 'private-renderer'
    renderer = adapter.DurableAnimationRenderer(root, image=IMAGE, enabled=True)
    return renderer, executor, document, source


def test_disabled_default_does_not_create_state_or_contact_executor(tmp_path, monkeypatch):
    monkeypatch.setattr(isolated, 'render_scene', lambda *a, **k: pytest.fail('No executor admission'))
    root = tmp_path / 'disabled'
    renderer = adapter.DurableAnimationRenderer(root, image=IMAGE)
    document, source = input_pair()
    with pytest.raises(ServiceError, match='renderer_disabled'):
        renderer.render('fragment:initial', source, document)
    assert not root.exists()
    with pytest.raises(ServiceError) as error:
        renderer.lookup('fragment:initial')
    assert error.value.status == 404


def test_real_freeze_validators_and_persistent_lookup_preserve_scope(setup):
    renderer, executor, document, source = setup
    result = renderer.render('fragment:initial', source, document)
    assert set(result) == {'schema', 'operation_id', 'source_sha256', 'technical_passed', 'media_versions', 'checks'}
    assert result['technical_passed'] is True
    assert {'code':'runtime_only_goal_not_evaluated', 'status':'passed'} in result['checks']
    assert result['source_sha256'] == source['source_sha256']
    assert executor.calls == 1
    restarted = adapter.DurableAnimationRenderer(renderer.root, image=IMAGE)
    assert restarted.lookup('fragment:initial') == result
    assert renderer.render('fragment:initial', source, document) == result
    assert executor.calls == 1
    media = restarted.media_path('fragment:initial')
    assert hashlib.sha256(media.read_bytes()).hexdigest() == result['media_versions'][0]['sha256']
    assessment = json.loads((media.parents[2] / 'adapter-assessment.json').read_bytes())
    assert assessment['goal_review_status'] == 'pending_goal_review'
    assert assessment['goal_verified'] is False and assessment['aesthetic_status'] == 'unreviewed'


def test_goal_semantic_failure_is_not_downgraded_to_pending(setup):
    renderer, executor, document, source = setup
    document['constraints'].update(stationary_until_seconds=0, movement_complete_seconds=.5,
        objects=[dict(id='a', label='A', width=20, height=20, initial=[100,100], final=[100,100])])
    executor.failure = 'semantic'
    result = renderer.render('fragment:initial', source, document)
    assert not result['technical_passed']
    assert {'code':'declared_goal_checks', 'status':'failed'} in result['checks']
    assert result['media_versions']  # Valid encoded artifact can still be diagnosed.
    assert renderer.media_path('fragment:initial').is_file()


def test_runtime_failure_under_pending_goal_does_not_pass(setup):
    renderer, executor, document, source = setup
    executor.failure = 'reverse'
    result = renderer.render('fragment:initial', source, document)
    assert not result['technical_passed']
    assert {'code':'runtime_only_goal_not_evaluated', 'status':'failed'} in result['checks']


def test_encoded_probe_cannot_disagree_with_frozen_resolution(setup):
    renderer, executor, document, source = setup
    executor.failure = 'probe'
    with pytest.raises(ServiceError, match='media_probe_invalid'):
        renderer.render('fragment:initial', source, document)
    with pytest.raises(ServiceError, match='media_probe_invalid'):
        renderer.lookup('fragment:initial')
    assert executor.calls == 1


@pytest.mark.parametrize('failure,expected', [('known_failure', False), ('cleanup_unknown', None), ('unknown', None)])
def test_failed_or_unknown_execution_never_retries_after_restart(setup, failure, expected):
    renderer, executor, document, source = setup
    executor.failure = failure
    result = renderer.render('fragment:initial', source, document)
    if expected is False:
        assert result['technical_passed'] is False and result['media_versions'] == []
    else:
        assert result is None
    restarted = adapter.DurableAnimationRenderer(renderer.root, image=IMAGE, enabled=True)
    assert restarted.lookup('fragment:initial') == result
    assert restarted.render('fragment:initial', source, document) == result
    assert executor.calls == 1


def test_parallel_callers_share_one_external_claim(setup):
    renderer, executor, document, source = setup
    executor.entered, executor.release = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(renderer.render, 'fragment:initial', source, document)
        assert executor.entered.wait(10)
        restarted = adapter.DurableAnimationRenderer(renderer.root, image=IMAGE, enabled=True)
        assert restarted.render('fragment:initial', source, document) is None
        assert restarted.lookup('fragment:initial') is None
        assert executor.calls == 1
        executor.release.set()
        assert first.result(10)['technical_passed']
    assert executor.calls == 1


def _process_claim(root, marker, document, source, ready, results):
    def no_actual_renderer(*args, **kwargs):
        with open(marker, 'ab') as stream:
            stream.write(b'one external admission\n')
        raise RuntimeError('Synthetic unknown outcome')
    isolated.render_scene = no_actual_renderer
    renderer = adapter.DurableAnimationRenderer(root, image=IMAGE, enabled=True)
    ready.wait(15)
    try:
        results.put(('ok', renderer.render('fragment:initial', source, document)))
    except Exception as error:
        results.put(('error', type(error).__name__))


def test_processes_share_the_same_durable_attempt(tmp_path):
    document, source = input_pair()
    ctx = multiprocessing.get_context('spawn')
    barrier, results = ctx.Barrier(2), ctx.Queue()
    marker = tmp_path / 'admissions.log'
    processes = [ctx.Process(target=_process_claim,
        args=(str(tmp_path / 'isolated'), str(marker), document, source, barrier, results)) for _ in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(25)
        if process.is_alive():
            process.terminate()
            process.join(5)
            pytest.fail('Synthetic process did not finish')
        assert process.exitcode == 0
    assert [results.get(timeout=5) for _ in range(2)] == [('ok', None), ('ok', None)]
    assert marker.read_bytes().splitlines() == [b'one external admission']


@pytest.mark.parametrize('changed', ['source', 'goal', 'assets'])
def test_same_operation_cannot_change_frozen_input(setup, changed):
    renderer, executor, document, source = setup
    renderer.render('fragment:initial', source, document)
    if changed == 'source':
        source = dict(source, files={'scene.html': source['files']['scene.html'] + '<p>Changed</p>'})
        source.pop('source_sha256')
        source.pop('files_sha256')
    elif changed == 'goal':
        document['goal'] += ' Different goal.'
    else:
        document['assets'] = {'gsap.min.js': 'f' * 64}
    with pytest.raises(ServiceError, match='operation_conflict'):
        renderer.render('fragment:initial', source, document)
    assert executor.calls == 1


@pytest.mark.parametrize('relative', ['binding.json', 'source/scene.html',
    'render/render-plan.json', 'render/render-outcome.json', 'render/input/request.json',
    'render/input-document.json', 'render/work/render-receipt.json',
    'render/work/observations.json', 'render/work/output.mp4',
    'render/work/frames/frame-000000.jpg', 'render/adapters/animation_experiment_host.mjs'])
def test_artifact_tampering_is_rejected_without_another_render(setup, relative):
    renderer, executor, document, source = setup
    renderer.render('fragment:initial', source, document)
    target = renderer._directory('fragment:initial') / relative
    target.write_bytes(b'tampered evidence')
    with pytest.raises(ServiceError) as error:
        renderer.lookup('fragment:initial')
    assert error.value.status == 409 and executor.calls == 1


def test_missing_output_is_pending_and_does_not_restart(setup):
    renderer, executor, document, source = setup
    renderer.render('fragment:initial', source, document)
    renderer.media_path('fragment:initial').unlink()
    assert renderer.lookup('fragment:initial') is None
    assert renderer.render('fragment:initial', source, document) is None
    with pytest.raises(ServiceError, match='render_pending'):
        renderer.media_path('fragment:initial')
    assert executor.calls == 1


def test_lost_ledger_cannot_reset_an_existing_attempt(setup):
    renderer, executor, document, source = setup
    renderer.render('fragment:initial', source, document)
    renderer.ledger.unlink()
    with pytest.raises(ServiceError, match='ledger_missing'):
        renderer.render('fragment:initial', source, document)
    assert executor.calls == 1


def test_media_remains_bound_to_frozen_assets_and_image(tmp_path, monkeypatch):
    executor = SyntheticExecutor()
    monkeypatch.setattr(isolated, 'render_scene', executor)
    assets = tmp_path / 'assets'
    assets.mkdir()
    (assets / 'gsap.min.js').write_bytes(b'trusted unit asset')
    document, source = input_pair()
    document['assets'] = {'gsap.min.js': hashlib.sha256(b'trusted unit asset').hexdigest()}
    renderer = adapter.DurableAnimationRenderer(tmp_path / 'executor', image=IMAGE, assets_dir=assets, enabled=True)
    result = renderer.render('fragment:initial', source, document)
    (assets / 'gsap.min.js').write_bytes(b'a later unrelated asset version')
    assert renderer.lookup('fragment:initial') == result  # Original copied asset is frozen.
    frozen = renderer._directory('fragment:initial') / 'render/input/scene/assets/gsap.min.js'
    frozen.write_bytes(b'changed frozen asset')
    with pytest.raises(ServiceError):
        renderer.lookup('fragment:initial')
    different_image = IMAGE[:-64] + 'e' * 64
    with pytest.raises(ServiceError, match='ledger_binding_changed'):
        adapter.DurableAnimationRenderer(renderer.root, image=different_image).lookup('fragment:initial')
    assert executor.calls == 1


@pytest.mark.parametrize('operation', ['../outside', '/absolute', r'C:\escape', 'bad/name', 'bad\nname'])
def test_operation_identity_cannot_escape_the_private_root(tmp_path, operation):
    document, source = input_pair()
    renderer = adapter.DurableAnimationRenderer(tmp_path / 'executor', image=IMAGE, enabled=True)
    with pytest.raises(ServiceError, match='operation_invalid'):
        renderer.render(operation, source, document)
    assert not renderer.root.exists()


@pytest.mark.parametrize('change', [dict(duration_seconds=11), dict(duration_seconds=.5),
    dict(duration_seconds=1.1), dict(fps=30), dict(width=1920), dict(height=1080)])
def test_unsupported_render_ranges_do_not_claim_or_execute(setup, change):
    renderer, executor, document, source = setup
    document['constraints'].update(change)
    with pytest.raises(ServiceError, match='budget_not_admitted'):
        renderer.render('fragment:initial', source, document)
    assert executor.calls == 0 and not renderer.root.exists()


@pytest.mark.parametrize('engine', ['canvas', 'three'])
def test_other_engines_are_not_admitted_here(setup, engine):
    renderer, executor, document, source = setup
    source['engine'] = engine
    with pytest.raises(ServiceError, match='engine_not_admitted'):
        renderer.render('fragment:initial', source, document)
    assert executor.calls == 0
