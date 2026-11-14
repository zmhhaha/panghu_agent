"""Pure package/clock fixtures. No Agent, lifecycle, browser or render claims."""
from copy import deepcopy
from fractions import Fraction
import hashlib
import io
import json
import math
from pathlib import Path
import wave

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench import hyperframes_film_project as H
from test_creative_hyperframes_renderer import RUNTIME

ROOT = Path(__file__).resolve().parents[2]


def wav(rate, samples, *, channels=1, width=2):
    data = io.BytesIO()
    with wave.open(data, 'wb') as stream:
        stream.setparams((channels, width, rate, 0, 'NONE', 'not compressed'))
        stream.writeframes(bytes([37]) * samples * channels * width)
    return data.getvalue()


def module(mid, scene_id, film_sha, *, engine='html-svg', entry_state=None, exit_state=None):
    entry = 'modules/' + mid + '/entry.mjs'
    text = ('// Nonvisual protocol fixture; exports have not been executed.\n'
        'export async function mount(root, context) {}\n'
        'export async function renderAt(context) {}\n'
        'export async function dispose() {}\n')
    return dict(schema=H.MODULE_SCHEMA, module_id=mid, namespace=mid,
        role='scene' if scene_id is not None else 'transition', scene_id=scene_id, film_sha256=film_sha,
        engine=engine, entry=entry, files={entry: text}, files_sha256={entry: hashlib.sha256(text.encode()).hexdigest()},
        lifecycle=deepcopy(H.LIFECYCLE), assets=[],
        states=dict(entry={'subject': entry_state or {'x': 0}}, exit={'subject': exit_state or {'x': 1}}))


def inputs(*, rates=(24000, 48000, 44100), channels=(1, 1, 1), widths=(2, 2, 2)):
    rows, recordings, cursor = [], [], 0
    for index, rate in enumerate(rates):
        samples = rate // 2 + index
        raw = wav(rate, samples, channels=channels[index], width=widths[index])
        seconds, leading = samples / rate, .125
        frames = math.ceil((seconds + leading + .125) * 24)
        rows.append(dict(scene_id='scene-' + str(index), title='Subject ' + str(index), narration='旁白' + str(index),
            visual_intent='Preserve a changing subject.', audio_version_id='audio-' + str(index),
            audio_sha256=hashlib.sha256(raw).hexdigest(), audio_seconds=seconds,
            start_seconds=cursor / 24, end_seconds=(cursor + frames) / 24,
            leading_silence_seconds=leading, trailing_silence_seconds=frames / 24 - seconds - leading))
        cursor += frames
        recordings.append(dict(scene_id=rows[-1]['scene_id'], audio_version_id=rows[-1]['audio_version_id'],
            sha256=rows[-1]['audio_sha256'], spoken_text=rows[-1]['narration'], data=raw))
    film = dict(project_id='full-film-fixture', joint_plan_version_id='plan-1', content_binding_sha256='a' * 64,
        title='One exact film clock', script='原文总稿', understanding_goal='Follow a persistent subject.',
        style='Agent chooses the drawing.', scenes=rows, total_seconds=cursor / 24)
    checksum = digest(film)
    modules = [module('scene_' + str(index), row['scene_id'], checksum,
        engine='canvas' if index == 1 else 'html-svg', entry_state={'x': index if index < 2 else index + 1},
        exit_state={'x': index + 1 if index < 2 else index + 2}) for index, row in enumerate(rows)]
    transition = module('relation_transition', None, checksum, entry_state={'x': 2}, exit_state={'x': 3})
    modules.append(transition)
    boundaries = []
    for index in range(2):
        outgoing, incoming = modules[index:index + 2]
        at = round(rows[index]['end_seconds'] * 24)
        boundaries.append(dict(schema=H.BOUNDARY_SCHEMA, from_scene_id=rows[index]['scene_id'],
            to_scene_id=rows[index + 1]['scene_id'], from_module_sha256=H.module_identity(outgoing),
            to_module_sha256=H.module_identity(incoming), at_frame=at,
            objects=[dict(object_id='subject', outgoing_state_sha256=digest(outgoing['states']['exit']['subject']),
                incoming_state_sha256=digest(incoming['states']['entry']['subject']), mode='retain' if index == 0 else 'transform')],
            transition=None if index == 0 else dict(module_id=transition['module_id'],
                module_sha256=H.module_identity(transition), start_frame=at - 3, end_frame=at + 3)))
    return dict(film=film, film_sha256=checksum, modules=modules, recordings=recordings,
        boundaries=boundaries, assets={}, runtime_fingerprint=deepcopy(RUNTIME))


