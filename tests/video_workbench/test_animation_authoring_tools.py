from copy import deepcopy

import pytest

from agent_service.contracts import digest
from video_workbench.animation_authoring_tools import observed_authoring_tools
from video_workbench.animation_method_service import _sample_engine
from video_workbench.animejs_library import descriptor


def runtime():
    libraries = {'animejs': descriptor()}
    return dict(engine='canvas', admission={'status': 'admitted'}, authoring_libraries=libraries,
        authoring_library_observation=dict(scope='loaded_scripts_not_api_calls_or_visual_application',
            authoring_libraries=deepcopy(libraries), used_libraries=['animejs'],
            loaded_scripts=['/assets/anime.esm.min.js', '/assets/anime_timeline.mjs']))


def test_observed_anime_library_is_a_separate_tool_not_a_drawing_engine():
    assert observed_authoring_tools(runtime()) == [dict(id='animejs', label='Anime.js',
        version=descriptor()['version'], status='loaded',
        evidence_scope='render_loaded_dependency_not_visual_application_or_acceptance')]


@pytest.mark.parametrize('change', ['missing_observation', 'requirements_only', 'not_loaded',
    'wrong_sha', 'wrong_scope', 'missing_helper', 'missing_official_library'])
def test_declared_or_incomplete_library_is_not_reported_as_observed(change):
    value = runtime()
    if change == 'missing_observation':
        value.pop('authoring_library_observation')
    elif change == 'requirements_only':
        value = dict(runtime_requirements={'authoring_libraries': value['authoring_libraries']})
    elif change == 'not_loaded':
        value['authoring_library_observation']['used_libraries'] = []
    elif change == 'wrong_sha':
        value['authoring_libraries']['animejs']['assets']['anime.esm.min.js'] = 'f' * 64
    elif change == 'wrong_scope':
        value['authoring_library_observation']['scope'] = 'agent_declared'
    else:
        name = '/assets/anime_timeline.mjs' if change == 'missing_helper' else '/assets/anime.esm.min.js'
        value['authoring_library_observation']['loaded_scripts'].remove(name)
    assert observed_authoring_tools(value) == []


def fixture():
    candidate = dict(id='candidate', source={'engine': 'canvas'}, operation_id='render',
        media={'sha256': 'a' * 64}, technical={'status': 'passed'})
    demonstration = dict(source={'engine': 'canvas'}, runtime=runtime(),
        media={'sha256': 'a' * 64}, technical={'status': 'passed'},
        provenance={'candidate_id': 'candidate', 'render_operation_id': 'render'})
    return candidate, {'candidate': [demonstration]}


def test_tools_projection_requires_exact_render_and_media_binding():
    candidate, demos = fixture()
    view = _sample_engine(candidate, demos)
    assert view['engine'] == 'canvas' and view['engine_status'] == 'rendered'
    assert view['authoring_tools'][0]['id'] == 'animejs'
    demos['candidate'][0]['media']['sha256'] = 'b' * 64
    assert _sample_engine(candidate, demos) == dict(engine=None, engine_status='conflict')


def test_old_sample_shape_is_preserved_without_library_observation():
    candidate, demos = fixture()
    demos['candidate'][0]['runtime'].pop('authoring_library_observation')
    assert _sample_engine(candidate, demos) == dict(engine='canvas', engine_status='rendered')


def manim_runtime():
    from video_workbench.manim_resource import MANIM_VERSION, sha
    from video_workbench.manim_method_resources import helper_assets
    helpers = {name: sha(raw) for name, raw in helper_assets().items()}
    assets = {'manim/example/frame_000000.png': 'a' * 64, **helpers}
    item = dict(schema='video.manim-method-resource.v1', assets_sha256=assets,
        helpers=helpers,
        manifest=dict(runtime=dict(library='manim-community', version=MANIM_VERSION),
            frame_count=1, frames=[dict(path='assets/manim/example/frame_000000.png')]))
    item['sha256'] = digest(item)
    return dict(generated_resources=[item], generated_resource_observation=dict(
        schema='video.manim-method-resource-observation.v1',
        scope='loaded_resources_not_visual_acceptance', helpers_loaded=True,
        resources_sha256=[item['sha256']], loaded_png_paths=['/assets/manim/example/frame_000000.png']))


def test_manim_badge_requires_exact_generated_resource_and_observed_frames():
    from video_workbench.manim_resource import MANIM_VERSION
    assert observed_authoring_tools(manim_runtime()) == [dict(id='manim', label='Manim',
        version=MANIM_VERSION, status='loaded',
        evidence_scope='render_loaded_dependency_not_visual_application_or_acceptance')]


def _reseal_resource(value):
    item = value['generated_resources'][0]
    item['sha256'] = digest({key: content for key, content in item.items() if key != 'sha256'})
    value['generated_resource_observation']['resources_sha256'] = [item['sha256']]


