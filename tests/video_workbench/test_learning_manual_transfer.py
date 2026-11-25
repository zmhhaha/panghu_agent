"""Changed-content work requires an explicit approval of actual rendered work."""
from copy import deepcopy
import json
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from test_picture_learning_service import system, start, finish, request_transfer


@pytest.mark.parametrize('score', [0, 60, 100])
def test_default_reproduction_never_schedules_transfer_even_when_review_proposes_it(system, score):
    service, author, reviewer, renderer = system
    reviewer.scores = [score]
    project, _ = start(service, max_rounds=1, max_seconds=3600)
    assert project['experiments'][0]['transfer_status'] == 'not_requested'
    completed = finish(service, project)
    run = completed['experiments'][0]
    assert run['state'] == 'completed' and run['stop_reason'] == 'reproduction_complete'
    assert run['transfer_status'] == 'awaiting_approval'
    assert run['frozen']['transfer_policy'] == run['transfer_policy'] == 'manual'
    assert [d['mode'] for d, _ in reviewer.uploads] == ['review']
    assert all(d['work_kind'] == 'reproduction' for d, _ in author.uploads)
    assert len(renderer.calls) == 1
    assert service.catalogue('developer')['capabilities']['transfer'] == dict(automatic=False,
        requires_user_approval=True, experiment_mode='transfer', default_max_rounds=1,
        default_max_seconds=3600)
    counts = len(author.submissions), len(reviewer.submissions), len(renderer.calls)
    for _ in range(3):
        service.advance('developer', run['id'])
    assert counts == (len(author.submissions), len(reviewer.submissions), len(renderer.calls))


@pytest.mark.parametrize('approval', [None, False, 'true', 1, 0, {}])
def test_transfer_requires_literal_user_approval_without_any_paid_or_durable_submission(system, approval):
    service, author, reviewer, renderer = system
    project, _ = start(service, max_rounds=1)
    completed = finish(service, project)
    before = deepcopy(completed)
    body = dict(request_id=uuid4().hex, mode='transfer',
        base_candidate_id=completed['candidates'][0]['id'])
    if approval is not None:
        body['approve_candidate'] = approval
    counts = len(author.submissions), len(reviewer.submissions), len(renderer.calls)
    with pytest.raises(ServiceError, match='transfer_approval_required'):
        service.next_experiment('developer', completed['id'], body)
    assert service.get('developer', completed['id']) == before
    assert service.store.request('developer', 'experiment:'+completed['id'], body['request_id'], body) is None
    assert counts == (len(author.submissions), len(reviewer.submissions), len(renderer.calls))


def test_transfer_can_use_a_user_approved_render_without_an_agent_review_or_score(system):
    service, author, reviewer, _ = system
    project, _ = start(service, max_rounds=1)
    for _ in range(3):
        service.advance('developer', project['latest_experiment_id'])
    detail = service.get('developer', project['id'])
    assert detail['candidates'][0]['status'] == 'rendered'
    assert detail['candidates'][0]['review'] is None
    service.stop('developer', project['id'])
    stopped = service.advance('developer', project['latest_experiment_id'])
    assert stopped['state'] == 'cancelled'
    assert reviewer.submissions == []
    approved = request_transfer(service, stopped)
    run = approved['experiments'][-1]
    assert run['work_kind'] == 'transfer' and run['stage'] == 'transfer_plan'
    assert run['budget'] == dict(max_rounds=1, max_seconds=3600)
    assert run['frozen']['transfer_approval']['approve_candidate'] is True
    assert run['frozen']['allow_probes'] is run['frozen']['allow_collaboration'] is False
    assert len(approved['candidates']) == 1 and run['candidates'] == []
    assert approved['adopted_candidate_id'] is None and approved['published'] is False
    done = finish(service, approved)
    assert done['experiments'][-1]['transfer_status'] == 'reviewed'
    assert done['candidates'][-1]['work_kind'] == 'transfer'


