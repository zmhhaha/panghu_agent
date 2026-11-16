"""Bounded, task-scoped cooperative animation authorship contracts.

One admitted Agent role serves three phases. The business orchestrator selects
direct dependencies; no Agent launches peers, renders, or publishes. Source is
retained as inert data and remains subject to the isolated renderer's checks.
"""
from __future__ import annotations

from copy import deepcopy
import math

from . import video_animation_method as method_contract
from .video_animation_experiment import _digest, _identifier, _text, _tree, _validate_source_output
from ..animation_skills_contract import build_skill_package, validate_skill_package
from ..contracts import ServiceError, canonical_bytes, digest


PROFILE = 'video.animation-collaboration.v1'
INPUT_SCHEMA = 'video.animation-collaboration-input.v1'
RESULT_SCHEMA = 'video.animation-collaboration-result.v1'
INPUT_NAME = 'collaboration-input.json'
RESULT_NAME = 'collaboration-result.json'
INPUT_MAX_BYTES = 192 * 1024
OUTPUT_MAX_BYTES = 192 * 1024
PHASE_INPUT_MAX_BYTES = dict(plan=64 * 1024, component=96 * 1024, integrate=INPUT_MAX_BYTES)
PLAN_MAX_BYTES = 16 * 1024
COMPONENT_SOURCE_MAX_BYTES = 24 * 1024
METHODS_MAX_BYTES = 32 * 1024
MAX_COMPONENTS = 4
ENGINES = frozenset({'html-svg', 'canvas', 'three'})
_INPUT_FIELDS = frozenset({'schema', 'phase', 'goal', 'criteria', 'constraints', 'assets',
    'reference', 'feedback', 'brief', 'component_id', 'dependencies', 'methods', 'base_source'})
_RESULT_FIELDS = frozenset({'schema', 'input_sha256', 'phase', 'criteria', 'methods', 'source',
    'sound_events', 'review', 'knowledge_proposals', 'plan', 'component'})
_METHOD_CONTENT_FIELDS = frozenset({'schema', 'title', 'problem', 'guidance', 'applicability',
    'limitations', 'criteria', 'runtime_requirements', 'skill_package', 'research_record_ids',
    'method_id', 'version_id', 'parent_version_id', 'version'})
_METHOD_REQUIRED = frozenset({'schema', 'title', 'problem', 'guidance', 'applicability',
    'limitations', 'criteria', 'runtime_requirements', 'skill_package', 'research_record_ids'})


def require(condition, code='animation_collaboration_invalid', status=422):
    if not condition:
        raise ServiceError(code, status)


def _bytes(value):
    try:
        return canonical_bytes(value)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ServiceError('animation_collaboration_invalid', 422) from None


def _fields(value, keys):
    require(type(value) is dict and set(value) == set(keys))


def _bounded(value, maximum, code):
    require(len(_bytes(value)) <= maximum, code, 413)


def _constraints(value):
    require(type(value) is dict and {'width', 'height', 'fps', 'duration_seconds', 'allowed_engines'} <= set(value))
    _tree(value)
    _bounded(value, 16 * 1024, 'animation_collaboration_constraints_too_large')
    for key, maximum in (('width', 3840), ('height', 2160), ('fps', 60)):
        require(type(value[key]) is int and 1 <= value[key] <= maximum)
    seconds = value['duration_seconds']
    require(type(seconds) in (int, float) and math.isfinite(seconds) and 0 < seconds <= 600)
    allowed = value['allowed_engines']
    require(type(allowed) is list and 1 <= len(allowed) <= len(ENGINES)
        and all(type(row) is str and row in ENGINES for row in allowed)
        and len(allowed) == len(set(allowed)))
    if 'collaboration' in value:
        collaboration = value['collaboration']
        require(type(collaboration) is dict and 'max_components' in collaboration
            and set(collaboration) <= {'max_components', 'max_parallel', 'max_rounds'})
        maximum = collaboration['max_components']
        require(type(maximum) is int and 2 <= maximum <= MAX_COMPONENTS)
        require('max_parallel' not in collaboration or type(collaboration['max_parallel']) is int
            and collaboration['max_parallel'] == 2)
        require('max_rounds' not in collaboration or type(collaboration['max_rounds']) is int
            and collaboration['max_rounds'] == 1)


