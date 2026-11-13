"""Independent reference observations and host-imported reference-free evidence.

These validators check custody and declared coverage, not artistic quality or
successful internalization. Only a server adapter may import execution receipts;
Agent text or browser request bodies are not verified host records.
"""
from copy import deepcopy
import json
import math
from urllib.parse import urlsplit

from agent_service.contracts import ServiceError, digest


RESEARCH_SCHEMA = 'video.animation-research.v1'
METHOD_SCHEMA = 'video.animation-method.v2'
REMOVAL_SCHEMA = 'video.animation-reference-removal.v1'
RELEASE_SCHEMA = 'video.animation-method-publication.v2'
MAX_RESEARCH_BYTES = 2 * 1024 * 1024


def _require(value, code='invalid_animation_research', status=422):
    if not value:
        raise ServiceError(code, status)


def _text(value, maximum=16000):
    _require(type(value) is str and bool(value.strip()) and len(value) <= maximum
             and not any((ord(c) < 32 and c not in '\r\n\t') or ord(c) == 127
                         or 0xD800 <= ord(c) <= 0xDFFF for c in value))
    return value


def _sha(value):
    _require(type(value) is str and len(value) == 64
             and all(c in '0123456789abcdef' for c in value))
    return value


def _number(value):
    _require(type(value) in (int, float) and math.isfinite(value) and value >= 0)
    return value


