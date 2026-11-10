"""Offline additive deployment preparation; synthetic pins grant no readiness."""
from copy import deepcopy
import hashlib

import pytest

from registry_fixtures import make_catalog
from agent_service.catalog import CATALOG_KEYS
from agent_service.contracts import canonical_bytes
from agent_service.deploy.animation_review_catalog import (
    PROFILES, measure_animation_review_bundles, prepare_animation_review_catalog)
from agent_service.deploy.video_catalog import VideoCatalogError, registry
from agent_service.profiles import video_animation_create as creation
from agent_service.profiles import video_animation_review as review
from agent_service.registry import binding_fingerprint


IMAGE = 'registry.internal/animation-pi:latest@sha256:' + 'c' * 64


def model(*, vision=True, model_name='candidate-vision'):
    return dict(revision='model-candidate-v1', api_mode='openai-responses',
                provider='task-proxy', model=model_name,
                capabilities=['text', 'tool_calls'] + (['vision'] if vision else []))


def public_config(*, vision=False):
    catalog = make_catalog('pi', True)
    if vision:
        binding = catalog['execution_bindings'][0]
        binding['model_policy'] = model()
        binding['acceptance']['combination_sha256'] = binding_fingerprint(
            catalog['task_contracts'][0], catalog['capability_bundles'][0],
            catalog['tool_environments'][0], binding)
    catalog.pop('schema_version')
    binding = catalog['execution_bindings'][0]
    return dict(schema_version=2, **catalog, callers=[dict(tenant='internal', caller='existing-service',
        profiles=['workspace-task.v1'], token_ref='existing-service')], workers=[],
        kubernetes_workers=[dict(pool_id='existing-pool', engine='pi',
            binding_id=binding['id'], binding_revision=binding['revision'],
            namespace='agent-workers', image='registry.internal/agent-pi:latest@sha256:' + 'a' * 64,
            host_config_map='existing-host', pod_cidrs=['10.244.0.0/16'], enabled=True,
            port=8090, uid_start=10000000, uid_count=65536, read_only_mounts=True,
            mount_apparmor_profile='agent-capsule-inputs',
            node_selector={'kubernetes.io/hostname': 'operator-node'},
            token_ref='pi-worker-pool', receipt_key_ref='pi-worker-pool')])


def prepare(current=None, **changes):
    manifests, _ = measure_animation_review_bundles(
        include_sequence_review=changes.get('include_sequence_review', False),
        include_batched_review=changes.get('include_batched_review', False))
    settings = dict(worker_image=IMAGE, resource_manifests=manifests,
                    source_pool_id='existing-pool', vision_model_policy=model())
    settings.update(changes)
    return prepare_animation_review_catalog(public_config() if current is None else current, **settings)


def test_local_measurement_verifies_exact_resources_without_claiming_image_or_model_readiness():
    manifests, measured = measure_animation_review_bundles()
    assert set(manifests) == {review.BUNDLE_REVISION, creation.BUNDLE_REVISION}
    assert measured['local_files_verified'] is True
    assert measured['container_measured'] is measured['vision_delivery_verified'] is False
    assert measured['model_requests'] == measured['agent_tasks'] == 0
    for module, bundle in zip((review, creation), measured['bundles'], strict=True):
        manifest = manifests[module.BUNDLE_REVISION]
        assert bundle['manifest_sha256'] in {
            hashlib.sha256(canonical_bytes(manifest) + suffix).hexdigest() for suffix in (b'', b'\n')}
        for row in bundle['local_files']:
            raw = (module.BUNDLE_ROOT / row['path']).read_bytes()
            assert row['sha256'] == hashlib.sha256(raw).hexdigest()
            assert row['bytes'] == len(raw)


def test_preparation_is_pure_additive_and_all_three_roles_are_disabled_candidates():
    current = public_config()
    before = deepcopy(current)
    merged, hosts, summary = prepare(current)
    assert current == before
    for key, value in before.items():
        assert merged[key][:len(value)] == value if key in {*CATALOG_KEYS, 'callers', 'kubernetes_workers'} else merged[key] == value
    assert set(hosts) == set(PROFILES)
    assert len(summary['combinations']) == 3
    assert len(merged['capability_bundles']) == len(before['capability_bundles']) + 2
    assert len(merged['callers']) == len(before['callers']) + 1
    assert merged['callers'][-1]['profiles'] == list(PROFILES)
    assert summary['required_caller_token_ref'] == 'video-animation-creation'
    assert summary['vision_delivery_verified'] is summary['acceptance_created'] is False
    assert summary['secrets_created'] is False and summary['model_requests'] == 0
    for pool in merged['kubernetes_workers'][-3:]:
        assert pool['enabled'] is False and pool['uid_start'] is None
        assert pool['node_selector'] == before['kubernetes_workers'][0]['node_selector']
        assert pool['pod_cidrs'] == before['kubernetes_workers'][0]['pod_cidrs']
    for binding in merged['execution_bindings'][-3:]:
        assert 'acceptance' not in binding
    assert registry(merged).profiles().policy('workspace-task.v1', 'v1').enabled is True


