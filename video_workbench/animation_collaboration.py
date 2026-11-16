"""Animation capability adapter for the shared creative collaboration scheduler.

The core owns dependency scheduling and original operation identity. This
adapter projects admitted creative context, checks capability consistency and
hands exactly one completed composition to the existing render/review flow.
It never submits tasks, mutates the ledger or accepts an artwork.
"""
from __future__ import annotations

from copy import deepcopy

from agent_service.contracts import ServiceError
from agent_service.profiles.video_animation_collaboration_contract import ENGINES, INPUT_SCHEMA, PROFILE
from . import creative_collaboration as core


MAX_PARALLEL = core.MAX_PARALLEL
PHASES = core.PHASES
ACTIVE = core.ACTIVE
TERMINAL = core.TERMINAL
_POLICY = core.CollaborationPolicy(INPUT_SCHEMA, error_prefix='animation_collaboration')


def _require(value, code='animation_collaboration_invalid', status=422):
    if not value:
        raise ServiceError(code, status)


def validate_request(value, *, budget=None, max_rounds=1):
    """Enable bounded collaboration without expanding the disclosed budget."""
    config = core.validate_request(value, policy=_POLICY, budget=budget, max_rounds=max_rounds)
    if config is not None and budget is not None and 'creator' in budget:
        _require(all(type(budget.get(key)) is int and budget[key] >= 1
                     for key in ('render', 'review')), 'animation_collaboration_budget_insufficient')
    return config


def required_creator_budget(config):
    return core.required_creator_budget(config, policy=_POLICY)


def required_budget(config):
    return dict(creator=required_creator_budget(config), render=1, review=1)


def _admitted_engines(document):
    constraints = document.get('constraints')
    _require(type(constraints) is dict, 'animation_collaboration_engine_not_admitted', 409)
    allowed = constraints.get('allowed_engines')
    _require(type(allowed) is list and bool(allowed)
             and all(type(engine) is str and engine in ENGINES for engine in allowed)
             and len(set(allowed)) == len(allowed), 'animation_collaboration_engine_not_admitted', 409)
    return allowed


def operation_input(base_document, phase, *, config, brief=None, component_id=None,
                    dependencies=None, criteria=None):
    """Project task context; retain the frozen admitted capability selection.

    Wiki/history database wrappers are excluded by the existing profile. Size
    violations are rejected rather than silently truncating creative content.
    """
    from agent_service.profiles.video_animation_collaboration import build_input

    config = validate_request(config)
    _require(config is not None, 'animation_collaboration_not_enabled')
    _require(type(base_document) is dict and type(phase) is str and phase in PHASES)
    _admitted_engines(base_document)
    constraints = deepcopy(base_document['constraints'])
    constraints.pop('result_requirements', None)
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


def _validate_plan(plan, document):
    _require(type(plan.get('engine')) is str and plan['engine'] in _admitted_engines(document),
             'animation_collaboration_engine_not_admitted', 409)


def _validate_component(component, part, plan, document):
    _require(component.get('engine') == plan['engine']
             and component['engine'] in _admitted_engines(document),
             'animation_collaboration_component_binding_invalid', 409)


def _validate_delivery(outcome, plan, document):
    source = outcome.get('source')
    _require(type(source) is dict and source.get('engine') == plan['engine']
             and source['engine'] in _admitted_engines(document)
             and type(outcome.get('sound_events')) is list,
             'animation_collaboration_final_source_required', 409)


def next_action(run, *, now=None):
    """Schedule admitted capabilities through the same domain-neutral core."""
    if type(run) is dict and type(run.get('frozen')) is dict:
        validate_request(run['frozen'].get('collaboration'), budget=run.get('budget'))
    decision = core.next_action(run, policy=_POLICY, now=now,
        validate_plan=_validate_plan, validate_component=_validate_component,
        validate_delivery=_validate_delivery)
    if decision['kind'] != 'deliver':
        return decision
    author_id = decision['author_operation_id']
    renders = [op for op in run['operations'] if op.get('stage') == 'render'
               and op.get('input', {}).get('author_operation_id') == author_id]
    _require(len(renders) <= 1, 'animation_collaboration_duplicate_render', 409)
    if renders:
        return dict(kind='complete', author_operation_id=author_id,
                    render_operation_id=renders[0]['id'])
    return dict(kind='render', stage='render', idempotency_key='render-' + author_id,
        input=dict(author_operation_id=author_id, source=decision['result']['source'],
                   sound_events=decision['result']['sound_events'], document=decision['input']))


def progress_view(run):
    """Adapt delivery progress to the final render/review stages."""
    if type(run) is dict and type(run.get('frozen')) is dict:
        validate_request(run['frozen'].get('collaboration'), budget=run.get('budget'))
    view = core.progress_view(run, policy=_POLICY, validate_plan=_validate_plan)
    if view is None:
        return None
    if view['stage'] == 'deliver':
        view['stage'] = 'render'
        author = next(op for op in run['operations']
                      if op.get('input', {}).get('schema') == INPUT_SCHEMA
                      and op['input'].get('phase') == 'integrate')
        renders = [op for op in run.get('operations', []) if op.get('stage') == 'render'
                   and op.get('input', {}).get('author_operation_id') == author['id']]
        if renders and renders[0]['status'] == 'completed':
            view['stage'] = 'review'
    view['final_sample_limit'] = 1
    return view
