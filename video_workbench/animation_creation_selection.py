"""Explicit experiment tool choices, bounded by the host's admitted runtime.

Selections are authoring constraints, never evidence that a tool was used or
that a rendered effect is good. Ordinary requests without a selection retain
their existing input and capability snapshots.
"""
from copy import deepcopy
import re

from agent_service.contracts import ServiceError
from .animation_authoring_tools import observed_authoring_tools
from .animation_motion_evidence import (
    GSAP_SHA256, _has_native_motion, _has_manim_clock_motion, _source_program, motion_orchestration,
)


CREATION = ('html-svg', 'canvas', 'three', 'manim')
MOTION = ('native-js', 'gsap', 'animejs')


def require(value, suffix='invalid', status=422):
    if not value:
        raise ServiceError('animation_creation_selection_' + suffix, status)


def normalize_selection(value):
    require(type(value) is dict and set(value) <= {'creation', 'motion'})
    result = {}
    for key, choices in (('creation', CREATION), ('motion', MOTION)):
        if key not in value:
            continue
        rows = value[key]
        require(type(rows) is list and all(type(row) is str and row in choices for row in rows)
                and len(rows) == len(set(rows)))
        result[key] = [choice for choice in choices if choice in rows]
    return result


def motion_tools(capabilities):
    """Expose availability from trusted fixed assets, not prior sample labels."""
    result = ['native-js']
    if type(capabilities) is not dict:
        return result
    if capabilities.get('assets', {}).get('gsap.min.js') == GSAP_SHA256:
        result.append('gsap')
    if capabilities.get('authoring_libraries', {}).get('animejs'):
        result.append('animejs')
    return result


def manim_request(selection, resource, duration_seconds):
    selection = normalize_selection(selection)
    creation = selection.get('creation', [])
    if creation and 'manim' not in creation:
        require(resource is None, 'resource_conflict')
    if 'manim' not in creation or resource is not None:
        return resource
    # Use the current bounded Manim producer, not another rendering backend.
    return dict(kind='manim', width=640, height=360,
                frame_count=min(48, int(duration_seconds * 24)), global_start_frame=0, seed=17)


def freeze_constraints(selection, constraints, assets):
    """Narrow admitted choices and add exact, structured Agent guidance."""
    selection = normalize_selection(selection)
    result = deepcopy(constraints)
    allowed = result.get('allowed_engines', ['html-svg'])
    creation = selection.get('creation', [])
    browsers = [value for value in creation if value != 'manim']
    require(all(value in allowed for value in browsers), 'engine_not_admitted', 409)
    if browsers:
        result['allowed_engines'] = browsers
    elif creation == ['manim']:
        require('html-svg' in allowed, 'engine_not_admitted', 409)
        result['allowed_engines'] = ['html-svg']
    available_motion = motion_tools(dict(assets=assets,
        authoring_libraries=result.get('authoring_libraries', {})))
    require(all(value in available_motion for value in selection.get('motion', [])),
            'motion_not_admitted', 409)
    result['capability_selection'] = deepcopy(selection)
    result['capability_selection_guidance'] = (
        'These are explicit experiment constraints, not observed usage. Empty or omitted groups '
        'let you choose admitted tools. For nonempty creation, choose or combine the requested '
        'drawing capabilities and keep source.engine in constraints.allowed_engines. Manim is '
        'a peer drawing capability consumed as a real preproduced resource. If Manim is the only '
        'drawing choice, the primary geometry and its transformation must come from that resource; '
        'HTML/SVG may carry supporting labels and composition, never fake the Manim geometry. '
        'Selecting Manim prepares a resource; when combined with other drawing choices, consuming it '
        'is optional. For nonempty motion, substantively use at least one requested browser carrier '
        'tool and do not import '
        'or use unselected motion libraries. Multiple choices allow selection/combination rather '
        'than requiring decorative use of every tool. native-js means deterministic authored '
        'state updates from the absolute clock; GSAP must drive a paused timeline from seek; '
        'Anime.js must use the supplied local frame bridge with real timeline entries and '
        'renderAt calls. All motion follows the same HyperFrames absolute clock. Merely loading '
        'a library is insufficient. Manim Python geometry uses its own native animations, never '
        'GSAP/Anime.js. A real Manim layer advanced by seekFrame from the absolute clock is native-js '
        'carrier motion; do not add unrelated decoration just to satisfy a tool selection. '
        'Keep requested intent distinct from source/render evidence '
        'and from visual quality; the renderer and source checks verify use independently.')
    return result


def validate_source_selection(selection, source):
    """Reject a substituted library or a declared tool without real API use."""
    selection = normalize_selection(selection)
    creation = selection.get('creation', [])
    allowed = [value for value in creation if value != 'manim']
    if creation == ['manim']:
        allowed = ['html-svg']
    if allowed:
        require(source.get('engine') in allowed, 'source_engine_mismatch', 409)
    requested = selection.get('motion', [])
    if not requested:
        return
    code, references = _source_program(source)
    gsap = bool(re.search(r'\bgsap\s*\.\s*(?:timeline|to|from|fromTo)\s*\(', code))
    anime = bool(re.search(r'\bcreateFrameTimeline\s*\(', code)
                 and re.search(r'\.\s*add\s*\(', code)
                 and re.search(r'\.\s*renderAt\s*\(', code))
    gsap_reference = any('gsap.min.js' in value for value in references)
    anime_reference = any('anime_timeline.mjs' in value or 'anime.esm.min.js' in value for value in references)
    require('gsap' in requested or not (gsap or gsap_reference), 'unselected_motion_library', 409)
    require('animejs' in requested or not (anime or anime_reference), 'unselected_motion_library', 409)
    used = ({'gsap'} if gsap and gsap_reference else set()) | ({'animejs'} if anime and anime_reference else set())
    if not gsap and not anime and (_has_native_motion(code) or _has_manim_clock_motion(code, references)):
        used.add('native-js')
    require(bool(used & set(requested)), 'motion_not_used', 409)


def validate_rendered_selection(selection, source, outcome, render_operation_id):
    """Require existing source-bound render observations, without claiming art quality."""
    selection = normalize_selection(selection)
    validate_source_selection(selection, source)
    requested = selection.get('motion', [])
    if requested:
        candidate = dict(source=source, media=outcome['media'], operation_id=render_operation_id)
        demonstration = dict(source=source, runtime=outcome.get('runtime', {}))
        observed = {row['id'] for row in motion_orchestration(candidate, demonstration)}
        require(bool(observed & set(requested)), 'motion_not_observed', 409)
    if selection.get('creation') == ['manim']:
        require(any(row['id'] == 'manim' for row in observed_authoring_tools(outcome.get('runtime', {}))),
                'manim_not_observed', 409)
