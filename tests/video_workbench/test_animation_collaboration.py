"""Collaboration scheduling safety with synthetic source; no models or media."""
from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench.animation_collaboration import (
    INPUT_SCHEMA, MAX_PARALLEL, next_action, operation_input, progress_view,
    required_budget, required_creator_budget, validate_request,
)
from video_workbench.animation_method_commissions import AnimationMethodCommissionStore


CRITERIA = [dict(id='shape', description='The complete shape remains readable.')]


def base_document():
    return dict(goal='Create a coherent synthetic ink landscape.', criteria=[], methods=[],
        constraints=dict(width=1280, height=720, fps=24, duration_seconds=8,
            allowed_engines=['html-svg', 'canvas', 'three']), assets={}, reference=None,
        feedback='', base_source=None)


def plan_document(engine='canvas'):
    return dict(engine=engine, composition='One coherent scene with negative space.',
        material='Directional marks follow the form.', clock='Absolute seconds, including backward seek.',
        interfaces='Each module exposes draw(ctx, seconds, shared).', keyframe_times=[0, 3, 8],
        components=[
            dict(id='mountains', responsibility='Mountain silhouettes and directional marks',
                 depends_on=[], interface='draw(ctx, seconds, shared)'),
            dict(id='foreground', responsibility='Foreground mass and sparse branches',
                 depends_on=[], interface='draw(ctx, seconds, shared)'),
            dict(id='atmosphere', responsibility='Combine fog with actual contours',
                 depends_on=['mountains', 'foreground'], interface='draw(ctx, seconds, shared)'),
        ])


def run_document():
    return dict(id='synthetic-commission', owner='developer', state='running', cancel_requested=False,
        frozen=dict(collaboration={'max_components': 3}),
        budget=dict(creator=5, render=1, review=1, seconds=300),
        remaining=dict(creator=5, render=1, review=1), deadline_at=400,
        operations=[], candidates=[], reviews=[])


def append_reservation(run, descriptor, *, base=None):
    plan = next((op for op in run['operations'] if op['input'].get('phase') == 'plan'), None)
    context = plan['input'] if plan else base_document() if base is None else base
    document = operation_input(context, descriptor['phase'], config=run['frozen']['collaboration'],
        brief=descriptor['brief'], component_id=descriptor['component_id'],
        dependencies=descriptor['dependencies'], criteria=descriptor['criteria'])
    op = dict(id='operation-' + str(len(run['operations'])), commission_id=run['id'], owner=run['owner'],
        stage='creator', status='reserved', input=document, input_sha256=digest(document),
        idempotency_key=descriptor['idempotency_key'], job_id=None, result=None)
    run['operations'].append(op)
    run['remaining']['creator'] -= 1
    return op


def complete(op, *, plan=None):
    document = op['input']
    result = dict(schema='video.animation-collaboration-result.v1', phase=document['phase'],
        input_sha256=digest(document), criteria=deepcopy(CRITERIA), methods=[], source=None,
        sound_events=[], review=None, knowledge_proposals=[], plan=None, component=None)
    if document['phase'] == 'plan':
        result['plan'] = deepcopy(plan or plan_document())
    elif document['phase'] == 'component':
        part = next(row for row in document['brief']['components'] if row['id'] == document['component_id'])
        result['component'] = dict(component_id=part['id'], engine=document['brief']['engine'], interface=part['interface'],
            source='export function draw(ctx, seconds, shared) {} // synthetic inert module')
    else:
        # Only a scheduling fixture. No contract acceptance, renderer or media is fabricated.
        result['source'] = dict(engine=document['brief']['engine'], files={'scene.html': '<canvas></canvas>',
            'scene.js': '// synthetic final source'},
            strategy=dict(description='Synthetic composition', tradeoffs='No visual validation.'))
    op.update(status='completed', result=result, result_sha256=digest(result))
    return op


def planned(run=None, *, plan=None):
    run = run_document() if run is None else run
    action = next_action(run, now=100)
    complete(append_reservation(run, action['operations'][0]), plan=plan)
    return run


def finish_parts(run):
    while True:
        action = next_action(run, now=100)
        if action['kind'] != 'reserve' or action['operations'][0]['phase'] == 'integrate':
            return action
        for reservation in action['operations']:
            complete(append_reservation(run, reservation))


def test_collaboration_is_explicit_and_one_round_budget_is_disclosed():
    assert validate_request(None, max_rounds=3) is None
    assert next_action(dict(frozen={}, operations=[]), now=100) == {'kind': 'disabled'}
    request = {'max_components': 3}
    assert validate_request(request, budget={'max_rounds': 1, 'max_seconds': 300}) == request
    assert required_creator_budget(request) == 5
    assert required_budget(request) == dict(creator=5, render=1, review=1)
    assert request == {'max_components': 3}


