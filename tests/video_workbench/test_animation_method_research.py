"""Independent research custody and exact Skill/media publication gates.

All renders and host receipts here are synthetic persistence fixtures; these
tests do not establish actual creative reuse, motion or human acceptance.
"""
from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.animation_method_store import AnimationMethodStore
from video_workbench.animation_skills import build_skill_package, freeze_skill_catalog, bundle_files


def package(skill_id='causal-field', version=1, text='Track identities before changing relationships.'):
    return build_skill_package(skill_id=skill_id, version=version, kind='expression', files={
        'SKILL.md': '---\nname: causal-field\ndescription: Explain changing fields without losing identities.\n---\n' + text,
    }, runtime_requirements=dict(engines=['canvas'], runtime='hyperframes-core', version='0.8.149'))


def research_body():
    return dict(title='Partial field observation', source=dict(
        url='https://www.bilibili.com/video/BV1BxWu6SESe/',
        media_identity=dict(platform='bilibili', external_id='BV1BxWu6SESe', duration_seconds=111)),
        observation=dict(frames=[dict(at_seconds=12, evidence_sha256='e' * 64)],
            motion_intervals=[], audio_intervals=[], limitations=['Only a captured frame was observed.']),
        raw_observations=['Two differently colored sources and a bright intersection are visible.'],
        provenance=dict(observer='synthetic-test-observer', source='delegated_observation'))


def method_body(**changes):
    result = dict(schema='video.animation-method.v2', title='Explain a field',
        problem='The audience loses the origin of a changing relation.',
        guidance='Keep sources identifiable and make changes locally readable.',
        applicability='Explanations involving changing fields and relationships.', limitations=['Synthetic evidence only.'],
        criteria=[dict(id='causality', description='The change has a trackable cause.')],
        runtime_requirements=dict(engine='canvas'), skill_package=package(), research_record_ids=[])
    result.update(changes)
    return result


@pytest.fixture
def store(tmp_path):
    return AnimationMethodStore(tmp_path / 'methods.sqlite3')


def demo(store, mark='a', document=None):
    document = document or dict(goal='Synthetic representative creation')
    return store.add_demonstration('alice', dict(title='Synthetic full fragment', goal='Explain a changing relation',
        media=dict(sha256=mark * 64, duration_seconds=18), source=None,
        runtime=dict(engine='canvas', admission=dict(status='admitted', id='synthetic-runtime', evidence_sha256='c' * 64)),
        technical=dict(status='passed', evidence_sha256='d' * 64),
        provenance=dict(creator_operation_id='creator-' + mark, render_operation_id='render-' + mark,
                        input_sha256=digest(document))), 'demo-' + mark)


def reference(store, method):
    first = demo(store)
    checked = store.link_validation('alice', dict(method_version_id=method['id'],
        demonstration_version_id=first['id'], role='reference', criteria_sha256=method['criteria_sha256'],
        outcome='passed', evidence=dict(media_sha256=first['media']['sha256'])), 'reference')
    return first, checked


def receipt(method, demonstration, document, resources=None):
    resources = [] if resources is None else resources
    return dict(schema='video.animation-reference-removal.v1', input_document=document,
        resource_manifest=resources, input_receipt=dict(source='verified_host_frozen_input',
            operation_id=demonstration['provenance']['creator_operation_id'], input_sha256=digest(document),
            resource_manifest_sha256=digest(resources), method_content_sha256=method['content_sha256'],
            skill_package_sha256=method['skill_package']['sha256']),
        run_result=dict(source='verified_host_render_record',
            creator_operation_id=demonstration['provenance']['creator_operation_id'],
            render_operation_id=demonstration['provenance']['render_operation_id'],
            demonstration_version_id=demonstration['id'], media_sha256=demonstration['media']['sha256'],
            technical_evidence_sha256=demonstration['technical']['evidence_sha256']))


def transfer_body(method, baseline, demonstration, proof=None):
    result = dict(method_version_id=method['id'], demonstration_version_id=demonstration['id'],
        role='transfer', criteria_sha256=method['criteria_sha256'], outcome='passed',
        evidence=dict(media_sha256=demonstration['media']['sha256']),
        baseline_demonstration_version_id=baseline['id'],
        structural_changes=dict(relations=dict(before='two sources overlap', after='a branching network splits flow')),
        content_changes=dict(before='Wave interference', after='Queued requests redistribute execution load',
                             reason='Different causal subject and relationship structure, not changed labels.'))
    if proof is not None:
        result['reference_removal'] = proof
    return result


