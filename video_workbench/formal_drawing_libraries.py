"""Operator-owned drawing dependencies for composable film modules.

New admissions verify the installed library exactly. Persisted compatibility
records verify their own original descriptor and assets without consulting
today's pins; neither task metadata nor an Agent result admits a dependency.
"""
from copy import deepcopy
import re

from agent_service.contracts import ServiceError, canonical_bytes


SCHEMA = 'video.animation-runtime-capabilities.v3'
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def validate_libraries(value, assets, *, frozen=False):
    valid = type(value) is dict and set(value) == {'ink-landscape'} and type(assets) is dict
    if not valid:
        raise ServiceError('film_drawing_library_not_admitted', 422)
    spec = value['ink-landscape']
    if frozen:
        valid = (type(spec) is dict and set(spec) == {'schema', 'library_id', 'version', 'source_revision',
                'license', 'role', 'engines', 'entrypoint', 'assets'}
            and spec.get('schema') == 'video.learning-drawing-library.v1'
            and spec.get('library_id') == 'ink-landscape' and spec.get('license') == 'MIT'
            and spec.get('role') == 'drawing' and spec.get('engines') == ['canvas']
            and spec.get('entrypoint') == './assets/ink-landscape.mjs'
            and type(spec.get('version')) is str and bool(re.fullmatch(r'\d+\.\d+\.\d+', spec['version']))
            and type(spec.get('source_revision')) is str
            and bool(re.fullmatch(r'[0-9a-f]{40}', spec['source_revision']))
            and type(spec.get('assets')) is dict
            and set(spec['assets']) == {'ink-landscape.mjs', 'ink-landscape.LICENSE.txt'}
            and all(type(sha) is str and _SHA.fullmatch(sha) and assets.get(name) == sha
                for name, sha in spec['assets'].items()))
        if not valid:
            raise ServiceError('film_drawing_library_not_admitted', 422)
    else:
        from .ink_landscape_library import validate_descriptor
        validate_descriptor(spec, assets)
    return deepcopy(value)


def enable_in_capabilities(capabilities):
    """Build a new release declaration; execution admission is still separate."""
    from .animation_runtime_capabilities import validate_runtime_capabilities
    from .ink_landscape_library import descriptor, read_assets
    value = validate_runtime_capabilities(capabilities)
    read_assets()
    value['schema'] = SCHEMA
    value.setdefault('authoring_libraries', {})
    value['drawing_libraries'] = {'ink-landscape': descriptor()}
    value['assets'].update(descriptor()['assets'])
    return validate_runtime_capabilities(value)


def compact_module_runtime_guidance(capabilities):
    """Server-owned essentials when a complete predecessor needs the space.

    Assets and the existing profile remain authoritative. Optional library
    tutorials can be omitted; original user direction and resource instructions
    cannot. The store only uses this without added resource instructions.
    """
    guidance = (
        'Intent/style: scene/film_context.film. Source plus exit states: target <=12000 characters. '
        'Cache static layers in mount; renderAt uses global_frame; dispose buffers. '
        'Max 500 native contexts/realm. Declare used admitted assets/licenses. '
        'read_inputs({}) has no mode/path or asset access; skill_read reads published files only. '
        'Use documented APIs; renderer verifies execution. '
    )
    from .animejs_library import descriptor
    if capabilities.get('authoring_libraries') == {'animejs': descriptor()}:
        guidance += (
            'Optional Anime: import createFrameTimeline from /project/assets/anime_timeline.mjs. '
            'In mount use fps=24,startFrame=context.start_frame,'
            'durationFrames=context.end_frame-context.start_frame. Forward renderAt(context); '
            'do not reset tween-owned state or autoplay; dispose the timeline. Declare all three '
            'assets: anime_timeline.mjs,anime.esm.min.js,animejs.LICENSE.txt. '
        )
    if capabilities.get('drawing_libraries'):
        guidance += (
            'Ink: import /project/assets/ink-landscape.mjs. '
            'createStrokeGroupDocument returns recipe/result; nonempty groups only. '
            'renderCanvas(ctx,doc.result,{clear:false,paperTexture:0}) '
            'resets transforms to canvas/document ratios: use document-space points or local '
            'caches plus drawImage. #RRGGBB document colors. '
        )
    return guidance


