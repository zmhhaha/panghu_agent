"""Offline additive process routing; synthetic approvals never grant readiness."""
from copy import deepcopy
import json

import pytest

from agent_service import catalog
from agent_service.contracts import canonical_bytes, digest
from agent_service.deploy import process_catalog as helper
from agent_service.registry import binding_fingerprint
from test_catalog import image_packages


PROFILE = 'video.process-plan.v1'


@pytest.fixture
def catalogs(image_packages):
    current, _, _ = catalog.generate(pi_image='registry.invalid/current:latest@sha256:' + 'a' * 64,
        runtime_revision='previous-runtime', include_motion=True, include_explanation=True)
    reg = helper.registry(current)
    route = next(row for row in current['routes'] if row['profile'] == 'video.motion-plan.v1')
    binding = reg.binding(route['binding_id'], route['binding_revision'])
    row = next(row for row in current['execution_bindings'] if row['id'] == binding.id)
    row['acceptance'] = dict(combination_sha256=binding_fingerprint(
        reg.contract(binding.profile, binding.profile_revision),
        reg.bundle(binding.bundle.name, binding.bundle.revision),
        reg.environment(binding.tool_environment.name, binding.tool_environment.revision), binding),
        suite='video.motion-plan-contract.v1', report_sha256='f' * 64)
    route['enabled'] = True
    pool = next(row for row in current['kubernetes_workers'] if row['binding_id'] == binding.id)
    pool.update(enabled=True, uid_start=43000000, read_only_mounts=True)
    candidate, _, _ = catalog.generate(pi_image='registry.invalid/process:latest@sha256:' + 'b' * 64,
        runtime_revision='process-runtime', include_process=True)
    return current, candidate


def test_merge_keeps_accepted_existing_profiles_and_extends_only_process_scope(catalogs):
    current, candidate = catalogs
    before = deepcopy(current)
    merged, summary = helper.merge_process_catalog(current, candidate)
    assert current == before
    assert summary['existing_enabled_routes_preserved'] == 1
    assert summary['acceptance_created'] is summary['process_routes_enabled'] is False
    assert summary['model_requests'] == 0 and len(summary['worker_host_config_maps']) == 1
    for section in (*catalog.CATALOG_KEYS, 'kubernetes_workers'):
        assert merged[section][:len(before[section])] == before[section]
    old = next(row for row in current['callers'] if row['caller'] == 'video-workbench')
    new = next(row for row in merged['callers'] if row['caller'] == 'video-workbench')
    assert new == dict(old, profiles=old['profiles'] + [PROFILE])
    assert merged['callers'][:2] == before['callers'][:2]
    new_binding = merged['execution_bindings'][-1]
    assert new_binding['profile'] == PROFILE and 'acceptance' not in new_binding
    assert not merged['routes'][-1]['enabled'] and not merged['kubernetes_workers'][-1]['enabled']
    assert merged['tool_environments'][-1] not in current['tool_environments']
    assert helper.merge_process_catalog(merged, candidate)[0] == merged


def test_existing_revoked_old_video_profiles_are_not_regranted(catalogs):
    current, candidate = catalogs
    caller = next(row for row in current['callers'] if row['caller'] == 'video-workbench')
    caller['profiles'] = ['video.motion-plan.v1']
    merged, _ = helper.merge_process_catalog(current, candidate)
    updated = next(row for row in merged['callers'] if row['caller'] == 'video-workbench')
    assert updated['profiles'] == ['video.motion-plan.v1', PROFILE]


@pytest.mark.parametrize('change', ['enabled', 'reserved_uid', 'mutable_image', 'bundle_hash',
    'unrelated_capability', 'wrong_caller', 'missing_caller', 'conflicting_contract'])
def test_merge_refuses_unreviewed_or_conflicting_process_candidate(catalogs, change):
    current, candidate = catalogs
    before = digest(current)
    route = next(row for row in candidate['routes'] if row['profile'] == PROFILE)
    binding = next(row for row in candidate['execution_bindings'] if row['profile'] == PROFILE)
    pool = next(row for row in candidate['kubernetes_workers'] if row['binding_id'] == binding['id'])
    if change == 'enabled':
        route['enabled'] = True
    elif change == 'reserved_uid':
        pool['uid_start'] = 45000000
    elif change == 'mutable_image':
        pool['image'] = 'registry.invalid/process:latest'
    elif change == 'bundle_hash':
        next(row for row in candidate['capability_bundles']
             if row['name'] == 'video-process')['resources'][0]['sha256'] = '0' * 64
    elif change == 'unrelated_capability':
        env = next(row for row in candidate['tool_environments']
                   if row['name'] == binding['tool_environment']['name'])
        env['capabilities'].append('video-motion-plan-output.v1')
    elif change == 'wrong_caller':
        next(row for row in candidate['callers'] if row['caller'] == 'video-workbench')['token_ref'] = 'other'
    elif change == 'missing_caller':
        current['callers'] = [row for row in current['callers'] if row['caller'] != 'video-workbench']
        before = digest(current)
    else:
        current['task_contracts'].append(dict(next(row for row in candidate['task_contracts']
             if row['name'] == PROFILE), max_timeout_seconds=100))
        before = digest(current)
    with pytest.raises(helper.ProcessCatalogError):
        helper.merge_process_catalog(current, candidate)
    assert digest(current) == before


def test_process_catalog_cli_exclusively_creates_reviewable_public_file(catalogs, tmp_path, capsys):
    current, candidate = catalogs
    old, new, output = (tmp_path / name for name in ('old.json', 'candidate.json', 'prepared.json'))
    old.write_bytes(canonical_bytes(current))
    new.write_bytes(canonical_bytes(candidate))
    args = ['--current', str(old), '--candidate', str(new), '--output', str(output)]
    assert helper.main(args) == 0
    assert json.loads(capsys.readouterr().out)['acceptance_created'] is False
    first = output.read_bytes()
    assert helper.main(args) == 1 and output.read_bytes() == first
