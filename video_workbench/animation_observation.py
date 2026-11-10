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
    OBSERVE_PROFILE, REVIEW_PROFILE, REVIEW_SEQUENCE_PROFILE, OBSERVE_INPUT_NAME, REVIEW_INPUT_NAME,
    OBSERVE_INPUT_SCHEMA, REVIEW_INPUT_SCHEMA, MAX_FRAMES, MAX_IMAGE_BYTES,
    validate_observation_input, validate_review_input)


MAX_MEDIA_BYTES = 512 * 1024 * 1024
# Leave room in the shared 8-MiB model request for base64, tools and history.
MAX_FRAME_TOTAL_BYTES = 3 * 1024 * 1024
TEMPORAL_PLAN_SCHEMA = 'video.animation-temporal-sampling.v1'
TEMPORAL_PLAN_NAME = 'sampling-plan.json'


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


def temporal_sampling_plan(duration_seconds, sampling_layers, *, frame_limit=MAX_FRAMES):
    """Allocate explicit overview/detail grids without selecting issues for an Agent.

    Layers may overlap. Their union is observable scope; duplicate requested
    timestamps consume one slot. Exceeding the budget is an error, never a
    silent truncation or an automatically changed sampling rate.
    """
    require(type(duration_seconds) in (int, float) and math.isfinite(duration_seconds)
            and .001 <= duration_seconds <= 86400)
    require(type(frame_limit) is int and 2 <= frame_limit <= MAX_FRAMES
            and type(sampling_layers) is list and 1 <= len(sampling_layers) <= MAX_FRAMES)
    layers = deepcopy(sampling_layers)
    times = set()
    for layer in layers:
        require(type(layer) is dict and set(layer) ==
                {'start_seconds', 'end_seconds', 'sample_fps'})
        start, end, fps = (layer[key] for key in
                           ('start_seconds', 'end_seconds', 'sample_fps'))
        require(all(type(n) in (int, float) and math.isfinite(n) for n in (start, end, fps))
                and 0 <= start < end <= duration_seconds and .25 <= fps <= 12)
        count = math.ceil((end - start) * fps - 1e-9)
        # A layer cannot fit if it alone exceeds the total unique-time budget.
        require(count <= frame_limit, 'animation_observation_select_shorter_windows')
        times.update(round(start + i / fps, 6) for i in range(count))
        require(len(times) <= frame_limit, 'animation_observation_select_shorter_windows')
    windows = []
    for layer in sorted(layers, key=lambda row: (row['start_seconds'], row['end_seconds'])):
        start, end = layer['start_seconds'], layer['end_seconds']
        if windows and start <= windows[-1]['end_seconds']:
            windows[-1]['end_seconds'] = max(windows[-1]['end_seconds'], end)
        else:
            windows.append(dict(start_seconds=start, end_seconds=end))
    return dict(schema=TEMPORAL_PLAN_SCHEMA, layers=layers, frame_limit=frame_limit,
                windows=windows, requested_times=sorted(times))


