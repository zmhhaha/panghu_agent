"""Frozen spatial intent reaches the actual worker contract; no pixel claims.

The stopped worker peer proves input/result custody. Its source is deliberately
synthetic and is not presented as native Three execution or visual acceptance.
"""
from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_animation_film_module_contract as C
from agent_service.profiles.video_animation_film_module_spatial import (
    make_spatial_offer, validate_spatial_offer,
)
from agent_service.spatial_rendering_contract import INTENT_SCHEMA, resolve_quality
from video_workbench.film_creation_store import FilmCreationStore
from video_workbench.film_creation_workflow import FilmCreationWorkflow
from video_workbench.spatial_rendering_route import FILM_FEATURES
import test_film_creation_workflow as fixtures
from test_film_creation_workflow import OWNER, env, module_request


def intent(engine='three'):
    return dict(schema=INTENT_SCHEMA, space_mode='geometric' if engine == 'three' else 'planar',
        engine=engine, camera_mode='animated',
        required_features=['form', 'coverage', 'perspective_camera', 'viewpoint_change'] if engine == 'three' else ['coverage'],
        optional_features=['lighting', 'texture', 'atmosphere', 'depth_of_field'],
        quality=dict(structural_errors='block', optional_degradation='declared'),
        light=dict(space='world', direction=[.55, .52, -.72], response='artistic'))


def request(env, *, engine='three', base=None):
    value = module_request(env)
    value['rendering_intent'] = intent(engine)
    value['rendering_quality'] = resolve_quality(value['rendering_intent'], FILM_FEATURES[engine])
    if base is not None:
        value['base_module_version_id'] = base
    return value


def start(env, value, key='film-spatial-request-1'):
    return env.workflow.start_module(OWNER, env.project['id'], env.project['scenes'][0]['id'], value, key)


def configure_engines(env):
    old = env.creation
    root = Path(__file__).resolve().parents[2]/'video_workbench/motion_renderer/vendor'
    assets = {'assets/' + name: (root/name).read_bytes() for name in ('three.module.min.js', 'three.LICENSE.txt')}
    caps = deepcopy(old.runtime_capabilities)
    caps.update(allowed_engines=['html-svg', 'canvas', 'three'],
        assets={name.removeprefix('assets/'): hashlib.sha256(raw).hexdigest() for name, raw in assets.items()})
    creation = FilmCreationStore(env.store, runtime_capabilities=caps, runtime_fingerprint=old.runtime,
        host_files=old.host_files, trusted_host_sha256=old.hosts, image=old.image, assets=assets, enabled=True)
    env.creation = env.workflow.creation = env.gateway.creation = env.renderer.creation = creation


def three_results(monkeypatch):
    original = fixtures.raw_result
    def result(document):
        raw = original(document)
        raw['module']['engine'] = 'three'
        raw['module']['assets'] = ['assets/three.module.min.js', 'assets/three.LICENSE.txt']
        return raw
    monkeypatch.setattr(fixtures, 'raw_result', result)


def completed(env, value):
    op = start(env, value)
    env.workflow.tick()
    done = env.workflow.get_module(OWNER, op['id'])
    assert done['status'] == 'completed', done
    return done


def test_selected_engine_light_and_resolved_quality_reach_real_worker_input_and_frozen_catalog(env, monkeypatch):
    configure_engines(env);three_results(monkeypatch)
    value = request(env)
    done = completed(env, value)
    document = env.gateway.documents[done['id']]
    offer = document['constraints']['spatial_rendering']
    assert offer == make_spatial_offer(value['rendering_intent'], value['rendering_quality'])
    assert offer['intent']['light'] == value['rendering_intent']['light']
    assert offer['quality']['status'] == 'degraded'
    assert {'lighting', 'texture', 'atmosphere', 'depth_of_field'} == {
        row['feature'] for row in offer['quality']['degraded_features']}
    assert offer['evidence_scope'] == 'host_frozen_requirements_not_execution_or_visual_acceptance'
    assert document['constraints']['allowed_engines'] == document['skill_catalog']['allowed_engines'] == ['three']
    assert 'APP-FROZEN-SPATIAL-RENDERING' in document['scene']['instruction']
    assert canonical_bytes(offer).decode() in document['scene']['instruction']
    assert 'world XYZ' in document['scene']['instruction']
    assert 'grants no tools.surface' in document['scene']['instruction']
    package = env.workflow.module_package(OWNER, done['version_id'])
    assert package['engine'] == package['result']['module']['engine'] == 'three'
    assert package['input_sha256'] == digest(document)
    assert package['input_document']['constraints']['spatial_rendering'] == offer


