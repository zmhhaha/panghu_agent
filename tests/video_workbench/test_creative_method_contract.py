"""New production boundaries; fixtures make no runtime or quality claims."""
from copy import deepcopy
import hashlib

import pytest

from agent_service.contracts import ServiceError, digest
from agent_service.profiles import video_animation_production as production
from agent_service.profiles.video_animation_production_contract import validate_delivery as worker_delivery
from video_workbench.animation_method_production import POLICY, validate_delivery
from video_workbench.creative_method_contract import (
    prepare_core_task, build_scene_input, validate_scene_input, collect_scene_result,
    validate_scene_package, scene_context, scene_source, scene_files,
)
from video_workbench.motion import make_beats, estimated_alignment


def core_task(*, seconds=3, scene_id='production-fixture-scene', base_source=None):
    narration = '先筛选对象，再比较留下来的群体。'
    beats = make_beats(narration)
    return prepare_core_task(scene_id=scene_id, title='筛选与比较',
        visual_description='让对象逐个经过筛选，保持身份并呈现群体变化。', narration=narration,
        beats=beats, alignment=estimated_alignment(beats, 'a' * 64, seconds),
        audio_sha256='a' * 64, duration_seconds=seconds, base_source=base_source)


def published_method():
    content = dict(title='群体变化', problem='筛选后失去对象身份', guidance='让对象自己移动并保留身份。',
        applicability='筛选与群体比较', limitations=['Fixture only'],
        criteria=[dict(id='identity', description='对象身份可跟踪')], runtime_requirements={'engine': 'html-svg'})
    checksum = digest(content)
    return dict(id='fixture-method-v1', version_id='fixture-method-v1', method_id='fixture-method',
        version=1, content_sha256=checksum, status='published', **content,
        publication=dict(method_version_id='fixture-method-v1', method_content_sha256=checksum,
                         reference_validation_ids=['fixture-reference'], transfer_validation_ids=['fixture-transfer']))


def delivery(core, *, methods=None):
    methods = [] if methods is None else deepcopy(methods)
    value = dict(schema='video.production-method-delivery.v1', recipient_owner='video-user',
        consumer_id='fixture-operation', task_sha256=digest(core), allowed_engines=['html-svg'],
        selection=dict(algorithm='conservative-lexical-shortlist-v1', offered=[], excluded=[],
            disposition='offered_for_agent_decision' if methods else 'free_creation_no_match'),
        methods=methods, read_receipt=dict(consumer_id='fixture-operation', owner='video-user',
            scope='methods_delivered_to_caller_not_model_observation',
            method_versions=[dict(version_id=row['id'], content_sha256=row['content_sha256']) for row in methods])
            if methods else None,
        policy=POLICY, evidence_scope='frozen_input_delivery_not_agent_application')
    return dict(value, sha256=digest(value))


def production_input(*, methods=None, seconds=3):
    core = core_task(seconds=seconds)
    return build_scene_input(core_task=core, method_delivery=delivery(core, methods=methods))


def production_result(document, *, source_html=None, action='adapt'):
    html = source_html or '<!doctype html><svg><g id="moving-group"></g></svg><script>window.__animationExperiment={seek(t){}}</script>'
    source = dict(engine='html-svg', strategy=dict(description='Objects preserve their identity', tradeoffs='Fixture only'),
                  files={'scene.html': html}, files_sha256={'scene.html': hashlib.sha256(html.encode()).hexdigest()})
    decisions = [dict(version_id=row['id'], content_sha256=row['content_sha256'], action=action,
        reason='适配当前对象关系。' if action != 'reject' else '这项方法不适合当前片段。',
        scene_targets=[dict(target_id='moving-group', description='Maintains object identity',
            source_file='scene.html', source_anchor='id="moving-group"')] if action != 'reject' else [])
        for row in document['method_delivery']['methods']]
    return dict(schema=production.RESULT_SCHEMA, input_sha256=digest(document), source=source,
        method_decision=dict(delivery_sha256=document['method_delivery']['sha256'], decisions=decisions))


def production_package(*, methods=None, seconds=3, source_html=None):
    document = production_input(methods=methods, seconds=seconds)
    return collect_scene_result(document, production_result(document, source_html=source_html))


def test_new_package_keeps_real_audio_and_declarations_separate_from_visual_evidence():
    document = production_input(methods=[published_method()])
    result = production_result(document)
    package = collect_scene_result(document, result)
    assert package['schema'] == 'video.creative-scene.v2'
    assert package['profile']['name'] == production.PROFILE
    assert package['adapter_revision'] == 'creative-method-scene-adapter-1'
    assert package['method_decision'] == result['method_decision']
    assert package['evidence_scope'] == 'agent_self_report_source_anchors_not_observed_application_or_quality'
    assert 'capability_release_sha256' not in package
    assert scene_context(package) == document['scene']
    assert scene_source(package) == result['source']
    assert scene_files(package)['scene.html'] == result['source']['files']['scene.html'].encode()
    assert validate_scene_package(package) == package


