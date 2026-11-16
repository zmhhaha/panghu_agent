"""New method role wiring is offline preparation, never runtime acceptance."""
from copy import deepcopy
import hashlib

import pytest

from agent_service.catalog import CATALOG_KEYS
from agent_service.deploy.animation_method_catalog import (
    PROFILES, measure_animation_method_bundle, prepare_animation_method_catalog)
from agent_service.deploy.acceptance_probe import candidate_config, fixture
from agent_service.deploy.animation_worker_host import project_animation_worker_host
from agent_service.deploy.video_catalog import VideoCatalogError, registry
from agent_service.input_tools import READ_INPUTS_ENVIRONMENT_NAME, read_inputs_environment_revision
from agent_service.profiles import video_animation_method as method
from test_animation_review_catalog import IMAGE, public_config, model


def prepare(current=None, **changes):
    manifests, _ = measure_animation_method_bundle()
    options = dict(worker_image=IMAGE, resource_manifests=manifests,
                   source_pool_id='existing-pool', review_model_policy=model())
    options.update(changes)
    return prepare_animation_method_catalog(public_config() if current is None else current, **options)


def test_exact_local_measurement_makes_no_runtime_or_vision_claim():
    manifests, receipt = measure_animation_method_bundle()
    assert set(manifests) == {method.BUNDLE_REVISION}
    assert receipt['local_files_verified'] is True
    assert receipt['container_measured'] is receipt['vision_delivery_verified'] is False
    assert receipt['model_requests'] == receipt['agent_tasks'] == 0
    assert receipt['routes_changed'] is receipt['secrets_created'] is False
    assert receipt['manifest_sha256'] == method.BUNDLE_MANIFEST_SHA256
    assert {row['path'] for row in receipt['local_files']} == method.BUNDLE_FILES
    for row in receipt['local_files']:
        raw = (method.BUNDLE_ROOT / row['path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row['sha256']
        assert len(raw) == row['bytes']


def test_new_roles_are_pure_additive_disabled_and_share_only_the_exact_bundle():
    current = public_config()
    before = deepcopy(current)
    merged, hosts, summary = prepare(current)
    assert current == before
    for key, previous in before.items():
        assert merged[key][:len(previous)] == previous if key in {
            *CATALOG_KEYS, 'kubernetes_workers', 'callers'} else merged[key] == previous
    assert set(hosts) == set(PROFILES)
    assert len(merged['capability_bundles']) == len(before['capability_bundles']) + 1
    assert merged['callers'][-1] == dict(tenant='internal', caller='video-animation-methods',
        profiles=list(PROFILES), token_ref='video-animation-methods')
    assert summary['acceptance_created'] is summary['vision_delivery_verified'] is False
    assert summary['model_requests'] == 0 and summary['uid_reserved'] is False
    for pool in merged['kubernetes_workers'][-2:]:
        assert pool['enabled'] is False and pool['uid_start'] is None
        for key in ('namespace', 'pod_cidrs', 'node_selector', 'read_only_mounts',
                    'mount_apparmor_profile', 'token_ref', 'receipt_key_ref'):
            assert pool[key] == before['kubernetes_workers'][0][key]
    assert prepare(merged) == (merged, hosts, dict(summary,
        before_sha256=summary['after_sha256'], existing_routes_preserved=len(merged['routes'])))


@pytest.mark.parametrize('profile', PROFILES)
def test_exact_hosts_reuse_general_pi_and_fixed_read_inputs_tool(profile):
    merged, hosts, summary = prepare()
    parsed, policy, binding = candidate_config(hosts[profile])
    assert parsed.engine == 'pi' and binding.adapter_revision == 'pi-cage-adapter-v1'
    assert binding.acceptance is None and policy.enabled is False
    environment = registry(merged).environment(binding.tool_environment.name, binding.tool_environment.revision)
    assert environment.name == READ_INPUTS_ENVIRONMENT_NAME
    assert set(environment.tools) == {'read_inputs', 'write'}
    assert environment.revision == read_inputs_environment_revision(environment.model_dump(mode='json'))
    needed = {'text', 'tool_calls'} | ({'vision'} if profile == method.REVIEW_PROFILE else set())
    assert set(registry(merged).contract(profile, '1').required_model_capabilities) == needed
    combo = next(row for row in summary['combinations'] if row['profile'] == profile)
    assert policy.execution_binding.combination_sha256 == combo['combination_sha256']
    host = dict(deepcopy(hosts[profile]), execution_catalog={key:deepcopy(merged[key]) for key in CATALOG_KEYS})
    projected = project_animation_worker_host(host)
    assert all(len(rows) == 1 for rows in projected['execution_catalog'].values())
    assert candidate_config(projected)[1].pins() == policy.pins()
    with pytest.raises(Exception, match='candidate_scenario_required'):
        fixture(profile)


@pytest.mark.parametrize('changes,reason', [
    ({'worker_image':'registry.invalid/pi:latest'}, 'animation_method_worker_latest_digest_required'),
    ({'worker_image':'registry.invalid/pi:v1@sha256:' + 'a'*64}, 'animation_method_worker_latest_digest_required'),
    ({'source_pool_id':None}, 'animation_review_source_pool_required'),
    ({'source_pool_id':'missing'}, 'animation_review_source_pi_pool_required'),
    ({'review_model_policy':model(vision=False)}, 'animation_method_review_model_policy_required'),
    ({'review_model_policy':None}, 'animation_method_review_model_policy_required'),
    ({'author_model_policy':{}}, 'animation_method_author_model_policy_required'),
    ({'register_caller':'no'}, 'animation_method_caller_registration_invalid'),
    ({'resource_manifests':{}}, 'animation_method_bundle_manifests_required'),
])
def test_invalid_candidate_authority_is_rejected(changes, reason):
    with pytest.raises(VideoCatalogError, match=reason):
        prepare(**changes)


def test_caller_permissions_and_token_references_cannot_be_borrowed():
    caller = dict(tenant='internal', caller='video-workbench', profiles=list(PROFILES), token_ref='new-token')
    with pytest.raises(VideoCatalogError, match='dedicated_animation_method_caller_required'):
        prepare(caller=caller)
    caller['caller'] = 'new-caller'
    caller['token_ref'] = 'existing-service'
    with pytest.raises(VideoCatalogError, match='dedicated_animation_method_token_required'):
        prepare(caller=caller)
    caller['token_ref'] = 'new-token'
    caller['profiles'] += ['video.scene.v1']
    with pytest.raises(VideoCatalogError, match='dedicated_animation_method_caller_required'):
        prepare(caller=caller)


def test_operator_can_prepare_without_registering_a_caller_and_cannot_replace_route():
    merged, _, summary = prepare(register_caller=False)
    assert merged['callers'] == public_config()['callers']
    assert summary['caller_registered'] is False
    with pytest.raises(VideoCatalogError, match='existing_catalog_identity_conflict'):
        prepare(merged, worker_image=IMAGE.replace('c'*64, 'd'*64))


def test_supplied_manifest_must_match_the_pinned_method_bytes():
    manifests, _ = measure_animation_method_bundle()
    manifests[method.BUNDLE_REVISION]['local_files'][0]['sha256'] = 'a'*64
    with pytest.raises(VideoCatalogError, match='animation_method_bundle_manifest_mismatch'):
        prepare(resource_manifests=manifests)
