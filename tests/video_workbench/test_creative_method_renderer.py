"""Production method packages cross the existing isolated render boundary.

Uses synthetic media receipts and real source/audio hashing. No model use,
motion observation or improvement in visual quality is claimed by these tests.
"""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench.creative_contract import scene_context, scene_files, scene_source, validate_scene_package
from video_workbench.creative_method_contract import prepare_core_task, build_scene_input, collect_scene_result
from video_workbench.creative_renderer import COMPILER, METHOD_COMPILER, METHOD_TEMPLATE, compile_creative_project
from video_workbench.creative_snapshot import freeze_snapshot_v4, verify_snapshot_v4
from video_workbench.motion_render_k8s import ExecutionJournal, KubeSceneRenderer, validate_artifacts
from video_workbench.motion_renderer.job import freeze_input
from test_creative_method_contract import delivery, production_result, published_method
from test_creative_production import source_scene
from test_creative_renderer import IMAGE, node_verify, receipt_artifacts
from test_motion_snapshot import RUNTIME


def method_scene():
    scene = source_scene()
    original = scene_context(scene['creative']['package'])
    core = prepare_core_task(**{key: original[key] for key in ('scene_id', 'title', 'visual_description',
        'narration', 'beats', 'alignment', 'audio_sha256', 'style', 'instruction')},
        duration_seconds=scene['audio']['duration_seconds'])
    document = build_scene_input(core_task=core, method_delivery=delivery(core, methods=[published_method()]))
    scene['creative']['package'] = collect_scene_result(document, production_result(document))
    return scene


def compiled(scene):
    creative = scene['creative']
    return compile_creative_project(creative['package'], creative['beats'], creative['alignment'],
                                    runtime_fingerprint=RUNTIME)


def test_v2_dispatch_preserves_method_input_and_separate_source():
    package = method_scene()['creative']['package']
    assert validate_scene_package(package) == package
    assert scene_context(package) == package['input_document']['scene']
    assert scene_source(package) == package['result']['source']
    assert scene_files(package)['scene.html'] == package['result']['source']['files']['scene.html'].encode()
    changed = deepcopy(package)
    changed['method_decision']['decisions'][0]['reason'] = 'changed after collection'
    with pytest.raises(ServiceError):
        validate_scene_package(changed)


def test_new_project_binds_knowledge_and_declaration_without_fake_capability_approval():
    scene = method_scene()
    files = compiled(scene)
    package = scene['creative']['package']
    manifest, timeline = (json.loads(files[name]) for name in ('manifest.json', 'timeline.json'))
    assert manifest['compiler'] == timeline['compiler'] == METHOD_COMPILER
    assert manifest['template'] == METHOD_TEMPLATE
    assert files['index.html'] == scene_files(package)['scene.html']
    assert json.loads(files['plan.json']) == package
    assert len(files) == 6
    evidence = timeline['creative']
    assert evidence['method_delivery_sha256'] == package['method_delivery_sha256']
    assert evidence['method_decision_sha256'] == digest(package['method_decision'])
    assert evidence['method_evidence_scope'] == package['evidence_scope']
    assert 'capability_release_sha256' not in evidence
    assert timeline['audio_sha256'] == scene['audio']['sha256']


@pytest.mark.parametrize('method_package,compiler', [(True, COMPILER), (False, METHOD_COMPILER)])
def test_compilers_cannot_be_crossed_between_package_generations(method_package, compiler):
    scene = method_scene() if method_package else source_scene()
    creative = scene['creative']
    with pytest.raises(ServiceError, match='creative_renderer_version_unsupported'):
        compile_creative_project(creative['package'], creative['beats'], creative['alignment'],
            runtime_fingerprint=RUNTIME, compiler_version=compiler)


def test_frozen_snapshot_rebuilds_new_package_and_refuses_changed_recording():
    scene = method_scene()
    snapshot = freeze_snapshot_v4('methods-p1', 1, '方法试用', [scene], runtime_fingerprint=RUNTIME)
    assert verify_snapshot_v4(snapshot) == snapshot
    assert snapshot['scenes'][0]['creative']['timeline']['compiler'] == METHOD_COMPILER
    changed = deepcopy(scene)
    changed['audio']['sha256'] = 'f' * 64
    with pytest.raises(ServiceError):
        freeze_snapshot_v4('methods-p1', 1, '方法试用', [changed], runtime_fingerprint=RUNTIME)