@pytest.mark.parametrize('configuration', [False, True, [], {'enabled': True}, {'max_parallel': 4},
    {'max_components': True}, {'max_components': 1}, {'max_components': 5}])
def test_invalid_configuration_is_not_silently_expanded(configuration):
    with pytest.raises(ServiceError):
        validate_request(configuration)


def test_multiple_rounds_and_undisclosed_extra_creator_cost_are_rejected():
    with pytest.raises(ServiceError, match='rounds_invalid'):
        validate_request({}, budget={'max_rounds': 2})
    with pytest.raises(ServiceError, match='budget_insufficient'):
        validate_request({}, budget=dict(creator=4, render=1, review=1))
    assert validate_request({'max_components': 2}, budget=dict(creator=4, render=1, review=1))


def test_phase_input_keeps_relevant_content_without_wiki_or_history_dump():
    context = base_document()
    context.update(knowledge_snapshot={'huge_history': 'x' * 200000},
                   research_records=[{'raw': 'y' * 200000}], operations=[{'large': 'z' * 200000}])
    context['methods'] = [dict(guidance='Preserve negative space.', database_wrapper='not a Skill')]
    context['constraints']['result_requirements'] = {'legacy_plan': True}
    before = deepcopy(context)
    value = operation_input(context, 'plan', config={'max_components': 3})
    assert value['schema'] == INPUT_SCHEMA
    assert value['constraints']['allowed_engines'] == ['html-svg', 'canvas', 'three']
    assert value['constraints']['collaboration'] == dict(max_components=3, max_parallel=2)
    assert value['methods'] == [{'guidance': 'Preserve negative space.'}]
    assert not {'knowledge_snapshot', 'research_records', 'operations'} & set(value)
    assert 'result_requirements' not in value['constraints']
    assert len(canonical_bytes(value)) < 64000
    assert context == before


@pytest.mark.parametrize('engines', [[], ['unknown'], ['canvas', 'canvas'], 'canvas'])
def test_phase_input_rejects_unknown_empty_or_ambiguous_capabilities(engines):
    context = base_document()
    context['constraints']['allowed_engines'] = engines
    with pytest.raises(ServiceError, match='engine_not_admitted'):
        operation_input(context, 'plan', config={})


def test_phase_input_does_not_truncate_large_request():
    context = base_document()
    context['feedback'] = 'x' * 8001
    with pytest.raises(ServiceError):
        operation_input(context, 'plan', config={})


@pytest.mark.parametrize('engine', ['html-svg', 'canvas', 'three'])
def test_each_admitted_capability_uses_the_same_dependency_and_final_delivery_flow(engine):
    context = base_document()
    context['constraints']['allowed_engines'] = [engine]
    run = run_document()
    descriptor = next_action(run, now=100)['operations'][0]
    plan = complete(append_reservation(run, descriptor, base=context), plan=plan_document(engine))
    assert plan['input']['constraints']['allowed_engines'] == [engine]
    parts = next_action(run, now=100)
    assert [part['component_id'] for part in parts['operations']] == ['mountains', 'foreground']
    assert progress_view(run)['stage'] == 'component'
    integration = finish_parts(run)
    complete(append_reservation(run, integration['operations'][0]))
    action = next_action(run, now=100)
    assert action['kind'] == 'render'
    assert action['input']['source']['engine'] == engine
    assert action['input']['document']['constraints']['allowed_engines'] == [engine]
    assert all(op['input']['constraints']['allowed_engines'] == [engine] for op in run['operations'])
    assert progress_view(run)['author_calls_used'] == 5


def test_plan_cannot_choose_a_capability_outside_the_original_admission():
    context = base_document()
    context['constraints']['allowed_engines'] = ['html-svg']
    run = run_document()
    descriptor = next_action(run, now=100)['operations'][0]
    complete(append_reservation(run, descriptor, base=context), plan=plan_document('three'))
    with pytest.raises(ServiceError, match='engine_not_admitted'):
        next_action(run, now=100)
    assert len(run['operations']) == 1


@pytest.mark.parametrize('phase', ['component', 'integrate'])
def test_component_and_final_composition_cannot_change_the_planned_capability(phase):
    run = planned(plan=plan_document('three'))
    descriptor = (next_action(run, now=100)['operations'][0] if phase == 'component'
                  else finish_parts(run)['operations'][0])
    op = complete(append_reservation(run, descriptor))
    field = 'component' if phase == 'component' else 'source'
    op['result'][field]['engine'] = 'html-svg'
    op['result_sha256'] = digest(op['result'])
    with pytest.raises(ServiceError, match='component_binding_invalid|final_source_required'):
        next_action(run, now=100)