def attach_drawing_libraries(core, capabilities):
    """Supply API custody and module lifecycle, never an artistic composition."""
    from agent_service.profiles.video_animation_film_module_contract import validate_core_task
    core = validate_core_task(core)
    libraries = capabilities.get('drawing_libraries')
    if not libraries:
        return core
    validate_libraries(libraries, capabilities['assets'])
    if any(core['assets'].get('assets/' + name) != sha
            for spec in libraries.values() for name, sha in spec['assets'].items()):
        raise ServiceError('film_drawing_library_assets_missing', 409)
    guidance = (
        'Optional fixed Canvas 2D drawing library: ink-landscape. Its exact descriptor is below. '
        'Use the declared Canvas engine and import * as ink from "/project/assets/ink-landscape.mjs"; '
        'as an ES module. Declare both assets/ink-landscape.mjs and '
        'assets/ink-landscape.LICENSE.txt in each module that uses it. Do not fetch or inline replacements. '
        'This is a composable film module, not a learning page: export mount(root,context), '
        'renderAt(context), dispose(); do not use window.__animationExperiment. Initialize bounded '
        'editable data in mount and draw deterministic state from the absolute context frame/time '
        'in renderAt. Preserve explicit seeds, paths and recipes. Dispose owned buffers. '
        'Keep source and outgoing states compact and readable (target <=12000 characters combined), '
        'so the next author receives the complete original source and states within its input budget. '
        'read_inputs({}) has no mode/path parameters and cannot read renderer assets. '
        '/project/assets/ink-landscape.mjs is a renderer import URL, not an input-reader path. '
        'skill_read can read only exact published package files. Use their documented exports; '
        'actual asset export/API execution is verified later by the renderer, not by this author role. '
        'createStrokeGroupDocument(groups,options) returns a document with recipe/result. '
        'Groups accept id,role?,parentId?,strokes; role is structure or mark. Strokes accept '
        'id,points,options?; points use x,y,pressure?. Per-stroke width/ink/opacity/dryness and '
        'other supported brush fields belong inside options, not at the group/stroke top level. '
        'Each group requires at least one stroke; omit a document/group for a wash-only layer '
        'instead of submitting an empty strokes array. '
        'Document options are width,height,seed?,background?,ink?,strokeWidth?,dryness?,opacity?. '
        'Use six-digit #RRGGBB document colors; paperTexture is a numeric 0..1. '
        'renderCanvas(realContext,document.result,{clear:false,paperTexture:0}) draws marks '
        'without filling document paper; clear the transparent target yourself before a fresh '
        'deterministic draw. It resets the Canvas transform to the canvas/document dimension '
        'ratios even with clear:false; caller translate/rotate/scale do not place its marks. '
        'Author points in document coordinates, or render an object in a local cache and '
        'place that cache with native drawImage transforms. Default clear:true paints document paper. Document creation alone '
        'does not draw. Each stroke-group document supports at most 512 groups and 512 total '
        'strokes, each with 2..4096 points. Split documents/buffers when needed without replacing '
        'the artwork with a technical demo. renderInkProcess accepts only time?/cellSize? and '
        'paints paper; it does not accept clear:false or paperTexture. Its raster budget is '
        'ceil(width/cellSize)*ceil(height/cellSize)<=262144 (default cellSize=2). '
        'Choose the composition and tools yourself. Library availability, import and function '
        'execution are separate from visible contribution and artistic quality.\n'
        'APP-FROZEN-DRAWING-LIBRARIES\n' + canonical_bytes(libraries).decode('utf-8')
        + '\nEND-APP-FROZEN-DRAWING-LIBRARIES')
    instruction = core['scene']['instruction']
    core['scene']['instruction'] = instruction + ('\n\n' if instruction else '') + guidance
    if len(core['scene']['instruction']) > 16000:
        raise ServiceError('film_authoring_instruction_too_large', 413)
    return validate_core_task(core)