def _decode_presentation_frame(media_path, target, requested_time, *, ffmpeg):
    """Decode one selected frame and record its presentation timestamp.

    ``select`` runs before the single-frame output limit. ``showinfo`` therefore
    describes the first selected output frame, unlike an output-side seek that
    merely labels a request time. The timestamp is FFmpeg's normalized input
    timeline, not proof of observing motion between frames.
    """
    selector = 'select=gte(t\\,%s),scale=\'min(960,iw)\':-2,showinfo' % requested_time
    try:
        response = subprocess.run([ffmpeg, '-v', 'info', '-nostdin', '-i', str(media_path),
            '-map', '0:v:0', '-vf', selector, '-frames:v', '1', '-an',
            '-fps_mode', 'passthrough', '-q:v', '4', '-threads', '1', str(target)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ServiceError('animation_observation_decoder_unavailable', 503) from None
    require(response.returncode == 0 and len(response.stdout) <= 1024 * 1024
            and len(response.stderr) <= 1024 * 1024,
            'animation_observation_decode_failed')
    log = response.stderr.decode('utf-8', errors='replace')
    bases = re.findall(r'^\[Parsed_showinfo_(\d+) @ ([^\]]+)\]\s+config in time_base:\s*(\d+)/(\d+)',
                       log, re.MULTILINE)
    firsts = re.findall(r'^\[Parsed_showinfo_(\d+) @ ([^\]]+)\]\s+n:\s*0\s+pts:\s*(-?\d+)\s+pts_time:',
                        log, re.MULTILINE)
    require(len(bases) == 1 and len(firsts) == 1 and bases[0][:2] == firsts[0][:2],
            'animation_observation_timestamp_unverified')
    numerator, denominator, pts = int(bases[0][2]), int(bases[0][3]), int(firsts[0][2])
    require(numerator > 0 and denominator > 0 and pts >= 0,
            'animation_observation_timestamp_unverified')
    actual_time = pts * numerator / denominator
    require(math.isfinite(actual_time) and actual_time + 1e-9 >= requested_time,
            'animation_observation_timestamp_unverified')
    return dict(pts=pts, time_base=[numerator, denominator], time_seconds=actual_time)


def prepare_temporal_media(media_path, directory, *, clip_id, goal, sampling_layers,
                           source_sha256=None, frame_limit=MAX_FRAMES,
                           ffmpeg='ffmpeg', ffprobe='ffprobe', review_profile=REVIEW_PROFILE):
    """Prepare explicit multi-density evidence with measured frame timestamps.

    A caller chooses the overview and detail windows. This helper neither
    guesses an issue location nor changes the caller's requested budget. New
    temporal bundles retain a plan and all request-to-presentation mappings;
    repeated requests for the same presentation frame are disclosed aliases.
    """
    return _prepare_media(media_path, directory, clip_id=clip_id, goal=goal,
        source_sha256=source_sha256, windows=None, sample_fps=3, frame_limit=frame_limit,
        ffmpeg=ffmpeg, ffprobe=ffprobe, sampling_layers=sampling_layers, review_profile=review_profile)


def prepare_sampled_media(media_path, directory, *, clip_id, goal, source_sha256=None,
                          windows=None, sample_fps=3, frame_limit=MAX_FRAMES,
                          ffmpeg='ffmpeg', ffprobe='ffprobe', review_profile=REVIEW_PROFILE):
    """Decode local media into a new immutable input bundle, without model I/O.

    Times label the sampling grid. Decoder frame selection can differ from an
    original presentation timestamp, which is explicitly disclosed. Longer
    references require selected windows instead of a silent first-N truncation.
    """
    return _prepare_media(media_path, directory, clip_id=clip_id, goal=goal,
        source_sha256=source_sha256, windows=windows, sample_fps=sample_fps,
        frame_limit=frame_limit, ffmpeg=ffmpeg, ffprobe=ffprobe, review_profile=review_profile)


def _prepare_media(media_path, directory, *, clip_id, goal, source_sha256, windows,
                   sample_fps, frame_limit, ffmpeg, ffprobe, sampling_layers=None,
                   review_profile=REVIEW_PROFILE):
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
    temporal = sampling_layers is not None
    plan = None
    if temporal:
        plan = temporal_sampling_plan(duration, sampling_layers, frame_limit=frame_limit)
        windows, times = plan['windows'], plan['requested_times']
    else:
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
    require(review_profile in (REVIEW_PROFILE, REVIEW_SEQUENCE_PROFILE)
            and (review or review_profile == REVIEW_PROFILE), 'animation_observation_profile_invalid')
    profile = review_profile if review else OBSERVE_PROFILE
    input_name = REVIEW_INPUT_NAME if review else OBSERVE_INPUT_NAME
    document = dict(schema=REVIEW_INPUT_SCHEMA if review else OBSERVE_INPUT_SCHEMA,
        clip_id=clip_id, goal=goal, media=media,
        prompt_revision='visual-temporal-r1' if temporal else 'visual-evidence-r1',
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
    decoded_frames, requests = [], []
    for frame in document['observation']['frames']:
        target = root / 'inputs' / frame['file_name']
        if temporal:
            requested_time = frame['time_seconds']
            measured = _decode_presentation_frame(media_path, target, requested_time, ffmpeg=ffmpeg)
            actual_time = measured['time_seconds']
            require(actual_time <= duration and any(window['start_seconds'] <= actual_time
                    <= window['end_seconds'] for window in windows),
                    'animation_observation_sampling_outside_window')
            alias = bool(decoded_frames and actual_time == decoded_frames[-1]['time_seconds'])
            # Static pixels at different PTS are separate samples. Only an exact
            # presentation-frame alias is removed; do not deduplicate by image SHA.
            requests.append(dict(requested_time_seconds=requested_time, **measured,
                frame_id=decoded_frames[-1]['frame_id'] if alias else frame['frame_id'], alias=alias))
            if alias:
                _file(target, MAX_IMAGE_BYTES).unlink()
                continue
            require(not decoded_frames or actual_time > decoded_frames[-1]['time_seconds'],
                    'animation_observation_timestamp_order')
            frame['time_seconds'] = actual_time
        else:
            # Legacy sampling-grid labels remain unchanged for frozen callers.
            _run([ffmpeg, '-v', 'error', '-nostdin', '-i', str(media_path), '-ss',
                str(frame['time_seconds']), '-map', '0:v:0', '-frames:v', '1', '-an',
                '-vf', "scale='min(960,iw)':-2", '-q:v', '4', '-threads', '1', str(target)])
        raw = _file(target, MAX_IMAGE_BYTES).read_bytes()
        frame['sha256'] = hashlib.sha256(raw).hexdigest()
        decoded_frames.append(frame)
        frame_total += len(raw)
        require(frame_total <= MAX_FRAME_TOTAL_BYTES, 'animation_observation_image_budget')
    if temporal:
        document['observation']['frames'] = decoded_frames
        plan.update(media_sha256=original_sha, requests=requests)
        plan_raw = canonical_bytes(plan)
        plan_sha = hashlib.sha256(plan_raw).hexdigest()
        (root / TEMPORAL_PLAN_NAME).write_bytes(plan_raw)
        document['observation']['limitations'][1] = (
            'Frame times are measured FFmpeg normalized presentation timestamps; '
            'original container timeline and unsampled movement are not verified.')
        document['observation']['limitations'].extend([
            'Temporal sampling plan SHA256: ' + plan_sha,
            'Explicit sampling layers (start/end seconds, requested fps): ' +
                json.dumps(plan['layers'], separators=(',', ':')),
            '%d requested timestamps produced %d unique presentation frames; %d aliases are '
                'not additional observations. Unequal time intervals must be read explicitly.' %
                (len(times), len(decoded_frames), len(times) - len(decoded_frames))])
    require(file_sha256(media_path) == original_sha, 'animation_observation_media_changed')
    raw = canonical_bytes(validator(document))
    (root / 'inputs' / input_name).write_bytes(raw)
    checksum = hashlib.sha256(raw).hexdigest()
    implementation(profile).validate_inputs(root, {'input_sha256': checksum})
    receipt = dict(schema='video.animation-observation-preparation.v1', profile=profile,
        input_name=input_name, input_sha256=checksum, media_sha256=original_sha,
        frames=len(decoded_frames), total_image_bytes=frame_total, model_calls=0,
        observation_status='prepared_not_observed', input_directory='inputs')
    if temporal:
        receipt.update(sampling_plan_file=TEMPORAL_PLAN_NAME, sampling_plan_sha256=plan_sha,
                       requested_samples=len(times), aliased_samples=len(times) - len(decoded_frames))
    (root / 'evidence.json').write_bytes(canonical_bytes(receipt))
    return dict(document=document, receipt=receipt)


def _validate_temporal_plan(root, receipt, document):
    """Keep the separately stored selection plan bound to uploaded evidence."""
    present = {'sampling_plan_file', 'sampling_plan_sha256', 'requested_samples', 'aliased_samples'}
    if not (present & set(receipt) or (root / TEMPORAL_PLAN_NAME).exists()):
        return
    require(present <= set(receipt) and receipt['sampling_plan_file'] == TEMPORAL_PLAN_NAME,
            'animation_observation_sampling_plan_changed')
    require(type(receipt['sampling_plan_sha256']) is str
            and re.fullmatch('[0-9a-f]{64}', receipt['sampling_plan_sha256']) is not None
            and all(type(receipt[key]) is int for key in
                    ('requested_samples', 'aliased_samples', 'frames'))
            and 1 <= receipt['frames'] <= receipt['requested_samples'] <= MAX_FRAMES
            and 0 <= receipt['aliased_samples'] < receipt['requested_samples'],
            'animation_observation_sampling_plan_changed')
    raw = _file(root / TEMPORAL_PLAN_NAME, 32768).read_bytes()
    checksum = hashlib.sha256(raw).hexdigest()
    require(receipt['sampling_plan_sha256'] == checksum and
            'Temporal sampling plan SHA256: ' + checksum in document['observation']['limitations'],
            'animation_observation_sampling_plan_changed')
    try:
        plan = json.loads(raw)
    except ValueError:
        raise ServiceError('animation_observation_sampling_plan_changed', 422) from None
    require(type(plan) is dict and set(plan) == {'schema', 'layers', 'frame_limit', 'windows',
            'requested_times', 'media_sha256', 'requests'} and plan['schema'] == TEMPORAL_PLAN_SCHEMA,
            'animation_observation_sampling_plan_changed')
    rebuilt = temporal_sampling_plan(document['media']['duration_seconds'], plan['layers'],
                                     frame_limit=plan['frame_limit'])
    require(all(plan[key] == value for key, value in rebuilt.items())
            and plan['media_sha256'] == document['media']['sha256']
            and plan['windows'] == document['observation']['windows']
            and type(plan['requests']) is list
            and len(plan['requests']) == len(plan['requested_times'])
            and receipt['requested_samples'] == len(plan['requests']),
            'animation_observation_sampling_plan_changed')
    frames = {row['frame_id']: row for row in document['observation']['frames']}
    first_refs, aliases, previous_time, previous_ref = [], 0, -1, None
    for row, requested in zip(plan['requests'], plan['requested_times']):
        require(type(row) is dict and set(row) == {'requested_time_seconds', 'pts', 'time_base',
                'time_seconds', 'frame_id', 'alias'} and row['requested_time_seconds'] == requested
                and type(row['pts']) is int and row['pts'] >= 0
                and type(row['time_base']) is list and len(row['time_base']) == 2
                and all(type(n) is int and n > 0 for n in row['time_base'])
                and type(row['time_seconds']) in (int, float) and math.isfinite(row['time_seconds'])
                and row['time_seconds'] == row['pts'] * row['time_base'][0] / row['time_base'][1]
                and row['time_seconds'] + 1e-9 >= requested
                and type(row['frame_id']) is str and row['frame_id'] in frames
                and row['time_seconds'] == frames[row['frame_id']]['time_seconds']
                and type(row['alias']) is bool, 'animation_observation_sampling_plan_changed')
        require(row['alias'] == (row['time_seconds'] == previous_time)
                and (row['frame_id'] == previous_ref if row['alias'] else
                     row['time_seconds'] > previous_time), 'animation_observation_sampling_plan_changed')
        if row['alias']:
            aliases += 1
        else:
            first_refs.append(row['frame_id'])
        previous_time, previous_ref = row['time_seconds'], row['frame_id']
    require(first_refs == list(frames) and receipt['aliased_samples'] == aliases
            and receipt['frames'] == len(frames), 'animation_observation_sampling_plan_changed')


def load_bundle(directory):
    root = Path(directory).absolute()
    receipt = json.loads(_file(root / 'evidence.json', 16384).read_bytes())
    require(receipt.get('schema') == 'video.animation-observation-preparation.v1')
    profile = receipt.get('profile')
    require(profile in (OBSERVE_PROFILE, REVIEW_PROFILE, REVIEW_SEQUENCE_PROFILE))
    name = REVIEW_INPUT_NAME if profile in (REVIEW_PROFILE, REVIEW_SEQUENCE_PROFILE) else OBSERVE_INPUT_NAME
    raw = _file(root / 'inputs' / name, 128 * 1024).read_bytes()
    require(receipt.get('input_name') == name and hashlib.sha256(raw).hexdigest() ==
            receipt.get('input_sha256'), 'animation_observation_input_changed')
    implementation(profile).validate_inputs(root, {'input_sha256':receipt['input_sha256']})
    document = json.loads(raw)
    _validate_temporal_plan(root, receipt, document)
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