def test_planner_and_creator_receive_only_the_approved_effect_and_general_method(system):
    service, author, reviewer, _ = system
    original_collect = author.collect

    def collect(job, checksum, document):
        result = original_collect(job, checksum, document)
        if document['work_kind'] == 'reproduction':
            result['method'].update(source={'private-source':True}, world={'private-world':True},
                plan={'private-plan':True}, layout={'private-layout':True},
                references=['private-reference'], reference_ids=['private-id'],
                reference_sources=[{'url':'https://private-reference.invalid'}],
                observed_facts=['Original photo-specific description.'], evidence_frame_ids=['reference-1'])
        return result

    author.collect = collect
    project, _ = start(service, max_rounds=1, goal='ORIGINAL SUBJECT AND LAYOUT',
        criteria=['ORIGINAL COMPOSITION'], use_wikiskill=False)
    completed = finish(service, project)
    original_candidates = deepcopy(completed['candidates'])
    manual = request_transfer(service, completed)
    run = service.store.read('experiments', 'developer', manual['latest_experiment_id'])
    planner = service._document(run, 'transfer_plan')
    assert {f['purpose'] for f in planner['observation']['frames']} == {'output'}
    assert planner['observation']['frames'][0]['sha256'] == run['frozen']['transfer_approval']['media_sha256']
    assert planner['base_source'] is planner['prior_review'] is planner['knowledge_snapshot'] is None
    assert planner['feedback'] == '' and planner['knowledge_binding']['use_wikiskill'] is False
    for key in ('source', 'world', 'plan', 'layout', 'references', 'reference_ids',
                'reference_sources', 'observed_facts', 'evidence_frame_ids'):
        assert key not in planner['learned_method']
    assert 'ORIGINAL SUBJECT' not in json.dumps(planner)
    done = finish(service, manual)
    creator = next(d for d, _ in author.uploads if d['work_kind'] == 'transfer')
    assert creator['observation']['frames'] == []
    assert creator['base_source'] is creator['prior_review'] is creator['knowledge_snapshot'] is None
    assert creator['feedback'] == '' and creator['learned_method'] == planner['learned_method']
    assert done['candidates'][:len(original_candidates)] == original_candidates
    assert done['experiments'][-1]['budget'] == dict(max_rounds=1, max_seconds=3600)


def test_transfer_request_is_idempotent_and_cannot_change_approved_identity(system):
    service, author, reviewer, renderer = system
    project, _ = start(service)
    completed = finish(service, project)
    body = dict(request_id=uuid4().hex, mode='transfer', approve_candidate=True,
        base_candidate_id=completed['candidates'][0]['id'])
    first = service.next_experiment('developer', completed['id'], body)
    second = service.next_experiment('developer', completed['id'], body)
    assert first == second
    with pytest.raises(ServiceError, match='idempotency_conflict'):
        service.next_experiment('developer', completed['id'],
            dict(body, base_candidate_id=completed['candidates'][1]['id']))
    assert len(first['experiments']) == 2 and first['experiments'][-1]['candidates'] == []


@pytest.mark.parametrize('mutation', ['cross_project', 'cross_owner', 'source_only', 'method_missing', 'transfer_work', 'rounds'])
def test_invalid_transfer_source_does_not_start_an_experiment(system, mutation):
    service, author, reviewer, renderer = system
    if mutation == 'source_only':
        renderer.fail = True
    project, _ = start(service, max_rounds=1)
    completed = finish(service, project)
    candidate = completed['candidates'][0]
    selected = candidate['id']
    owner = 'developer'
    if mutation == 'cross_project':
        another, _ = start(service, max_rounds=1)
        another = finish(service, another)
        selected = another['candidates'][0]['id']
    elif mutation == 'cross_owner':
        owner = 'other-owner'
    elif mutation in {'method_missing', 'transfer_work'}:
        run = service.store.read('experiments', 'developer', completed['latest_experiment_id'])
        if mutation == 'method_missing':
            run['candidates'][0]['method'] = None
        else:
            run['candidates'][0]['work_kind'] = 'transfer'
        service.store.put('experiments', run)
    before = service.get('developer', completed['id'])
    counts = len(author.submissions), len(reviewer.submissions), len(renderer.calls)
    body = dict(request_id=uuid4().hex, mode='transfer', base_candidate_id=selected,
        approve_candidate=True, max_rounds=2 if mutation == 'rounds' else 1)
    with pytest.raises(ServiceError):
        service.next_experiment(owner, completed['id'], body)
    assert service.get('developer', completed['id']) == before
    assert counts == (len(author.submissions), len(reviewer.submissions), len(renderer.calls))


def test_manual_planner_retry_preserves_the_approval_and_exact_failed_input(system):
    service, author, reviewer, _ = system
    project, _ = start(service, max_rounds=1)
    completed = finish(service, project)
    manual = request_transfer(service, completed)
    reviewer.receipt_missing = True
    failed = finish(service, manual)
    prior = failed['experiments'][-1]
    assert prior['state'] == 'failed' and prior['stage'] == 'transfer_plan'
    original = service.inspect('developer', failed['id'], prior['id'])['operations'][-1]
    reviewer.receipt_missing = False
    retried = service.next_experiment('developer', failed['id'], dict(request_id=uuid4().hex, mode='retry'))
    current = retried['experiments'][-1]
    assert current['stage'] == 'transfer_plan' and current['candidates'] == []
    assert current['frozen'] == prior['frozen']
    service.advance('developer', current['id'])
    replay = reviewer.uploads[-1][0]
    expected = deepcopy(original['input']);expected['experiment_id'] = current['id']
    assert replay == expected
    assert finish(service, retried)['experiments'][-1]['transfer_status'] == 'reviewed'


