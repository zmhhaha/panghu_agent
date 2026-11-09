"""Prepare bounded, ordered visual evidence from actual local media.

This is a media adapter, not an animation template or model client. Sampling
never means the entire video has been watched. The Agent's eventual result also
needs independently recorded model-request image evidence before being used.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
from uuid import UUID

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import implementation
from agent_service.profiles.video_animation_review import (
    OBSERVE_PROFILE, REVIEW_PROFILE, OBSERVE_INPUT_NAME, REVIEW_INPUT_NAME,
    OBSERVE_INPUT_SCHEMA, REVIEW_INPUT_SCHEMA, MAX_FRAMES, MAX_IMAGE_BYTES,
    validate_observation_input, validate_review_input)


MAX_MEDIA_BYTES = 512 * 1024 * 1024
# Leave room in the shared 8-MiB model request for base64, tools and history.
MAX_FRAME_TOTAL_BYTES = 3 * 1024 * 1024


def require(value, code='animation_observation_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def _file(path, maximum):
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents))
            and path.is_file() and path.stat().st_nlink == 1
            and 0 < path.stat().st_size <= maximum, 'animation_observation_file_invalid')
    return path


def file_sha256(path, maximum=MAX_MEDIA_BYTES):
    path = _file(path, maximum)
    before = path.stat()
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
    after = path.stat()
    require((before.st_size, before.st_mtime_ns, before.st_ino) ==
            (after.st_size, after.st_mtime_ns, after.st_ino), 'animation_observation_media_changed')
    return checksum.hexdigest()


def _run(argv, *, limit=1024 * 1024):
    try:
        response = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ServiceError('animation_observation_decoder_unavailable', 503) from None
    require(response.returncode == 0 and len(response.stdout) <= limit,
            'animation_observation_decode_failed')
    return response.stdout


def prepare_sampled_media(media_path, directory, *, clip_id, goal, source_sha256=None,
                          windows=None, sample_fps=3, frame_limit=MAX_FRAMES,
                          ffmpeg='ffmpeg', ffprobe='ffprobe'):
    """Decode local media into a new immutable input bundle, without model I/O.

    Times label the sampling grid. Decoder frame selection can differ from an
    original presentation timestamp, which is explicitly disclosed. Longer
    references require selected windows instead of a silent first-N truncation.
    """
    media_path = _file(media_path, MAX_MEDIA_BYTES)
    original_sha = file_sha256(media_path)
    root = Path(directory).absolute()
    require(not root.exists() and not any(p.is_symlink() for p in (root, *root.parents)),
            'animation_observation_no_overwrite', 409)
    require(type(sample_fps) in (int, float) and math.isfinite(sample_fps)
            and .25 <= sample_fps <= 12 and type(frame_limit) is int
            and 2 <= frame_limit <= MAX_FRAMES)
    try:
        metadata = json.loads(_run([ffprobe, '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height:format=duration', '-of', 'json', str(media_path)]))
        stream = metadata['streams'][0]
        duration = float(metadata['format']['duration'])
        width, height = int(stream['width']), int(stream['height'])
    except (ValueError, TypeError, KeyError, IndexError):
        raise ServiceError('animation_observation_media_invalid', 422) from None
    media = dict(sha256=original_sha, duration_seconds=duration, width=width, height=height)
    windows = deepcopy(windows if windows is not None else
                       [dict(start_seconds=0, end_seconds=duration)])
    require(type(windows) is list and 1 <= len(windows) <= MAX_FRAMES)
    times, previous = [], -1
    for window in windows:
        require(type(window) is dict and set(window) == {'start_seconds', 'end_seconds'})
        start, end = window['start_seconds'], window['end_seconds']
        require(all(type(n) in (int, float) and math.isfinite(n) for n in (start, end))
                and 0 <= start < end <= duration and start >= previous)
        previous = end
        count = math.ceil((end - start) * sample_fps - 1e-9)
        require(len(times) + count <= frame_limit, 'animation_observation_select_shorter_windows')
        times.extend(round(start + i / sample_fps, 6) for i in range(count))
    # Validate metadata, identifiers and windows before creating files.
    review = source_sha256 is not None
    profile = REVIEW_PROFILE if review else OBSERVE_PROFILE
    input_name = REVIEW_INPUT_NAME if review else OBSERVE_INPUT_NAME
    document = dict(schema=REVIEW_INPUT_SCHEMA if review else OBSERVE_INPUT_SCHEMA,
        clip_id=clip_id, goal=goal, media=media, prompt_revision='visual-evidence-r1',
        observation=dict(coverage='sampled_frames', windows=windows, frames=[dict(
            frame_id='frame-%03d' % i, file_name='frame-%03d.jpg' % i,
            time_seconds=time, sha256='0' * 64) for i,time in enumerate(times)],
            limitations=[
                'Ordered sampled still frames only; unsampled movement and whole-video playback were not observed.',
                'Frame times are the requested decode sampling grid, not verified original frame PTS.',
                'No audio is supplied; speech naturalness and audiovisual timing are not evaluated.'],
            audio_available=False))
    if review:
        document['source_sha256'] = source_sha256
    validator = validate_review_input if review else validate_observation_input
    validator(document)
    (root / 'inputs').mkdir(parents=True)
    frame_total = 0
    for frame in document['observation']['frames']:
        target = root / 'inputs' / frame['file_name']
        # Accurate output-side seek; one decoded frame, bounded dimensions and no audio.
        _run([ffmpeg, '-v', 'error', '-nostdin', '-i', str(media_path), '-ss',
            str(frame['time_seconds']), '-map', '0:v:0', '-frames:v', '1', '-an',
            '-vf', "scale='min(960,iw)':-2", '-q:v', '4', '-threads', '1', str(target)])
        raw = _file(target, MAX_IMAGE_BYTES).read_bytes()
        frame['sha256'] = hashlib.sha256(raw).hexdigest()
        frame_total += len(raw)
        require(frame_total <= MAX_FRAME_TOTAL_BYTES, 'animation_observation_image_budget')
    require(file_sha256(media_path) == original_sha, 'animation_observation_media_changed')
    raw = canonical_bytes(validator(document))
    (root / 'inputs' / input_name).write_bytes(raw)
    checksum = hashlib.sha256(raw).hexdigest()
    implementation(profile).validate_inputs(root, {'input_sha256': checksum})
    receipt = dict(schema='video.animation-observation-preparation.v1', profile=profile,
        input_name=input_name, input_sha256=checksum, media_sha256=original_sha,
        frames=len(times), total_image_bytes=frame_total, model_calls=0,
        observation_status='prepared_not_observed', input_directory='inputs')
    (root / 'evidence.json').write_bytes(canonical_bytes(receipt))
    return dict(document=document, receipt=receipt)


def load_bundle(directory):
    root = Path(directory).absolute()
    receipt = json.loads(_file(root / 'evidence.json', 16384).read_bytes())
    require(receipt.get('schema') == 'video.animation-observation-preparation.v1')
    profile = receipt.get('profile')
    require(profile in (OBSERVE_PROFILE, REVIEW_PROFILE))
    name = REVIEW_INPUT_NAME if profile == REVIEW_PROFILE else OBSERVE_INPUT_NAME
    raw = _file(root / 'inputs' / name, 128 * 1024).read_bytes()
    require(receipt.get('input_name') == name and hashlib.sha256(raw).hexdigest() ==
            receipt.get('input_sha256'), 'animation_observation_input_changed')
    implementation(profile).validate_inputs(root, {'input_sha256':receipt['input_sha256']})
    document = json.loads(raw)
    files = {name:raw}
    for frame in document['observation']['frames']:
        files[frame['file_name']] = _file(root / 'inputs' / frame['file_name'], MAX_IMAGE_BYTES).read_bytes()
        require(hashlib.sha256(files[frame['file_name']]).hexdigest() == frame['sha256'],
                'animation_observation_input_changed')
    require(sum(len(v) for k,v in files.items() if k != name) <= MAX_FRAME_TOTAL_BYTES,
            'animation_observation_image_budget')
    return profile, document, files


def validate_model_frame_receipt(document, receipt):
    """Require actual proxy image-byte evidence, never creator 'I read it' text.

    Receipt construction belongs to the trusted Agent model gateway. The
    business runner must obtain it through its authenticated control path.
    """
    validator = validate_review_input if document.get('schema') == REVIEW_INPUT_SCHEMA else validate_observation_input
    document = validator(document)
    frames = document['observation']['frames']
    expected = {frame['sha256'] for frame in frames}
    require(type(receipt) is dict and set(receipt) == {'schema', 'job_id', 'attempt_id',
                'request_sha256', 'evidence_scope', 'images'}
            and receipt.get('schema') == 'agent-model-image-evidence.v1'
            and receipt.get('evidence_scope') == 'completed_upstream_transport'
            and type(receipt.get('images')) is list and len(receipt['images']) <= 10000,
            'animation_model_frame_evidence_missing', 409)
    try:
        require(all(type(receipt[key]) is str and str(UUID(receipt[key])) == receipt[key]
                    for key in ('job_id', 'attempt_id')), 'animation_model_frame_evidence_missing', 409)
    except (ValueError, AttributeError, TypeError):
        raise ServiceError('animation_model_frame_evidence_missing', 409) from None
    def sha(value):
        return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None
    require(sha(receipt['request_sha256']), 'animation_model_frame_evidence_missing', 409)
    actual = set()
    for row in receipt['images']:
        require(type(row) is dict and set(row) == {'sha256', 'model_request_completed',
                    'model_request_sha256', 'media_type', 'byte_length', 'input_item_index', 'part_index'}
                and sha(row['sha256']) and sha(row['model_request_sha256'])
                and row['media_type'] in ('image/png', 'image/jpeg', 'image/webp')
                and type(row['byte_length']) is int and 0 < row['byte_length'] <= MAX_IMAGE_BYTES
                and all(type(row[key]) is int and 0 <= row[key] < 2000
                        for key in ('input_item_index', 'part_index'))
                and type(row['model_request_completed']) is bool,
                'animation_model_frame_evidence_invalid', 409)
        if row['model_request_completed']:
            actual.add(row['sha256'])
    require(expected <= actual, 'animation_model_frame_evidence_incomplete', 409)
    return dict(schema='video.animation-observed-frames.v1', input_sha256=digest(document),
        media_sha256=document['media']['sha256'], observed_frame_ids=[f['frame_id'] for f in frames],
        model_evidence_sha256=digest(receipt), coverage='sampled_frames',
        evidence_scope='visual_only', model_evidence_scope='completed_upstream_transport',
        model_understanding_verified=False, whole_video_watched=False, audio_observed=False)
