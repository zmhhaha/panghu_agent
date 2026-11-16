"""Synthetic protocol fixtures: no rendered sample or quality claim is implied."""
from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service.animation_skills_contract import build_skill_package
from agent_service.contracts import InlineFile, ServiceError, canonical_bytes, digest
from agent_service.engines import StopEvidence
from agent_service.profiles import video_animation_collaboration as profile
from agent_service.profiles import video_animation_collaboration_contract as contract


CRITERIA = [dict(id='coherence', description='The synthetic fixture preserves shared coordinates.',
                 requires_motion=True)]


def plan(engine='canvas'):
    return dict(engine=engine, composition='One shared coordinate system with an intentional focal area.',
        material='A consistent palette and deliberate mark scale, not an approved render.',
        clock='Derive all states from absolute seconds and a shared seed.',
        interfaces='Pure drawable factories receive context, seconds and shared parameters.',
        components=[dict(id='structure', responsibility='Establish reusable geometry.', depends_on=[],
                         interface='makeStructure(shared) returns geometry'),
                    dict(id='treatment', responsibility='Apply visual treatment to that geometry.',
                         depends_on=['structure'], interface='makeTreatment(geometry,shared) returns draw(t)')],
        keyframe_times=[0, 4, 8])


def component(identity='structure', engine='canvas'):
    return dict(component_id=identity, engine=engine,
        interface={'structure': 'makeStructure(shared) returns geometry',
                   'treatment': 'makeTreatment(geometry,shared) returns draw(t)',
                   'closure': 'Consumes the treatment.'}[identity],
        source='export function makeSynthetic(shared) { return {draw(seconds) { return seconds; }}; }')


def context(phase='plan', engine='canvas'):
    brief = None if phase == 'plan' else plan(engine)
    dependencies = []
    if phase == 'integrate':
        dependencies = [component(row['id'], engine) for row in brief['components']]
    return contract.build_input(phase=phase, goal='Synthetic cross-responsibility contract test.',
        criteria=CRITERIA, constraints=dict(width=1280, height=720, fps=24,
            duration_seconds=8, allowed_engines=[engine]),
        assets={'three.module.min.js': 'a' * 64} if engine == 'three' else {},
        brief=brief, component_id='structure' if phase == 'component' else None,
        dependencies=dependencies)


def skill(engine='canvas', version=1):
    return build_skill_package(skill_id='fixture-shared-coordinates', version=version, kind='expression',
        files={'SKILL.md': '---\nname: fixture-shared-coordinates\n'
                          'description: Coordinate independent creative responsibilities.\n---\n'
                          'Agree on coordinate space and absolute time before splitting responsibilities.\n'
                          'This synthetic fixture has no rendered or developer accepted evidence.'},
        runtime_requirements={'engines': [engine]})


def output(value):
    result = dict(schema=contract.RESULT_SCHEMA, input_sha256=digest(value), phase=value['phase'],
        criteria=deepcopy(value['criteria'] or CRITERIA), methods=[], source=None, sound_events=[],
        review=None, knowledge_proposals=[], plan=None, component=None)
    if value['phase'] == 'plan':
        result['plan'] = plan(value['constraints']['allowed_engines'][0])
    elif value['phase'] == 'component':
        result['component'] = component(value['component_id'], value['brief']['engine'])
    else:
        engine = value['brief']['engine']
        result['source'] = dict(engine=engine, strategy=dict(description='Synthetic integration', tradeoffs=''),
            files={'scene.html': '<html><body>Inert protocol fixture only.</body></html>',
                   'scene.js': 'window.__animationExperiment={seek(seconds){return seconds;}};'})
        result['methods'] = [dict(schema='video.animation-method.v2', title='Shared coordinates',
            problem='Independent modules need a common space.', guidance='Agree on coordinates and clock first.',
            applicability='Independent creative responsibilities.', limitations=['Not visually tested.'],
            criteria=deepcopy(result['criteria']), runtime_requirements={'engine': engine},
            research_record_ids=[], skill_package=skill(engine))]
    return result


def validated(value, result=None):
    return contract.validate_result(output(value) if result is None else result, digest(value), value)


def stage(tmp_path, value, result=None):
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    raw = canonical_bytes(value)
    (inputs / contract.INPUT_NAME).write_bytes(raw)
    (tmp_path / 'output.json').write_bytes(canonical_bytes(output(value) if result is None else result))
    attempt = str(uuid4())
    envelope = SimpleNamespace(profile=contract.PROFILE, profile_revision='1', attempt_id=attempt,
        task={'input_sha256': digest(value)}, inputs=[InlineFile.from_bytes(contract.INPUT_NAME, raw)],
        checkpoint_inputs=[], checkpoint_ref=None)
    proof = StopEvidence(execution_id=attempt, all_writers_stopped=True, resource_ids=('synthetic-stopped-writer',))
    return envelope, proof


