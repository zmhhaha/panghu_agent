"""Stopped handoff assembly fixtures, without browser or live Agent calls."""
from copy import deepcopy
from pathlib import Path

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.film_production_assembly import assemble_film_production
from video_workbench.creative_film_module_contract import predecessor_from_package
from video_workbench.hyperframes_film_browser import HOST_NAMES
from video_workbench.hyperframes_film_snapshot import decode_film_execution_snapshot, sha
from test_creative_film_module_contract import stage, collected, raw_result, recordings_and_film, RUNTIME


def assembly_input(tmp_path, *, transform=False, wrong_predecessor=False):
    first = collected(stage(tmp_path))[2]
    previous = predecessor_from_package(first)
    if wrong_predecessor:
        previous['result_sha256'] = 'a' * 64
    staged = stage(tmp_path, index=1, predecessor=previous)
    second = collected(staged, raw_result(staged[1], transform=transform))[2]
    film, recordings = recordings_and_film()
    root = Path(__file__).resolve().parents[2] / 'video_workbench/motion_renderer'
    hosts = {name: (root / name).read_bytes() for name in HOST_NAMES}
    packages = [first, second]
    return dict(packages=packages, film=film, film_sha256=digest(film), recordings=recordings,
        assets={}, runtime_fingerprint=deepcopy(RUNTIME),
        runtime_identity=deepcopy(first['input_document']['constraints']['runtime_identity']),
        module_targets=[deepcopy(package['input_document']['target']) for package in packages],
        host_files=hosts, trusted_host_sha256={name: sha(raw) for name, raw in hosts.items()},
        image='registry.internal/video-motion-renderer:latest@' + RUNTIME['image_digest'],
        project_revision=2, production_run_id='run-whole-film')


@pytest.mark.parametrize('transform', [False, True])
def test_two_exact_stopped_modules_freeze_one_snapshot_preserving_originals(tmp_path, transform):
    value = assembly_input(tmp_path, transform=transform)
    before = deepcopy(value)
    result = assemble_film_production(**value)
    assert value == before
    snapshot, manifest = result['snapshot'], result['manifest']
    files = decode_film_execution_snapshot(snapshot, expected_snapshot_sha256=snapshot['sha256'],
        **{key: value[key] for key in ('runtime_fingerprint', 'trusted_host_sha256', 'image')})
    assert snapshot['clock']['frame_count'] == 48
    assert len(snapshot['module_sha256']) == (3 if transform else 2)
    for index, recording in enumerate(value['recordings']):
        assert files[f'project/recordings/{index:03d}.wav'] == recording['data']
    for package in value['packages']:
        for name, source in package['result']['module']['files'].items():
            assert files['project/' + name] == source.encode()
    assert not any(name.endswith('.mp4') for name in files)
    assert manifest['snapshot_sha256'] == snapshot['sha256']
    assert manifest['sources'][0]['package_sha256'] == digest(value['packages'][0])
    assert manifest['execution']['status'] == 'not_submitted'
    assert manifest['execution']['concatenated_scene_videos'] is False
    assert manifest['sha256'] == digest({key: item for key, item in manifest.items() if key != 'sha256'})
    assert assemble_film_production(**value) == result


def test_structurally_valid_predecessor_must_be_the_actual_collected_previous_result(tmp_path):
    value = assembly_input(tmp_path, wrong_predecessor=True)
    with pytest.raises(ServiceError, match='exact_predecessor_mismatch'):
        assemble_film_production(**value)


@pytest.mark.parametrize('change', ['order', 'missing', 'extra', 'film', 'target', 'runtime', 'image', 'host', 'recording'])
def test_assembly_rejects_mixed_frozen_inputs_before_execution(tmp_path, change):
    value = assembly_input(tmp_path)
    if change == 'order': value['packages'].reverse()
    elif change == 'missing': value['packages'].pop()
    elif change == 'extra': value['packages'].append(value['packages'][0])
    elif change == 'film':
        value['film']['title'] = 'An unrelated frozen film'
        value['film_sha256'] = digest(value['film'])
    elif change == 'target': value['module_targets'][0]['module_id'] = 'different-host-target'
    elif change == 'runtime': value['runtime_identity']['host_sha256'] = 'b' * 64
    elif change == 'image': value['image'] = value['image'].replace(':latest@', ':version-1@')
    elif change == 'host': value['host_files']['hyperframes_film.mjs'] += b'\n// changed host'
    else:
        raw = value['recordings'][0]['data']
        value['recordings'][0]['data'] = raw[:-1] + bytes([raw[-1] ^ 1])
    with pytest.raises(ServiceError):
        assemble_film_production(**value)


def test_available_unused_asset_is_recorded_without_adding_it_to_executable_bundle(tmp_path):
    value = assembly_input(tmp_path)
    value['assets'] = {'assets/available.txt': b'available but unused'}
    result = assemble_film_production(**value)
    assert result['manifest']['available_asset_sha256'] == {'assets/available.txt': sha(b'available but unused')}
    assert result['manifest']['project_asset_sha256'] == {}


@pytest.mark.parametrize('name', ['../escape', 'assets/../escape', 'assets//empty.txt', 'assets/./dot.txt', 'assets\\wrong.txt'])
def test_even_unused_asset_names_cannot_escape_the_asset_namespace(tmp_path, name):
    value = assembly_input(tmp_path)
    value['assets'] = {name: b'not an admitted resource path'}
    with pytest.raises(ServiceError, match='asset_inventory_invalid'):
        assemble_film_production(**value)
