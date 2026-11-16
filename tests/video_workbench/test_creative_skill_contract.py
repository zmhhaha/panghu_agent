"""Creative v5 binds synthetic stopped results; no real reading/quality claims."""
from copy import deepcopy
import hashlib
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles.video_animation_skill_production_contract import RESULT_SCHEMA
from agent_service.skill_tool_evidence import SkillToolObserver
from video_workbench.animation_skill_production import SkillProductionAdapter
from video_workbench.creative_method_contract import prepare_core_task
from video_workbench.creative_skill_contract import (
    build_scene_input, collect_scene_result, validate_scene_package, scene_context, scene_source, scene_files,
)
from video_workbench.motion import make_beats, estimated_alignment
from test_animation_skill_production import published


def core_task():
    narration = '保持对象身份，再说明关系如何变化。'
    beats = make_beats(narration)
    audio = 'a' * 64
    row = dict(scene_id='scene', title='Meaningful relationships', narration=narration,
        visual_intent='Keep identities while relationships change.', audio_version_id='audio-1', audio_sha256=audio,
        audio_seconds=3, start_seconds=0, end_seconds=3, leading_silence_seconds=0, trailing_silence_seconds=0)
    film = dict(project_id='project', joint_plan_version_id='plan', content_binding_sha256='e' * 64,
        title='A complete explanation', script=narration, understanding_goal='Explain a changing relation.',
        style='Keep the subject distinct.', scenes=[row], total_seconds=3)
    return prepare_core_task(scene_id='scene', title=row['title'], visual_description=row['visual_intent'],
        narration=narration, beats=beats, alignment=estimated_alignment(beats, audio, 3), audio_sha256=audio,
        duration_seconds=3, engine_aware=True, film_context=dict(schema='video.film-context.v1',
            film_sha256=digest(film), film=film, current_scene_index=0, predecessor=None))


def fixture(tmp_path, *, count=2, read=True, supporting_file=False):
    core = core_task()
    delivery = SkillProductionAdapter(lambda _: [published(index, supporting_file=supporting_file)
        for index in range(count)], None,
        tmp_path / 'ledger.sqlite3').freeze('alice', 'operation', core, ['html-svg'])
    document = build_scene_input(core, delivery)
    observer = SkillToolObserver(delivery['catalog'], delivery['packages'], input_sha256=digest(document))
    decisions = []
    if read and delivery['packages']:
        key = next(iter(delivery['packages']))
        args = dict(package_sha256=key)
        observer.observe(dict(type='tool_execution_start', toolName='skill_read', toolCallId='root-read', args=args))
        observer.observe(dict(type='tool_execution_end', toolName='skill_read', toolCallId='root-read',
            isError=False, result=observer.tools.tool_result('skill_read', args)))
        decisions = [dict(package_sha256=key, action='adapt', reason='Adapt identities to the current explanation.',
            scene_targets=[dict(target_id='subject', description='Persistent subject.',
                source_file='scene.html', source_anchor='id="subject"')])]
    evidence = observer.finish(str(uuid4()))
    html = '<svg id="subject"></svg>'
    result = dict(schema=RESULT_SCHEMA, input_sha256=digest(document),
        source=dict(engine='html-svg', strategy=dict(description='Continuous subject.', tradeoffs='Synthetic source.'),
            files={'scene.html': html}, files_sha256={'scene.html': hashlib.sha256(html.encode()).hexdigest()}),
        handoff=dict(schema='video.scene-handoff.v1', film_sha256=document['film_context']['film_sha256'],
            scene_id='scene', objects=[], entry_state='The subject starts visible.', exit_state='The relation changes.',
            camera='A stable reference.', transition_intent='Continue the same subject.'),
        skill_decisions=decisions, skill_tool_evidence_sha256=evidence['sha256'])
    return document, result, evidence, delivery


def test_main_input_is_lightweight_but_complete_package_retains_exact_resources_and_host_evidence(tmp_path):
    document, result, evidence, delivery = fixture(tmp_path)
    assert 'INDEPENDENT-BODY' not in canonical_bytes(document).decode()
    assert 'packages' not in document and 'skill_catalog' in document
    package = collect_scene_result(document, result, evidence, skill_delivery=delivery)
    assert package['schema'] == 'video.creative-scene.v5'
    assert package['profile']['name'] == 'video.animation-production.v3'
    assert package['skill_delivery'] == delivery
    assert package['skill_tool_evidence_sha256'] == evidence['sha256']
    assert package['skill_decisions'] == result['skill_decisions']
    assert 'not_observed' in package['evidence_scope']
    assert scene_context(package) == document['scene']
    assert scene_source(package) == result['source']
    assert scene_files(package) == {'scene.html': result['source']['files']['scene.html'].encode()}
    assert validate_scene_package(package) == package


