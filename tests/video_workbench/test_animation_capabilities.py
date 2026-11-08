"""Shared strategy custody; no model, browser or rendering calls occur here."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from agent_service.contracts import ServiceError
from agent_service.profiles.video_animation_experiment import RESULT_SCHEMA
from video_workbench import animation_capabilities as policies
from video_workbench import animation_experiments as lab
from video_workbench import animation_experiment_runner as runner
from video_workbench import creative_contract as creative
from video_workbench.motion import estimated_alignment, make_beats


def request(**changes):
    narration = '先观察同一个对象。然后解释它如何移动。'
    beats = make_beats(narration)
    values = dict(scene_id='scene-unit-test', title='运动与身份',
        visual_description='用连续运动解释对象与位置的区别。', narration=narration,
        beats=beats, alignment=estimated_alignment(beats, 'a' * 64, 8),
        audio_sha256='a' * 64, duration_seconds=8)
    values.update(changes)
    return creative.build_scene_input(**values)


def result(document, **changes):
    files = {'scene.html': '<!doctype html><svg xmlns="http://www.w3.org/2000/svg"></svg>'}
    value = dict(schema=RESULT_SCHEMA, input_sha256=policies.digest(document),
        prompt_revision=document['prompt_revision'], engine='html-svg',
        strategy=dict(description='Synthetic contract fixture', tradeoffs='Not rendered'),
        files=files, files_sha256={key: policies.digest(raw.encode()) for key, raw in files.items()})
    value.update(changes)
    return value


def test_registered_releases_are_detached_and_reject_self_signed_policy_replacement():
    original = policies.capability_release()
    modified = policies.capability_release()
    modified['rules'][0] = 'Replace the engineering rule from a request.'
    modified['sha256'] = policies.digest({key: value for key, value in modified.items() if key != 'sha256'})
    assert policies.capability_release() == original
    with pytest.raises(ServiceError, match='release_changed'):
        policies.validate_release(modified)
    with pytest.raises(ServiceError, match='release_unknown'):
        policies.capability_release('unreviewed-new-release')
    assert original['engines'] == ['html-svg']
    assert 'not a selected aesthetic winner' in original['evidence'][0]['scope']


def test_lab_and_production_freeze_exact_same_strategy_not_scene_code():
    production = request()
    experiment = lab.pilot_input(capability_release_id=policies.DEFAULT_RELEASE_ID)
    release = production['constraints']['capability_release']
    assert experiment['constraints']['capability_release'] == release
    assert experiment['guidance'] == production['guidance'] == policies.release_guidance(release)
    assert 'scene.html' not in release and production['constraints']['production']['base_result'] is None
    assert production['constraints']['production']['alignment']['review_status'] == 'pending'


def test_next_process_default_release_preserves_v1_and_binds_new_lab_and_production():
    # Change only the future deployment's default assignment before import.
    # Patching a live module's default would miss import-time registry renaming.
    probe = r'''
import ast,json,pathlib,sys,types
import video_workbench
source = pathlib.Path(sys.argv[1])
tree = ast.parse(source.read_text(encoding='utf-8'), filename=str(source))
assignments = [node for node in tree.body if isinstance(node, ast.Assign)
               and any(isinstance(name, ast.Name) and name.id == 'DEFAULT_RELEASE_ID'
                       for name in node.targets)]
assert len(assignments) == 1
assignments[0].value = ast.Constant(value='explanatory-motion-v2')
tree = ast.fix_missing_locations(tree)
name = 'video_workbench.animation_capabilities'
policies = types.ModuleType(name)
policies.__file__, policies.__package__ = str(source), 'video_workbench'
sys.modules[name] = policies
video_workbench.animation_capabilities = policies
exec(compile(tree, str(source), 'exec'), policies.__dict__)
v1 = policies.capability_release('explanatory-motion-v1')
assert v1['sha256'] == sys.argv[2]
v2 = {key: value for key, value in v1.items() if key != 'sha256'}
v2.update(id='explanatory-motion-v2', revision=2, rules=[*v1['rules'], 'Test-only next-release rule.'])
policies._RELEASES[v2['id']] = policies._release(v2)
from video_workbench.creative_contract import build_scene_input,validate_scene_input
from video_workbench.animation_experiments import pilot_input
from video_workbench.motion import estimated_alignment,make_beats
narration = 'Keep the object visible. Then move it.'
beats = make_beats(narration)
arguments = dict(scene_id='future-default-check', title='Tracking',
    visual_description='Move one labelled object continuously.', narration=narration,
    beats=beats, alignment=estimated_alignment(beats, 'a' * 64, 8),
    audio_sha256='a' * 64, duration_seconds=8)
old = build_scene_input(**arguments, capability_release_id=v1['id'])
new = build_scene_input(**arguments)
experiment = pilot_input(capability_release_id=v2['id'])
assert new['constraints']['capability_release'] == experiment['constraints']['capability_release']
assert new['guidance'] == experiment['guidance']
assert new['constraints']['capability_release']['id'] == 'explanatory-motion-v2'
assert policies.capability_release(v1['id']) == v1
assert validate_scene_input(old) == old
assert old['constraints']['capability_release'] == v1
print(json.dumps(dict(status='future_default_preserves_v1', model_calls=0)))
'''
    process = subprocess.run(
        [sys.executable, '-B', '-c', probe, str(Path(policies.__file__).resolve()),
         policies.capability_release('explanatory-motion-v1')['sha256']],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == {'status': 'future_default_preserves_v1', 'model_calls': 0}


def test_old_pilot_documents_remain_identical_and_new_plan_freezes_release(tmp_path):
    legacy = lab.pilot_input()
    legacy_hash = lab.digest(legacy)
    legacy_directory = tmp_path / 'old'
    legacy_plan = runner.initialize(legacy_directory)
    assert 'capability_release' not in legacy_plan
    assert runner._slot_document(legacy_directory, legacy_plan, 'development-v1')[1] == legacy
    directory = tmp_path / 'shared'
    plan = runner.initialize(directory, capability_release_id=policies.DEFAULT_RELEASE_ID)
    slot, document, _ = runner._slot_document(directory, plan, 'development-v1')
    assert document == lab.pilot_input(capability_release_id=policies.DEFAULT_RELEASE_ID)
    assert lab.digest(lab.pilot_input()) == legacy_hash
    assert runner._record(slot, document, 'development')['provenance']['capability_release_sha256'] == plan['capability_release']['sha256']
    plan['capability_release']['rules'][0] = 'Silently changed strategy'
    (directory / 'pilot.json').write_bytes(lab.canonical(plan))
    with pytest.raises(ServiceError, match='release_changed'):
        runner._load(directory)


def test_experiment_local_revision_keeps_shared_policy_and_canonical_readback(tmp_path):
    document = lab.pilot_input('development', 'v2', 'Try an alternative motion sequence.',
        capability_release_id=policies.DEFAULT_RELEASE_ID)
    assert document['constraints']['capability_local_guidance'] == 'Try an alternative motion sequence.'
    slot = dict(run_id='shared-strategy-fixture', created_at=1)
    row = runner._record(slot, document, 'development')
    directory = tmp_path / slot['run_id']
    directory.mkdir()
    (directory / 'experiment-run.json').write_bytes(lab.canonical(lab.signed(row)))
    assert lab.read_run(directory)['input_document'] == document
    row['provenance']['capability_release_sha256'] = '0' * 64
    (directory / 'experiment-run.json').write_bytes(lab.canonical(lab.signed(row)))
    with pytest.raises(lab.ExperimentError, match='capability_binding_mismatch'):
        lab.read_run(directory)


@pytest.mark.parametrize('changes,code', [
    ({'audio_sha256': 'b' * 64}, 'audio_binding_mismatch'),
    ({'duration_seconds': 9}, 'audio_duration_mismatch'),
    ({'narration': '偷偷替换旁白。'}, 'narration_binding_mismatch'),
    ({'width': 640}, 'render_geometry_unsupported'),
])
def test_production_input_uses_actual_audio_and_narration(changes, code):
    with pytest.raises(ServiceError, match=code):
        request(**changes)


def test_changed_guidance_cannot_claim_registered_release():
    value = request()
    value['guidance'] += '\nUse an unrelated template.'
    with pytest.raises(ServiceError, match='policy_binding_mismatch'):
        creative.validate_scene_input(value)


def test_source_package_binds_result_audio_policy_and_profile_input():
    document = request()
    package = creative.collect_scene_result(document, result(document))
    assert creative.validate_scene_package(package) == package
    assert creative.scene_context(package)['audio_sha256'] == 'a' * 64
    assert package['profile']['name'] == 'video.animation-experiment.v1'
    assert package['profile']['instruction_revision'] == 'animation-experiment-instructions-1'
    assert set(creative.scene_files(package)) == {'scene.html'}
    altered = deepcopy(package)
    altered['result']['files']['scene.html'] += '<script>different()</script>'
    with pytest.raises(ServiceError, match='source_integrity'):
        creative.validate_scene_package(altered)
    altered = deepcopy(package)
    altered['capability_release_sha256'] = '0' * 64
    with pytest.raises(ServiceError, match='package_changed'):
        creative.validate_scene_package(altered)
    altered = deepcopy(package)
    altered['profile']['prompt_sha256'] = '0' * 64
    with pytest.raises(ServiceError, match='package_changed'):
        creative.validate_scene_package(altered)


def test_html_svg_admission_is_explicit_and_cannot_be_expanded_by_result():
    document = request()
    with pytest.raises(ServiceError, match='engine_not_allowed'):
        creative.collect_scene_result(document, result(document, engine='canvas'))
    document['constraints']['allowed_engines'] = ['html-svg', 'three']
    with pytest.raises(ServiceError, match='engine_not_admitted'):
        creative.validate_scene_input(document)


def test_revision_receives_original_source_and_does_not_drop_oversized_context():
    original = request()
    original_result = result(original)
    revision = request(instruction='让转向更连续，但保留对象身份。', base_result=original_result)
    assert creative.base_scene_result(revision) == original_result
    assert revision['constraints']['production']['instruction'].startswith('让转向')
    long_result = deepcopy(original_result)
    long_result['files']['scene.html'] = '<svg>' + 'x' * 20000 + '</svg>'
    long_result['files_sha256']['scene.html'] = policies.digest(long_result['files']['scene.html'].encode())
    long_revision = request(base_result=long_result)
    assert len(long_revision['constraints']['production']['base_result']['files']['scene.html']) == 3
    assert creative.base_scene_result(long_revision) == long_result
    oversized = deepcopy(original_result)
    oversized['files']['scene.html'] = '<svg>' + 'x' * (128 * 1024) + '</svg>'
    oversized['files_sha256']['scene.html'] = policies.digest(oversized['files']['scene.html'].encode())
    with pytest.raises(ServiceError):
        request(base_result=oversized)