def test_wrong_result_engine_is_rejected_by_actual_worker_contract_before_version_creation(env):
    configure_engines(env)
    op = start(env, request(env))
    # Existing fake peer deliberately returns html-svg; the Three-only input is
    # now enforced by the same validator that receives stopped Agent output.
    env.workflow.tick()
    failed = env.workflow.get_module(OWNER, op['id'])
    assert failed['status'] == 'failed' and failed['stop_proven'] is True
    assert failed['version_id'] is None
    assert failed['error'] == 'film_module_resources_not_admitted'
    assert env.gateway.documents[op['id']]['constraints']['allowed_engines'] == ['three']


@pytest.mark.parametrize('missing', ['rendering_intent', 'rendering_quality'])
def test_request_fields_must_be_paired_before_catalog_or_author_work(env, missing):
    value = request(env, engine='html-svg');value.pop(missing)
    with pytest.raises(ServiceError, match='film_creation_rendering_requirements_incomplete'):
        start(env, value)
    assert env.catalog_calls == env.gateway.calls == []
    assert env.creation.active_operations() == []


def test_unadmitted_engine_and_forged_optional_feature_resolution_fail_before_author_work(env):
    value = request(env)
    with pytest.raises(ServiceError, match='film_creation_rendering_engine_not_admitted'):
        start(env, value)
    configure_engines(env)
    value = request(env)
    value['rendering_quality'] = resolve_quality(value['rendering_intent'], FILM_FEATURES['three'] | {'lighting'})
    with pytest.raises(ServiceError, match='film_creation_rendering_quality_changed'):
        start(env, value)
    assert env.catalog_calls == env.gateway.calls == []


def test_film_route_does_not_gain_scene_cpu_helper_features_from_intent(env):
    configure_engines(env)
    value = request(env)
    value['rendering_intent']['optional_features'].remove('lighting')
    value['rendering_intent']['required_features'].append('lighting')
    with pytest.raises(ServiceError, match='spatial_rendering_required_features_unavailable'):
        start(env, value)
    assert env.catalog_calls == env.gateway.calls == []


def test_new_request_idempotency_covers_complete_intent_and_quality(env):
    configure_engines(env)
    value = request(env)
    op = start(env, value)
    assert start(env, value)['id'] == op['id']
    assert op['request_sha256'] == digest(value)
    changed = deepcopy(value);changed['rendering_intent']['light']['direction'][0] = -.55
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        start(env, changed)
    assert env.gateway.calls == []


def test_refinement_inherits_frozen_offer_from_exact_base_when_new_fields_are_omitted(env, monkeypatch):
    configure_engines(env);three_results(monkeypatch)
    base = completed(env, request(env))
    package = env.workflow.module_package(OWNER, base['version_id']);original_bytes = canonical_bytes(package)
    value = module_request(env)
    value.update(base_module_version_id=base['version_id'], instruction='Preserve the subject; repair geometry.')
    op = start(env, value, 'film-spatial-refinement-1')
    core = env.creation.operation('module', op['id'])['document']['core_task']
    assert core['constraints']['spatial_rendering'] == package['input_document']['constraints']['spatial_rendering']
    assert core['constraints']['allowed_engines'] == ['three']
    assert core['base_module'] == package['result']['module']
    env.workflow.tick()
    assert env.workflow.get_module(OWNER, op['id'])['status'] == 'completed'
    assert env.gateway.documents[op['id']]['constraints']['spatial_rendering'] == core['constraints']['spatial_rendering']
    assert canonical_bytes(env.workflow.module_package(OWNER, base['version_id'])) == original_bytes


def test_refinement_cannot_silently_swap_base_light_or_quality_offer(env, monkeypatch):
    configure_engines(env);three_results(monkeypatch)
    base = completed(env, request(env))
    before = list(env.gateway.calls)
    value = request(env, base=base['version_id'])
    value['rendering_intent']['light']['direction'][0] = -.55
    with pytest.raises(ServiceError, match='film_creation_base_rendering_requirements_changed'):
        start(env, value, 'film-spatial-refinement-swap')
    assert env.gateway.calls == before and len(env.gateway.jobs) == 1