def test_zero_match_and_all_rejected_methods_allow_free_original_source():
    empty = production_input()
    assert collect_scene_result(empty, production_result(empty))['method_decision']['decisions'] == []
    document = production_input(methods=[published_method()])
    output = production_result(document, action='reject')
    assert collect_scene_result(document, output)['method_decision']['decisions'][0]['scene_targets'] == []


@pytest.mark.parametrize('change', ['narration', 'audio', 'duration', 'engine', 'task', 'method-content', 'draft', 'unknown'])
def test_changed_task_or_unpublished_knowledge_cannot_enter_new_profile(change):
    document = production_input(methods=[published_method()])
    if change == 'narration': document['scene']['narration'] += '改变了。'
    elif change == 'audio': document['scene']['audio_sha256'] = 'b' * 64
    elif change == 'duration': document['constraints']['duration_seconds'] += 1
    elif change == 'engine': document['constraints']['allowed_engines'] = ['canvas']
    elif change == 'task': document['method_delivery']['task_sha256'] = 'b' * 64
    elif change == 'method-content': document['method_delivery']['methods'][0]['guidance'] = 'changed'
    elif change == 'draft': document['method_delivery']['methods'][0]['status'] = 'draft'
    else: document['method_delivery']['methods'][0].pop('publication')
    if change in {'task', 'method-content', 'draft', 'unknown'}:
        value = document['method_delivery']; value['sha256'] = digest({k:v for k,v in value.items() if k != 'sha256'})
    with pytest.raises(ServiceError): validate_scene_input(document)


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'unknown', 'content', 'receipt', 'no-target', 'anchor', 'file', 'false-pass'])
def test_each_exact_offered_method_requires_inspectable_nonvisual_decision(change):
    document = production_input(methods=[published_method()])
    output = production_result(document)
    row = output['method_decision']['decisions'][0]
    if change == 'missing': output['method_decision']['decisions'] = []
    elif change == 'duplicate': output['method_decision']['decisions'].append(deepcopy(row))
    elif change == 'unknown': row['version_id'] = 'unknown-method'
    elif change == 'content': row['content_sha256'] = 'c' * 64
    elif change == 'receipt': output['method_decision']['delivery_sha256'] = 'd' * 64
    elif change == 'no-target': row['scene_targets'] = []
    elif change == 'anchor': row['scene_targets'][0]['source_anchor'] = 'not in source'
    elif change == 'file': row['scene_targets'][0]['source_file'] = 'private.txt'
    else: row['observed_applied'] = True
    with pytest.raises(ServiceError): collect_scene_result(document, output)


def test_source_and_package_hashes_are_bound_and_not_forged_as_old_profile():
    document = production_input()
    result = production_result(document)
    result['source']['files']['scene.html'] += 'changed'
    with pytest.raises(ServiceError, match='source_integrity'): collect_scene_result(document, result)
    package = production_package()
    package['profile']['name'] = 'video.animation-experiment.v1'
    with pytest.raises(ServiceError, match='package_changed'): validate_scene_package(package)


def test_worker_and_authenticated_business_delivery_validate_the_same_frozen_record():
    core = core_task()
    for methods in ([], [published_method()]):
        value = delivery(core, methods=methods)
        assert worker_delivery(value) == validate_delivery(value)
        for key, replacement in (('policy', 'changed'), ('sha256', 'f' * 64), ('read_receipt', {})):
            changed = deepcopy(value); changed[key] = replacement
            if key != 'sha256': changed['sha256'] = digest({k:v for k,v in changed.items() if k != 'sha256'})
            for validator in (worker_delivery, validate_delivery):
                with pytest.raises(ServiceError): validator(changed)


def test_prior_source_is_losslessly_frozen_and_rejects_changed_hash():
    previous = production_result(production_input())['source']
    core = core_task(base_source=previous)
    assert core['base_source'] == previous
    bad = deepcopy(previous); bad['files']['scene.html'] += 'changed'
    with pytest.raises(ServiceError, match='source_integrity'): core_task(base_source=bad)


def test_revision_core_carries_source_larger_than_legacy_small_task_limit_without_truncation():
    html = '<svg></svg><!--' + 'x' * (140 * 1024) + '-->'
    previous = production_result(production_input(), source_html=html)['source']
    core = core_task(base_source=previous)
    document = build_scene_input(core_task=core, method_delivery=delivery(core))
    assert document['base_source']['files']['scene.html'] == html
