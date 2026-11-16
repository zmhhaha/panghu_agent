"""Skill upgrades replace routing pointers, never historical execution pins."""
from copy import deepcopy
import hashlib

import pytest

from agent_service.deploy.animation_method_catalog import (
    SKILL_PROFILES, measure_animation_method_bundle, prepare_animation_method_catalog,
    prepare_animation_method_catalog_upgrade,
)
from agent_service.deploy.video_catalog import VideoCatalogError, registry
from agent_service.profiles import video_animation_skill_method as method
from test_animation_review_catalog import IMAGE, model, public_config


NEW_IMAGE = IMAGE.replace('c' * 64, 'd' * 64)


def previous_catalog(monkeypatch):
    """Synthetic accepted v3 identities, measured from the retained v3 bytes."""
    root = method.BUNDLE_ROOT.parent / 'animation-method-v3'
    with monkeypatch.context() as patch:
        patch.setattr(method, 'BUNDLE_REVISION', 'animation-method-v3')
        patch.setattr(method, 'BUNDLE_ROOT', root)
        patch.setattr(method, 'BUNDLE_MANIFEST_SHA256',
                      hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest())
        manifests, _ = measure_animation_method_bundle(skills=True)
        current, _, _ = prepare_animation_method_catalog(public_config(),
            worker_image=IMAGE, resource_manifests=manifests,
            source_pool_id='existing-pool', review_model_policy=model(), skills=True)
    resolved = registry(current)
    for index, profile in enumerate(SKILL_PROFILES):
        route = next(row for row in current['routes'] if row['profile'] == profile)
        binding = next(row for row in current['execution_bindings']
                       if (row['id'], row['revision']) == (route['binding_id'], route['binding_revision']))
        binding['acceptance'] = dict(
            combination_sha256=resolved.binding_policy(binding['id'], binding['revision'])
                .execution_binding.combination_sha256,
            suite=profile.rsplit('.v', 1)[0] + '-contract.v2', report_sha256='e' * 64)
        route['enabled'] = True
        pool = next(row for row in current['kubernetes_workers']
                    if row['binding_revision'] == binding['revision'])
        pool.update(enabled=True, uid_start=12000000 + index * 100000, uid_count=65536)
    # Keep an unrelated route between the two pointers being replaced.
    current['routes'] = [current['routes'][1], current['routes'][0], current['routes'][2]]
    registry(current)
    return current


def upgrade(current, **changes):
    manifests, _ = measure_animation_method_bundle(skills=True)
    options = dict(worker_image=NEW_IMAGE, resource_manifests=manifests,
        source_pool_id='existing-pool', review_model_policy=model())
    options.update(changes)
    return prepare_animation_method_catalog_upgrade(current, **options)


def test_upgrade_replaces_only_two_route_slots_and_preserves_original_input(monkeypatch):
    current = previous_catalog(monkeypatch)
    before = deepcopy(current)
    merged, hosts, receipt = upgrade(current)
    assert current == before
    assert [row['profile'] for row in merged['routes']] == [row['profile'] for row in before['routes']]
    assert merged['routes'][1] == before['routes'][1]
    for index in (0, 2):
        assert merged['routes'][index]['profile'] == before['routes'][index]['profile']
        assert merged['routes'][index]['binding_revision'] != before['routes'][index]['binding_revision']
        assert merged['routes'][index]['enabled'] is False
    for key in ('task_contracts', 'capability_bundles', 'tool_environments', 'execution_bindings',
                'kubernetes_workers'):
        assert merged[key][:len(before[key])] == before[key]
    assert merged['callers'] == before['callers']
    assert receipt['schema'] == 'animation-method-catalog-upgrade-preparation.v1'
    assert [row['index'] for row in receipt['replaced_routes']] == [0, 2]
    assert receipt['caller_registered'] is False
    assert receipt['existing_callers_unchanged'] is receipt['historical_resources_preserved'] is True
    assert receipt['model_requests'] == 0 and receipt['uid_reserved'] is False
    assert set(hosts) == set(SKILL_PROFILES)
    for host in hosts.values():
        assert host['profile']['enabled'] is False
        binding = host['execution_catalog']['execution_bindings'][0]
        assert 'acceptance' not in binding
        assert binding['bundle']['revision'] == method.BUNDLE_REVISION
    for pool in merged['kubernetes_workers'][-2:]:
        assert pool['enabled'] is False and pool['uid_start'] is None and pool['image'] == NEW_IMAGE
    # The old bindings remain independently resolvable for frozen attempts.
    after = registry(merged)
    previous = registry(before)
    for binding in before['execution_bindings']:
        assert after.binding(binding['id'], binding['revision']) == previous.binding(
            binding['id'], binding['revision'])
        assert after.binding_policy(binding['id'], binding['revision']).pins() == previous.binding_policy(
            binding['id'], binding['revision']).pins()


def test_conflicting_bundle_identity_is_rejected_without_rewriting_history(monkeypatch):
    current = previous_catalog(monkeypatch)
    old = next(row for row in current['capability_bundles'] if row['name'] == 'animation-skill-method')
    current['capability_bundles'].append(dict(deepcopy(old), revision=method.BUNDLE_REVISION))
    before = deepcopy(current)
    registry(current)
    with pytest.raises(VideoCatalogError, match='existing_catalog_identity_conflict'):
        upgrade(current)
    assert current == before


@pytest.mark.parametrize('changes', [dict(register_caller=True), dict(skills=False), dict(skills=1)])
def test_upgrade_cannot_register_credentials_or_change_protocol(monkeypatch, changes):
    with pytest.raises(VideoCatalogError, match='upgrade_skills_without_caller_registration_required'):
        upgrade(previous_catalog(monkeypatch), **changes)


def test_both_existing_routes_and_exact_registered_caller_are_required(monkeypatch):
    current = previous_catalog(monkeypatch)
    missing = deepcopy(current)
    missing['routes'] = [row for row in missing['routes'] if row['profile'] != SKILL_PROFILES[0]]
    with pytest.raises(VideoCatalogError, match='upgrade_existing_routes_required'):
        upgrade(missing)
    changed = deepcopy(current['callers'][-1])
    changed['token_ref'] = 'replacement-token'
    with pytest.raises(VideoCatalogError, match='upgrade_existing_caller_required'):
        upgrade(current, caller=changed)