def _copy(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False,
                         sort_keys=True, separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise ServiceError('invalid_animation_research', 422) from None
    _require(len(raw) <= MAX_RESEARCH_BYTES, 'animation_research_too_large', 413)
    return json.loads(raw)


def validate_research(body):
    """Validate observations without deriving full viewing from a few frames."""
    fields = {'title', 'source', 'observation', 'raw_observations', 'provenance'}
    _require(type(body) is dict and fields <= set(body)
             and set(body) <= fields | {'research_id', 'parent_version_id'})
    body = _copy(body)
    _text(body['title'])
    source = body['source']
    _require(type(source) is dict and set(source) == {'url', 'media_identity'})
    _text(source['url'], 8000)
    try:
        parsed = urlsplit(source['url'])
        valid = (parsed.scheme in {'http', 'https'} and bool(parsed.hostname)
                 and parsed.username is None and parsed.password is None)
    except ValueError:
        valid = False
    _require(valid)
    media = source['media_identity']
    _require(type(media) is dict and {'platform', 'external_id'} <= set(media)
             and set(media) <= {'platform', 'external_id', 'sha256', 'duration_seconds', 'title'})
    _text(media['platform'], 200)
    _text(media['external_id'], 4000)
    if 'title' in media:
        _text(media['title'])
    if 'sha256' in media:
        _sha(media['sha256'])
    duration = media.get('duration_seconds')
    if duration is not None:
        _require(_number(duration) > 0)
    observation = body['observation']
    _require(type(observation) is dict and set(observation) == {
        'frames', 'motion_intervals', 'audio_intervals', 'limitations'})
    for field in ('frames', 'motion_intervals', 'audio_intervals'):
        rows = observation[field]
        _require(type(rows) is list and len(rows) <= 512)
        for row in rows:
            expected = ({'at_seconds', 'evidence_sha256'} if field == 'frames'
                        else {'start_seconds', 'end_seconds', 'evidence_sha256'})
            _require(type(row) is dict and set(row) == expected)
            _sha(row['evidence_sha256'])
            if field == 'frames':
                end = _number(row['at_seconds'])
            else:
                start, end = _number(row['start_seconds']), _number(row['end_seconds'])
                _require(end > start)
            _require(duration is None or end <= duration)
    limits = observation['limitations']
    _require(type(limits) is list and len(limits) <= 64)
    for item in limits:
        _text(item)
    observations = body['raw_observations']
    _require(type(observations) is list and 1 <= len(observations) <= 256)
    for item in observations:
        _text(item, 32000)
    _require(type(body['provenance']) is dict and bool(body['provenance']))
    for key in ('research_id', 'parent_version_id'):
        if key in body and body[key] is not None:
            _text(body[key], 200)
    return body


def validate_content_changes(value):
    """A difference description is evidence to assess, not a semantic verdict."""
    _require(type(value) is dict and set(value) == {'before', 'after', 'reason'},
             'animation_method_content_change_required')
    for key in value:
        _text(value[key])
    _require(value['before'] != value['after'], 'animation_method_content_change_required')
    return _copy(value)


def _contains(value, target):
    if value == target:
        return True
    if type(value) is dict:
        return any(_contains(child, target) for child in value.values())
    if type(value) is list:
        return any(_contains(child, target) for child in value)
    return False


def _no_reference_material(value, source_urls):
    if type(value) is dict:
        for key, child in value.items():
            if key in {'reference', 'research_records', 'raw_observations'} and child not in (None, [], {}):
                return False
            if not _no_reference_material(child, source_urls):
                return False
    elif type(value) is list:
        return all(_no_reference_material(child, source_urls) for child in value)
    elif type(value) is str:
        return not any(url in value for url in source_urls)
    return True


def validate_reference_removal(value, method, demonstration, research_records):
    """Bind an imported host receipt to actual frozen input and rendered output.

    The calling adapter must have retrieved these records from its execution
    ledger. This function cannot authenticate arbitrary client-supplied claims.
    It also cannot infer successful method reuse from a technically valid render.
    """
    code = 'animation_method_reference_removal_invalid'
    _require(type(value) is dict and set(value) == {
        'schema', 'input_document', 'resource_manifest', 'input_receipt', 'run_result'}, code)
    _require(value['schema'] == REMOVAL_SCHEMA, code)
    value = _copy(value)
    document, resources = value['input_document'], value['resource_manifest']
    receipt, result = value['input_receipt'], value['run_result']
    _require(type(document) is dict and bool(document) and type(resources) is list
             and len(resources) <= 128, code)
    paths = set()
    for resource in resources:
        _require(type(resource) is dict and set(resource) == {'path', 'sha256'}, code)
        _text(resource['path'], 2000)
        _sha(resource['sha256'])
        _require(resource['path'] not in paths, code)
        paths.add(resource['path'])
    _require(type(receipt) is dict and set(receipt) == {
        'source', 'operation_id', 'input_sha256', 'resource_manifest_sha256',
        'method_content_sha256', 'skill_package_sha256'}, code)
    _require(type(result) is dict and set(result) == {
        'source', 'creator_operation_id', 'render_operation_id', 'demonstration_version_id',
        'media_sha256', 'technical_evidence_sha256'}, code)
    _text(receipt['operation_id'], 500)
    for key in ('input_sha256', 'resource_manifest_sha256', 'method_content_sha256', 'skill_package_sha256'):
        _sha(receipt[key])
    for key in ('creator_operation_id', 'render_operation_id', 'demonstration_version_id'):
        _text(result[key], 500)
    for key in ('media_sha256', 'technical_evidence_sha256'):
        _sha(result[key])
    _require(receipt['source'] == 'verified_host_frozen_input'
             and result['source'] == 'verified_host_render_record', code)
    _require(receipt['input_sha256'] == digest(document)
             and receipt['resource_manifest_sha256'] == digest(resources)
             and receipt['method_content_sha256'] == method['content_sha256']
             and receipt['skill_package_sha256'] == method['skill_package']['sha256'], code)
    _require(_contains(document, method['skill_package']), code)
    sources = []
    forbidden_shas = {row['source']['media_identity'].get('sha256') for row in research_records}
    for row in research_records:
        source = row['source']
        parsed = urlsplit(source['url'])
        sources.extend((source['url'], parsed.netloc + parsed.path.rstrip('/')))
        identity = source['media_identity']['external_id']
        if len(identity) >= 8:
            sources.append(identity)
        # Exact copied observation prose is still reference material when put
        # under an innocuous key. This is a custody guard, not a semantic test
        # that the Agent has learned an independent method.
        sources.extend(text for text in row['raw_observations'] if len(text) >= 32)
        for kind in ('frames', 'motion_intervals', 'audio_intervals'):
            forbidden_shas.update(item['evidence_sha256'] for item in row['observation'][kind])
    sources.extend(value for value in forbidden_shas if value is not None)
    _require(_no_reference_material(document, sources)
             and all(row['sha256'] not in forbidden_shas for row in resources), code)
    provenance = demonstration['provenance']
    _require(result['creator_operation_id'] == receipt['operation_id']
             == provenance.get('creator_operation_id')
             and receipt['input_sha256'] == provenance.get('input_sha256')
             and result['render_operation_id'] == provenance.get('render_operation_id')
             and result['demonstration_version_id'] == demonstration['id']
             and result['media_sha256'] == demonstration['media']['sha256']
             and result['technical_evidence_sha256'] == demonstration['technical']['evidence_sha256'], code)
    return deepcopy(value)
