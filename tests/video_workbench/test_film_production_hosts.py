"""Keep the existing admitted film release usable after an optional host update."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench import film_production_hosts as hosts
from video_workbench.film_production_config import FilmProductionSettings
from video_workbench.ink_landscape_library import descriptor, read_assets
from test_film_production_config import release


@pytest.fixture
def image_hosts(tmp_path, monkeypatch):
    root = tmp_path / 'image-hosts'
    root.mkdir()
    # A Linux image uses Git's canonical LF blobs. Windows checkouts can use
    # CRLF for the five unchanged hosts; fixtures reproduce the image bytes.
    for name in hosts.HOST_NAMES:
        (root / name).write_bytes((hosts.ROOT / name).read_bytes().replace(b'\r\n', b'\n'))
    (root / 'legacy').mkdir()
    (root / hosts.LEGACY_REALM).write_bytes((hosts.ROOT / hosts.LEGACY_REALM).read_bytes())
    monkeypatch.setattr(hosts, 'ROOT', root)
    return root


def hashes(root):
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in hosts.HOST_NAMES}


def legacy_release(tmp_path):
    value, path = release(tmp_path)
    pinned = deepcopy(hosts.LEGACY_V3_HOSTS)
    value['trusted_host_sha256'] = pinned
    admission = value['execution_admission']
    admission['trusted_host_sha256'] = deepcopy(pinned)
    admission['sha256'] = digest({key: item for key, item in admission.items() if key != 'sha256'})
    capabilities = value['runtime_capabilities']
    capabilities.update(schema='video.animation-runtime-capabilities.v3', authoring_libraries={},
        drawing_libraries={'ink-landscape': descriptor()}, assets=descriptor()['assets'])
    capabilities['runtime_identity']['host_sha256'] = digest(pinned)
    path.write_text(json.dumps(value), encoding='utf-8')
    assets = tmp_path / 'assets'
    assets.mkdir()
    for name, raw in read_assets().items():
        (assets / name).write_bytes(raw)
    return value, path, assets


def test_preserved_realm_is_the_exact_original_admitted_blob():
    assert hashlib.sha256((hosts.ROOT / hosts.LEGACY_REALM).read_bytes()).hexdigest() == hosts.LEGACY_V3_HOSTS['hyperframes_film_realm.mjs']


@pytest.mark.parametrize('schema', ['video.animation-runtime-capabilities.v1', 'video.animation-runtime-capabilities.v4'])
def test_current_hosts_take_priority_without_reading_legacy(image_hosts, schema):
    pinned = hashes(image_hosts)
    (image_hosts / hosts.LEGACY_REALM).unlink()
    selected = hosts.read_image_host_files(pinned, dict(schema=schema, spatial_libraries={'current-only': {}}))
    assert {name: hashlib.sha256(raw).hexdigest() for name, raw in selected.items()} == pinned


def test_existing_v3_full_configuration_keeps_exact_admission_and_feature_gate(image_hosts, tmp_path):
    value, path, assets = legacy_release(tmp_path)
    before = path.read_bytes()
    selected = FilmProductionSettings.from_env(dict(VIDEO_FILM_PRODUCTION_ENABLED='1',
        VIDEO_FILM_PRODUCTION_RELEASE_FILE=str(path), VIDEO_FILM_PRODUCTION_ASSET_DIRECTORY=str(assets)))
    assert selected.enabled is True
    assert selected.trusted_host_sha256 == hosts.LEGACY_V3_HOSTS
    assert selected.runtime_capabilities == value['runtime_capabilities']
    assert selected.admission == value['execution_admission']
    assert {name: hashlib.sha256(raw).hexdigest() for name, raw in selected.host_files.items()} == hosts.LEGACY_V3_HOSTS
    assert selected.host_files['hyperframes_film_realm.mjs'] != (image_hosts / 'hyperframes_film_realm.mjs').read_bytes()
    assert path.read_bytes() == before


@pytest.mark.parametrize('capabilities', [
    {'schema': 'video.animation-runtime-capabilities.v4', 'spatial_libraries': {}},
    {'schema': 'video.animation-runtime-capabilities.v3', 'spatial_libraries': {'three-spatial': {}}},
    {'schema': 'video.animation-runtime-capabilities.v1'},
])
def test_old_hosts_cannot_authorize_a_different_capability_scope(image_hosts, capabilities):
    with pytest.raises(ValueError):
        hosts.read_image_host_files(hosts.LEGACY_V3_HOSTS, capabilities)


@pytest.mark.parametrize('mutation', ['unknown_pin', 'missing_pin', 'missing_legacy', 'changed_legacy', 'changed_other_host'])
def test_compatibility_rejects_unreviewed_or_incomplete_host_sets(image_hosts, mutation):
    pinned = deepcopy(hosts.LEGACY_V3_HOSTS)
    if mutation == 'unknown_pin':
        pinned['hyperframes_film_root.mjs'] = 'a' * 64
    elif mutation == 'missing_pin':
        pinned.pop('hyperframes_film_root.mjs')
    elif mutation == 'missing_legacy':
        (image_hosts / hosts.LEGACY_REALM).unlink()
    elif mutation == 'changed_legacy':
        (image_hosts / hosts.LEGACY_REALM).write_bytes(b'changed old executable host')
    else:
        (image_hosts / 'hyperframes_film_channel.mjs').write_bytes(b'changed current shared host')
    with pytest.raises((ValueError, ServiceError)):
        hosts.read_image_host_files(pinned, {'schema': 'video.animation-runtime-capabilities.v3'})


def test_legacy_selection_does_not_bypass_actual_asset_validation(image_hosts, tmp_path):
    _, path, assets = legacy_release(tmp_path)
    (assets / 'ink-landscape.mjs').write_bytes(b'unadmitted bytes')
    with pytest.raises(ValueError, match='does not match installed hosts and assets'):
        FilmProductionSettings.from_env(dict(VIDEO_FILM_PRODUCTION_ENABLED='1',
            VIDEO_FILM_PRODUCTION_RELEASE_FILE=str(path), VIDEO_FILM_PRODUCTION_ASSET_DIRECTORY=str(assets)))
