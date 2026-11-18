"""Bounded learning plans over real creator, render and review operations.

This is a business adapter for the shared dependency scheduler.  Plans and
source packages remain Agent authored.  A plan is not a probe result, a source
package is not rendered evidence, and a model opinion is not user acceptance.
"""
from __future__ import annotations

from copy import deepcopy
import re

from agent_service.contracts import ServiceError, digest
from . import creative_collaboration as collaboration


SCHEMA = 'video.learning-strategy.v1'
TASK_SCHEMA = 'video.learning-strategy-task.v1'
MAX_PROBES = 2
MAX_COMPONENTS = 3
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z')
_POLICY = collaboration.CollaborationPolicy(TASK_SCHEMA,
    error_prefix='learning_strategy')


def require(value, code='invalid', status=422):
    _POLICY.require(value, code, status)


def _text(value, maximum=8000):
    require(type(value) is str and value.strip() and len(value) <= maximum,
        'text_invalid')
    require(not any((ord(c) < 32 and c not in '\r\n\t') or
        0xD800 <= ord(c) <= 0xDFFF for c in value), 'text_invalid')
    return value


def _criteria(value):
    require(type(value) is list and 1 <= len(value) <= 16, 'criteria_invalid')
    return [_text(row, 2000) for row in value]


def options(*, allow_collaboration=True, allow_probes=True):
    """Freeze an opt-out, never an instruction to manufacture extra tasks."""
    require(type(allow_collaboration) is bool and type(allow_probes) is bool,
        'options_invalid')
    return dict(schema=SCHEMA, allow_collaboration=allow_collaboration,
        allow_probes=allow_probes, max_probes=MAX_PROBES,
        max_components=MAX_COMPONENTS, max_parallel=1,
        scope='first_reproduction_creation_only')


def validate_options(value):
    require(type(value) is dict and value == options(
        allow_collaboration=value.get('allow_collaboration'),
        allow_probes=value.get('allow_probes')), 'options_invalid', 409)
    return deepcopy(value)


def validate_plan(value, *, config, engine, allowed_engines, resource_plan=None):
    """Admit only a small explicit DAG using an actually allowed engine."""
    config = validate_options(config)
    require(type(value) is dict and {'rationale', 'criteria', 'probes', 'components'} <= set(value)
        <= {'rationale', 'criteria', 'probes', 'components', 'engine'}, 'plan_invalid')
    require(type(engine) is str and engine in allowed_engines, 'engine_not_allowed')
    require(value.get('engine', engine) == engine, 'engine_changed')
    _text(value['rationale'])
    standards = _criteria(value['criteria'])
    probes, components = value['probes'], value['components']
    require(type(probes) is list and len(probes) <= config['max_probes'], 'probes_invalid')
    require(type(components) is list and (not components or
        2 <= len(components) <= config['max_components']), 'components_invalid')
    require(config['allow_probes'] or not probes, 'probes_disabled')
    require(config['allow_collaboration'] or not components, 'collaboration_disabled')
    # The current Manim producer has its own resource/compose gate.  A browser
    # component is not evidence that concurrent Manim creation was admitted.
    require(resource_plan is None or not probes and not components,
        'resource_subtasks_not_admitted')
    checked_probes, checked_components, identities = [], [], set()
    for probe in probes:
        require(type(probe) is dict and set(probe) == {'id', 'goal', 'criteria'},
            'probe_invalid')
        identity = probe['id']
        require(type(identity) is str and _ID.fullmatch(identity) and identity not in identities,
            'task_id_invalid')
        identities.add(identity)
        checked_probes.append(dict(id=identity, goal=_text(probe['goal']),
            criteria=_criteria(probe['criteria'])))
    component_ids = []
    for part in components:
        require(type(part) is dict and set(part) == {
            'id', 'goal', 'criteria', 'depends_on', 'interface'}, 'component_invalid')
        identity = part['id']
        require(type(identity) is str and _ID.fullmatch(identity) and identity not in identities,
            'task_id_invalid')
        identities.add(identity)
        component_ids.append(identity)
        require(type(part['depends_on']) is list and all(type(row) is str
            for row in part['depends_on']) and len(set(part['depends_on'])) == len(part['depends_on']),
            'dependencies_invalid')
        checked_components.append(dict(id=identity, goal=_text(part['goal']),
            criteria=_criteria(part['criteria']), depends_on=list(part['depends_on']),
            interface=_text(part['interface'])))
    if checked_components:
        # Validate and schedule this same dependency graph with the domain
        # neutral core; do not silently reorder a cyclic or external dependency.
        graph = dict(engine=engine, components=[dict(part,
            responsibility=part['goal']) for part in checked_components])
        collaboration._parts(graph, {'max_components': config['max_components']}, _POLICY)
    return dict(engine=engine, rationale=value['rationale'], criteria=standards,
        probes=checked_probes, components=checked_components)


