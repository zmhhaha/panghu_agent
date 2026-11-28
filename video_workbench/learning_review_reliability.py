"""Explicit new consumers of the existing PNG and classified-review contracts.

Decoded reference samples remain reference observations. Only original renderer
PNGs carry capture provenance; neither path establishes motion/audio evidence.
"""
from copy import deepcopy
import hashlib
import math
import re
from uuid import UUID

from agent_service.contracts import ServiceError, digest
from agent_service.profiles import video_picture_review_v2 as protocol
from .scene_review_reliability import default_review_policy, retain_png, image_provenance
from .scene_space_capture import validate_sample_capture, selected_sample_frames
from .scene_review_continuation import MODEL_LIMITS, MODEL_LIMIT_CAPABILITY

CONTRACT = 'video.learning-classified-review.v1'
BINDING_SCHEMA = 'video.learning-review-binding.v1'
EVIDENCE_SCHEMA = 'video.learning-review-evidence.v1'
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def require_existing_review_standard(choice):
    """New work retains the established evaluator; frozen history is unchanged."""
    if choice is not None:
        raise ServiceError('review_standard_change_disabled_use_existing_standard',409)


def _route_valid(route):
    return (type(route) is dict and route.get('profile') == protocol.PROFILE
        and route.get('profile_revision') == '1' and route.get('configured') is True
        and route.get('ready') is True and route.get('supports_inputs') is True
        and route.get('model_request_limits') == MODEL_LIMIT_CAPABILITY
        and type(route.get('max_timeout_seconds')) is int and 1 <= route['max_timeout_seconds'] <= 3600
        and type(route.get('required_model_capabilities')) is list
        and all(type(row) is str for row in route['required_model_capabilities'])
        and {'text', 'tool_calls', 'vision'} <= set(route['required_model_capabilities']))


def _capability_valid(capability, capture):
    return (type(capability) is dict and capability.get('admitted') is True
        and capability.get('schema') == 'video.scene-space-render-capability.v1'
        and capability.get('engines') == ['canvas'] and capability.get('artwork_api_revision') == 3
        and all(type(capability.get(key)) is str and _SHA.fullmatch(capability[key])
            for key in ('runtime_sha256', 'host_sha256'))
        and capability.get('sample_capture') == capture
        and type(capability.get('limits')) is dict
        and all(capability['limits'].get(key) == val
            for key, val in [('width', 1280), ('height', 720), ('fps', 24)]))


def require(ok, code='learning_review_binding_invalid', status=409):
    if not ok:
        raise ServiceError(code, status)


def freeze_binding(choice, *, gateway, capability, width, height, deadline_at):
    if choice is None:
        return None
    require(choice == CONTRACT, 'learning_review_contract_invalid', 422)
    require((width, height) == (1280, 720), 'learning_review_viewport_not_admitted', 422)
    require(type(capability) is dict, 'learning_review_renderer_unavailable', 503)
    capture = validate_sample_capture(capability.get('sample_capture'))
    require(_capability_valid(capability, capture), 'learning_review_renderer_unavailable', 503)
    require(gateway is not None and gateway.profile == protocol.PROFILE
        and gateway.profile_revision == '1', 'learning_classified_review_unavailable', 503)
    route = gateway.preflight(deadline_at)
    require(_route_valid(route), 'learning_classified_review_unavailable', 503)
    policy = default_review_policy()
    policy['criteria'][0]['text'] = (
        'The actual supplied images satisfy the full frozen learning/effect goal, explicit criteria, '
        'content and requested visual style. Required artistic failures remain required violations.')
    policy = protocol.validate_review_policy(policy)
    value = dict(schema=BINDING_SCHEMA, contract=CONTRACT, profile=protocol.PROFILE,
        profile_revision='1', gate_schema='video.scene-classified-review-gate.v1',
        policy=policy, minimum_score=80, sample_capture=capture,
        renderer_capability=deepcopy(capability), review_route=deepcopy(route),
        model_limits=deepcopy(MODEL_LIMITS), model_limits_scope=deepcopy(MODEL_LIMIT_CAPABILITY),
        evidence_scope='static_samples_only', continuous_motion_verified=False, audio_verified=False)
    value['sha256'] = digest(value)
    return value


