"""Synthetic publication/ledger fixtures; no Skill acceptance is created."""
from copy import deepcopy

import pytest

from agent_service.animation_skills_contract import build_skill_package, SkillCatalogTools, bundle_files
from agent_service.contracts import ServiceError, digest
from video_workbench import animation_skill_compatibility as compatibility
from video_workbench.animejs_library import ASSETS, descriptor, enable_in_capabilities
from video_workbench.animation_skill_production import SkillProductionAdapter
from video_workbench.animation_method_production import MethodProductionAdapter
from test_animation_skill_production import published, metadata
from test_animation_method_production import published as legacy_published, TASK as LEGACY_TASK
from video_workbench.animation_method_store import AnimationMethodStore
from video_workbench.animation_method_service import AnimationMethodService
from test_animation_skill_method_service import SkillGateway
from test_animation_method_service import Renderer, start, CRITERIA, method, runtime_capabilities


def dependent(index=0, *, resources=False):
    row = published(index)
    prior = row['skill_package']
    requirements = dict(engines=['html-svg'], **({'resource_authoring':{'manim':{
        'version':'0.22.1','renderer':'cairo','latex_available':False}}} if resources else
        {'authoring_libraries':{'animejs':descriptor()}}))
    row['skill_package'] = build_skill_package(skill_id=prior['skill_id'], version=prior['version'],
        kind=prior['kind'], files=prior['files'], runtime_requirements=requirements)
    row['publication']['skill_package_sha256'] = row['skill_package']['sha256']
    return row


def task(assets=None, **fields):
    return dict(assets={} if assets is None else deepcopy(assets), **fields)


def test_exact_descriptor_and_all_asset_hashes_are_required_and_resources_are_separate():
    requirements = {'authoring_libraries':{'animejs':descriptor()}}
    admitted = compatibility.freeze_compatibility(task({'assets/'+name:sha for name,sha in ASSETS.items()}))
    assert admitted['schema'] == compatibility.SCHEMA
    assert compatibility.requirements_compatible(requirements,admitted)
    for mutation in ['empty','partial','sha','version','unknown','legacy']:
        assets = deepcopy(ASSETS)
        required = deepcopy(requirements)
        if mutation == 'empty': assets = {}
        elif mutation == 'partial': del assets['animejs.LICENSE.txt']
        elif mutation == 'sha': assets['anime_timeline.mjs'] = 'a'*64
        elif mutation == 'version': required['authoring_libraries']['animejs']['version'] = 'latest'
        elif mutation == 'unknown': required['authoring_libraries'] = {'unknown':descriptor()}
        frozen = compatibility.freeze_compatibility(task(assets,**({'base_source':None} if mutation=='legacy' else {})))
        assert not compatibility.requirements_compatible(required,frozen)
    assert not compatibility.requirements_compatible({'resource_authoring':{'manim':{'version':'0.22.1'}}},admitted)
    assert compatibility.requirements_compatible({'engines':['html-svg']},compatibility.freeze_compatibility(task()))
    with pytest.raises(ServiceError,match='dependency_invalid'):
        compatibility.freeze_compatibility(task({**ASSETS,'assets/anime.esm.min.js':ASSETS['anime.esm.min.js']}))


def test_unavailable_skill_never_enters_search_read_or_staged_package_files(tmp_path):
    row = dependent()
    plain = published(1)
    adapter = SkillProductionAdapter(lambda _: [row,plain],None,tmp_path/'ledger.sqlite3')
    result = adapter.freeze('alice','without-library',task(),['html-svg'])
    assert len(result['catalog']['skills']) == 1
    assert set(result['packages']) == {plain['skill_package']['sha256']}
    tools = SkillCatalogTools(result['catalog'],result['packages'])
    assert not tools.search('fixture-0')['skills']
    with pytest.raises(ServiceError,match='not_in_snapshot'):
        tools.read(row['skill_package']['sha256'])
    assert not any(row['skill_package']['sha256'] in item.name for item in bundle_files(result['catalog'],result['packages']))
    full = adapter.freeze('alice','with-library',task(ASSETS),['html-svg'])
    assert set(full['packages']) == {row['skill_package']['sha256'],plain['skill_package']['sha256']}
    legacy = adapter.freeze('alice','legacy-source',task(ASSETS,base_source=None),['html-svg'])
    assert set(legacy['packages']) == {plain['skill_package']['sha256']}