def test_job_and_production_context_recompile_exact_new_source(tmp_path):
    scene = method_scene()
    files = compiled(scene)
    for name, raw in files.items():
        (tmp_path / name).write_bytes(raw)
    (tmp_path / 'audio.wav').write_bytes(scene['audio']['data'])
    frozen = freeze_input(tmp_path, image_digest=RUNTIME['image_digest'])
    assert frozen['unbound_experiment'] is False
    snapshot = freeze_snapshot_v4('methods-p1', 1, '方法试用', [scene], runtime_fingerprint=RUNTIME)
    renderer = KubeSceneRenderer(ExecutionJournal(tmp_path / 'journal.db'),
        SimpleNamespace(namespace='video-motion-render'), image=IMAGE, release_id='method-test',
        runtime=RUNTIME, root=tmp_path / 'runs')
    context = renderer._context(snapshot['scenes'][0], 'test-operation', 0, 'draft')
    assert context[0]['plan.json'] == files['plan.json']
    assert context[0]['audio.wav'] == scene['audio']['data']


@pytest.mark.parametrize('mutation', ['none', 'old-compiler', 'old-schema', 'wrong-audio', 'changed-source'])
def test_node_boundary_accepts_v2_and_rejects_rehashed_crossed_bindings(tmp_path, mutation):
    scene = method_scene()
    files = compiled(scene)
    plan = json.loads(files['plan.json'])
    timeline = json.loads(files['timeline.json'])
    manifest = json.loads(files['manifest.json'])
    if mutation == 'old-compiler':
        manifest.update(compiler=COMPILER, template='generated-html-svg-1')
        timeline['compiler'] = COMPILER
    elif mutation == 'old-schema':
        plan['schema'] = 'video.creative-scene.v1'
    elif mutation == 'wrong-audio':
        plan['input_document']['scene']['audio_sha256'] = 'f' * 64
    elif mutation == 'changed-source':
        plan['result']['source']['files']['scene.html'] += '<div>tampered</div>'
    files.update({'plan.json': canonical_bytes(plan), 'timeline.json': canonical_bytes(timeline)})
    manifest.update(plan_sha256=digest(plan), timeline_sha256=digest(timeline),
        files={name: hashlib.sha256(raw).hexdigest() for name, raw in files.items() if name != 'manifest.json'})
    # Keep the plan/timeline coupling consistent so this exercises the new
    # schema/compiler, source and recording checks, rather than an outer hash.
    timeline['plan_sha256'] = digest(plan)
    files['timeline.json'] = canonical_bytes(timeline)
    manifest['timeline_sha256'] = digest(timeline)
    manifest['files']['timeline.json'] = digest(timeline)
    files['manifest.json'] = canonical_bytes(manifest)
    for name, raw in files.items():
        (tmp_path / name).write_bytes(raw)
    (tmp_path / 'audio.wav').write_bytes(scene['audio']['data'])
    process = node_verify(tmp_path, hashlib.sha256(files['manifest.json']).hexdigest())
    if mutation == 'none':
        assert process.returncode == 0, process.stderr
    else:
        assert process.returncode != 0
        assert 'creative_plan_binding_mismatch' in process.stderr or 'creative_source_mismatch' in process.stderr


def test_technical_receipt_keeps_method_application_as_unobserved_declaration():
    entries, _ = receipt_artifacts()
    scene = method_scene()
    files = compiled(scene)
    package = scene['creative']['package']
    entries.update(files)
    observations = json.loads(entries['observations.json'])
    observations.update(input_sha256=package['input_sha256'], source_sha256=package['result_sha256'])
    entries['observations.json'] = canonical_bytes(observations)
    for name in ('render-receipt.json', 'observe-receipt.json'):
        receipt = json.loads(entries[name])
        receipt.update(input_manifest_sha256=hashlib.sha256(files['manifest.json']).hexdigest(),
            timeline_sha256=hashlib.sha256(files['timeline.json']).hexdigest())
        entries[name] = canonical_bytes(receipt)
    evidence = validate_artifacts(entries, files, RUNTIME, 'draft')['technical_review']
    assert evidence['method_delivery_sha256'] == package['method_delivery_sha256']
    assert evidence['method_evidence_scope'] == package['evidence_scope']
    assert evidence['visual_quality'] == 'not_evaluated' and evidence['aesthetic_approval'] is False
    assert 'capability_release_sha256' not in evidence
