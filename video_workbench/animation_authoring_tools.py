"""Read projections of observed render dependencies, never requested capabilities.

The caller must first bind the demonstration to the exact candidate, media and
stopped render operation. Loading a library does not establish visual quality.
"""
from agent_service.contracts import digest
from .animejs_library import descriptor


def observed_authoring_tools(runtime):
    if type(runtime) is not dict:
        return []
    tools = _observed_animejs(runtime)
    manim = _observed_manim(runtime)
    if manim is not None:
        tools.append(manim)
    return tools


def _observed_animejs(runtime):
    expected = descriptor()
    observation = runtime.get('authoring_library_observation')
    if (runtime.get('authoring_libraries') != {'animejs': expected}
            or type(observation) is not dict
            or observation.get('scope') != 'loaded_scripts_not_api_calls_or_visual_application'
            or observation.get('authoring_libraries') != {'animejs': expected}
            or observation.get('used_libraries') != ['animejs']
            or type(observation.get('loaded_scripts')) is not list
            or not {'/assets/anime_timeline.mjs', '/assets/anime.esm.min.js'}
                <= set(path for path in observation['loaded_scripts'] if type(path) is str)):
        return []
    return [dict(id='animejs', label='Anime.js', version=expected['version'], status='loaded',
        evidence_scope='render_loaded_dependency_not_visual_application_or_acceptance')]


def _observed_manim(runtime):
    """Project only the exact stopped resource observed by the admitted host."""
    from .manim_resource import MANIM_VERSION
    from .manim_method_resources import HELPERS

    resources = runtime.get('generated_resources')
    observation = runtime.get('generated_resource_observation')
    if (type(resources) is not list or len(resources) != 1
            or type(observation) is not dict
            or observation.get('schema') != 'video.manim-method-resource-observation.v1'
            or observation.get('scope') != 'loaded_resources_not_visual_acceptance'
            or observation.get('helpers_loaded') is not True):
        return None
    item = resources[0]
    if type(item) is not dict or item.get('schema') != 'video.manim-method-resource.v1':
        return None
    manifest = item.get('manifest')
    assets = item.get('assets_sha256')
    helpers = item.get('helpers')
    if (item.get('sha256') != digest({key: value for key, value in item.items() if key != 'sha256'})
            or observation.get('resources_sha256') != [item['sha256']]
            or type(assets) is not dict or any(type(name) is not str for name in assets)
            or type(manifest) is not dict
            or type(manifest.get('runtime')) is not dict
            or manifest['runtime'].get('library') != 'manim-community'
            or manifest['runtime'].get('version') != MANIM_VERSION
            or type(helpers) is not dict or set(helpers) != set(HELPERS)
            or any(type(checksum) is not str or len(checksum) != 64
                or not set(checksum) <= set('0123456789abcdef')
                or assets.get(name) != checksum for name, checksum in helpers.items())):
        return None
    # Read the exact stopped sample's sealed inventory, which can predate the
    # installed helper revision. New renders still require current helper bytes
    # in manim_method_resources.validate_descriptor and the browser host.
    expected = sorted('/assets/' + name for name in assets if name.endswith('.png'))
    frames = manifest.get('frames')
    if (not expected or type(frames) is not list
            or len(frames) != manifest.get('frame_count')
            or any(type(frame) is not dict or type(frame.get('path')) is not str for frame in frames)
            or sorted('/' + frame['path'] for frame in frames) != expected
            or observation.get('loaded_png_paths') != expected):
        return None
    return dict(id='manim', label='Manim', version=MANIM_VERSION, status='loaded',
        evidence_scope='render_loaded_dependency_not_visual_application_or_acceptance')
