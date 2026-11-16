"""Artifact/provenance boundaries for real scene generation, without executing model code."""
from copy import deepcopy
import hashlib
import json
import os
import shutil
from types import SimpleNamespace

import pytest

from agent_service.contracts import InlineFile, ServiceError, canonical_bytes
from agent_service.engines import StopEvidence
from agent_service.profiles import implementation
from agent_service.profiles import video_animation_experiment as experiment
from agent_service.registry import TaskContract


ATTEMPT = 'animation-experiment-attempt'


def input_document():
    return dict(schema=experiment.INPUT_SCHEMA, experiment_id='identity-tracking',
        capability='object-identity', goal='Keep A traceable while A/B/C exchange places.',
        constraints=dict(width=1280, height=720, fps=24, duration_seconds=8,
            allowed_engines=['html-svg'], objects=[{'id': key} for key in 'ABC'],
            initial_order=['A', 'B', 'C'], final_order=['C', 'A', 'B']),
        assets={}, prompt_revision='v1', guidance='')


def source_output():
    return dict(engine='html-svg', strategy=dict(description='Separate the paths in depth.',
        tradeoffs='Longer travel distance.'), files={
            'scene.html': '<!doctype html><html><body><script src="./scene.js"></script></body></html>',
            'scene.js': 'window.__animationExperiment = {seek(t) { document.body.dataset.time = String(t); }};',
            'scene.css': 'body { margin: 0; }'})


def prepare(tmp_path):
    profile = implementation(experiment.PROFILE)
    (tmp_path / 'inputs').mkdir()
    raw = canonical_bytes(input_document())
    (tmp_path / 'inputs' / experiment.INPUT_NAME).write_bytes(raw)
    (tmp_path / 'output.json').write_bytes(canonical_bytes(source_output()))
    envelope = SimpleNamespace(profile=experiment.PROFILE, profile_revision='1', attempt_id=ATTEMPT,
        task=dict(input_sha256=hashlib.sha256(raw).hexdigest()),
        inputs=[InlineFile.from_bytes(experiment.INPUT_NAME, raw)],
        checkpoint_inputs=[], checkpoint_ref=None)
    return profile, envelope


def collect(profile, tmp_path, envelope, proof=None):
    return profile.collect_output(tmp_path, envelope,
        proof or StopEvidence(ATTEMPT, True, ('isolated-worker-stopped',)))


def test_reviewed_registration_does_not_widen_existing_profiles():
    profile = implementation(experiment.PROFILE)
    assert profile.required_tools == ('read', 'write')
    contract = TaskContract(name=profile.name, revision='1', task_schema=profile.task_schema,
        output_schema=profile.output_schema, required_tools=['read', 'write'],
        required_model_capabilities=['text', 'tool_calls'])
    assert contract.limits()['max_artifact_files'] == 1
    assert contract.limits()['max_input_files'] == 1
    assert implementation('video.motion-plan.v1').name == 'video.motion-plan.v1'
    assert implementation('video.scene.v1').name == 'video.scene.v1'


def test_stopped_collector_preserves_code_and_service_owned_hashes(tmp_path):
    profile, envelope = prepare(tmp_path)
    profile.validate_inputs(tmp_path, envelope.task)
    artifacts = collect(profile, tmp_path, envelope)
    assert len(artifacts) == 1 and artifacts[0].name == experiment.RESULT_NAME
    result = json.loads(artifacts[0].content())
    assert experiment.validate_experiment_result(result, envelope.task['input_sha256'], input_document()) == result
    assert result['files'] == source_output()['files']
    assert result['prompt_revision'] == 'v1'
    assert experiment.scene_files(result) == {
        name: content.encode() for name, content in source_output()['files'].items()}
    assert set(result) == {'schema', 'input_sha256', 'prompt_revision', 'engine', 'strategy', 'files', 'files_sha256'}
    # No model-defined technical success or creativity score enters the receipt.
    assert 'passed' not in result and 'approval' not in result


@pytest.mark.parametrize('change', ['different_input_bytes', 'wrong_hash', 'wrong_input_name', 'extra_input',
    'checkpoint_ref', 'checkpoint_input', 'wrong_profile', 'wrong_revision', 'noncanonical',
    'writer_active', 'wrong_attempt', 'no_resources'])
