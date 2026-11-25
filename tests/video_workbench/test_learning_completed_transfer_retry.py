"""Paid changed-content sources survive a strict, stopped acceptance retry."""
from copy import deepcopy
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, digest
from test_learning_completed_author_retry import failed_composition, restart, retry
from test_picture_learning_service import finish, request_transfer
from test_video_learning_service import real_mp4


def failed_transfer(tmp_path, *, kind='picture', raw=None):
    service, author, reviewer, renderer, project, compose_prior, _ = failed_composition(
        tmp_path, kind=kind, raw=raw)
    project = retry(service, project)
    completed = finish(service, project, limit=80)
    assert completed['experiments'][-1]['transfer_status'] == 'awaiting_approval'
    project = request_transfer(service, completed, completed['best_candidate_id'])
    validate = service._validate_learning_source

    def reject_transfer(run, source, **kwargs):
        if run['work_kind'] == 'transfer':
            raise ServiceError('test_transfer_source_gate_rejected', 422)
        return validate(run, source, **kwargs)

    service._validate_learning_source = reject_transfer
    failed = finish(service, project, limit=80)
    service._validate_learning_source = validate
    prior = service.store.read('experiments', 'developer', failed['latest_experiment_id'])
    assert prior['state'] == 'failed' and prior['stage'] == 'author' and prior['stop_proven']
    assert prior['work_kind'] == 'transfer' and 'completed_author_retry' not in prior
    assert prior['candidates'] == []
    approved = service._approved_transfer_candidate(prior)
    assert approved['status'] == 'reviewed' and prior['inherited_candidate_ids'] == [approved['id']]
    operation = service.store.read('operations', 'developer', prior['operation_ids'][-1])
    assert operation['status'] == 'completed' and operation['input']['work_kind'] == 'transfer'
    assert 'learning_strategy_task' not in operation['input']['constraints']
    assert len(author.submissions) == 5 and len(renderer.calls) == 1
    return service, author, reviewer, renderer, project, compose_prior, prior, operation


@pytest.mark.parametrize('kind', ['picture', 'video'])
def test_compose_recovery_then_transfer_recovery_preserves_paid_sources_and_reviewed_reproduction(
        tmp_path, real_mp4, kind):
    service, author, reviewer, renderer, project, compose_prior, prior, operation = failed_transfer(
        tmp_path, kind=kind, raw=real_mp4)
    originals = {row: service.store.read('operations', 'developer', row)
        for row in compose_prior['operation_ids'] + prior['operation_ids']}
    retried = retry(service, project)
    eid = retried['latest_experiment_id']
    pending = service.store.read('experiments', 'developer', eid)
    assert pending['completed_author_retry']['work_kind'] == 'transfer'
    assert pending['completed_author_retry']['operation']['id'] == operation['id']
    assert pending['completed_author_retry']['operation']['id'] not in compose_prior['operation_ids']
    assert pending['frozen'] == prior['frozen'] and pending['budget'] == prior['budget']
    assert pending['transfer_task'] == prior['transfer_task']
    assert pending['transfer_method'] == prior['transfer_method']
    assert pending['inherited_candidate_ids'] == prior['inherited_candidate_ids']
    assert not pending['candidates'] and not pending['operation_ids']
    service = restart(service, author, reviewer, renderer)
    service.advance('developer', eid)
    current = service.store.read('experiments', 'developer', eid)
    assert current['stage'] == 'render' and current['completed_author_retry']['consumed']
    assert len(current['candidates']) == 1 and current['candidates'][0]['work_kind'] == 'transfer'
    assert current['candidates'][0]['source'] == operation['result']['source']
    assert current['candidates'][0]['reused_author']['input_sha256'] == operation['input_sha256']
    assert len(author.submissions) == 5 and len(renderer.calls) == 1
    assert len(reviewer.submissions) == 2  # Reproduction review and approved planning.
    completed = finish(restart(service, author, reviewer, renderer), retried, limit=80)
    assert completed['state'] == 'completed', completed['experiments'][-1].get('error')
    assert completed['experiments'][-1]['transfer_status'] == 'reviewed'
    assert len(completed['candidates']) == 2 and len(renderer.calls) == 2
    assert len(author.submissions) == 5 and len(reviewer.submissions) == 3
    assert service.store.read('experiments', 'developer', compose_prior['id']) == compose_prior
    assert service.store.read('experiments', 'developer', prior['id']) == prior
    assert all(service.store.read('operations', 'developer', identity) == row
        for identity, row in originals.items())