def test_each_host_resolves_independently_for_a_later_real_candidate():
    from agent_service.deploy.acceptance_probe import candidate_config
    merged, hosts, summary = prepare()
    resolved = registry(merged)
    for item in summary['combinations']:
        profile = item['profile']
        host = hosts[profile]
        _, policy, binding = candidate_config(host)
        assert policy.name == profile and policy.enabled is False
        assert binding.acceptance is None
        assert len(host['execution_catalog']['routes']) == 1
        assert policy.execution_binding.combination_sha256 == item['combination_sha256']
        contract = resolved.contract(profile, '1')
        required = {'text', 'tool_calls'} | ({'vision'} if profile != creation.PROFILE else set())
        assert set(contract.required_model_capabilities) == required
        assert set(contract.required_tools) == {'read', 'write'}
        assert set(item['required_model_capabilities']) == required
        assert item['route_enabled'] is item['pool_enabled'] is item['uid_reserved'] is False


def test_replay_is_idempotent_and_new_image_cannot_replace_existing_routes():
    merged, hosts, _ = prepare()
    again, same_hosts, _ = prepare(merged)
    assert again == merged and same_hosts == hosts
    changed = 'registry.internal/animation-pi:latest@sha256:' + 'd' * 64
    other, _, second = prepare(worker_image=changed)
    assert other['routes'][-1] != merged['routes'][-1]
    with pytest.raises(VideoCatalogError, match='existing_catalog_identity_conflict'):
        prepare(merged, worker_image=changed)
    assert len(second['combinations']) == 3


def test_text_only_default_policy_cannot_be_used_for_visual_roles():
    with pytest.raises(VideoCatalogError, match='animation_review_vision_model_policy_required'):
        prepare(vision_model_policy=None)
    merged, _, summary = prepare(public_config(vision=True), vision_model_policy=None)
    assert summary['vision_policy_source']['binding_id'] == 'workspace-pi'
    assert merged['execution_bindings'][-3]['model_policy']['model'] == 'candidate-vision'


def test_explicit_new_model_policies_are_disabled_candidates_and_create_may_use_vision_model():
    explicit = model(model_name='never-accepted-before')
    merged, _, summary = prepare(vision_model_policy=explicit, creator_model_policy=explicit)
    assert summary['vision_policy_source'] == 'explicit'
    for binding in merged['execution_bindings'][-3:]:
        assert binding['model_policy']['model'] == 'never-accepted-before'
        assert 'acceptance' not in binding
    assert all(not row['enabled'] for row in merged['routes'][-3:])


def test_vision_pool_or_binding_selects_model_but_never_expands_inherited_operator_settings():
    current = public_config()
    binding = deepcopy(current['execution_bindings'][0])
    binding['id'] = 'vision-source'
    binding['model_policy'] = model(model_name='inspected-vision')
    binding.pop('acceptance')
    current['execution_bindings'].append(binding)
    pool = deepcopy(current['kubernetes_workers'][0])
    pool.update(pool_id='vision-source-pool', binding_id=binding['id'],
                enabled=False, uid_start=None, node_selector={'kubernetes.io/hostname': 'other-node'},
                pod_cidrs=['10.99.0.0/16'])
    current['kubernetes_workers'].append(pool)
    merged, hosts, _ = prepare(current, vision_model_policy=None, vision_source_pool_id='vision-source-pool')
    assert hosts[review.REVIEW_PROFILE]['model']['model'] == 'inspected-vision'
    assert all(row['node_selector']['kubernetes.io/hostname'] == 'operator-node'
               and row['pod_cidrs'] == ['10.244.0.0/16'] for row in merged['kubernetes_workers'][-3:])
    _, from_binding, _ = prepare(current, vision_model_policy=None,
        vision_source_binding=dict(id=binding['id'], revision=binding['revision']))
    assert from_binding == hosts
    with pytest.raises(VideoCatalogError, match='animation_review_vision_source_ambiguous'):
        prepare(current, vision_source_pool_id='vision-source-pool',
                vision_source_binding=dict(id=binding['id'], revision=binding['revision']))


