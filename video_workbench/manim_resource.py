"""Bounded Manim/Cairo PNG resources, independent of author or browser execution.

Manim is an offline authoring capability, not a fourth browser engine. A
verified resource is consumed on the existing absolute HyperFrames clock.
"""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import re
import stat

from PIL import Image

from agent_service.contracts import ServiceError, canonical_bytes, digest, safe_relative_path

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


def require(value, code='manim_resource_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


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


def frame_path(resource_id, index):
    require(type(resource_id) is str and _ID.fullmatch(resource_id)
        and type(index) is int and 0 <= index < MAX_FRAMES)
    return f'assets/manim/{resource_id}/frame_{index:06d}.png'


def manifest_path(resource_id):
    require(type(resource_id) is str and _ID.fullmatch(resource_id))
    return f'assets/manim/{resource_id}/manifest.json'


def inspect_png(raw, width, height):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_FRAME_BYTES, 'manim_resource_frame_budget', 413)
    try:
        with Image.open(io.BytesIO(raw)) as image:
            require(image.format == 'PNG' and image.size == (width, height)
                and image.mode == 'RGBA' and not getattr(image, 'is_animated', False),
                'manim_resource_frame_format_invalid')
            image.verify()
        # verify() checks the chunk structure; load() also checks compressed pixels.
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise ServiceError('manim_resource_frame_format_invalid', 422) from None


def compile_resource(request, frames, *, runtime_image_digest):
    request = validate_request(request)
    require(type(runtime_image_digest) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', runtime_image_digest),
        'manim_resource_image_digest_required')
    expected = [frame_path(request['resource_id'], index) for index in range(request['frame_count'])]
    require(type(frames) is dict and set(frames) == set(expected), 'manim_resource_frame_inventory_invalid')
    require(all(type(raw) is bytes for raw in frames.values())
        and sum(map(len, frames.values())) <= MAX_RESOURCE_BYTES, 'manim_resource_budget_exceeded', 413)
    inventory = []
    for index, path in enumerate(expected):
        raw = frames[path]
        inspect_png(raw, request['width'], request['height'])
        inventory.append(dict(index=index, global_frame=request['global_start_frame'] + index,
            path=path, bytes=len(raw), sha256=sha(raw)))
    manifest = dict(schema=SCHEMA, resource_id=request['resource_id'],
        request_sha256=digest(request), source_sha256=sha(request['source'].encode('utf-8')),
        runtime=dict(library='manim-community', version=MANIM_VERSION, renderer='cairo',
            image_digest=runtime_image_digest, seed=request['seed'], latex_available=False),
        width=request['width'], height=request['height'], fps=FPS, frame_count=request['frame_count'],
        decoded_bytes=request['width'] * request['height'] * request['frame_count'] * 4,
        limits=dict(max_resource_bytes=MAX_RESOURCE_BYTES, max_decoded_bytes=MAX_DECODED_BYTES),
        global_start_frame=request['global_start_frame'], transparent=request['transparent'],
        frames=inventory, audio=None, evidence_scope='validated_frame_resource_not_visual_acceptance')
    result = dict(frames)
    result[manifest_path(request['resource_id'])] = canonical_bytes(manifest)
    require(sum(map(len, result.values())) <= MAX_RESOURCE_BYTES, 'manim_resource_budget_exceeded', 413)
    return result


def verify_resource(files, request, *, expected_manifest_sha256, runtime_image_digest):
    request = validate_request(request)
    path = manifest_path(request['resource_id'])
    require(type(files) is dict and type(files.get(path)) is bytes
        and type(expected_manifest_sha256) is str and _SHA.fullmatch(expected_manifest_sha256)
        and sha(files[path]) == expected_manifest_sha256, 'manim_resource_manifest_binding_mismatch', 409)
    frames = {name: raw for name, raw in files.items() if name != path}
    expected = compile_resource(request, frames, runtime_image_digest=runtime_image_digest)
    require(files == expected, 'manim_resource_manifest_changed', 409)
    return json.loads(files[path])


def collect_resource_files(directory, request, *, expected_manifest_sha256, runtime_image_digest):
    """Independent collector for a stopped job's exact deliverable tree.

    The caller must obtain the existing runtime StopProof first. This function
    does not execute a job or infer stopped writers from a file's existence.
    """
    request = validate_request(request)
    root = Path(directory).absolute()
    allowed = {frame_path(request['resource_id'], index) for index in range(request['frame_count'])}
    allowed.add(manifest_path(request['resource_id']))
    folders = {prefix for path in allowed for prefix in
        ('/'.join(path.split('/')[:i]) for i in range(1, len(path.split('/'))))}
    require(root.is_dir() and not any(path.is_symlink()
        or getattr(path, 'is_junction', lambda: False)() for path in (root, *root.parents)),
        'manim_resource_path_invalid')
    # Traverse with a count bound. Do not eagerly rglob an untrusted tree or
    # follow directory links into another job or the runtime filesystem.
    files, pending, seen, total = {}, [(root, '')], 0, 0
    while pending:
        directory, prefix = pending.pop()
        for path in directory.iterdir():
            seen += 1
            require(seen <= len(allowed) + len(folders), 'manim_resource_file_inventory_invalid')
            name = prefix + path.name
            metadata = path.lstat()
            require(not path.is_symlink() and not getattr(path, 'is_junction', lambda: False)(),
                'manim_resource_path_invalid')
            if stat.S_ISDIR(metadata.st_mode):
                require(name in folders, 'manim_resource_file_inventory_invalid')
                pending.append((path, name + '/'))
            else:
                maximum = 128 * 1024 if name.endswith('/manifest.json') else MAX_FRAME_BYTES
                require(stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1 and name in allowed,
                    'manim_resource_file_inventory_invalid')
                require(0 < metadata.st_size <= maximum, 'manim_resource_frame_budget', 413)
                total += metadata.st_size
                require(total <= MAX_RESOURCE_BYTES, 'manim_resource_budget_exceeded', 413)
                raw = path.read_bytes()
                require(len(raw) == metadata.st_size, 'manim_resource_file_changed', 409)
                files[name] = raw
    require(set(files) == allowed, 'manim_resource_file_inventory_invalid')
    verify_resource(files, request, expected_manifest_sha256=expected_manifest_sha256,
        runtime_image_digest=runtime_image_digest)
    return files
