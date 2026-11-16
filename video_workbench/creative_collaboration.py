"""Pure scheduling for bounded, dependency-aware collaborative creation.

The scheduler knows operations, dependencies and immutable task identity. A
caller supplies its document schema and optional domain validators, then owns
reservation, submission, reconciliation, cancellation and delivery. The core
never invokes an Agent, executes contributed content or mutates the ledger.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
import time

from agent_service.contracts import ServiceError, digest


MAX_PARALLEL = 2
PHASES = frozenset({'plan', 'component', 'integrate'})
ACTIVE = frozenset({'reserved', 'submitting', 'submission_unknown', 'submitted', 'running'})
TERMINAL = frozenset({'completed', 'failed', 'cancelled', 'released'})


@dataclass(frozen=True)
class CollaborationPolicy:
    input_schema: str
    error_prefix: str = 'creative_collaboration'

    def require(self, value, suffix='invalid', status=422):
        if not value:
            raise ServiceError(self.error_prefix + '_' + suffix, status)


def validate_request(value, *, policy, budget=None, max_rounds=1):
    """Validate an explicit bounded round without increasing its allocation."""
    if value is None:
        return None
    require = policy.require
    require(type(value) is dict and set(value) <= {'max_components'}, 'request_invalid')
    count = value.get('max_components', 3)
    require(type(count) is int and 2 <= count <= 4, 'components_invalid')
    if budget is not None:
        require(type(budget) is dict, 'budget_invalid')
        max_rounds = budget.get('max_rounds', max_rounds)
        if 'creator' in budget:
            require(type(budget['creator']) is int and budget['creator'] >= count + 2,
                    'budget_insufficient')
    require(type(max_rounds) is int and max_rounds == 1, 'rounds_invalid')
    return {'max_components': count}


def required_creator_budget(config, *, policy):
    config = validate_request(config, policy=policy)
    policy.require(config is not None, 'not_enabled')
    return config['max_components'] + 2


def _config(run, policy):
    policy.require(type(run) is dict and type(run.get('frozen')) is dict)
    return validate_request(run['frozen'].get('collaboration'), policy=policy,
                            budget=run.get('budget'))


def _operations(run, policy):
    require = policy.require
    operations = run.get('operations', [])
    require(type(operations) is list)
    result, ids, keys = [], set(), set()
    for op in operations:
        require(type(op) is dict and type(op.get('input')) is dict)
        if op['input'].get('schema') != policy.input_schema:
            require(op.get('stage') != 'creator', 'unexpected_creator', 409)
            continue
        document = op['input']
        require(op.get('stage') == 'creator' and document.get('phase') in PHASES
                and type(op.get('id')) is str and op['id'] not in ids
                and op.get('status') in ACTIVE | TERMINAL, 'operation_invalid', 409)
        require(op.get('commission_id', run['id']) == run['id']
                and op.get('owner', run.get('owner')) == run.get('owner'),
                'operation_scope_invalid', 409)
        if 'input_sha256' in op:
            require(op['input_sha256'] == digest(document), 'input_changed', 409)
        component_id = document.get('component_id')
        require(type(component_id) is str if document['phase'] == 'component'
                else component_id is None, 'operation_invalid', 409)
        key = (document['phase'], component_id)
        require(key not in keys, 'duplicate_operation', 409)
        if op['status'] == 'completed':
            outcome = op.get('result')
            require(type(outcome) is dict and outcome.get('phase') == document['phase']
                    and outcome.get('input_sha256') == digest(document),
                    'result_binding_invalid', 409)
            if 'result_sha256' in op:
                require(op['result_sha256'] == digest(outcome), 'result_changed', 409)
        ids.add(op['id'])
        keys.add(key)
        result.append(op)
    return result


def _parts(plan, config, policy):
    require = policy.require
    require(type(plan) is dict and type(plan.get('components')) is list
            and 2 <= len(plan['components']) <= config['max_components'], 'plan_invalid', 409)
    parts = plan['components']
    ids = [part.get('id') for part in parts if type(part) is dict]
    require(len(ids) == len(parts) and all(type(item) is str and item for item in ids)
            and len(set(ids)) == len(ids), 'plan_invalid', 409)
    by_id = {part['id']: part for part in parts}
    for part in parts:
        deps = part.get('depends_on')
        require(type(deps) is list and all(type(item) is str for item in deps)
                and len(set(deps)) == len(deps) and set(deps) <= set(ids)
                and part['id'] not in deps, 'dependencies_invalid', 409)
    resolved = set()
    while len(resolved) < len(parts):
        ready = {key for key, part in by_id.items() if set(part['depends_on']) <= resolved} - resolved
        require(ready, 'dependency_cycle', 409)
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


def next_action(run, *, policy, now=None, validate_plan=None, validate_component=None,
                validate_delivery=None):
    """Return an original-ID decision; callbacks only validate domain content.

    ``deliver`` retains the exact integration input and result. The caller
    decides how to consume it. Unknown submissions stay active, and stopping
    always drains active original operations before returning ``stop``.
    """
    require = policy.require
    config = _config(run, policy)
    if config is None:
        return {'kind': 'disabled'}
    operations = _operations(run, policy)
    require(len([op for op in operations if op['status'] != 'released'])
            <= required_creator_budget(config, policy=policy), 'budget_exceeded', 409)
    if run.get('state') in {'completed', 'failed', 'cancelled'}:
        require(not any(op['status'] in ACTIVE for op in operations),
                'terminal_operation_active', 409)
        return {'kind': 'done'}
    now = time.time() if now is None else now
    deadline = run.get('deadline_at')
    require(type(now) in (int, float) and math.isfinite(now)
            and type(deadline) in (int, float) and math.isfinite(deadline))
    active = [op for op in operations if op['status'] in ACTIVE]
    failed = any(op['status'] in {'failed', 'cancelled', 'released'} for op in operations)
    reason = ('cancelled' if run.get('cancel_requested') else
              'budget_exhausted' if now >= deadline else 'failed' if failed else None)
    if reason:
        return _advance(active, cancel=True, reason=reason) if active else dict(kind='stop', reason=reason)
    plans = [op for op in operations if op['input']['phase'] == 'plan']
    if not plans:
        require(not operations, 'plan_missing', 409)
        remaining = run.get('remaining', run.get('budget', {}))
        require(type(remaining.get('creator')) is int
                and remaining['creator'] >= required_creator_budget(config, policy=policy),
                'budget_insufficient', 409)
        return dict(kind='reserve', operations=[_reserve('plan', deadline_at=deadline)])
    plan_operation = plans[0]
    if plan_operation['status'] in ACTIVE:
        require(len(operations) == 1, 'plan_incomplete', 409)
        return _advance([plan_operation])
    plan = plan_operation['result'].get('plan')
    standards = plan_operation['result'].get('criteria')
    require(type(standards) is list and bool(standards), 'criteria_required', 409)
    parts = _parts(plan, config, policy)
    if validate_plan is not None:
        validate_plan(plan, plan_operation['input'])
    part_by_id = {part['id']: part for part in parts}
    component_ops = {op['input'].get('component_id'): op for op in operations
                     if op['input']['phase'] == 'component'}
    require(set(component_ops) <= set(part_by_id), 'component_unknown', 409)
    completed = {key: op for key, op in component_ops.items() if op['status'] == 'completed'}
    for identity, op in component_ops.items():
        require(op['input'].get('brief') == plan, 'plan_binding_invalid', 409)
        require(op['input'].get('criteria') == standards, 'criteria_changed', 409)
        direct = part_by_id[identity]['depends_on']
        require(set(direct) <= set(completed), 'dependency_not_ready', 409)
        expected = [completed[key]['result']['component'] for key in direct]
        require(op['input'].get('dependencies') == expected, 'dependency_changed', 409)
        if op['status'] == 'completed':
            component = op['result'].get('component')
            require(type(component) is dict and component.get('component_id') == identity
                    and component.get('interface') == part_by_id[identity].get('interface'),
                    'component_binding_invalid', 409)
            if validate_component is not None:
                validate_component(component, part_by_id[identity], plan, op['input'])
    integration = next((op for op in operations if op['input']['phase'] == 'integrate'), None)
    require(integration is None or len(completed) == len(parts), 'integration_not_ready', 409)
    require(len(active) <= MAX_PARALLEL and all(op['input']['phase'] == 'component' for op in active)
            if integration is None else not any(op['input']['phase'] == 'component' for op in active),
            'parallel_limit_exceeded', 409)
    if len(completed) < len(parts):
        ready = [part for part in parts if part['id'] not in component_ops
                 and set(part['depends_on']) <= set(completed)]
        ready = ready[:MAX_PARALLEL - len(active)]
        if ready:
            return dict(kind='reserve', operations=[_reserve('component', deadline_at=deadline,
                brief=plan, part=part, dependencies=[completed[key]['result']['component']
                    for key in part['depends_on']], criteria=standards) for part in ready],
                active_operation_ids=[op['id'] for op in active])
        require(active, 'dependency_stalled', 409)
        return _advance(active)
    all_components = [completed[part['id']]['result']['component'] for part in parts]
    if integration is None:
        return dict(kind='reserve', operations=[_reserve('integrate', deadline_at=deadline,
            brief=plan, dependencies=all_components, criteria=standards)])
    require(integration['input'].get('brief') == plan
            and integration['input'].get('dependencies') == all_components
            and integration['input'].get('criteria') == standards, 'integration_binding_invalid', 409)
    if integration['status'] in ACTIVE:
        return _advance([integration])
    outcome = integration['result']
    if validate_delivery is not None:
        validate_delivery(outcome, plan, integration['input'])
    return dict(kind='deliver', author_operation_id=integration['id'],
                input=deepcopy(integration['input']), result=deepcopy(outcome))


def progress_view(run, *, policy, validate_plan=None):
    """Describe dependencies and reservations without inferring provider calls."""
    config = _config(run, policy)
    if config is None:
        return None
    operations = _operations(run, policy)
    plan_op = next((op for op in operations if op['input']['phase'] == 'plan'), None)
    plan = (plan_op['result'].get('plan') if plan_op and plan_op['status'] == 'completed' else None)
    parts = _parts(plan, config, policy) if plan is not None else []
    if plan is not None and validate_plan is not None:
        validate_plan(plan, plan_op['input'])
    by_id = {op['input'].get('component_id'): op for op in operations
             if op['input']['phase'] == 'component'}
    completed = {key for key, op in by_id.items() if op['status'] == 'completed'}
    components = [dict(id=part['id'], title=part.get('responsibility', part['id']),
        depends_on=list(part['depends_on']),
        status=(by_id[part['id']]['status'] if part['id'] in by_id else
                'ready' if set(part['depends_on']) <= completed else 'waiting')) for part in parts]
    integrated = next((op for op in operations if op['input']['phase'] == 'integrate'), None)
    stage = ('plan' if plan is None else 'component' if len(completed) < len(parts) else 'integrate')
    if integrated and integrated['status'] == 'completed':
        stage = 'deliver'
    if run.get('cancel_requested'):
        stage = 'stopping'
    if run.get('state') in {'completed', 'failed', 'cancelled'}:
        stage = run['state']
    return dict(enabled=True, stage=stage, components=components, completed=len(completed), total=len(parts),
        max_components=config['max_components'], max_parallel=MAX_PARALLEL,
        author_calls_used=len([op for op in operations if op['status'] != 'released']),
        author_calls_max=required_creator_budget(config, policy=policy),
        author_calls_scope='reserved_creator_operations_not_provider_model_requests')