def validate_plan(value, constraints):
    """A shared creative agreement, not a prescribed scene template or render gate."""
    _fields(value, {'engine', 'composition', 'material', 'clock', 'interfaces', 'components', 'keyframe_times'})
    require(type(value['engine']) is str and value['engine'] in constraints['allowed_engines'],
            'animation_collaboration_engine_not_admitted')
    for key in ('composition', 'material', 'clock', 'interfaces'):
        _text(value[key], 6000)
    rows = value['components']
    maximum = constraints.get('collaboration', {}).get('max_components', MAX_COMPONENTS)
    require(type(rows) is list and 2 <= len(rows) <= maximum,
            'animation_collaboration_component_budget_exceeded')
    identities, graph = set(), {}
    for row in rows:
        _fields(row, {'id', 'responsibility', 'depends_on', 'interface'})
        _identifier(row['id'])
        require(row['id'] not in identities, 'animation_collaboration_duplicate_component')
        identities.add(row['id'])
        _text(row['responsibility'], 4000)
        _text(row['interface'], 4000)
        dependencies = row['depends_on']
        require(type(dependencies) is list and len(dependencies) <= MAX_COMPONENTS - 1
            and all(type(item) is str for item in dependencies)
            and len(dependencies) == len(set(dependencies)))
        graph[row['id']] = dependencies
    for identity, dependencies in graph.items():
        require(set(dependencies) <= identities - {identity}, 'animation_collaboration_dependency_invalid')
    visited, active = set(), set()
    def visit(identity):
        require(identity not in active, 'animation_collaboration_dependency_cycle')
        if identity in visited:
            return
        active.add(identity)
        for dependency in graph[identity]:
            visit(dependency)
        active.remove(identity)
        visited.add(identity)
    for identity in graph:
        visit(identity)
    times = value['keyframe_times']
    require(type(times) is list and 3 <= len(times) <= 16
        and all(type(item) in (int, float) and math.isfinite(item) for item in times)
        and times[0] == 0 and times[-1] == constraints['duration_seconds']
        and all(0 <= first < second <= constraints['duration_seconds'] for first, second in zip(times, times[1:])),
        'animation_collaboration_keyframes_invalid')
    _bounded(value, PLAN_MAX_BYTES, 'animation_collaboration_plan_too_large')
    return deepcopy(value)


def validate_component(value, brief):
    _fields(value, {'component_id', 'engine', 'interface', 'source'})
    _identifier(value['component_id'])
    require(value['component_id'] in {row['id'] for row in brief['components']}
        and value['engine'] == brief['engine'], 'animation_collaboration_component_binding_mismatch')
    _text(value['interface'], 4000)
    specification = next(row for row in brief['components'] if row['id'] == value['component_id'])
    require(value['interface'] == specification['interface'],
            'animation_collaboration_component_interface_changed')
    _text(value['source'], COMPONENT_SOURCE_MAX_BYTES)
    require(len(value['source'].encode('utf-8')) <= COMPONENT_SOURCE_MAX_BYTES,
            'animation_collaboration_component_source_too_large', 413)
    return deepcopy(value)


def _selected_methods(value):
    require(type(value) is list and len(value) <= 4, 'animation_collaboration_methods_not_scoped')
    for row in value:
        require(type(row) is dict and set(row) <= _METHOD_CONTENT_FIELDS and 'guidance' in row,
                'animation_collaboration_methods_not_scoped')
        _text(row['guidance'], 16000)
        for key in ('method_id', 'version_id', 'parent_version_id'):
            if key in row:
                _text(row[key], 200)
        if 'version' in row:
            require(type(row['version']) is int and row['version'] >= 1)
        if 'schema' in row:
            require(row['schema'] == 'video.animation-method.v2')
        if 'skill_package' in row:
            validate_skill_package(row['skill_package'])
        _tree({key: item for key, item in row.items() if key != 'skill_package'})
    _bounded(value, METHODS_MAX_BYTES, 'animation_collaboration_methods_too_large')