@pytest.mark.parametrize('changes,code', [
    ({'source_pool_id': None}, 'animation_review_source_pool_required'),
    ({'source_pool_id': 'missing'}, 'animation_review_source_pi_pool_required'),
    ({'vision_source_pool_id': 'missing'}, 'animation_review_source_pi_pool_required'),
    ({'vision_source_binding': {'id': 'missing', 'revision': 'v1'}}, 'animation_review_vision_binding_required'),
    ({'vision_model_policy': model(vision=False)}, 'animation_review_vision_model_policy_required'),
    ({'creator_model_policy': dict(model(), capabilities=['vision'])}, 'animation_review_creator_model_policy_required'),
])
def test_invalid_explicit_sources_and_policies_are_rejected(changes, code):
    with pytest.raises(VideoCatalogError, match=code):
        prepare(**changes)


def test_bundle_pins_and_latest_image_digest_are_required():
    manifests, _ = measure_animation_review_bundles()
    manifests[review.BUNDLE_REVISION]['local_files'][0]['sha256'] = 'e' * 64
    with pytest.raises(VideoCatalogError, match='animation_review_bundle_manifest_mismatch'):
        prepare(resource_manifests=manifests)
    with pytest.raises(VideoCatalogError, match='animation_review_bundle_manifests_required'):
        prepare(resource_manifests={})
    for image in ('registry/animation:latest', 'registry/animation:v2@sha256:' + 'a' * 64):
        with pytest.raises(VideoCatalogError, match='animation_review_worker_latest_digest_required'):
            prepare(worker_image=image)


def test_new_caller_cannot_broaden_an_existing_caller_or_reuse_its_token_reference():
    with pytest.raises(VideoCatalogError, match='dedicated_animation_review_caller_required'):
        prepare(caller=dict(tenant='internal', caller='video-workbench',
                            profiles=list(PROFILES), token_ref='new-token'))
    with pytest.raises(VideoCatalogError, match='dedicated_animation_review_caller_required'):
        prepare(caller=dict(tenant='internal', caller='new-caller',
                            profiles=list(PROFILES) + ['workspace-task.v1'], token_ref='new-token'))
    with pytest.raises(VideoCatalogError, match='existing_catalog_identity_conflict'):
        prepare(caller=dict(tenant='internal', caller='existing-service',
                            profiles=list(PROFILES), token_ref='existing-service'))
    with pytest.raises(VideoCatalogError, match='invalid_public_catalog'):
        prepare(caller=dict(tenant='internal', caller='new-caller',
                            profiles=list(PROFILES), token_ref='existing-service'))


def test_new_combination_cannot_be_silently_enabled_using_source_acceptance():
    merged, _, _ = prepare()
    merged['routes'][-1]['enabled'] = True
    with pytest.raises(VideoCatalogError, match='invalid_public_catalog'):
        registry(merged)


def test_sequence_resources_are_only_measured_when_explicitly_selected():
    original_manifests, original = measure_animation_review_bundles()
    manifests, measured = measure_animation_review_bundles(include_sequence_review=True)
    assert set(manifests) == set(original_manifests) | {review.SEQUENCE_BUNDLE_REVISION}
    assert all(manifests[name] == value for name, value in original_manifests.items())
    assert measured['bundles'][:2] == original['bundles']
    assert measured['local_files_verified'] is True
    assert measured['container_measured'] is measured['vision_delivery_verified'] is False
    assert measured['model_requests'] == measured['agent_tasks'] == 0
    sequence = measured['bundles'][-1]
    raw = (review.SEQUENCE_BUNDLE_ROOT / 'manifest.json').read_bytes()
    assert sequence['bundle_revision'] == review.SEQUENCE_BUNDLE_REVISION
    assert sequence['manifest_sha256'] == review.SEQUENCE_BUNDLE_MANIFEST_SHA256 == hashlib.sha256(raw).hexdigest()
    assert {row['path'] for row in sequence['local_files']} == review.SEQUENCE_BUNDLE_FILES
    for row in sequence['local_files']:
        raw = (review.SEQUENCE_BUNDLE_ROOT / row['path']).read_bytes()
        assert row['sha256'] == hashlib.sha256(raw).hexdigest()
        assert row['bytes'] == len(raw)


