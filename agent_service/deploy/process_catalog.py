"""Prepare one additive, disabled process-plan route without cluster/model I/O.

The existing video-workbench caller gains only this explicitly requested profile.
Old contracts, environments, bindings, approvals, pools and routes stay exact.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

from agent_service.catalog import CATALOG_KEYS
from agent_service.contracts import canonical_bytes, digest
from agent_service.profiles.video_process import (
    PROFILE, BUNDLE_REVISION, BUNDLE_MANIFEST_SHA256, verified_bundle)
from agent_service.deploy.video_catalog import (
    VideoCatalogError as ProcessCatalogError, append_row, read_public, registry)


def require(value, code):
    if not value:
        raise ProcessCatalogError(code)


def merge_process_catalog(current, candidate):
    """Return a candidate catalog; this never enables a route or creates approval."""
    before = deepcopy(current)
    registry(current)
    proposed = registry(candidate)
    require(current.get('schema_version') == candidate.get('schema_version') == 2,
            'catalog_schema_mismatch')
    require(not any(row['enabled'] for row in current['routes'] if row['profile'] == PROFILE),
            'process_already_enabled')
    routes = [row for row in candidate['routes'] if row['profile'] == PROFILE]
    require(len(routes) == 1, 'process_candidate_route_required')
    route = routes[0]
    require(route['profile_revision'] == '1' and route['enabled'] is False,
            'process_candidate_must_be_disabled')
    binding = proposed.binding(route['binding_id'], route['binding_revision'])
    require(binding is not None and binding.profile == PROFILE and binding.engine == 'pi'
            and binding.acceptance is None, 'unaccepted_pi_process_binding_required')
    contract = proposed.contract(PROFILE, '1')
    bundle = proposed.bundle(binding.bundle.name, binding.bundle.revision)
    environment = proposed.environment(binding.tool_environment.name, binding.tool_environment.revision)
    resources = {'manifest.json': BUNDLE_MANIFEST_SHA256} | {
        name: hashlib.sha256(text.encode('utf-8')).hexdigest() for name, text in verified_bundle().items()}
    require(bundle.name == 'video-process' and bundle.revision == BUNDLE_REVISION
        and bundle.source == 'original-local-video-process-procedures-v1' and bundle.license == 'MIT'
        and {item.name: item.sha256 for item in bundle.resources} == resources
        and set(bundle.required_tools) == set(contract.required_tools) == set(environment.tools) == {'read', 'write'}
        and set(environment.capabilities) == {'video-process-plan-output.v1'},
        'process_candidate_resources_mismatch')
    # A candidate environment must be independent from every existing profile.
    # Replaying the same prepared process environment is permitted.
    require(not any(row['profile'] != PROFILE and row['tool_environment'] ==
                    binding.tool_environment.model_dump(mode='json')
                    for row in current['execution_bindings']), 'process_environment_not_independent')
    pools = [row for row in candidate['kubernetes_workers']
             if (row['binding_id'], row['binding_revision']) == (binding.id, binding.revision)]
    require(len(pools) == 1 and pools[0]['engine'] == 'pi' and pools[0]['enabled'] is False
        and pools[0]['uid_start'] is None and ':latest@sha256:' in pools[0]['image']
        and pools[0]['image'].rsplit('@', 1)[1] == environment.image_digest,
        'disabled_unreserved_latest_process_pool_required')
    merged = deepcopy(current)
    for section, value, keys in (
        ('task_contracts', contract, ('name', 'revision')),
        ('capability_bundles', bundle, ('name', 'revision')),
        ('tool_environments', environment, ('name', 'revision')),
        ('execution_bindings', binding, ('id', 'revision')),
    ):
        append_row(merged, section, value.model_dump(mode='json', exclude_none=True), keys)
    append_row(merged, 'routes', route, ('profile', 'profile_revision'))
    append_row(merged, 'kubernetes_workers', pools[0], ('pool_id',))
    callers = [row for row in merged['callers'] if row['caller'] == 'video-workbench']
    require(len(callers) == 1 and callers[0]['tenant'] == 'internal'
            and callers[0]['token_ref'] == 'video-workbench', 'existing_video_caller_required')
    caller = callers[0]
    candidate_callers = [row for row in candidate['callers'] if row['caller'] == 'video-workbench']
    require(len(candidate_callers) == 1 and candidate_callers[0]['tenant'] == 'internal'
        and candidate_callers[0]['token_ref'] == caller['token_ref']
        and PROFILE in candidate_callers[0]['profiles']
        and all(name.startswith('video.') for name in candidate_callers[0]['profiles']),
        'dedicated_process_caller_required')
    if PROFILE not in caller['profiles']:
        caller['profiles'].append(PROFILE)
    registry(merged)
    for key, value in before.items():
        if key == 'callers':
            for old, new in zip(value, merged[key], strict=True):
                expected = deepcopy(old)
                if old['caller'] == 'video-workbench' and PROFILE not in expected['profiles']:
                    expected['profiles'].append(PROFILE)
                require(new == expected, 'existing_catalog_changed')
        elif key in {*CATALOG_KEYS, 'kubernetes_workers'}:
            require(merged[key][:len(value)] == value, 'existing_catalog_changed')
        else:
            require(merged[key] == value, 'existing_catalog_changed')
    require(current == before, 'current_catalog_mutated')
    return merged, dict(schema_version='process-catalog-preparation.v1',
        before_sha256=digest(before), after_sha256=digest(merged), candidate_profiles=[PROFILE],
        existing_routes_preserved=len(before['routes']),
        existing_enabled_routes_preserved=sum(bool(row['enabled']) for row in before['routes']),
        process_routes_enabled=False, acceptance_created=False, model_requests=0, secrets_created=False,
        worker_host_config_maps=[pools[0]['host_config_map']],
        required_caller_token_ref='video-workbench')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--current', required=True, type=Path)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='New public JSON; existing paths are refused')
    args = parser.parse_args(argv)
    try:
        merged, summary = merge_process_catalog(read_public(args.current), read_public(args.candidate))
        with args.output.open('xb') as stream:
            stream.write(canonical_bytes(merged) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        print(json.dumps(summary, sort_keys=True))
        return 0
    except (ProcessCatalogError, OSError, ValueError, TypeError, KeyError, AttributeError):
        print(json.dumps(dict(status='process_catalog_preparation_failed',
            acceptance_created=False, process_routes_enabled=False)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