def transfer(store, method, baseline, **changes):
    document = dict(goal='Explain an execution queue', skills=[method['skill_package']], reference=None)
    second = demo(store, 'b', document)
    body = transfer_body(method, baseline, second, receipt(method, second, document))
    body.update(changes)
    checked = store.link_validation('alice', body, 'transfer')
    return second, checked


def accept(store, method, checked, **changes):
    demonstration = store.get_demonstration('alice', checked['demonstration_version_id'])
    human = dict(criteria_ids=['causality'], motion_watched=True, audio_listened=False,
        validation_target_sha256=checked['acceptance_target_sha256'], media_sha256=demonstration['media']['sha256'])
    if checked['role'] == 'transfer':
        human.update(content_and_structure_changed=True, reference_free_reuse=True)
    human.update(changes)
    return store.accept('alice', dict(validation_id=checked['id'], comment='Synthetic explicit developer decision.',
        method_content_sha256=method['content_sha256'], human_verification=human), 'accept-' + checked['id'])


def test_research_is_immutable_versioned_owner_scoped_and_separate_from_skill(store):
    body = research_body()
    original = store.create_research('alice', body, 'research')
    assert store.create_research('alice', body, 'research') == original
    assert original['schema'] == 'video.animation-research.v1'
    assert original['observation']['motion_intervals'] == []
    assert original['observation']['audio_intervals'] == []
    assert 'full_video_observed' not in original
    assert store.list_research('bob') == []
    with pytest.raises(ServiceError, match='animation_method_not_found'):
        store.get_research('bob', original['id'])
    method = store.create_method('alice', method_body(research_record_ids=[original['id']]), 'method')
    assert method['research_bindings'] == [dict(id=original['id'], content_sha256=original['content_sha256'])]
    assert original['source']['url'] not in str(method['skill_package'])
    assert 'raw_observations' not in method
    changed = deepcopy(body)
    changed.update(parent_version_id=original['id'], raw_observations=['An additional actual observation.'])
    newer = store.create_research('alice', changed, 'research-v2')
    assert newer['version'] == 2 and newer['research_id'] == original['research_id']
    assert store.get_research('alice', original['id']) == original
    assert store.get_method('alice', method['id'])['research_record_ids'] == [original['id']]
    original['raw_observations'].clear()
    assert store.get_research('alice', original['id'])['raw_observations']


@pytest.mark.parametrize('bad', ['range', 'nonfinite', 'missing_identity', 'invented_coverage', 'unbounded_motion'])
def test_research_preserves_real_coverage_and_rejects_ambiguous_or_invalid_shapes(store, bad):
    body = research_body()
    if bad == 'range':
        body['observation']['frames'][0]['at_seconds'] = 112
    elif bad == 'nonfinite':
        body['observation']['frames'][0]['at_seconds'] = float('nan')
    elif bad == 'missing_identity':
        body['source']['media_identity'].pop('external_id')
    elif bad == 'invented_coverage':
        body['observation']['full_video_observed'] = True
    else:
        body['observation']['motion_intervals'] = [dict(start_seconds=12, end_seconds=12, evidence_sha256='e' * 64)]
    with pytest.raises(ServiceError, match='invalid_animation_research'):
        store.create_research('alice', body, bad)
    assert store.list_research('alice') == []


def test_skill_version_and_method_identity_cannot_be_reused_with_different_content(store):
    first = store.create_method('alice', method_body(), 'first')
    with pytest.raises(ServiceError, match='animation_method_parent_required'):
        store.create_method('alice', method_body(skill_package=package(text='Changed instructions')), 'rewrite')
    with pytest.raises(ServiceError, match='animation_method_skill_identity_mismatch'):
        store.create_method('alice', method_body(parent_version_id=first['id']), 'wrong-version')
    second = store.create_method('alice', method_body(parent_version_id=first['id'],
        skill_package=package(version=2, text='Revised self-contained instructions.')), 'second')
    assert second['version'] == second['skill_package']['version'] == 2
    assert first['content_sha256'] != second['content_sha256']
    assert store.get_method('alice', first['id']) == first
    legacy = method_body()
    for key in ('schema', 'skill_package', 'research_record_ids'):
        legacy.pop(key)
    legacy['parent_version_id'] = second['id']
    with pytest.raises(ServiceError, match='animation_method_contract_downgrade'):
        store.create_method('alice', legacy, 'downgrade')


