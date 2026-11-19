"""Scoped reference-read handoffs; descriptors are never pixel evidence."""
from __future__ import annotations

import math
import re
from urllib.parse import urlsplit

from .contracts import ServiceError

_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
INPUT_FIELDS = frozenset({'reference_sources', 'observation_request_history'})
MAX_REQUESTS = 4
MAX_REQUEST_FRAMES = 8


def require(value, code='reference_observation_invalid'):
    if not value:
        raise ServiceError(code, 422)


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def crop_valid(crop):
    return (type(crop) is dict and set(crop) == {'x', 'y', 'width', 'height'}
        and all(number(v) for v in crop.values())
        and 0 <= crop['x'] < 1 and 0 <= crop['y'] < 1
        and 0 < crop['width'] <= 1 - crop['x'] + 1e-9
        and 0 < crop['height'] <= 1 - crop['y'] + 1e-9)


def validate_request(request, sources):
    require(type(request) is dict and {'reference_id', 'times', 'quality', 'reason'} <= set(request)
        <= {'reference_id', 'times', 'quality', 'reason', 'crop'})
    source = next((row for row in sources if row['reference_id'] == request.get('reference_id')), None)
    require(source is not None, 'reference_observation_source_not_allowed')
    times = request['times']
    require(type(times) is list and 1 <= len(times) <= MAX_REQUEST_FRAMES
        and all(number(t) and source['start_seconds'] <= t < source['end_seconds'] for t in times)
        and all(b > a for a, b in zip(times, times[1:])), 'reference_observation_time_not_allowed')
    require(request['quality'] in ('overview', 'detail') and type(request['quality']) is str)
    reason = request['reason']
    require(type(reason) is str and 1 <= len(reason.strip()) <= 2000
        and not any(ord(c) < 32 and c not in '\r\n\t' for c in reason))
    require('crop' not in request or crop_valid(request['crop']), 'reference_observation_crop_invalid')
    return request


def validate_input(document):
    sources = document.get('reference_sources', [])
    require(type(sources) is list and len(sources) <= 4)
    seen = set()
    for source in sources:
        require(type(source) is dict and {'reference_id', 'source_snapshot_sha256',
            'source_identity_scope', 'start_seconds', 'end_seconds'} <= set(source)
            and set(source) <= {'reference_id', 'source_type', 'source_url', 'source_id',
                'source_snapshot_sha256', 'source_identity_scope', 'start_seconds', 'end_seconds',
                'width', 'height', 'duration_seconds', 'quality', 'timestamp_seconds', 'crop',
                'provider', 'video_id', 'title', 'has_audio', 'mime_type'})
        identity = source['reference_id']
        require(type(identity) is str and _ID.fullmatch(identity) and identity not in seen)
        seen.add(identity)
        require(type(source['source_snapshot_sha256']) is str
            and _SHA.fullmatch(source['source_snapshot_sha256'])
            and source['source_identity_scope'] == 'provider_metadata')
        require(number(source['start_seconds']) and number(source['end_seconds'])
            and 0 <= source['start_seconds'] < source['end_seconds'] <= 21600)
        if 'source_url' in source:
            require(type(source['source_url']) is str and len(source['source_url']) <= 2000)
            try:
                parsed = urlsplit(source['source_url'])
                require(parsed.scheme == 'https' and parsed.hostname and parsed.username is None
                    and parsed.password is None and not parsed.fragment)
            except ValueError:
                require(False)
        for key in ('width', 'height'):
            if key in source:
                require(type(source[key]) is int and 1 <= source[key] <= 7680)
        if 'crop' in source and source['crop'] is not None:
            require(crop_valid(source['crop']))
    require(document.get('work_kind') != 'transfer' or not sources,
        'reference_observation_transfer_forbidden')
    history = document.get('observation_request_history', [])
    require(type(history) is list and len(history) <= MAX_REQUESTS)
    request_ids = set()
    for item in history:
        require(type(item) is dict and {'request_id', 'reference_id', 'request', 'frame_ids',
            'source_snapshot_sha256', 'scope'} <= set(item))
        require(type(item['request_id']) is str and _ID.fullmatch(item['request_id'])
            and item['request_id'] not in request_ids)
        request_ids.add(item['request_id'])
        validate_request(item['request'], sources)
        require(item['request']['reference_id'] == item['reference_id'])
        source = next(s for s in sources if s['reference_id'] == item['reference_id'])
        require(item['request']['reference_id'] == item['reference_id']
            and item['source_snapshot_sha256'] == source['source_snapshot_sha256']
            and item['scope'] == 'sampled_frames_only'
            and type(item['frame_ids']) is list and 1 <= len(item['frame_ids']) <= MAX_REQUEST_FRAMES
            and all(type(f) is str and _ID.fullmatch(f) for f in item['frame_ids']))
    return sources


