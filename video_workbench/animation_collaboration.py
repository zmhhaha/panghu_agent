"""Bounded, source-only collaboration over the existing commission ledger.

This module makes decisions; it never submits jobs, changes the ledger, renders
parts or accepts an artwork. Each reservation is an ordinary ``creator``
operation. The service must hold its commission lock while reserving decisions
and use the existing claim / original-ID lookup / cancel / stop-proof path.

Integration helpers:
* Normalize ``collaboration`` and allocate ``required_creator_budget`` at start;
  this replaces the ordinary planner/creator allocation for this one round.
* For each ``reserve`` descriptor, call ``operation_input`` with the initial
  method document (plan) or original completed plan input (later phases), then
  store.reserve with the supplied stage and idempotency key.
* ``advance`` identifies existing operations, including unknown submissions.
  ``cancel=True`` means drain all of them with original identity. Release only
  never-claimed reservations; do not declare a commission stopped beforehand.
* ``render`` is the sole final author output. Send its input to the normal
  renderer; ``complete`` hands the existing render/review/materialize flow back.

All operations keep the commission's original deadline. Raw plans, component
modules and integration input/result stay in that same ledger for snapshots.
"""
from __future__ import annotations

from copy import deepcopy
import math
import time

from agent_service.contracts import ServiceError, digest


INPUT_SCHEMA = 'video.animation-collaboration-input.v1'
PROFILE = 'video.animation-collaboration.v1'
MAX_PARALLEL = 2
PHASES = frozenset({'plan', 'component', 'integrate'})
ACTIVE = frozenset({'reserved', 'submitting', 'submission_unknown', 'submitted', 'running'})
TERMINAL = frozenset({'completed', 'failed', 'cancelled', 'released'})


