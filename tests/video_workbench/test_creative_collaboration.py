"""Reusable creative scheduling with an editorial task and no media contract."""
from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench import creative_collaboration as core


POLICY = core.CollaborationPolicy('editorial.collaboration-input.v1')
CRITERIA = ['Claims are supported; the argument has one clear conclusion.']
PLAN = dict(components=[
    dict(id='evidence', responsibility='Establish the primary evidence.', depends_on=[], interface='text'),
    dict(id='counterpoint', responsibility='Write the strongest counterpoint.', depends_on=[], interface='text'),
    dict(id='synthesis', responsibility='Resolve the two arguments.',
         depends_on=['evidence', 'counterpoint'], interface='text'),
])


def run_document():
    return dict(id='editorial-commission', owner='editor', state='running', cancel_requested=False,
        frozen={'collaboration': {'max_components': 3}}, budget={'creator': 5},
        remaining={'creator': 5}, deadline_at=300, operations=[])


def reserve(run, descriptor):
    document = dict(schema=POLICY.input_schema, phase=descriptor['phase'],
        component_id=descriptor['component_id'], brief=deepcopy(descriptor['brief']),
        criteria=deepcopy(descriptor['criteria']), dependencies=deepcopy(descriptor['dependencies']))
    op = dict(id='original-' + str(len(run['operations'])), commission_id=run['id'], owner=run['owner'],
        stage='creator', status='reserved', input=document, input_sha256=digest(document))
    run['operations'].append(op)
    run['remaining']['creator'] -= 1
    return op


def complete(op):
    phase = op['input']['phase']
    result = dict(phase=phase, input_sha256=digest(op['input']), criteria=CRITERIA)
    if phase == 'plan':
        result['plan'] = deepcopy(PLAN)
    elif phase == 'component':
        result['component'] = dict(component_id=op['input']['component_id'], interface='text',
                                   text='Original prose for ' + op['input']['component_id'])
    else:
        result['manuscript'] = 'A coherent editorial with a supported conclusion.'
    op.update(status='completed', result=result, result_sha256=digest(result))
    return op


def next_action(run, **kwargs):
    return core.next_action(run, policy=POLICY, now=100, **kwargs)


def planned():
    run = run_document()
    complete(reserve(run, next_action(run)['operations'][0]))
    return run


def test_editorial_dag_delivers_arbitrary_content_without_a_domain_or_delivery_budget():
    run = planned()
    first = next_action(run)
    assert [item['component_id'] for item in first['operations']] == ['evidence', 'counterpoint']
    evidence, counterpoint = [reserve(run, item) for item in first['operations']]
    complete(evidence)
    counterpoint['status'] = 'submission_unknown'
    assert next_action(run)['operation_ids'] == [counterpoint['id']]
    complete(counterpoint)
    synthesis_request = next_action(run)['operations'][0]
    assert synthesis_request['dependencies'] == [evidence['result']['component'], counterpoint['result']['component']]
    assert synthesis_request['deadline_at'] == 300
    complete(reserve(run, synthesis_request))
    final = complete(reserve(run, next_action(run)['operations'][0]))
    before = deepcopy(run)
    delivery = next_action(run)
    assert delivery['kind'] == 'deliver'
    assert delivery['author_operation_id'] == final['id']
    assert delivery['input'] == final['input'] and delivery['result'] == final['result']
    delivery['result']['manuscript'] = 'Changed by a consumer.'
    assert run == before
    view = core.progress_view(run, policy=POLICY)
    assert view['stage'] == 'deliver' and view['author_calls_used'] == 5
    assert run['remaining']['creator'] == 0


def test_domain_validator_can_prevent_new_dependent_work():
    run = planned()
    def validate_plan(plan, document):
        assert plan == PLAN and document['schema'] == POLICY.input_schema
        raise ServiceError('editorial_claims_unsupported', 409)
    with pytest.raises(ServiceError, match='editorial_claims_unsupported'):
        next_action(run, validate_plan=validate_plan)
    assert len(run['operations']) == 1


@pytest.mark.parametrize('reason', ['cancel', 'failure', 'deadline'])
def test_generic_stopping_drains_original_unknown_submission_without_replacement(reason):
    run = planned()
    first, sibling = [reserve(run, item) for item in next_action(run)['operations']]
    first['status'] = 'failed' if reason == 'failure' else 'completed'
    if first['status'] == 'completed':
        complete(first)
    sibling['status'] = 'submission_unknown'
    run['cancel_requested'] = reason == 'cancel'
    now = 300 if reason == 'deadline' else 100
    expected = {'cancel': 'cancelled', 'failure': 'failed', 'deadline': 'budget_exhausted'}[reason]
    assert core.next_action(run, policy=POLICY, now=now) == dict(kind='advance',
        operation_ids=[sibling['id']], cancel=True, reason=expected)
    sibling['status'] = 'cancelled'
    assert core.next_action(run, policy=POLICY, now=now) == dict(kind='stop', reason=expected)
    assert len(run['operations']) == 3 and run['remaining']['creator'] == 2