@pytest.mark.parametrize('field', ['goal', 'criteria', 'method', 'knowledge', 'input',
    'result', 'receipt', 'job', 'reproduction', 'review', 'render'])
def test_transfer_checkpoint_changes_cannot_trigger_an_author_or_render(tmp_path, field):
    service, author, _, renderer, project, _, prior, operation = failed_transfer(tmp_path)
    retried = retry(service, project)
    current = service.store.read('experiments', 'developer', retried['latest_experiment_id'])
    if field == 'goal':
        current['transfer_task']['goal'] += ' Changed objective.'
    elif field == 'criteria':
        current['transfer_task']['criteria'].append('Changed criterion.')
    elif field == 'method':
        current['transfer_method']['technique'] += ' Changed method.'
    elif field == 'knowledge':
        operation['input']['knowledge_snapshot'] = {'unfrozen': True}
        operation['input_sha256'] = digest(operation['input'])
        operation['result']['input_sha256'] = operation['input_sha256']
    elif field == 'input':
        operation['input']['feedback'] += ' Altered input.'
    elif field == 'result':
        operation['result']['method']['technique'] += ' Altered output.'
    elif field == 'receipt':
        operation['observation_receipt']['unfrozen'] = True
    elif field == 'job':
        operation['job_id'] = 'different-paid-job'
    elif field == 'reproduction':
        approved = service._approved_transfer_candidate(prior)
        origin = service.store.read('experiments', 'developer', prior['frozen']['transfer_approval']['source_experiment_id'])
        selected = next(row for row in origin['candidates'] if row['id'] == approved['id'])
        selected['method']['technique'] += ' Changed lineage.'
        service.store.put('experiments', origin)
    elif field == 'review':
        operation = service.store.read('operations', 'developer', service._approved_transfer_candidate(prior)['review_operation_id'])
        operation['result']['review']['summary'] += ' Changed review.'
    elif field == 'render':
        operation = service.store.read('operations', 'developer', service._approved_transfer_candidate(prior)['render_operation_id'])
        operation['result']['sha256'] = '0' * 64
    service.store.put('experiments', current)
    service.store.put('operations', operation)
    service.advance('developer', current['id'])
    current = service.store.read('experiments', 'developer', current['id'])
    assert current['state'] == 'failed' and not current['completed_author_retry']['consumed']
    assert not current['candidates'] and len(author.submissions) == 5 and len(renderer.calls) == 1


@pytest.mark.parametrize('state,stopped', [('running', False), ('completed', False), ('failed', True)])
def test_transfer_requires_original_author_completion_and_stop_proof(tmp_path, state, stopped):
    service, author, reviewer, renderer, project, _, _, operation = failed_transfer(tmp_path)
    retried = retry(service, project)
    author._job(operation['job_id']).update(state=state, stop_proven=stopped)
    for _ in range(2):
        service = restart(service, author, reviewer, renderer)
        service.advance('developer', retried['latest_experiment_id'])
    current = service.store.read('experiments', 'developer', retried['latest_experiment_id'])
    assert current['state'] == 'submission_unknown' and not current['candidates']
    assert not current['completed_author_retry']['consumed']
    assert len(author.submissions) == 5 and len(renderer.calls) == 1