def test_user_approved_unreviewed_output_keeps_paid_transfer_author_on_retry(system):
    service, author, _, _ = system
    project, _ = start(service, max_rounds=1)
    for _ in range(3):
        service.advance('developer', project['latest_experiment_id'])
    service.stop('developer', project['id'])
    stopped = service.advance('developer', project['latest_experiment_id'])
    assert stopped['candidates'][0]['status'] == 'rendered'
    assert stopped['candidates'][0]['review'] is None
    manual = request_transfer(service, stopped)
    validate = service._validate_learning_source

    def reject_transfer(run, source, **kwargs):
        if run['work_kind'] == 'transfer':
            raise ServiceError('test_user_approved_transfer_source_rejected', 422)
        return validate(run, source, **kwargs)

    service._validate_learning_source = reject_transfer
    failed = finish(service, manual)
    assert failed['state'] == 'failed' and failed['stage'] == 'author'
    count = len(author.submissions)
    service._validate_learning_source = validate
    retried = service.next_experiment('developer', failed['id'], dict(request_id=uuid4().hex, mode='retry'))
    run = service.store.read('experiments', 'developer', retried['latest_experiment_id'])
    assert run['completed_author_retry']['reproduction']['review_operation'] is None
    completed = finish(service, retried)
    assert completed['experiments'][-1]['transfer_status'] == 'reviewed'
    assert len(author.submissions) == count


def test_unapproved_legacy_transfer_retry_is_rejected_without_changing_history(system):
    service, author, reviewer, _ = system
    project, _ = start(service, max_rounds=1)
    completed = finish(service, project)
    raw = service.store.read('experiments', 'developer', completed['latest_experiment_id'])
    raw.update(state='failed', stage='author', work_kind='transfer', transfer_status='running', stop_proven=True)
    raw['frozen'].pop('transfer_policy', None)
    raw['frozen_sha256'] = digest(raw['frozen'])
    with service.store.db() as db:
        db.execute('UPDATE picture_experiments SET document=? WHERE owner=? AND id=?',
            (canonical_bytes(raw).decode(), 'developer', raw['id']))
    before = service.get('developer', completed['id'])
    count = len(author.submissions) + len(reviewer.submissions)
    with pytest.raises(ServiceError, match='transfer_retry_requires_approval'):
        service.next_experiment('developer', completed['id'], dict(request_id=uuid4().hex, mode='retry'))
    assert service.get('developer', completed['id']) == before
    assert len(author.submissions) + len(reviewer.submissions) == count


def test_legacy_reproduction_retry_keeps_frozen_input_and_stops_before_transfer(system):
    service, author, reviewer, renderer = system
    renderer.fail = True
    project, _ = start(service, max_rounds=1, max_seconds=3600)
    failed = finish(service, project)
    original = service.store.read('experiments', 'developer', failed['latest_experiment_id'])
    original['frozen'].pop('transfer_policy', None)
    original['frozen_sha256'] = digest(original['frozen'])
    operation = service.store.read('operations', 'developer', original['operation_ids'][-1])
    operation['input']['constraints'].pop('transfer_policy', None)
    operation['input_sha256'] = digest(operation['input'])
    with service.store.db() as db:
        db.execute('UPDATE picture_experiments SET document=? WHERE owner=? AND id=?',
            (canonical_bytes(original).decode(), 'developer', original['id']))
        db.execute('UPDATE picture_operations SET document=? WHERE owner=? AND id=?',
            (canonical_bytes(operation).decode(), 'developer', operation['id']))
    count = len(author.submissions)
    renderer.fail = False
    retried = service.next_experiment('developer', failed['id'], dict(request_id=uuid4().hex, mode='retry'))
    current = retried['experiments'][-1]
    assert current['frozen_sha256'] == original['frozen_sha256']
    assert 'transfer_policy' not in current['frozen'] and current['transfer_policy'] == 'manual'
    completed = finish(service, retried)
    run = completed['experiments'][-1]
    assert run['stop_reason'] == 'reproduction_complete' and run['transfer_status'] == 'awaiting_approval'
    assert len(author.submissions) == count
    assert all(d['mode'] != 'transfer_plan' for d, _ in reviewer.uploads)
    stored = service.store.read('experiments', 'developer', original['id'])
    assert stored == original