def test_plan_reservation_is_read_only_and_reuses_original_deadline():
    run = run_document()
    before = deepcopy(run)
    first = next_action(run, now=100)
    assert first == next_action(run, now=101)
    assert run == before
    reservation = first['operations'][0]
    assert reservation['phase'] == 'plan' and reservation['deadline_at'] == run['deadline_at']
    assert reservation['idempotency_key'] == 'collaboration-plan'
    run['remaining']['creator'] = 4
    with pytest.raises(ServiceError, match='budget_insufficient'):
        next_action(run, now=100)


def test_unknown_submission_reconciles_original_operation_instead_of_reserving_replacement():
    run = run_document()
    op = append_reservation(run, next_action(run, now=100)['operations'][0])
    op['status'] = 'submission_unknown'
    assert next_action(run, now=100) == dict(kind='advance', operation_ids=[op['id']], cancel=False, reason=None)
    assert len(run['operations']) == 1 and progress_view(run)['author_calls_used'] == 1


def test_independent_parts_are_exposed_together_and_plan_criteria_are_frozen():
    run = planned()
    action = next_action(run, now=100)
    assert [row['component_id'] for row in action['operations']] == ['mountains', 'foreground']
    assert all(row['criteria'] == CRITERIA and row['deadline_at'] == 400 for row in action['operations'])
    for reservation in action['operations']:
        op = append_reservation(run, reservation)
        assert op['input']['criteria'] == CRITERIA
    assert next_action(run, now=100)['operation_ids'] == ['operation-1', 'operation-2']
    assert MAX_PARALLEL == 2


def test_free_parallel_slot_can_be_filled_without_resubmitting_existing_part():
    run = planned()
    reservations = next_action(run, now=100)['operations']
    first = append_reservation(run, reservations[0])
    first.update(status='submitted', job_id='original-worker-job')
    action = next_action(run, now=100)
    assert action['active_operation_ids'] == [first['id']]
    assert [row['component_id'] for row in action['operations']] == ['foreground']


def test_dependency_waits_for_all_direct_parts_then_gets_exact_sources_only():
    run = planned()
    reservations = next_action(run, now=100)['operations']
    first, second = [append_reservation(run, row) for row in reservations]
    complete(first)
    assert next_action(run, now=100)['operation_ids'] == [second['id']]
    complete(second)
    action = next_action(run, now=100)
    request = action['operations'][0]
    assert request['component_id'] == 'atmosphere'
    assert request['dependencies'] == [first['result']['component'], second['result']['component']]
    assert all('input' not in value and 'result' not in value for value in request['dependencies'])
    assert not any(op.get('stage') == 'render' for op in run['operations'])


@pytest.mark.parametrize('bad_plan', ['cycle', 'too_many'])
def test_invalid_plan_never_opens_paid_component_work(bad_plan):
    plan = plan_document()
    if bad_plan == 'cycle':
        plan['components'][0]['depends_on'] = ['atmosphere']
    else:
        plan['components'].append(dict(id='extra', responsibility='Extra', depends_on=[], interface='draw'))
    run = planned(plan=plan)
    with pytest.raises(ServiceError, match='dependency_cycle|plan_invalid'):
        next_action(run, now=100)
    assert len(run['operations']) == 1


def test_changed_dependency_or_plan_is_not_treated_as_same_component():
    run = planned()
    action = finish_parts(run)
    last = run['operations'][-1]
    last['input']['dependencies'][0]['source'] = '// replaced source'
    last['input_sha256'] = digest(last['input'])
    last['result']['input_sha256'] = last['input_sha256']
    last['result_sha256'] = digest(last['result'])
    with pytest.raises(ServiceError, match='dependency_changed'):
        next_action(run, now=100)
    assert action['operations'][0]['phase'] == 'integrate'


def test_only_final_integration_hands_one_source_to_normal_render_flow():
    run = planned()
    integration_action = finish_parts(run)
    request = integration_action['operations'][0]
    assert request['phase'] == 'integrate'
    assert len(request['dependencies']) == 3
    integrated = complete(append_reservation(run, request))
    action = next_action(run, now=100)
    assert action['kind'] == 'render' and action['stage'] == 'render'
    assert action['input']['author_operation_id'] == integrated['id']
    assert action['input']['document'] == integrated['input']
    assert action['input']['source'] == integrated['result']['source']
    assert progress_view(run)['author_calls_used'] == required_creator_budget({'max_components': 3})
    assert run['candidates'] == [] and run['reviews'] == []
    action['input']['source']['files'].clear()
    assert integrated['result']['source']['files']


