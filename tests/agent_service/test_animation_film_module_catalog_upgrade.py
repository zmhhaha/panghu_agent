"""The explicit module-route upgrade retains accepted v1 execution identities."""
from copy import deepcopy

import pytest

from agent_service.deploy.animation_film_module_catalog import prepare_animation_film_module_catalog_upgrade
from agent_service.deploy.animation_production_catalog import (
    measure_animation_production_bundle, prepare_animation_production_catalog,
)
from agent_service.deploy.video_catalog import VideoCatalogError, registry
from agent_service.profiles import video_animation_film_module as profile
from test_animation_review_catalog import IMAGE, public_config


def old_catalog(monkeypatch):
    original_verify = profile.verified_bundle
    with monkeypatch.context() as patch:
        patch.setattr(profile, 'BUNDLE_REVISION', 'animation-film-module-v1')
        patch.setattr(profile, 'BUNDLE_ROOT', profile.BUNDLE_ROOT.parent / 'animation-film-module-v1')
        patch.setattr(profile, 'BUNDLE_MANIFEST_SHA256', profile.HISTORICAL_BUNDLES['animation-film-module-v1'][1])
        patch.setattr(profile, 'verified_bundle', lambda: original_verify('animation-film-module-v1'))
        manifests, _ = measure_animation_production_bundle(modules=True)
        current, _, _ = prepare_animation_production_catalog(public_config(), worker_image=IMAGE,
            resource_manifests=manifests, source_pool_id='existing-pool', modules=True)
    resolved = registry(current)
    route = next(row for row in current['routes'] if row['profile'] == profile.PROFILE)
    binding = next(row for row in current['execution_bindings'] if row['revision'] == route['binding_revision'])
    binding['acceptance'] = dict(combination_sha256=resolved.binding_policy(binding['id'], binding['revision'])
        .execution_binding.combination_sha256, suite='video.animation-film-module-contract.v1', report_sha256='e' * 64)
    route['enabled'] = True
    pool = next(row for row in current['kubernetes_workers'] if row['binding_revision'] == binding['revision'])
    pool.update(enabled=True, uid_start=12000000, uid_count=65536)
    registry(current)
    return current


def upgrade(current, **changes):
    manifests, _ = measure_animation_production_bundle(modules=True)
    options = dict(worker_image=IMAGE.replace('c' * 64, 'd' * 64),
        resource_manifests=manifests, source_pool_id='existing-pool')
    options.update(changes)
    return prepare_animation_film_module_catalog_upgrade(current, **options)


def test_accepted_old_module_route_gets_new_disabled_binding_without_rewriting_history(monkeypatch):
    current = old_catalog(monkeypatch)
    before = deepcopy(current)
    merged, hosts, report = upgrade(current)
    assert current == before and merged['callers'] == before['callers']
    for key in ('task_contracts', 'capability_bundles', 'tool_environments', 'execution_bindings', 'kubernetes_workers'):
        assert merged[key][:len(before[key])] == before[key]
    assert merged['routes'][0] == before['routes'][0]
    assert merged['routes'][-1]['profile'] == profile.PROFILE
    assert merged['routes'][-1]['enabled'] is False
    assert merged['routes'][-1]['binding_revision'] != before['routes'][-1]['binding_revision']
    assert merged['kubernetes_workers'][-1]['enabled'] is False and merged['kubernetes_workers'][-1]['uid_start'] is None
    assert hosts[profile.PROFILE]['execution_catalog']['capability_bundles'][0]['revision'] == profile.BUNDLE_REVISION
    assert report['replaced_routes'][0]['before'] == before['routes'][-1]
    assert report['old_pools_and_uids_preserved'] and not report['uid_reserved'] and not report['acceptance_created']
    previous, after = registry(before), registry(merged)
    for binding in before['execution_bindings']:
        assert after.binding_policy(binding['id'], binding['revision']).pins() == previous.binding_policy(
            binding['id'], binding['revision']).pins()


def test_upgrade_rejects_credential_or_historical_bundle_replacement(monkeypatch):
    current = old_catalog(monkeypatch)
    caller = dict(current['callers'][-1], token_ref='replacement')
    with pytest.raises(VideoCatalogError, match='existing_caller_required'): upgrade(current, caller=caller)
    old = next(row for row in current['capability_bundles'] if row['revision'] == 'animation-film-module-v1')
    current['capability_bundles'].append(dict(deepcopy(old), revision=profile.BUNDLE_REVISION))
    before = deepcopy(current)
    with pytest.raises(VideoCatalogError, match='existing_catalog_identity_conflict'): upgrade(current)
    assert current == before