@pytest.mark.parametrize('count,read', [(0, False), (5, False), (5, True)])
def test_empty_unread_or_partly_read_directory_supports_free_creation_without_mass_rejections(tmp_path, count, read):
    document, result, evidence, delivery = fixture(tmp_path, count=count, read=read)
    package = collect_scene_result(document, result, evidence, skill_delivery=delivery)
    assert len(package['skill_decisions']) == (1 if read else 0)


@pytest.mark.parametrize('failure', ['input', 'result', 'evidence', 'package_resource', 'catalog', 'decision', 'profile'])
def test_exact_inputs_packages_result_and_actual_tool_receipts_cannot_be_substituted(tmp_path, failure):
    document, result, evidence, delivery = fixture(tmp_path)
    package = collect_scene_result(document, result, evidence, skill_delivery=delivery)
    if failure == 'input': package['input_document']['scene']['instruction'] = 'A changed requirement.'
    elif failure == 'result': package['result']['source']['files']['scene.html'] += 'changed'
    elif failure == 'evidence': package['skill_tool_evidence']['events'][0]['receipt']['file_sha256'] = 'f' * 64
    elif failure == 'package_resource':
        next(iter(package['skill_delivery']['packages'].values()))['files']['SKILL.md'] += 'changed'
    elif failure == 'catalog': package['skill_catalog_sha256'] = 'f' * 64
    elif failure == 'decision': package['skill_decisions'][0]['reason'] = 'An unbound conclusion.'
    else: package['profile']['name'] = 'video.animation-production.v2'
    with pytest.raises(ServiceError):
        validate_scene_package(package)


def test_raw_agent_claim_cannot_replace_independent_host_evidence(tmp_path):
    document, result, evidence, delivery = fixture(tmp_path, read=False)
    key = next(iter(delivery['packages']))
    result['skill_decisions'] = [dict(package_sha256=key, action='reject',
        reason='I claim I read this.', scene_targets=[])]
    with pytest.raises(ServiceError, match='decisions_incomplete'):
        collect_scene_result(document, result, evidence, skill_delivery=delivery)
    evidence['source'] = 'agent_claimed_native_events'
    evidence['sha256'] = digest({k: v for k, v in evidence.items() if k != 'sha256'})
    result['skill_tool_evidence_sha256'] = evidence['sha256']
    with pytest.raises(ServiceError):
        collect_scene_result(document, result, evidence, skill_delivery=delivery)


@pytest.mark.parametrize('observation', ['search', 'supporting_file', 'failed_root'])
def test_search_hits_supporting_files_and_failed_root_reads_do_not_become_skill_decisions(tmp_path, observation):
    document, result, _, delivery = fixture(tmp_path, read=False, supporting_file=True)
    observer = SkillToolObserver(delivery['catalog'], delivery['packages'], input_sha256=digest(document))
    key = next(iter(delivery['packages']))
    name = 'skill_search' if observation == 'search' else 'skill_read'
    args = {'query': ''} if observation == 'search' else {'package_sha256': key}
    if observation == 'supporting_file':
        args['path'] = 'references/identity.md'
    observer.observe(dict(type='tool_execution_start', toolName=name, toolCallId='observed', args=args))
    observer.observe(dict(type='tool_execution_end', toolName=name, toolCallId='observed',
        isError=observation == 'failed_root',
        result=None if observation == 'failed_root' else observer.tools.tool_result(name, args)))
    evidence = observer.finish(str(uuid4()))
    result['skill_tool_evidence_sha256'] = evidence['sha256']
    package = collect_scene_result(document, result, evidence, skill_delivery=delivery)
    assert package['skill_decisions'] == []
    result['skill_decisions'] = [dict(package_sha256=key, action='reject',
        reason='A search or partial/failed read must not be promoted to an observed Skill read.', scene_targets=[])]
    with pytest.raises(ServiceError, match='decisions_incomplete'):
        collect_scene_result(document, result, evidence, skill_delivery=delivery)


def test_actual_root_read_requires_an_explicit_decision_even_when_nothing_is_applied(tmp_path):
    document, result, evidence, delivery = fixture(tmp_path)
    result['skill_decisions'] = []
    with pytest.raises(ServiceError, match='decisions_incomplete'):
        collect_scene_result(document, result, evidence, skill_delivery=delivery)
    key = next(iter(delivery['packages']))
    result['skill_decisions'] = [dict(package_sha256=key, action='reject',
        reason='The technique was read but does not help this particular scene.', scene_targets=[])]
    assert collect_scene_result(document, result, evidence, skill_delivery=delivery)['skill_decisions'] == result['skill_decisions']


def test_build_input_rejects_a_delivery_frozen_for_another_task(tmp_path):
    _, _, _, delivery = fixture(tmp_path)
    core = core_task()
    core['scene']['instruction'] = 'A new instruction cannot silently reuse a frozen directory.'
    with pytest.raises(ServiceError, match='catalog_binding_mismatch'):
        build_scene_input(core, delivery)
