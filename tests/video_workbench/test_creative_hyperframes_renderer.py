"""Frozen HF adapter custody; synthetic receipts are not real-core evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench.creative_renderer import (HYPERFRAMES_SKILL_COMPILER,
    SKILL_COMPILER, compile_creative_project)
from video_workbench.creative_skill_contract import collect_scene_result
from video_workbench.motion_render_k8s import _hyperframes_project_observation
from video_workbench.motion_renderer.job import freeze_input
import test_creative_skill_contract as skill_fixture
from test_creative_engine_renderer import RUNTIME_V2
from test_creative_renderer import node_verify

ROOT = Path(__file__).resolve().parents[2]
CORE_SHA = '675e02ee21f31540e3a6abaec061f86cea92a0178ae67a390700acb4f801dbbc'
RUNTIME = dict(RUNTIME_V2, schema='video.motion-runtime.v3', hyperframes_core_sha256=CORE_SHA,
    hyperframes_project_host_sha256=hashlib.sha256(
        (ROOT / 'video_workbench/motion_renderer/hyperframes_project.mjs').read_bytes()).hexdigest())


def bound_package(tmp_path, monkeypatch):
    audio = b'bound synthetic recording fixture'
    original = skill_fixture.core_task
    def core_task():
        core = original()
        checksum = hashlib.sha256(audio).hexdigest()
        core['scene']['audio_sha256'] = checksum
        core['scene']['alignment']['audio_sha256'] = checksum
        context = core['film_context']
        context['film']['scenes'][0]['audio_sha256'] = checksum
        context['film_sha256'] = digest(context['film'])
        return core
    monkeypatch.setattr(skill_fixture, 'core_task', core_task)
    document, result, evidence, delivery = skill_fixture.fixture(tmp_path)
    package = collect_scene_result(document, result, evidence, skill_delivery=delivery)
    return package, document['scene'], audio


def compile_bound(package, scene, **options):
    return compile_creative_project(package, scene['beats'], scene['alignment'],
        runtime_fingerprint=options.pop('runtime_fingerprint', RUNTIME), **options)


def test_new_hf_identity_freezes_original_agent_source_and_audio_with_recompilation(tmp_path, monkeypatch):
    package, scene, audio = bound_package(tmp_path, monkeypatch)
    native = compile_bound(package, scene)
    files = compile_bound(package, scene, compiler_version=HYPERFRAMES_SKILL_COMPILER)
    timeline, manifest = (json.loads(files[name]) for name in ('timeline.json', 'manifest.json'))
    assert files['index.html'] == native['index.html']
    assert files['plan.json'] == native['plan.json']
    assert json.loads(native['manifest.json'])['compiler'] == SKILL_COMPILER
    assert 'project_runtime' not in json.loads(native['timeline.json'])['creative']
    assert manifest['compiler'] == HYPERFRAMES_SKILL_COMPILER
    proof = timeline['creative']
    project = proof['hyperframes_project']
    assert digest(project) == proof['hyperframes_project_sha256'] == manifest['hyperframes_project_sha256']
    assert proof['legacy_bridge'] is True
    assert proof['source_adapter_revision'] == package['adapter_revision']
    assert project['scenes'][0]['source_sha256'] == hashlib.sha256(files['index.html']).hexdigest()
    assert project['scenes'][0]['audio_seconds'] == timeline['audio_seconds'] == scene['alignment']['audio_seconds']
    for name, raw in files.items():
        (tmp_path / name).write_bytes(raw)
    (tmp_path / 'audio.wav').write_bytes(audio)
    assert freeze_input(tmp_path, image_digest=RUNTIME['image_digest'])['unbound_experiment'] is False
    process = node_verify(tmp_path, hashlib.sha256(files['manifest.json']).hexdigest())
    assert process.returncode == 0, process.stderr


def test_hf_mode_requires_measured_core_runtime_not_only_installed_version(tmp_path, monkeypatch):
    package, scene, _ = bound_package(tmp_path, monkeypatch)
    with pytest.raises(ServiceError, match='creative_hyperframes_runtime_required'):
        compile_bound(package, scene, compiler_version=HYPERFRAMES_SKILL_COMPILER, runtime_fingerprint=RUNTIME_V2)


@pytest.mark.parametrize('change', ['clock', 'source', 'engine', 'bridge', 'adapter'])
def test_rehashed_project_mutations_do_not_change_trusted_compiler_output(tmp_path, monkeypatch, change):
    package, scene, audio = bound_package(tmp_path, monkeypatch)
    files = compile_bound(package, scene, compiler_version=HYPERFRAMES_SKILL_COMPILER)
    timeline, manifest = (json.loads(files[name]) for name in ('timeline.json', 'manifest.json'))
    proof = timeline['creative']
    if change == 'clock': proof['hyperframes_project']['scenes'][0]['audio_seconds'] -= .1
    elif change == 'source': proof['hyperframes_project']['scenes'][0]['source_sha256'] = 'f' * 64
    elif change == 'engine': proof['hyperframes_project']['scenes'][0]['engine'] = 'canvas'
    elif change == 'bridge': proof['legacy_bridge'] = False
    else: proof['source_adapter_revision'] = HYPERFRAMES_SKILL_COMPILER
    proof['hyperframes_project_sha256'] = digest(proof['hyperframes_project'])
    manifest['hyperframes_project_sha256'] = proof['hyperframes_project_sha256']
    files['timeline.json'] = canonical_bytes(timeline)
    manifest['timeline_sha256'] = digest(timeline)
    manifest['files']['timeline.json'] = digest(timeline)
    files['manifest.json'] = canonical_bytes(manifest)
    for name, raw in files.items():
        (tmp_path / name).write_bytes(raw)
    (tmp_path / 'audio.wav').write_bytes(audio)
    with pytest.raises(ValueError, match='input_not_compiler_output'):
        freeze_input(tmp_path, image_digest=RUNTIME['image_digest'])
    process = node_verify(tmp_path, hashlib.sha256(files['manifest.json']).hexdigest())
    assert process.returncode != 0
    assert 'creative_hyperframes_binding_mismatch' in process.stderr


@pytest.mark.parametrize('change', ['core', 'project', 'clock', 'count', 'dispatch', 'bridge', 'scope'])
def test_independent_receipt_check_rejects_borrowed_or_incomplete_core_evidence(tmp_path, monkeypatch, change):
    package, scene, _ = bound_package(tmp_path, monkeypatch)
    timeline = json.loads(compile_bound(package, scene, compiler_version=HYPERFRAMES_SKILL_COMPILER)['timeline.json'])
    proof = dict(schema='video.hyperframes-project-runtime-proof.v1', runtime='hyperframes-core-project-1',
        project_id=timeline['creative']['hyperframes_project']['project_id'],
        project_sha256=timeline['creative']['hyperframes_project_sha256'], version=RUNTIME['hyperframes'],
        core_sha256=CORE_SHA, seek_protocol='hyperframes-core-renderSeek-exact', legacy_bridge=True,
        evidence_scope='single_scene_project_not_whole_film', core_seek_count=20, dispatch_count=20,
        actual_seconds=0, last_dispatched_seconds=0)
    assert _hyperframes_project_observation(proof, timeline, RUNTIME, minimum_seeks=20) == proof
    if change == 'core': proof['core_sha256'] = 'f' * 64
    elif change == 'project': proof['project_sha256'] = 'f' * 64
    elif change == 'clock': proof['actual_seconds'] = .5
    elif change == 'count': proof['core_seek_count'] = 0
    elif change == 'dispatch': proof['dispatch_count'] = 0
    elif change == 'bridge': proof['legacy_bridge'] = False
    else: proof['evidence_scope'] = 'whole_film'
    with pytest.raises(ServiceError, match='creative_hyperframes_observation_invalid'):
        _hyperframes_project_observation(proof, timeline, RUNTIME, minimum_seeks=20)
