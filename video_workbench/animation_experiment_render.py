"""Bounded native media rendering of untrusted lab scenes in one isolated Docker job.

This is an opt-in developer executor, not a production compiler. Scene scripts
are never served to the authenticated lab page. The Docker boundary is shared
with the existing native capture executor; its measured output remains separate
from developer effect judgments and production admission.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import uuid

from . import animation_lab_capture as docker


MAX_FRAMES = 240
MAX_MEDIA_BYTES = 32 * 1024 * 1024
MAX_EVIDENCE_BYTES = 96 * 1024 * 1024
MAX_TIMEOUT = 210
SCENE_FILES = frozenset({'scene.html', 'scene.js', 'scene.css'})
ASSET_FILES = frozenset({'gsap.min.js', 'three.module.min.js', 'NotoSansCJK-Regular.ttc'})
HOST = Path(__file__).with_name('animation_experiment_host.mjs')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_IMAGE = re.compile(r'([a-z0-9][a-z0-9._:/-]*):latest@(sha256:[0-9a-f]{64})\Z')


class RenderExecutionError(RuntimeError):
    def __init__(self, outcome):
        self.outcome = outcome
        super().__init__(outcome['error'])


def _require(value, code):
    if not value:
        raise ValueError('animation_experiment_' + code)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _read(path, limit):
    path = Path(path)
    _require(path.is_file() and not path.is_symlink() and path.stat().st_nlink == 1
             and path.stat().st_size <= limit, 'render_file_invalid')
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    _require(len(raw) <= limit, 'render_file_invalid')
    return raw


def _write(path, raw):
    with Path(path).open('xb') as stream:
        stream.write(raw)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _constraints(value):
    _require(type(value) is dict, 'render_constraints_invalid')
    width, height, fps, duration = (value.get(key) for key in
                                  ('width', 'height', 'fps', 'duration_seconds'))
    _require(type(width) is int and 320 <= width <= 1280 and type(height) is int
             and 180 <= height <= 720 and fps == 24 and type(fps) is int
             and _number(duration) and 1 <= duration <= 10
             and duration * fps == int(duration * fps), 'render_budget_invalid')
    return value


def prepare_render(bundle_dir, output_dir, *, image, input_document, timeout=180, assets_dir=None):
    """Freeze a new run directory without contacting Docker or executing code."""
    source = Path(bundle_dir).absolute()
    destination = Path(output_dir).absolute()
    _require(source.is_dir() and not source.is_symlink(), 'render_bundle_invalid')
    _require(not destination.exists() and not destination.is_symlink(), 'render_no_overwrite')
    for path in (source, destination):
        _require(not any(char in str(path) for char in (',', '\r', '\n', '\0')),
                 'render_mount_path_invalid')
    match = _IMAGE.fullmatch(image) if type(image) is str else None
    _require(match is not None, 'render_image_pin_required')
    _require(_number(timeout) and 30 <= timeout <= MAX_TIMEOUT, 'render_timeout_invalid')
    _require(type(input_document) is dict
             and input_document.get('schema') == 'video.animation-experiment-input.v1',
             'render_input_invalid')
    constraints = _constraints(input_document.get('constraints'))
    names = {path.name for path in source.iterdir()}
    _require('scene.html' in names and names <= SCENE_FILES, 'render_bundle_files_invalid')
    files = {name: _read(source / name, 512 * 1024) for name in sorted(names)}
    _require(sum(map(len, files.values())) <= 1024 * 1024, 'render_bundle_size_invalid')
    declared_assets = input_document.get('assets')
    _require(type(declared_assets) is dict and declared_assets.keys() <= ASSET_FILES
             and all(type(value) is str and _SHA.fullmatch(value)
                     for value in declared_assets.values()), 'render_assets_invalid')
    assets = {}
    if declared_assets:
        _require(assets_dir is not None, 'render_assets_missing')
        root = Path(assets_dir).absolute()
        _require(root.is_dir() and not root.is_symlink(), 'render_assets_invalid')
        for name, expected in declared_assets.items():
            raw = _read(root / name, 32 * 1024 * 1024)
            _require(digest(raw) == expected, 'render_asset_digest_mismatch')
            assets[name] = raw
    host = _read(HOST, 1024 * 1024)
    request = dict(schema='video.animation-experiment-render-request.v1',
        input_sha256=digest(canonical(input_document)), constraints=constraints,
        files_sha256={name: digest(raw) for name, raw in files.items()},
        assets_sha256={name: digest(raw) for name, raw in assets.items()},
        host_sha256=digest(host), image_digest=match[2], fps=24,
        max_frames=MAX_FRAMES, max_media_bytes=MAX_MEDIA_BYTES,
        max_evidence_bytes=MAX_EVIDENCE_BYTES,
        output='silent_native_frames_and_mp4', production_admitted=False)
    request['identity'] = digest(canonical(request))
    plan = dict(schema='video.animation-experiment-render-plan.v1', directory=str(destination),
        image=image, repository=match[1], image_digest=match[2], timeout_seconds=timeout,
        request_sha256=digest(canonical(request)), request_identity=request['identity'],
        input_sha256=request['input_sha256'], host_sha256=digest(host),
        network='none', uid=10001, read_only_root=True, automatic_retry=False,
        renderer='native_seek_screenshots_ffmpeg_not_hyperframes_export')
    destination.mkdir(parents=True)
    (destination / 'input' / 'scene' / 'assets').mkdir(parents=True)
    (destination / 'adapters').mkdir()
    for name, raw in files.items():
        _write(destination / 'input' / 'scene' / name, raw)
    for name, raw in assets.items():
        _write(destination / 'input' / 'scene' / 'assets' / name, raw)
    _write(destination / 'input' / 'request.json', canonical(request))
    _write(destination / 'input-document.json', canonical(input_document))
    _write(destination / 'adapters' / HOST.name, host)
    _write(destination / 'render-plan.json', canonical(plan))
    return plan


def _frozen(plan):
    root = Path(plan['directory'])
    request_raw = _read(root / 'input' / 'request.json', 64 * 1024)
    _require(digest(request_raw) == plan['request_sha256'], 'render_request_changed')
    request = json.loads(request_raw)
    _require(request['identity'] == digest(canonical({key: value for key, value in request.items()
                                                   if key != 'identity'})), 'render_request_changed')
    _require(digest(_read(root / 'input-document.json', 256 * 1024)) == plan['input_sha256'],
             'render_input_changed')
    _require(digest(_read(root / 'adapters' / HOST.name, 1024 * 1024)) == plan['host_sha256'],
             'render_host_changed')
    for name, expected in request['files_sha256'].items():
        _require(digest(_read(root / 'input' / 'scene' / name, 512 * 1024)) == expected,
                 'render_scene_changed')
    for name, expected in request['assets_sha256'].items():
        _require(digest(_read(root / 'input' / 'scene' / 'assets' / name, 32 * 1024 * 1024)) == expected,
                 'render_asset_changed')
    return request


def validate_observations(observations, constraints):
    """Independent pilot geometry checks; measured occlusion remains a metric."""
    issues, metrics = [], {}
    _require(type(observations) is dict and observations.get('schema')
             == 'video.animation-experiment-observations.v1', 'render_observations_invalid')
    samples = observations.get('samples')
    expected_count = int(constraints['duration_seconds'] * constraints['fps'])
    _require(type(samples) is list and len(samples) == expected_count,
             'render_observation_count_invalid')
    _require(all(type(row) is dict and row.get('frame_index') == index
                 and _number(row.get('time')) and abs(row['time'] - index / 24) < 1e-9
                 and type(row.get('objects')) is list for index, row in enumerate(samples)),
             'render_observation_sequence_invalid')
    if observations.get('reverse_initial_sha256') != samples[0].get('frame_sha256'):
        issues.append('reverse_seek_pixels_differ')
    if observations.get('reverse_initial_objects') != samples[0]['objects']:
        issues.append('reverse_seek_geometry_differs')
    objects = constraints.get('objects')
    if type(objects) is not list or not objects:
        return dict(status='pending_goal_review', issues=issues,
                    metrics=metrics, scope='runtime_and_reverse_seek_only',
                    aesthetic_approval=False)
    ids = [item['id'] for item in objects]
    _require(len(ids) == len(set(ids)), 'render_goal_objects_invalid')
    validation = constraints.get('validation', {})
    tolerance = validation.get('position_tolerance_px', 2)
    max_step = validation.get('max_step_px', 112)
    _require(_number(tolerance) and 0 <= tolerance <= 10 and _number(max_step)
             and 1 <= max_step <= 500, 'render_goal_threshold_invalid')
    stationary = constraints.get('stationary_until_seconds', 0)
    complete = constraints.get('movement_complete_seconds', constraints['duration_seconds'] - 1 / 24)
    _require(_number(stationary) and _number(complete)
             and 0 <= stationary < complete < constraints['duration_seconds'],
             'render_goal_timing_invalid')
    travelled = {identity: 0.0 for identity in ids}
    previous = {}
    overlap_peak, largest_step = 0.0, 0.0
    for sample in samples:
        measured = sample['objects']
        present = [item.get('id') for item in measured]
        if sorted(present) != sorted(ids):
            issues.append('identity_missing_or_duplicated')
            continue
        by_id = {item['id']: item for item in measured}
        for target in objects:
            identity = target['id']
            actual = by_id[identity]
            bounds = actual.get('bounds', {})
            if not all(_number(bounds.get(key)) for key in ('x', 'y', 'width', 'height')):
                issues.append('object_bounds_invalid')
                continue
            x, y = bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2
            if (actual.get('visible') is not True or actual.get('text') != target['label']
                    or bounds['width'] < 1 or bounds['height'] < 1):
                issues.append('object_identity_not_visible')
            if (bounds['x'] < -tolerance or bounds['y'] < -tolerance
                    or bounds['x'] + bounds['width'] > constraints['width'] + tolerance
                    or bounds['y'] + bounds['height'] > constraints['height'] + tolerance):
                issues.append('object_outside_frame')
            point = target['initial'] if sample['time'] <= stationary else (
                target['final'] if sample['time'] >= complete else None)
            if point is not None:
                if abs(x - point[0]) > tolerance or abs(y - point[1]) > tolerance:
                    issues.append('initial_or_final_position_mismatch')
                if (abs(bounds['width'] - target['width']) > tolerance
                        or abs(bounds['height'] - target['height']) > tolerance):
                    issues.append('initial_or_final_size_mismatch')
            if identity in previous:
                step = math.hypot(x - previous[identity][0], y - previous[identity][1])
                travelled[identity] += step
                largest_step = max(largest_step, step)
                if step > max_step:
                    issues.append('movement_discontinuity')
            previous[identity] = (x, y)
        for index, left in enumerate(measured):
            for right in measured[index + 1:]:
                a, b = left['bounds'], right['bounds']
                intersection = (max(0, min(a['x'] + a['width'], b['x'] + b['width']) - max(a['x'], b['x']))
                                * max(0, min(a['y'] + a['height'], b['y'] + b['height']) - max(a['y'], b['y'])))
                area = min(a['width'] * a['height'], b['width'] * b['height'])
                if area > 0:
                    overlap_peak = max(overlap_peak, intersection / area)
    for target in objects:
        required = math.dist(target['initial'], target['final'])
        if required > tolerance and travelled[target['id']] < required - tolerance:
            issues.append('object_did_not_move')
    metrics.update(travelled_px=travelled, largest_frame_step_px=largest_step,
                   peak_bounding_box_overlap_ratio=overlap_peak,
                   overlap_is_measurement_not_preference=True)
    return dict(status='failed' if issues else 'passed', issues=sorted(set(issues)),
                metrics=metrics, scope='pilot_dom_geometry_and_rendered_frames', aesthetic_approval=False)


def _outputs(root, request, plan):
    work = root / 'work'
    receipt_raw = _read(work / 'render-receipt.json', 128 * 1024)
    receipt = json.loads(receipt_raw)
    _require(receipt.get('schema') == 'video.animation-experiment-render-receipt.v1'
             and receipt.get('request_sha256') == plan['request_sha256']
             and receipt.get('request_identity') == plan['request_identity']
             and receipt.get('host_sha256') == plan['host_sha256']
             and receipt.get('status') == 'rendered'
             and receipt.get('decode_verified') is True, 'render_receipt_invalid')
    media = _read(work / 'output.mp4', MAX_MEDIA_BYTES)
    _require(media[4:8] == b'ftyp' and digest(media) == receipt.get('output_sha256'),
             'render_media_invalid')
    raw = _read(work / 'observations.json', 8 * 1024 * 1024)
    _require(digest(raw) == receipt.get('observations_sha256'), 'render_observations_mismatch')
    observations = json.loads(raw)
    frames, total = receipt.get('frames'), len(media)
    _require(type(frames) is list and len(frames) == int(request['constraints']['duration_seconds'] * 24) + 1,
             'render_frames_invalid')
    expected_names = []
    for index, row in enumerate(frames):
        name = ('frame-%06d.jpg' % index) if index < len(frames) - 1 else 'reverse-initial.jpg'
        _require(type(row) is dict and row.get('name') == name, 'render_frame_name_invalid')
        content = _read(work / 'frames' / name, 4 * 1024 * 1024)
        _require(content[:2] == b'\xff\xd8' and content[-2:] == b'\xff\xd9'
                 and digest(content) == row.get('sha256') and len(content) == row.get('bytes'),
                 'render_frame_digest_mismatch')
        if index < len(frames) - 1:
            _require(observations['samples'][index].get('frame_sha256') == row['sha256'],
                     'render_observation_frame_mismatch')
        else:
            _require(observations.get('reverse_initial_sha256') == row['sha256'],
                     'render_observation_frame_mismatch')
        expected_names.append(name)
        total += len(content)
    _require(total <= MAX_EVIDENCE_BYTES and {path.name for path in (work / 'frames').iterdir()}
             == set(expected_names), 'render_evidence_budget_invalid')
    _require(set(path.name for path in work.iterdir()) <=
             {'frames', 'observations.json', 'render-receipt.json', 'output.mp4'},
             'render_extra_output')
    technical = validate_observations(observations, request['constraints'])
    return receipt, technical


def execute_render(output_dir):
    """Execute one prepared run; durable failures are never retried or overwritten."""
    root = Path(output_dir).absolute()
    plan = json.loads(_read(root / 'render-plan.json', 64 * 1024))
    _require(plan.get('directory') == str(root), 'render_directory_mismatch')
    _require(sys.platform == 'linux', 'render_linux_required')
    _require(not (root / 'render-start.json').exists(), 'render_no_retry')
    request = _frozen(plan)
    token = uuid.uuid4().hex
    name = 'animation-experiment-' + request['identity'][:12] + '-' + token[:12]
    now = lambda: datetime.now(timezone.utc).isoformat()
    start = dict(schema='video.animation-experiment-render-start.v1', started_at=now(),
                 container_name=name, owner_token=token, plan=plan)
    _write(root / 'render-start.json', canonical(start))
    began = time.monotonic()
    deadline = began + plan['timeout_seconds']
    run_deadline = deadline - 15
    image_id = container_id = None
    created, error, result = False, None, None
    cleanup = dict(status='not_created', container_id=None)
    try:
        actual = json.loads(docker._checked(['image', 'inspect', '--format', docker._IMAGE_FORMAT,
                                            plan['image']], run_deadline))
        _require(re.fullmatch(r'sha256:[0-9a-f]{64}', actual.get('image_id', ''))
                 and plan['repository'] + '@' + plan['image_digest'] in actual.get('repo_digests', []),
                 'render_actual_image_mismatch')
        image_id = actual['image_id']
        work = root / 'work'
        work.mkdir()
        docker._ownership(work, directory=True)
        # All untrusted source files are read-only mounts; only this run-owned
        # evidence directory is writable. No Docker socket or credentials exist.
        arguments = ['container', 'create', '--name', name,
            '--label', docker._OWNER + '=' + token, '--label', docker._CAPTURE + '=' + request['identity'],
            '--pull=never', '--network=none', '--read-only', '--user=10001:10001',
            '--cap-drop=ALL', '--security-opt=no-new-privileges', '--cpus=2', '--memory=1g',
            '--pids-limit=256', '--shm-size=128m', '--tmpfs=/tmp:rw,nosuid,nodev,size=256m',
            '--restart=no', '--log-driver=none', '--stop-timeout=3', '--init', '--workdir=/work',
            '--env', 'ANIMATION_EXPERIMENT_IMAGE_DIGEST=' + plan['image_digest'],
            '--env', 'ANIMATION_EXPERIMENT_REQUEST_SHA256=' + plan['request_sha256'],
            '--mount', 'type=bind,source=' + str(root / 'input') + ',target=/input,readonly',
            '--mount', 'type=bind,source=' + str(root / 'adapters' / HOST.name)
                + ',target=/opt/renderer/animation_experiment_host.mjs,readonly',
            '--mount', 'type=bind,source=' + str(work) + ',target=/work',
            '--entrypoint=node', image_id, '/opt/renderer/animation_experiment_host.mjs']
        created = True
        container_id = docker._checked(arguments, run_deadline)
        _require(_SHA.fullmatch(container_id), 'render_container_id_invalid')
        _require(docker._owned(docker._container(container_id, run_deadline), name=name,
            token=token, capture=request['identity'], image_id=image_id, container_id=container_id),
            'render_created_container_mismatch')
        completed = docker._command(['container', 'start', '--attach', container_id],
            docker._remaining(run_deadline, plan['timeout_seconds']), log_path=root / 'render.log')
        _require(completed['code'] == 0, 'render_container_failed')
        final = docker._container(container_id, run_deadline)
        _require(docker._owned(final, name=name, token=token, capture=request['identity'],
            image_id=image_id, container_id=container_id) and final['running'] is False
            and final['exit_code'] == 0 and final['oom_killed'] is False, 'render_exit_invalid')
        result = _outputs(root, request, plan)
    except BaseException as exc:
        error = str(exc) if isinstance(exc, (ValueError, TimeoutError)) else type(exc).__name__
    finally:
        if created:
            try:
                cleanup = docker._cleanup(name=name, token=token, capture=request['identity'],
                    image_id=image_id, container_id=container_id, deadline=deadline)
            except BaseException as exc:
                cleanup = dict(status='unconfirmed', container_id=container_id, error=type(exc).__name__)
                error = error or 'animation_experiment_render_cleanup_unconfirmed'
    if error is None:
        try:
            _frozen(plan)
        except ValueError as exc:
            error = str(exc)
    receipt, technical = result if result is not None else (None, None)
    outcome = dict(schema='video.animation-experiment-render-outcome.v1',
        request_identity=request['identity'], input_sha256=plan['input_sha256'],
        status='failed' if error else 'rendered', error=error, cleanup=cleanup,
        started_at=start['started_at'], ended_at=now(), elapsed_seconds=round(time.monotonic() - began, 3),
        image=plan['image'], actual_image_id=image_id, container_name=name, container_id=container_id,
        renderer=plan['renderer'], receipt=receipt, technical=technical,
        visual_quality='not_evaluated', production_adoption=False, automatic_retry=False)
    _write(root / 'render-outcome.json', canonical(outcome))
    if error:
        raise RenderExecutionError(outcome)
    return outcome


def render_scene(bundle_dir, output_dir, *, image, input_document, timeout=180, assets_dir=None):
    prepare_render(bundle_dir, output_dir, image=image, input_document=input_document,
                   timeout=timeout, assets_dir=assets_dir)
    return execute_render(output_dir)
