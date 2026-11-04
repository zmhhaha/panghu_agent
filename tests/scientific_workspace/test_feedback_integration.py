"""Feedback integration across frozen inputs, job receipts and version views."""
from copy import deepcopy

from test_workspace import setup, action, create, planned, outlined, drafted, finish
from scientific_agent.source_quality import retrieval_summary


def test_confirmed_plan_reaches_outline_and_draft_frozen_context(setup):
    store, service, agent = setup
    project = planned(setup)
    expected = deepcopy(project['plan']['content'])
    project = action(store, project, 'confirm_plan')
    confirmation = project['confirmations']['plan']['id']
    project = finish(store, service, agent, project, 'retrieve')
    job = store.raw('alice', project['id'])['jobs'][-1]
    assert job['stage'] == 'outline'
    assert job['frozen_context']['confirmed_plan'] == dict(plan_id=project['plan']['id'],
        confirmation_id=confirmation, content=expected)
    project = finish(store, service, agent, project, 'outline')
    project = action(store, project, 'confirm_evidence')
    assert store.raw('alice', project['id'])['jobs'][-1]['frozen_context']['confirmed_plan']['content'] == expected


def test_version_receipt_matches_job_and_reconciliation_never_duplicates_it(setup):
    store, service, agent = setup
    project = drafted(setup)
    job = project['jobs'][-1]
    assert job['result_version_id'] == project['current_version_id']
    version = store.version('alice', project['id'], job['result_version_id'])
    assert version['task_id'] == job['id']
    assert version['writing_quality']['missing_requirements'] == []
    before = deepcopy(store.raw('alice', project['id'])['versions'])
    service.reconcile('alice', project['id'])
    assert store.raw('alice', project['id'])['versions'] == before


def test_stale_result_quality_uses_its_frozen_requirements_not_new_plan(setup):
    store, service, agent = setup
    project = action(store, outlined(setup), 'confirm_evidence')
    service.reconcile('alice', project['id'])
    job = store.raw('alice', project['id'])['jobs'][-1]
    agent.complete('alice', project['id'], job['id'])
    # New user requirements arrive after the original result has been written.
    project = store.get('alice', project['id'])
    store.patch('alice', project['id'], dict(expected_revision=project['revision'],
        requirements=dict(notes='全文最多10字，必须包含 fixed-case.py')), 'new-input')
    service.reconcile('alice', project['id'])
    saved = store.raw('alice', project['id'])['versions'][-1]
    assert saved['stale']
    assert saved['writing_quality'].get('target_length') is None
    assert saved['writing_quality']['missing_requirements'] == []


def test_current_query_coverage_is_repaired_without_erasing_failed_history():
    def batch(name, query, count):
        return dict(batch_id=name, papers=[], searches=[dict(source='arxiv', query=query,
            status='success', result_count=count)], observations=dict(observations=[
                dict(request=dict(tool='academic_search'))]))
    old = batch('first', 'AutoGen architecture workflow comparison', 0)
    new = batch('second', 'AutoGen', 10)
    project = dict(plan=dict(content=dict(search_queries=['AutoGen architecture workflow comparison']),
        source_policy=dict(max_search_calls=3)), search_batches=[old, new], selections=[])
    result = retrieval_summary(project)
    assert result['topics_without_evidence'] == []
    assert result['current_batch_id'] == 'second'
    assert result['cumulative_calls'] == 2 and result['calls_used'] == 1
    assert result['batch_count'] == 2 and len(result['batches']) == 2
    assert result['providers'][0]['result_count'] == 0
    assert old['searches'][0]['result_count'] == 0
