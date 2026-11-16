"""Independent resource planning preserves the frozen joint-plan-v1 artifact."""
from copy import deepcopy
import re

from ..contracts import ServiceError, canonical_bytes, digest
from .video_joint_plan_contract import validate_joint_plan_input, validate_joint_plan_result


INPUT_SCHEMA = 'video-resource-plan-input.v1'
RESULT_SCHEMA = 'video-resource-plan-result.v1'
INPUT_MAX_BYTES = 3 * 1024 * 1024
OUTPUT_MAX_BYTES = 256 * 1024
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
NODE_FIELDS = frozenset({'id', 'kind', 'producer', 'inputs', 'dependencies', 'objective',
    'required_capabilities', 'key_effects', 'local_timing', 'placement'})


def require(value, code='resource_planning_contract_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def text(value, maximum=16000):
    require(type(value) is str and value.strip() and len(value) <= maximum
        and not any(ord(c) < 32 and c not in '\r\n\t' or 0xD800 <= ord(c) <= 0xDFFF for c in value))
    return value


def labels(value):
    require(type(value) is list and len(value) <= 64
        and all(type(item) is str and _ID.fullmatch(item) for item in value)
        and len(value) == len(set(value)))
    return value


def checksum(value):
    require(type(value) is str and _SHA.fullmatch(value))
    return value


def catalogue(value):
    require(type(value) is list and len(value) <= 16)
    seen = set()
    for row in value:
        require(type(row) is dict and set(row) == {'id', 'kind', 'revision', 'admitted', 'capabilities', 'description', 'limits'})
        require(all(type(row[key]) is str and _ID.fullmatch(row[key]) for key in ('id', 'kind', 'revision'))
            and row['id'] not in seen and row['admitted'] is True)
        seen.add(row['id'])
        labels(row['capabilities'])
        text(row['description'], 4000)
        require(type(row['limits']) is dict)
    require(len(canonical_bytes(value)) <= 128 * 1024, 'resource_planning_catalog_too_large', 413)
    return deepcopy(value)


def validate_input(document):
    require(type(document) is dict and set(document) == {'schema_version', 'joint_input', 'joint_input_sha256',
        'joint_result', 'joint_result_sha256', 'producer_catalog', 'producer_catalog_sha256'})
    require(document['schema_version'] == INPUT_SCHEMA)
    validate_joint_plan_input(document['joint_input'])
    require(checksum(document['joint_input_sha256']) == digest(document['joint_input']), 'resource_planning_joint_input_changed', 409)
    validate_joint_plan_result(document['joint_result'], document['joint_input_sha256'], document['joint_input'])
    require(checksum(document['joint_result_sha256']) == digest(document['joint_result']), 'resource_planning_joint_result_changed', 409)
    catalogue(document['producer_catalog'])
    require(checksum(document['producer_catalog_sha256']) == digest(document['producer_catalog']),
        'resource_planning_producers_changed', 409)
    require(len(canonical_bytes(document)) <= INPUT_MAX_BYTES, 'resource_planning_input_too_large', 413)
    return deepcopy(document)


def validate_result(result, input_sha256, input_document):
    context = validate_input(input_document)
    require(type(result) is dict and set(result) == {'schema_version', 'input_sha256', 'resource_plan', 'reason', 'warnings'}
        and result['schema_version'] == RESULT_SCHEMA and checksum(input_sha256) == digest(context)
        and result['input_sha256'] == input_sha256, 'resource_planning_input_binding_mismatch', 409)
    text(result['reason'], 8000)
    require(type(result['warnings']) is list and len(result['warnings']) <= 16)
    for warning in result['warnings']:
        text(warning, 2000)
    plan = result['resource_plan']
    require(type(plan) is dict and set(plan) == {'nodes', 'budget'})
    budget = plan['budget']
    require(type(budget) is dict and set(budget) == {'max_attempts', 'max_attempts_per_node'}
        and all(type(value) is int and 1 <= value <= 32 for value in budget.values())
        and budget['max_attempts_per_node'] <= budget['max_attempts'])
    nodes = plan['nodes']
    require(type(nodes) is list and len(nodes) <= 16)
    require(len(nodes) <= budget['max_attempts'], 'resource_planning_budget_insufficient')
    offered = {row['id']: row for row in context['producer_catalog']}
    segments = {row['segment_id'] for row in context['joint_result']['scenes']}
    seen, dependencies = set(), {}
    for node in nodes:
        require(type(node) is dict and set(node) == NODE_FIELDS)
        identity = node['id']
        require(type(identity) is str and _ID.fullmatch(identity) and not identity.startswith('audio_')
            and identity not in seen, 'resource_planning_node_invalid')
        seen.add(identity)
        require(type(node['producer']) is str and node['producer'] in offered, 'resource_planning_unoffered_producer')
        producer = offered[node['producer']]
        require(node['kind'] == producer['kind'], 'resource_planning_producer_kind_mismatch')
        require(set(labels(node['required_capabilities'])) <= set(producer['capabilities']),
            'resource_planning_unavailable_capability')
        labels(node['key_effects'])
        text(node['objective'])
        dependencies[identity] = labels(node['dependencies'])
        require(type(node['inputs']) is dict and type(node['local_timing']) is dict)
        placement = node['placement']
        require(type(placement) is dict and set(placement) == {'segment_id', 'start_frame'}
            and type(placement['segment_id']) is str and placement['segment_id'] in segments and type(placement['start_frame']) is int
            and 0 <= placement['start_frame'] < 1440, 'resource_planning_placement_invalid')
        # Only a currently implemented consumer is advertised by this version.
        # New producer types need their own admitted bounded contract first.
        require(producer['id'] == 'manim', 'resource_planning_producer_contract_unimplemented')
        require(set(node['inputs']) <= {'resource_authoring', 'guidance'}
            and 'resource_authoring' in node['inputs'], 'resource_planning_manim_options_invalid')
        options = node['inputs']['resource_authoring']
        require(type(options) is dict and set(options) == {'kind', 'width', 'height', 'frame_count', 'global_start_frame', 'seed'}
            and options['kind'] == 'manim' and type(options['global_start_frame']) is int and options['global_start_frame'] == 0,
            'resource_planning_local_clock_required')
        limits = producer['limits']
        for key, low, high in (('width', 320, 640), ('height', 180, 360), ('frame_count', 1, 60)):
            limit = limits.get('max_frames' if key == 'frame_count' else 'max_' + key, high)
            require(type(limit) is int and type(options[key]) is int and low <= options[key] <= min(high, limit),
                'resource_planning_resource_limit')
        require(type(options['seed']) is int and 0 <= options['seed'] <= 2 ** 32 - 1)
        require(node['local_timing'] == dict(fps=24, frame_count=options['frame_count']),
            'resource_planning_local_timing_invalid')
        if 'guidance' in node['inputs']:
            text(node['inputs']['guidance'])
    visited, visiting = set(), set()
    def visit(identity):
        require(identity in dependencies, 'resource_planning_dependency_missing')
        require(identity not in visiting, 'resource_planning_dependency_cycle')
        if identity in visited:
            return
        visiting.add(identity)
        for dependency in dependencies[identity]:
            visit(dependency)
        visiting.remove(identity)
        visited.add(identity)
    for identity in dependencies:
        visit(identity)
    require(sum(node['inputs']['resource_authoring']['frame_count'] for node in nodes) <= 96,
        'resource_planning_aggregate_budget_exceeded')
    for segment in segments:
        selected = [node for node in nodes if node['placement']['segment_id'] == segment]
        require(sum(node['inputs']['resource_authoring']['frame_count'] + 1 for node in selected)
            + (2 if selected else 0) <= 60, 'resource_planning_scene_asset_budget_exceeded')
    require(len(canonical_bytes(result)) <= OUTPUT_MAX_BYTES, 'resource_planning_output_too_large', 413)
    return deepcopy(result)
