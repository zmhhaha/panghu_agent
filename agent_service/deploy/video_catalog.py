"""Append disabled video candidates to an existing public catalog.

This administrative preparation tool performs no cluster/model/Secret I/O and
creates no acceptance or enablement. Existing rows remain exactly as supplied.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path

from agent_service.catalog import CATALOG_KEYS, VIDEO_PROFILES
from agent_service.config import _Public
from agent_service.contracts import canonical_bytes, digest
from agent_service.registry import DeploymentRegistry


class VideoCatalogError(RuntimeError):
    pass


def require(value, code):
    if not value:
        raise VideoCatalogError(code)


def registry(public):
    try:
        _Public.model_validate(public)
        return DeploymentRegistry.from_config(**{key: public[key] for key in CATALOG_KEYS})
    except (ValueError, KeyError, TypeError):
        raise VideoCatalogError('invalid_public_catalog') from None


def append_row(public, section, row, keys):
    identity = tuple(row[key] for key in keys)
    matches = [item for item in public[section] if tuple(item[key] for key in keys) == identity]
    require(len(matches) <= 1, 'duplicate_catalog_identity')
    if matches:
        require(matches[0] == row, 'existing_catalog_identity_conflict')
    else:
        public[section].append(deepcopy(row))


def merge_video_catalog(current, candidate):
    """Return an additive public catalog, with all new video work disabled."""
    before = deepcopy(current)
    registry(current)
    proposed = registry(candidate)
    require(current.get('schema_version') == candidate.get('schema_version') == 2,
            'catalog_schema_mismatch')
    require(not any(row['enabled'] for row in current['routes']
                    if row['profile'] in VIDEO_PROFILES), 'video_already_enabled')
    merged = deepcopy(current)
    selected_hosts = []
    for profile in VIDEO_PROFILES:
        routes = [row for row in candidate['routes'] if row['profile'] == profile]
        require(len(routes) == 1, 'video_candidate_route_required')
        route = routes[0]
        require(route['profile_revision'] == '1' and route['enabled'] is False,
                'video_candidate_must_be_disabled')
        binding = proposed.binding(route['binding_id'], route['binding_revision'])
        require(binding is not None and binding.engine == 'pi' and binding.acceptance is None,
                'unaccepted_pi_video_binding_required')
        contract = proposed.contract(profile, '1')
        bundle = proposed.bundle(binding.bundle.name, binding.bundle.revision)
        environment = proposed.environment(binding.tool_environment.name, binding.tool_environment.revision)
        require(bundle.name == 'video-workbench' and bundle.revision == 'video-workbench-v1'
                and set(contract.required_tools) == {'read', 'write'}
                and set(environment.tools) == {'read', 'write'}, 'video_candidate_resources_mismatch')
        pools = [row for row in candidate['kubernetes_workers']
                 if (row['binding_id'], row['binding_revision']) == (binding.id, binding.revision)]
        require(len(pools) == 1 and pools[0]['engine'] == 'pi' and pools[0]['enabled'] is False
                and pools[0]['uid_start'] is None and ':latest@sha256:' in pools[0]['image'],
                'disabled_unreserved_latest_video_pool_required')
        for section, value, keys in (
            ('task_contracts', contract, ('name', 'revision')),
            ('capability_bundles', bundle, ('name', 'revision')),
            ('tool_environments', environment, ('name', 'revision')),
            ('execution_bindings', binding, ('id', 'revision')),
        ):
            append_row(merged, section, value.model_dump(mode='json', exclude_none=True), keys)
        append_row(merged, 'routes', route, ('profile', 'profile_revision'))
        append_row(merged, 'kubernetes_workers', pools[0], ('pool_id',))
        selected_hosts.append(pools[0]['host_config_map'])
    expected_caller = dict(tenant='internal', caller='video-workbench', token_ref='video-workbench',
                           profiles=list(VIDEO_PROFILES))
    callers = [row for row in candidate['callers'] if row['caller'] == 'video-workbench']
    require(callers == [expected_caller], 'dedicated_video_caller_required')
    append_row(merged, 'callers', expected_caller, ('tenant', 'caller'))
    registry(merged)
    # Verify preservation independently of the append loop, including settings,
    # disabled historical pools, old accepted bindings and caller revocations.
    sections = {*CATALOG_KEYS, 'kubernetes_workers', 'callers'}
    for key, value in before.items():
        require(merged[key][:len(value)] == value if key in sections else merged[key] == value,
                'existing_catalog_changed')
    require(current == before, 'current_catalog_mutated')
    summary = dict(schema_version='video-catalog-preparation.v1',
        before_sha256=digest(before), after_sha256=digest(merged),
        existing_routes_preserved=len(before['routes']),
        existing_enabled_routes_preserved=sum(bool(row['enabled']) for row in before['routes']),
        candidate_profiles=list(VIDEO_PROFILES), video_routes_enabled=False,
        acceptance_created=False, model_requests=0, secrets_created=False,
        worker_host_config_maps=selected_hosts,
        required_caller_token_ref='video-workbench')
    return merged, summary


def read_public(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(4 * 1024 * 1024 + 1)
    require(len(raw) <= 4 * 1024 * 1024, 'public_catalog_too_large')
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate_public_catalog_key')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise VideoCatalogError('invalid_public_catalog') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--current', required=True, type=Path)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='New public JSON file; existing paths are refused')
    args = parser.parse_args(argv)
    try:
        merged, summary = merge_video_catalog(read_public(args.current), read_public(args.candidate))
        with args.output.open('xb') as stream:
            stream.write(canonical_bytes(merged) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        print(json.dumps(summary, sort_keys=True))
        return 0
    except (VideoCatalogError, OSError, ValueError, TypeError, KeyError):
        print(json.dumps({'status': 'video_catalog_preparation_failed',
                          'acceptance_created': False, 'video_routes_enabled': False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