def test_explicit_sequence_review_adds_one_disabled_role_and_preserves_existing_combinations():
    from agent_service.deploy.acceptance_probe import candidate_config
    original, original_hosts, original_summary = prepare()
    before = deepcopy(original)
    merged, hosts, summary = prepare(original, include_sequence_review=True)
    assert original == before
    for section, value in before.items():
        assert merged[section][:len(value)] == value if section in {*CATALOG_KEYS, 'callers', 'kubernetes_workers'} else merged[section] == value
    assert set(hosts) == set(original_hosts) | {review.REVIEW_SEQUENCE_PROFILE}
    assert all(hosts[name] == value for name, value in original_hosts.items())
    assert summary['combinations'][:3] == original_summary['combinations']
    assert len(summary['combinations']) == 4
    assert len(merged['capability_bundles']) == len(before['capability_bundles']) + 1
    assert len(merged['execution_bindings']) == len(before['execution_bindings']) + 1
    assert len(merged['routes']) == len(before['routes']) + 1
    assert len(merged['kubernetes_workers']) == len(before['kubernetes_workers']) + 1
    assert merged['callers'][-1]['caller'] == 'video-animation-creation-sequence'
    assert merged['callers'][-1]['profiles'] == [*PROFILES, review.REVIEW_SEQUENCE_PROFILE]
    assert summary['required_caller_token_ref'] == 'video-animation-creation-sequence'
    route, pool = merged['routes'][-1], merged['kubernetes_workers'][-1]
    assert route['enabled'] is pool['enabled'] is False
    assert pool['uid_start'] is None
    assert pool['node_selector'] == before['kubernetes_workers'][0]['node_selector']
    assert pool['pod_cidrs'] == before['kubernetes_workers'][0]['pod_cidrs']
    binding = merged['execution_bindings'][-1]
    assert binding['bundle']['revision'] == review.SEQUENCE_BUNDLE_REVISION
    assert 'acceptance' not in binding
    _, policy, selected_binding = candidate_config(hosts[review.REVIEW_SEQUENCE_PROFILE])
    assert policy.name == review.REVIEW_SEQUENCE_PROFILE and policy.enabled is False
    assert selected_binding.acceptance is None
    contract = registry(merged).contract(review.REVIEW_SEQUENCE_PROFILE, '1')
    assert contract.output_schema == 'video-animation-review-output.v2'
    assert contract.max_input_files == 25
    assert set(contract.required_model_capabilities) == {'text', 'tool_calls', 'vision'}
    assert set(contract.required_tools) == {'read', 'write'}
    assert summary['combinations'][-1]['acceptance_suite'] == 'video.animation-review-contract.v2'
    again, same_hosts, _ = prepare(merged, include_sequence_review=True)
    assert again == merged and same_hosts == hosts


def test_sequence_selection_does_not_allow_missing_or_unselected_bundle_manifests():
    manifests, _ = measure_animation_review_bundles()
    with pytest.raises(VideoCatalogError, match='animation_review_bundle_manifests_required'):
        prepare(include_sequence_review=True, resource_manifests=manifests)
    manifests, _ = measure_animation_review_bundles(include_sequence_review=True)
    with pytest.raises(VideoCatalogError, match='animation_review_bundle_manifests_required'):
        prepare(resource_manifests=manifests)
    manifests[review.SEQUENCE_BUNDLE_REVISION]['local_files'][0]['sha256'] = 'e' * 64
    with pytest.raises(VideoCatalogError, match='animation_review_bundle_manifest_mismatch'):
        prepare(include_sequence_review=True, resource_manifests=manifests)


@pytest.mark.parametrize('selected', [None, 0, 1, 'yes'])
def test_sequence_role_selection_requires_an_explicit_boolean(selected):
    with pytest.raises(VideoCatalogError, match='animation_sequence_review_selection_invalid'):
        measure_animation_review_bundles(include_sequence_review=selected)
    manifests, _ = measure_animation_review_bundles()
    with pytest.raises(VideoCatalogError, match='animation_sequence_review_selection_invalid'):
        prepare_animation_review_catalog(public_config(), worker_image=IMAGE,
            resource_manifests=manifests, source_pool_id='existing-pool',
            vision_model_policy=model(), include_sequence_review=selected)