def compiled(**options):
    value = inputs(**options)
    files = H.compile_film_project(**value)
    checksum = hashlib.sha256(files['manifest.json']).hexdigest()
    return value, files, checksum, H.verify_film_project(files, expected_manifest_sha256=checksum)


def rehashed_manifest(files, mutate):
    files = dict(files)
    manifest = json.loads(files['manifest.json'])
    mutate(manifest)
    files['manifest.json'] = canonical_bytes(manifest)
    return files, hashlib.sha256(files['manifest.json']).hexdigest()


def explicit_clock(value, silent_scenes):
    value = deepcopy(value)
    value['film']['schema'] = H.FILM_CLOCK_SCHEMA
    for row in value['film']['scenes']:
        row['clock_source'] = 'silent' if row['scene_id'] in silent_scenes else 'recording'
        if row['clock_source'] == 'silent':
            row.update(narration='', audio_version_id=None, audio_sha256=None, audio_seconds=0,
                leading_silence_seconds=0, trailing_silence_seconds=row['end_seconds'] - row['start_seconds'])
    value['recordings'] = [row for row in value['recordings'] if row['scene_id'] not in silent_scenes]
    value['film_sha256'] = digest(value['film'])
    for module in value['modules']: module['film_sha256'] = value['film_sha256']
    modules = {module['scene_id']: module for module in value['modules'] if module['role'] == 'scene'}
    for row in value['boundaries']:
        row['from_module_sha256'] = H.module_identity(modules[row['from_scene_id']])
        row['to_module_sha256'] = H.module_identity(modules[row['to_scene_id']])
        if row['transition'] is not None:
            row['transition']['module_sha256'] = H.module_identity(next(module for module in value['modules']
                if module['module_id'] == row['transition']['module_id']))
    return value


def test_one_package_copies_all_exact_sources_assets_recordings_and_frozen_state():
    value, files, checksum, manifest = compiled()
    assert manifest['film'] == value['film'] and manifest['film_sha256'] == digest(value['film'])
    assert manifest['execution'] == H.EXECUTION and manifest['execution']['status'] == 'not_executed'
    assert manifest['clock']['frame_count'] == 56
    assert manifest['clock']['duration'] == {'numerator': 7, 'denominator': 3}
    assert len(manifest['scenes']) == 3 and len(manifest['modules']) == 4
    for item in value['modules']:
        for name, text in item['files'].items(): assert files[name] == text.encode()
    for index, recording in enumerate(value['recordings']):
        assert files[f'recordings/{index:03d}.wav'] == recording['data']
        assert manifest['narration_tracks'][index]['source_start_sample'] == 0
    assert set(files) == set(manifest['resources']) | {'manifest.json'}
    # All inputs and returned metadata are independent of frozen byte outputs.
    value['film']['title'] = 'Later project head'
    manifest['modules'][0]['states']['entry'].clear()
    assert H.verify_film_project(files, expected_manifest_sha256=checksum)['film']['title'] != value['film']['title']


def test_execution_status_is_a_readonly_declaration_not_a_lifecycle_result():
    with pytest.raises(TypeError): H.EXECUTION['status'] = 'rendered'
    _, files, checksum, _ = compiled()
    assert H.verify_film_project(files, expected_manifest_sha256=checksum)['execution']['status'] == 'not_executed'