def task_guidance(config):
    """Versioned input data; no modification of an old profile or bundle."""
    config = validate_options(config)
    return dict(schema=TASK_SCHEMA, phase='plan', scope=config['scope'],
        allow_collaboration=config['allow_collaboration'], allow_probes=config['allow_probes'],
        max_probes=config['max_probes'], max_components=config['max_components'],
        instructions=[
            'Create one normal, complete editable source using the ordinary result contract.',
            'If the target needs no internal studies or specialists, omit method.learning_strategy or use empty probes/components; your source proceeds directly to rendering.',
            'Only when useful, propose method.learning_strategy with rationale, nonempty criteria, probes and components. A proposed plan is not executed evidence.',
            'A probe has id, goal and nonempty string[] criteria. It is a small focused technique study, not a second complete learning project.',
            'A component has id, goal, nonempty string[] criteria, depends_on (component IDs) and a plain interface contract. Use two or three only when the problem benefits from real specialist creators.',
            'The host runs each requested author as a separate paid operation. Probes are rendered and independently reviewed; component sources are integrated then the full work is rendered/reviewed.',
            'All tasks share this frozen experiment, feedback, knowledge choice and deadline. Do not claim collaborators, renders, reviews or improvement before their actual results are supplied.',
            'User-disabled collaboration/probes must remain empty. Manim resource plans cannot be combined with these subtasks in this admitted version.',
            'Components share one admitted browser engine and a common coordinate system, clock, interfaces and style; do not require a network resource or reference screenshot as an asset.',
        ])


def _operation_binding(operation):
    require(type(operation) is dict and type(operation.get('input')) is dict
        and digest(operation['input']) == operation.get('input_sha256'), 'input_changed', 409)
    require(operation.get('status') == 'completed' and type(operation.get('result')) is dict,
        'operation_incomplete', 409)
    require(operation['result'].get('input_sha256') == operation['input_sha256'],
        'result_binding_changed', 409)
    return dict(id=operation['id'], input_sha256=operation['input_sha256'],
        result_sha256=digest(operation['result']))


def verify_operation(operation, binding):
    require(_operation_binding(operation) == binding, 'operation_changed', 409)
    return operation


def operation_binding(operation):
    return _operation_binding(operation)


def collaborative_action(plan, plan_operation, components, integration=None, *,
                         experiment_id, owner, deadline_at, now):
    """Project actual immutable operations into the common DAG scheduler.

    Projection does not submit work or assert that two jobs ran concurrently.
    The learning transport currently reserves one ready specialist at a time.
    """
    graph = dict(engine=plan['engine'], components=[dict(part,
        responsibility=part['goal']) for part in plan['components']])
    def wrap(operation, phase, component_id=None, dependencies=None):
        binding = _operation_binding(operation)
        document = dict(schema=TASK_SCHEMA, phase=phase, component_id=component_id,
            source_input_sha256=binding['input_sha256'],
            source_result_sha256=binding['result_sha256'])
        outcome = dict(phase=phase)
        if phase == 'plan':
            outcome.update(plan=deepcopy(graph), criteria=deepcopy(plan['criteria']))
        else:
            document.update(brief=deepcopy(graph), criteria=deepcopy(plan['criteria']),
                dependencies=deepcopy(dependencies or []))
            if phase == 'component':
                part = next(row for row in plan['components'] if row['id'] == component_id)
                outcome['component'] = dict(component_id=component_id, interface=part['interface'],
                    engine=operation['result']['source']['engine'],
                    source=deepcopy(operation['result']['source']), operation=deepcopy(binding))
            else:
                outcome['source'] = deepcopy(operation['result']['source'])
        outcome['input_sha256'] = digest(document)
        return dict(id=operation['id'], owner=owner, commission_id=experiment_id,
            stage='creator', status='completed', input=document, input_sha256=digest(document),
            result=outcome, result_sha256=digest(outcome))
    projected = [wrap(plan_operation, 'plan')]
    by_id = {}
    remaining = list(components)
    while remaining:
        ready = [entry for entry in remaining if set(next(part for part in plan['components']
            if part['id'] == entry[0])['depends_on']) <= set(by_id)]
        require(ready, 'dependencies_invalid', 409)
        for identity, operation in ready:
            part = next(row for row in plan['components'] if row['id'] == identity)
            row = wrap(operation, 'component', identity,
                [by_id[key]['result']['component'] for key in part['depends_on']])
            require(row['result']['component']['engine'] == plan['engine'],
                'component_engine_changed', 409)
            projected.append(row)
            by_id[identity] = row
            remaining.remove((identity, operation))
    if integration is not None:
        projected.append(wrap(integration, 'integrate', dependencies=[
            by_id[part['id']]['result']['component'] for part in plan['components']]))
    virtual = dict(id=experiment_id, owner=owner, state='running', deadline_at=deadline_at,
        frozen=dict(collaboration={'max_components': MAX_COMPONENTS}),
        budget=dict(max_rounds=1, creator=MAX_COMPONENTS + 2),
        remaining=dict(creator=MAX_COMPONENTS + 2 - len(projected)), operations=projected)
    return collaboration.next_action(virtual, policy=_POLICY, now=now)


def progress(state):
    if state is None:
        return None
    return dict(enabled=True, phase=state['phase'],
        probes=[dict(id=row['id'], goal=row['goal'], status=row['status'],
            candidate_id=row.get('candidate_id'), review=deepcopy(row.get('review')))
            for row in state.get('probes', [])],
        components=[dict(id=row['id'], status='authored', operation_id=row['operation']['id'])
            for row in state.get('components', [])],
        plan_operation_id=state['plan_operation']['id'],
        max_probes=MAX_PROBES, max_components=MAX_COMPONENTS, max_parallel=1,
        scope='actual_reserved_operations_not_provider_model_requests',
        complete=bool(state.get('complete')))