def test_unknown_or_foreign_research_cannot_be_bound_and_urls_cannot_replace_guidance(store):
    record = store.create_research('alice', research_body(), 'research')
    with pytest.raises(ServiceError, match='animation_method_not_found'):
        store.create_method('bob', method_body(research_record_ids=[record['id']]), 'foreign')
    with pytest.raises(ServiceError, match='animation_method_reference_dependency'):
        store.create_method('alice', method_body(research_record_ids=[record['id']],
            guidance='Open ' + record['source']['url'] + ' to learn the implementation.'), 'dependent')


def test_v2_publication_binds_exact_skill_media_reference_free_run_and_developer_decisions(store):
    research = store.create_research('alice', research_body(), 'research')
    method = store.create_method('alice', method_body(research_record_ids=[research['id']]), 'method')
    first, representative = reference(store, method)
    second, adapted = transfer(store, method, first)
    assert store.publication_readiness('alice', method['id'])['ready'] is False
    with pytest.raises(ServiceError, match='animation_method_publication_blocked'):
        store.publish('alice', method['id'], 'too-early')
    one = accept(store, method, representative)
    assert store.publication_readiness('alice', method['id'])['ready'] is False
    two = accept(store, method, adapted)
    readiness = store.publication_readiness('alice', method['id'])
    assert readiness['ready'] is True and readiness['unmet_conditions'] == []
    published = store.publish('alice', method['id'], 'publish')
    publication = published['publication']
    assert publication['schema'] == 'video.animation-method-publication.v2'
    assert publication['skill_package_sha256'] == method['skill_package']['sha256']
    assert publication['reference_removal_receipt_sha256s'] == [adapted['reference_removal_sha256']]
    assert publication['developer_acceptance_sha256s'] == {representative['id']: digest(one), adapted['id']: digest(two)}
    assert publication['research_bindings'] == method['research_bindings']
    assert 'internalization_succeeded' not in publication
    assert store.get_method('bob', method['id']) == published
    catalog = freeze_skill_catalog([published], recipient_owner='bob', consumer_id='new-production',
        task_sha256='f' * 64, allowed_engines=['canvas'])
    assert catalog['skills'][0]['package_sha256'] == method['skill_package']['sha256']
    assert all(research['source']['url'] not in item.content().decode('utf-8')
               for item in bundle_files(catalog, [method['skill_package']]))
    # Published source attribution does not expose private raw research to a consumer.
    with pytest.raises(ServiceError, match='animation_method_not_found'):
        store.get_research('bob', research['id'])
    assert store.publish('alice', method['id'], 'publish') == published


@pytest.mark.parametrize('failure,expected', [
    ('no_receipt', 'animation_method_reference_free_execution_required'),
    ('numbers_only', 'animation_method_content_and_structure_evidence_required'),
    ('no_content', 'animation_method_content_and_structure_evidence_required'),
    ('human_not_substantive', 'animation_method_human_transfer_verification_required'),
])
def test_declared_differences_and_agent_approval_do_not_automatically_certify_reuse(store, failure, expected):
    method = store.create_method('alice', method_body(), 'method')
    first, representative = reference(store, method)
    changes = {}
    if failure == 'no_receipt':
        changes['reference_removal'] = None
    elif failure == 'numbers_only':
        changes['structural_changes'] = dict(object_count=dict(before=5, after=9))
    if failure == 'no_content':
        document = dict(goal='New goal', skills=[method['skill_package']])
        second = demo(store, 'b', document)
        body = transfer_body(method, first, second, receipt(method, second, document))
        body.pop('content_changes')
        adapted = store.link_validation('alice', body, 'transfer')
    elif failure == 'no_receipt':
        document = dict(goal='New goal', skills=[method['skill_package']])
        second = demo(store, 'b', document)
        adapted = store.link_validation('alice', transfer_body(method, first, second), 'transfer')
    else:
        _, adapted = transfer(store, method, first, **changes)
    accept(store, method, representative)
    human = dict(content_and_structure_changed=False) if failure == 'human_not_substantive' else {}
    accept(store, method, adapted, **human)
    readiness = store.publication_readiness('alice', method['id'])
    assert readiness['ready'] is False and expected in readiness['unmet_conditions']
    with pytest.raises(ServiceError, match='animation_method_publication_blocked'):
        store.publish('alice', method['id'], 'not-published')