def test_collector_rejects_changed_inputs_and_incomplete_stop_proof(tmp_path, change):
    profile, envelope = prepare(tmp_path)
    proof = None
    if change == 'different_input_bytes':
        document = input_document()
        document['guidance'] = 'Changed after submission'
        (tmp_path / 'inputs' / experiment.INPUT_NAME).write_bytes(canonical_bytes(document))
    elif change == 'wrong_hash':
        envelope.task['input_sha256'] = '0' * 64
    elif change == 'wrong_input_name':
        envelope.inputs = [InlineFile.from_bytes('other.json', canonical_bytes(input_document()))]
    elif change == 'extra_input':
        (tmp_path / 'inputs/extra.json').write_text('{}')
    elif change == 'checkpoint_ref':
        envelope.checkpoint_ref = object()
    elif change == 'checkpoint_input':
        envelope.checkpoint_inputs = [object()]
    elif change == 'wrong_profile':
        envelope.profile = 'video.scene.v1'
    elif change == 'wrong_revision':
        envelope.profile_revision = '2'
    elif change == 'noncanonical':
        (tmp_path / 'inputs' / experiment.INPUT_NAME).write_text(
            json.dumps(input_document(), indent=2), encoding='utf-8')
    elif change == 'writer_active':
        proof = StopEvidence(ATTEMPT, False, ('worker',))
    elif change == 'wrong_attempt':
        proof = StopEvidence('another-attempt', True, ('worker',))
    else:
        proof = StopEvidence(ATTEMPT, True, ())
    with pytest.raises(ServiceError):
        collect(profile, tmp_path, envelope, proof)


@pytest.mark.parametrize('bad_name', ['../scene.js', '/scene.html', 'subdir/scene.js',
    'scene.HTML', 'assets/gsap.min.js', 'output.json', 'image.png'])
def test_generated_package_cannot_escape_its_finite_file_contract(tmp_path, bad_name):
    profile, envelope = prepare(tmp_path)
    output = source_output()
    output['files'][bad_name] = 'untrusted'
    (tmp_path / 'output.json').write_bytes(canonical_bytes(output))
    with pytest.raises(ServiceError, match='animation_experiment_files_invalid'):
        collect(profile, tmp_path, envelope)


@pytest.mark.parametrize('change', ['file_hash', 'file_content', 'prompt_revision', 'input_sha256',
    'claimed_pass', 'unknown_file', 'empty_html'])
def test_result_validation_detects_tampering_and_self_attestation(tmp_path, change):
    profile, envelope = prepare(tmp_path)
    result = json.loads(collect(profile, tmp_path, envelope)[0].content())
    if change == 'file_hash':
        result['files_sha256']['scene.js'] = '1' * 64
    elif change == 'file_content':
        result['files']['scene.js'] += '\n// mutated'
    elif change == 'prompt_revision':
        result['prompt_revision'] = 'v2'
    elif change == 'input_sha256':
        result['input_sha256'] = '1' * 64
    elif change == 'claimed_pass':
        result['passed'] = True
    elif change == 'unknown_file':
        result['files']['extra.js'] = 'code'
    else:
        result['files']['scene.html'] = ''
    with pytest.raises(ServiceError):
        experiment.validate_experiment_result(result, envelope.task['input_sha256'], input_document())


@pytest.mark.parametrize('change', ['nan', 'depth', 'boolean_width', 'too_wide', 'duration', 'unknown_asset',
    'asset_path', 'invalid_digest', 'invalid_engine', 'extra_field', 'huge_input', 'lone_surrogate'])
def test_probe_constraints_are_bounded_data_not_an_unlimited_protocol(change):
    value = input_document()
    if change == 'nan':
        value['constraints']['initial'] = float('nan')
    elif change == 'depth':
        nested = 'value'
        for _ in range(15):
            nested = [nested]
        value['constraints']['nested'] = nested
    elif change == 'boolean_width':
        value['constraints']['width'] = True
    elif change == 'too_wide':
        value['constraints']['width'] = 100000
    elif change == 'duration':
        value['constraints']['duration_seconds'] = 61
    elif change == 'unknown_asset':
        value['assets']['remote-library.js'] = '1' * 64
    elif change == 'asset_path':
        value['assets']['../gsap.min.js'] = '1' * 64
    elif change == 'invalid_digest':
        value['assets']['gsap.min.js'] = 'not-a-digest'
    elif change == 'invalid_engine':
        value['constraints']['allowed_engines'] = ['shell']
    elif change == 'extra_field':
        value['run_shell'] = True
    elif change == 'huge_input':
        value['constraints']['payload'] = ['x' * 10000] * 20
    else:
        value['goal'] = '\ud800'
    with pytest.raises(ServiceError):
        experiment.validate_experiment_input(value)


