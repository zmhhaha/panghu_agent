"""Freeze developer captures and review genuine native-renderer observations.

No browser, model, Agent, TTS, network or production task is started here. A
separate bounded renderer invocation runs ``lab_observe.mjs`` against these
inputs; this module checks its exact evidence and reuses existing technical
reviewers. This is sampled technical evidence, never an aesthetic approval.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import re

from agent_service.contracts import ServiceError
from .motion import canonical, sha256
from .motion_review import verify_observations
from .process_review import verify_process_observations


SCHEMA = 'video.animation-lab-capture.v1'
MAX_ACTIONS = 64
MAX_FRAMES = 240
MAX_EVIDENCE_BYTES = 96 * 1024 * 1024
_IDENTIFIER = re.compile(r'[a-z0-9][a-z0-9_-]{0,99}\Z')
_FRAME = re.compile(r'[a-z0-9][a-z0-9-]{0,100}\.jpg\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_HERE = Path(__file__).with_name('motion_renderer')
_RUNTIME_KEYS = frozenset({'schema', 'hyperframes', 'hyperframes_source', 'node', 'browser',
    'ffmpeg', 'gsap', 'gsap_sha256', 'font_sha256', 'npm_lock_sha256', 'dpkg_sha256', 'image_digest'})


def _require(value, code='animation_lab_capture_invalid'):
    if not value:
        raise ValueError(code)


def _read(path, maximum):
    path = Path(path)
    _require(not path.is_symlink() and path.is_file() and path.stat().st_size <= maximum,
             'animation_lab_capture_file_invalid')
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    _require(len(raw) <= maximum, 'animation_lab_capture_file_invalid')
    return raw


def _runtime(value):
    _require(type(value) is dict and set(value) == _RUNTIME_KEYS,
             'animation_lab_capture_runtime_invalid')
    _require(all(type(item) is str and 0 < len(item) <= 2000 and '\0' not in item
                 for item in value.values()), 'animation_lab_capture_runtime_invalid')
    _require(value['schema'] == 'video.motion-runtime.v1' and value['hyperframes'] == '0.8.149'
             and value['gsap'] == '3.14.2', 'animation_lab_capture_runtime_invalid')
    for name in ('gsap_sha256', 'font_sha256', 'npm_lock_sha256', 'dpkg_sha256'):
        _require(_SHA.fullmatch(value[name]), 'animation_lab_capture_runtime_invalid')
    _require(value['image_digest'].startswith('sha256:') and _SHA.fullmatch(value['image_digest'][7:]),
             'animation_lab_capture_runtime_invalid')
    return deepcopy(value)


def capture_request(bundle, clip_id, runtime):
    """Bind one real compiled clip, the measured runtime and capture adapters."""
    _require(type(bundle) is dict and type(clip_id) is str and _IDENTIFIER.fullmatch(clip_id))
    clips = bundle.get('clips')
    _require(type(clips) is list and 1 <= len(clips) <= 4)
    clip = next((row for row in clips if row.get('id') == clip_id), None)
    _require(clip is not None, 'animation_lab_capture_unknown_clip')
    _require(len(clip['timeline']['actions']) <= MAX_ACTIONS, 'animation_lab_capture_action_limit')
    _require(all(_IDENTIFIER.fullmatch(str(bundle.get(name, '')))
                 for name in ('case_id', 'variant_id')))
    _require(set(clip['files']) == {'index.html', 'plan.json', 'beats.json', 'alignment.json',
                                  'timeline.json', 'manifest.json', 'audio.wav'})
    offset = sum(row['duration_seconds'] for row in clips[:clips.index(clip)])
    value = dict(schema=SCHEMA, case_id=bundle['case_id'], variant_id=bundle['variant_id'],
        sample_identity=bundle['identity'], input_sha256=bundle['input_sha256'],
        artifact_sha256=bundle['artifact_sha256'], clip_id=clip_id, offset_seconds=offset,
        compiler=clip['timeline']['compiler'], duration_seconds=clip['duration_seconds'],
        files={name: sha256(raw) for name, raw in sorted(clip['files'].items())},
        runtime=_runtime(runtime),
        capture_adapter_sha256=sha256((_HERE / 'lab_observe.mjs').read_bytes()),
        observer_sha256=sha256((_HERE / 'observe.mjs').read_bytes()),
        runtime_collector_sha256=sha256((_HERE / 'runtime.mjs').read_bytes()),
        max_frames=MAX_FRAMES, max_evidence_bytes=MAX_EVIDENCE_BYTES,
        historical_local_experiment=clip['timeline']['compiler'] == 'motion-process-compiler-1',
        agent_calls=0, tts_calls=0, production_writes=0,
        output='sampled_browser_observations_not_mp4')
    if clip['timeline'].get('engine') in {'canvas', 'three'}:
        value.update(engine_observer_sha256=sha256((_HERE / 'engine_observe.mjs').read_bytes()),
                     engine_assets=json.loads(clip['files']['manifest.json']).get('engine_assets', {}))
    return dict(value, capture_identity=sha256(value))


def write_capture_inputs(bundle, destination, runtime):
    """Write an ordinary, new directory for each clip; never overwrite evidence."""
    destination = Path(destination).absolute()
    _require(not destination.exists() and not destination.is_symlink(),
             'animation_lab_capture_destination_exists')
    prepared = [(clip, capture_request(bundle, clip['id'], runtime)) for clip in bundle['clips']]
    destination.mkdir(parents=True)
    result = []
    for clip, request in prepared:
        directory = destination / clip['id']
        directory.mkdir()
        for name, raw in clip['files'].items():
            (directory / name).write_bytes(raw)
        raw = canonical(request)
        (directory / 'capture-request.json').write_bytes(raw)
        result.append(dict(clip_id=clip['id'], directory=str(directory),
                           request_sha256=sha256(raw), capture_identity=request['capture_identity']))
    return result


def _samples(observations):
    """Find measured samples without assigning identity from a model's report."""
    found = []

    def visit(value):
        if type(value) is dict:
            if 'frame_sha256' in value and 'time' in value:
                found.append(value)
            for child in value.values():
                visit(child)
        elif type(value) is list:
            for child in value:
                visit(child)

    visit(observations)
    return found


