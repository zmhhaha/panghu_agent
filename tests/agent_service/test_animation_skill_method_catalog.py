"""New Wiki/Skill roles get distinct disabled identities, not legacy acceptance."""
from copy import deepcopy
import hashlib

import pytest

from agent_service.deploy.animation_method_catalog import (
    LEGACY_CALLER, PROFILES, SKILL_CALLER, SKILL_PROFILES, measure_animation_method_bundle,
    prepare_animation_method_catalog, unify_animation_method_caller,
)
from agent_service.contracts import JobRequest, Principal, ServiceError, digest
from agent_service.deploy.acceptance_probe import candidate_config
from agent_service.deploy.video_catalog import VideoCatalogError, registry
from agent_service.profiles import implementation, video_animation_skill_method as method
from agent_service.worker_entry import WorkerConfigError
from test_animation_method_catalog import prepare as prepare_legacy
from test_animation_review_catalog import IMAGE, model, public_config


def prepare(current=None, **changes):
    manifests, _ = measure_animation_method_bundle(skills=True)
    options = dict(worker_image=IMAGE, resource_manifests=manifests,
        source_pool_id='existing-pool', review_model_policy=model(), skills=True)
    options.update(changes)
    return prepare_animation_method_catalog(public_config() if current is None else current, **options)


def test_skill_roles_are_distinct_and_can_coexist_with_legacy_routes():
    current, old_hosts, _ = prepare_legacy()
    before = deepcopy(current)
    merged, hosts, receipt = prepare(current)
    assert current == before
    assert set(hosts) == set(SKILL_PROFILES)
    assert set(hosts).isdisjoint(old_hosts)
    assert merged['routes'][:len(before['routes'])] == before['routes']
    assert merged['callers'][-1] == dict(tenant='internal', caller='video-animation-skill-methods',
        profiles=list(SKILL_PROFILES), token_ref='video-animation-skill-methods')
    resolved = registry(merged)
    for name, host in hosts.items():
        parsed, policy, binding = candidate_config(host)
        assert parsed.profile.name == name and policy.enabled is False
        assert binding.acceptance is None
        assert binding.bundle.name == 'animation-skill-method'
        assert binding.bundle.revision == method.BUNDLE_REVISION
        contract = resolved.contract(name, '1')
        assert contract.output_schema == implementation(name).output_schema
        assert contract.output_schema.endswith('.v2')
        combo = next(row for row in receipt['combinations'] if row['profile'] == name)
        assert combo['acceptance_suite'].endswith('-contract.v2')
        assert combo['acceptance_created'] is combo['route_enabled'] is combo['pool_enabled'] is False
        assert combo['uid_reserved'] is False
    assert receipt['model_requests'] == 0


def test_measured_wiki_skill_bundle_binds_exact_reviewed_files():
    manifests, measurement = measure_animation_method_bundle(skills=True)
    assert set(manifests) == {method.BUNDLE_REVISION}
    assert measurement['manifest_sha256'] == method.BUNDLE_MANIFEST_SHA256
    assert measurement['container_measured'] is False
    for row in measurement['local_files']:
        content = (method.BUNDLE_ROOT / row['path']).read_bytes()
        assert hashlib.sha256(content).hexdigest() == row['sha256']
        assert len(content) == row['bytes']


def test_legacy_resources_and_caller_scope_cannot_admit_new_roles():
    old_manifests, _ = measure_animation_method_bundle()
    with pytest.raises(VideoCatalogError, match='bundle_manifests_required'):
        prepare(resource_manifests=old_manifests)
    legacy, _, _ = prepare_legacy()
    with pytest.raises(VideoCatalogError, match='dedicated_animation_method_caller_required'):
        prepare(caller=legacy['callers'][-1])
    for value in (None, 0, 1, 'yes'):
        with pytest.raises(VideoCatalogError, match='protocol_invalid'):
            measure_animation_method_bundle(skills=value)


def test_preparation_does_not_turn_old_acceptance_into_new_readiness():
    merged, hosts, _ = prepare()
    for host in hosts.values():
        changed = deepcopy(host)
        row = changed['execution_catalog']['execution_bindings'][0]
        # Even a matching combination needs the actual v2 domain suite.
        _, _, binding = candidate_config(host)
        policy = registry(merged).binding_policy(binding.id, binding.revision)
        row['acceptance'] = dict(combination_sha256=policy.execution_binding.combination_sha256,
            suite=binding.profile.rsplit('.v', 1)[0] + '-contract.v1', report_sha256='a' * 64)
        with pytest.raises(WorkerConfigError, match='invalid_worker_configuration'):
            candidate_config(changed)


def test_new_method_roles_use_the_new_capture_protocol():
    from agent_service.deploy import animation_method_acceptance as admission
    for profile in SKILL_PROFILES:
        assert profile in admission.PROFILES
        assert admission._protocol(profile) is method
    assert admission._protocol(document={'schema': method.INPUT_SCHEMA}) is method
    with pytest.raises(Exception, match='profile_not_allowed'):
        admission._protocol('video.animation-method-author.v99')


def both_method_callers():
    legacy, _, _ = prepare_legacy()
    current, _, _ = prepare(legacy)
    resolved = registry(current)
    # Exercise retirement of genuinely enabled routing pointers, without
    # changing or removing their original acceptance and execution resources.
    for profile in PROFILES:
        route = next(row for row in current['routes'] if row['profile'] == profile)
        binding = next(row for row in current['execution_bindings']
                       if (row['id'], row['revision']) == (route['binding_id'], route['binding_revision']))
        binding['acceptance'] = dict(combination_sha256=resolved.binding_policy(
            binding['id'], binding['revision']).execution_binding.combination_sha256,
            suite=profile.rsplit('.v', 1)[0] + '-contract.v1', report_sha256='f' * 64)
        route['enabled'] = True
    registry(current)
    return current


