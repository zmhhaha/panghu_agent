"""Explicit silent intervals, unchanged recording contracts, and immutable prompts."""
from copy import deepcopy
import hashlib
import json

import pytest

from agent_service.contracts import ServiceError, digest
from agent_service.profiles import video_animation_film_module as profile
from agent_service.profiles import video_animation_film_module_contract as contract
from video_workbench.creative_film_module_contract import validate_module_input, validate_module_package
from video_workbench.hyperframes_film_snapshot import decode_film_execution_snapshot
from test_creative_film_module_contract import core, collected, stage
from test_film_creation_workflow import env, completed_modules, render_request, OWNER


def explicit_film(film, silent_ids):
    film = deepcopy(film)
    film['schema'] = contract.FILM_CLOCK_SCHEMA
    for row in film['scenes']:
        row['clock_source'] = 'silent' if row['scene_id'] in silent_ids else 'recording'
        if row['clock_source'] == 'silent':
            row.update(narration='', audio_version_id=None, audio_sha256=None, audio_seconds=0,
                leading_silence_seconds=0, trailing_silence_seconds=row['end_seconds'] - row['start_seconds'])
    return film


@pytest.mark.parametrize('silent_current', [False, True])
def test_explicit_clock_strictly_distinguishes_silent_and_recorded_current_interval(silent_current):
    task = core()
    selected = task['film_context']['film']['scenes'][0 if silent_current else 1]['scene_id']
    film = explicit_film(task['film_context']['film'], {selected})
    task['film_context'].update(schema=contract.EXPLICIT_CONTEXT_SCHEMA, film=film, film_sha256=digest(film))
    if silent_current:
        task['scene'].update(narration='', beats=None, alignment=None, audio_sha256=None)
    assert contract.validate_core_task(task) == task
    assert contract.is_silent_task(task) is silent_current
    changed = deepcopy(task)
    if silent_current:
        changed['scene']['beats'] = {'fake': 'speech'}
    else:
        changed['scene']['alignment'] = None
    with pytest.raises(ServiceError): contract.validate_core_task(changed)
    legacy = deepcopy(task);legacy['film_context']['schema'] = contract.CONTEXT_SCHEMA
    with pytest.raises(ServiceError): contract.validate_core_task(legacy)


def test_missing_legacy_audio_never_becomes_silent_and_bad_explicit_clock_is_rejected():
    task = core()
    task['scene'].update(narration='', beats=None, alignment=None, audio_sha256=None)
    with pytest.raises(ServiceError): contract.validate_core_task(task)
    film = explicit_film(core()['film_context']['film'], {'scene-0'})
    for field, value in [('audio_sha256', 'f' * 64), ('audio_version_id', 'invented'),
            ('audio_seconds', 1), ('clock_source', 'missing-recording'), ('narration', 'invented speech')]:
        bad = deepcopy(film);bad['scenes'][0][field] = value
        with pytest.raises(ServiceError): contract.validate_author_film(bad, digest(bad), explicit=True)


def test_formal_silent_children_have_no_recording_inputs_and_render_one_real_frozen_silent_clock(env):
    env.film = explicit_film(env.film, {row['scene_id'] for row in env.film['scenes']})
    modules = completed_modules(env)
    assert all(item['status'] == 'completed' for item in modules)
    for item in modules:
        package = env.creation.module_package(OWNER, item['version_id'])
        document = package['input_document']
        assert document['film_context']['schema'] == contract.STRUCTURED_CONTEXT_SCHEMA
        assert (document['film_context']['predecessor_appearance'] is None) is (
            document['film_context']['current_scene_index'] == 0)
        assert document['scene']['narration'] == ''
        assert document['scene']['alignment'] is document['scene']['beats'] is document['scene']['audio_sha256'] is None
        assert package['profile']['bundle_revision'] == profile.BUNDLE_REVISION
        assert validate_module_input(document) == document
    op = env.workflow.start_render(OWNER, env.project['id'], render_request(env, modules), 'explicit-silent-render')
    env.workflow.tick()
    result = env.workflow.get_render(OWNER, op['id'])
    assert result['status'] == 'completed' and result['result']['silent'] is True
    assert result['result']['contains_narration'] is False
    snapshot = env.creation.render_snapshot(op['id'])
    assert snapshot['recording_sha256'] == {} and snapshot['clock']['authority'] == 'explicit_silent_intervals'
    files = decode_film_execution_snapshot(snapshot, expected_snapshot_sha256=snapshot['sha256'],
        runtime_fingerprint=env.creation.runtime, trusted_host_sha256=env.creation.hosts, image=env.creation.image)
    assert not any('/recordings/' in name for name in files)


def test_old_completed_recording_package_keeps_original_bundle_and_prompt_identity(tmp_path):
    package = collected(stage(tmp_path))[2]
    old = 'animation-film-module-v1'
    instruction, manifest = profile.HISTORICAL_BUNDLES[old]
    package['profile'] = dict(name=profile.PROFILE, revision='1', instruction_revision=instruction,
        bundle_revision=old, bundle_manifest_sha256=manifest,
        prompt_sha256=hashlib.sha256(profile.build_prompt_for_bundle(
            {'input_sha256': package['input_sha256']}, old).encode()).hexdigest())
    assert validate_module_package(package) == package
    tampered = deepcopy(package);tampered['profile']['prompt_sha256'] = '0' * 64
    with pytest.raises(ServiceError): validate_module_package(tampered)