def validate_binding(value):
    fields = {'schema', 'contract', 'profile', 'profile_revision', 'gate_schema', 'policy',
        'minimum_score', 'sample_capture', 'renderer_capability', 'review_route',
        'model_limits', 'model_limits_scope',
        'evidence_scope', 'continuous_motion_verified', 'audio_verified', 'sha256'}
    require(type(value) is dict and set(value) == fields and value.get('schema') == BINDING_SCHEMA
        and value.get('contract') == CONTRACT and value.get('profile') == protocol.PROFILE
        and value.get('profile_revision') == '1' and type(value.get('minimum_score')) is int
        and value.get('minimum_score') == 80
        and value.get('gate_schema') == 'video.scene-classified-review-gate.v1'
        and value.get('sha256') == digest({k: v for k, v in value.items() if k != 'sha256'}))
    protocol.validate_review_policy(value['policy'])
    validate_sample_capture(value['sample_capture'])
    require(_route_valid(value['review_route']) and _capability_valid(value['renderer_capability'], value['sample_capture'])
        and value['model_limits'] == MODEL_LIMITS and value['model_limits_scope'] == MODEL_LIMIT_CAPABILITY
        and value['evidence_scope'] == 'static_samples_only'
        and value['continuous_motion_verified'] is False and value['audio_verified'] is False)
    return deepcopy(value)


def validate_job_limits(job, binding):
    """A resumed/polled job must retain the same job-wide provider ceiling."""
    require(job.get('model_limits') == validate_binding(binding)['model_limits'],
            'learning_review_model_limits_unproven')


def selected_frames(frames):
    """A bounded declared subset of existing observations, without new capture."""
    selected = []
    for purpose, maximum in [('reference', 3), ('output', 3), ('baseline', 2)]:
        rows = [row for row in frames if row['purpose'] == purpose]
        indices = (list(range(len(rows))) if len(rows) <= maximum else
            sorted({round(i * (len(rows) - 1) / (maximum - 1)) for i in range(maximum)}))
        selected.extend(deepcopy(rows[index]) for index in indices)
    require(any(row['purpose'] == 'output' for row in selected), 'learning_review_output_missing')
    return selected


def review_document(document, binding):
    binding = validate_binding(binding)
    frames = selected_frames(document['observation']['frames'])
    # A video creator's input is not a v2 static-review input. Build the exact
    # existing picture envelope, retaining only the explicitly sampled images.
    fields = ('project_id', 'experiment_id', 'goal', 'criteria', 'feedback')
    result = {key: deepcopy(document[key]) for key in fields}
    has_reference = any(row['purpose'] == 'reference' for row in frames)
    if not has_reference:
        frames = [row for row in frames if row['purpose'] != 'baseline']
    result.update(schema=protocol.INPUT_SCHEMA, mode='review',
        work_kind='reproduction' if has_reference else 'transfer',
        constraints=dict(width=1280, height=720, allowed_engines=['canvas'],
            review_policy=deepcopy(binding['policy']), learning_review_binding=deepcopy(binding)),
        observation=dict(frames=frames, continuous_motion_verified=False, audio_verified=False),
        base_source=None, knowledge_snapshot=None, prior_review=None, learned_method=None)
    protocol.validate_input(result)
    return result


def capture_frames(rows, *, result, candidate, render_id, frame_range, store_blob):
    require(result.get('sample_capture') is not None, 'learning_review_lossless_capture_missing')
    validate_sample_capture(result['sample_capture'])
    require(result.get('sample_frame_range') == frame_range, 'learning_review_capture_range_changed')
    expected = selected_sample_frames(frame_range)
    require(type(rows) is list and len(rows) == len(expected), 'learning_review_capture_set_changed')
    plan = candidate['scene_space']['plan']
    retained, provenance = [], []
    total = 0
    for index, (row, absolute) in enumerate(zip(rows, expected)):
        require(type(row) is dict and {'raw', 'mime', 'timestamp_seconds', 'capture'} <= set(row)
            and type(row['timestamp_seconds']) in (int, float) and math.isfinite(row['timestamp_seconds']),
            'learning_review_capture_set_changed')
        raw = row['raw']
        checksum = retain_png(raw, row.get('mime'))
        capture = row.get('capture')
        item = dict(identity='output-' + str(index + 1), frame=absolute,
                    artifact_sha256=checksum, capture=deepcopy(capture))
        proof = image_provenance(item, raw, source_sha256=candidate['source_sha256'],
            plan_sha256=digest(plan), kind='clip_sample', render_id=render_id, frame_range=frame_range)
        require(row['timestamp_seconds'] == capture['relative_frame'] / 24,
                'learning_review_capture_time_changed')
        require(store_blob(raw) == checksum, 'learning_review_retained_bytes_changed')
        retained.append(dict(frame_id=item['identity'], sha256=checksum, mime='image/png',
            timestamp_seconds=row['timestamp_seconds'], absolute_frame=absolute,
            capture=deepcopy(capture)))
        provenance.append(proof)
        total += len(raw)
    require(total <= result['sample_capture']['max_total_sample_bytes'], 'learning_review_capture_size_exceeded')
    return dict(frames=retained, coverage=dict(scope='sampled_frames_only',
        continuous_motion_verified=False, audio_verified=False)), provenance