def test_separate_admitted_role_uses_only_fixed_reader_and_writer():
    implementation = profile.VideoAnimationCollaborationProfile()
    assert implementation.name == 'video.animation-collaboration.v1'
    assert implementation.required_tools == ('read_inputs', 'write')
    assert implementation.required_model_capabilities == ('text', 'tool_calls')
    assert implementation.task_schema == 'video-animation-collaboration.v1'
    assert implementation.output_schema == 'video-animation-collaboration-output.v1'
    assert implementation.limits.max_input_bytes == 192 * 1024
    assert implementation.limits.max_input_files == implementation.limits.max_artifact_files == 1
    assert profile.INPUT_NAME == contract.INPUT_NAME and profile.RESULT_NAME == contract.RESULT_NAME
    assert set(profile.verified_bundle()) == profile.BUNDLE_FILES


@pytest.mark.parametrize('phase', ['plan', 'component', 'integrate'])
@pytest.mark.parametrize('engine', ['html-svg', 'canvas', 'three'])
def test_three_phases_and_admitted_engines_are_content_neutral(phase, engine):
    value = context(phase, engine)
    assert contract.validate_input(value) == value
    result = validated(value)
    assert result['phase'] == phase
    assert result['input_sha256'] == digest(value)
    assert result['review'] is None and result['knowledge_proposals'] == []


def test_builder_projects_method_content_without_whole_wiki_or_database_wrapper():
    value = context('integrate')
    candidate = output(value)['methods'][0]
    candidate.update(owner='synthetic-owner', knowledge_snapshot={'pages': ['not included']},
                     operation_history=['not included'])
    built = contract.build_input(**{key: deepcopy(item) for key, item in value.items()
                                  if key not in {'schema', 'methods'}}, methods=[candidate])
    assert built['methods'][0]['skill_package'] == candidate['skill_package']
    assert not {'owner', 'knowledge_snapshot', 'operation_history'} & set(built['methods'][0])
    malformed = deepcopy(built)
    malformed['methods'][0]['knowledge_snapshot'] = {'pages': []}
    with pytest.raises(ServiceError, match='not_scoped'):
        contract.validate_input(malformed)


def test_selected_method_budget_is_bounded_not_a_whole_knowledge_catalog():
    value = context()
    value['methods'] = [{'guidance': 'Synthetic guidance'}] * 5
    with pytest.raises(ServiceError, match='not_scoped'):
        contract.validate_input(value)
    value['methods'] = [{'guidance': 'x' * 9000}] * 4
    with pytest.raises(ServiceError, match='methods_too_large'):
        contract.validate_input(value)


def test_empty_criteria_can_only_be_decided_in_plan_and_then_remain_frozen():
    value = context()
    value['criteria'] = []
    assert validated(value)['criteria'] == CRITERIA
    for phase in ('component', 'integrate'):
        value = context(phase)
        value['criteria'] = []
        with pytest.raises(ServiceError):
            contract.validate_input(value)
    value = context('integrate')
    changed = output(value)
    changed['criteria'][0]['description'] = 'Changed after shared planning.'
    with pytest.raises(ServiceError, match='criteria_changed'):
        validated(value, changed)


@pytest.mark.parametrize('change', ['self', 'cycle', 'unknown', 'duplicate_id', 'duplicate_dependency'])
def test_plan_requires_real_unique_acyclic_dependencies(change):
    value = context()
    result = output(value)
    rows = result['plan']['components']
    if change == 'self':
        rows[0]['depends_on'] = ['structure']
    elif change == 'cycle':
        rows[0]['depends_on'] = ['treatment']
    elif change == 'unknown':
        rows[1]['depends_on'] = ['absent']
    elif change == 'duplicate_id':
        rows[1]['id'] = rows[0]['id']
    else:
        rows[1]['depends_on'] = ['structure', 'structure']
    with pytest.raises(ServiceError):
        validated(value, result)


def test_shared_component_budget_is_enforced_even_when_global_limit_would_allow_more():
    value = context()
    value['constraints']['collaboration'] = dict(max_components=2, max_parallel=2, max_rounds=1)
    result = output(value)
    result['plan']['components'].append(dict(id='third', responsibility='A third scope',
        depends_on=[], interface='Independent optional scope'))
    with pytest.raises(ServiceError, match='component_budget_exceeded'):
        validated(value, result)