def test_historical_manim_render_uses_its_own_frozen_helper_inventory():
    """Upgrading consumer helpers must not erase a proven stored media label."""
    value = manim_runtime()
    item = value['generated_resources'][0]
    historical = {'manim_frame_layer.mjs': 'e6785bc26df55a4b4e88fdca2bc74c629643a6b4296ac9eb5970c3221bde1fd5',
        'manim_method_frames.mjs': '9f3aa078149af3f9f7c3fb7d590b00f3dc9e05605b0c6fb2982a6e91fa1ba6cc'}
    item['helpers'] = historical
    item['assets_sha256'].update(historical)
    _reseal_resource(value)
    original = deepcopy(value)
    assert [tool['id'] for tool in observed_authoring_tools(value)] == ['manim']
    candidate, demos = fixture()
    demos['candidate'][0]['runtime'].update(value)
    view = _sample_engine(candidate, demos)
    assert view['engine_status'] == 'rendered'
    assert [tool['id'] for tool in view['authoring_tools']] == ['animejs', 'manim']
    assert value == original


@pytest.mark.parametrize('change', ['missing_helper_inventory', 'changed_helper_inventory',
    'malformed_helper_digest', 'extra_helper', 'requested_only'])
def test_historical_manim_without_exact_frozen_consumption_stays_unknown(change):
    value = manim_runtime()
    item = value['generated_resources'][0]
    name = 'manim_frame_layer.mjs'
    if change == 'missing_helper_inventory':
        item['assets_sha256'].pop(name)
    elif change == 'changed_helper_inventory':
        item['assets_sha256'][name] = 'f' * 64
    elif change == 'malformed_helper_digest':
        item['helpers'][name] = item['assets_sha256'][name] = 'not-a-digest'
    elif change == 'extra_helper':
        item['helpers']['unobserved.mjs'] = 'f' * 64
    else:
        value.pop('generated_resource_observation')
        value['resource_authoring'] = {'kind': 'manim'}
    if 'generated_resource_observation' in value:
        _reseal_resource(value)
    assert observed_authoring_tools(value) == []


def test_historical_display_never_admits_old_helpers_for_new_generation():
    """Read compatibility does not relax the independent producer boundary."""
    from agent_service.contracts import ServiceError, canonical_bytes
    from video_workbench.manim_method_resources import helper_assets, validate_descriptor
    from video_workbench.manim_resource import sha

    value = manim_runtime()
    item = value['generated_resources'][0]
    item['manifest']['global_start_frame'] = 0
    item.update(scope=dict(owner='fixture', effect_id='fixture', experiment_id='fixture', operation_id='fixture'),
        envelope_sha256='b' * 64, image_digest='sha256:' + 'c' * 64,
        manifest_path='assets/manim/example/manifest.json', manifest_sha256=digest(item['manifest']))
    assets = {**helper_assets(), 'manim/example/frame_000000.png': b'fixture inventory, not a PNG admission',
        'manim/example/manifest.json': canonical_bytes(item['manifest'])}
    item['assets_sha256'] = {name: sha(raw) for name, raw in assets.items()}
    _reseal_resource(value)
    assert validate_descriptor(item, assets=assets, duration_seconds=1) == item

    for name in item['helpers']:
        assets[name] = ('previous stopped helper ' + name).encode()
        item['helpers'][name] = item['assets_sha256'][name] = sha(assets[name])
    _reseal_resource(value)
    assert [tool['id'] for tool in observed_authoring_tools(value)] == ['manim']
    with pytest.raises(ServiceError, match='manim_method_resource_helper_changed'):
        validate_descriptor(item, assets=assets, duration_seconds=1)


@pytest.mark.parametrize('change', ['missing_observation', 'wrong_scope', 'helpers_not_loaded',
    'wrong_resource_sha', 'missing_png', 'extra_png', 'changed_descriptor', 'wrong_version'])
def test_unproven_manim_resource_has_no_tool_badge(change):
    value = manim_runtime()
    observation = value['generated_resource_observation']
    if change == 'missing_observation':
        value.pop('generated_resource_observation')
    elif change == 'wrong_scope':
        observation['scope'] = 'agent_declared'
    elif change == 'helpers_not_loaded':
        observation['helpers_loaded'] = False
    elif change == 'wrong_resource_sha':
        observation['resources_sha256'] = ['f' * 64]
    elif change == 'missing_png':
        observation['loaded_png_paths'] = []
    elif change == 'extra_png':
        observation['loaded_png_paths'].append('/assets/manim/other/frame_000000.png')
    else:
        item = value['generated_resources'][0]
        item['manifest']['runtime']['version'] = 'unknown'
        if change == 'wrong_version':
            item['sha256'] = digest({key: content for key, content in item.items() if key != 'sha256'})
            observation['resources_sha256'] = [item['sha256']]
    assert observed_authoring_tools(value) == []


def test_anime_and_manim_are_independent_observed_tools():
    value = runtime()
    value.update(manim_runtime())
    assert [tool['id'] for tool in observed_authoring_tools(value)] == ['animejs', 'manim']
