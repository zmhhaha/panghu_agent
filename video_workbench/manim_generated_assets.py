"""Stopped Manim resource transport, separate from fixed runtime libraries."""
import base64
from copy import deepcopy
import re

from agent_service.contracts import canonical_bytes, digest
from agent_service.engines import StopEvidence
from .manim_resource import (MAX_RESOURCE_BYTES, MAX_DECODED_BYTES, manifest_path,
    require, sha, validate_request, verify_resource)

SCHEMA = 'video.manim-generated-assets.v1'
MAX_ENVELOPE_BYTES = 34 * 1024 * 1024


def scope(owner, effect_id, experiment_id, operation_id):
    require(all(type(value) is str and 1 <= len(value) <= 100
        and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]*', value)
        for value in (owner, effect_id, experiment_id, operation_id)), 'manim_resource_scope_invalid')
    return dict(owner=owner, effect_id=effect_id, experiment_id=experiment_id, operation_id=operation_id)


def freeze_generated_assets(files, request, *, owner, effect_id, experiment_id,
        operation_id, runtime_image_digest, stop_evidence):
    binding = scope(owner, effect_id, experiment_id, operation_id)
    require(type(stop_evidence) is StopEvidence and stop_evidence.execution_id == operation_id
        and stop_evidence.all_writers_stopped is True and bool(stop_evidence.resource_ids),
        'manim_resource_writers_not_stopped', 409)
    request = validate_request(request)
    path = manifest_path(request['resource_id'])
    require(type(files) is dict and type(files.get(path)) is bytes, 'manim_resource_manifest_missing')
    checksum = sha(files[path])
    verify_resource(files, request, expected_manifest_sha256=checksum, runtime_image_digest=runtime_image_digest)
    value = dict(schema=SCHEMA, scope=binding, request=request, manifest_sha256=checksum,
        runtime_image_digest=runtime_image_digest,
        writer_stop=dict(execution_id=stop_evidence.execution_id,
            resource_ids=list(stop_evidence.resource_ids), all_writers_stopped=True),
        files={name: dict(sha256=sha(raw), bytes=len(raw), base64=base64.b64encode(raw).decode('ascii'))
            for name, raw in sorted(files.items())})
    value['sha256'] = digest(value)
    require(len(canonical_bytes(value)) <= MAX_ENVELOPE_BYTES, 'manim_resource_envelope_too_large', 413)
    return value


def decode_generated_assets(value, *, expected_sha256, owner, effect_id, experiment_id, operation_id,
        runtime_image_digest):
    require(type(value) is dict and set(value) == {'schema', 'scope', 'request', 'manifest_sha256',
        'runtime_image_digest', 'writer_stop', 'files', 'sha256'} and value['schema'] == SCHEMA,
        'manim_resource_envelope_invalid')
    require(value['scope'] == scope(owner, effect_id, experiment_id, operation_id)
        and value['runtime_image_digest'] == runtime_image_digest
        and value['sha256'] == expected_sha256
        and digest({key: item for key, item in value.items() if key != 'sha256'}) == expected_sha256,
        'manim_resource_envelope_binding_mismatch', 409)
    require(len(canonical_bytes(value)) <= MAX_ENVELOPE_BYTES, 'manim_resource_envelope_too_large', 413)
    stop = value['writer_stop']
    require(type(stop) is dict and set(stop) == {'execution_id', 'resource_ids', 'all_writers_stopped'}
        and stop['execution_id'] == operation_id and stop['all_writers_stopped'] is True
        and type(stop['resource_ids']) is list and 1 <= len(stop['resource_ids']) <= 64
        and all(type(item) is str and 1 <= len(item) <= 200 for item in stop['resource_ids'])
        and len(set(stop['resource_ids'])) == len(stop['resource_ids']), 'manim_resource_stop_binding_invalid')
    request = validate_request(value['request'])
    require(type(value['files']) is dict and len(value['files']) == request['frame_count'] + 1,
        'manim_resource_frame_inventory_invalid')
    files, total = {}, 0
    for name, entry in value['files'].items():
        require(type(entry) is dict and set(entry) == {'sha256', 'bytes', 'base64'}
            and type(entry['bytes']) is int and 0 < entry['bytes'] <= 4 * 1024 * 1024
            and type(entry['base64']) is str and len(entry['base64']) == 4 * ((entry['bytes'] + 2) // 3),
            'manim_resource_envelope_file_invalid')
        total += entry['bytes']
        require(total <= MAX_RESOURCE_BYTES, 'manim_resource_budget_exceeded', 413)
        try:
            raw = base64.b64decode(entry['base64'], validate=True)
        except (ValueError, UnicodeError):
            require(False, 'manim_resource_envelope_encoding_invalid')
        require(len(raw) == entry['bytes'] and sha(raw) == entry['sha256'],
            'manim_resource_envelope_file_changed', 409)
        files[name] = raw
    verify_resource(files, request, expected_manifest_sha256=value['manifest_sha256'],
        runtime_image_digest=runtime_image_digest)
    return files


def merge_generated_assets(runtime_assets, resources, *, max_assets=128, max_module_assets=64):
    """Pure merge of already authenticated resource records, never an allowlist change.

    Resources are (files, frozen_request, expected_manifest_sha256,
    admitted_image_digest). Authentication/owner resolution occurs before this
    helper. The final film/compiler still independently validates its input.
    """
    require(type(runtime_assets) is dict and type(resources) is list and len(resources) <= 8)
    result, decoded = deepcopy(runtime_assets), 0
    for files, request, checksum, image in resources:
        manifest = verify_resource(files, request, expected_manifest_sha256=checksum, runtime_image_digest=image)
        require(not (set(result) & set(files)), 'manim_resource_asset_collision', 409)
        decoded += manifest['decoded_bytes']
        require(decoded <= MAX_DECODED_BYTES, 'manim_resource_decoded_budget_exceeded', 413)
        result.update(files)
    require(len(result) <= max_assets and len(result) <= max_module_assets,
        'manim_resource_asset_slots_exceeded', 413)
    require(all(type(raw) is bytes for raw in result.values())
        and sum(map(len, result.values())) <= 48 * 1024 * 1024, 'manim_resource_film_budget_exceeded', 413)
    return result
