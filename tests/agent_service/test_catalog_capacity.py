"""Bounded shared-catalog growth and exact offline publication preflight."""
from copy import deepcopy
import json

import pytest

from agent_service.catalog_limits import (
    CATALOG_KEYS, CATALOG_READER_MAX_BYTES, CONFIGMAP_MAX_BYTES, HISTORY_RECORD_LIMIT,
    PUBLISH_CATALOG_MAX_BYTES, wire_bytes,
)
from agent_service.config import _Public
from agent_service.deploy.catalog_capacity import (
    CapacityError, REQUIRED_CONSUMERS, _configmap, main, preflight, render_capacity,
)
from agent_service.registry import (
    CapabilityBundle, DeploymentRegistry, ExecutionBinding, Route, TaskContract, ToolEnvironment,
)
from agent_service.deploy.render import RenderError, render
from agent_service.worker_entry import HostConfig, host_policy
from registry_fixtures import make_catalog

IMAGE = 'registry.invalid/agent-worker@sha256:' + 'a' * 64


def dense_public(count):
    """Valid pinned history; no synthetic acceptance or enabled pool exists."""
    value = make_catalog()
    original = deepcopy(value['execution_bindings'][0])
    for index in range(1, count):
        value['execution_bindings'].append({**deepcopy(original), 'revision': f'history-{index:03d}'})
    value.update(callers=[dict(tenant='internal', caller='fixture',
        profiles=['workspace-task.v1'], token_ref='fixture')], workers=[],
        kubernetes_workers=[dict(pool_id=f'fixture-{i:03d}', engine='pi', namespace='agent-workers',
            binding_id=row['id'], binding_revision=row['revision'], image=IMAGE,
            host_config_map=f'fixture-host-{i:03d}', pod_cidrs=['10.244.0.0/16'],
            enabled=False, uid_start=1000000 + i * 2, uid_count=2,
            token_ref='pi-worker-pool', receipt_key_ref='pi-worker-pool')
            for i, row in enumerate(value['execution_bindings'])])
    return value


def registry(public):
    return DeploymentRegistry.from_config(**{key: public[key] for key in CATALOG_KEYS})


def host(public, pool):
    return dict(schema_version=1, engine='pi', binding_id=pool['binding_id'],
        binding_revision=pool['binding_revision'], profile=dict(name='workspace-task.v1', revision='v1',
            runtime_revision='runtime-v1', image_digest=IMAGE.rsplit('@', 1)[1],
            enabled=False, max_timeout_seconds=600),
        execution_catalog={key: deepcopy(public[key]) for key in CATALOG_KEYS}, model=None)


def hosts(current, candidate):
    existing = {row['host_config_map'] for row in current['kubernetes_workers']}
    return {row['host_config_map']: {'config.json': wire_bytes(host(candidate, row)).decode('utf-8')}
            for row in candidate['kubernetes_workers'] if row['host_config_map'] not in existing}


def support(maximum=256):
    return {'schema': 'agent-catalog-consumer-compatibility.v1', 'consumers': [
        dict(role=role, image=IMAGE, history_record_limit=maximum, evidence_sha256='e' * 64)
        for role in REQUIRED_CONSUMERS]}


def test_95_then_two_batches_cross_100_without_rewriting_pins_or_old_host():
    old, first, second = (dense_public(n) for n in (95, 100, 105))
    old_copy = deepcopy(old)
    old_host = host(old, old['kubernetes_workers'][-1])
    old_pins = host_policy(HostConfig.model_validate(old_host)).pins()
    assert preflight(old, first, host_data=hosts(old, first))['status'] == 'ready'
    blocked = preflight(first, second, host_data=hosts(first, second))
    assert blocked['status'] == 'blocked'
    assert 'consumer_catalog_capacity_exceeded' in blocked['reasons']
    assert blocked['consumer_support']['effective_history_limit'] == 100
    ready = preflight(first, second, host_data=hosts(first, second), compatibility=support())
    assert ready['status'] == 'ready'
    assert ready['consumer_support']['transition'] == 'expanded_256'
    assert ready['sections']['execution_bindings']['candidate'] == 105
    assert ready['count_only_ppt_releases_remaining'] == 30
    assert ready['future_release_bytes_predicted'] is False
    assert registry(second).binding_policy(old_host['binding_id'], old_host['binding_revision']).pins() == old_pins
    assert host_policy(HostConfig.model_validate(old_host)).pins() == old_pins
    assert old == old_copy
    assert old_host['execution_catalog']['execution_bindings'] == old['execution_bindings']
    assert not ready['historical_pruning_performed'] and not ready['uid_ledger_accessed']
    assert not ready['publication_authorized'] and not ready['acceptance_created']


