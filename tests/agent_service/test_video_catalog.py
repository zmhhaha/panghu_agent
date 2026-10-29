"""Offline additive catalog preparation; synthetic approvals grant no readiness."""
from copy import deepcopy
import json

import pytest

from agent_service import catalog
from agent_service.contracts import canonical_bytes
from agent_service.deploy import video_catalog as helper
from agent_service.registry import binding_fingerprint
from test_catalog import image_packages


@pytest.fixture
def catalogs(image_packages):
    current, _, _ = catalog.generate(pi_image='registry.invalid/current@sha256:' + 'a' * 64,
                                     runtime_revision='previous-runtime')
    current['task_contracts'] = [r for r in current['task_contracts'] if r['name'] not in catalog.VIDEO_PROFILES]
    current['execution_bindings'] = [r for r in current['execution_bindings'] if r['profile'] not in catalog.VIDEO_PROFILES]
    current['routes'] = [r for r in current['routes'] if r['profile'] not in catalog.VIDEO_PROFILES]
    current['kubernetes_workers'] = [r for r in current['kubernetes_workers'] if not r['binding_id'].startswith('video-')]
    current['capability_bundles'] = [r for r in current['capability_bundles'] if r['name'] != 'video-workbench']
    current['callers'] = [r for r in current['callers'] if r['caller'] != 'video-workbench']
    # An explicitly synthetic old approval tests preservation of an enabled
    # research route; it is never persisted as an operator readiness record.
    reg = helper.registry(current)
    route = next(r for r in current['routes'] if r['profile'] == 'research.plan.v1')
    binding = reg.binding(route['binding_id'], route['binding_revision'])
    row = next(r for r in current['execution_bindings'] if r['id'] == binding.id)
    row['acceptance'] = dict(combination_sha256=binding_fingerprint(
        reg.contract(binding.profile, binding.profile_revision),
        reg.bundle(binding.bundle.name, binding.bundle.revision),
        reg.environment(binding.tool_environment.name, binding.tool_environment.revision), binding),
        suite='research.plan-contract.v1', report_sha256='f' * 64)
    route['enabled'] = True
    pool = next(r for r in current['kubernetes_workers'] if r['binding_id'] == binding.id)
    pool.update(enabled=True, uid_start=43000000, read_only_mounts=True)
    current['api']['max_request_bytes'] = 4194304
    candidate, _, _ = catalog.generate(pi_image='registry.invalid/video:latest@sha256:' + 'b' * 64,
                                       runtime_revision='video-runtime')
    return current, candidate


def test_merge_appends_only_disabled_video_and_preserves_research_history(catalogs):
    current, candidate = catalogs
    before = deepcopy(current)
    merged, summary = helper.merge_video_catalog(current, candidate)
    assert current == before
    for key, value in current.items():
        if type(value) is list:
            assert merged[key][:len(value)] == value
        else:
            assert merged[key] == value
    videos = [r for r in merged['routes'] if r['profile'] in catalog.VIDEO_PROFILES]
    assert len(videos) == 2 and all(not r['enabled'] and r['profile_revision'] == '1' for r in videos)
    bindings = [r for r in merged['execution_bindings'] if r['profile'] in catalog.VIDEO_PROFILES]
    assert len(bindings) == 2 and all('acceptance' not in r for r in bindings)
    assert summary['existing_enabled_routes_preserved'] == 1
    assert summary['video_routes_enabled'] is summary['acceptance_created'] is False
    assert len(summary['worker_host_config_maps']) == 2
    assert merged['callers'][-1]['token_ref'] == 'video-workbench'
    repeated, _ = helper.merge_video_catalog(merged, candidate)
    assert repeated == merged


@pytest.mark.parametrize('change', ['enabled', 'wrong_revision', 'caller_scope', 'reserved_uid', 'mutable_image'])
def test_unreviewed_or_conflicting_video_candidate_is_refused(catalogs, change):
    current, candidate = catalogs
    route = next(r for r in candidate['routes'] if r['profile'] == 'video.scene.v1')
    pool = next(r for r in candidate['kubernetes_workers'] if r['binding_id'] == route['binding_id'])
    if change == 'enabled':
        route['enabled'] = True
    elif change == 'wrong_revision':
        route['profile_revision'] = 'v1'
    elif change == 'caller_scope':
        next(r for r in candidate['callers'] if r['caller'] == 'video-workbench')['profiles'].append('research.plan.v1')
    elif change == 'reserved_uid':
        pool['uid_start'] = 45000000
    else:
        pool['image'] = 'registry.invalid/video:latest'
    with pytest.raises(helper.VideoCatalogError):
        helper.merge_video_catalog(current, candidate)


def test_existing_caller_revocation_is_not_silently_regranted(catalogs):
    current, candidate = catalogs
    current['callers'].append(dict(tenant='internal', caller='video-workbench',
                                   token_ref='video-workbench', profiles=[]))
    with pytest.raises(helper.VideoCatalogError, match='existing_catalog_identity_conflict'):
        helper.merge_video_catalog(current, candidate)


def test_cli_writes_only_new_public_config_and_refuses_replacement(catalogs, tmp_path, capsys):
    current, candidate = catalogs
    old, new, output = (tmp_path / name for name in ('current.json', 'candidate.json', 'prepared.json'))
    old.write_bytes(canonical_bytes(current))
    new.write_bytes(canonical_bytes(candidate))
    args = ['--current', str(old), '--candidate', str(new), '--output', str(output)]
    assert helper.main(args) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary['model_requests'] == 0 and summary['secrets_created'] is False
    first = output.read_bytes()
    assert json.loads(first)['routes'][:len(current['routes'])] == current['routes']
    assert helper.main(args) == 1
    assert output.read_bytes() == first
