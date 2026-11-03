"""Offline capacity preflight for an additive PPT catalog publication.

No record is pruned, no UID is allocated, and no cluster or database is opened.
Consumer references are supplied by the trusted operator after actual image
verification; this tool never turns an image name into runtime verification.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from agent_service.catalog_limits import (
    CATALOG_KEYS, CONFIGMAP_MAX_BYTES, HISTORY_RECORD_LIMIT, LEGACY_HISTORY_LIMIT,
    PPT_RELEASE_GROWTH, PUBLISH_CATALOG_MAX_BYTES, PUBLISH_PUBLIC_MAX_BYTES,
    SECTION_LIMITS, wire_bytes,
)
from agent_service.config import _Public
from agent_service.registry import DeploymentRegistry

REQUIRED_CONSUMERS = ('control-api', 'control-dispatcher', 'model-proxy', 'candidate-worker')
PPT_PROFILES = frozenset('ppt.' + phase + '.v1' for phase in (
    'plan_initial', 'plan_options', 'prepare', 'generate', 'revise'))
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_IMAGE = re.compile(r'[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}\Z')
_IDENTITIES = {'task_contracts': ('name', 'revision'), 'capability_bundles': ('name', 'revision'),
               'tool_environments': ('name', 'revision'), 'execution_bindings': ('id', 'revision'),
               'kubernetes_workers': ('pool_id',), 'routes': ('profile', 'profile_revision')}


class CapacityError(ValueError):
    pass


def _require(value, code):
    if not value:
        raise CapacityError(code)


def _registry(public):
    parsed = _Public.model_validate(public)
    registry = DeploymentRegistry.from_config(**{key: public[key] for key in CATALOG_KEYS})
    registered = {row['name'] for row in parsed.task_contracts}
    _require(all(set(caller.profiles) <= registered for caller in parsed.callers),
             'unregistered_caller_scope')
    names = set()
    for pool in parsed.kubernetes_workers:
        _require(pool.namespace == 'agent-workers' and pool.host_config_map not in names,
                 'invalid_worker_pool_namespace_or_config')
        names.add(pool.host_config_map)
        _require((pool.binding_id is None) == (pool.binding_revision is None),
                 'invalid_worker_pool_binding')
        matches = ([registry.binding_policy(pool.binding_id, pool.binding_revision)]
                   if pool.binding_id is not None else
                   [policy for policy in registry.profiles().policies() if policy.engine == pool.engine])
        _require(len(matches) == 1 and matches[0] is not None
            and matches[0].engine == pool.engine
            and matches[0].image_digest == pool.image.rsplit('@', 1)[1],
                 'invalid_worker_pool_binding')
    return registry


def _consumer_support(compatibility):
    records = {}
    if compatibility is not None:
        _require(type(compatibility) is dict and set(compatibility) == {'schema', 'consumers'}
            and compatibility['schema'] == 'agent-catalog-consumer-compatibility.v1'
            and type(compatibility['consumers']) is list
            and len(compatibility['consumers']) <= len(REQUIRED_CONSUMERS), 'invalid_consumer_compatibility')
        for row in compatibility['consumers']:
            _require(type(row) is dict and set(row) == {'role', 'image', 'history_record_limit', 'evidence_sha256'},
                     'invalid_consumer_compatibility')
            role, maximum = row['role'], row['history_record_limit']
            _require(type(role) is str and role in REQUIRED_CONSUMERS and role not in records
                and type(maximum) is int and maximum in (LEGACY_HISTORY_LIMIT, HISTORY_RECORD_LIMIT)
                and type(row['image']) is str and _IMAGE.fullmatch(row['image'])
                and type(row['evidence_sha256']) is str and _SHA.fullmatch(row['evidence_sha256'])
                and row['evidence_sha256'] != '0' * 64, 'invalid_consumer_compatibility')
            records[role] = deepcopy(row)
    missing = [role for role in REQUIRED_CONSUMERS if role not in records]
    effective = min(records.get(role, {}).get('history_record_limit', LEGACY_HISTORY_LIMIT)
                    for role in REQUIRED_CONSUMERS)
    state = ('expanded_256' if effective == HISTORY_RECORD_LIMIT else
             'mixed_or_unverified_legacy_100' if records else 'unverified_legacy_100')
    return {'required_roles': list(REQUIRED_CONSUMERS), 'missing_roles': missing,
            'records': [records[role] for role in REQUIRED_CONSUMERS if role in records],
            'effective_history_limit': effective, 'transition': state,
            'evidence_scope': 'operator_verified_image_report_references_only',
            'live_consumers_verified_by_this_tool': False}


def _retention_reasons(current, candidate, current_registry):
    reasons = []
    for section, keys in _IDENTITIES.items():
        before = {tuple(row[key] for key in keys): row for row in current.get(section, [])}
        after = {tuple(row[key] for key in keys): row for row in candidate.get(section, [])}
        if len(after) != len(candidate.get(section, [])):
            reasons.append('duplicate_candidate_identity')
        for identity, row in before.items():
            proposed = after.get(identity)
            if proposed is None:
                reasons.append('historical_record_removed')
                continue
            if section == 'routes' and row['profile'] in PPT_PROFILES:
                continue
            if section == 'kubernetes_workers' and row.get('binding_id') is not None:
                binding = current_registry.binding(row['binding_id'], row['binding_revision'])
                # Old PPT pools may be disabled, but their immutable host,
                # image, reservation and every other field must stay exact.
                if binding is not None and binding.profile in PPT_PROFILES:
                    if proposed == {**row, 'enabled': False} and row.get('enabled') is True:
                        continue
            if proposed != row:
                reasons.append('non_ppt_route_changed' if section == 'routes'
                               else 'historical_record_changed')
    old_callers = {(r['tenant'], r['caller']): r for r in current['callers']}
    new_callers = {(r['tenant'], r['caller']): r for r in candidate['callers']}
    if any(new_callers.get(key) != row for key, row in old_callers.items()):
        reasons.append('existing_caller_scope_changed')
    shared_fields = (set(current) | set(candidate)) - set(_IDENTITIES) - {'callers'}
    if any(current.get(key) != candidate.get(key) for key in shared_fields):
        reasons.append('shared_public_settings_changed')
    return reasons


def _configmap(name, namespace, data):
    return {'apiVersion': 'v1', 'kind': 'ConfigMap', 'immutable': True,
            'metadata': {'name': name, 'namespace': namespace}, 'data': data}


def _map_measure(value):
    raw = wire_bytes(value)
    return {'name': value['metadata']['name'], 'namespace': value['metadata']['namespace'],
            'json_bytes_including_wrapper': len(raw),
            'data_utf8_bytes': sum(len(v.encode('utf-8')) for v in value['data'].values()),
            'json_sha256': hashlib.sha256(raw).hexdigest(), 'kubernetes_limit_bytes': CONFIGMAP_MAX_BYTES,
            'remaining_json_bytes': CONFIGMAP_MAX_BYTES - len(raw)}


def preflight(current, candidate, *, compatibility=None, host_data=None):
    """Validate exact prospective bytes without editing either supplied catalog.

    ``host_data`` maps newly referenced immutable ConfigMap names to the exact
    data strings rendered by deploy/render.py. Existing host references remain
    untouched and do not require rewriting or reserializing their old configs.
    """
    current_registry, candidate_registry = _registry(current), _registry(candidate)
    support = _consumer_support(compatibility)
    reasons = _retention_reasons(current, candidate, current_registry)
    sections = {}
    for key, configured_limit in SECTION_LIMITS.items():
        effective = min(configured_limit, support['effective_history_limit'])
        count = len(candidate.get(key, []))
        sections[key] = {'current': len(current.get(key, [])), 'candidate': count,
            'reader_limit': configured_limit, 'effective_consumer_limit': effective,
            'remaining': effective - count}
        if count > effective:
            reasons.append('consumer_catalog_capacity_exceeded')
    catalog_raw = wire_bytes({key: candidate[key] for key in CATALOG_KEYS})
    public_raw = wire_bytes(candidate)
    if len(catalog_raw) > PUBLISH_CATALOG_MAX_BYTES:
        reasons.append('new_catalog_byte_budget_exceeded')
    if len(public_raw) > PUBLISH_PUBLIC_MAX_BYTES:
        reasons.append('new_public_byte_budget_exceeded')
    public_name = 'agent-service-config-' + hashlib.sha256(public_raw).hexdigest()[:20]
    maps = [_map_measure(_configmap(public_name, 'agent-service', {'config.json': public_raw.decode('utf-8')}))]
    host_data = {} if host_data is None else host_data
    _require(type(host_data) is dict and len(host_data) <= HISTORY_RECORD_LIMIT, 'invalid_host_capacity_data')
    existing_hosts = {row['host_config_map'] for row in current.get('kubernetes_workers', [])}
    pools = {row['host_config_map']: row for row in candidate.get('kubernetes_workers', [])}
    required_hosts = sorted(set(pools) - existing_hosts)
    worker_proof = next((row for row in support['records'] if row['role'] == 'candidate-worker'), None)
    if worker_proof is not None and any(pools[name]['image'] != worker_proof['image'] for name in required_hosts):
        reasons.append('candidate_worker_compatibility_image_mismatch')
    if set(host_data) - set(pools):
        reasons.append('unreferenced_host_capacity_data')
    for name in required_hosts:
        data = host_data.get(name)
        if data is None:
            reasons.append('new_host_configmap_measurement_missing')
            continue
        _require(type(data) is dict and 'config.json' in data
            and set(data) <= {'config.json', 'acceptance.json', 'acceptance-evidence.json'}
            and all(type(raw) is str for raw in data.values()), 'invalid_host_capacity_data')
        # Match the renderer's actual selected binding checks. A subset catalog
        # or an unrelated valid host must not pass this size-only preflight.
        from agent_service.worker_entry import HostConfig, WorkerConfigError, host_policy
        maps.append(_map_measure(_configmap(name, 'agent-workers', data)))
        try:
            parsed = _json_bytes(data['config.json'].encode('utf-8'))
            _require(type(parsed.get('execution_catalog')) is dict
                and set(parsed['execution_catalog']) == set(CATALOG_KEYS), 'new_host_binding_mismatch')
            actual = host_policy(HostConfig.model_validate(parsed))
        except (ValueError, TypeError, KeyError, RecursionError, WorkerConfigError):
            reasons.append('new_host_binding_mismatch')
            continue
        pool = pools[name]
        expected = candidate_registry.binding_policy(pool['binding_id'], pool['binding_revision'])
        for section in ('task_contracts', 'capability_bundles', 'tool_environments', 'execution_bindings'):
            keys = _IDENTITIES[section]
            host_rows = {tuple(row[key] for key in keys): row for row in parsed['execution_catalog'][section]}
            if any(host_rows.get(tuple(row[key] for key in keys)) != row for row in candidate[section]):
                reasons.append('new_host_complete_catalog_required')
        if (actual.pins() != expected.pins() or actual.max_timeout_seconds != expected.max_timeout_seconds
                or actual.supports_inputs != expected.supports_inputs
                or pool['image'].rsplit('@', 1)[1] != actual.image_digest):
            reasons.append('new_host_binding_mismatch')
    if any(row['json_bytes_including_wrapper'] > CONFIGMAP_MAX_BYTES for row in maps):
        reasons.append('configmap_wire_capacity_exceeded')
    count_batches = min(max(0, sections[key]['remaining']) // growth
                        for key, growth in PPT_RELEASE_GROWTH.items())
    unique_reasons = sorted(set(reasons))
    return {'schema': 'agent-catalog-capacity-preflight.v1',
        'status': 'blocked' if unique_reasons else 'ready', 'reasons': unique_reasons,
        'current_public_sha256': hashlib.sha256(wire_bytes(current)).hexdigest(),
        'candidate_public_sha256': hashlib.sha256(public_raw).hexdigest(),
        'consumer_support': support, 'sections': sections,
        'catalog_bytes': len(catalog_raw), 'catalog_publish_budget_bytes': PUBLISH_CATALOG_MAX_BYTES,
        'public_bytes': len(public_raw), 'public_publish_budget_bytes': PUBLISH_PUBLIC_MAX_BYTES,
        'configmaps': maps, 'existing_immutable_host_maps_retained': len(existing_hosts & set(pools)),
        'new_host_maps_required': required_hosts,
        'count_only_ppt_releases_remaining': count_batches,
        'ppt_release_growth_assumption': dict(PPT_RELEASE_GROWTH),
        'future_release_bytes_predicted': False, 'historical_pruning_performed': False,
        'uid_ledger_accessed': False, 'uid_allocation_performed': False,
        'cluster_writes': 0, 'database_access': False, 'model_requests': 0,
        'acceptance_created': False, 'publication_authorized': False}


def render_capacity(public, host_configmaps, *, compatibility=None, control_image=None):
    """Guard the ordinary renderer too; legacy consumers remain the default.

    Existing host snapshots retain their own historical catalogs. The renderer
    has already verified their selected immutable binding before this size check.
    """
    report = preflight(public, public, compatibility=compatibility)
    worker_proof = next((row for row in report['consumer_support']['records']
                         if row['role'] == 'candidate-worker'), None)
    if worker_proof is not None and worker_proof['image'] not in {
            row['image'] for row in public.get('kubernetes_workers', [])}:
        report['reasons'] = sorted(set(report['reasons']) | {'candidate_worker_compatibility_image_mismatch'})
        report['status'] = 'blocked'
    pool_images = {row['host_config_map']: row['image'] for row in public.get('kubernetes_workers', [])}
    for configmap in host_configmaps:
        parsed = _json_bytes(configmap['data']['config.json'].encode('utf-8'))
        catalog = parsed.get('execution_catalog') or {}
        expanded_host = any(len(catalog.get(section, [])) > LEGACY_HISTORY_LIMIT
            for section in ('capability_bundles', 'tool_environments', 'execution_bindings'))
        if expanded_host and (worker_proof is None
                or worker_proof['history_record_limit'] != HISTORY_RECORD_LIMIT
                or worker_proof['image'] != pool_images.get(configmap['metadata']['name'])):
            report['reasons'] = sorted(set(report['reasons']) | {'expanded_host_consumer_unverified'})
            report['status'] = 'blocked'
    if control_image is not None and any(row['image'] != control_image
            for row in report['consumer_support']['records'] if row['role'] != 'candidate-worker'):
        report['reasons'] = sorted(set(report['reasons']) | {'control_compatibility_image_mismatch'})
        report['status'] = 'blocked'
    measured = [_map_measure(value) for value in host_configmaps]
    report['configmaps'].extend(measured)
    if any(row['json_bytes_including_wrapper'] > CONFIGMAP_MAX_BYTES for row in measured):
        report['reasons'] = sorted(set(report['reasons']) | {'configmap_wire_capacity_exceeded'})
        report['status'] = 'blocked'
    return report


def _json_bytes(raw):
    _require(len(raw) <= 4 * 1024 * 1024, 'capacity_input_too_large')
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, 'duplicate_capacity_input_key')
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise CapacityError('invalid_capacity_input') from None
    _require(type(value) is dict, 'invalid_capacity_input')
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--current', required=True, type=Path)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--compatibility', type=Path, help='Verified consumer image/report references; omission keeps legacy 100')
    parser.add_argument('--host-config-dir', type=Path, help='Renderer input directories for newly referenced hosts')
    args = parser.parse_args(argv)
    try:
        from agent_service.deploy.render import ordinary, no_secrets, canonical
        current, candidate = (_json_bytes(ordinary(path)) for path in (args.current, args.candidate))
        no_secrets(current)
        no_secrets(candidate)
        compatibility = _json_bytes(ordinary(args.compatibility)) if args.compatibility else None
        old = {row['host_config_map'] for row in current.get('kubernetes_workers', [])}
        data = {}
        for row in candidate.get('kubernetes_workers', []):
            name = row['host_config_map']
            if name in old or args.host_config_dir is None:
                continue
            folder = args.host_config_dir / name
            host = _json_bytes(ordinary(folder / 'config.json'))
            no_secrets(host)
            values = {'config.json': canonical(host)}
            for filename in ('acceptance.json', 'acceptance-evidence.json'):
                path = folder / filename
                if path.exists():
                    values[filename] = ordinary(path).decode('utf-8')
            data[name] = values
        report = preflight(current, candidate, compatibility=compatibility, host_data=data)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if report['status'] == 'ready' else 2
    except (OSError, ValueError, TypeError, KeyError, RecursionError, RuntimeError):
        print(json.dumps({'schema': 'agent-catalog-capacity-preflight.v1', 'status': 'blocked',
                          'reasons': ['invalid_capacity_input'], 'publication_authorized': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
