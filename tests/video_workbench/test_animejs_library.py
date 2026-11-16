from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from agent_service.contracts import ServiceError
from video_workbench.animation_runtime_capabilities import validate_runtime_capabilities
from video_workbench.animejs_library import (ASSETS, descriptor, read_assets,
    enable_in_capabilities, foundation_skill_package)
from video_workbench.film_production_config import FilmProductionSettings
from test_film_production_config import release


def test_official_bytes_and_self_contained_unpublished_foundation_are_pinned(tmp_path):
    assets = read_assets()
    assert {name: hashlib.sha256(raw).hexdigest() for name, raw in assets.items()} == ASSETS
    root = Path(__file__).resolve().parents[2] / 'video_workbench/motion_renderer/vendor'
    provenance = json.loads((root / 'animejs-assets.json').read_text())
    assert provenance['version'] == descriptor()['version'] == '4.5.0'
    assert provenance['assets']['anime.esm.min.js']['role'] == 'upstream_unchanged'
    assert {name: row['sha256'] for name,row in provenance['assets'].items()} == ASSETS
    package = foundation_skill_package()
    assert package['kind'] == 'foundation'
    assert package['runtime_requirements']['authoring_libraries'] == {'animejs': descriptor()}
    assert 'publication' not in package
    for name, raw in assets.items(): (tmp_path/name).write_bytes(raw)
    (tmp_path/'anime.esm.min.js').write_bytes(b'changed')
    with pytest.raises(ServiceError, match='bytes_invalid'): read_assets(tmp_path)


def test_v1_is_not_broadened_and_v2_requires_complete_exact_library(tmp_path):
    value, _ = release(tmp_path)
    original = value['runtime_capabilities']
    upgraded = enable_in_capabilities(original)
    assert original['assets'] == {} and original['schema'].endswith('.v1')
    assert upgraded['allowed_engines'] == original['allowed_engines']
    assert validate_runtime_capabilities(upgraded) == upgraded
    for mutation in ['v1', 'missing', 'checksum', 'version', 'engine', 'unknown', 'descriptor', 'empty']:
        bad = deepcopy(upgraded)
        if mutation == 'v1':
            bad['schema'] = original['schema']; del bad['authoring_libraries']
        elif mutation == 'missing': del bad['assets']['animejs.LICENSE.txt']
        elif mutation == 'checksum': bad['assets']['anime_timeline.mjs'] = 'a'*64
        elif mutation == 'version': bad['authoring_libraries']['animejs']['version'] = 'latest'
        elif mutation == 'engine': bad['allowed_engines'].append('animejs')
        elif mutation == 'unknown': bad['assets']['remote.js'] = 'a'*64
        elif mutation == 'descriptor': bad['authoring_libraries']['animejs']['arbitrary'] = True
        else: bad['authoring_libraries'] = {}
        with pytest.raises(ServiceError): validate_runtime_capabilities(bad)


def test_optional_film_release_loads_measured_library_and_rejects_modified_bytes(tmp_path):
    value, path = release(tmp_path)
    value['runtime_capabilities'] = enable_in_capabilities(value['runtime_capabilities'])
    path.write_text(json.dumps(value), encoding='utf-8')
    for name, raw in read_assets().items(): (tmp_path/name).write_bytes(raw)
    env = {'VIDEO_FILM_PRODUCTION_RELEASE_FILE':str(path), 'VIDEO_FILM_PRODUCTION_ASSET_DIRECTORY':str(tmp_path)}
    settings = FilmProductionSettings.from_env(env)
    assert set(settings.assets) == {'assets/'+name for name in ASSETS}
    assert settings.enabled is False
    (tmp_path/'anime_timeline.mjs').write_bytes(b'untrusted adapter')
    with pytest.raises(ValueError, match='release is unavailable'): FilmProductionSettings.from_env(env)
