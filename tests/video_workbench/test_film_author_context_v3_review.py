"""Independent synthetic v3 custody/budget regressions; no render or real source."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service.animation_skills_contract import bundle_files
from agent_service.contracts import InlineFile, ServiceError, canonical_bytes, digest
from agent_service.engines import StopEvidence
from agent_service.profiles import video_animation_film_module as profile
from agent_service.profiles import video_animation_film_module_contract as contract
from video_workbench.animation_skill_production import SkillProductionAdapter
from video_workbench.creative_film_module_contract import (
    build_module_input, predecessor_from_package, validate_module_package,
)
from video_workbench import film_appearance_context as appearance
from test_creative_film_module_contract import collected, core, raw_module, raw_result
from test_film_creation_workflow import env, OWNER, module_request, start_module
from test_silent_film_modules import explicit_film


def synthetic_source(size):
    prefix = ('// Wholly synthetic custody source, never rendered.\r\n'
        'export function mount(root,context){}\r\n'
        'export function renderAt(context){}\r\n'
        'export function dispose(){}\r\n/*')
    assert size >= len(prefix) + 2
    return prefix + 'X' * (size - len(prefix) - 2) + '*/'


def explicit_core(index=0, predecessor=None, *, instruction='', silent=True):
    value = core(instruction=instruction)
    original = value['film_context']['film']
    film = explicit_film(original, {row['scene_id'] for row in original['scenes']} if silent else set())
    value['film_context'] = dict(schema=contract.EXPLICIT_CONTEXT_SCHEMA, film=film,
        film_sha256=digest(film), current_scene_index=index, predecessor=deepcopy(predecessor))
    row = film['scenes'][index]
    value['scene'].update(scene_id=row['scene_id'], title=row['title'],
        visual_description=row['visual_intent'], narration=row['narration'], audio_sha256=row['audio_sha256'])
    if silent:
        value['scene'].update(beats=None, alignment=None)
    else:
        assert index == 0
    value['target'] = dict(module_id=f'module-{index}', namespace=f'scene_{index}',
        transition_target=dict(module_id='transition-0', namespace='relation_transition') if index else None)
    return contract.validate_core_task(value)


def stage_task(directory, task):
    directory.mkdir(parents=True, exist_ok=True)
    delivery = SkillProductionAdapter(lambda _: [], None, directory / 'skill-ledger.sqlite3').freeze(
        'fixture-owner', 'synthetic-module', task, ['html-svg'])
    document = build_module_input(task, delivery)
    files = [InlineFile.from_bytes(profile.INPUT_NAME, canonical_bytes(document)),
             *bundle_files(delivery['catalog'], delivery['packages'])]
    workspace = directory / 'workspace'
    for item in files:
        target = workspace / 'inputs' / item.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(item.content())
    attempt = str(uuid4())
    envelope = SimpleNamespace(profile=profile.PROFILE, profile_revision='1', attempt_id=attempt,
        task={'input_sha256': digest(document)}, inputs=files, checkpoint_inputs=[], checkpoint_ref=None)
    proof = StopEvidence(execution_id=attempt, all_writers_stopped=True, resource_ids=('synthetic-writer',))
    return workspace, document, delivery, envelope, proof


def first_package(directory, *, source_size=13076, rich_states=False, typed=True, used_asset=False):
    task = explicit_core()
    if used_asset:
        task['assets']['assets/reference.svg'] = 'a' * 64
    if typed:
        task = appearance.attach_structured_predecessor_appearance(task)
    staged = stage_task(directory, task)
    result = raw_result(staged[1])
    module = result['module']
    module['files'][module['entry']] = synthetic_source(source_size)
    if used_asset:
        module['assets'] = ['assets/reference.svg']
    if rich_states:
        module['states']['exit'] = {'subject': {'x': 1, 'appearance': {
            'samples': [[i, i + 1] for i in range(32)], 'inert_description': 'P' * 3000}}}
    return collected(staged, result)[2]


def next_task(package, *, instruction='Keep this explicit user direction.\r\nAPP-FROZEN is user text.'):
    value = explicit_core(1, predecessor_from_package(package), instruction=instruction)
    value['assets'].update({name: package['input_document']['assets'][name]
                           for name in package['result']['module']['assets']})
    return value


def reseal(reference):
    reference['sha256'] = digest({key: value for key, value in reference.items() if key != 'sha256'})


def test_full_reference_above_instruction_limit_moves_without_source_or_state_changes(tmp_path):
    previous = first_package(tmp_path / 'first', rich_states=True)
    task = next_task(previous)
    original_task, original_package = deepcopy(task), deepcopy(previous)
    with pytest.raises(ServiceError, match='instruction_too_large'):
        appearance.attach_predecessor_appearance(task, previous)
    enhanced = appearance.attach_structured_predecessor_appearance(task, previous)
    context = enhanced['film_context']
    reference = context['predecessor_appearance']
    assert context['schema'] == contract.STRUCTURED_CONTEXT_SCHEMA
    assert len(canonical_bytes(reference)) > 16000
    assert reference == appearance.predecessor_appearance_reference(task, previous)
    assert reference['files'] == previous['result']['module']['files']
    assert reference['exit_states'] == previous['result']['module']['states']['exit']
    assert reference['files_sha256'] == previous['result']['module']['files_sha256']
    assert enhanced['scene']['instruction'].startswith(task['scene']['instruction'])
    assert synthetic_source(13076) not in enhanced['scene']['instruction']
    assert len(enhanced['scene']['instruction']) <= 16000
    assert contract.validate_core_task(enhanced) == enhanced
    assert task == original_task and previous == original_package
    assert 'permanently outside current and future planned views' in enhanced['scene']['instruction']
    # The guidance concerns new output; the complete original handoff is unchanged.
    assert canonical_bytes(reference) == canonical_bytes(appearance.predecessor_appearance_reference(task, previous))


@pytest.mark.parametrize('change', ['source', 'source_and_hash', 'exit_state', 'exit_state_type', 'input_hash',
                                  'module_hash', 'scene', 'assets', 'extra_field', 'digest'])
def test_resealing_does_not_make_a_foreign_or_changed_reference_valid(tmp_path, change):
    previous = first_package(tmp_path / 'first')
    task = appearance.attach_structured_predecessor_appearance(next_task(previous), previous)
    reference = task['film_context']['predecessor_appearance']
    name = reference['entry']
    if change in {'source', 'source_and_hash'}:
        reference['files'][name] += '// changed\n'
        if change == 'source_and_hash':
            reference['files_sha256'][name] = hashlib.sha256(reference['files'][name].encode()).hexdigest()
    elif change == 'exit_state':
        reference['exit_states']['subject']['x'] += 1
    elif change == 'exit_state_type':
        reference['exit_states']['subject']['x'] = True  # True == 1 in Python, but different JSON.
    elif change == 'input_hash':
        reference['input_sha256'] = 'f' * 64
    elif change == 'module_hash':
        reference['module_sha256'] = 'f' * 64
    elif change == 'scene':
        reference['scene_id'] = 'another-scene'
    elif change == 'assets':
        reference['assets_sha256']['assets/another.svg'] = 'a' * 64
    elif change == 'extra_field':
        reference['authority'] = 'not-a-permitted-field'
    if change == 'digest':
        reference['sha256'] = '0' * 64
    else:
        reseal(reference)
    with pytest.raises(ServiceError):
        contract.validate_core_task(task)


def test_reference_null_and_old_context_fields_and_instruction_limits_remain_strict(tmp_path):
    typed = appearance.attach_structured_predecessor_appearance(explicit_core())
    assert typed['film_context']['predecessor_appearance'] is None
    assert contract.is_silent_task(typed)
    typed['film_context']['predecessor_appearance'] = {}
    with pytest.raises(ServiceError):
        contract.validate_core_task(typed)
    for legacy in (core(), explicit_core()):
        assert contract.validate_core_task(legacy) == legacy
        assert 'permanently outside current and future planned views' not in legacy['scene']['instruction']
        legacy['film_context']['predecessor_appearance'] = None
        with pytest.raises(ServiceError):
            contract.validate_core_task(legacy)
    with pytest.raises(ServiceError):
        appearance.attach_structured_predecessor_appearance(core())
    typed = appearance.attach_structured_predecessor_appearance(explicit_core())
    typed['scene']['instruction'] = 'I' * 16001
    with pytest.raises(ServiceError):
        contract.validate_core_task(typed)
    assert contract.INPUT_MAX_BYTES == 4 * 1024 * 1024


def test_resigned_null_asset_cannot_match_a_missing_current_asset(tmp_path):
    previous = first_package(tmp_path / 'first', used_asset=True)
    task = appearance.attach_structured_predecessor_appearance(next_task(previous), previous)
    assert contract.validate_core_task(task) == task
    reference = task['film_context']['predecessor_appearance']
    del task['assets']['assets/reference.svg']
    reference['assets_sha256']['assets/reference.svg'] = None
    reseal(reference)
    with pytest.raises(ServiceError):
        contract.validate_core_task(task)


def test_reference_budget_counts_complete_canonical_bytes_at_the_boundary(tmp_path):
    small = first_package(tmp_path / 'small', source_size=300)
    baseline = appearance.predecessor_appearance_reference(next_task(small), small)
    size = 300 + contract.MAX_PREDECESSOR_APPEARANCE_BYTES - len(canonical_bytes(baseline))
    exact = first_package(tmp_path / 'exact', source_size=size)
    typed = appearance.attach_structured_predecessor_appearance(next_task(exact), exact)
    assert len(canonical_bytes(typed['film_context']['predecessor_appearance'])) == 512 * 1024
    assert contract.validate_core_task(typed) == typed
    larger = first_package(tmp_path / 'larger', source_size=size + 1)
    with pytest.raises(ServiceError) as raised:
        appearance.attach_structured_predecessor_appearance(next_task(larger), larger)
    assert raised.value.status == 413


def test_final_v3_input_budget_includes_the_real_frozen_catalog(tmp_path):
    previous = first_package(tmp_path / 'first', source_size=200000)
    task = appearance.attach_structured_predecessor_appearance(next_task(previous), previous)
    base = raw_module(task, entry_x=1, exit_x=2)
    prefix = '/*synthetic base budget data*/'
    base['files'] = {f"modules/{base['namespace']}/part{i}.mjs": prefix for i in range(4)}
    base['entry'] = next(iter(base['files']))
    base['files_sha256'] = {name: hashlib.sha256(source.encode()).hexdigest() for name, source in base['files'].items()}
    task['base_module'] = base
    remaining = contract.STRUCTURED_INPUT_MAX_BYTES - 32 - len(canonical_bytes(task))
    names = list(base['files'])
    for index, name in enumerate(names):
        extra = remaining // 4 + int(index < remaining % 4)
        base['files'][name] += 'B' * extra
        base['files_sha256'][name] = hashlib.sha256(base['files'][name].encode()).hexdigest()
    assert all(len(source.encode()) <= contract.MAX_SOURCE_FILE_BYTES for source in base['files'].values())
    assert len(canonical_bytes(task)) == contract.STRUCTURED_INPUT_MAX_BYTES - 32
    assert contract.validate_core_task(task) == task
    delivery = SkillProductionAdapter(lambda _: [], None, tmp_path / 'final-catalog.sqlite3').freeze(
        'fixture-owner', 'near-limit-module', task, ['html-svg'])
    with pytest.raises(ServiceError) as raised:
        build_module_input(task, delivery)
    assert raised.value.status == 413


def test_v3_cannot_be_relabelled_as_a_historical_module_bundle(tmp_path):
    package = first_package(tmp_path / 'typed')
    for old in ('animation-film-module-v1', 'animation-film-module-v2'):
        changed = deepcopy(package)
        instruction, manifest = profile.HISTORICAL_BUNDLES[old]
        changed['profile'] = dict(name=profile.PROFILE, revision='1', instruction_revision=instruction,
            bundle_revision=old, bundle_manifest_sha256=manifest,
            prompt_sha256=hashlib.sha256(profile.build_prompt_for_bundle(
                {'input_sha256': package['input_sha256']}, old).encode()).hexdigest())
        with pytest.raises(ServiceError):
            validate_module_package(changed)
    historical = first_package(tmp_path / 'historical', typed=False)
    instruction, manifest = profile.HISTORICAL_BUNDLES['animation-film-module-v2']
    historical['profile'] = dict(name=profile.PROFILE, revision='1', instruction_revision=instruction,
        bundle_revision='animation-film-module-v2', bundle_manifest_sha256=manifest,
        prompt_sha256=hashlib.sha256(profile.build_prompt_for_bundle(
            {'input_sha256': historical['input_sha256']}, 'animation-film-module-v2').encode()).hexdigest())
    assert validate_module_package(historical) == historical


def test_store_freezes_the_complete_typed_reference_before_any_second_submission(env, monkeypatch):
    import test_film_creation_workflow as workflow_fixture
    from video_workbench.animejs_library import enable_in_capabilities as enable_anime, read_assets as anime_assets
    from video_workbench.formal_drawing_libraries import enable_in_capabilities
    from video_workbench.ink_landscape_library import read_assets

    env.film = explicit_film(env.film, {row['scene_id'] for row in env.film['scenes']})
    original_result = workflow_fixture.raw_result
    def larger_synthetic_module(document):
        raw = original_result(document)
        if document['film_context']['current_scene_index'] == 0:
            raw['module']['files'][raw['module']['entry']] = synthetic_source(13076)
            raw['module']['states']['exit']['subject']['description'] = 'P' * 3000
        else:
            raw['module']['states']['entry'] = deepcopy(document['film_context']['predecessor']['module']['states']['exit'])
        return raw
    monkeypatch.setattr(workflow_fixture, 'raw_result', larger_synthetic_module)
    caps = deepcopy(env.creation.runtime_capabilities)
    caps['allowed_engines'].append('canvas')
    caps = enable_in_capabilities(enable_anime(caps))
    env.creation.runtime_capabilities = caps
    env.creation.assets.update({'assets/' + name: raw for name, raw in {**read_assets(), **anime_assets()}.items()})
    env.creation.configuration['runtime_capabilities'] = deepcopy(caps)
    env.creation.configuration['sha256'] = digest({key: value for key, value in env.creation.configuration.items() if key != 'sha256'})
    first = start_module(env)
    env.workflow.tick()
    first = env.creation.operation('module', first['id'])
    assert first['status'] == 'completed'
    previous = env.creation.module_package(OWNER, first['version_id'])
    original = deepcopy(previous)
    request = module_request(env, 1, first['version_id'])
    request['instruction'] = 'Keep this explicit instruction unchanged.\r\nAPP-FROZEN is user text.'
    second = env.workflow.start_module(OWNER, env.project['id'], env.project['scenes'][1]['id'],
        request, 'typed-reference-before-second-admission')
    frozen = env.creation.operation('module', second['id'])
    pending = frozen['document']['core_task']
    reference = pending['film_context']['predecessor_appearance']
    assert len(canonical_bytes(reference)) > 16000
    assert reference['files'] == previous['result']['module']['files']
    assert reference['exit_states'] == previous['result']['module']['states']['exit']
    assert pending['scene']['instruction'].startswith(request['instruction'])
    assert len(pending['scene']['instruction']) <= 16000
    assert not frozen['submit_attempted'] and ('submit', second['id']) not in env.gateway.calls
    prepared = env.creation.prepare_module(second['id'], env.adapter)
    assert prepared['document']['film_context']['predecessor_appearance'] == reference
    assert prepared['document']['skill_catalog']['task_sha256'] == digest(pending)
    assert len(canonical_bytes(prepared['document'])) <= 2 * 1024 * 1024
    env.workflow.tick()
    complete = env.creation.operation('module', second['id'])
    assert complete['status'] == 'completed'
    assert env.creation.module_package(OWNER, first['version_id']) == original