@pytest.mark.parametrize('times', [[1, 4, 8], [0, 4, 7], [0, 4, 4, 8], [0, -1, 8],
                                 [0, float('inf'), 8], [0, True, 8], [0, 8], [0, 4, 9, 8]])
def test_keyframes_are_bounded_exact_planning_times_not_media_approval(times):
    value = context()
    result = output(value)
    result['plan']['keyframe_times'] = times
    with pytest.raises(ServiceError):
        validated(value, result)


def test_component_accepts_only_its_direct_dependencies_not_transitive_or_unrelated_source():
    value = context('component')
    value['component_id'] = 'treatment'
    with pytest.raises(ServiceError, match='direct_dependencies_required'):
        contract.validate_input(value)
    value['dependencies'] = [component('structure')]
    assert contract.validate_input(value) == value
    value['brief']['components'].append(dict(id='closure', responsibility='Close the fragment.',
        depends_on=['treatment'], interface='Consumes the treatment.'))
    value['component_id'] = 'closure'
    value['dependencies'] = [component('treatment')]
    assert contract.validate_input(value) == value
    value['dependencies'].append(component('structure'))
    with pytest.raises(ServiceError, match='direct_dependencies_required'):
        contract.validate_input(value)


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'unplanned', 'wrong_engine'])
def test_integrator_requires_each_planned_component_once(change):
    value = context('integrate')
    if change == 'missing':
        value['dependencies'].pop()
    elif change == 'duplicate':
        value['dependencies'].append(deepcopy(value['dependencies'][0]))
    elif change == 'unplanned':
        value['dependencies'][0]['component_id'] = 'absent'
    else:
        value['dependencies'][0]['engine'] = 'three'
    with pytest.raises(ServiceError):
        contract.validate_input(value)


@pytest.mark.parametrize('phase,field,value', [
    ('plan', 'base_source', {'unrelated': 'history'}), ('plan', 'brief', {}),
    ('plan', 'component_id', 'structure'), ('component', 'base_source', {'unrelated': 'history'}),
    ('integrate', 'component_id', 'structure')])
def test_phase_scope_excludes_unneeded_sources_and_wrong_identifiers(phase, field, value):
    document = context(phase)
    document[field] = value
    with pytest.raises(ServiceError):
        contract.validate_input(document)


def test_only_integrate_can_receive_complete_revision_source():
    value = context('integrate')
    value['base_source'] = output(value)['source']
    assert contract.validate_input(value) == value
    value['base_source']['files']['index.html'] = 'Forbidden entry.'
    with pytest.raises(ServiceError, match='files_invalid'):
        contract.validate_input(value)


