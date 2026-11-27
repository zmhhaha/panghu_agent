"""Exact, frozen lossless sample metadata; no render, conversion or defaults.

Encoded screenshot bytes are the raw capture here. A later conversion must
record its own input/output hashes rather than claim these were pixel hashes.
"""
from copy import deepcopy
import hashlib
import math

from agent_service.contracts import ServiceError

SAMPLE_CAPTURE_POLICY = dict(schema='video.scene-space-sample-capture.v1',
    format='png', quality=None, max_samples=3, max_sample_bytes=4*1024*1024,
    max_total_sample_bytes=12*1024*1024)
FRAME_CAPTURE_SCHEMA = 'video.scene-space-frame-capture.v1'


def _require(ok, code):
    if not ok:
        raise ServiceError(code, 409)


def validate_sample_capture(value):
    _require(type(value) is dict and set(value)==set(SAMPLE_CAPTURE_POLICY)
        and all(type(value[key]) is type(expected) and value[key]==expected
            for key,expected in SAMPLE_CAPTURE_POLICY.items()), 'scene_space_sample_capture_invalid')
    return deepcopy(value)


def _frame_range(value):
    _require(type(value) is dict and set(value)=={'start_frame','end_frame'}
        and all(type(value[key]) is int for key in value)
        and 0<=value['start_frame']<value['end_frame']<=7200, 'scene_space_sample_frame_range_invalid')
    return value['start_frame'],value['end_frame']


def selected_sample_frames(frame_range):
    start,end=_frame_range(frame_range)
    return sorted({start,min(end-1,start+(end-start)//2),end-1})


def validate_frame_capture(capture, *, frame_range, raw=None, timestamp_seconds=None):
    start,end=_frame_range(frame_range)
    code='scene_space_frame_capture_invalid'
    _require(type(capture) is dict and set(capture)=={'schema','absolute_frame','relative_frame',
        'format','quality','sha256','bytes'}, code)
    _require(capture['schema']==FRAME_CAPTURE_SCHEMA and capture['format']=='png'
        and capture['quality'] is None and type(capture['absolute_frame']) is int
        and type(capture['relative_frame']) is int and start<=capture['absolute_frame']<end
        and capture['absolute_frame'] in selected_sample_frames(frame_range)
        and capture['relative_frame']==capture['absolute_frame']-start
        and type(capture['bytes']) is int and 0<capture['bytes']<=SAMPLE_CAPTURE_POLICY['max_sample_bytes']
        and type(capture['sha256']) is str and len(capture['sha256'])==64
        and all(ch in '0123456789abcdef' for ch in capture['sha256']), code)
    if timestamp_seconds is not None:
        _require(type(timestamp_seconds) in (int,float) and math.isfinite(timestamp_seconds)
            and timestamp_seconds==capture['relative_frame']/24, code)
    if raw is not None:
        _require(type(raw) is bytes and raw.startswith(b'\x89PNG\r\n\x1a\n')
            and len(raw)==capture['bytes'] and hashlib.sha256(raw).hexdigest()==capture['sha256'],
            'scene_space_frame_capture_changed')
    return deepcopy(capture)
