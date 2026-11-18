"""Learning adapter custody over real ledgers; peers do not claim art quality."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
from types import SimpleNamespace

import pytest

from agent_service.contracts import ServiceError
from video_workbench.learning_resources import LearningResourceStage, validate_learning_resource_plan
from video_workbench.picture_learning_renderer import PictureLearningRenderer
from video_workbench.video_learning_renderer import VideoLearningRenderer
from video_workbench.animation_method_renderer import MethodAnimationRenderer
from video_workbench import picture_learning_renderer as picture_module
from test_manim_method_integration import OWNER, setup
from test_picture_learning_renderer import document, source, IMAGE, SyntheticExecution
from test_video_learning_renderer import document as video_document, source as video_source


def stage(tmp_path, kind='picture'):
    coordinator, gateway, executor, _ = setup(tmp_path)
    bindings = tmp_path / 'learning.sqlite3'
    with sqlite3.connect(bindings) as db:
        db.execute('CREATE TABLE learning(id TEXT)')
    adapter = LearningResourceStage(tmp_path / 'learning-resource-plans.sqlite3',
        bindings_path=bindings, coordinator=lambda: coordinator, output_kind=kind)
    run = dict(id='learning-experiment', work_kind='reproduction', round=1, mode='initial',
        state='running', deadline_at=time.time() + 300,
        frozen=dict(width=320, height=180, duration_seconds=6,
            criteria=['The correspondence stays readable.'], feedback=''))
    plan = dict(kind='manim', goal='Reproduce the geometry actually observed by the reference author.',
        guidance='Use two corresponding triangles with a shared base.',
        options=dict(width=320, height=180))
    return adapter, coordinator, gateway, executor, run, plan


def finish(adapter, run, plan):
    for _ in range(20):
        state = adapter.advance(OWNER, run, plan)
        if state in {'completed', 'failed', 'cancelled'}:
            return state
    raise AssertionError('learning resources did not stop')


def test_still_uses_one_real_resource_frame_and_keeps_the_editable_recipe(tmp_path):
    adapter, coordinator, gateway, executor, run, plan = stage(tmp_path)
    assert adapter.view(OWNER, run)['status'] == 'pending'
    assert not adapter.shared.path.exists() and gateway.submissions == executor.calls == 0
    assert finish(adapter, run, plan) == 'completed'
    value = adapter.render_context(OWNER, run, plan)
    descriptor = value['generated_resources'][0]
    assert descriptor['manifest']['frame_count'] == 1
    assert descriptor['manifest']['global_start_frame'] == 0
    assert value['resource_placement']['global_start_frame'] == 0
    assert value['manim_source_context']['request']['source']
    receipt = coordinator._get(OWNER, descriptor['scope']['experiment_id'])
    assert 'frame 0 must already contain the complete' in receipt['input']['guidance']
    assert value['assets'] == descriptor['assets_sha256']
    assert gateway.submissions == executor.calls == 1
    assert finish(adapter, run, plan) == 'completed'
    assert gateway.submissions == executor.calls == 1


def test_video_uses_local_resource_time_and_frozen_global_placement(tmp_path):
    adapter, coordinator, gateway, executor, run, plan = stage(tmp_path, 'video')
    plan['options'].update(frame_count=4, global_start_frame=48)
    assert finish(adapter, run, plan) == 'completed'
    value = adapter.render_context(OWNER, run, plan)
    descriptor = value['generated_resources'][0]
    assert descriptor['manifest']['global_start_frame'] == 0
    assert value['resource_placement']['global_start_frame'] == 48
    assert value['resource_placement']['frame_count'] == 4
    assert coordinator.resolve(descriptor)
    changed = deepcopy(run)
    changed['work_kind'] = 'transfer'
    assert adapter.identity(changed) != adapter.identity(run)
    changed['work_kind'], changed['round'] = 'reproduction', 2
    assert adapter.identity(changed) != adapter.identity(run)
    assert gateway.submissions == executor.calls == 1


@pytest.mark.parametrize('kind,start,count', [('picture', 0, 1), ('video', 48, 4)])
def test_supplied_playback_example_executes_real_helper_and_respects_half_open_placement(
        tmp_path, kind, start, count):
    """Catch guidance/API drift: execute the supplied example, not a rewritten call.

    A real author previously guessed a one-object signature and never registered
    seek. Fake image decoding keeps this an interface check, not a browser claim.
    """
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is required for the actual shipped playback module')
    adapter, _, _, _, run, plan = stage(tmp_path, kind)
    plan['options'].update(frame_count=count, global_start_frame=start)
    assert finish(adapter, run, plan) == 'completed'
    context = adapter.render_context(OWNER, run, plan)
    example = context['consumption_guidance'].split('```javascript\n', 1)[1].split('\n```', 1)[0]
    helper = Path(__file__).resolve().parents[2] / 'video_workbench/motion_renderer/manim_method_frames.mjs'
    example = example.replace("'/assets/manim_method_frames.mjs'", json.dumps(helper.as_uri()))
    script = '''
import assert from 'node:assert/strict';
const images = [];
const root = {append(image) { images.push(image); }};
globalThis.document = {getElementById(id) { assert.equal(id, 'manim-resource'); return root; }};
globalThis.window = {};
globalThis.Image = class {
  constructor() { this.style = {}; }
  async decode() {}
  removeAttribute() {}
  remove() {}
};
const manifest = MANIFEST;
EXAMPLE
assert.equal(typeof window.__animationExperiment.seek, 'function');
assert.equal(typeof window.__animationExperiment.ready.then, 'function');
await window.__animationExperiment.ready;
assert.equal(images.length, COUNT);
window.__animationExperiment.seek(START / 24);
assert.equal(images[0].style.display, 'block');
window.__animationExperiment.seek((START + COUNT) / 24);
assert.ok(images.every(image => image.style.display === 'none'));
window.__animationExperiment.seek(START / 24);
assert.equal(images[0].style.display, 'block');
console.log(JSON.stringify({registered: true, frames: images.length, half_open: true}));
'''.replace('MANIFEST', json.dumps(context['generated_resources'][0]['manifest']))
    script = script.replace('EXAMPLE', example).replace('COUNT', str(count)).replace('START', str(start))
    result = subprocess.run([node, '--input-type=module', '-e', script],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == dict(registered=True, frames=count, half_open=True)


def test_uncertain_recipe_resumes_original_paid_identity_after_restart(tmp_path):
    adapter, coordinator, gateway, executor, run, plan = stage(tmp_path)
    gateway.unknown = True
    for _ in range(4):
        adapter.advance(OWNER, run, plan)
    resumed = LearningResourceStage(adapter.shared.path, bindings_path=adapter.shared.bindings_path,
        coordinator=lambda: coordinator, output_kind='picture')
    assert finish(resumed, run, plan) == 'completed'
    assert gateway.submissions == executor.calls == 1
    with pytest.raises(ServiceError, match='not_ready'):
        resumed.shared.context('another-owner', resumed.identity(run))


def test_continue_supplies_owned_python_but_fresh_cannot_inherit_it(tmp_path):
    adapter, coordinator, gateway, executor, run, plan = stage(tmp_path)
    assert finish(adapter, run, plan) == 'completed'
    original = adapter.context(OWNER, run)
    second = deepcopy(run)
    second.update(round=2, resource_baseline_descriptor=original,
        resource_prior_review=dict(issues=['Make the area relation clear.']))
    assert finish(adapter, second, plan) == 'completed'
    descriptor = adapter.context(OWNER, second)
    stored = coordinator._get(OWNER, descriptor['scope']['experiment_id'])
    assert 'baseline' in stored['input']['guidance']
    assert 'Make the area relation clear.' in stored['input']['guidance']
    iteration = json.loads(stored['input']['guidance'].split('\n', 1)[1])
    assert iteration['baseline']['request']['source'] == coordinator.source_context(OWNER, original)['request']['source']
    fresh = deepcopy(second)
    fresh.update(id='fresh-experiment', mode='fresh')
    with pytest.raises(ServiceError, match='fresh_baseline_forbidden'):
        adapter.advance(OWNER, fresh, plan)
    assert gateway.submissions == executor.calls == 2


def test_fresh_can_refine_its_own_completed_first_round_but_cannot_forge_an_old_round(tmp_path):
    adapter, coordinator, gateway, executor, run, plan = stage(tmp_path)
    run['mode'] = 'fresh'
    assert finish(adapter, run, plan) == 'completed'
    baseline = adapter.context(OWNER, run)
    second = deepcopy(run)
    second.update(round=2, resource_baseline_descriptor=baseline,
        candidates=[dict(round=1, work_kind='reproduction', resource_descriptor=baseline)])
    assert finish(adapter, second, plan) == 'completed'
    value = coordinator._get(OWNER, adapter.context(OWNER, second)['scope']['experiment_id'])
    iteration = json.loads(value['input']['guidance'].split('\n', 1)[1])
    assert iteration['baseline']['descriptor_sha256'] == baseline['sha256']
    assert iteration['baseline']['request']['source'] == coordinator.source_context(OWNER, baseline)['request']['source']
    forged = deepcopy(second)
    forged['id'] = 'another-fresh-experiment'
    with pytest.raises(ServiceError):
        adapter.advance(OWNER, forged, plan)
    assert gateway.submissions == executor.calls == 2


def test_cancel_before_plan_does_not_claim_recipe_or_resource(tmp_path):
    adapter, _, gateway, executor, run, plan = stage(tmp_path)
    assert adapter.advance(OWNER, run, plan, cancelled=True) == 'cancelled'
    assert not adapter.shared.path.exists() and gateway.submissions == executor.calls == 0


def test_retry_adopts_completed_stopped_resource_instead_of_paying_again(tmp_path):
    adapter, _, gateway, executor, run, plan = stage(tmp_path)
    assert finish(adapter, run, plan) == 'completed'
    original = adapter.context(OWNER, run)
    run['state'] = 'failed'
    retry = deepcopy(run)
    retry.update(id='technical-retry', mode='retry', state='running', deadline_at=time.time() + 300)
    adapter.restore_retry(OWNER, retry, plan, run, plan)
    assert finish(adapter, retry, plan) == 'completed'
    assert adapter.context(OWNER, retry) == original
    assert gateway.submissions == executor.calls == 1


@pytest.mark.parametrize('kind,options', [
    ('picture', dict(frame_count=2)),
    ('picture', dict(global_start_frame=1)),
    ('video', dict(frame_count=61)),
    ('video', dict(width=1280, height=720, frame_count=60)),
    ('video', dict(global_start_frame=140, frame_count=24)),
])
def test_invalid_resource_plan_is_rejected_before_paid_work(kind, options):
    value = dict(kind='manim', goal='Geometry', guidance='', options=options)
    with pytest.raises(ServiceError):
        validate_learning_resource_plan(value, output_kind=kind, duration_seconds=6)


def test_picture_renderer_freezes_exact_resource_and_rejects_missing_or_changed_assets(tmp_path):
    adapter, coordinator, _, _, run, plan = stage(tmp_path)
    assert finish(adapter, run, plan) == 'completed'
    resource = adapter.render_context(OWNER, run, plan)
    runtime = SimpleNamespace(image=IMAGE, assets_dir=None, enabled=True, timeout=60,
        generated_resource_resolver=coordinator.resolve,
        capabilities=lambda:dict(allowed_engines=['html-svg'], assets={}, runtime_identity={}))
    renderer = PictureLearningRenderer(tmp_path/'picture-renders', runtime_renderer=runtime)
    context = document()
    context['constraints']['generated_resources'] = resource['generated_resources']
    context['assets'] = resource['assets']
    binding = renderer._snapshot('still-with-manim', source(), context)
    assert binding['request']['schema'] == 'video.picture-learning-render-request.v2'
    assert binding['request']['generated_resources'] == resource['generated_resources']
    assert binding['request']['assets_sha256'] == resource['assets']
    broken = deepcopy(context)
    broken['assets'].pop(next(iter(broken['assets'])))
    with pytest.raises(ServiceError, match='resource_assets_missing'):
        renderer._snapshot('missing-resource', source(), broken)
    broken = deepcopy(context)
    broken['constraints']['generated_resources'][0]['scope']['owner'] = 'another-owner'
    with pytest.raises(ServiceError, match='not_owned'):
        renderer._snapshot('foreign-resource', source(), broken)


def test_picture_render_copies_and_rechecks_resource_bytes_and_consumption_receipt(tmp_path, monkeypatch):
    adapter, coordinator, _, _, run, plan = stage(tmp_path)
    assert finish(adapter, run, plan) == 'completed'
    resource = adapter.render_context(OWNER, run, plan)
    runtime = SimpleNamespace(image=IMAGE, assets_dir=None, enabled=True, timeout=60,
        generated_resource_resolver=coordinator.resolve,
        capabilities=lambda:dict(allowed_engines=['html-svg'], assets={}, runtime_identity={}))
    # PNG resource paths include their immutable IDs; keep the test render root
    # short enough for Windows os.open, while all evidence remains in basetemp.
    renderer = PictureLearningRenderer(tmp_path.parent/'pr', runtime_renderer=runtime)
    context = document()
    context['constraints']['generated_resources'] = resource['generated_resources']
    context['assets'] = resource['assets']
    execute = SyntheticExecution()
    def with_resources(root):
        result = execute(root)
        request = json.loads((root/'input/request.json').read_bytes())
        for name, checksum in request['assets_sha256'].items():
            assert picture_module.digest_bytes((root/'input/scene/assets'/name).read_bytes()) == checksum
        receipt = json.loads((root/'work/render-receipt.json').read_bytes())
        receipt['generated_resource_observation'] = dict(schema='video.manim-method-resource-observation.v1',
            scope='loaded_resources_not_visual_acceptance', helpers_loaded=True,
            resources_sha256=[item['sha256'] for item in request['generated_resources']],
            loaded_png_paths=sorted('/assets/' + name for name in resource['assets'] if name.endswith('.png')))
        (root/'work/render-receipt.json').write_text(json.dumps(receipt))
        return result
    monkeypatch.setattr(picture_module, 'execute_method_render', with_resources)
    result = renderer.render('consumed-picture', source(), context)
    assert result['technical_passed'] and result['stop_proven']
    assert result['runtime']['generated_resources'] == resource['generated_resources']
    assert result['runtime']['generated_resource_observation']['scope'] == 'loaded_resources_not_visual_acceptance'
    assert renderer.render('consumed-picture', source(), context) == result
    assert execute.calls == 1
    png_name = next(name for name in resource['assets'] if name.endswith('.png'))
    path = renderer._directory('consumed-picture')/'input/scene/assets'/png_name
    path.write_bytes(path.read_bytes() + b'changed')
    with pytest.raises(ServiceError, match='assets_changed'):
        renderer.lookup('consumed-picture')


def test_video_adapter_keeps_generated_resources_and_global_placement_on_real_v5_request(tmp_path):
    adapter, coordinator, _, _, run, plan = stage(tmp_path, 'video')
    plan['options'].update(frame_count=4, global_start_frame=48)
    assert finish(adapter, run, plan) == 'completed'
    resource = adapter.render_context(OWNER, run, plan)
    runtime = MethodAnimationRenderer(tmp_path/'base-renderer', IMAGE, enabled=True, timeout=60,
        hyperframes=True, legacy_bridge=True, generated_resource_resolver=coordinator.resolve)
    renderer = VideoLearningRenderer(tmp_path/'video-renders', runtime_renderer=runtime)
    context = video_document()
    context['constraints'].update(duration_seconds=6,
        generated_resources=resource['generated_resources'], resource_placement=resource['resource_placement'])
    context['assets'] = resource['assets']
    binding = renderer._snapshot('video-with-manim', video_source(), context, [])
    assert binding['request']['schema'] == 'video.animation-method-render-request.v5'
    assert binding['request']['generated_resources'] == resource['generated_resources']
    assert binding['request']['assets_sha256'] == resource['assets']
    assert binding['document']['constraints']['resource_placement']['global_start_frame'] == 48
    assert binding['document'] == context
