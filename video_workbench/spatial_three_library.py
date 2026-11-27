"""Pinned Three vertex appearance adapter for an explicitly selected release.

Descriptors describe authorable mechanisms. Installed bytes, executor admission,
actual execution and visual acceptance remain separate checks.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from agent_service.contracts import ServiceError, canonical_bytes

SCHEMA = 'video.three-spatial-library.v1'
CAPABILITY_SCHEMA = 'video.animation-runtime-capabilities.v4'
LIBRARY_ID = 'three-spatial'
FEATURES = frozenset({'lighting', 'texture', 'atmosphere'})
CODE_ASSET_NAMES = frozenset({'spatial_three_adapter.mjs', 'scene_space_surface.mjs',
    'scene_space_surface_budget.mjs'})
METADATA_ASSET = 'spatial-three-library.json'
METADATA_SCHEMA = 'video.three-spatial-library-metadata.v1'
ASSET_NAMES = CODE_ASSET_NAMES | {METADATA_ASSET}
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def _require(ok):
    if not ok:
        raise ServiceError('film_spatial_library_not_admitted', 422)


def read_assets():
    root = Path(__file__).parent / 'motion_renderer'
    result = {}
    for name in sorted(CODE_ASSET_NAMES):
        path = root / name
        _require(not path.is_symlink() and path.is_file())
        raw = path.read_bytes()
        _require(0 < len(raw) <= 1024 * 1024)
        raw.decode('utf-8')
        result[name] = raw
    # The metadata carries only the three code pins and policy. Its own pin is
    # added by descriptor(), avoiding a self-referential hash. These bytes are
    # frozen into each actual project and are sufficient for later recompiles.
    result[METADATA_ASSET] = canonical_bytes(dict(schema=METADATA_SCHEMA,
        library=_descriptor(result), policy=material_policy()))
    return result


def _descriptor(assets):
    return dict(schema=SCHEMA, library_id=LIBRARY_ID, revision=1, engines=['three'],
        entrypoint='./assets/spatial_three_adapter.mjs', features=sorted(FEATURES),
        scope='bounded_cpu_vertex_colors_fixed_object_material_world_light_and_distance_color',
        assets={name: hashlib.sha256(raw).hexdigest() for name, raw in assets.items()})


def descriptor():
    return _descriptor(read_assets())


def _validate_spec(item, assets, names):
    _require(type(item) is dict and set(item) == {'schema', 'library_id', 'revision', 'engines',
        'entrypoint', 'features', 'scope', 'assets'})
    _require(item['schema'] == SCHEMA and item['library_id'] == LIBRARY_ID
        and type(item['revision']) is int and item['revision'] == 1
        and item['engines'] == ['three'] and item['entrypoint'] == './assets/spatial_three_adapter.mjs'
        and item['features'] == sorted(FEATURES)
        and item['scope'] == 'bounded_cpu_vertex_colors_fixed_object_material_world_light_and_distance_color'
        and type(item['assets']) is dict and set(item['assets']) == names
        and all(type(pin) is str and _SHA.fullmatch(pin) and assets.get(name) == pin
            for name, pin in item['assets'].items()))


def _validate_policy(value):
    _require(type(value) is dict and set(value) == {'schema', 'requested_parameters',
        'actual_parameters', 'material_noise_limit', 'estimated_material_samples'})
    _require(value['schema'] == 'video.scene-appearance-policy.v1'
        and value['requested_parameters'] == {'material_quality': 'full'}
        and type(value['actual_parameters']) is dict
        and set(value['actual_parameters']) == {'material_quality'}
        and type(value['actual_parameters']['material_quality']) is str
        and value['actual_parameters']['material_quality'] in {'full', 'coarse'}
        and type(value['material_noise_limit']) is int
        and 1 <= value['material_noise_limit'] <= 64 * 1024 * 1024
        and type(value['estimated_material_samples']) is int
        and 0 <= value['estimated_material_samples'] <= 64 * 1024 * 1024)


def validate_libraries(value, assets, *, frozen=False):
    _require(type(value) is dict and set(value) == {LIBRARY_ID} and type(assets) is dict)
    item = value[LIBRARY_ID]
    _validate_spec(item, assets, ASSET_NAMES)
    if not frozen:
        _require(item == descriptor())
    return deepcopy(value)


def enable_in_capabilities(capabilities):
    from .animation_runtime_capabilities import validate_runtime_capabilities
    value = validate_runtime_capabilities(capabilities)
    _require('three' in value['allowed_engines'])
    spec = descriptor()
    value.update(schema=CAPABILITY_SCHEMA, spatial_libraries={LIBRARY_ID: spec})
    value.setdefault('authoring_libraries', {})
    value.setdefault('drawing_libraries', {})
    value['assets'].update(spec['assets'])
    return validate_runtime_capabilities(value)


def available_features(capabilities, engine, *, frozen=False):
    if engine != 'three' or not capabilities.get('spatial_libraries'):
        return frozenset()
    _require(capabilities.get('schema') == CAPABILITY_SCHEMA
        and 'three' in capabilities.get('allowed_engines', []))
    validate_libraries(capabilities['spatial_libraries'], capabilities['assets'], frozen=frozen)
    return FEATURES


def material_policy():
    # Immutable per-realm allowance installed by the release host before author
    # import. This counts helper work, not model tokens or total CPU seconds.
    return dict(schema='video.scene-appearance-policy.v1',
        requested_parameters={'material_quality': 'full'},
        actual_parameters={'material_quality': 'full'},
        material_noise_limit=3_000_000, estimated_material_samples=1_000_000)


def module_policy(engine, used_assets, project_files):
    """Use only the verified original project's bytes, never today's release.

    Project verification already binds every raw asset to its frozen inventory.
    Metadata additionally binds the exact three code dependencies and original
    host policy, so upgrading installed source cannot silently alter a replay.
    """
    present = {name for name in ASSET_NAMES if 'assets/' + name in used_assets}
    if not present:
        return None
    _require(engine == 'three' and present == ASSET_NAMES)
    original = {name: project_files.get('assets/' + name) for name in ASSET_NAMES}
    _require(all(type(raw) is bytes and 0 < len(raw) <= 1024 * 1024 for raw in original.values()))
    raw = original[METADATA_ASSET]
    _require(len(raw) <= 65536)
    try:
        metadata = json.loads(raw)
        normalized = canonical_bytes(metadata)
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise ServiceError('film_spatial_library_not_admitted', 422) from None
    _require(type(metadata) is dict and set(metadata) == {'schema', 'library', 'policy'}
        and metadata['schema'] == METADATA_SCHEMA and normalized == raw)
    pins = {name: hashlib.sha256(content).hexdigest() for name, content in original.items()}
    _validate_spec(metadata['library'], pins, CODE_ASSET_NAMES)
    _validate_policy(metadata['policy'])
    expected = dict(metadata['library'], assets=pins)
    _require({'assets/three.module.min.js', 'assets/three.LICENSE.txt'} <= set(used_assets))
    return dict(library=expected, policy=deepcopy(metadata['policy']))


def attach_spatial_libraries(core, capabilities):
    from agent_service.profiles.video_animation_film_module_contract import validate_core_task
    core = validate_core_task(core)
    if core['constraints']['allowed_engines'] != ['three'] or not available_features(capabilities, 'three'):
        return core
    libraries = capabilities['spatial_libraries']
    spec = libraries[LIBRARY_ID]
    _require(all(core['assets'].get('assets/' + name) == pin for name, pin in spec['assets'].items()))
    guidance = (
        'Optional pinned Three vertex-appearance adapter: import createThreeAppearanceAdapter from '
        '"/project/assets/spatial_three_adapter.mjs". Use the admitted THREE namespace and a single '
        'non-skinned/non-morph BufferGeometry Mesh with positions, normals and MeshBasicMaterial. '
        'Construct in mount with explicit seed and world surface-to-light direction. In renderAt '
        'derive object/camera matrices from absolute time, then call adapter.update({camera}) before '
        'renderer.render. Dispose the adapter before owned original resources. The release host '
        'installs its immutable material policy before module import. Declare all four descriptor '
        'assets plus the existing Three module/license. Do not replace or inline these assets. '
        'Object-space seeded material stays fixed; world normals use the inverse-transpose matrix; '
        'atmosphere only mixes RGB using world distance/height and never changes solid coverage. '
        'The adapter changes vertex colors, not PBR, cast shadows, silhouette geometry, physical '
        'scattering, DOF or blur. Per-instance vertex/update limits and canonical per-realm material '
        'work limits apply; these are not a total film CPU or token cap. Availability and observed '
        'calls do not prove artistic quality.\nAPP-FROZEN-SPATIAL-LIBRARIES\n'
        + canonical_bytes(libraries).decode('utf-8') + '\nEND-APP-FROZEN-SPATIAL-LIBRARIES')
    instruction = core['scene']['instruction']
    core['scene']['instruction'] = instruction + ('\n\n' if instruction else '') + guidance
    if len(core['scene']['instruction']) > 16000:
        raise ServiceError('film_authoring_instruction_too_large', 413)
    return validate_core_task(core)
