"""Image-owned ink drawing code and unpublished documentation sources.

This module grants neither a new engine nor a knowledge publication. Only
learning renderers consume its exact local assets; frozen tasks keep the
descriptor actually supplied to them.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from agent_service.contracts import ServiceError


ROOT = Path(__file__).parent / 'vendor/ink-landscape'
MANIFEST_SHA256 = 'a38391a07298b0259457f1d43bf901fe93de737da6ba7eeed0d4f5268dffb5dc'
SOURCE_REVISION = 'bb7338a5c57d4151c3d8635a8549057bb0383ca0'
VERSION = '0.1.0'
ASSETS = {
    'ink-landscape.mjs': '03ff8324ebee4e33ab1991399fe43ae7751f189bfa0116c30db29d64fd5f7469',
    'ink-landscape.LICENSE.txt': 'b4c356e924703056db7f4392488d1911d3a532d83b752cfa8af9369d2c543108',
}


def descriptor():
    return dict(schema='video.learning-drawing-library.v1', library_id='ink-landscape',
        version=VERSION, source_revision=SOURCE_REVISION, license='MIT', role='drawing',
        engines=['canvas'], entrypoint='./assets/ink-landscape.mjs', assets=deepcopy(ASSETS))


def _read(root, relative, expected, maximum):
    path = root / relative
    if (root.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != root.parent)
            or path.is_symlink() or not path.is_file() or path.stat().st_size > maximum):
        raise ServiceError('ink_landscape_library_bytes_invalid', 422)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ServiceError('ink_landscape_library_bytes_invalid', 422)
    return raw


def manifest(directory=None):
    root = Path(directory) if directory is not None else ROOT
    raw = _read(root, 'manifest.json', MANIFEST_SHA256, 64 * 1024)
    value = json.loads(raw)
    if (value.get('schema') != 'video.ink-landscape-vendor.v1'
            or value.get('source_revision') != SOURCE_REVISION or value.get('version') != VERSION
            or value.get('license') != 'MIT' or value.get('assets') != ASSETS):
        raise ServiceError('ink_landscape_library_manifest_invalid', 422)
    return value


def read_assets(directory=None):
    root = Path(directory) if directory is not None else ROOT
    manifest(root)
    return {name: _read(root, name, sha, 1024 * 1024) for name, sha in ASSETS.items()}


def read_skill_files(directory=None):
    root = Path(directory) if directory is not None else ROOT
    frozen = manifest(root)
    return {name: _read(root, 'skill/' + name, sha, 128 * 1024).decode('utf-8')
            for name, sha in frozen['skill_files'].items()}


def validate_descriptor(value, assets):
    if (value != descriptor() or type(assets) is not dict
            or any(assets.get(name) != sha for name, sha in ASSETS.items())):
        raise ServiceError('ink_landscape_library_not_admitted', 422)
    return deepcopy(value)


def runtime_guidance():
    """Runtime identity and API shapes, independent of artistic knowledge."""
    return (
        'The host supplies ink-landscape 0.1.0 at the exact source_revision in '
        'constraints.drawing_libraries["ink-landscape"]. In scene.js use '
        'import * as ink from "./assets/ink-landscape.mjs" as an ES module. '
        'This is an optional Canvas 2D drawing library; source.engine remains canvas. '
        'The library is not a global variable, a motion orchestrator or a Shader backend. '
        'Use the existing window.__animationExperiment ready/seek interface. '
        'Provide a real CanvasRenderingContext2D to renderCanvas or renderInkProcess; '
        'pure document creation does not draw pixels. Keep the original recipe and '
        'explicit seed in authored source. Each seek must derive state from the '
        'supplied absolute seconds and clear/redraw deterministically. '
        'The module and MIT license are supplied locally; do not fetch packages or '
        'inline replacement implementations. Use only the declared entrypoint/version. '
        'The following API contract applies equally to reproduction and changed-content '
        'transfer, including when WikiSkill is disabled; it supplies no drawing recipe. '
        'InkPoint is {x,y,pressure?}, with finite coordinates and pressure in 0..1. '
        'createStrokeGroupDocument(groups,options) returns {recipe,result}; result is an InkScene. '
        'Each group accepts only {id,role?,parentId?,strokes}; role is structure or mark. '
        'Each stroke accepts only {id,points,options?}. Put per-stroke width,ink,opacity, '
        'dryness,smoothing,controls and other supported brush settings inside options. '
        'Do not add path,size,color,brush or pressure at the stroke/group top level, '
        'or ink at the group top level; point pressure belongs inside each point. '
        'Group IDs must be unique; stroke IDs must be unique within each group. '
        'Document options are width,height,seed?,background?,ink?,strokeWidth?,dryness?,opacity?. '
        'Document width/height are scene dimensions; strokeWidth is the default contact '
        'width, while per-stroke options.width overrides it. Minimal API shape example:\n'
        '```javascript\n'
        'const sampleDocument = ink.createStrokeGroupDocument([{id:"group",role:"mark",strokes:['
        '{id:"stroke",points:[{x:0,y:0,pressure:0.5},{x:1,y:1,pressure:1}],'
        'options:{width:1,opacity:0.8,dryness:0.4}}]}],'
        '{width:2,height:2,seed:1,background:"#f5f2e9",ink:"#242622"});\n'
        '```\n'
        'Document background/ink and per-stroke ink accept six-digit #RRGGBB colors, '
        'never transparent, rgba(), or color aliases. Native Canvas styles are separate. '
        'renderCanvas(ctx,document.result,{time:10000,paperTexture:0,clear:false}) '
        'draws onto an already cleared transparent offscreen target without filling '
        'document paper. paperTexture is a number in 0..1, not a boolean. Default '
        'clear:true clears the target and paints document paper. Compose separate '
        'results explicitly; do not unintentionally clear earlier layers. '
        'A stroke-group document supports at most 512 groups and 512 total strokes; '
        'each group needs at least one stroke, with 2..4096 points per stroke. '
        'createCunDocument also accepts at most 512 source strokes per document; '
        'its stroke options cannot override ink,seed,gestureModel. These are '
        'per-document limits, not a required reduction of the whole composition. '
        'renderInkProcess accepts only {time?,cellSize?}; it clears and paints paper '
        'and does not support clear:false or paperTexture. Ink-process programs '
        'support at most 32 layers, 512 total strokes and 4096 points per stroke; '
        'ceil(width/cellSize)*ceil(height/cellSize) must not exceed 262144, with '
        'cellSize defaulting to 2. Preserve output requirements when choosing '
        'bounded offscreen regions or an explicitly recorded sampling resolution. '
        'API availability and loading do not establish actual API use or visual quality.'
    )