def test_sequence_selection_cannot_broaden_frozen_original_caller():
    current, _, _ = prepare()
    with pytest.raises(VideoCatalogError, match='existing_catalog_identity_conflict'):
        prepare(current, include_sequence_review=True, caller=dict(tenant='internal',
            caller='video-animation-creation', profiles=[*PROFILES, review.REVIEW_SEQUENCE_PROFILE],
            token_ref='video-animation-creation'))


def test_batch_selection_prepares_only_creation_and_new_review_and_is_idempotent():
    from agent_service.deploy.acceptance_probe import candidate_config
    original = public_config()
    before = deepcopy(original)
    manifests, measured = measure_animation_review_bundles(include_batched_review=True)
    assert set(manifests) == {creation.BUNDLE_REVISION, review.BATCH_BUNDLE_REVISION}
    assert measured['vision_delivery_verified'] is False
    assert measured['model_requests'] == measured['agent_tasks'] == 0
    merged, hosts, summary = prepare(original, include_batched_review=True)
    assert original == before
    assert set(hosts) == {creation.PROFILE, review.REVIEW_BATCH_PROFILE}
    assert [row['profile'] for row in summary['combinations']] == [creation.PROFILE, review.REVIEW_BATCH_PROFILE]
    assert len(merged['execution_bindings']) == len(before['execution_bindings']) + 2
    assert not {review.OBSERVE_PROFILE, review.REVIEW_PROFILE, review.REVIEW_SEQUENCE_PROFILE} & set(hosts)
    assert merged['callers'][-1]['profiles'] == [creation.PROFILE, review.REVIEW_BATCH_PROFILE]
    assert summary['required_caller_token_ref'] == 'video-animation-creation-batch'
    contract = registry(merged).contract(review.REVIEW_BATCH_PROFILE, '1')
    assert contract.output_schema == 'video-animation-review-output.v3'
    assert contract.max_input_files == 25 and contract.max_timeout_seconds == 1200
    _, policy, binding = candidate_config(hosts[review.REVIEW_BATCH_PROFILE])
    assert policy.name == review.REVIEW_BATCH_PROFILE and policy.enabled is False
    assert binding.bundle.revision == review.BATCH_BUNDLE_REVISION and binding.acceptance is None
    assert summary['combinations'][-1]['acceptance_suite'] == 'video.animation-review-contract.v3'
    again, same_hosts, same_summary = prepare(merged, include_batched_review=True)
    assert again == merged and same_hosts == hosts
    assert same_summary['combinations'] == summary['combinations']


def test_batch_role_has_a_new_combination_without_changing_existing_sequence_history():
    existing, old_hosts, _ = prepare(include_sequence_review=True)
    # The new source/image identity applies to the new candidate creation too;
    # existing creation cannot be silently replaced by this additive helper.
    merged, hosts, _ = prepare(existing, include_batched_review=True)
    assert hosts[creation.PROFILE] == old_hosts[creation.PROFILE]
    for section, rows in existing.items():
        assert merged[section][:len(rows)] == rows if section in {*CATALOG_KEYS, 'callers', 'kubernetes_workers'} else merged[section] == rows
    assert hosts[review.REVIEW_BATCH_PROFILE]['binding_id'] != old_hosts[review.REVIEW_SEQUENCE_PROFILE]['binding_id']


def test_batch_new_worker_can_reuse_the_existing_unchanged_creation_image_and_combination():
    existing, old_hosts, old_summary = prepare(include_sequence_review=True)
    new_image = 'registry.internal/animation-pi:latest@sha256:' + 'd' * 64
    merged, hosts, summary = prepare(existing, include_batched_review=True,
        worker_image=new_image, creator_worker_image=IMAGE)
    assert hosts[creation.PROFILE] == old_hosts[creation.PROFILE]
    assert hosts[creation.PROFILE]['profile']['image_digest'] == IMAGE.rsplit('@', 1)[1]
    assert hosts[review.REVIEW_BATCH_PROFILE]['profile']['image_digest'] == new_image.rsplit('@', 1)[1]
    assert len(merged['execution_bindings']) == len(existing['execution_bindings']) + 1
    old_creation = next(row for row in old_summary['combinations'] if row['profile'] == creation.PROFILE)
    assert summary['combinations'][0] == old_creation
    with pytest.raises(VideoCatalogError, match='animation_review_creator_worker_latest_digest_required'):
        prepare(include_batched_review=True, creator_worker_image='registry.internal/pi:latest')