@pytest.mark.parametrize('missing', REQUIRED_CONSUMERS)
def test_one_unverified_consumer_keeps_effective_legacy_limit(missing):
    current, candidate = dense_public(95), dense_public(105)
    compatibility = support()
    compatibility['consumers'] = [row for row in compatibility['consumers'] if row['role'] != missing]
    report = preflight(current, candidate, compatibility=compatibility, host_data=hosts(current, candidate))
    assert report['status'] == 'blocked'
    assert report['consumer_support']['missing_roles'] == [missing]
    assert report['consumer_support']['effective_history_limit'] == 100


def test_one_proven_legacy_control_or_wrong_worker_image_still_blocks_105():
    current, candidate = dense_public(95), dense_public(105)
    compatibility = support()
    compatibility['consumers'][0]['history_record_limit'] = 100
    assert preflight(current, candidate, compatibility=compatibility,
        host_data=hosts(current, candidate))['status'] == 'blocked'
    compatibility = support()
    compatibility['consumers'][-1]['image'] = 'registry.invalid/other@sha256:' + 'b' * 64
    report = preflight(current, candidate, compatibility=compatibility, host_data=hosts(current, candidate))
    assert 'candidate_worker_compatibility_image_mismatch' in report['reasons']


def test_history_expansion_remains_fixed_at_256_and_schemas_stay_strict():
    maximum = dense_public(HISTORY_RECORD_LIMIT)
    _Public.model_validate(maximum)
    assert registry(maximum).summary()['execution_bindings'] == 256
    oversized = dense_public(HISTORY_RECORD_LIMIT + 1)
    with pytest.raises(ValueError):
        _Public.model_validate(oversized)
    with pytest.raises(ValueError):
        registry(oversized)
    invalid = deepcopy(maximum)
    invalid['task_contracts'][0]['output_schema'] = 'unreviewed-output.v1'
    with pytest.raises(ValueError):
        registry(invalid)


def test_catalog_reader_keeps_accepting_previously_validated_typed_records():
    public = dense_public(1)
    classes = dict(task_contracts=TaskContract, capability_bundles=CapabilityBundle,
        tool_environments=ToolEnvironment, execution_bindings=ExecutionBinding, routes=Route)
    typed = {key: tuple(classes[key].model_validate(row) for row in public[key]) for key in CATALOG_KEYS}
    assert DeploymentRegistry.from_config(**typed).summary() == registry(public).summary()


@pytest.mark.parametrize('section', ['execution_bindings', 'tool_environments', 'capability_bundles', 'kubernetes_workers'])
def test_no_history_pruning_or_rewriting_to_reclaim_capacity(section):
    current, candidate = dense_public(95), dense_public(100)
    if section == 'execution_bindings':
        candidate[section][1]['model_policy']['model'] = 'changed-historical-model'
    elif section == 'tool_environments':
        candidate[section][0]['runtime_revision'] = 'changed-history'
    elif section == 'capability_bundles':
        candidate[section][0]['source'] = 'changed-historical-package'
    else:
        candidate[section][0]['uid_start'] += 10000
    report = preflight(current, candidate, host_data=hosts(current, candidate))
    assert report['status'] == 'blocked' and 'historical_record_changed' in report['reasons']
    removed = dense_public(100)
    removed['kubernetes_workers'] = removed['kubernetes_workers'][1:]
    report = preflight(current, removed, host_data=hosts(current, removed))
    assert 'historical_record_removed' in report['reasons']


def test_non_ppt_route_and_caller_revocation_are_preserved():
    current, candidate = dense_public(95), dense_public(100)
    current['callers'][0]['profiles'] = []
    candidate['callers'][0]['profiles'] = []
    candidate['routes'][0]['binding_revision'] = 'history-001'
    report = preflight(current, candidate, host_data=hosts(current, candidate))
    assert 'non_ppt_route_changed' in report['reasons']
    candidate['routes'] = deepcopy(current['routes'])
    candidate['callers'][0]['profiles'] = ['workspace-task.v1']
    report = preflight(current, candidate, host_data=hosts(current, candidate))
    assert 'existing_caller_scope_changed' in report['reasons']