@pytest.mark.parametrize('rates', [(24000, 24000, 24000), (48000, 48000, 48000), (44100, 44100, 44100), (44100, 24000, 48000)])
def test_rational_audio_clock_partitions_all_original_samples_at_each_absolute_frame(rates):
    _, files, checksum, manifest = compiled(rates=rates)
    windows = {row['scene_id']: [] for row in manifest['narration_tracks']}
    for frame in range(manifest['clock']['frame_count']):
        context = H.frame_context(files, frame, expected_manifest_sha256=checksum)
        for row in context['narration_windows']: windows[row['scene_id']].append(row)
    for track in manifest['narration_tracks']:
        rows = windows[track['scene_id']]
        assert rows[0]['source_start_sample'] == 0
        assert rows[-1]['source_end_sample'] == track['source_sample_count']
        assert all(previous['source_end_sample'] == current['source_start_sample'] for previous, current in zip(rows, rows[1:]))
        assert sum(row['source_end_sample'] - row['source_start_sample'] for row in rows) == track['source_sample_count']
        onset = Fraction(**{key: track['start_time'][key] for key in ('numerator', 'denominator')})
        finish = Fraction(**{key: track['end_time'][key] for key in ('numerator', 'denominator')})
        assert finish - onset == Fraction(track['source_sample_count'], track['source_sample_rate'])
    boundaries = [H.frame_to_sample(frame, 44100) for frame in range(25)]
    assert boundaries[:3] == [0, 1837, 3675]
    assert set(b - a for a, b in zip(boundaries, boundaries[1:])) == {1837, 1838}
    assert boundaries[-1] == 44100


def test_different_original_pcm_formats_are_bound_without_conversion():
    value, files, _, manifest = compiled(channels=(1, 2, 1), widths=(1, 2, 3))
    assert [row['source_channels'] for row in manifest['narration_tracks']] == [1, 2, 1]
    assert [row['source_sample_width'] for row in manifest['narration_tracks']] == [1, 2, 3]
    for index, row in enumerate(value['recordings']): assert files[f'recordings/{index:03d}.wav'] == row['data']


def test_float_representation_noise_never_drops_the_last_original_sample():
    value = inputs()
    row = value['film']['scenes'][0]
    raw = wav(44100, 30870)
    row.update(start_seconds=0, end_seconds=1, audio_seconds=.7,
        leading_silence_seconds=.30000000000000004, trailing_silence_seconds=0,
        audio_sha256=hashlib.sha256(raw).hexdigest())
    value['film'].update(scenes=[row], total_seconds=1)
    value['film_sha256'] = digest(value['film'])
    value['modules'] = value['modules'][:1]
    value['modules'][0]['film_sha256'] = value['film_sha256']
    value['recordings'] = value['recordings'][:1]
    value['recordings'][0].update(data=raw, sha256=row['audio_sha256'])
    value['boundaries'] = []
    files = H.compile_film_project(**value)
    checksum = hashlib.sha256(files['manifest.json']).hexdigest()
    manifest = H.verify_film_project(files, expected_manifest_sha256=checksum)
    assert manifest['narration_tracks'][0]['start_time'] == {'numerator': 3, 'denominator': 10}
    assert H.frame_context(files, 23, expected_manifest_sha256=checksum)['narration_windows'][0]['source_end_sample'] == 30870
    assert files['recordings/000.wav'] == raw


def test_explicit_film_without_narration_has_no_placeholder_recordings_or_audio_claims():
    value = explicit_clock(inputs(), {'scene-0', 'scene-1', 'scene-2'})
    files = H.compile_film_project(**value)
    checksum = hashlib.sha256(files['manifest.json']).hexdigest()
    manifest = H.verify_film_project(files, expected_manifest_sha256=checksum)
    assert manifest['clock']['authority'] == 'explicit_silent_intervals'
    assert manifest['narration_tracks'] == []
    assert not any(name.startswith('recordings/') for name in files)
    assert manifest['execution']['status'] == 'not_executed'
    for frame in range(manifest['clock']['frame_count']):
        assert H.frame_context(files, frame, expected_manifest_sha256=checksum)['narration_windows'] == []