def test_caller_unification_preserves_history_and_only_changes_scope_and_retired_routes():
    current = both_method_callers()
    before = deepcopy(current)
    merged, receipt = unify_animation_method_caller(current)
    assert current == before
    assert not any(row['caller'] == LEGACY_CALLER for row in merged['callers'])
    target = next(row for row in merged['callers'] if row['caller'] == SKILL_CALLER)
    assert target == dict(tenant='internal', caller=SKILL_CALLER, token_ref=SKILL_CALLER,
                          profiles=list(SKILL_PROFILES + PROFILES))
    expected = deepcopy(before)
    expected['callers'] = [row for row in expected['callers'] if row['caller'] != LEGACY_CALLER]
    next(row for row in expected['callers'] if row['caller'] == SKILL_CALLER)['profiles'] += list(PROFILES)
    for route in expected['routes']:
        if route['profile'] in PROFILES:
            route['enabled'] = False
    assert merged == expected
    assert receipt['before_sha256'] == digest(before) and receipt['after_sha256'] == digest(merged)
    assert receipt['legacy_caller_removed'] is True
    assert receipt['database_migrated'] is receipt['secrets_changed'] is False
    assert receipt['model_requests'] == 0
    for profile in PROFILES:
        request = JobRequest(profile=profile, profile_revision='1', task={},
            business_ref='offline-caller-cutover', timeout_seconds=1, execution_deadline_at=1000.0)
        principal = Principal(target['tenant'], target['caller'], frozenset(target['profiles']))
        assert profile in principal.profiles  # Existing API ownership reads retain their original profile.
        with pytest.raises(ServiceError, match='profile_not_ready'):
            registry(merged).profiles().resolve(principal, request)


def test_already_unified_caller_is_idempotent_without_recreating_old_credentials():
    once, _ = unify_animation_method_caller(both_method_callers())
    twice, receipt = unify_animation_method_caller(once)
    assert twice == once
    assert receipt['before_sha256'] == receipt['after_sha256']
    assert receipt['legacy_caller_removed'] is False
    assert not any(row['caller'] == LEGACY_CALLER for row in twice['callers'])


@pytest.mark.parametrize('change', ['legacy-tenant', 'current-tenant', 'legacy-token',
    'current-token', 'legacy-extra-scope', 'current-extra-scope', 'duplicate-legacy',
    'duplicate-current', 'missing-current', 'missing-legacy-scope', 'missing-legacy-route',
    'missing-skill-route'])
def test_unification_rejects_ambiguous_or_incomplete_authority_without_mutating_input(change):
    current = both_method_callers()
    legacy = next(row for row in current['callers'] if row['caller'] == LEGACY_CALLER)
    target = next(row for row in current['callers'] if row['caller'] == SKILL_CALLER)
    if change.endswith('-tenant'):
        (legacy if change.startswith('legacy') else target)['tenant'] = 'other-tenant'
    elif change.endswith('-token'):
        (legacy if change.startswith('legacy') else target)['token_ref'] = 'different-token'
    elif change.endswith('-extra-scope'):
        (legacy if change.startswith('legacy') else target)['profiles'].append('workspace-task.v1')
    elif change.startswith('duplicate-'):
        selected = legacy if change.endswith('legacy') else target
        current['callers'].append(dict(deepcopy(selected), token_ref='duplicate-token'))
    elif change == 'missing-current':
        current['callers'].remove(target)
    elif change == 'missing-legacy-scope':
        legacy['profiles'].pop()
    elif change.endswith('-route'):
        profile = PROFILES[0] if change == 'missing-legacy-route' else SKILL_PROFILES[0]
        current['routes'] = [row for row in current['routes'] if row['profile'] != profile]
    before = deepcopy(current)
    with pytest.raises(VideoCatalogError):
        unify_animation_method_caller(current)
    assert current == before


@pytest.mark.parametrize('scopes,enabled', [(list(SKILL_PROFILES), False),
                                         (list(SKILL_PROFILES + PROFILES), True)])
def test_missing_legacy_caller_is_only_accepted_after_complete_cutover(scopes, enabled):
    current = both_method_callers()
    current['callers'] = [row for row in current['callers'] if row['caller'] != LEGACY_CALLER]
    next(row for row in current['callers'] if row['caller'] == SKILL_CALLER)['profiles'] = scopes
    for route in current['routes']:
        if route['profile'] in PROFILES:
            route['enabled'] = enabled
    before = deepcopy(current)
    with pytest.raises(VideoCatalogError, match='cutover_incomplete_identity'):
        unify_animation_method_caller(current)
    assert current == before


def test_future_skill_upgrade_keeps_unified_history_scope_and_disabled_legacy_routes(monkeypatch):
    from test_animation_skill_method_catalog_upgrade import previous_catalog, upgrade
    previous = previous_catalog(monkeypatch)
    previous, _, _ = prepare_legacy(previous)
    current, _ = unify_animation_method_caller(previous)
    before = deepcopy(current)
    merged, _, _ = upgrade(current)
    assert current == before
    assert merged['callers'] == before['callers']
    assert next(row for row in merged['callers'] if row['caller'] == SKILL_CALLER)['profiles'] == list(
        SKILL_PROFILES + PROFILES)
    for profile in PROFILES:
        assert next(row for row in merged['routes'] if row['profile'] == profile) == next(
            row for row in before['routes'] if row['profile'] == profile)
    assert not any(row['caller'] == LEGACY_CALLER for row in merged['callers'])