def test_review_only_preserves_enabled_accepted_creator_and_leaves_caller_updates_to_operator():
    from agent_service.registry import binding_fingerprint
    current, _, _ = prepare(include_sequence_review=True)
    binding = next(row for row in current['execution_bindings'] if row['profile'] == creation.PROFILE)
    resolved = registry(current)
    contract = resolved.contract(creation.PROFILE, '1')
    bundle = resolved.bundle(binding['bundle']['name'], binding['bundle']['revision'])
    environment = resolved.environment(binding['tool_environment']['name'], binding['tool_environment']['revision'])
    binding['acceptance'] = dict(combination_sha256=binding_fingerprint(contract, bundle, environment, binding),
        suite='video.animation-create-contract.v2', report_sha256='e' * 64)
    next(row for row in current['routes'] if row['profile'] == creation.PROFILE)['enabled'] = True
    creator_pool = next(row for row in current['kubernetes_workers'] if row['binding_id'] == binding['id'])
    creator_pool.update(enabled=True, uid_start=130000000)
    before = deepcopy(current)
    caller = dict(tenant='internal', caller='video-animation-creation-sequence',
        profiles=[creation.PROFILE, review.REVIEW_BATCH_PROFILE], token_ref='video-animation-creation-sequence')
    new_image = 'registry.internal/animation-pi:latest@sha256:' + 'd' * 64
    merged, hosts, summary = prepare(current, include_batched_review=True, review_only=True,
        register_caller=False, worker_image=new_image, creator_worker_image=IMAGE,
        creator_model_policy=binding['model_policy'], caller=caller)
    assert current == before
    assert set(hosts) == {review.REVIEW_BATCH_PROFILE}
    assert [row['profile'] for row in summary['combinations']] == [review.REVIEW_BATCH_PROFILE]
    assert summary['existing_creator_reused'] is True and summary['caller_registered'] is False
    assert summary['required_caller_token_ref'] == caller['token_ref']
    assert merged['callers'] == before['callers']
    assert len(merged['execution_bindings']) == len(before['execution_bindings']) + 1
    for section, rows in before.items():
        assert merged[section][:len(rows)] == rows if section in {*CATALOG_KEYS, 'callers', 'kubernetes_workers'} else merged[section] == rows
    with pytest.raises(VideoCatalogError, match='existing_catalog_identity_conflict'):
        prepare(current, include_batched_review=True, review_only=True, caller=caller)
    with pytest.raises(VideoCatalogError, match='animation_review_existing_creator_image_mismatch'):
        prepare(current, include_batched_review=True, review_only=True, creator_worker_image=new_image)
    with pytest.raises(VideoCatalogError, match='animation_review_existing_creator_model_mismatch'):
        prepare(current, include_batched_review=True, review_only=True,
            creator_model_policy=dict(binding['model_policy'], model='changed-creator-model'))


@pytest.mark.parametrize('changes,code', [
    ({'review_only': 'yes'}, 'animation_review_only_selection_invalid'),
    ({'review_only': True}, 'animation_review_only_selection_invalid'),
    ({'register_caller': None}, 'animation_review_caller_registration_invalid'),
    ({'include_batched_review': True, 'review_only': True}, 'animation_review_existing_creator_required'),
])
def test_review_only_preparation_rejects_implicit_modes_and_missing_creator(changes, code):
    with pytest.raises(VideoCatalogError, match=code):
        prepare(**changes)


@pytest.mark.parametrize('selected', [None, 0, 1, 'yes'])
def test_batch_role_selection_requires_an_explicit_boolean(selected):
    with pytest.raises(VideoCatalogError, match='animation_batch_review_selection_invalid'):
        measure_animation_review_bundles(include_batched_review=selected)


def test_batch_selection_rejects_mixed_modes_and_wrong_resource_manifests():
    with pytest.raises(VideoCatalogError, match='animation_review_selection_ambiguous'):
        measure_animation_review_bundles(include_sequence_review=True, include_batched_review=True)
    manifests, _ = measure_animation_review_bundles(include_sequence_review=True)
    with pytest.raises(VideoCatalogError, match='animation_review_bundle_manifests_required'):
        prepare(include_batched_review=True, resource_manifests=manifests)
    manifests, _ = measure_animation_review_bundles(include_batched_review=True)
    manifests[review.BATCH_BUNDLE_REVISION]['local_files'][0]['sha256'] = 'e' * 64
    with pytest.raises(VideoCatalogError, match='animation_review_bundle_manifest_mismatch'):
        prepare(include_batched_review=True, resource_manifests=manifests)