def test_recordings_and_explicit_silent_scenes_share_the_same_global_clock():
    value = explicit_clock(inputs(), {'scene-1'})
    files = H.compile_film_project(**value)
    checksum = hashlib.sha256(files['manifest.json']).hexdigest()
    manifest = H.verify_film_project(files, expected_manifest_sha256=checksum)
    assert manifest['clock']['authority'] == 'recordings_with_explicit_silent_intervals'
    assert [row['scene_id'] for row in manifest['narration_tracks']] == ['scene-0', 'scene-2']
    assert 'recordings/001.wav' not in files and 'recordings/002.wav' in files
    for frame in range(18, 37):
        assert H.frame_context(files, frame, expected_manifest_sha256=checksum)['narration_windows'] == []


@pytest.mark.parametrize('change', ['not_explicit', 'version', 'sha', 'narration', 'audio_duration'])
def test_missing_audio_is_never_silently_relabelled_as_an_unvoiced_film(change):
    if change == 'not_explicit':
        value = inputs(); value['recordings'] = []
    else:
        value = explicit_clock(inputs(), {'scene-0'})
        row = value['film']['scenes'][0]
        if change == 'version': row['audio_version_id'] = 'old-recording'
        elif change == 'sha': row['audio_sha256'] = 'a' * 64
        elif change == 'narration': row['narration'] = 'A missing voice'
        else: row['audio_seconds'] = .1
        value['film_sha256'] = digest(value['film'])
    with pytest.raises(ServiceError): H.compile_film_project(**value)


def test_transition_window_uses_one_global_frame_and_explicit_enter_exit_phases():
    value, files, checksum, manifest = compiled()
    at = value['boundaries'][1]['at_frame']
    before = H.frame_context(files, at - 1, expected_manifest_sha256=checksum)
    assert [(row['scene_id'], row['phase'], row['local_frame']) for row in before['scenes']] == [
        ('scene-1', 'active', 18), ('scene-2', 'entering', -1)]
    current = H.frame_context(files, at, expected_manifest_sha256=checksum)
    assert [(row['scene_id'], row['phase']) for row in current['scenes']] == [('scene-1', 'exiting'), ('scene-2', 'active')]
    assert current['transitions'] == [{'boundary_index': 1, 'module_id': 'relation_transition'}]
    after = H.frame_context(files, at + 3, expected_manifest_sha256=checksum)
    assert [row['scene_id'] for row in after['scenes']] == ['scene-2'] and after['transitions'] == []


@pytest.mark.parametrize('schema', ['video.creative-scene.v5', 'video.animation-production-result.v4'])
def test_legacy_seek_only_outputs_are_not_relabelled_as_composable_modules(schema):
    value = inputs()
    value['modules'][0] = dict(schema=schema, source=dict(engine='html-svg',
        files={'scene.html': '<script>window.__animationExperiment={seek(){}}</script>'}))
    with pytest.raises(ServiceError, match='composable_module_required'): H.compile_film_project(**value)


@pytest.mark.parametrize('change', ['schema', 'lifecycle', 'entry', 'namespace', 'film', 'source', 'source_hash', 'missing_scene', 'duplicate_scene'])
def test_modules_require_new_lifecycle_exact_film_and_source_inventory(change):
    value = inputs(); item = value['modules'][0]
    if change == 'schema': item['schema'] = 'video.creative-scene.v5'
    elif change == 'lifecycle': item['lifecycle']['seek'] = 'incremental'
    elif change == 'entry': item['entry'] = 'scene.html'
    elif change == 'namespace': item['namespace'] = '../escape'
    elif change == 'film': item['film_sha256'] = 'f' * 64
    elif change == 'source': item['files'][item['entry']] += 'changed'
    elif change == 'source_hash': item['files_sha256'][item['entry']] = 'f' * 64
    elif change == 'missing_scene': value['modules'].pop(0)
    else: value['modules'][1]['scene_id'] = item['scene_id']
    with pytest.raises(ServiceError): H.compile_film_project(**value)