def _labelled_frames(request, observations):
    # The same pixels can legitimately persist at several different times.
    # Associate timestamps by the capture label, never by screenshot hash.
    reverse = observations.get('reverse_seek', {})
    result = {'seek-first.jpg': (0, reverse.get('first_sha256')),
              'seek-repeat.jpg': (0, reverse.get('repeated_first_sha256')),
              'seek-last.jpg': (request['duration_seconds'] - 1 / 24, reverse.get('last_sha256'))}
    for window in observations.get('windows', []):
        for label in ('before', 'middle', 'after'):
            sample = window.get(label, {})
            result[f"a{window.get('ordinal')}-{label}.jpg"] = (sample.get('time'), sample.get('frame_sha256'))
    boundary = observations.get('continuity_boundary', {})
    for name, key in [('continuity-transition-middle.jpg', 'transition_middle'),
                      ('continuity-transition-end.jpg', 'transition_end')]:
        if key in boundary:
            result[name] = (boundary[key].get('time'), boundary[key].get('frame_sha256'))
    for index, sample in enumerate(observations.get('seek_sequence', {}).get('samples', [])):
        result[f'continuity-sequence-{index}.jpg'] = (sample.get('time'), sample.get('frame_sha256'))
    return result


def _frames_for_review(request, observations, frames, maximum=6):
    by_name_time = _labelled_frames(request, observations)
    by_name = {row['name']: row for row in frames}
    candidates = ['seek-first.jpg', 'seek-last.jpg', 'continuity-transition-middle.jpg',
                  'continuity-transition-end.jpg']
    # A before/middle/after sequence for the middle declared action is more
    # informative than unrelated stills; the full check still retains all frames.
    windows = observations.get('windows', [])
    if windows:
        selected = windows[len(windows) // 2]
        candidates += [f"a{selected['ordinal']}-{label}.jpg" for label in ('before', 'middle', 'after')]
    candidates += [row['name'] for row in frames]
    selected, seen = [], set()
    for name in candidates:
        row = by_name.get(name)
        if row is None or row['name'] in seen:
            continue
        if name not in by_name_time:
            continue
        time, digest = by_name_time[name]
        if type(time) not in (int, float) or not math.isfinite(time) or digest != row['sha256']:
            continue
        seen.add(row['name'])
        selected.append(dict(frame_id=request['clip_id'] + ':' + name[:-4],
            clip_id=request['clip_id'], path='observations/' + name, sha256=row['sha256'],
            mime_type='image/jpeg', time_seconds=time,
            global_time_seconds=request['offset_seconds'] + time))
        if len(selected) == maximum:
            break
    return selected


def review_capture(bundle, clip_id, capture_directory, expected_runtime):
    """Verify binding and run the unchanged production technical reviewer.

    Binding failures raise and stop the caller. A genuine visual/semantic check
    failure is returned as ``technical_status=failed`` with its precise code so
    a later revision can be compared without erasing the original failure.
    """
    root = Path(capture_directory)
    request = capture_request(bundle, clip_id, expected_runtime)
    request_raw = _read(root / 'capture-request.json', 64 * 1024)
    _require(request_raw == canonical(request), 'animation_lab_capture_request_mismatch')
    for name, digest in request['files'].items():
        _require(sha256(_read(root / name, 8 * 1024 * 1024)) == digest,
                 'animation_lab_capture_input_mismatch')
    receipt_raw = _read(root / 'capture-receipt.json', 128 * 1024)
    receipt = json.loads(receipt_raw)
    _require(type(receipt) is dict and receipt.get('schema') == 'video.animation-lab-capture-receipt.v1'
             and receipt.get('capture_identity') == request['capture_identity']
             and receipt.get('request_sha256') == sha256(request_raw)
             and receipt.get('runtime') == request['runtime']
             and receipt.get('capture_adapter_sha256') == request['capture_adapter_sha256']
             and receipt.get('observer_sha256') == request['observer_sha256']
             and receipt.get('runtime_collector_sha256') == request['runtime_collector_sha256']
             and receipt.get('image_digest_source') == 'coordinator_pinned_not_self_measured'
             and receipt.get('output') == 'sampled_browser_observations_not_mp4'
             and all(type(receipt.get(key)) is int and receipt[key] == 0
                     for key in ('agent_calls', 'tts_calls', 'production_writes')),
             'animation_lab_capture_receipt_mismatch')
    observations_raw = _read(root / 'observations.json', 8 * 1024 * 1024)
    _require(sha256(observations_raw) == receipt.get('observations_sha256'),
             'animation_lab_capture_observations_mismatch')
    observations = json.loads(observations_raw)
    rows = receipt.get('frames')
    _require(type(rows) is list and 1 <= len(rows) <= MAX_FRAMES
             and receipt.get('frame_count') == len(rows), 'animation_lab_capture_frame_limit')
    frames, names, total = {}, set(), 0
    for row in rows:
        _require(type(row) is dict and type(row.get('name')) is str and _FRAME.fullmatch(row['name'])
                 and row['name'] not in names, 'animation_lab_capture_frame_invalid')
        raw = _read(root / 'observations' / row['name'], 4 * 1024 * 1024)
        _require(raw[:2] == b'\xff\xd8' and raw[-2:] == b'\xff\xd9'
                 and sha256(raw) == row.get('sha256') and len(raw) == row.get('bytes'),
                 'animation_lab_capture_frame_mismatch')
        total += len(raw)
        _require(total <= MAX_EVIDENCE_BYTES, 'animation_lab_capture_frame_limit')
        names.add(row['name'])
        frames[sha256(raw)] = raw
    _require(set(path.name for path in (root / 'observations').iterdir()) == names,
             'animation_lab_capture_extra_frame')
    references = {sample['frame_sha256'] for sample in _samples(observations)}
    references.update(value for key, value in observations.get('reverse_seek', {}).items()
                      if key.endswith('_sha256'))
    _require(references and references <= set(frames), 'animation_lab_capture_frame_reference_missing')
    by_name = {row['name']: row for row in rows}
    for name, (_, digest) in _labelled_frames(request, observations).items():
        if digest is not None:
            _require(name in by_name and by_name[name]['sha256'] == digest,
                     'animation_lab_capture_frame_label_mismatch')
    clip = next(row for row in bundle['clips'] if row['id'] == clip_id)
    plan = json.loads(clip['files']['plan.json'])
    timeline = clip['timeline']
    technical, error = None, None
    try:
        if timeline.get('engine') in {'canvas', 'three'}:
            from .animation_lab_engine_review import verify_engine_observations
            _require(receipt.get('engine_assets') == request.get('engine_assets'),
                     'animation_lab_capture_engine_assets_mismatch')
            technical = verify_engine_observations(plan, timeline, observations)
        else:
            technical = (verify_process_observations(plan, timeline, observations)
                     if timeline['compiler'] in {'motion-process-compiler-1', 'motion-process-compiler-2'}
                     else verify_observations(plan, timeline, observations, frame_bytes=frames))
    except ServiceError as exc:
        error = exc.code
    value = dict(schema='video.animation-lab-technical-review.v1',
        case_id=bundle['case_id'], variant_id=bundle['variant_id'], clip_id=clip_id,
        sample_identity=bundle['identity'], input_sha256=bundle['input_sha256'],
        artifact_sha256=bundle['artifact_sha256'], capture_identity=request['capture_identity'],
        runtime=request['runtime'], request_sha256=sha256(request_raw), receipt_sha256=sha256(receipt_raw),
        observations_sha256=sha256(observations_raw), frame_count=len(rows),
        technical_status='failed' if error else 'passed',
        issues=[] if error is None else [dict(code=error, severity='blocker', source='technical_check')],
        technical_review=technical, visual_frames=_frames_for_review(request, observations, rows),
        scope='all_declared_action_windows_and_recorded_seek_samples_only',
        visual_quality='not_evaluated', motion_smoothness='sampled_states_only',
        timing_review='pending', voice_naturalness='not_evaluated',
        agent_calls=0, tts_calls=0, production_writes=0, production_adoption=False)
    return dict(value, evidence_identity=sha256(value))
