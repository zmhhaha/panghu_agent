"""Bounded offline Manim request shared by author and resource worker."""
from copy import deepcopy
import re
from .contracts import ServiceError, safe_relative_path


def require(value, code='manim_resource_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


REQUEST_SCHEMA = 'video.manim-resource-request.v1'
SCHEMA = 'video.manim-frame-resource.v1'
MANIM_VERSION = '0.22.1'
FPS = 24
MAX_FRAMES = 240
MAX_SOURCE_BYTES = 128 * 1024
MAX_FRAME_BYTES = 4 * 1024 * 1024
MAX_RESOURCE_BYTES = 24 * 1024 * 1024
MAX_DECODED_BYTES = 128 * 1024 * 1024
_ID = re.compile(r'[a-z][a-z0-9_-]{0,63}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def validate_request(value):
    fields = {'schema', 'resource_id', 'source', 'scene_class', 'width', 'height',
        'fps', 'frame_count', 'global_start_frame', 'seed', 'transparent', 'manim_version'}
    require(type(value) is dict and set(value) == fields and value['schema'] == REQUEST_SCHEMA)
    require(type(value['resource_id']) is str and _ID.fullmatch(value['resource_id']),
        'manim_resource_identity_invalid')
    try:
        safe_relative_path('assets/manim/' + value['resource_id'] + '/manifest.json')
    except ValueError:
        raise ServiceError('manim_resource_identity_invalid', 422) from None
    source = value['source']
    require(type(source) is str and source.strip() and '\x00' not in source, 'manim_resource_source_invalid')
    try:
        raw = source.encode('utf-8')
    except UnicodeError:
        raise ServiceError('manim_resource_source_invalid', 422) from None
    require(len(raw) <= MAX_SOURCE_BYTES, 'manim_resource_source_too_large', 413)
    require(type(value['scene_class']) is str and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', value['scene_class']),
        'manim_resource_scene_invalid')
    require(type(value['width']) is int and 320 <= value['width'] <= 1280
        and type(value['height']) is int and 180 <= value['height'] <= 720,
        'manim_resource_dimensions_invalid')
    require(type(value['fps']) is int and value['fps'] == FPS
        and type(value['frame_count']) is int and 1 <= value['frame_count'] <= MAX_FRAMES
        and type(value['global_start_frame']) is int and 0 <= value['global_start_frame'] <= 14400,
        'manim_resource_clock_invalid')
    require(value['width'] * value['height'] * value['frame_count'] * 4 <= MAX_DECODED_BYTES,
        'manim_resource_decoded_budget_exceeded', 413)
    require(type(value['seed']) is int and 0 <= value['seed'] <= 2**32 - 1
        and type(value['transparent']) is bool and value['manim_version'] == MANIM_VERSION,
        'manim_resource_runtime_invalid')
    return deepcopy(value)