def test_component_code_budget_counts_utf8_and_does_not_clip_source():
    value = context('component')
    result = output(value)
    result['component']['source'] = '中' * (contract.COMPONENT_SOURCE_MAX_BYTES // 3)
    assert validated(value, result)['component']['source'] == result['component']['source']
    result['component']['source'] += '中'
    before = canonical_bytes(result)
    with pytest.raises(ServiceError, match='source_too_large'):
        validated(value, result)
    assert canonical_bytes(result) == before


def test_component_cannot_rewrite_the_shared_interface():
    value = context('component')
    result = output(value)
    result['component']['interface'] = 'An incompatible interface invented by one worker.'
    with pytest.raises(ServiceError, match='interface_changed'):
        validated(value, result)


def test_plan_and_reference_have_their_own_bounds_before_whole_input_budget():
    value = context()
    value['reference'] = {'text': 'x' * 8500}
    with pytest.raises(ServiceError, match='reference_too_large'):
        contract.validate_input(value)
    result = output(context())
    for key in ('composition', 'material', 'clock', 'interfaces'):
        result['plan'][key] = 'x' * 5000
    with pytest.raises(ServiceError, match='plan_too_large'):
        validated(context(), result)


@pytest.mark.parametrize('phase', ['plan', 'component', 'integrate'])
def test_each_phase_enforces_its_own_total_input_budget(phase):
    value = context(phase)
    value['reference'] = {'unbounded': 'x' * contract.PHASE_INPUT_MAX_BYTES[phase]}
    with pytest.raises(ServiceError, match='input_too_large'):
        contract.validate_input(value)


@pytest.mark.parametrize('field,value', [('review', {'outcome': 'goal_met'}),
                                      ('knowledge_proposals', [{'invented': 'rendered success'}])])
def test_authorship_cannot_claim_a_review_or_update_unprovided_knowledge(field, value):
    document = context('integrate')
    result = output(document)
    result[field] = value
    with pytest.raises(ServiceError, match='unobserved_claim'):
        validated(document, result)


def test_full_input_hash_and_phase_are_bound_without_a_legacy_schema_projection():
    value = context('integrate')
    result = output(value)
    changed = deepcopy(value)
    changed['dependencies'][0]['source'] += '\n// Changed dependency bytes.'
    with pytest.raises(ServiceError, match='input_binding_mismatch'):
        contract.validate_result(result, digest(value), changed)
    result['phase'] = 'component'
    with pytest.raises(ServiceError, match='input_binding_mismatch'):
        validated(value, result)
    result = output(value)
    result['schema'] = 'video.animation-method-result.v2'
    with pytest.raises(ServiceError, match='input_binding_mismatch'):
        validated(value, result)


@pytest.mark.parametrize('filename', ['index.html', 'components/structure.js', 'structure.js'])
def test_final_scene_requires_standard_files_without_adding_component_paths(filename):
    value = context('integrate')
    result = output(value)
    result['source']['files'][filename] = '// Not an admitted scene filename.'
    with pytest.raises(ServiceError, match='files_invalid'):
        validated(value, result)


def test_final_source_and_skill_must_match_the_shared_admitted_engine():
    value = context('integrate')
    result = output(value)
    result['source']['engine'] = 'html-svg'
    with pytest.raises(ServiceError):
        validated(value, result)
    result = output(value)
    result['methods'][0]['runtime_requirements']['engine'] = 'html-svg'
    with pytest.raises(ServiceError, match='runtime_mismatch'):
        validated(value, result)
    result['methods'][0]['runtime_requirements']['engine'] = ['canvas']
    with pytest.raises(ServiceError, match='runtime_mismatch'):
        validated(value, result)


def test_standard_draft_skill_metadata_is_computed_without_rewriting_authored_content():
    value = context('integrate')
    result = output(value)
    package = result['methods'][0]['skill_package']
    draft = {key: deepcopy(package[key]) for key in ('skill_id', 'version', 'kind', 'files', 'runtime_requirements')}
    result['methods'][0]['skill_package'] = draft
    result_before = canonical_bytes(result)
    compiled = contract.compile_final_result(result, value)
    assert compiled['schema'] == contract.RESULT_SCHEMA
    assert compiled['phase'] == 'integrate' and compiled['input_sha256'] == digest(value)
    assert compiled['methods'][0]['skill_package'] == package
    assert canonical_bytes(result) == result_before
    with pytest.raises(ServiceError, match='final_phase_required'):
        contract.compile_final_result(output(context()), context())


def test_lineage_uses_an_exact_supplied_persisted_parent_not_an_unpersisted_plan_draft():
    value = context('integrate')
    parent = deepcopy(output(value)['methods'][0])
    parent.update(method_id='fixture-shared-coordinates', version_id='synthetic-persisted-version', version=1)
    value['methods'] = [parent]
    result = output(value)
    result['methods'][0].update(method_id=parent['method_id'], parent_version_id=parent['version_id'],
                                skill_package=skill(version=2))
    assert validated(value, result)['methods'][0]['skill_package']['version'] == 2
    value['methods'][0].pop('version_id')
    result['input_sha256'] = digest(value)
    with pytest.raises(ServiceError, match='parent_not_supplied'):
        validated(value, result)


def test_unsupplied_research_or_wrong_method_criteria_cannot_be_invented():
    value = context('integrate')
    result = output(value)
    result['methods'][0]['research_record_ids'] = ['invented-record']
    with pytest.raises(ServiceError, match='research_not_supplied'):
        validated(value, result)
    result = output(value)
    result['methods'][0]['criteria'][0]['description'] = 'Different requirement.'
    with pytest.raises(ServiceError, match='criteria_changed'):
        validated(value, result)


def test_effect_sounds_are_allowed_but_must_fit_the_host_clock():
    value = context('integrate')
    result = output(value)
    result['sound_events'] = [dict(time=1, duration=.1, frequency=400, gain=.1, waveform='sine')]
    assert validated(value, result)['sound_events'] == result['sound_events']
    result['sound_events'][0]['time'] = 8
    with pytest.raises(ServiceError):
        validated(value, result)


@pytest.mark.parametrize('phase', ['plan', 'component', 'integrate'])
def test_collector_preserves_new_profile_source_and_requires_a_stopped_writer(tmp_path, phase):
    value = context(phase)
    envelope, proof = stage(tmp_path, value)
    implementation = profile.VideoAnimationCollaborationProfile()
    implementation.validate_inputs(tmp_path, envelope.task)
    collected = implementation.collect_output(tmp_path, envelope, proof)
    assert len(collected) == 1 and collected[0].name == contract.RESULT_NAME
    assert json.loads(collected[0].content()) == validated(value)
    invalid = StopEvidence(execution_id=envelope.attempt_id, all_writers_stopped=False, resource_ids=('active',))
    with pytest.raises(ServiceError, match='writers_not_stopped'):
        implementation.collect_output(tmp_path, envelope, invalid)


def test_collector_computes_package_hashes_from_agent_draft_only_after_input_binding(tmp_path):
    value = context('integrate')
    result = output(value)
    package = result['methods'][0]['skill_package']
    result['methods'][0]['skill_package'] = {key: package[key] for key in
        ('skill_id', 'version', 'kind', 'files', 'runtime_requirements')}
    envelope, proof = stage(tmp_path, value, result)
    collected = profile.VideoAnimationCollaborationProfile().collect_output(tmp_path, envelope, proof)
    assert json.loads(collected[0].content())['methods'][0]['skill_package'] == package
    envelope.inputs = [InlineFile.from_bytes(contract.INPUT_NAME, b'substituted')]
    with pytest.raises(ServiceError, match='input_binding_mismatch'):
        profile.VideoAnimationCollaborationProfile().collect_output(tmp_path, envelope, proof)


@pytest.mark.parametrize('mutation', ['profile', 'revision', 'checkpoint', 'duplicate_ref', 'changed_input', 'extra_file'])
def test_collector_rejects_substitution_and_unexpected_context(tmp_path, mutation):
    value = context()
    envelope, proof = stage(tmp_path, value)
    if mutation == 'profile':
        envelope.profile = 'video.animation-method-author.v2'
    elif mutation == 'revision':
        envelope.profile_revision = '2'
    elif mutation == 'checkpoint':
        envelope.checkpoint_ref = 'not-admitted'
    elif mutation == 'duplicate_ref':
        envelope.inputs.append(envelope.inputs[0])
    elif mutation == 'changed_input':
        value['goal'] = 'Changed original task.'
        (tmp_path / 'inputs' / contract.INPUT_NAME).write_bytes(canonical_bytes(value))
    else:
        (tmp_path / 'inputs' / 'unrelated.json').write_text('{}', encoding='utf-8')
    with pytest.raises(ServiceError):
        profile.VideoAnimationCollaborationProfile().collect_output(tmp_path, envelope, proof)


def test_input_must_be_canonical_exact_bytes_even_for_semantically_identical_json(tmp_path):
    value = context()
    envelope, _ = stage(tmp_path, value)
    (tmp_path / 'inputs' / contract.INPUT_NAME).write_text(json.dumps(value, indent=2), encoding='utf-8')
    with pytest.raises(ServiceError, match='input_binding_mismatch'):
        profile.VideoAnimationCollaborationProfile().validate_inputs(tmp_path, envelope.task)


def test_prompt_teaches_real_output_and_render_boundaries_without_a_fixed_art_template():
    prompt = profile.VideoAnimationCollaborationProfile().build_prompt({'input_sha256': 'f' * 64})
    for value in ('plan', 'component', 'integrate', 'scene.html', 'scene.js', 'scene.css',
                  'window.__animationExperiment.seek(seconds)', 'window.__animationExperiment.ready',
                  'constraints.width', 'constraints.height', 'constraints.fps', 'constraints.duration_seconds',
                  'HyperFrames', 'direct dependencies', '24 KiB', 'keyframe_times'):
        assert value in prompt
    assert 'not proof that keyframes have been rendered or approved' in prompt
    assert 'review=null and knowledge_proposals=[]' in prompt
    assert 'one Agent per object' in prompt
    assert 'inline/adapt' in prompt
    assert 'whole Wiki' not in prompt or 'not an' in prompt


def test_pinned_manifest_rejects_changed_local_instructions(tmp_path, monkeypatch):
    root = tmp_path / 'bundle'
    for name in [*profile.BUNDLE_FILES, 'manifest.json']:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((profile.BUNDLE_ROOT / name).read_bytes())
    monkeypatch.setattr(profile, 'BUNDLE_ROOT', root)
    assert set(profile.verified_bundle()) == profile.BUNDLE_FILES
    (root / 'CONTRACT.md').write_text('Substituted contract.', encoding='utf-8')
    with pytest.raises(ServiceError, match='bundle_integrity'):
        profile.verified_bundle()
