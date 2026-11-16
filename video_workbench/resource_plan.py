"""Bounded common resource contracts; placement never defines generated content.

These contracts describe custody and scheduling, not media or aesthetic approval.
Producer adapters must verify actual output before the workflow adopts it.
"""
from copy import deepcopy
import json
import math
import re

from agent_service.contracts import ServiceError, digest


SCHEMA = 'video.resource-plan.v1'
NODE_FIELDS = frozenset({'id', 'kind', 'producer', 'inputs', 'dependencies', 'objective',
    'required_capabilities', 'key_effects', 'local_timing', 'placement'})
ACTIVE = frozenset({'submitting', 'running', 'submission_unknown', 'cancelling'})
TERMINAL = frozenset({'completed', 'failed', 'cancelled'})
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z')


def require(condition, code='resource_plan_invalid', status=422):
    if not condition:
        raise ServiceError(code, status)


def identifier(value):
    require(type(value) is str and _ID.fullmatch(value), 'resource_plan_identifier_invalid')
    return value


def text(value, limit=16000):
    require(type(value) is str and value.strip() and len(value) <= limit
        and not any(ord(c) < 32 and c not in '\r\n\t' or 0xD800 <= ord(c) <= 0xDFFF for c in value))
    return value


def packed(value, limit=8 * 1024 * 1024):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
        encoded = raw.encode('utf-8')
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        raise ServiceError('resource_plan_json_invalid', 422) from None
    require(len(encoded) <= limit, 'resource_plan_too_large', 413)
    return raw


def labels(value):
    require(type(value) is list and len(value) <= 64
        and all(type(x) is str and _ID.fullmatch(x) for x in value)
        and len(value) == len(set(value)))
    return deepcopy(value)


def mapping(value):
    require(type(value) is dict)
    packed(value, 256 * 1024)
    return deepcopy(value)


def validate_nodes(nodes):
    require(type(nodes) is list and 1 <= len(nodes) <= 64)
    result = {}
    for raw in nodes:
        require(type(raw) is dict and set(raw) == NODE_FIELDS)
        row = {key: deepcopy(raw[key]) for key in NODE_FIELDS}
        for key in ('id', 'kind', 'producer'):
            identifier(row[key])
        text(row['objective'])
        row['dependencies'] = labels(row['dependencies'])
        row['required_capabilities'] = labels(row['required_capabilities'])
        row['key_effects'] = labels(row['key_effects'])
        for key in ('inputs', 'local_timing', 'placement'):
            row[key] = mapping(row[key])
        require(row['id'] not in result, 'resource_plan_duplicate_node')
        result[row['id']] = row
    visited, visiting = set(), set()
    def visit(key):
        require(key in result, 'resource_plan_dependency_missing')
        require(key not in visiting, 'resource_plan_dependency_cycle')
        if key in visited:
            return
        visiting.add(key)
        for dependency in result[key]['dependencies']:
            visit(dependency)
        visiting.remove(key)
        visited.add(key)
    for key in result:
        visit(key)
    return result


def validate_request(value):
    require(type(value) is dict and set(value) == {'nodes', 'budget', 'deadline_at'})
    nodes = validate_nodes(value['nodes'])
    budget = value['budget']
    require(type(budget) is dict and set(budget) == {'max_attempts', 'max_attempts_per_node'})
    require(all(type(v) is int and 1 <= v <= 128 for v in budget.values())
        and budget['max_attempts_per_node'] <= budget['max_attempts'])
    deadline = value['deadline_at']
    require(type(deadline) in (int, float) and math.isfinite(deadline) and deadline > 0)
    result = dict(nodes=list(nodes.values()), budget=deepcopy(budget), deadline_at=deadline)
    packed(result, 1024 * 1024)
    return result


def content_definition(definition):
    """Whole-film placement is deliberately absent from production identity."""
    return {key: deepcopy(value) for key, value in definition.items() if key != 'placement'}


def validate_output(output, definition):
    require(type(output) is dict and set(output) == {
        'content', 'local_timing', 'capabilities', 'key_effects'}, 'resource_plan_output_invalid')
    value = dict(content=mapping(output['content']), local_timing=mapping(output['local_timing']),
        capabilities=labels(output['capabilities']), key_effects=labels(output['key_effects']))
    require(bool(value['content']), 'resource_plan_output_invalid')
    require(set(definition['required_capabilities']) <= set(value['capabilities'])
        and set(definition['key_effects']) <= set(value['key_effects']),
        'resource_plan_required_effect_missing', 409)
    # The producer validates measured timing; declared constraints may only be
    # omitted at planning time, never silently changed by an output.
    require(all(value['local_timing'].get(key) == expected
        for key, expected in definition['local_timing'].items()),
        'resource_plan_local_timing_mismatch', 409)
    packed(value, 512 * 1024)
    return value


def verify_producer(descriptor, definition):
    require(type(descriptor) is dict and descriptor.get('admitted') is True,
        'resource_plan_producer_not_admitted', 503)
    require(descriptor.get('kind') == definition['kind'], 'resource_plan_producer_kind_mismatch', 409)
    identifier(descriptor.get('revision'))
    capabilities = labels(descriptor.get('capabilities'))
    labels(descriptor.get('key_effects', []))
    # Key effects describe creative requirements. A producer's technical
    # admission cannot prove semantic or aesthetic achievement of those goals.
    require(set(definition['required_capabilities']) <= set(capabilities),
        'resource_plan_substitution_forbidden', 409)
    return deepcopy(descriptor)


def descendants(definitions, changed):
    affected = set(changed)
    while True:
        extra = {key for key, row in definitions.items() if set(row['dependencies']) & affected}
        if extra <= affected:
            return affected
        affected |= extra