def test_rejected_transfer_can_retry_again_without_reusing_consumed_compose(tmp_path):
    service, author, _, renderer, project, _, prior, operation = failed_transfer(tmp_path)
    retried = retry(service, project)
    validate = service._validate_learning_source

    def reject(run, source, **kwargs):
        raise ServiceError('test_still_rejected', 422)

    service._validate_learning_source = reject
    service.advance('developer', retried['latest_experiment_id'])
    first_retry = service.store.read('experiments', 'developer', retried['latest_experiment_id'])
    assert first_retry['state'] == 'failed' and not first_retry['operation_ids']
    assert not first_retry['completed_author_retry']['consumed']
    service._validate_learning_source = validate
    retried = retry(service, project)
    service.advance('developer', retried['latest_experiment_id'])
    recovered = service.store.read('experiments', 'developer', retried['latest_experiment_id'])
    assert recovered['stage'] == 'render' and len(recovered['candidates']) == 1
    assert recovered['candidates'][0]['source'] == operation['result']['source']
    assert recovered['completed_author_retry']['experiment_id'] == prior['id']
    assert len(author.submissions) == 5 and len(renderer.calls) == 1


def test_transfer_restart_before_atomic_acceptance_repeats_only_reads(tmp_path, monkeypatch):
    service, author, reviewer, renderer, project, _, _, _ = failed_transfer(tmp_path)
    retried = retry(service, project)
    eid = retried['latest_experiment_id']
    save = service._save

    def interrupted(run):
        if run.get('completed_author_retry', {}).get('consumed'):
            raise KeyboardInterrupt('Exit before atomic candidate save')
        return save(run)

    monkeypatch.setattr(service, '_save', interrupted)
    with pytest.raises(KeyboardInterrupt):
        service.advance('developer', eid)
    pending = service.store.read('experiments', 'developer', eid)
    assert not pending['candidates'] and not pending['completed_author_retry']['consumed']
    service = restart(service, author, reviewer, renderer)
    service.advance('developer', eid)
    recovered = service.store.read('experiments', 'developer', eid)
    assert recovered['stage'] == 'render' and len(recovered['candidates']) == 1
    assert len(author.submissions) == 5 and len(renderer.calls) == 1


def test_matching_forged_transfer_input_hash_is_not_enough(tmp_path):
    service, author, _, renderer, project, _, prior, operation = failed_transfer(tmp_path)
    operation['input']['goal'] += ' New unrelated goal.'
    operation['input_sha256'] = digest(operation['input'])
    operation['result']['input_sha256'] = operation['input_sha256']
    service.store.put('operations', operation)
    with pytest.raises(ServiceError, match='learning_strategy_retry_transfer_input_changed'):
        retry(service, project)
    assert service.store.read('projects', 'developer', project['id'])['latest_experiment_id'] == prior['id']
    assert len(author.submissions) == 5 and len(renderer.calls) == 1


@pytest.mark.parametrize('change', ['round', 'nonfinal', 'resource', 'probe', 'transfer_candidate'])
def test_arbitrary_completed_phases_are_not_transfer_recovery_checkpoints(tmp_path, change):
    service, _, _, _, _, _, prior, _ = failed_transfer(tmp_path)
    prior = deepcopy(prior)
    if change == 'round':
        prior['round'] = 2
    elif change == 'nonfinal':
        prior['stage'] = 'review'
    elif change == 'resource':
        prior['resource_plan'] = {'kind': 'manim'}
    elif change == 'probe':
        prior['learning_strategy'] = {'phase':'probe_author'}
    else:
        prior['candidates'].append(dict(work_kind='transfer'))
    run = service._new_experiment('developer', prior['project_id'], uuid4().hex,
        uuid4().hex, 'retry', deepcopy(prior['frozen']), deepcopy(prior['budget']), service.clock())
    run.update(work_kind='transfer', transfer_task=deepcopy(prior['transfer_task']),
        transfer_method=deepcopy(prior['transfer_method']), transfer_status='running')
    service._learning_restore_retry(prior, run)
    assert 'completed_author_retry' not in run