def test_catalog_publication_does_not_change_shared_model_or_execution_slots():
    current, candidate = dense_public(95), dense_public(100)
    current['dispatcher'] = dict(execution_slots=5)
    candidate['dispatcher'] = dict(execution_slots=1)
    report = preflight(current, candidate, host_data=hosts(current, candidate))
    assert 'shared_public_settings_changed' in report['reasons']


def test_measures_complete_public_wrapper_and_new_host_acceptance_payload():
    current, candidate = dense_public(95), dense_public(100)
    data = hosts(current, candidate)
    report = preflight(current, candidate, host_data=data)
    public = report['configmaps'][0]
    expected = _configmap(public['name'], 'agent-service', {'config.json': wire_bytes(candidate).decode('utf-8')})
    assert public['json_bytes_including_wrapper'] == len(wire_bytes(expected))
    assert public['data_utf8_bytes'] == len(wire_bytes(candidate))
    assert public['json_bytes_including_wrapper'] > public['data_utf8_bytes']
    name = next(iter(data))
    data[name]['acceptance-evidence.json'] = json.dumps({'synthetic_oversize_only': 'x' * CONFIGMAP_MAX_BYTES})
    blocked = preflight(current, candidate, host_data=data)
    assert blocked['status'] == 'blocked' and 'configmap_wire_capacity_exceeded' in blocked['reasons']


def test_missing_or_subset_new_host_catalog_cannot_bypass_full_consumer_measurement():
    current, candidate = dense_public(95), dense_public(100)
    assert 'new_host_configmap_measurement_missing' in preflight(current, candidate)['reasons']
    data = hosts(current, candidate)
    name = next(iter(data))
    value = json.loads(data[name]['config.json'])
    own_revision = value['binding_revision']
    value['execution_catalog']['execution_bindings'] = [row for row in candidate['execution_bindings']
        if row['revision'] == own_revision or row['revision'] == 'binding-v1']
    data[name]['config.json'] = wire_bytes(value).decode('utf-8')
    assert 'new_host_complete_catalog_required' in preflight(current, candidate, host_data=data)['reasons']


def test_new_publication_byte_budget_does_not_tighten_immutable_host_reader():
    current, candidate = dense_public(1), dense_public(1)
    bundle = deepcopy(candidate['capability_bundles'][0])
    bundle.update(name='large-retained-fixture', revision='large-v1', resources=[
        {'name': f'{i:04d}/' + '/'.join(['x' * 100] * 4), 'sha256': 'a' * 64} for i in range(2048)])
    candidate['capability_bundles'].append(bundle)
    assert len(wire_bytes({key: candidate[key] for key in CATALOG_KEYS})) > PUBLISH_CATALOG_MAX_BYTES
    report = preflight(current, candidate)
    assert 'new_catalog_byte_budget_exceeded' in report['reasons']
    # Historical host snapshots keep the former 4MiB reader allowance.
    assert host_policy(HostConfig.model_validate(host(candidate, candidate['kubernetes_workers'][0]))).name == 'workspace-task.v1'


def test_renderer_also_defaults_to_legacy_and_measures_supplied_old_immutable_maps():
    public = dense_public(105)
    assert render_capacity(public, [])['status'] == 'blocked'
    report = render_capacity(public, [], compatibility=support())
    assert report['status'] == 'ready'
    oversized = _configmap('historical-host', 'agent-workers', {'config.json': '{}',
        'acceptance-evidence.json': 'x' * CONFIGMAP_MAX_BYTES})
    assert 'configmap_wire_capacity_exceeded' in render_capacity(public, [oversized], compatibility=support())['reasons']