@pytest.mark.parametrize('failure', ['agent_claim', 'input_hash', 'resource_hash', 'media', 'operation',
                                     'missing_package', 'original_url', 'alternate_url', 'copied_observation',
                                     'observation_material', 'original_asset', 'original_asset_in_input'])
def test_reference_removal_requires_real_host_receipt_bound_to_frozen_input_and_result(store, failure):
    research = store.create_research('alice', research_body(), 'research')
    method = store.create_method('alice', method_body(research_record_ids=[research['id']]), 'method')
    first, _ = reference(store, method)
    document = dict(goal='New field explanation', skills=[method['skill_package']])
    resources = []
    if failure == 'missing_package':
        document.pop('skills')
    elif failure == 'original_url':
        document['goal'] += ' copy ' + research['source']['url']
    elif failure == 'alternate_url':
        document['goal'] += ' read https://www.bilibili.com/video/BV1BxWu6SESe?different=query'
    elif failure == 'copied_observation':
        document['task_notes'] = research['raw_observations'][0]
    elif failure == 'observation_material':
        document['nested'] = dict(reference=dict(notes='The original observation is still supplied.'))
    elif failure == 'original_asset':
        resources = [dict(path='assets/reference.jpg', sha256='e' * 64)]
    elif failure == 'original_asset_in_input':
        document['image_input_sha256'] = 'e' * 64
    second = demo(store, 'b', document)
    proof = receipt(method, second, document, resources)
    if failure == 'agent_claim':
        proof['input_receipt']['source'] = 'agent_reported_reference_free'
    elif failure == 'input_hash':
        proof['input_receipt']['input_sha256'] = 'f' * 64
    elif failure == 'resource_hash':
        proof['input_receipt']['resource_manifest_sha256'] = 'f' * 64
    elif failure == 'media':
        proof['run_result']['media_sha256'] = 'f' * 64
    elif failure == 'operation':
        proof['run_result']['creator_operation_id'] = 'another-creator'
    with pytest.raises(ServiceError, match='animation_method_reference_removal_invalid'):
        store.link_validation('alice', transfer_body(method, first, second, proof), 'invalid-proof')
    assert len(store.list_validations('alice')) == 1


@pytest.mark.parametrize('failure,expected', [
    ('not_explicit', 'animation_method_explicit_viewing_required'),
    ('wrong_media', 'animation_method_acceptance_target_mismatch'),
    ('wrong_validation', 'animation_method_acceptance_target_mismatch'),
    ('not_watched', 'animation_method_human_motion_required'),
])
def test_v2_requires_actual_explicit_developer_decision_for_exact_visible_result(store, failure, expected):
    method = store.create_method('alice', method_body(), 'method')
    _, representative = reference(store, method)
    if failure == 'not_explicit':
        body = dict(validation_id=representative['id'], method_content_sha256=method['content_sha256'],
                    comment='Model says this is good.')
        with pytest.raises(ServiceError, match=expected):
            store.accept('alice', body, 'not-human')
    else:
        changes = ({'media_sha256': 'f' * 64} if failure == 'wrong_media' else
                   {'validation_target_sha256': 'f' * 64} if failure == 'wrong_validation' else
                   {'motion_watched': False})
        with pytest.raises(ServiceError, match=expected):
            accept(store, method, representative, **changes)
    assert store.list_validations('alice')[0]['acceptance'] is None


def test_legacy_records_keep_frozen_meaning_and_need_a_new_version_for_current_contract(store):
    body = method_body()
    for key in ('schema', 'skill_package', 'research_record_ids'):
        body.pop(key)
    legacy = store.create_method('alice', body, 'legacy')
    reopened = AnimationMethodStore(store.path)
    assert reopened.get_method('alice', legacy['id']) == legacy
    readiness = reopened.publication_readiness('alice', legacy['id'])
    assert readiness['historical_contract'] is True and readiness['ready'] is False
    assert readiness['unmet_conditions'] == ['animation_method_v2_version_required']
    current = reopened.create_method('alice', method_body(parent_version_id=legacy['id'],
        skill_package=package(skill_id=legacy['method_id'], version=2)), 'promoted')
    assert current['schema'] == 'video.animation-method.v2'
    assert current['version'] == 2 and reopened.list_validations('alice', current['id']) == []
    assert reopened.get_method('alice', legacy['id']) == legacy