def build_input(*, phase, goal, criteria, constraints, assets=None, reference=None, feedback='',
                brief=None, component_id=None, dependencies=None, methods=None, base_source=None):
    """Build the explicit content projection; never copy a Wiki or operation log.

    Method database wrappers are omitted deliberately. Their supplied content
    and retained identity references are bound by this operation's input hash.
    Oversized source/content is rejected, never silently summarized or clipped.
    """
    if methods is None:
        selected = []
    else:
        require(type(methods) is list and all(type(row) is dict for row in methods))
        selected = [{key: deepcopy(item) for key, item in row.items() if key in _METHOD_CONTENT_FIELDS
                     and not (key in {'method_id', 'version_id', 'parent_version_id'} and item is None)}
                    for row in methods]
    value = dict(schema=INPUT_SCHEMA, phase=phase, goal=goal, criteria=deepcopy(criteria),
        constraints=deepcopy(constraints), assets={} if assets is None else deepcopy(assets),
        reference=deepcopy(reference), feedback=feedback, brief=deepcopy(brief), component_id=component_id,
        dependencies=[] if dependencies is None else deepcopy(dependencies), methods=selected,
        base_source=deepcopy(base_source))
    return validate_input(value)


def validate_input(document):
    _fields(document, _INPUT_FIELDS)
    require(document['schema'] == INPUT_SCHEMA and type(document['phase']) is str
        and document['phase'] in PHASE_INPUT_MAX_BYTES)
    phase = document['phase']
    _bounded(document, PHASE_INPUT_MAX_BYTES[phase], 'animation_collaboration_input_too_large')
    _text(document['goal'], 12000)
    _text(document['feedback'], 8000, empty=True)
    if document['criteria']:
        method_contract.criteria(document['criteria'])
    else:
        require(type(document['criteria']) is list and phase == 'plan')
    _constraints(document['constraints'])
    assets = document['assets']
    require(type(assets) is dict and len(assets) <= 32)
    for name, checksum in assets.items():
        _text(name, 200)
        require('/' not in name and '\\' not in name and '..' not in name)
        _digest(checksum)
    reference = document['reference']
    require(reference is None or type(reference) is dict)
    if reference is not None:
        _tree(reference)
        _bounded(reference, 8 * 1024, 'animation_collaboration_reference_too_large')
    _selected_methods(document['methods'])
    dependencies = document['dependencies']
    require(type(dependencies) is list and len(dependencies) <= MAX_COMPONENTS)
    if phase == 'plan':
        require(document['brief'] is None and document['component_id'] is None
            and dependencies == [] and document['base_source'] is None,
            'animation_collaboration_phase_mismatch')
    else:
        brief = validate_plan(document['brief'], document['constraints'])
        components = {row['id']: row for row in brief['components']}
        identities = []
        for dependency in dependencies:
            validate_component(dependency, brief)
            identities.append(dependency['component_id'])
        require(len(identities) == len(set(identities)), 'animation_collaboration_duplicate_dependency')
        if phase == 'component':
            identity = document['component_id']
            require(type(identity) is str and identity in components and document['base_source'] is None,
                    'animation_collaboration_phase_mismatch')
            require(set(identities) == set(components[identity]['depends_on']),
                    'animation_collaboration_direct_dependencies_required')
        else:
            require(document['component_id'] is None and set(identities) == set(components),
                    'animation_collaboration_integrate_dependencies_required')
            if document['base_source'] is not None:
                _validate_source_output(document['base_source'], document)
                require(document['base_source']['engine'] == brief['engine'],
                        'animation_collaboration_engine_not_admitted')
    _bounded(document, PHASE_INPUT_MAX_BYTES[phase], 'animation_collaboration_input_too_large')
    return deepcopy(document)