def test_renderer_proof_must_cover_the_image_that_reads_each_expanded_host_catalog():
    public = dense_public(105)
    environment = {**deepcopy(public['tool_environments'][0]), 'name': 'different-image-tools',
                   'image_digest': 'sha256:' + 'b' * 64}
    public['tool_environments'].append(environment)
    public['execution_bindings'][-1]['tool_environment']['name'] = environment['name']
    pool = public['kubernetes_workers'][-1]
    pool['image'] = IMAGE.replace('a' * 64, 'b' * 64)
    value = host(public, pool)
    value['profile']['image_digest'] = environment['image_digest']
    host_policy(HostConfig.model_validate(value))
    configmap = _configmap(pool['host_config_map'], 'agent-workers', {'config.json': wire_bytes(value).decode('utf-8')})
    # The evidence image is present in 104 historical/disabled pools, but does
    # not cover the image that will actually parse this 105-binding host.
    report = render_capacity(public, [configmap], compatibility=support())
    assert report['status'] == 'blocked' and 'expanded_host_consumer_unverified' in report['reasons']
    proof = support()
    proof['consumers'][-1]['image'] = pool['image']
    assert render_capacity(public, [configmap], compatibility=proof)['status'] == 'ready'
    old = dense_public(95)
    old_pool = old['kubernetes_workers'][0]
    old_map = _configmap(old_pool['host_config_map'], 'agent-workers',
        {'config.json': wire_bytes(host(old, old_pool)).decode('utf-8')})
    assert render_capacity(public, [old_map, configmap], compatibility=proof)['status'] == 'ready'


def test_registry_retains_a_hard_four_mib_catalog_reader_bound():
    public = dense_public(1)
    template = deepcopy(public['capability_bundles'][0])
    template['resources'] = [{'name': f'{i:04d}/' + '/'.join(['x' * 100] * 4), 'sha256': 'a' * 64}
                             for i in range(2048)]
    public['capability_bundles'].extend({**deepcopy(template), 'name': f'large-history-{i}'}
                                       for i in range(5))
    assert len(wire_bytes({key: public[key] for key in CATALOG_KEYS})) > CATALOG_READER_MAX_BYTES
    with pytest.raises(ValueError, match='reader byte bound'):
        registry(public)


@pytest.mark.parametrize('change', ['pool-image', 'host-name', 'caller-scope'])
def test_preflight_rejects_unmatched_pool_and_caller_references(change):
    current, candidate = dense_public(95), dense_public(100)
    if change == 'pool-image':
        candidate['kubernetes_workers'][-1]['image'] = IMAGE.replace('a' * 64, 'b' * 64)
    elif change == 'host-name':
        candidate['kubernetes_workers'][-1]['host_config_map'] = candidate['kubernetes_workers'][0]['host_config_map']
    else:
        candidate['callers'][0]['profiles'] = ['unregistered.v1']
    with pytest.raises(CapacityError):
        preflight(current, candidate, host_data=hosts(current, candidate))


def test_full_renderer_rejects_105_before_writing_and_requires_matching_control_evidence(tmp_path):
    public = dense_public(105)
    public.update(api=dict(host='0.0.0.0', port=8080),
        model_proxy=dict(upstream_base_url='http://llm-service.llm.svc.cluster.local/v1',
                         upstream_token_ref='model-upstream', signing_key_ref='model-signing'))
    path = tmp_path / 'public.json'
    path.write_bytes(wire_bytes(public))
    arguments = dict(config_path=path, control_image=IMAGE, output_dir=tmp_path / 'rendered',
                     kubernetes_api_cidrs=['10.96.0.1/32'])
    with pytest.raises(RenderError, match='catalog_publication_capacity_blocked'):
        render(**arguments)
    assert not (tmp_path / 'rendered').exists()
    mismatched = support()
    mismatched['consumers'][0]['image'] = IMAGE.replace('a' * 64, 'b' * 64)
    with pytest.raises(RenderError, match='catalog_publication_capacity_blocked'):
        render(**arguments, catalog_compatibility=mismatched)
    assert not (tmp_path / 'rendered').exists()
    result = render(**arguments, catalog_compatibility=support())
    report = json.loads((result['output_dir'] / 'catalog-capacity.json').read_text(encoding='utf-8'))
    assert report['status'] == 'ready'
    assert report['consumer_support']['effective_history_limit'] == 256
    assert not report['publication_authorized']


def test_cli_is_offline_read_only_and_keeps_default_transition_closed(tmp_path, capsys, monkeypatch):
    import subprocess
    def forbidden(*args, **kwargs):
        raise AssertionError('Capacity preparation must not execute a process')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    paths = [tmp_path / name for name in ('current.json', 'candidate.json')]
    for path, value in zip(paths, (dense_public(95), dense_public(105))):
        path.write_bytes(wire_bytes(value))
    before = [path.read_bytes() for path in paths]
    assert main(['--current', str(paths[0]), '--candidate', str(paths[1])]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'blocked'
    assert report['consumer_support']['effective_history_limit'] == 100
    assert [path.read_bytes() for path in paths] == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ['candidate.json', 'current.json']
