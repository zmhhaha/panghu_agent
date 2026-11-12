"""Engine-aware production custody; synthetic sources do not prove quality.

Real native-context/seek measurements are covered by creative-engine-host.test.mjs.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench.creative_contract import scene_context, validate_scene_package
from video_workbench.creative_method_contract import prepare_core_task, build_scene_input, collect_scene_result
from video_workbench.creative_renderer import ENGINE_METHOD_COMPILER, compile_creative_project
from video_workbench.creative_snapshot import freeze_snapshot_v4, verify_snapshot_v4
from video_workbench.motion_render_k8s import _creative_engine_observation
from video_workbench.motion_renderer.job import freeze_input
from video_workbench.motion_snapshot import _runtime
from test_creative_method_contract import delivery, production_result
from test_creative_production import source_scene
from test_creative_renderer import node_verify
from test_motion_snapshot import RUNTIME

ROOT = Path(__file__).resolve().parents[2]
THREE_SHA = '08fd7545d13d2c7fb65ab691530a802dafefd638596501854f267d0fb13c39e7'
LICENSE_SHA = '4c40a1ef62450b857c3b2aaf294936304cd552d965fbcd9d32d4c5bcf4ba4454'
RUNTIME_V2 = dict(RUNTIME, schema='video.motion-runtime.v2', three='0.170.0', three_sha256=THREE_SHA,
    three_license_sha256=LICENSE_SHA, engine_runtime_sha256=hashlib.sha256(
        (ROOT / 'video_workbench/motion_renderer/engine_runtime.mjs').read_bytes()).hexdigest(),
    creative_host_sha256=hashlib.sha256((ROOT / 'video_workbench/motion_renderer/creative.mjs').read_bytes()).hexdigest())


def engine_scene(engine='canvas', *, identity_change=None):
    scene = source_scene()
    original = scene_context(scene['creative']['package'])
    identity = dict(image_digest=RUNTIME_V2['image_digest'], host_sha256=RUNTIME_V2['creative_host_sha256'],
        engine_runtime_sha256=RUNTIME_V2['engine_runtime_sha256'], admission_id='synthetic-formal-admission')
    if identity_change:
        identity[identity_change] = ('sha256:' if identity_change == 'image_digest' else '') + 'f' * 64
    core = prepare_core_task(**{key: original[key] for key in ('scene_id', 'title', 'visual_description',
        'narration', 'beats', 'alignment', 'audio_sha256', 'style', 'instruction')},
        duration_seconds=scene['audio']['duration_seconds'], allowed_engines=[engine],
        runtime_identity=identity, assets={'three.module.min.js': THREE_SHA} if engine == 'three' else {},
        engine_aware=True)
    offered = delivery(core)
    offered['allowed_engines'] = [engine]
    offered['sha256'] = digest({key: value for key, value in offered.items() if key != 'sha256'})
    document = build_scene_input(core_task=core, method_delivery=offered, engine_aware=True)
    result = production_result(document)
    result['source']['engine'] = engine
    scene['creative']['package'] = collect_scene_result(document, result)
    return scene


def compile_scene(scene, **options):
    value = scene['creative']
    return compile_creative_project(value['package'], value['beats'], value['alignment'], **options)


@pytest.mark.parametrize('engine', ['html-svg', 'canvas', 'three'])
def test_new_engine_package_preserves_original_audio_and_freezes_distinct_compiler(tmp_path, engine):
    scene = engine_scene(engine)
    package = scene['creative']['package']
    assert validate_scene_package(package) == package
    assert package['schema'] == 'video.creative-scene.v3'
    files = compile_scene(scene, runtime_fingerprint=RUNTIME_V2, leading_seconds=.25, trailing_seconds=.5)
    timeline, manifest = (json.loads(files[name]) for name in ('timeline.json', 'manifest.json'))
    assert timeline['compiler'] == manifest['compiler'] == ENGINE_METHOD_COMPILER
    assert manifest['engine'] == timeline['creative']['engine'] == engine
    assert timeline['audio_sha256'] == scene['audio']['sha256']
    assert timeline['audio_seconds'] == scene['audio']['duration_seconds']
    assert timeline['leading_seconds'] == .25 and timeline['trailing_seconds'] == .5
    assert manifest['engine_assets'] == ({'three.module.min.js': THREE_SHA} if engine == 'three' else {})
    snapshot = freeze_snapshot_v4('engine-fixture', 1, 'Protocol fixture', [scene], runtime_fingerprint=RUNTIME_V2)
    assert verify_snapshot_v4(snapshot) == snapshot
    for name, raw in files.items():
        (tmp_path / name).write_bytes(raw)
    (tmp_path / 'audio.wav').write_bytes(scene['audio']['data'])
    assert freeze_input(tmp_path, image_digest=RUNTIME_V2['image_digest'])['unbound_experiment'] is False
    process = node_verify(tmp_path, hashlib.sha256(files['manifest.json']).hexdigest())
    assert process.returncode == 0, process.stderr


@pytest.mark.parametrize('runtime', [None, RUNTIME])
def test_new_engine_admission_requires_engine_aware_runtime(runtime):
    with pytest.raises(ServiceError, match='creative_engine_runtime_required'):
        compile_scene(engine_scene(), runtime_fingerprint=runtime)


@pytest.mark.parametrize('field', ['image_digest', 'host_sha256', 'engine_runtime_sha256'])
def test_render_cannot_borrow_another_native_host_identity(field):
    with pytest.raises(ServiceError, match='creative_engine_runtime_identity_mismatch'):
        compile_scene(engine_scene(identity_change=field), runtime_fingerprint=RUNTIME_V2)


@pytest.mark.parametrize('field', ['three', 'three_sha256', 'three_license_sha256', 'engine_runtime_sha256', 'creative_host_sha256'])
def test_runtime_rejects_missing_or_changed_engine_identity(field):
    changed = dict(RUNTIME_V2)
    changed.pop(field)
    with pytest.raises(ServiceError):
        _runtime(changed)
    changed = dict(RUNTIME_V2, **{field: 'changed'})
    with pytest.raises(ServiceError):
        _runtime(changed)


def test_pinned_vendor_bytes_and_license_are_build_inputs():
    directory = ROOT / 'video_workbench/motion_renderer/vendor'
    assert hashlib.sha256((directory / 'three.module.min.js').read_bytes()).hexdigest() == THREE_SHA
    assert hashlib.sha256((directory / 'three.LICENSE.txt').read_bytes()).hexdigest() == LICENSE_SHA
    assert b'MIT License' in (directory / 'three.LICENSE.txt').read_bytes()


def native_observation(engine='three'):
    row = dict(kind='webgl2' if engine == 'three' else '2d', width=320, height=180,
               connected=True, visible=True)
    if engine == 'three':
        row.update(renderer='ANGLE (Google, Vulkan SwiftShader)', software=True, context_lost=False, gl_error=0)
    return dict(engine=engine, revision='animation-engine-runtime-1', contexts=[row])


@pytest.mark.parametrize('engine', ['canvas', 'three'])
def test_collector_accepts_only_native_observation_shape(engine):
    value = native_observation(engine)
    assert _creative_engine_observation(value, engine) == value


@pytest.mark.parametrize('change', ['no-native', 'hardware', 'lost', 'gl-error', 'hidden', 'self-report'])
def test_collector_rejects_missing_software_context_or_forged_extra_declaration(change):
    value = native_observation()
    if change == 'no-native': value['contexts'] = []
    elif change == 'hardware': value['contexts'][0].update(renderer='Hardware GPU', software=False)
    elif change == 'lost': value['contexts'][0]['context_lost'] = True
    elif change == 'gl-error': value['contexts'][0]['gl_error'] = 1280
    elif change == 'hidden': value['contexts'][0]['visible'] = False
    else: value['approved'] = True
    with pytest.raises(ServiceError):
        _creative_engine_observation(value, 'three')