def _normalize_methods(value, context, standards, source):
    require(type(value) is list and 1 <= len(value) <= 4)
    result = deepcopy(value)
    parents = {row['version_id']: row for row in context['methods'] if row.get('version_id')}
    seen = set()
    for row in result:
        require(type(row) is dict and _METHOD_REQUIRED <= set(row)
            <= _METHOD_REQUIRED | {'method_id', 'parent_version_id'} and row['schema'] == 'video.animation-method.v2',
            'animation_collaboration_method_invalid')
        require(row['research_record_ids'] == [], 'animation_collaboration_research_not_supplied')
        package = row['skill_package']
        if type(package) is dict and set(package) == {'skill_id', 'version', 'kind', 'files', 'runtime_requirements'}:
            package = build_skill_package(**package)
        package = validate_skill_package(package)
        row['skill_package'] = package
        require(type(row['runtime_requirements']) is dict
            and type(row['runtime_requirements'].get('engine')) is str
            and row['runtime_requirements']['engine'] in ENGINES,
            'animation_collaboration_runtime_mismatch')
        projected = {key: deepcopy(item) for key, item in row.items()
                     if key not in {'schema', 'skill_package', 'research_record_ids'}}
        method_contract.methods([projected])
        require(all(standards.get(item['id']) == item for item in row['criteria']),
                'animation_collaboration_criteria_changed')
        engine = row['runtime_requirements']['engine']
        require(engine == source['engine'] and engine in package['runtime_requirements']['engines']
            and set(package['runtime_requirements']['engines']) <= set(context['constraints']['allowed_engines']),
            'animation_collaboration_runtime_mismatch')
        require(package['skill_id'] not in seen, 'animation_collaboration_duplicate_method')
        seen.add(package['skill_id'])
        parent_id, method_id = row.get('parent_version_id'), row.get('method_id')
        if parent_id is not None or method_id is not None:
            parent = parents.get(parent_id)
            require(parent is not None and method_id == parent.get('method_id')
                and 'skill_package' in parent and package['skill_id'] == parent['skill_package']['skill_id']
                and package['version'] == parent['skill_package']['version'] + 1,
                'animation_collaboration_parent_not_supplied')
        else:
            require(package['version'] == 1, 'animation_collaboration_parent_not_supplied')
    return result


def _sound_events(value, duration):
    require(type(value) is list and len(value) <= 128)
    for row in value:
        _fields(row, {'time', 'duration', 'frequency', 'gain', 'waveform'})
        for key, lower, upper in (('time', 0, 600), ('duration', .01, 5),
                                  ('frequency', 40, 16000), ('gain', 0, .8)):
            number = row[key]
            require(type(number) in (int, float) and math.isfinite(number) and lower <= number <= upper)
        require(type(row['waveform']) is str and row['waveform'] in {'sine', 'triangle', 'noise'}
            and row['time'] + row['duration'] <= duration)


def validate_result(document, input_sha256, input_document=None):
    _digest(input_sha256)
    require(input_document is not None, 'animation_collaboration_input_required')
    context = validate_input(input_document)
    _fields(document, _RESULT_FIELDS)
    _bounded(document, OUTPUT_MAX_BYTES, 'animation_collaboration_output_too_large')
    require(document['schema'] == RESULT_SCHEMA and document['input_sha256'] == input_sha256 == digest(context)
        and document['phase'] == context['phase'], 'animation_collaboration_input_binding_mismatch')
    method_contract.criteria(document['criteria'])
    if context['criteria']:
        require(document['criteria'] == context['criteria'], 'animation_collaboration_criteria_changed')
    require(document['review'] is None and document['knowledge_proposals'] == [],
            'animation_collaboration_unobserved_claim')
    phase = context['phase']
    result = deepcopy(document)
    if phase == 'plan':
        validate_plan(document['plan'], context['constraints'])
        require(document['component'] is None and document['methods'] == []
            and document['source'] is None and document['sound_events'] == [],
            'animation_collaboration_phase_mismatch')
    elif phase == 'component':
        require(document['plan'] is None and document['methods'] == [] and document['source'] is None
            and document['sound_events'] == [], 'animation_collaboration_phase_mismatch')
        component = validate_component(document['component'], context['brief'])
        require(component['component_id'] == context['component_id'],
                'animation_collaboration_component_binding_mismatch')
    else:
        require(document['plan'] is None and document['component'] is None,
                'animation_collaboration_phase_mismatch')
        source = _validate_source_output(document['source'], context)
        require(source['engine'] == context['brief']['engine'], 'animation_collaboration_engine_not_admitted')
        standards = {row['id']: row for row in document['criteria']}
        result['methods'] = _normalize_methods(document['methods'], context, standards, source)
        _sound_events(document['sound_events'], context['constraints']['duration_seconds'])
    _bounded(result, OUTPUT_MAX_BYTES, 'animation_collaboration_output_too_large')
    return result


def compile_final_result(result, input_document):
    """Return ingestible candidate fields without relabeling the Agent provenance.

    The result intentionally retains this profile's schema, phase and input
    hash. Standard v2 methods/Skill packages and scene source can be stored by
    existing consumers; no legacy method-author input or evidence is invented.
    """
    context = validate_input(input_document)
    require(context['phase'] == 'integrate', 'animation_collaboration_final_phase_required')
    return validate_result(result, digest(context), context)
