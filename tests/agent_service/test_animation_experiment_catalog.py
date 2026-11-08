"""Additive disabled deployment preparation; all authority below is synthetic test data."""
from copy import deepcopy
import json

import pytest

from registry_fixtures import make_catalog
from agent_service.catalog import CATALOG_KEYS
from agent_service.deploy.animation_experiment_catalog import prepare_animation_catalog
from agent_service.deploy.video_catalog import VideoCatalogError, registry
from agent_service.profiles.video_animation_experiment import BUNDLE_ROOT, PROFILE


def public_config():
    # The existing accepted synthetic workspace combination must remain exact.
    catalog = make_catalog('pi', True)
    catalog.pop('schema_version', None)
    binding = catalog['execution_bindings'][0]
    image = 'registry.internal/agent-pi:latest@' + catalog['tool_environments'][0]['image_digest']
    return dict(schema_version=2, **catalog, callers=[dict(tenant='internal', caller='existing-service',
        profiles=['workspace-task.v1'], token_ref='existing-service')], workers=[],
        kubernetes_workers=[dict(pool_id='existing-pool', engine='pi',
            binding_id=binding['id'], binding_revision=binding['revision'],
            namespace='agent-workers', image=image, host_config_map='existing-host',
            pod_cidrs=['10.244.0.0/16'], enabled=True, port=8090, uid_start=10000000,
            uid_count=65536, read_only_mounts=True, mount_apparmor_profile='agent-capsule-inputs',
            node_selector={'kubernetes.io/hostname': 'worker-node'},
            token_ref='pi-worker-pool', receipt_key_ref='pi-worker-pool')])


def prepare(current=None, **changes):
    settings = dict(worker_image='registry.internal/animation-pi:latest@sha256:' + 'c' * 64,
        resource_manifest=json.loads((BUNDLE_ROOT / 'manifest.json').read_bytes()),
        source_pool_id='existing-pool')
    settings.update(changes)
    return prepare_animation_catalog(current or public_config(), **settings)


def test_preparation_is_pure_additive_disabled_and_independently_resolvable():
    current = public_config()
    before = deepcopy(current)
    merged, host, summary = prepare(current)
    assert current == before
    for key, value in before.items():
        assert merged[key][:len(value)] == value if key in {*CATALOG_KEYS, 'callers', 'kubernetes_workers'} else merged[key] == value
    assert merged['routes'][-1]['enabled'] is False
    assert merged['kubernetes_workers'][-1]['enabled'] is False
    assert merged['kubernetes_workers'][-1]['uid_start'] is None
    assert merged['kubernetes_workers'][-1]['node_selector'] == before['kubernetes_workers'][0]['node_selector']
    assert 'acceptance' not in merged['execution_bindings'][-1]
    assert merged['callers'][-1]['profiles'] == [PROFILE]
    assert summary['acceptance_created'] is False and summary['secrets_created'] is False
    assert summary['model_requests'] == 0
    assert summary['required_caller_token_ref'] == 'video-animation-lab'
    assert summary['acceptance_suite'] == 'video.animation-experiment-contract.v1'
    assert host['profile']['enabled'] is False
    assert len(host['execution_catalog']['routes']) == 1
    from agent_service.deploy.acceptance_probe import candidate_config
    parsed, policy, binding = candidate_config(host)
    assert policy.name == PROFILE and binding.acceptance is None
    assert policy.execution_binding.combination_sha256 == summary['combination_sha256']
    assert registry(merged).profiles().policy('workspace-task.v1', 'v1').enabled


def test_same_preparation_can_be_replayed_without_duplicate_records():
    merged, host, _ = prepare()
    again, second_host, _ = prepare(merged)
    assert again == merged and second_host == host


def test_different_worker_image_requires_a_different_combination_and_cannot_replace_route():
    first, _, summary = prepare()
    second, _, other = prepare(worker_image='registry.internal/animation-pi:latest@sha256:' + 'd' * 64)
    assert summary['combination_sha256'] != other['combination_sha256']
    assert first['routes'][-1] != second['routes'][-1]
    with pytest.raises(VideoCatalogError, match='existing_catalog_identity_conflict'):
        prepare(first, worker_image='registry.internal/animation-pi:latest@sha256:' + 'd' * 64)


@pytest.mark.parametrize('bad_image', ['registry.internal/animation:1.2',
    'registry.internal/animation:latest', 'registry.internal/animation:v1@sha256:' + 'a' * 64])
def test_prep_requires_latest_and_an_immutable_digest(bad_image):
    with pytest.raises(VideoCatalogError, match='animation_worker_latest_digest_required'):
        prepare(worker_image=bad_image)


def test_new_bundle_cannot_borrow_motion_resources_or_unpinned_content():
    manifest = json.loads((BUNDLE_ROOT / 'manifest.json').read_bytes())
    manifest['local_files'][0]['sha256'] = 'e' * 64
    with pytest.raises(VideoCatalogError, match='animation_bundle_manifest_mismatch'):
        prepare(resource_manifest=manifest)


def test_lab_caller_cannot_widen_production_caller_or_reuse_an_existing_credential_ref():
    with pytest.raises(VideoCatalogError, match='dedicated_animation_caller_required'):
        prepare(caller=dict(tenant='internal', caller='video-workbench', profiles=[PROFILE], token_ref='video-workbench'))
    with pytest.raises(VideoCatalogError):
        prepare(caller=dict(tenant='internal', caller='lab', profiles=[PROFILE], token_ref='existing-service'))


def test_candidate_cannot_be_silently_enabled_without_own_acceptance():
    merged, _, _ = prepare()
    merged['routes'][-1]['enabled'] = True
    with pytest.raises(VideoCatalogError, match='invalid_public_catalog'):
        registry(merged)