def test_guidance_and_revision_bind_the_actual_generation_context():
    first = input_document()
    second = deepcopy(first)
    second.update(prompt_revision='v2', guidance='Preserve spatial separation while paths cross.')
    profile = implementation(experiment.PROFILE)
    prompts = [profile.build_prompt({'input_sha256': hashlib.sha256(canonical_bytes(doc)).hexdigest()})
               for doc in (first, second)]
    assert prompts[0] != prompts[1]
    assert 'Use only read/write tools' in prompts[0]
    assert 'window.__animationExperiment' in prompts[0]
    assert 'Do not run a shell' in prompts[0]
    # Effective context consists of the saved top-level prompt AND immutable input bytes.
    assert first['guidance'] != second['guidance']


def test_engine_allowlist_and_three_asset_requirement_are_independent(tmp_path):
    profile, envelope = prepare(tmp_path)
    output = source_output()
    output['engine'] = 'three'
    (tmp_path / 'output.json').write_bytes(canonical_bytes(output))
    with pytest.raises(ServiceError, match='animation_experiment_engine_not_allowed'):
        collect(profile, tmp_path, envelope)
    value = input_document()
    value['constraints']['allowed_engines'] = ['three']
    raw = canonical_bytes(value)
    (tmp_path / 'inputs' / experiment.INPUT_NAME).write_bytes(raw)
    envelope.task = {'input_sha256': hashlib.sha256(raw).hexdigest()}
    envelope.inputs = [InlineFile.from_bytes(experiment.INPUT_NAME, raw)]
    with pytest.raises(ServiceError, match='animation_experiment_asset_missing'):
        collect(profile, tmp_path, envelope)
    value['assets']['three.module.min.js'] = 'a' * 64
    raw = canonical_bytes(value)
    (tmp_path / 'inputs' / experiment.INPUT_NAME).write_bytes(raw)
    envelope.task = {'input_sha256': hashlib.sha256(raw).hexdigest()}
    envelope.inputs = [InlineFile.from_bytes(experiment.INPUT_NAME, raw)]
    assert json.loads(collect(profile, tmp_path, envelope)[0].content())['engine'] == 'three'


def test_sources_are_size_bounded_but_never_treated_as_safe_executable_code(tmp_path):
    profile, envelope = prepare(tmp_path)
    output = source_output()
    # Collection keeps arbitrary code as inert bytes. Only the separate renderer may run it.
    output['files']['scene.js'] = 'throw new Error("not executed by artifact collector");'
    (tmp_path / 'output.json').write_bytes(canonical_bytes(output))
    result = json.loads(collect(profile, tmp_path, envelope)[0].content())
    assert experiment.scene_files(result)['scene.js'] == output['files']['scene.js'].encode()
    output['files']['scene.js'] = '中' * (experiment.SOURCE_MAX_BYTES // 2)
    (tmp_path / 'output.json').write_bytes(canonical_bytes(output))
    with pytest.raises(ServiceError, match='animation_experiment_source_too_large'):
        collect(profile, tmp_path, envelope)


def test_output_hardlink_is_not_collected(tmp_path):
    profile, envelope = prepare(tmp_path)
    try:
        os.link(tmp_path / 'output.json', tmp_path / 'linked-output.json')
    except OSError:
        pytest.skip('Host does not support creating a test hardlink')
    with pytest.raises(ServiceError, match='invalid_video_file'):
        collect(profile, tmp_path, envelope)


def test_image_owned_bundle_is_independent_and_hash_pinned(tmp_path, monkeypatch):
    assert set(experiment.verified_bundle()) == experiment.BUNDLE_FILES
    assert hashlib.sha256((experiment.BUNDLE_ROOT / 'manifest.json').read_bytes()).hexdigest() == experiment.BUNDLE_MANIFEST_SHA256
    copied = tmp_path / 'animation-experiment-v1'
    shutil.copytree(experiment.BUNDLE_ROOT, copied)
    contract = copied / 'CONTRACT.md'
    contract.write_bytes(contract.read_bytes() + b'\nchanged')
    monkeypatch.setattr(experiment, 'BUNDLE_ROOT', copied)
    with pytest.raises(ServiceError, match='animation_experiment_bundle_integrity'):
        implementation(experiment.PROFILE).build_prompt({'input_sha256': 'a' * 64})