def validate_remote_frame(frame, sources):
    """Validate metadata scope, without pretending it proves remote pixels."""
    if 'source_snapshot_sha256' not in frame:
        return
    checksum = frame['source_snapshot_sha256']
    require('media_sha256' not in frame and type(checksum) is str and _SHA.fullmatch(checksum),
        'reference_observation_identity_invalid')
    matching = [s for s in sources if s['source_snapshot_sha256'] == checksum]
    require(frame.get('purpose') == 'reference' and matching
        and frame.get('source_identity_scope') == 'provider_metadata',
        'reference_observation_identity_invalid')
    require(number(frame.get('actual_pts')) and number(frame.get('requested_time'))
        and any(s['start_seconds'] <= frame['requested_time'] <= frame['actual_pts'] < s['end_seconds']
                for s in matching), 'reference_observation_time_not_allowed')
    if frame.get('crop') is not None:
        require(crop_valid(frame['crop']))
    if 'time_base' in frame or 'pts_ticks' in frame:
        base, ticks = frame.get('time_base'), frame.get('pts_ticks')
        require(type(base) is list and len(base) == 2
            and all(type(v) is int and v > 0 for v in base) and type(ticks) is int and ticks >= 0
            and math.isclose(frame['actual_pts'], ticks * base[0] / base[1], abs_tol=1e-6))


def validate_result(result, context):
    request = result.get('observation_request')
    if request is None:
        return False
    require(context['work_kind'] == 'reproduction' and context['mode'] != 'transfer_plan',
        'reference_observation_transfer_forbidden')
    require(len(context.get('observation_request_history', [])) < MAX_REQUESTS,
        'reference_observation_request_limit')
    validate_request(request, context.get('reference_sources', []))
    require(all(result.get(k) is None for k in ('source', 'review', 'transfer_task', 'method')),
        'reference_observation_handoff_mixed_result')
    require(not result.get('sound_events') and result.get('analysis') is None,
        'reference_observation_handoff_mixed_result')
    return True


PROMPT = '''
REMOTE REFERENCE OBSERVATIONS: reference_sources are scoped source descriptors, not observed pixels.
Use only supplied image pixels as visual evidence. If more reference detail or times are necessary,
use the existing write tool to write output.json as a REFERENCE READ HANDOFF and finish immediately.
Keep the normal result schema/input_sha256/mode/evidence_scope; set source, review, transfer_task
and method to null; add observation_request = {"reference_id":"the permitted reference id",
"times":[absolute_source_seconds],"quality":"detail","reason":"what needs inspecting"}.
Optional crop is normalized {"x":0,"y":0,"width":1,"height":1}; quality may be overview or detail.
Times must be strictly increasing, inside the declared half-open source scope; at most 8 per request.
At most 4 additional requests share the experiment's original budget. Request early, before authoring
a full source. This does NOT synchronously return pixels. Stop: the host retrieves real observations
and resumes a separate role operation in the same round. Do not browse or fetch yourself, do not
claim the requested frames were seen until supplied, and do not mix a handoff with creative output.
observation_request_history records previous requests; only the current supplied images are evidence
available in this operation. Provider metadata hashes are not complete video hashes. Static or sampled
frames do not establish continuous motion, audio perception, or user approval.
'''