def test_authenticated_metadata_read_cannot_hide_dependency_and_new_intent_is_versioned(tmp_path):
    row = dependent()
    adapter = SkillProductionAdapter(lambda _: [metadata(row)],
        lambda *_:dict(methods=[row]),tmp_path/'ledger.sqlite3')
    result = adapter.freeze('alice','metadata',task(),['html-svg'])
    assert not result['packages'] and not result['catalog']['skills']
    binding = {key:result[key] for key in ('recipient_owner','consumer_id','task_sha256','allowed_engines')}
    intent = adapter._entry(binding)['intent']
    assert intent['compatibility']['schema'] == compatibility.SCHEMA
    assert intent['compatibility']['task_sha256'] == digest(task())


def test_pending_and_completed_historical_intents_keep_original_catalog_bytes(tmp_path):
    row = dependent()
    document = task()
    adapter = SkillProductionAdapter(lambda _:pytest.fail('No new discovery'),lambda *_:dict(methods=[row]),
        tmp_path/'ledger.sqlite3')
    binding = dict(recipient_owner='alice',consumer_id='historical',task_sha256=digest(document),allowed_engines=['html-svg'])
    # Pre-upgrade pending intent: no new compatibility policy existed yet.
    adapter._entry(binding,intent=dict(methods=[metadata(row)]))
    historical = adapter.freeze('alice','historical',document,['html-svg'])
    assert row['skill_package']['sha256'] in historical['packages']
    reopened = SkillProductionAdapter(lambda _:pytest.fail('Frozen read must not discover'),None,adapter.ledger_path)
    assert reopened.freeze('alice','historical',document,['html-svg'],existing=historical) == historical
    assert reopened.get_bundle('alice','historical',historical['catalog']['sha256']) == historical


def test_new_pending_intent_recovers_using_original_asset_policy_after_registry_changes(tmp_path,monkeypatch):
    row = dependent()
    def interrupted(*_): raise OSError('interrupted authenticated read')
    adapter = SkillProductionAdapter(lambda _: [metadata(row)],interrupted,tmp_path/'ledger.sqlite3')
    document = task(ASSETS)
    with pytest.raises(OSError): adapter.freeze('alice','pending',document,['html-svg'])
    monkeypatch.setattr(compatibility,'_known_libraries',lambda:{})
    reopened = SkillProductionAdapter(lambda _:pytest.fail('Do not rediscover'),lambda *_:dict(methods=[row]),adapter.ledger_path)
    recovered = reopened.freeze('alice','pending',document,['html-svg'])
    assert row['skill_package']['sha256'] in recovered['packages']
    future = SkillProductionAdapter(lambda _: [row],None,tmp_path/'future.sqlite3')
    assert not future.freeze('alice','new-version',document,['html-svg'])['packages']


def test_method_selection_checks_both_metadata_and_skill_package_dependencies():
    row = dependent()
    row['runtime_requirements'] = {'engine':'html-svg'}
    with pytest.raises(ServiceError,match='dependency_not_admitted'):
        compatibility.require_method_compatible(row,assets=ASSETS,authoring_libraries={})
    compatibility.require_method_compatible(row,assets=ASSETS,authoring_libraries={'animejs':descriptor()})
    row['skill_package']['runtime_requirements'] = {'engines':['html-svg']}
    row['runtime_requirements']['authoring_libraries'] = {'animejs':descriptor()}
    with pytest.raises(ServiceError,match='dependency_not_admitted'):
        compatibility.require_method_compatible(row,assets={},authoring_libraries={'animejs':descriptor()})


def test_legacy_method_production_excludes_dependencies_instead_of_ignoring_metadata(tmp_path):
    store = AnimationMethodStore(tmp_path/'methods')
    row = legacy_published(store,runtime_requirements={'engine':'html-svg','authoring_libraries':{'animejs':descriptor()}})
    adapter = MethodProductionAdapter(lambda _: [row],lambda *_:pytest.fail('Excluded version must not be delivered'),
        ledger_path=tmp_path/'legacy.sqlite3')
    result = adapter.freeze('alice','legacy',LEGACY_TASK,['html-svg'])
    assert result['methods'] == []
    assert result['selection']['excluded'] == [dict(version_id=row['id'],reason='dependency_not_supported_by_legacy_source')]