@pytest.mark.parametrize('change', ['order', 'version', 'sha', 'bytes', 'truncated', 'duration', 'missing'])
def test_complete_recordings_cannot_be_swapped_trimmed_or_borrow_other_audio_versions(change):
    value = inputs(); row = value['recordings'][0]
    if change == 'order': value['recordings'].reverse()
    elif change == 'version': row['audio_version_id'] = 'later-audio'
    elif change == 'sha': row['sha256'] = 'f' * 64
    elif change == 'bytes': row['data'] = row['data'][:-1] + b'\x00'
    elif change == 'truncated': row['data'] = row['data'][:-2]
    elif change == 'duration': value['film']['scenes'][0]['audio_seconds'] += .01; value['film_sha256'] = digest(value['film'])
    else: value['recordings'].pop()
    with pytest.raises(ServiceError): H.compile_film_project(**value)


@pytest.mark.parametrize('change', ['boundary', 'source', 'state', 'missing_object', 'transform', 'transition_source', 'window', 'transition_state'])
def test_boundaries_bind_exact_neighbor_modules_serialized_states_and_agent_transition(change):
    value = inputs(); row = value['boundaries'][1]
    if change == 'boundary': row['at_frame'] += 1
    elif change == 'source': row['from_module_sha256'] = 'f' * 64
    elif change == 'state': row['objects'][0]['outgoing_state_sha256'] = 'f' * 64
    elif change == 'missing_object': row['objects'] = []
    elif change == 'transform': row['transition'] = None
    elif change == 'transition_source': row['transition']['module_sha256'] = 'f' * 64
    elif change == 'window': row['transition']['end_frame'] = 100000
    else:
        value['modules'][-1]['states']['entry']['subject'] = {'x': -1}
        row['transition']['module_sha256'] = H.module_identity(value['modules'][-1])
    with pytest.raises(ServiceError): H.compile_film_project(**value)


def test_retain_cannot_silently_change_structured_object_state():
    value = inputs(); incoming = value['modules'][1]; row = value['boundaries'][0]
    incoming['states']['entry']['subject'] = {'x': 100}
    row['to_module_sha256'] = H.module_identity(incoming)
    value['boundaries'][1]['from_module_sha256'] = row['to_module_sha256']
    row['objects'][0]['incoming_state_sha256'] = digest({'x': 100})
    with pytest.raises(ServiceError, match='retained_state_changed'): H.compile_film_project(**value)


def test_transition_windows_cannot_overlap_or_reference_an_unused_module():
    value = inputs()
    extra = module('first_transition', None, value['film_sha256'], entry_state={'x': 1}, exit_state={'x': 1})
    value['modules'].append(extra)
    with pytest.raises(ServiceError, match='unreferenced_transition'): H.compile_film_project(**value)
    value['boundaries'][0]['transition'] = dict(module_id=extra['module_id'],
        module_sha256=H.module_identity(extra), start_frame=10, end_frame=30)
    value['boundaries'][1]['transition'].update(start_frame=25, end_frame=40)
    with pytest.raises(ServiceError, match='transition_invalid'): H.compile_film_project(**value)


@pytest.mark.parametrize('change', ['extra', 'traversal', 'external', 'collision', 'bad_type', 'wrong_pin'])
def test_asset_inventory_is_local_exact_portable_and_runtime_bound(change):
    value = inputs(); module = value['modules'][0]
    if change == 'extra': value['assets']['assets/unused.bin'] = b'unused'
    elif change == 'bad_type': module['assets'] = [{}]
    elif change == 'collision':
        module['assets'] = ['assets/object.bin', 'assets/OBJECT.bin']
        value['assets'] = {name: b'object' for name in module['assets']}
    else:
        path = 'assets/../escape.bin' if change == 'traversal' else 'https://example.com/shape.bin' if change == 'external' else 'assets/gsap.min.js'
        module['assets'] = [path]; value['assets'] = {path: b'incorrect pinned runtime bytes'}
    with pytest.raises(ServiceError): H.compile_film_project(**value)