def _require(value, code='animation_collaboration_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def validate_request(value, *, budget=None, max_rounds=1):
    """Explicit presence enables Canvas collaboration; no budget is increased.

    ``budget`` may be the public max_rounds/max_seconds object or the normalized
    ledger allocation. The caller allocates the returned requirement explicitly.
    """
    if value is None:
        return None
    _require(type(value) is dict and set(value) <= {'max_components'},
             'animation_collaboration_request_invalid')
    count = value.get('max_components', 3)
    _require(type(count) is int and 2 <= count <= 4,
             'animation_collaboration_components_invalid')
    if budget is not None:
        _require(type(budget) is dict, 'animation_collaboration_budget_invalid')
        max_rounds = budget.get('max_rounds', max_rounds)
        if 'creator' in budget:
            _require(type(budget['creator']) is int and budget['creator'] >= count + 2
                     and all(type(budget.get(key)) is int and budget[key] >= 1
                             for key in ('render', 'review')),
                     'animation_collaboration_budget_insufficient')
    _require(type(max_rounds) is int and max_rounds == 1,
             'animation_collaboration_rounds_invalid')
    return {'max_components': count}


def required_creator_budget(config):
    config = validate_request(config)
    _require(config is not None, 'animation_collaboration_not_enabled')
    return 1 + config['max_components'] + 1


def required_budget(config):
    return dict(creator=required_creator_budget(config), render=1, review=1)


def operation_input(base_document, phase, *, config, brief=None, component_id=None,
                    dependencies=None, criteria=None):
    """Project relevant creative context, never the Wiki or operation history.

    Size violations are rejected by the new contract rather than silently
    truncating source, methods or a developer's request.
    """
    from agent_service.profiles.video_animation_collaboration import build_input

    config = validate_request(config)
    _require(config is not None, 'animation_collaboration_not_enabled')
    _require(type(base_document) is dict and type(phase) is str and phase in PHASES)
    constraints = deepcopy(base_document.get('constraints', {}))
    _require(type(constraints) is dict and 'canvas' in constraints.get('allowed_engines', []),
             'animation_collaboration_canvas_not_admitted', 409)
    constraints.pop('result_requirements', None)
    constraints['allowed_engines'] = ['canvas']
    constraints['collaboration'] = dict(max_components=config['max_components'],
                                        max_parallel=MAX_PARALLEL)
    return build_input(phase=phase, goal=base_document['goal'],
        criteria=deepcopy(base_document.get('criteria', []) if criteria is None else criteria),
        constraints=constraints,
        assets=deepcopy(base_document.get('assets', {})),
        reference=deepcopy(base_document.get('reference')),
        feedback=base_document.get('feedback', ''), brief=deepcopy(brief),
        component_id=component_id, dependencies=deepcopy(dependencies or []),
        methods=deepcopy(base_document.get('methods', [])),
        base_source=(deepcopy(base_document.get('base_source')) if phase == 'integrate' else None))


def _config(run):
    _require(type(run) is dict and type(run.get('frozen')) is dict)
    return validate_request(run['frozen'].get('collaboration'), budget=run.get('budget'))


def _operations(run):
    operations = run.get('operations', [])
    _require(type(operations) is list)
    result, ids, keys = [], set(), set()
    for op in operations:
        _require(type(op) is dict and type(op.get('input')) is dict)
        if op['input'].get('schema') != INPUT_SCHEMA:
            _require(op.get('stage') != 'creator',
                     'animation_collaboration_unexpected_creator', 409)
            continue
        document = op['input']
        _require(op.get('stage') == 'creator' and document.get('phase') in PHASES
                 and type(op.get('id')) is str and op['id'] not in ids
                 and op.get('status') in ACTIVE | TERMINAL,
                 'animation_collaboration_operation_invalid', 409)
        _require(op.get('commission_id', run['id']) == run['id']
                 and op.get('owner', run.get('owner')) == run.get('owner'),
                 'animation_collaboration_operation_scope_invalid', 409)
        if 'input_sha256' in op:
            _require(op['input_sha256'] == digest(document),
                     'animation_collaboration_input_changed', 409)
        component_id = document.get('component_id')
        _require(type(component_id) is str if document['phase'] == 'component' else component_id is None,
                 'animation_collaboration_operation_invalid', 409)
        key = (document['phase'], component_id)
        _require(key not in keys, 'animation_collaboration_duplicate_operation', 409)
        if op['status'] == 'completed':
            outcome = op.get('result')
            _require(type(outcome) is dict and outcome.get('phase') == document['phase']
                     and outcome.get('input_sha256') == digest(document),
                     'animation_collaboration_result_binding_invalid', 409)
            if 'result_sha256' in op:
                _require(op['result_sha256'] == digest(outcome),
                         'animation_collaboration_result_changed', 409)
        ids.add(op['id'])
        keys.add(key)
        result.append(op)
    return result


def _plan(plan, config):
    _require(type(plan) is dict and plan.get('engine') == 'canvas'
             and type(plan.get('components')) is list
             and 2 <= len(plan['components']) <= config['max_components'],
             'animation_collaboration_plan_invalid', 409)
    parts = plan['components']
    ids = [part.get('id') for part in parts if type(part) is dict]
    _require(len(ids) == len(parts) and all(type(item) is str and item for item in ids)
             and len(set(ids)) == len(ids), 'animation_collaboration_plan_invalid', 409)
    by_id = {part['id']: part for part in parts}
    for part in parts:
        deps = part.get('depends_on')
        _require(type(deps) is list and all(type(item) is str for item in deps)
                 and len(set(deps)) == len(deps) and set(deps) <= set(ids)
                 and part['id'] not in deps, 'animation_collaboration_dependencies_invalid', 409)
    resolved = set()
    while len(resolved) < len(parts):
        ready = {key for key, part in by_id.items() if set(part['depends_on']) <= resolved} - resolved
        _require(ready, 'animation_collaboration_dependency_cycle', 409)
        resolved.update(ready)
    return parts


def _reserve(phase, *, deadline_at, brief=None, part=None, dependencies=None, criteria=None):
    component_id = part['id'] if part is not None else None
    suffix = 'component-' + component_id if component_id is not None else phase
    return dict(stage='creator', phase=phase, component_id=component_id,
        brief=deepcopy(brief), dependencies=deepcopy(dependencies or []), criteria=deepcopy(criteria),
        idempotency_key='collaboration-' + suffix, deadline_at=deadline_at)


def _advance(operations, *, cancel=False, reason=None):
    return dict(kind='advance', operation_ids=[op['id'] for op in operations],
                cancel=cancel, reason=reason)


def next_action(run, *, now=None):
    """Choose bounded reservations or reconcile original IDs, without mutation."""
    config = _config(run)
    if config is None:
        return {'kind': 'disabled'}
    operations = _operations(run)
    _require(len([op for op in operations if op['status'] != 'released'])
             <= required_creator_budget(config), 'animation_collaboration_budget_exceeded', 409)
    if run.get('state') in {'completed', 'failed', 'cancelled'}:
        _require(not any(op['status'] in ACTIVE for op in operations),
                 'animation_collaboration_terminal_operation_active', 409)
        return {'kind': 'done'}
    now = time.time() if now is None else now
    deadline = run.get('deadline_at')
    _require(type(now) in (int, float) and math.isfinite(now)
             and type(deadline) in (int, float) and math.isfinite(deadline))
    active = [op for op in operations if op['status'] in ACTIVE]
    failed = any(op['status'] in {'failed', 'cancelled', 'released'} for op in operations)
    reason = ('cancelled' if run.get('cancel_requested') else
              'budget_exhausted' if now >= deadline else 'failed' if failed else None)
    if reason:
        return _advance(active, cancel=True, reason=reason) if active else dict(kind='stop', reason=reason)
    plans = [op for op in operations if op['input']['phase'] == 'plan']
    if not plans:
        _require(not operations, 'animation_collaboration_plan_missing', 409)
        remaining = run.get('remaining', run.get('budget', {}))
        _require(type(remaining.get('creator')) is int
                 and remaining['creator'] >= required_creator_budget(config),
                 'animation_collaboration_budget_insufficient', 409)
        return dict(kind='reserve', operations=[_reserve('plan', deadline_at=deadline)])
    plan_operation = plans[0]
    if plan_operation['status'] in ACTIVE:
        _require(len(operations) == 1, 'animation_collaboration_plan_incomplete', 409)
        return _advance([plan_operation])
    plan = plan_operation['result'].get('plan')
    standards = plan_operation['result'].get('criteria')
    _require(type(standards) is list and bool(standards), 'animation_collaboration_criteria_required', 409)
    parts = _plan(plan, config)
    part_by_id = {part['id']: part for part in parts}
    component_ops = {op['input'].get('component_id'): op for op in operations
                     if op['input']['phase'] == 'component'}
    _require(set(component_ops) <= set(part_by_id), 'animation_collaboration_component_unknown', 409)
    completed = {key: op for key, op in component_ops.items() if op['status'] == 'completed'}
    for identity, op in component_ops.items():
        _require(op['input'].get('brief') == plan,
                 'animation_collaboration_plan_binding_invalid', 409)
        _require(op['input'].get('criteria') == standards,
                 'animation_collaboration_criteria_changed', 409)
        direct = part_by_id[identity]['depends_on']
        _require(set(direct) <= set(completed),
                 'animation_collaboration_dependency_not_ready', 409)
        expected = [completed[key]['result']['component'] for key in direct]
        _require(op['input'].get('dependencies') == expected,
                 'animation_collaboration_dependency_changed', 409)
        if op['status'] == 'completed':
            component = op['result'].get('component')
            _require(type(component) is dict and component.get('component_id') == identity
                     and component.get('engine') == 'canvas'
                     and component.get('interface') == part_by_id[identity]['interface'],
                     'animation_collaboration_component_binding_invalid', 409)
    integration = next((op for op in operations if op['input']['phase'] == 'integrate'), None)
    _require(integration is None or len(completed) == len(parts),
             'animation_collaboration_integration_not_ready', 409)
    _require(len(active) <= MAX_PARALLEL and all(op['input']['phase'] == 'component' for op in active)
             if integration is None else not any(op['input']['phase'] == 'component' for op in active),
             'animation_collaboration_parallel_limit_exceeded', 409)
    if len(completed) < len(parts):
        ready = [part for part in parts if part['id'] not in component_ops
                 and set(part['depends_on']) <= set(completed)]
        ready = ready[:MAX_PARALLEL - len(active)]
        if ready:
            return dict(kind='reserve', operations=[_reserve('component', deadline_at=deadline,
                brief=plan, part=part, dependencies=[completed[key]['result']['component']
                    for key in part['depends_on']], criteria=standards) for part in ready],
                active_operation_ids=[op['id'] for op in active])
        _require(active, 'animation_collaboration_dependency_stalled', 409)
        return _advance(active)
    all_components = [completed[part['id']]['result']['component'] for part in parts]
    if integration is None:
        return dict(kind='reserve', operations=[_reserve('integrate', deadline_at=deadline,
            brief=plan, dependencies=all_components, criteria=standards)])
    _require(integration['input'].get('brief') == plan
             and integration['input'].get('dependencies') == all_components
             and integration['input'].get('criteria') == standards,
             'animation_collaboration_integration_binding_invalid', 409)
    if integration['status'] in ACTIVE:
        return _advance([integration])
    outcome = integration['result']
    _require(type(outcome.get('source')) is dict and outcome['source'].get('engine') == 'canvas'
             and type(outcome.get('sound_events')) is list,
             'animation_collaboration_final_source_required', 409)
    renders = [op for op in run['operations'] if op.get('stage') == 'render'
               and op.get('input', {}).get('author_operation_id') == integration['id']]
    _require(len(renders) <= 1, 'animation_collaboration_duplicate_render', 409)
    if renders:
        return dict(kind='complete', author_operation_id=integration['id'],
                    render_operation_id=renders[0]['id'])
    return dict(kind='render', stage='render', idempotency_key='render-' + integration['id'],
        input=dict(author_operation_id=integration['id'], source=deepcopy(outcome['source']),
                   sound_events=deepcopy(outcome['sound_events']), document=deepcopy(integration['input'])))


def progress_view(run):
    """Describe part progress without presenting reservations as provider calls."""
    config = _config(run)
    if config is None:
        return None
    operations = _operations(run)
    plan_op = next((op for op in operations if op['input']['phase'] == 'plan'), None)
    plan = (plan_op['result'].get('plan') if plan_op and plan_op['status'] == 'completed' else None)
    parts = _plan(plan, config) if plan is not None else []
    by_id = {op['input'].get('component_id'): op for op in operations
             if op['input']['phase'] == 'component'}
    completed = {key for key, op in by_id.items() if op['status'] == 'completed'}
    components = [dict(id=part['id'], title=part['responsibility'],
        depends_on=list(part['depends_on']),
        status=(by_id[part['id']]['status'] if part['id'] in by_id else
                'ready' if set(part['depends_on']) <= completed else 'waiting')) for part in parts]
    integrated = next((op for op in operations if op['input']['phase'] == 'integrate'), None)
    stage = ('plan' if plan is None else 'component' if len(completed) < len(parts) else 'integrate')
    if integrated and integrated['status'] == 'completed':
        stage = 'render'
        renders = [op for op in run.get('operations', []) if op.get('stage') == 'render'
                   and op.get('input', {}).get('author_operation_id') == integrated['id']]
        if renders and renders[0]['status'] == 'completed':
            stage = 'review'
    if run.get('cancel_requested'):
        stage = 'stopping'
    if run.get('state') in {'completed', 'failed', 'cancelled'}:
        stage = run['state']
    return dict(enabled=True, stage=stage, components=components, completed=len(completed), total=len(parts),
        max_components=config['max_components'], max_parallel=MAX_PARALLEL,
        author_calls_used=len([op for op in operations if op['status'] != 'released']),
        author_calls_max=required_creator_budget(config),
        author_calls_scope='reserved_creator_operations_not_provider_model_requests',
        final_sample_limit=1)