def test_actual_lab_explicit_method_entry_cannot_bypass_package_dependency_checks(tmp_path):
    service = AnimationMethodService(tmp_path/'lab',author_gateway=SkillGateway(),review_gateway=SkillGateway(True),
        renderer=Renderer(tmp_path/'renders'))
    body = method(CRITERIA)
    body.update(schema='video.animation-method.v2',skill_package=dependent()['skill_package'],research_record_ids=[])
    version = service.methods.create_method('developer',body,'dependency-fixture')
    with pytest.raises(ServiceError,match='dependency_not_admitted'):
        start(service,criteria=CRITERIA,method_versions=[version['id']])
    service._runtime_capabilities = enable_in_capabilities(runtime_capabilities(['html-svg']))
    result = start(service,criteria=CRITERIA,method_versions=[version['id']])
    assert result['id']


def test_only_explicit_server_producer_context_offers_manim_skills(tmp_path):
    from video_workbench.manim_method_resources import PRODUCER_REQUIREMENT
    row = dependent(resources=True)
    adapter = SkillProductionAdapter(lambda _: [row], None, tmp_path/'producer.sqlite3')
    # Neither task metadata nor old generated assets can create a producer.
    document = task({'manim/old.png': 'a'*64}, resource_authoring=PRODUCER_REQUIREMENT)
    ordinary = adapter.freeze('alice', 'formal-consumer', document, ['html-svg'])
    assert ordinary['packages'] == {}
    producer = adapter.freeze('alice', 'recipe-consumer', document, ['html-svg'],
        resource_authoring=PRODUCER_REQUIREMENT)
    package = row['skill_package']
    tools = SkillCatalogTools(producer['catalog'], producer['packages'])
    assert tools.search(package['skill_id'])['skills']
    assert tools.read(package['sha256'])
    intent = adapter._entry({key:producer[key] for key in (
        'recipient_owner', 'consumer_id', 'task_sha256', 'allowed_engines')})['intent']
    assert intent['compatibility']['schema'] == compatibility.PRODUCER_SCHEMA
    assert intent['compatibility']['resource_authoring'] == PRODUCER_REQUIREMENT
    mismatched = deepcopy(PRODUCER_REQUIREMENT)
    mismatched['manim']['version'] = '0.22.2'
    assert not compatibility.requirements_compatible({'resource_authoring':mismatched}, intent['compatibility'])
    for bad in [mismatched, {}, {'manim': {'version': '0.22.1'}}]:
        with pytest.raises(ServiceError, match='dependency_invalid'):
            compatibility.freeze_compatibility(document, resource_authoring=bad)
    with pytest.raises(ServiceError, match='dependency_invalid'):
        compatibility.freeze_compatibility(task(base_source=None), resource_authoring=PRODUCER_REQUIREMENT)
    changed_v1 = deepcopy(intent['compatibility'])
    changed_v1['schema'] = compatibility.SCHEMA
    with pytest.raises(ServiceError, match='dependency_invalid'):
        compatibility.validate_compatibility(changed_v1)


def test_existing_producer_and_ordinary_freezes_keep_original_context_on_recovery(tmp_path):
    from video_workbench.manim_method_resources import PRODUCER_REQUIREMENT
    row = dependent(resources=True)
    def interrupted(*_): raise OSError('interrupted authenticated read')
    adapter = SkillProductionAdapter(lambda _: [metadata(row)], interrupted, tmp_path/'recovery.sqlite3')
    document = task()
    with pytest.raises(OSError):
        adapter.freeze('alice', 'producer-pending', document, ['html-svg'], resource_authoring=PRODUCER_REQUIREMENT)
    with pytest.raises(OSError):
        adapter.freeze('alice', 'ordinary-pending', document, ['html-svg'])
    reopened = SkillProductionAdapter(lambda _:pytest.fail('No rediscovery'), lambda *_:dict(methods=[row]), adapter.ledger_path)
    # Both recover the saved intent, even when this call's optional argument differs.
    original_producer = reopened.freeze('alice', 'producer-pending', document, ['html-svg'])
    assert row['skill_package']['sha256'] in original_producer['packages']
    assert reopened.freeze('alice', 'producer-pending', document, ['html-svg'], existing=original_producer) == original_producer
    original_ordinary = reopened.freeze('alice', 'ordinary-pending', document, ['html-svg'],
        resource_authoring=PRODUCER_REQUIREMENT)
    assert original_ordinary['packages'] == {}
    assert reopened.freeze('alice', 'ordinary-pending', document, ['html-svg'], existing=original_ordinary) == original_ordinary