def validate_transport(document, *, read_blob, provenance):
    """Called before upload/claim. Oversized PNGs remain stored, never converted."""
    protocol.validate_input(document)
    by_sha = {row['retained_sha256']: row for row in provenance}
    total = 0
    for frame in document['observation']['frames']:
        raw = read_blob(frame['sha256'])
        require(hashlib.sha256(raw).hexdigest() == frame['sha256'], 'learning_review_frame_changed')
        require(0 < len(raw) <= protocol.MAX_IMAGE_BYTES, 'learning_review_model_image_too_large', 413)
        total += len(raw)
        if frame['purpose'] in {'output', 'baseline'}:
            checksum = retain_png(raw, 'image/png', expected_sha256=frame['sha256'])
            record = by_sha.get(checksum)
            require(record is not None and record['model_input_sha256'] == checksum
                and record['model_input_bytes'] == len(raw) and record['conversion'] == 'identity_original_png',
                'learning_review_provenance_missing')
    from .animation_observation import MAX_FRAME_TOTAL_BYTES
    require(total <= MAX_FRAME_TOTAL_BYTES, 'learning_review_model_images_too_large', 413)


def validate_provenance(records, *, source_sha256, plan_sha256, render_id,
                        frame_range, read_blob, static=False):
    """Rebuild every saved PNG/capture identity when resuming a review."""
    expected = [frame_range['start_frame']] if static else selected_sample_frames(frame_range)
    require(type(records) is list and len(records) == len(expected), 'learning_review_provenance_changed')
    for index, (record, absolute) in enumerate(zip(records, expected)):
        raw = read_blob(record['retained_sha256'])
        item = dict(identity='output-' + str(index + 1), frame=absolute,
            artifact_sha256=record['retained_sha256'], capture=deepcopy(record.get('capture')))
        rebuilt = image_provenance(item, raw, source_sha256=source_sha256,
            plan_sha256=plan_sha256, kind='static_preview' if static else 'clip_sample',
            render_id=render_id, frame_range=frame_range)
        require(record == rebuilt, 'learning_review_provenance_changed')
    return deepcopy(records)


def decision(result, document, binding, receipt, *, job_id, read_blob):
    binding = validate_binding(binding)
    protocol.validate_result(result, digest(document), document)
    require(document['constraints']['review_policy'] == binding['policy'], 'learning_review_policy_changed')
    require(type(receipt) is dict and receipt.get('schema') == 'agent-model-image-evidence.v1'
        and receipt.get('evidence_scope') == 'completed_upstream_transport'
        and receipt.get('job_id') == job_id and type(receipt.get('request_sha256')) is str
        and _SHA.fullmatch(receipt['request_sha256']), 'learning_review_transport_identity_invalid')
    try:
        require(str(UUID(receipt['attempt_id'])) == receipt['attempt_id'], 'learning_review_transport_identity_invalid')
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ServiceError('learning_review_transport_identity_invalid', 409) from None
    expected = {}
    from agent_service.profiles.video_animation_review import _image_header
    for frame in document['observation']['frames']:
        raw = read_blob(frame['sha256'])
        require(hashlib.sha256(raw).hexdigest() == frame['sha256'], 'learning_review_frame_changed')
        _image_header(raw, frame['file_name'])
        expected[frame['sha256']] = dict(bytes=len(raw), mime='image/png' if raw.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg')
    rows = receipt.get('images')
    require(type(rows) is list and 1 <= len(rows) <= 10000, 'learning_review_frame_transport_incomplete')
    for row in rows:
        require(type(row) is dict and row.get('sha256') in expected
            and row.get('model_request_completed') is True
            and type(row.get('model_request_sha256')) is str and _SHA.fullmatch(row['model_request_sha256'])
            and row.get('media_type') == expected[row['sha256']]['mime']
            and type(row.get('byte_length')) is int and row['byte_length'] == expected[row['sha256']]['bytes']
            and all(type(row.get(key)) is int and row[key] >= 0 for key in ('input_item_index', 'part_index')),
            'learning_review_frame_transport_metadata_changed')
    observed = {row['sha256'] for row in rows}
    require(set(expected) <= observed, 'learning_review_frame_transport_incomplete')
    return protocol.quality_gate(result['review'], binding['policy'],
        document['observation']['frames'], binding['minimum_score'])
