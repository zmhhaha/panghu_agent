"""New Wiki/Skill roles get distinct disabled identities, not legacy acceptance."""
from copy import deepcopy
import hashlib

import pytest

from agent_service.deploy.animation_method_catalog import (
    SKILL_PROFILES, measure_animation_method_bundle, prepare_animation_method_catalog,
)
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
