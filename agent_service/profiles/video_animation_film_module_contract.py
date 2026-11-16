"""Composable film author artifacts; no legacy seek wrapping or runtime claims.

This worker-only contract has no application imports. The application adapter
cross-checks modules against the independently frozen full-film compiler.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import math
import re

from ..animation_skills_contract import validate_skill_catalog
from ..contracts import ServiceError, canonical_bytes, digest, safe_relative_path
from .video_animation_production_contract import (
    require, checksum, text, number, validate_clock, validate_film_context,
    validate_runtime_identity,
)
from .video_animation_skill_production_contract import validate_skill_decisions
from .video_motion_contract import validate_beats

INPUT_SCHEMA = 'video.animation-film-module-input.v1'
RESULT_SCHEMA = 'video.animation-film-module-result.v1'
CONTEXT_SCHEMA = 'video.hyperframes-film-author-context.v1'
MODULE_SCHEMA = 'video.hyperframes-composable-module.v1'
BOUNDARY_SCHEMA = 'video.hyperframes-film-boundary.v1'
INPUT_MAX_BYTES = 4 * 1024 * 1024
OUTPUT_MAX_BYTES = 6 * 1024 * 1024
MAX_TOOL_EVIDENCE_BYTES = 2 * 1024 * 1024
MAX_SOURCE_FILE_BYTES = 512 * 1024
MAX_MODULE_BYTES = 2 * 1024 * 1024
DECISION_SCOPE = 'agent_declared_composable_states_and_skill_anchors_not_runtime_or_quality'
CORE_FIELDS = frozenset({'scene', 'film_context', 'target', 'constraints', 'assets', 'base_module'})
RAW_RESULT_FIELDS = frozenset({'schema', 'input_sha256', 'module', 'strategy',
                              'incoming_boundary', 'skill_decisions'})
LIFECYCLE = dict(protocol='hyperframes-composable-module-1',
    exports=dict(mount='mount', render_at='renderAt', dispose='dispose'),
    scope='mount-root', time='absolute-global-frame', completion='await-returned-promises',
    seek='reconstructive')
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}\Z')
_NS = re.compile(r'[a-z][a-z0-9_]{0,63}\Z')
_MODULE_FIELDS = frozenset({'schema', 'module_id', 'namespace', 'role', 'scene_id',
    'film_sha256', 'engine', 'entry', 'files', 'files_sha256', 'lifecycle', 'assets', 'states'})
_SCENE_FIELDS = frozenset({'scene_id', 'title', 'visual_description', 'narration', 'beats',
                         'alignment', 'audio_sha256', 'style', 'instruction'})


def _identity(value):
    require(type(value) is str and _ID.fullmatch(value), 'film_module_identity_invalid')


def _path(value, prefix):
    try:
        safe_relative_path(value)
    except (ValueError, TypeError):
        raise ServiceError('film_module_path_invalid', 422) from None
    require(len(value) <= 200 and value.startswith(prefix)
        and re.fullmatch(r'[A-Za-z0-9_./-]+', value), 'film_module_path_invalid')


def _bounded_json(value, maximum, code='film_module_metadata_invalid'):
    try:
        raw = canonical_bytes(value)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ServiceError(code, 422) from None
    require(len(raw) <= maximum, code, 413)
    return raw


def validate_states(value):
    require(type(value) is dict and len(value) <= 32, 'film_module_states_invalid')
    nodes = 0
    def walk(item, depth=0):
        nonlocal nodes
        nodes += 1
        require(depth <= 12 and nodes <= 4096, 'film_module_states_invalid')
        if type(item) is dict:
            require(len(item) <= 128 and all(type(key) is str and len(key) <= 200 for key in item),
                    'film_module_states_invalid')
            for child in item.values():
                walk(child, depth + 1)
        elif type(item) is list:
            require(len(item) <= 128, 'film_module_states_invalid')
            for child in item:
                walk(child, depth + 1)
        else:
            require(item is None or type(item) in (bool, str, int, float), 'film_module_states_invalid')
            if type(item) is str:
                text(item, 4000, empty=True)
            if type(item) is float:
                require(math.isfinite(item), 'film_module_states_invalid')
    for object_id, state in value.items():
        _identity(object_id)
        require(type(state) is dict, 'film_module_states_invalid')
        walk(state)
    _bounded_json(value, 64 * 1024, 'film_module_states_invalid')
    return deepcopy(value)


def module_metadata(module):
    """Identity matches the full-film compiler, including sorted asset names."""
    require(type(module) is dict and set(module) == _MODULE_FIELDS,
            'film_module_composable_module_required')
    require(type(module['assets']) is list and all(type(name) is str for name in module['assets']),
            'film_module_assets_invalid')
    value = deepcopy(module)
    value.pop('files')
    value['assets'] = sorted(value['assets'])
    return value


def module_identity(module):
    return digest(module_metadata(module))


def _validate_metadata(module):
    require(type(module) is dict and set(module) == _MODULE_FIELDS - {'files'}
        and module.get('schema') == MODULE_SCHEMA, 'film_module_composable_module_required')
    _identity(module['module_id'])
    checksum(module['film_sha256'])
    require(type(module['namespace']) is str and _NS.fullmatch(module['namespace'])
        and type(module['role']) is str and module['role'] in {'scene', 'transition'}
        and type(module['engine']) is str and module['engine'] in {'html-svg', 'canvas', 'three'},
        'film_module_metadata_invalid')
    if module['role'] == 'scene':
        text(module['scene_id'], 100)
    else:
        require(module['scene_id'] is None, 'film_module_metadata_invalid')
    require(module['lifecycle'] == LIFECYCLE, 'film_module_lifecycle_invalid')
    hashes = module['files_sha256']
    require(type(hashes) is dict and 1 <= len(hashes) <= 32, 'film_module_files_invalid')
    paths = set()
    for name, sha in hashes.items():
        _path(name, 'modules/' + module['namespace'] + '/')
        require(name.rsplit('.', 1)[-1] in {'mjs', 'js', 'css', 'html', 'svg'}
                and name.casefold() not in paths, 'film_module_files_invalid')
        paths.add(name.casefold())
        checksum(sha)
    require(not any('/'.join(name.split('/')[:i]).casefold() in paths
        for name in hashes for i in range(1, len(name.split('/')))), 'film_module_path_collision')
    require(type(module['entry']) is str and module['entry'] in hashes
        and module['entry'].endswith('.mjs'), 'film_module_entry_invalid')
    assets = module['assets']
    require(type(assets) is list and len(assets) <= 64
        and all(type(name) is str for name in assets) and len(set(assets)) == len(assets),
        'film_module_assets_invalid')
    for name in assets:
        _path(name, 'assets/')
    if module['engine'] == 'three':
        require({'assets/three.module.min.js', 'assets/three.LICENSE.txt'} <= set(assets),
                'film_module_three_assets_required')
    states = module['states']
    require(type(states) is dict and set(states) == {'entry', 'exit'}, 'film_module_states_invalid')
    validate_states(states['entry'])
    validate_states(states['exit'])
    _bounded_json(module, 192 * 1024)
    return deepcopy(module)


def validate_module(module, *, context=None, role='scene'):
    require(type(module) is dict and set(module) == _MODULE_FIELDS,
            'film_module_composable_module_required')
    metadata = _validate_metadata(module_metadata(module))
    files, total = module['files'], 0
    require(type(files) is dict and set(files) == set(metadata['files_sha256']), 'film_module_files_invalid')
    for name, value in files.items():
        text(value, MAX_SOURCE_FILE_BYTES)
        raw = value.encode('utf-8')
        total += len(raw)
        require(len(raw) <= MAX_SOURCE_FILE_BYTES
            and hashlib.sha256(raw).hexdigest() == metadata['files_sha256'][name],
            'film_module_source_changed', 409)
    require(total <= MAX_MODULE_BYTES, 'film_module_source_budget', 413)
    if context is not None:
        target = context['target'] if role == 'scene' else context['target']['transition_target']
        require(target is not None and module['role'] == role
            and all(module[key] == target[key] for key in ('module_id', 'namespace'))
            and module['film_sha256'] == context['film_context']['film_sha256']
            and module['scene_id'] == (context['scene']['scene_id'] if role == 'scene' else None),
            'film_module_target_mismatch', 409)
        require(module['engine'] in context['constraints']['allowed_engines']
            and set(module['assets']) <= set(context['assets']), 'film_module_resources_not_admitted', 409)
    return deepcopy(module)


def _target(value):
    require(type(value) is dict and set(value) == {'module_id', 'namespace'}, 'film_module_target_invalid')
    _identity(value['module_id'])
    require(type(value['namespace']) is str and _NS.fullmatch(value['namespace']), 'film_module_target_invalid')


def validate_core_task(document):
    require(type(document) is dict and set(document) == CORE_FIELDS, 'film_module_core_invalid')
    scene, bounds, context = document['scene'], document['constraints'], document['film_context']
    require(type(scene) is dict and set(scene) == _SCENE_FIELDS, 'film_module_scene_invalid')
    for key, maximum in [('scene_id', 100), ('title', 300), ('visual_description', 16000),
        ('narration', 10000), ('style', 10000), ('instruction', 16000)]:
        text(scene[key], maximum, empty=key in {'style', 'instruction'})
    beats = validate_beats(scene['beats'])
    alignment = validate_clock(scene['alignment'], beats)
    checksum(scene['audio_sha256'])
    require(beats['source_text'] == scene['narration']
        and alignment['audio_sha256'] == scene['audio_sha256'], 'film_module_audio_binding_mismatch')
    require(type(bounds) is dict and set(bounds) == {'width', 'height', 'fps', 'duration_seconds',
        'allowed_engines', 'runtime_identity'}, 'film_module_constraints_invalid')
    require(all(type(bounds[key]) is int and bounds[key] == value
        for key, value in [('width', 1280), ('height', 720), ('fps', 24)]), 'film_module_constraints_invalid')
    engines = bounds['allowed_engines']
    require(type(engines) is list and bool(engines) and all(type(engine) is str
        and engine in {'html-svg', 'canvas', 'three'} for engine in engines)
        and len(set(engines)) == len(engines), 'film_module_engines_invalid')
    validate_runtime_identity(bounds['runtime_identity'])
    number(bounds['duration_seconds'], .1, 60)
    require(bounds['duration_seconds'] == alignment['audio_seconds'], 'film_module_audio_binding_mismatch')
    require(type(context) is dict and set(context) == {'schema', 'film', 'film_sha256',
        'current_scene_index', 'predecessor'} and context['schema'] == CONTEXT_SCHEMA,
        'film_module_context_invalid')
    # Reuse the complete recording-clock checks, never the legacy source/handoff.
    validate_film_context(dict(schema='video.film-context.v1', film=context['film'],
        film_sha256=context['film_sha256'], current_scene_index=0, predecessor=None))
    film, index = context['film'], context['current_scene_index']
    require(film['total_seconds'] <= 600 and type(index) is int and 0 <= index < len(film['scenes']),
        'film_module_context_invalid')
    row = film['scenes'][index]
    require(row['scene_id'] == scene['scene_id'] and row['narration'] == scene['narration']
        and row['audio_sha256'] == scene['audio_sha256']
        and row['audio_seconds'] == bounds['duration_seconds'], 'film_module_film_scene_mismatch', 409)
    target = document['target']
    require(type(target) is dict and set(target) == {'module_id', 'namespace', 'transition_target'},
        'film_module_target_invalid')
    _target({key: target[key] for key in ('module_id', 'namespace')})
    transition = target['transition_target']
    if transition is not None:
        _target(transition)
        require(index > 0 and transition['module_id'] != target['module_id']
            and transition['namespace'] != target['namespace'], 'film_module_target_invalid')
    assets = document['assets']
    require(type(assets) is dict and len(assets) <= 128, 'film_module_assets_invalid')
    for name, sha in assets.items():
        _path(name, 'assets/'); checksum(sha)
    if 'three' in engines:
        require({'assets/three.module.min.js', 'assets/three.LICENSE.txt'} <= set(assets),
                'film_module_three_assets_required')
    predecessor = context['predecessor']
    if index == 0:
        require(predecessor is None and transition is None, 'film_module_predecessor_invalid')
    else:
        require(type(predecessor) is dict and set(predecessor) == {'module', 'module_sha256',
            'input_sha256', 'result_sha256'}, 'film_module_predecessor_invalid')
        for key in ('input_sha256', 'result_sha256', 'module_sha256'):
            checksum(predecessor[key])
        previous = _validate_metadata(predecessor['module'])
        require(previous['role'] == 'scene' and previous['scene_id'] == film['scenes'][index - 1]['scene_id']
            and previous['film_sha256'] == context['film_sha256']
            and digest(previous) == predecessor['module_sha256']
            and previous['module_id'] != target['module_id'] and previous['namespace'] != target['namespace'],
            'film_module_predecessor_invalid', 409)
        if transition is not None:
            require(previous['module_id'] != transition['module_id']
                and previous['namespace'] != transition['namespace'], 'film_module_target_invalid')
    if document['base_module'] is not None:
        validate_module(document['base_module'], context=document)
    _bounded_json(document, INPUT_MAX_BYTES, 'film_module_input_too_large')
    return deepcopy(document)


def validate_input(document):
    require(type(document) is dict and set(document) == CORE_FIELDS | {'schema', 'skill_catalog'}
        and document['schema'] == INPUT_SCHEMA, 'film_module_input_invalid')
    core = validate_core_task({key: document[key] for key in CORE_FIELDS})
    catalog = validate_skill_catalog(document['skill_catalog'])
    require(catalog['task_sha256'] == digest(core)
        and catalog['allowed_engines'] == core['constraints']['allowed_engines'],
        'film_module_catalog_binding_mismatch', 409)
    _bounded_json(document, INPUT_MAX_BYTES, 'film_module_input_too_large')
    return deepcopy(document)


def _normalize_module(module):
    require(type(module) is dict and set(module) == _MODULE_FIELDS - {'files_sha256'}
        and type(module['files']) is dict, 'film_module_agent_module_invalid')
    value = deepcopy(module)
    require(all(type(raw) is str for raw in value['files'].values()), 'film_module_files_invalid')
    try:
        value['files_sha256'] = {name: hashlib.sha256(raw.encode('utf-8')).hexdigest()
                                for name, raw in value['files'].items()}
    except UnicodeError:
        raise ServiceError('film_module_files_invalid', 422) from None
    return value


def _boundary_from_raw(raw, context, module):
    index = context['film_context']['current_scene_index']
    if index == 0:
        require(raw is None, 'film_module_first_boundary_invalid')
        return None
    require(type(raw) is dict and set(raw) == {'intent', 'objects', 'transition'},
            'film_module_boundary_invalid')
    text(raw['intent'], 8000)
    previous = context['film_context']['predecessor']['module']
    shared = set(previous['states']['exit']) & set(module['states']['entry'])
    require(type(raw['objects']) is list and len(raw['objects']) <= 32, 'film_module_boundary_invalid')
    seen, objects = set(), []
    for row in raw['objects']:
        require(type(row) is dict and set(row) == {'object_id', 'mode'}
            and type(row['object_id']) is str and row['object_id'] in shared and row['object_id'] not in seen
            and type(row['mode']) is str and row['mode'] in {'retain', 'transform'}, 'film_module_boundary_invalid')
        oid = row['object_id']; seen.add(oid)
        outgoing, incoming = digest(previous['states']['exit'][oid]), digest(module['states']['entry'][oid])
        require(row['mode'] != 'retain' or outgoing == incoming, 'film_module_retained_state_changed')
        require(row['mode'] != 'transform' or raw['transition'] is not None,
                'film_module_transform_requires_transition')
        objects.append(dict(object_id=oid, mode=row['mode'], outgoing_state_sha256=outgoing,
                            incoming_state_sha256=incoming))
    require(seen == shared, 'film_module_shared_states_incomplete')
    film = context['film_context']['film']; left, right = film['scenes'][index - 1:index + 1]
    at = round(right['start_seconds'] * 24)
    transition, transition_module = None, None
    if raw['transition'] is not None:
        item = raw['transition']
        require(type(item) is dict and set(item) == {'module', 'start_frame', 'end_frame'},
                'film_module_transition_invalid')
        transition_module = validate_module(item['module'], context=context, role='transition')
        start, end = item['start_frame'], item['end_frame']
        require(type(start) is int and type(end) is int
            and round(left['start_seconds'] * 24) <= start <= at < end <= round(right['end_seconds'] * 24)
            and 0 < end - start <= 48, 'film_module_transition_clock_invalid')
        require(transition_module['states']['entry'] == {oid: previous['states']['exit'][oid] for oid in shared}
            and transition_module['states']['exit'] == {oid: module['states']['entry'][oid] for oid in shared},
            'film_module_transition_states_invalid')
        transition = dict(module_id=transition_module['module_id'],
            module_sha256=module_identity(transition_module), start_frame=start, end_frame=end)
    boundary = dict(schema=BOUNDARY_SCHEMA, from_scene_id=left['scene_id'], to_scene_id=right['scene_id'],
        from_module_sha256=context['film_context']['predecessor']['module_sha256'],
        to_module_sha256=module_identity(module), at_frame=at, objects=objects, transition=transition)
    return dict(intent=raw['intent'], boundary=boundary, transition_module=transition_module)


def normalize_agent_result(document, input_document, *, tool_evidence_sha256):
    """Add host-calculated hashes and boundaries; never rewrite source/lifecycle."""
    context = validate_input(input_document)
    require(type(document) is dict and set(document) == RAW_RESULT_FIELDS,
            'film_module_agent_output_invalid')
    value = deepcopy(document)
    value['module'] = _normalize_module(value['module'])
    validate_module(value['module'], context=context)
    raw_boundary = value['incoming_boundary']
    if type(raw_boundary) is dict and type(raw_boundary.get('transition')) is dict:
        item = raw_boundary['transition']
        require('module' in item, 'film_module_transition_invalid')
        item['module'] = _normalize_module(item['module'])
    value['incoming_boundary'] = _boundary_from_raw(raw_boundary, context, value['module'])
    value['skill_tool_evidence_sha256'] = tool_evidence_sha256
    return value


def validate_result(document, input_sha256, input_document, *, read_package_sha256s, tool_evidence_sha256):
    context = validate_input(input_document)
    checksum(input_sha256); checksum(tool_evidence_sha256)
    require(type(document) is dict and set(document) == RAW_RESULT_FIELDS | {'skill_tool_evidence_sha256'}
        and document['schema'] == RESULT_SCHEMA and document['input_sha256'] == input_sha256
        and digest(context) == input_sha256 and document['skill_tool_evidence_sha256'] == tool_evidence_sha256,
        'film_module_result_binding_mismatch', 409)
    module = validate_module(document['module'], context=context)
    strategy = document['strategy']
    require(type(strategy) is dict and set(strategy) == {'description', 'tradeoffs'}, 'film_module_strategy_invalid')
    for value in strategy.values():
        text(value, 8000)
    incoming = document['incoming_boundary']
    if context['film_context']['current_scene_index'] == 0:
        require(incoming is None, 'film_module_first_boundary_invalid')
    else:
        require(type(incoming) is dict and set(incoming) == {'intent', 'boundary', 'transition_module'},
                'film_module_boundary_invalid')
        boundary = incoming['boundary']
        require(type(boundary) is dict and set(boundary) == {'schema', 'from_scene_id', 'to_scene_id',
            'from_module_sha256', 'to_module_sha256', 'at_frame', 'objects', 'transition'}
            and type(boundary['objects']) is list, 'film_module_boundary_invalid')
        objects = boundary['objects']
        require(all(type(row) is dict and set(row) == {'object_id', 'mode',
            'outgoing_state_sha256', 'incoming_state_sha256'} for row in objects), 'film_module_boundary_invalid')
        raw = dict(intent=incoming['intent'], objects=[{key: row[key] for key in ('object_id', 'mode')}
            for row in objects], transition=None)
        if boundary['transition'] is not None:
            transition = boundary['transition']
            require(type(transition) is dict and set(transition) == {'module_id', 'module_sha256',
                'start_frame', 'end_frame'}, 'film_module_transition_invalid')
            raw['transition'] = dict(module=incoming['transition_module'], start_frame=transition['start_frame'],
                                     end_frame=transition['end_frame'])
        expected = _boundary_from_raw(raw, context, module)
        require(incoming == expected, 'film_module_boundary_binding_mismatch', 409)
    files = dict(module['files'])
    if incoming is not None and incoming['transition_module'] is not None:
        files.update(incoming['transition_module']['files'])
    validate_skill_decisions(document['skill_decisions'], context['skill_catalog'], {'files': files},
                             read_package_sha256s)
    _bounded_json(document, OUTPUT_MAX_BYTES, 'film_module_output_too_large')
    return deepcopy(document)
