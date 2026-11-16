"""Pure full-film input assembly; no browser, Agent, media encoding or adoption.

New composable-module declarations are deliberately distinct from v5 scene
packages. Freezing bytes does not prove their lifecycle exports execute, that
declared states look continuous, or that a real HyperFrames film was rendered.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import hashlib
import math
import re
from types import MappingProxyType

from agent_service.contracts import ServiceError, canonical_bytes, digest, safe_relative_path
from agent_service.profiles.video_animation_production_contract import validate_film_context
from .motion_snapshot import _runtime
from .speech import inspect_wav

SCHEMA = 'video.hyperframes-film-project.v1'
FILM_CLOCK_SCHEMA = 'video.hyperframes-film-clock.v1'
MODULE_SCHEMA = 'video.hyperframes-composable-module.v1'
BOUNDARY_SCHEMA = 'video.hyperframes-film-boundary.v1'
FPS, MAX_SECONDS, MAX_SCENES = 24, 600, 24
MAX_MODULES, MAX_FILES = 47, 512
MAX_SOURCE_FILE_BYTES, MAX_MODULE_BYTES = 512 * 1024, 2 * 1024 * 1024
MAX_SOURCE_BYTES, MAX_ASSET_BYTES = 8 * 1024 * 1024, 48 * 1024 * 1024
MAX_RECORDING_BYTES, MAX_TOTAL_BYTES = 128 * 1024 * 1024, 192 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
LIFECYCLE = dict(protocol='hyperframes-composable-module-1',
    exports=dict(mount='mount', render_at='renderAt', dispose='dispose'),
    scope='mount-root', time='absolute-global-frame', completion='await-returned-promises',
    seek='reconstructive')
EXECUTION = MappingProxyType(dict(status='not_executed', legacy_bridge=False,
    required_host_protocol='hyperframes-composable-film-host-1',
    evidence_scope='frozen_inputs_and_declared_lifecycle_not_runtime_or_visual_proof'))
_IDENTITY = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}\Z')
_NAMESPACE = re.compile(r'[a-z][a-z0-9_]{0,63}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def _require(value, code='hyperframes_film_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _identity(value):
    _require(type(value) is str and _IDENTITY.fullmatch(value), 'hyperframes_film_identity_invalid')
    return value


def _text(value, maximum, *, empty=False):
    _require(type(value) is str and len(value) <= maximum and (empty or value.strip())
        and not any(ord(char) < 32 and char not in '\n\r\t' for char in value),
        'hyperframes_film_text_invalid')
    return value


def _checksum(value):
    _require(type(value) is str and _SHA.fullmatch(value), 'hyperframes_film_digest_invalid')
    return value


def _json(value, maximum, code='hyperframes_film_metadata_invalid'):
    try:
        raw = canonical_bytes(value)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ServiceError(code, 422) from None
    _require(len(raw) <= maximum, code)
    return raw


def _path(value, prefix):
    try:
        safe_relative_path(value)
    except (ValueError, TypeError):
        raise ServiceError('hyperframes_film_resource_path_invalid', 422) from None
    _require(len(value) <= 200 and value.startswith(prefix)
        and re.fullmatch(r'[A-Za-z0-9_./-]+', value), 'hyperframes_film_resource_path_invalid')
    return value


def _frame(seconds):
    _require(type(seconds) in (int, float) and math.isfinite(seconds) and 0 <= seconds <= MAX_SECONDS,
        'hyperframes_film_clock_invalid')
    frame = round(seconds * FPS)
    _require(abs(seconds * FPS - frame) <= 1e-5, 'hyperframes_film_clock_invalid')
    return frame


def _film(value, checksum):
    if type(value) is dict and value.get('schema') == FILM_CLOCK_SCHEMA:
        fields = {'schema', 'project_id', 'joint_plan_version_id', 'content_binding_sha256',
            'title', 'script', 'understanding_goal', 'style', 'scenes', 'total_seconds'}
        _require(set(value) == fields and digest(value) == checksum, 'hyperframes_film_clock_binding_mismatch')
        _checksum(value['content_binding_sha256'])
        for key, maximum in [('project_id', 100), ('joint_plan_version_id', 100), ('title', 300),
                              ('script', 100000), ('understanding_goal', 16000), ('style', 10000)]:
            _text(value[key], maximum, empty=key in ('script', 'style'))
        _require(type(value['scenes']) is list and 1 <= len(value['scenes']) <= MAX_SCENES,
            'hyperframes_film_clock_invalid')
        previous, seen = 0, set()
        for row in value['scenes']:
            _require(type(row) is dict and set(row) == {'scene_id', 'title', 'narration', 'visual_intent',
                'audio_version_id', 'audio_sha256', 'audio_seconds', 'start_seconds', 'end_seconds',
                'leading_silence_seconds', 'trailing_silence_seconds', 'clock_source'},
                'hyperframes_film_clock_invalid')
            for key, maximum in [('scene_id', 100), ('title', 300), ('visual_intent', 16000)]:
                _text(row[key], maximum)
            _require(row['scene_id'] not in seen, 'hyperframes_film_clock_invalid')
            seen.add(row['scene_id'])
            start, end = _frame(row['start_seconds']), _frame(row['end_seconds'])
            _require(start == previous and start < end <= start + 60 * FPS, 'hyperframes_film_clock_invalid')
            previous = end
            for key in ('audio_seconds', 'leading_silence_seconds', 'trailing_silence_seconds'):
                item = row[key]
                _require(type(item) in (int, float) and math.isfinite(item) and 0 <= item <= 60,
                    'hyperframes_film_clock_invalid')
            _require(abs((end - start) / FPS - row['audio_seconds'] - row['leading_silence_seconds']
                - row['trailing_silence_seconds']) <= 1e-6, 'hyperframes_film_clock_invalid')
            if row['clock_source'] == 'silent':
                _require(row['audio_version_id'] is None and row['audio_sha256'] is None
                    and row['audio_seconds'] == 0 and row['leading_silence_seconds'] == 0
                    and row['narration'] == '', 'hyperframes_film_silent_interval_invalid')
            else:
                _require(row['clock_source'] == 'recording' and row['audio_seconds'] > 0,
                    'hyperframes_film_clock_invalid')
                _text(row['audio_version_id'], 100); _checksum(row['audio_sha256']); _text(row['narration'], 10000)
        _require(_frame(value['total_seconds']) == previous, 'hyperframes_film_clock_invalid')
        _json(value, 1024 * 1024)
        return deepcopy(value)
    # The existing coordinator's film is a recording-backed frozen clock.
    # It may be reused as input, but missing recordings never imply silence.
    return validate_film_context(dict(schema='video.film-context.v1', film=value,
        film_sha256=checksum, current_scene_index=0, predecessor=None))['film']


def _ratio(value):
    return dict(numerator=value.numerator, denominator=value.denominator)


def _rational_time(value):
    # The existing frozen film uses JSON floats. Reconstruct simple rational
    # values only within sub-picosecond representation noise; never round an
    # onset to video frames or to a different recording's sample rate.
    exact = Fraction(str(value))
    simple = exact.limit_denominator(10**9)
    return simple if abs(exact - simple) <= Fraction(1, 10**12) else exact


def _fraction(value):
    return Fraction(value['numerator'], value['denominator'])


def _states(value):
    _require(type(value) is dict and len(value) <= 32, 'hyperframes_film_state_invalid')
    nodes = 0
    def walk(item, depth=0):
        nonlocal nodes
        nodes += 1
        _require(depth <= 12 and nodes <= 4096, 'hyperframes_film_state_invalid')
        if type(item) is dict:
            _require(len(item) <= 128 and all(type(key) is str and len(key) <= 200 for key in item),
                'hyperframes_film_state_invalid')
            for child in item.values(): walk(child, depth + 1)
        elif type(item) is list:
            _require(len(item) <= 128, 'hyperframes_film_state_invalid')
            for child in item: walk(child, depth + 1)
        else:
            _require(item is None or type(item) in (bool, str, int, float), 'hyperframes_film_state_invalid')
            if type(item) is str: _require(len(item) <= 4000, 'hyperframes_film_state_invalid')
            if type(item) is float: _require(math.isfinite(item), 'hyperframes_film_state_invalid')
    for object_id, state in value.items():
        _identity(object_id)
        _require(type(state) is dict, 'hyperframes_film_state_invalid')
        walk(state)
    _json(value, 64 * 1024, 'hyperframes_film_state_invalid')
    return deepcopy(value)


def _module(value):
    fields = {'schema', 'module_id', 'namespace', 'role', 'scene_id', 'film_sha256', 'engine',
        'entry', 'files', 'files_sha256', 'lifecycle', 'assets', 'states'}
    _require(type(value) is dict and set(value) == fields and value.get('schema') == MODULE_SCHEMA,
        'hyperframes_film_composable_module_required')
    _identity(value['module_id'])
    _checksum(value['film_sha256'])
    namespace = value['namespace']
    _require(type(namespace) is str and _NAMESPACE.fullmatch(namespace)
        and value['role'] in ('scene', 'transition') and value['engine'] in ('html-svg', 'canvas', 'three'),
        'hyperframes_film_module_invalid')
    if value['role'] == 'scene': _text(value['scene_id'], 100)
    else: _require(value['scene_id'] is None, 'hyperframes_film_module_invalid')
    _require(value['lifecycle'] == LIFECYCLE, 'hyperframes_film_lifecycle_invalid')
    files, hashes = value['files'], value['files_sha256']
    _require(type(files) is dict and type(hashes) is dict and 1 <= len(files) <= 32 and set(files) == set(hashes),
        'hyperframes_film_module_files_invalid')
    prefix, total, resources = 'modules/' + namespace + '/', 0, {}
    for name, text in files.items():
        _path(name, prefix)
        _require(name.rsplit('.', 1)[-1] in ('mjs', 'js', 'css', 'html', 'svg') and type(text) is str,
            'hyperframes_film_module_files_invalid')
        try: raw = text.encode('utf-8')
        except UnicodeError: raise ServiceError('hyperframes_film_module_files_invalid', 422) from None
        _require(0 < len(raw) <= MAX_SOURCE_FILE_BYTES and _sha(raw) == _checksum(hashes[name]),
            'hyperframes_film_module_source_changed')
        total += len(raw)
        resources[name] = raw
    _require(total <= MAX_MODULE_BYTES, 'hyperframes_film_module_budget_exceeded')
    _require(type(value['entry']) is str and value['entry'] in files and value['entry'].endswith('.mjs'),
        'hyperframes_film_lifecycle_entry_invalid')
    assets = value['assets']
    _require(type(assets) is list and len(assets) <= 64 and all(type(name) is str for name in assets)
        and len(set(assets)) == len(assets),
        'hyperframes_film_module_assets_invalid')
    for name in assets: _path(name, 'assets/')
    states = value['states']
    _require(type(states) is dict and set(states) == {'entry', 'exit'}, 'hyperframes_film_state_invalid')
    _states(states['entry']); _states(states['exit'])
    metadata = {key: deepcopy(item) for key, item in value.items() if key != 'files'}
    metadata['assets'] = sorted(assets)
    _json(metadata, 192 * 1024)
    return metadata, resources


def module_identity(module):
    """Content identity only; declared lifecycle exports are not executed."""
    metadata, _ = _module(module)
    return digest(metadata)


def compile_film_project(*, film, film_sha256, modules, recordings, boundaries, assets,
                         runtime_fingerprint):
    """Copy exact module/asset/WAV bytes into a new, bounded full-film package.

    The caller resolves owner/version authorization before invoking this pure
    function. No current project heads, filesystem paths or remote URLs enter it.
    """
    _checksum(film_sha256)
    film = _film(film, film_sha256)
    rows = film['scenes']
    count = _frame(film['total_seconds'])
    _require(1 <= count <= MAX_SECONDS * FPS and 1 <= len(rows) <= MAX_SCENES,
        'hyperframes_film_budget_exceeded')
    runtime = _runtime(runtime_fingerprint)
    _require(runtime['schema'] == 'video.motion-runtime.v3', 'hyperframes_film_core_identity_required')
    _require(type(modules) is list and len(rows) <= len(modules) <= MAX_MODULES,
        'hyperframes_film_modules_invalid')
    resources, metadata, seen_paths, namespaces, scenes = {}, {}, set(), set(), {}
    source_bytes = 0
    def add(name, raw):
        _require(name.casefold() not in seen_paths, 'hyperframes_film_resource_collision')
        seen_paths.add(name.casefold()); resources[name] = raw
    for module in modules:
        frozen, sources = _module(module)
        mid = frozen['module_id']
        _require(mid not in metadata and frozen['namespace'] not in namespaces
            and frozen['film_sha256'] == film_sha256, 'hyperframes_film_module_binding_mismatch')
        namespaces.add(frozen['namespace'])
        frozen['module_sha256'] = digest(frozen)
        metadata[mid] = frozen
        if frozen['role'] == 'scene':
            sid = frozen['scene_id']
            _require(sid not in scenes, 'hyperframes_film_scene_module_mismatch')
            scenes[sid] = frozen
        for name, raw in sources.items():
            source_bytes += len(raw); add(name, raw)
    _require(source_bytes <= MAX_SOURCE_BYTES, 'hyperframes_film_source_budget_exceeded')
    _require(set(scenes) == {row['scene_id'] for row in rows}, 'hyperframes_film_scene_module_mismatch')
    _require(type(assets) is dict and len(assets) <= 128, 'hyperframes_film_assets_invalid')
    declared_assets = {name for module in metadata.values() for name in module['assets']}
    _require(set(assets) == declared_assets, 'hyperframes_film_asset_inventory_mismatch')
    asset_bytes = 0
    for name, raw in assets.items():
        _path(name, 'assets/')
        _require(type(raw) is bytes and 0 < len(raw) <= 32 * 1024 * 1024, 'hyperframes_film_assets_invalid')
        asset_bytes += len(raw); add(name, raw)
    _require(asset_bytes <= MAX_ASSET_BYTES, 'hyperframes_film_asset_budget_exceeded')
    for name, key in [('gsap.min.js', 'gsap_sha256'), ('NotoSansCJK-Regular.ttc', 'font_sha256'),
                      ('three.module.min.js', 'three_sha256'), ('three.LICENSE.txt', 'three_license_sha256')]:
        if 'assets/' + name in assets:
            _require(_sha(assets['assets/' + name]) == runtime[key], 'hyperframes_film_runtime_asset_mismatch')
    for module in metadata.values():
        if module['engine'] == 'three':
            _require({'assets/three.module.min.js', 'assets/three.LICENSE.txt'} <= set(module['assets']),
                'hyperframes_film_three_asset_required')
    recorded_rows = [row for row in rows if row.get('clock_source', 'recording') == 'recording']
    _require(type(recordings) is list and len(recordings) == len(recorded_rows), 'hyperframes_film_recordings_invalid')
    tracks, ranges, recording_bytes, previous_end = [], [], 0, 0
    recording_index = 0
    for index, row in enumerate(rows):
        start, end = _frame(row['start_seconds']), _frame(row['end_seconds'])
        _require(start == previous_end and end > start, 'hyperframes_film_clock_invalid')
        previous_end = end
        ranges.append(dict(scene_id=row['scene_id'], module_id=scenes[row['scene_id']]['module_id'],
            start_frame=start, end_frame=end, active_start_frame=start, active_end_frame=end))
        if row.get('clock_source') == 'silent':
            continue
        recording = recordings[recording_index]
        recording_index += 1
        fields = {'scene_id', 'audio_version_id', 'sha256', 'spoken_text', 'data'}
        _require(type(recording) is dict and set(recording) == fields and recording['scene_id'] == row['scene_id']
            and recording['audio_version_id'] == row['audio_version_id'] and recording['sha256'] == row['audio_sha256'],
            'hyperframes_film_recording_binding_mismatch')
        raw = recording['data']
        info = inspect_wav(raw, max_duration_seconds=60)
        _require(_sha(raw) == row['audio_sha256']
            and abs(info.duration_seconds - row['audio_seconds']) <= 1e-9,
            'hyperframes_film_recording_changed')
        spoken = recording['spoken_text']
        _require(type(spoken) is str and 0 < len(spoken) <= 10000 and spoken.strip(),
            'hyperframes_film_spoken_text_invalid')
        recording_bytes += len(raw)
        path = f'recordings/{index:03d}.wav'; add(path, raw)
        onset = Fraction(start, FPS) + _rational_time(row['leading_silence_seconds'])
        finish = onset + Fraction(info.frame_count, info.sample_rate)
        _require(Fraction(start, FPS) <= onset < finish <= Fraction(end, FPS),
            'hyperframes_film_recording_outside_scene')
        tracks.append(dict(scene_id=row['scene_id'], audio_version_id=row['audio_version_id'], path=path,
            sha256=row['audio_sha256'], spoken_text=spoken, spoken_text_sha256=_sha(spoken.encode('utf-8')),
            spoken_text_evidence='declared_not_transcribed', source_sample_rate=info.sample_rate,
            source_sample_count=info.frame_count, source_channels=info.channels, source_sample_width=info.sample_width,
            source_start_sample=0, source_end_sample=info.frame_count, start_time=_ratio(onset), end_time=_ratio(finish),
            source_policy='complete_original_pcm_no_trim_stretch_or_resample'))
    _require(previous_end == count and recording_bytes <= MAX_RECORDING_BYTES,
        'hyperframes_film_recording_budget_exceeded')
    _require(type(boundaries) is list and len(boundaries) == len(rows) - 1,
        'hyperframes_film_boundaries_invalid')
    used_transitions, windows = set(), []
    for index, boundary in enumerate(boundaries):
        outgoing, incoming = scenes[rows[index]['scene_id']], scenes[rows[index + 1]['scene_id']]
        _require(type(boundary) is dict and set(boundary) == {'schema', 'from_scene_id', 'to_scene_id',
            'from_module_sha256', 'to_module_sha256', 'at_frame', 'objects', 'transition'}
            and boundary['schema'] == BOUNDARY_SCHEMA
            and boundary['from_scene_id'] == rows[index]['scene_id']
            and boundary['to_scene_id'] == rows[index + 1]['scene_id']
            and boundary['from_module_sha256'] == outgoing['module_sha256']
            and boundary['to_module_sha256'] == incoming['module_sha256']
            and type(boundary['at_frame']) is int and boundary['at_frame'] == ranges[index]['end_frame'],
            'hyperframes_film_boundary_binding_mismatch')
        shared = set(outgoing['states']['exit']) & set(incoming['states']['entry'])
        objects = boundary['objects']
        _require(type(objects) is list and len(objects) <= 32, 'hyperframes_film_boundary_state_invalid')
        seen = set()
        for item in objects:
            _require(type(item) is dict and set(item) == {'object_id', 'outgoing_state_sha256',
                'incoming_state_sha256', 'mode'}, 'hyperframes_film_boundary_state_invalid')
            oid = item['object_id']
            _require(type(oid) is str and oid in shared and oid not in seen
                and item['outgoing_state_sha256'] == digest(outgoing['states']['exit'][oid])
                and item['incoming_state_sha256'] == digest(incoming['states']['entry'][oid])
                and item['mode'] in ('retain', 'transform'), 'hyperframes_film_boundary_state_invalid')
            seen.add(oid)
            _require(item['mode'] != 'retain' or item['outgoing_state_sha256'] == item['incoming_state_sha256'],
                'hyperframes_film_retained_state_changed')
            _require(item['mode'] != 'transform' or boundary['transition'] is not None,
                'hyperframes_film_transform_module_required')
        _require(seen == shared, 'hyperframes_film_boundary_state_incomplete')
        transition = boundary['transition']
        if transition is not None:
            _require(type(transition) is dict and set(transition) == {'module_id', 'module_sha256',
                'start_frame', 'end_frame'}, 'hyperframes_film_transition_invalid')
            tid, start, end = transition['module_id'], transition['start_frame'], transition['end_frame']
            _require(type(tid) is str and tid in metadata and tid not in used_transitions
                and metadata[tid]['role'] == 'transition' and metadata[tid]['module_sha256'] == transition['module_sha256']
                and type(start) is int and type(end) is int and ranges[index]['start_frame'] <= start
                and start <= boundary['at_frame'] < end <= ranges[index + 1]['end_frame']
                and 0 < end - start <= 48 and (not windows or start >= windows[-1]['end_frame']),
                'hyperframes_film_transition_invalid')
            states = metadata[tid]['states']
            _require(states['entry'] == {oid: outgoing['states']['exit'][oid] for oid in shared}
                and states['exit'] == {oid: incoming['states']['entry'][oid] for oid in shared},
                'hyperframes_film_transition_state_mismatch')
            used_transitions.add(tid)
            windows.append(deepcopy(transition))
            ranges[index]['active_end_frame'] = end
            ranges[index + 1]['active_start_frame'] = start
    _require(used_transitions == {mid for mid, module in metadata.items() if module['role'] == 'transition'},
        'hyperframes_film_unreferenced_transition')
    _require(len(resources) + 1 <= MAX_FILES and sum(map(len, resources.values())) <= MAX_TOTAL_BYTES,
        'hyperframes_film_resource_budget_exceeded')
    manifest = dict(schema=SCHEMA, film=film, film_sha256=film_sha256,
        runtime_fingerprint=runtime, execution=dict(EXECUTION),
        clock=dict(fps=FPS, frame_count=count, duration=_ratio(Fraction(count, FPS)),
            audio_timebase='original-pcm-rational-timestamps', sample_boundary_policy='floor_each_absolute_boundary',
            float_time_policy='rational_reconstruction_within_1e-12_seconds',
            authority='recordings_with_explicit_silent_intervals' if tracks else 'explicit_silent_intervals'),
        modules=[metadata[mid] for mid in sorted(metadata)], scenes=ranges,
        boundaries=deepcopy(boundaries), narration_tracks=tracks,
        resources={name: dict(sha256=_sha(raw), bytes=len(raw),
            role='module-source' if name.startswith('modules/') else 'asset' if name.startswith('assets/') else 'recording')
            for name, raw in sorted(resources.items())})
    resources['manifest.json'] = _json(manifest, MAX_MANIFEST_BYTES)
    return resources


def verify_film_project(files, *, expected_manifest_sha256):
    """Require an external trusted digest and byte-for-byte pure recompilation."""
    import json
    _checksum(expected_manifest_sha256)
    _require(type(files) is dict and 'manifest.json' in files and len(files) <= MAX_FILES
        and all(type(name) is str and type(raw) is bytes for name, raw in files.items()),
        'hyperframes_film_package_invalid')
    raw = files['manifest.json']
    _require(0 < len(raw) <= MAX_MANIFEST_BYTES and _sha(raw) == expected_manifest_sha256,
        'hyperframes_film_manifest_binding_mismatch', 409)
    try:
        manifest = json.loads(raw)
        _require(type(manifest) is dict and manifest.get('schema') == SCHEMA
            and raw == _json(manifest, MAX_MANIFEST_BYTES), 'hyperframes_film_manifest_invalid')
        inventory = manifest['resources']
        _require(type(inventory) is dict and set(files) == set(inventory) | {'manifest.json'},
            'hyperframes_film_resource_inventory_mismatch')
        _require(sum(map(len, files.values())) <= MAX_TOTAL_BYTES + MAX_MANIFEST_BYTES,
            'hyperframes_film_resource_budget_exceeded')
        for name, identity in inventory.items():
            _require(type(identity) is dict and identity.get('bytes') == len(files[name])
                and identity.get('sha256') == _sha(files[name]), 'hyperframes_film_resource_changed')
        modules = []
        for frozen in manifest['modules']:
            module = {key: deepcopy(value) for key, value in frozen.items() if key != 'module_sha256'}
            module['files'] = {name: files[name].decode('utf-8') for name in module['files_sha256']}
            _require(module_identity(module) == frozen['module_sha256'], 'hyperframes_film_module_source_changed')
            modules.append(module)
        recordings = [dict(scene_id=track['scene_id'], audio_version_id=track['audio_version_id'],
            sha256=track['sha256'], spoken_text=track['spoken_text'], data=files[track['path']])
            for track in manifest['narration_tracks']]
        expected = compile_film_project(film=manifest['film'], film_sha256=manifest['film_sha256'], modules=modules,
            recordings=recordings, boundaries=manifest['boundaries'],
            assets={name: files[name] for name in inventory if name.startswith('assets/')},
            runtime_fingerprint=manifest['runtime_fingerprint'])
        _require(expected == files, 'hyperframes_film_not_compiler_output')
        return deepcopy(manifest)
    except (KeyError, ValueError, TypeError, UnicodeError, RecursionError, OverflowError, ZeroDivisionError):
        raise ServiceError('hyperframes_film_manifest_invalid', 422) from None


def frame_to_sample(frame, sample_rate):
    """Floor each absolute frame boundary independently; never accumulate drift."""
    _require(type(frame) is int and 0 <= frame <= MAX_SECONDS * FPS
        and type(sample_rate) is int and 8000 <= sample_rate <= 96000, 'hyperframes_film_frame_invalid')
    return frame * sample_rate // FPS


def frame_context(files, frame, *, expected_manifest_sha256):
    """Verified offline query; rechecks all bytes and is not a render hot path.

    Transition overlap deliberately exposes negative/post-end local frames.
    A future host passes the absolute global frame and explicit phase; it must
    not clamp those values or silently substitute a scene-local clock.
    """
    manifest = verify_film_project(files, expected_manifest_sha256=expected_manifest_sha256)
    _require(type(frame) is int and 0 <= frame < manifest['clock']['frame_count'], 'hyperframes_film_frame_invalid')
    scenes = [dict(scene_id=row['scene_id'], module_id=row['module_id'],
        local_frame=frame - row['start_frame'], phase='entering' if frame < row['start_frame']
            else 'exiting' if frame >= row['end_frame'] else 'active') for row in manifest['scenes']
        if row['active_start_frame'] <= frame < row['active_end_frame']]
    transitions = [dict(boundary_index=index, module_id=row['transition']['module_id'])
        for index, row in enumerate(manifest['boundaries']) if row['transition'] is not None
        and row['transition']['start_frame'] <= frame < row['transition']['end_frame']]
    audio = []
    for track in manifest['narration_tracks']:
        onset, rate, samples = _fraction(track['start_time']), track['source_sample_rate'], track['source_sample_count']
        # Tracks retain their own rates, including 44.1kHz. These exact rational
        # boundaries partition every original sample; no PCM is resampled here.
        start = min(samples, max(0, math.floor((Fraction(frame, FPS) - onset) * rate)))
        end = min(samples, max(0, math.floor((Fraction(frame + 1, FPS) - onset) * rate)))
        if end > start:
            audio.append(dict(scene_id=track['scene_id'], path=track['path'],
                source_start_sample=start, source_end_sample=end, source_sample_rate=rate))
    return dict(global_frame=frame, time=_ratio(Fraction(frame, FPS)), scenes=scenes,
        transitions=transitions, narration_windows=audio, execution_status='not_executed')
