"""Pure validation of runtime capabilities already admitted by a renderer.

This shape check grants no admission. Only the configured trusted renderer may
produce the table; request bodies, source declarations and stored methods may
not broaden it. Callers freeze the returned copy before submitting Agent work.
"""
from copy import deepcopy
import re

from agent_service.contracts import ServiceError
from agent_service.profiles.video_animation_production_contract import (
    THREE_ASSET_SHA256, validate_runtime_identity,
)


SCHEMA = 'video.animation-runtime-capabilities.v1'
ENGINES = frozenset({'html-svg', 'canvas', 'three'})
ASSETS = frozenset({'gsap.min.js', 'three.module.min.js', 'three.LICENSE.txt', 'NotoSansCJK-Regular.ttc'})
SHA = re.compile(r'[0-9a-f]{64}\Z')


def validate_runtime_capabilities(value):
    from .animejs_library import ASSETS as ANIME_ASSETS, validate_libraries
    v2 = type(value) is dict and value.get('schema') == 'video.animation-runtime-capabilities.v2'
    fields = {'schema', 'allowed_engines', 'assets', 'runtime_identity'}
    if v2:
        fields.add('authoring_libraries')
    valid = type(value) is dict and set(value) == fields and type(value['schema']) is str and value['schema'] in {
        SCHEMA, 'video.animation-runtime-capabilities.v2'}
    if not valid:
        raise ServiceError('animation_runtime_capabilities_invalid', 422)
    engines, assets = value['allowed_engines'], value['assets']
    if not (type(engines) is list and bool(engines)
            and all(type(engine) is str and engine in ENGINES for engine in engines)
            and len(engines) == len(set(engines))
            and type(assets) is dict and set(assets) <= (ASSETS | set(ANIME_ASSETS) if v2 else ASSETS)
            and all(type(checksum) is str and SHA.fullmatch(checksum) for checksum in assets.values())):
        raise ServiceError('animation_runtime_capabilities_invalid', 422)
    validate_runtime_identity(value['runtime_identity'])
    if v2:
        validate_libraries(value['authoring_libraries'], assets)
    if 'three' in engines and assets.get('three.module.min.js') != THREE_ASSET_SHA256:
        raise ServiceError('animation_runtime_three_asset_not_admitted', 422)
    return deepcopy(value)
