"""Pinned optional Anime.js authoring assets, independent of drawing engines.

This descriptor verifies bytes and compatibility, never grants execution
admission or developer publication. Frozen capabilities v1 remain unchanged.
"""
from copy import deepcopy
import hashlib
from pathlib import Path

from agent_service.contracts import ServiceError

VERSION = '4.5.0'
ASSETS = {
    'anime.esm.min.js': 'a19015a1a92d52025a2fb6703b6d67eadd1cc2aeaf880770e96e04cf6aa07be1',
    'animejs.LICENSE.txt': '3f3e835a9952cfc2a6ca836fb95b3257c981f24fa3ef51249055acb09931897b',
    'anime_timeline.mjs': 'c4c6bf0428909366a7f4fced4fbf61c8f2533c23286cc0f26aef785424208b18',
}


def descriptor():
    return dict(schema='video.animation-authoring-library.v1', library_id='animejs',
        version=VERSION, license='MIT', role='animation-authoring',
        engines=['html-svg', 'canvas', 'three'], clock='hyperframes-absolute-seek',
        assets=deepcopy(ASSETS))


def validate_libraries(value, assets):
    if (type(value) is not dict or set(value) != {'animejs'}
            or value['animejs'] != descriptor()
            or any(assets.get(name) != sha for name, sha in ASSETS.items())):
        raise ServiceError('animation_authoring_library_invalid', 422)
    return deepcopy(value)


def read_assets(directory=None):
    root = Path(directory) if directory is not None else Path(__file__).parent / 'motion_renderer/vendor'
    result = {}
    for name, sha in ASSETS.items():
        path = root / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 512 * 1024:
            raise ServiceError('animation_authoring_library_bytes_invalid', 422)
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha:
            raise ServiceError('animation_authoring_library_bytes_invalid', 422)
        result[name] = raw
    return result


def enable_in_capabilities(capabilities, *, directory=None):
    """Operator-side shape builder; caller must separately admit the release."""
    from .animation_runtime_capabilities import validate_runtime_capabilities
    value = validate_runtime_capabilities(capabilities)
    read_assets(directory)
    value['schema'] = 'video.animation-runtime-capabilities.v2'
    value['assets'].update(ASSETS)
    value['authoring_libraries'] = {'animejs': descriptor()}
    return validate_runtime_capabilities(value)


def foundation_skill_package():
    """Build an unpublished foundation draft for the ordinary review pipeline."""
    from agent_service.animation_skills_contract import build_skill_package
    skill = Path(__file__).parent / 'skills/animejs-frame-authoring/SKILL.md'
    return build_skill_package(skill_id='animejs-frame-authoring', version=1, kind='foundation',
        files={'SKILL.md': skill.read_text(encoding='utf-8')},
        runtime_requirements=dict(engines=['html-svg', 'canvas', 'three'],
            authoring_libraries={'animejs': descriptor()}))


def method_authoring_context(capabilities):
    """Trusted task guidance delivered to author/reviewer, frozen on replay."""
    from .animation_runtime_capabilities import validate_runtime_capabilities
    value = validate_runtime_capabilities(capabilities)
    if value.get('authoring_libraries') != {'animejs': descriptor()}:
        raise ServiceError('animation_authoring_library_invalid', 422)
    return dict(authoring_libraries=deepcopy(value['authoring_libraries']),
        authoring_library_guidance=(
            'Anime.js 4.5.0 is an optional authoring library, not a drawing engine. '
            'Keep source.engine html-svg, canvas or three as admitted. Import createFrameTimeline, '
            'stagger, svg or eases from ./assets/anime_timeline.mjs. Create the complete paused '
            'timeline once after assets/DOM are ready, with fps=constraints.fps, startFrame=0, '
            'durationFrames=constraints.duration_seconds*constraints.fps; add(targets, values, position) '
            'takes scoped element references/state objects and millisecond durations/positions. '
            'Use explicit property start/end values. In the required absolute '
            'window.__animationExperiment.seek(seconds), compute frame=Math.round(seconds*24), '
            'then timeline.renderAt({global_frame:frame,time:{numerator:frame,denominator:24}}), '
            'and redraw Canvas or render Three.js from tweened state. Keep total timeline duration '
            'within the frame window. Register the interface immediately and use its ready Promise '
            'for bounded initialization. Do not autoplay, use WAAPI, callbacks, wall-clock deltas '
            'or your own RAF. Never inline or fetch the library. No global selectors. Dispose/revert '
            'the timeline when replacing the scene. Design freely; this is no template. '
            'For a proposed reusable Skill distinguish this laboratory seconds interface from the '
            'formal film module: import /project/assets/anime_timeline.mjs, build in mount(root,context) '
            'with startFrame=context.start_frame and durationFrames=context.end_frame-context.start_frame, '
            'forward the real context from renderAt(context), and dispose on module disposal. '
            'Record the exact authoring_libraries descriptor in runtime_requirements beside engine '
            'and in skill_package.runtime_requirements beside engines. Use only when all three '
            'admitted library assets exist. Review only supplied evidence; static frames cannot '
            'establish complete motion, idle stability, quality or publication acceptance.'))