def test_three_module_requires_the_exact_pinned_library_and_license_as_declared_assets():
    value = inputs(); module = value['modules'][2]; module['engine'] = 'three'
    with pytest.raises(ServiceError, match='three_asset_required'): H.compile_film_project(**value)
    module['assets'] = ['assets/three.module.min.js', 'assets/three.LICENSE.txt']
    value['assets'] = {'assets/' + name: (ROOT / 'video_workbench/motion_renderer/vendor' / name).read_bytes()
        for name in ('three.module.min.js', 'three.LICENSE.txt')}
    value['boundaries'][1]['to_module_sha256'] = H.module_identity(module)
    files = H.compile_film_project(**value)
    assert files['assets/three.module.min.js'] == value['assets']['assets/three.module.min.js']


@pytest.mark.parametrize('change', ['clock', 'audio', 'range', 'execution'])
def test_rehashed_derived_schedule_or_execution_claim_is_not_trusted_compiler_output(change):
    _, files, _, _ = compiled()
    def mutate(manifest):
        if change == 'clock': manifest['clock']['frame_count'] += 1
        elif change == 'audio': manifest['narration_tracks'][0]['source_end_sample'] -= 1
        elif change == 'range': manifest['scenes'][1]['active_start_frame'] += 1
        else: manifest['execution']['status'] = 'rendered'
    files, checksum = rehashed_manifest(files, mutate)
    with pytest.raises(ServiceError, match='not_compiler_output'): H.verify_film_project(files, expected_manifest_sha256=checksum)


def test_external_digest_and_exact_inventory_are_required_even_after_source_rehashing():
    _, files, checksum, _ = compiled()
    altered = dict(files); altered['recordings/000.wav'] += b'changed'
    with pytest.raises(ServiceError, match='resource_changed'): H.verify_film_project(altered, expected_manifest_sha256=checksum)
    altered = dict(files); altered['undeclared.mjs'] = b'code'
    with pytest.raises(ServiceError, match='inventory_mismatch'): H.verify_film_project(altered, expected_manifest_sha256=checksum)
    altered, _ = rehashed_manifest(files, lambda row: row['film'].update(title='Later project head'))
    with pytest.raises(ServiceError, match='manifest_binding_mismatch'): H.verify_film_project(altered, expected_manifest_sha256=checksum)


@pytest.mark.parametrize('budget', ['MAX_SOURCE_BYTES', 'MAX_ASSET_BYTES', 'MAX_RECORDING_BYTES', 'MAX_TOTAL_BYTES', 'MAX_FILES', 'MAX_MANIFEST_BYTES'])
def test_resource_limits_are_applied_to_real_bytes_and_complete_inventory(monkeypatch, budget):
    value = inputs()
    if budget == 'MAX_ASSET_BYTES':
        value['modules'][0]['assets'] = ['assets/object.bin']; value['assets']['assets/object.bin'] = b'object'
    monkeypatch.setattr(H, budget, 1)
    with pytest.raises(ServiceError): H.compile_film_project(**value)


@pytest.mark.parametrize('budget', ['MAX_SOURCE_FILE_BYTES', 'MAX_MODULE_BYTES', 'MAX_MODULES', 'MAX_SCENES'])
def test_module_and_scene_limits_fail_before_compiling_an_oversized_project(monkeypatch, budget):
    value = inputs()
    monkeypatch.setattr(H, budget, 1)
    with pytest.raises(ServiceError): H.compile_film_project(**value)


@pytest.mark.parametrize('frame', [-1, True, 1.5, 99999])
def test_frame_queries_refuse_invalid_frames(frame):
    _, files, checksum, _ = compiled()
    with pytest.raises(ServiceError, match='frame_invalid'): H.frame_context(files, frame, expected_manifest_sha256=checksum)
