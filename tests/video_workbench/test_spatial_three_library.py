"""Real asset/frozen-input contracts; synthetic workers are not native draws."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.spatial_rendering_contract import resolve_quality
from video_workbench import spatial_three_library as S
from video_workbench.animation_runtime_capabilities import validate_runtime_capabilities
from video_workbench.film_creation_store import FilmCreationStore
from video_workbench.film_creation_workflow import FilmCreationWorkflow
from video_workbench.film_production_config import FilmProductionSettings
from video_workbench.hyperframes_film_browser import HOST_NAMES, compile_film_browser_bundle, verify_film_browser_bundle
from video_workbench import hyperframes_film_project as H
from video_workbench.spatial_rendering_route import admit_route, film_features
from test_animation_method_service import runtime_capabilities
from test_film_creation_workflow import env, OWNER, module_request
import test_film_creation_workflow as F
from test_film_spatial_requirements import configure_engines, intent, start
from test_hyperframes_film_project import inputs, explicit_clock
from test_hyperframes_film_browser import bundle_inputs
from test_film_production_config import release

ROOT = Path(__file__).resolve().parents[2]


def asset_bytes():
    root = ROOT / 'video_workbench/motion_renderer/vendor'
    return {**S.read_assets(), **{name: (root / name).read_bytes()
        for name in ('three.module.min.js', 'three.LICENSE.txt')}}


def capabilities():
    return S.enable_in_capabilities(runtime_capabilities(['html-svg', 'canvas', 'three']))


def upgraded_env(value):
    configure_engines(value)
    previous = value.creation
    caps = S.enable_in_capabilities(previous.runtime_capabilities)
    assets = {**previous.assets, **{'assets/' + name: raw for name, raw in S.read_assets().items()}}
    creation = FilmCreationStore(value.store, runtime_capabilities=caps, runtime_fingerprint=previous.runtime,
        host_files=previous.host_files, trusted_host_sha256=previous.hosts, image=previous.image,
        assets=assets, enabled=True)
    value.creation = value.workflow.creation = value.gateway.creation = value.renderer.creation = creation


def spatial_request(value):
    request = module_request(value)
    request['rendering_intent'] = intent('three')
    request['rendering_quality'] = resolve_quality(request['rendering_intent'],
        film_features(value.creation.runtime_capabilities, 'three'))
    return request


def with_three_result(monkeypatch):
    original = F.raw_result
    frozen_assets = sorted('assets/' + name for name in asset_bytes())
    def raw_result(document):
        raw = original(document)
        raw['module'].update(engine='three', assets=frozen_assets)
        return raw
    monkeypatch.setattr(F, 'raw_result', raw_result)


def spatial_browser_inputs():
    value = explicit_clock(inputs(), {'scene-0', 'scene-1', 'scene-2'})
    first = value['modules'][0]
    first.update(engine='three', assets=sorted('assets/' + name for name in asset_bytes()))
    value['assets'] = {'assets/' + name: raw for name, raw in asset_bytes().items()}
    value['boundaries'][0]['from_module_sha256'] = H.module_identity(first)
    project = H.compile_film_project(**value)
    return dict(project_files=project,
        expected_project_manifest_sha256=hashlib.sha256(project['manifest.json']).hexdigest(),
        width=640, height=360,
        host_files={name: (ROOT / 'video_workbench/motion_renderer' / name).read_bytes() for name in HOST_NAMES})


def no_current(*args, **kwargs):
    raise AssertionError('frozen recovery consulted current release bytes or policy')


def test_four_assets_bind_code_and_original_policy_without_a_self_hash_cycle():
    raw = S.read_assets();spec = S.descriptor();metadata = json.loads(raw[S.METADATA_ASSET])
    assert set(raw) == set(spec['assets']) == S.ASSET_NAMES
    assert set(metadata['library']['assets']) == S.CODE_ASSET_NAMES
    assert S.METADATA_ASSET not in metadata['library']['assets']
    assert metadata['policy'] == S.material_policy()
    assert raw[S.METADATA_ASSET] == canonical_bytes(metadata)
    assert spec['assets'] == {name: hashlib.sha256(content).hexdigest() for name, content in raw.items()}
    assert S.validate_libraries({S.LIBRARY_ID: spec}, spec['assets']) == {S.LIBRARY_ID: spec}


def test_runtime_v4_adds_exact_spatial_assets_without_changing_legacy_shapes():
    baseline = runtime_capabilities(['html-svg', 'canvas', 'three'])
    original = deepcopy(baseline);checked = validate_runtime_capabilities(baseline)
    assert checked == original and baseline == original
    upgraded = S.enable_in_capabilities(baseline)
    assert upgraded['schema'] == S.CAPABILITY_SCHEMA
    assert upgraded['authoring_libraries'] == upgraded['drawing_libraries'] == {}
    assert set(upgraded['assets']) == set(original['assets']) | S.ASSET_NAMES
    assert upgraded['runtime_identity'] == original['runtime_identity']
    assert baseline == original
    for name in ('spatial_libraries', 'assets'):
        upgraded[name].clear()
    assert S.enable_in_capabilities(baseline)['spatial_libraries']


@pytest.mark.parametrize('version', [2, 3])
def test_spatial_upgrade_preserves_existing_admitted_animation_and_drawing_libraries(version):
    from video_workbench import animejs_library, formal_drawing_libraries
    original = animejs_library.enable_in_capabilities(runtime_capabilities(['html-svg', 'canvas', 'three']))
    if version == 3:
        original = formal_drawing_libraries.enable_in_capabilities(original)
    before = deepcopy(original);upgraded = S.enable_in_capabilities(original)
    assert validate_runtime_capabilities(before) == before and original == before
    assert upgraded['authoring_libraries'] == before['authoring_libraries']
    assert upgraded['drawing_libraries'] == before.get('drawing_libraries', {})
    assert all(upgraded['assets'][name] == pin for name, pin in before['assets'].items())
    assert S.available_features(upgraded, 'three') == S.FEATURES


@pytest.mark.parametrize('field,value', [
    ('authoring_libraries', []), ('authoring_libraries', None),
    ('drawing_libraries', []), ('drawing_libraries', None), ('drawing_libraries', 0),
    ('spatial_libraries', {}), ('spatial_libraries', None)])
def test_new_capability_optional_library_fields_are_strict_dictionaries(field, value):
    caps = capabilities();caps[field] = value
    with pytest.raises(ServiceError): validate_runtime_capabilities(caps)


@pytest.mark.parametrize('legacy_schema', ['video.animation-runtime-capabilities.v1',
    'video.animation-runtime-capabilities.v2', 'video.animation-runtime-capabilities.v3'])
def test_legacy_schemas_cannot_silently_add_the_new_spatial_assets(legacy_schema):
    caps = capabilities();caps['schema'] = legacy_schema;caps.pop('spatial_libraries')
    if legacy_schema.endswith('.v1'):
        caps.pop('drawing_libraries');caps.pop('authoring_libraries')
    elif legacy_schema.endswith('.v2'): caps.pop('drawing_libraries')
    with pytest.raises(ServiceError): validate_runtime_capabilities(caps)


def test_current_feature_selection_rejects_old_or_forged_pins_but_frozen_reads_keep_original():
    caps = capabilities()
    assert S.available_features(caps, 'three') == S.FEATURES
    assert S.available_features(caps, 'canvas') == frozenset()
    forged = deepcopy(caps)
    forged['assets']['spatial_three_adapter.mjs'] = 'a' * 64
    forged['spatial_libraries'][S.LIBRARY_ID]['assets']['spatial_three_adapter.mjs'] = 'a' * 64
    # A shape validator alone is not runtime admission.
    validate_runtime_capabilities(forged)
    with pytest.raises(ServiceError): S.available_features(forged, 'three')
    assert S.available_features(caps, 'three', frozen=True) == S.FEATURES


def test_route_requires_current_complete_descriptor_before_enabling_appearance():
    caps = capabilities();wanted = intent()
    configuration = dict(available=True, renderer_mode='hyperframes-film', runtime_capabilities=caps,
        configuration_sha256='a' * 64)
    result = admit_route(wanted, configuration, audio_mode='silent')
    assert {'lighting', 'texture', 'atmosphere'} <= set(result['quality']['enabled_features'])
    assert result['quality']['degraded_features'] == [{'feature': 'depth_of_field', 'reason': 'capability_unavailable'}]
    configuration['runtime_capabilities']['assets'].pop(S.METADATA_ASSET)
    with pytest.raises(ServiceError): admit_route(wanted, configuration, audio_mode='silent')


def test_actual_frozen_module_input_has_complete_library_custody_and_only_real_three_engine(env):
    upgraded_env(env)
    op = start(env, spatial_request(env))
    durable = env.creation.operation('module', op['id'])
    core = durable['document']['core_task']
    assert core['constraints']['allowed_engines'] == ['three']
    assert {'assets/' + name for name in S.ASSET_NAMES} <= set(core['assets'])
    assert 'APP-FROZEN-SPATIAL-LIBRARIES' in core['scene']['instruction']
    assert canonical_bytes(env.creation.runtime_capabilities['spatial_libraries']).decode() in core['scene']['instruction']
    assert {'lighting', 'texture', 'atmosphere'} <= set(core['constraints']['spatial_rendering']['quality']['enabled_features'])
    assert env.catalog_calls == env.gateway.calls == []


def test_frozen_pending_submission_recovers_without_current_assets_policy_or_new_admission(env, monkeypatch):
    upgraded_env(env);with_three_result(monkeypatch)
    op = start(env, spatial_request(env));env.gateway.lose_submit = True;env.workflow.tick()
    original = canonical_bytes(env.creation.operation('module', op['id'])['document'])
    monkeypatch.setattr(S, 'descriptor', no_current);monkeypatch.setattr(S, 'read_assets', no_current)
    monkeypatch.setattr(S, 'material_policy', no_current)
    recovery = FilmCreationStore.for_recovery(env.store)
    recovery._check_spatial_binding(recovery.operation('module', op['id']))
    workflow = FilmCreationWorkflow(recovery, env.gateway, env.adapter, env.renderer, enabled=False)
    workflow.tick()
    assert workflow.get_module(OWNER, op['id'])['status'] == 'completed'
    assert canonical_bytes(recovery.operation('module', op['id'])['document']) == original
    assert len([call for call in env.gateway.calls if call[0] == 'submit']) == 1


def test_original_browser_bundle_recompiles_exactly_after_current_code_or_policy_upgrade(monkeypatch):
    value = spatial_browser_inputs();files = compile_film_browser_bundle(**value)
    checksum = hashlib.sha256(files['browser-manifest.json']).hexdigest()
    before = deepcopy(files)
    metadata = json.loads(value['project_files']['assets/' + S.METADATA_ASSET])
    realm = files['realms/scene_0/index.html'].decode()
    assert '"material_noise_limit":3000000' in realm
    monkeypatch.setattr(S, 'descriptor', no_current);monkeypatch.setattr(S, 'read_assets', no_current)
    monkeypatch.setattr(S, 'material_policy', no_current)
    policy = S.module_policy('three', ['assets/' + name for name in asset_bytes_without_current()], value['project_files'])
    assert policy['policy'] == metadata['policy']
    assert compile_film_browser_bundle(**value) == before
    assert verify_film_browser_bundle(files, expected_bundle_sha256=checksum)['execution_status'] == 'not_executed'
    assert files == before


def asset_bytes_without_current():
    return S.ASSET_NAMES | {'three.module.min.js', 'three.LICENSE.txt'}


@pytest.mark.parametrize('change', ['missing', 'code', 'code_pin', 'metadata_shape', 'policy', 'policy_mode', 'nonfinite', 'engine'])
def test_module_policy_rejects_partial_assets_wrong_code_and_invalid_original_metadata(change):
    raw = asset_bytes();files = {'assets/' + name: content for name, content in raw.items()}
    used = sorted(files);engine = 'three'
    metadata = json.loads(files['assets/' + S.METADATA_ASSET])
    if change == 'missing': used.remove('assets/' + S.METADATA_ASSET)
    elif change == 'code': files['assets/spatial_three_adapter.mjs'] += b'\n// changed'
    elif change == 'code_pin':
        metadata['library']['assets']['spatial_three_adapter.mjs'] = 'a' * 64
        files['assets/' + S.METADATA_ASSET] = canonical_bytes(metadata)
    elif change == 'metadata_shape':
        metadata['author_supplied'] = True;files['assets/' + S.METADATA_ASSET] = canonical_bytes(metadata)
    elif change == 'policy':
        metadata['policy']['material_noise_limit'] = True;files['assets/' + S.METADATA_ASSET] = canonical_bytes(metadata)
    elif change == 'policy_mode':
        metadata['policy']['actual_parameters']['material_quality'] = []
        files['assets/' + S.METADATA_ASSET] = canonical_bytes(metadata)
    elif change == 'nonfinite':
        metadata['policy']['material_noise_limit'] = float('nan')
        files['assets/' + S.METADATA_ASSET] = json.dumps(metadata).encode()
    else: engine = 'canvas'
    with pytest.raises(ServiceError): S.module_policy(engine, used, files)


@pytest.mark.parametrize('asset', sorted(S.ASSET_NAMES))
def test_existing_project_inventory_rejects_changed_code_or_valid_but_replaced_metadata(asset):
    value = spatial_browser_inputs()
    if asset == S.METADATA_ASSET:
        metadata = json.loads(value['project_files']['assets/' + asset]);metadata['policy']['material_noise_limit'] -= 1
        value['project_files']['assets/' + asset] = canonical_bytes(metadata)
    else: value['project_files']['assets/' + asset] += b'\n// changed'
    with pytest.raises(ServiceError): compile_film_browser_bundle(**value)


def test_unmarked_old_project_has_identical_bundle_and_no_spatial_host_policy(monkeypatch):
    value = bundle_inputs();files = compile_film_browser_bundle(**value)
    checksum = hashlib.sha256(files['browser-manifest.json']).hexdigest()
    monkeypatch.setattr(S, 'descriptor', no_current);monkeypatch.setattr(S, 'read_assets', no_current)
    monkeypatch.setattr(S, 'material_policy', no_current)
    assert all('spatial_appearance' not in raw.decode() for name, raw in files.items() if name.startswith('realms/'))
    assert compile_film_browser_bundle(**value) == files
    verify_film_browser_bundle(files, expected_bundle_sha256=checksum)


def test_new_release_loads_exact_current_four_assets_and_refuses_modified_metadata(tmp_path):
    value, path = release(tmp_path)
    value['runtime_capabilities']['allowed_engines'].append('three')
    vendor = asset_bytes()
    value['runtime_capabilities']['assets'] = {name: hashlib.sha256(raw).hexdigest()
        for name, raw in vendor.items() if name not in S.ASSET_NAMES}
    value['runtime_capabilities'] = S.enable_in_capabilities(value['runtime_capabilities'])
    directory = tmp_path / 'assets';directory.mkdir()
    for name, raw in vendor.items(): (directory / name).write_bytes(raw)
    path.write_bytes(canonical_bytes(value))
    settings = FilmProductionSettings.from_env({'VIDEO_FILM_PRODUCTION_RELEASE_FILE': str(path),
        'VIDEO_FILM_PRODUCTION_ASSET_DIRECTORY': str(directory)})
    assert settings.runtime_capabilities['schema'] == S.CAPABILITY_SCHEMA
    assert all(settings.assets['assets/' + name] == vendor[name] for name in S.ASSET_NAMES)
    metadata = json.loads(vendor[S.METADATA_ASSET]);metadata['policy']['material_noise_limit'] -= 1
    changed = canonical_bytes(metadata);(directory / S.METADATA_ASSET).write_bytes(changed)
    checksum = hashlib.sha256(changed).hexdigest()
    value['runtime_capabilities']['assets'][S.METADATA_ASSET] = checksum
    value['runtime_capabilities']['spatial_libraries'][S.LIBRARY_ID]['assets'][S.METADATA_ASSET] = checksum
    path.write_bytes(canonical_bytes(value))
    with pytest.raises(ValueError): FilmProductionSettings.from_env({
        'VIDEO_FILM_PRODUCTION_RELEASE_FILE': str(path), 'VIDEO_FILM_PRODUCTION_ASSET_DIRECTORY': str(directory)})
