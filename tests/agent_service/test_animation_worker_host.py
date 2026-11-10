"""Selected-host projection is offline configuration, never live acceptance."""
from copy import deepcopy

import pytest

from registry_fixtures import make_catalog
from agent_service.contracts import digest
from agent_service.deploy.animation_review_catalog import (
    measure_animation_review_bundles, prepare_animation_review_catalog)
from agent_service.deploy.animation_worker_host import (
    AnimationWorkerHostError, project_animation_worker_host)
from agent_service.registry import DeploymentRegistry, binding_fingerprint
from agent_service.worker_entry import CATALOG_KEYS, HostConfig, host_policy


IMAGE = 'registry.internal/animation-pi:latest@sha256:' + 'c' * 64
PROFILES = ('video.animation-create.v2', 'video.animation-review.v2', 'video.animation-review.v3')


@pytest.fixture(scope='module')
def full_hosts():
    base = make_catalog('pi', True)
    base.pop('schema_version')
    source = base['execution_bindings'][0]
    current = dict(schema_version=2, **base, callers=[dict(tenant='internal',
        caller='old-service', profiles=['workspace-task.v1'], token_ref='old-service')], workers=[],
        kubernetes_workers=[dict(pool_id='source-pool', engine='pi', binding_id=source['id'],
            binding_revision=source['revision'], namespace='agent-workers',
            image='registry.internal/agent-pi:latest@sha256:' + 'a' * 64,
            host_config_map='source-host', pod_cidrs=['10.244.0.0/16'], enabled=True,
            port=8090, uid_start=10000000, uid_count=65536, read_only_mounts=True,
            mount_apparmor_profile='agent-capsule-inputs', node_selector={'kubernetes.io/hostname':'node'},
            token_ref='source-token', receipt_key_ref='source-receipt')])
    manifests, _ = measure_animation_review_bundles(include_sequence_review=True)
    merged, hosts, _ = prepare_animation_review_catalog(current,
        worker_image=IMAGE, resource_manifests=manifests, source_pool_id='source-pool',
        include_sequence_review=True, vision_model_policy=dict(revision='vision-v1',
            api_mode='openai-responses', provider='task-proxy', model='vision-model',
            capabilities=['text','tool_calls','vision']))
    batch_manifests, _ = measure_animation_review_bundles(include_batched_review=True)
    merged, batch_hosts, _ = prepare_animation_review_catalog(merged,
        worker_image=IMAGE, resource_manifests=batch_manifests, source_pool_id='source-pool',
        include_batched_review=True, vision_model_policy=dict(revision='vision-v1',
            api_mode='openai-responses', provider='task-proxy', model='vision-model',
            capabilities=['text','tool_calls','vision']))
    hosts.update(batch_hosts)
    for profile in PROFILES:
        binding = next(row for row in merged['execution_bindings'] if row['profile'] == profile)
        contract = next(row for row in merged['task_contracts'] if row['name'] == profile)
        bundle = next(row for row in merged['capability_bundles']
            if (row['name'],row['revision']) == (binding['bundle']['name'],binding['bundle']['revision']))
        environment = next(row for row in merged['tool_environments']
            if (row['name'],row['revision']) == (binding['tool_environment']['name'],binding['tool_environment']['revision']))
        binding['acceptance'] = dict(combination_sha256=binding_fingerprint(contract,bundle,environment,binding),
            suite=profile.rsplit('.v',1)[0] + '-contract.v' + profile.rsplit('.v',1)[1], report_sha256='d'*64)
        next(row for row in merged['routes'] if row['profile'] == profile)['enabled'] = True
        hosts[profile]['profile']['enabled'] = True
    return {profile:dict(deepcopy(hosts[profile]),
                        execution_catalog={key:deepcopy(merged[key]) for key in CATALOG_KEYS})
            for profile in PROFILES}


def selected(host, section):
    return project_animation_worker_host(host)['execution_catalog'][section][0]


@pytest.mark.parametrize('profile', PROFILES)
def test_projects_exact_rows_without_changing_selected_pins_or_authority(full_hosts, profile):
    host = deepcopy(full_hosts[profile]); before = deepcopy(host)
    original = host_policy(HostConfig.model_validate(host))
    projected = project_animation_worker_host(host)
    actual = host_policy(HostConfig.model_validate(projected))
    assert host == before
    assert {key:value for key,value in projected.items() if key != 'execution_catalog'} == {
        key:value for key,value in host.items() if key != 'execution_catalog'}
    assert all(len(rows) == 1 for rows in projected['execution_catalog'].values())
    for key,rows in projected['execution_catalog'].items():
        assert rows[0] in host['execution_catalog'][key]
    assert actual.pins() == original.pins()
    assert actual.execution_binding == original.execution_binding
    assert actual.max_timeout_seconds == original.max_timeout_seconds
    assert actual.supports_inputs == original.supports_inputs
    approval = projected['execution_catalog']['execution_bindings'][0]['acceptance']
    assert actual.execution_binding.acceptance_sha256 == digest(approval)
    assert actual.enabled is original.enabled is True
    projected['model']['model'] = 'changed-copy'
    projected['execution_catalog']['capability_bundles'][0]['resources'][0]['sha256'] = 'e'*64
    assert host == before


