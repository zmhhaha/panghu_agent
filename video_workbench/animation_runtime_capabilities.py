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
MAX_CANVAS_CONTEXTS = 500


def drawing_context_budget():
    """Disclose the native guard's per-page/realm limit without granting engines."""
    return dict(max_contexts=MAX_CANVAS_CONTEXTS,
        scope='unique_native_contexts_per_page_or_module_realm',
        instruction=(
            f'At most {MAX_CANVAS_CONTEXTS} distinct native drawing contexts may be created in one page or module realm. '
            'Count the visible canvas and every detached or internal canvas buffer, including '
            'Canvas 2D and admitted WebGL2 contexts. Repeating getContext on the same canvas '
            'when it returns the same native context does not consume another slot. Removing '
            'a canvas or dropping a reference does not refund a slot. Use bounded buffers or '
            'atlases when useful; this count does not expand memory, time or engine admission.'))


def with_drawing_context_guidance(instruction, allowed_engines):
    """Keep the existing frozen production schema and preserve user direction."""
    if not set(allowed_engines) & {'canvas', 'three'}:
        return instruction
    guidance = 'Native drawing runtime constraint: ' + drawing_context_budget()['instruction']
    return instruction + ('\n\n' if instruction else '') + guidance


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