def test_existing_final_render_is_handed_back_without_another_author_or_sample():
    run = planned()
    final = complete(append_reservation(run, finish_parts(run)['operations'][0]))
    action = next_action(run, now=100)
    run['operations'].append(dict(id='only-render', stage='render', status='submitted', input=action['input']))
    assert next_action(run, now=100) == dict(kind='complete', author_operation_id=final['id'],
                                          render_operation_id='only-render')
    assert progress_view(run)['stage'] == 'render'
    run['operations'][-1]['status'] = 'completed'
    assert progress_view(run)['stage'] == 'review'


@pytest.mark.parametrize('stopping', ['cancel', 'deadline'])
def test_cancel_and_deadline_drain_original_tasks_before_stop(stopping):
    run = planned()
    part = append_reservation(run, next_action(run, now=100)['operations'][0])
    part.update(status='submission_unknown', job_id=None)
    run['cancel_requested'] = stopping == 'cancel'
    action = next_action(run, now=400 if stopping == 'deadline' else 100)
    expected = 'cancelled' if stopping == 'cancel' else 'budget_exhausted'
    assert action == dict(kind='advance', operation_ids=[part['id']], cancel=True, reason=expected)
    part['status'] = 'cancelled'
    assert next_action(run, now=400 if stopping == 'deadline' else 100) == dict(kind='stop', reason=expected)
    assert run['deadline_at'] == 400


def test_one_failed_component_drains_sibling_before_failure_no_auto_retry():
    run = planned()
    first, sibling = [append_reservation(run, row) for row in next_action(run, now=100)['operations']]
    first['status'] = 'failed'
    sibling['status'] = 'submitted'
    assert next_action(run, now=100) == dict(kind='advance', operation_ids=[sibling['id']], cancel=True, reason='failed')
    sibling['status'] = 'cancelled'
    assert next_action(run, now=100) == dict(kind='stop', reason='failed')
    assert len(run['operations']) == 3


def test_changed_result_and_cross_owner_records_fail_without_new_work():
    run = planned()
    run['operations'][0]['result']['plan']['material'] = 'Changed after completion'
    with pytest.raises(ServiceError, match='result_changed'):
        next_action(run, now=100)
    run = planned()
    run['operations'][0]['owner'] = 'another-owner'
    with pytest.raises(ServiceError, match='scope_invalid'):
        next_action(run, now=100)


def test_terminal_run_with_active_creator_cannot_hide_unproven_stop():
    run = run_document()
    append_reservation(run, next_action(run, now=100)['operations'][0])
    run['state'] = 'failed'
    with pytest.raises(ServiceError, match='terminal_operation_active'):
        next_action(run, now=100)


def test_progress_reports_responsibilities_and_reserved_operation_budget_not_model_calls():
    run = planned()
    first = complete(append_reservation(run, next_action(run, now=100)['operations'][0]))
    view = progress_view(run)
    assert view['stage'] == 'component' and view['completed'] == 1 and view['total'] == 3
    assert view['components'][0] == dict(id='mountains', title='Mountain silhouettes and directional marks',
                                       depends_on=[], status='completed')
    assert view['components'][1]['status'] == 'ready' and view['components'][2]['status'] == 'waiting'
    assert view['author_calls_used'] == 2 and view['author_calls_max'] == 5
    assert 'not_provider_model_requests' in view['author_calls_scope']
    assert view['enabled'] is True and view['final_sample_limit'] == 1
    assert first['id'] in {op['id'] for op in run['operations']}


def test_original_store_keeps_claim_unknown_submission_and_stop_identity(tmp_path):
    now = [100.0]
    store = AnimationMethodCommissionStore(tmp_path / 'commissions.sqlite3', clock=lambda: now[0])
    commission = store.create('developer', dict(goal='Synthetic collaboration transport',
        budget=dict(creator=5, render=1, review=1, seconds=300)), 'single-request')
    document = operation_input(base_document(), 'plan', config={'max_components': 3})
    operation = store.reserve('developer', commission['id'], 'creator', document, 'collaboration-plan')
    assert store.claim('developer', operation['id'])['submission_claimed'] is True
    store.mark_unknown('developer', operation['id'])
    reopened = AnimationMethodCommissionStore(store.path, clock=lambda: now[0])
    assert reopened.claim('developer', operation['id'])['submission_claimed'] is False
    assert reopened.reserve('developer', commission['id'], 'creator', document,
                            'collaboration-plan')['id'] == operation['id']
    reopened.mark_submitted('developer', operation['id'], 'original-upstream-job')
    now[0] = commission['deadline_at']
    assert reopened.get('developer', commission['id'])['usage']['creator'] == 1
    reopened.fail('developer', operation['id'], 'confirmed_stopped', cancelled=True)
    final = reopened.stop('developer', commission['id'], 'cancelled', 'Original task stop verified by host.')
    assert final['deadline_at'] == commission['deadline_at'] and final['operations'][0]['job_id'] == 'original-upstream-job'