@pytest.mark.parametrize('profile', PROFILES)
def test_unsupported_unrelated_handler_is_removed_before_registry_validation(full_hosts, profile):
    host = deepcopy(full_hosts[profile])
    unknown = deepcopy(host['execution_catalog']['task_contracts'][0])
    unknown['name'] = 'future.unavailable-domain.v99'
    host['execution_catalog']['task_contracts'].append(unknown)
    before = deepcopy(host)
    with pytest.raises(ValueError):
        DeploymentRegistry.from_config(**host['execution_catalog'])
    projected = project_animation_worker_host(host)
    assert host_policy(HostConfig.model_validate(projected)).name == profile
    assert host == before


@pytest.mark.parametrize('section', tuple(CATALOG_KEYS))
def test_duplicate_selected_reference_is_rejected(full_hosts, section):
    host = deepcopy(full_hosts[PROFILES[0]])
    host['execution_catalog'][section].append(deepcopy(selected(host,section)))
    with pytest.raises(AnimationWorkerHostError, match='reference_not_unique'):
        project_animation_worker_host(host)


@pytest.mark.parametrize('section', tuple(CATALOG_KEYS))
def test_missing_selected_reference_is_rejected(full_hosts, section):
    host = deepcopy(full_hosts[PROFILES[0]])
    record = selected(host,section)
    host['execution_catalog'][section].remove(record)
    with pytest.raises(AnimationWorkerHostError, match='reference_not_unique'):
        project_animation_worker_host(host)


def test_conflicting_second_route_for_same_profile_cannot_hide_behind_selector(full_hosts):
    host = deepcopy(full_hosts[PROFILES[0]])
    route = deepcopy(selected(host,'routes'));route['binding_id'] = 'other-binding'
    host['execution_catalog']['routes'].append(route)
    with pytest.raises(AnimationWorkerHostError, match='reference_not_unique'):
        project_animation_worker_host(host)


@pytest.mark.parametrize('change', ['route_binding','profile','image','runtime','model','approval','quota','bundle_ref','environment_ref'])
def test_target_mismatch_or_widening_is_rejected(full_hosts, change):
    host = deepcopy(full_hosts[PROFILES[0]])
    catalog = host['execution_catalog']
    binding = next(row for row in catalog['execution_bindings'] if row['id'] == host['binding_id'])
    route = next(row for row in catalog['routes'] if row['profile'] == PROFILES[0])
    contract = next(row for row in catalog['task_contracts'] if row['name'] == PROFILES[0])
    if change == 'route_binding': route['binding_id'] = 'other-binding'
    elif change == 'profile': host['profile']['name'] = PROFILES[1]
    elif change == 'image': host['profile']['image_digest'] = 'sha256:'+'e'*64
    elif change == 'runtime': host['profile']['runtime_revision'] = 'other-runtime'
    elif change == 'model': host['model']['model'] = 'other-model'
    elif change == 'approval': binding['acceptance']['combination_sha256'] = 'e'*64
    elif change == 'quota': contract['max_input_bytes'] = 64*1024*1024
    elif change == 'bundle_ref': binding['bundle']['revision'] = 'other-bundle'
    elif change == 'environment_ref': binding['tool_environment']['revision'] = 'other-tools'
    with pytest.raises(AnimationWorkerHostError):
        project_animation_worker_host(host)


@pytest.mark.parametrize('change', ['no_selector','bad_selector','catalog_keys','catalog_rows','model_missing','unknown_profile','extra_host_field'])
def test_malformed_target_host_is_rejected(full_hosts, change):
    host = deepcopy(full_hosts[PROFILES[0]])
    if change == 'no_selector': host.pop('binding_id')
    elif change == 'bad_selector': host['binding_revision'] = None
    elif change == 'catalog_keys': host['execution_catalog']['unknown'] = []
    elif change == 'catalog_rows': host['execution_catalog']['task_contracts'].append(None)
    elif change == 'model_missing': host['model'] = None
    elif change == 'extra_host_field': host['uid_start'] = 12345678
    elif change == 'unknown_profile':
        next(row for row in host['execution_catalog']['execution_bindings']
             if row['id'] == host['binding_id'])['profile'] = 'future.creation.v1'
    with pytest.raises(AnimationWorkerHostError):
        project_animation_worker_host(host)