def test_frozen_offer_survives_lost_submission_and_recovery_without_new_intent_selection(env, monkeypatch):
    configure_engines(env);three_results(monkeypatch)
    op = start(env, request(env))
    env.gateway.lose_submit = True
    env.workflow.tick()
    frozen = canonical_bytes(env.creation.operation('module', op['id'])['document'])
    recovery = FilmCreationStore.for_recovery(env.store)
    workflow = FilmCreationWorkflow(recovery, env.gateway, env.adapter, env.renderer, enabled=False)
    workflow.tick()
    done = workflow.get_module(OWNER, op['id'])
    assert done['status'] == 'completed'
    assert canonical_bytes(recovery.operation('module', op['id'])['document']) == frozen
    assert len([call for call in env.gateway.calls if call[0] == 'submit' and call[1] == op['id']]) == 1


def test_legacy_requests_retain_original_constraint_fields_and_package_protocol(env):
    value = module_request(env)
    done = completed(env, value)
    package = env.workflow.module_package(OWNER, done['version_id'])
    assert set(package['input_document']['constraints']) == {
        'width', 'height', 'fps', 'duration_seconds', 'allowed_engines', 'runtime_identity'}
    assert 'spatial_rendering' not in env.creation.operation('module', done['id'])
    assert 'APP-FROZEN-SPATIAL-RENDERING' not in package['input_document']['scene']['instruction']
    # A new unmarked request follows the already serving module-v3 binding;
    # historical module-v2 bytes remain separately available for frozen jobs.
    assert package['profile']['bundle_manifest_sha256'] == fixtures.P.BUNDLE_MANIFEST_SHA256
    assert fixtures.P.HISTORICAL_BUNDLES['animation-film-module-v2'][1] == (
        '53d43557bbd8e672ee2c45e536cff046f2f522b0b6086adc38d19ed5af01c414')
    fixtures.P.verified_bundle('animation-film-module-v2')


def test_worker_offer_rejects_mutation_broad_engine_lists_and_extra_fields(env):
    configure_engines(env)
    op = start(env, request(env))
    core = env.creation.operation('module', op['id'])['document']['core_task']
    changed = deepcopy(core);changed['constraints']['allowed_engines'].append('html-svg')
    with pytest.raises(ServiceError, match='film_module_spatial_engine_mismatch'):
        C.validate_core_task(changed)
    changed = deepcopy(core);changed['constraints']['spatial_rendering']['intent']['light']['direction'][0] = -.55
    with pytest.raises(ServiceError, match='film_module_spatial_offer_invalid'):
        C.validate_core_task(changed)
    changed = deepcopy(core);changed['constraints']['spatial_rendering']['arbitrary_helper'] = 'tools.surface'
    with pytest.raises(ServiceError, match='film_module_spatial_offer_invalid'):
        C.validate_core_task(changed)
    changed = deepcopy(core);changed['constraints']['spatial_rendering']['revision'] = True
    with pytest.raises(ServiceError, match='film_module_spatial_offer_invalid'):
        C.validate_core_task(changed)
    offer = deepcopy(core['constraints']['spatial_rendering'])
    offer['quality']['enabled_features'].remove('form')
    with pytest.raises(ServiceError, match='spatial_rendering_required_features_unavailable'):
        validate_spatial_offer(offer)


def test_persisted_core_cannot_drift_from_its_frozen_offer_before_skill_admission(env):
    configure_engines(env)
    op = start(env, request(env))
    durable = env.creation.operation('module', op['id'])
    durable['document']['core_task']['constraints']['spatial_rendering'] = make_spatial_offer(
        intent('html-svg'), resolve_quality(intent('html-svg'), FILM_FEATURES['html-svg']))
    with env.store.transaction() as db:
        env.creation._write(db, durable)
    with pytest.raises(ServiceError, match='film_creation_rendering_requirements_changed'):
        env.creation.prepare_module(op['id'], env.adapter)
    assert env.catalog_calls == env.gateway.calls == []
