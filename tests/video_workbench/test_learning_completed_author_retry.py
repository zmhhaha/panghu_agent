"""A stopped complete composition survives technical acceptance failures."""
from copy import deepcopy
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench.picture_learning_service import PictureLearningService
from video_workbench.video_learning_service import VideoLearningService
from test_learning_service_strategy import PlanningGateway
from test_learning_strategy import plan
from test_picture_learning_service import Gateway, Renderer, start, finish
from test_video_learning_service import (Gateway as VideoGateway, Renderer as VideoRenderer,
    start as video_start, real_mp4)


class VideoPlanningGateway(VideoGateway):
    def collect(self, job_id, sha, document):
        result = super().collect(job_id, sha, document)
        task = document['constraints'].get('learning_strategy_task', {})
        if task.get('phase') == 'plan':
            result['method']['learning_strategy'] = plan(probes=False)
        if document['mode'] == 'create':
            result['sound_events'] = [dict(time=.2, duration=.1, frequency=500,
                gain=.1, waveform='sine')]
        return result


def failed_composition(tmp_path, *, kind='picture', raw=None, probes=False):
    if kind == 'picture':
        author, reviewer = PlanningGateway(), Gateway(scores=[85])
        author.plan = plan(probes=probes)
        renderer = Renderer(tmp_path / 'renders')
        service = PictureLearningService(tmp_path / 'library', author_gateway=author,
            review_gateway=reviewer, renderer=renderer)
        project, _ = start(service, max_rounds=1)
    else:
        author, reviewer = VideoPlanningGateway(), VideoGateway()
        renderer = VideoRenderer(tmp_path / 'renders', raw)
        service = VideoLearningService(tmp_path / 'library', author_gateway=author,
            review_gateway=reviewer, renderer=renderer)
        project, _ = video_start(service, raw, max_rounds=1)
    validate = service._validate_learning_source

    def reject_completed_composition(run, source, **kwargs):
        if (run.get('learning_strategy') or {}).get('phase') == 'complete':
            raise ServiceError('test_current_source_selection_rejected', 422)
        return validate(run, source, **kwargs)

    service._validate_learning_source = reject_completed_composition
    failed = finish(service, project, limit=80)
    service._validate_learning_source = validate
    assert failed['state'] == 'failed'
    prior = service.store.read('experiments', 'developer', project['latest_experiment_id'])
    assert prior['stage'] == 'author' and prior['stop_proven'] and not prior['candidates']
    assert prior['learning_strategy_complete'] and prior['learning_strategy']['phase'] == 'complete'
    assert len(author.submissions) == 4 + int(probes) and len(renderer.calls) == int(probes)
    operation = service.store.read('operations', 'developer', prior['operation_ids'][-1])
    assert operation['status'] == 'completed' and operation['observation_receipt']
    assert operation['input']['constraints']['learning_strategy_task']['phase'] == 'compose'
    return service, author, reviewer, renderer, project, prior, operation


def restart(service, author, reviewer, renderer):
    return type(service)(service.root, author_gateway=author, review_gateway=reviewer, renderer=renderer)


def retry(service, project):
    return service.next_experiment('developer', project['id'], dict(request_id=uuid4().hex, mode='retry'))


@pytest.mark.parametrize('kind', ['picture', 'video'])
def test_real_service_reuses_completed_compose_once_across_restarts_and_lost_render(tmp_path, real_mp4, kind):
    service, author, reviewer, renderer, project, prior, operation = failed_composition(
        tmp_path, kind=kind, raw=real_mp4)
    original_rows = {identity: service.store.read('operations', 'developer', identity)
        for identity in prior['operation_ids']}
    request = dict(request_id=uuid4().hex, mode='retry')
    retried = service.next_experiment('developer', project['id'], request)
    assert service.next_experiment('developer', project['id'], request)['latest_experiment_id'] == retried['latest_experiment_id']
    eid = retried['latest_experiment_id']
    pending = service.store.read('experiments', 'developer', eid)
    assert pending['completed_author_retry']['operation'] == prior['learning_strategy']['composition_operation']
    assert pending['learning_strategy'] == prior['learning_strategy']
    assert pending['frozen'] == prior['frozen'] and not pending['operation_ids']
    service = restart(service, author, reviewer, renderer)
    service.advance('developer', eid)
    current = service.store.read('experiments', 'developer', eid)
    assert current['stage'] == 'render' and len(current['candidates']) == 1
    assert current['completed_author_retry']['consumed'] and not current['operation_ids']
    candidate = current['candidates'][0]
    assert candidate['source'] == operation['result']['source']
    assert candidate['author_operation_id'] == operation['id']
    assert candidate['reused_author'] == dict(source_experiment_id=prior['id'],
        source_operation_id=operation['id'], job_id=operation['job_id'],
        input_sha256=operation['input_sha256'], result_sha256=digest(operation['result']),
        observation_receipt_sha256=digest(operation['observation_receipt']), retry_experiment_id=eid)
    if kind == 'video':
        assert candidate['sound_events'] == operation['result']['sound_events']
    assert len(author.submissions) == 4 and not renderer.calls
    service = restart(service, author, reviewer, renderer)
    renderer.lose_result = True
    service.advance('developer', eid)
    assert len(renderer.calls) == 1
    renderer.lose_result = False
    service = restart(service, author, reviewer, renderer)
    service.advance('developer', eid)
    current = service.store.read('experiments', 'developer', eid)
    assert current['stage'] == 'review' and len(current['candidates']) == 1
    assert len(author.submissions) == 4 and len(renderer.calls) == 1
    completed = finish(service, retried, limit=80)
    assert completed['state'] == 'completed', completed['experiments'][-1].get('error')
    assert completed['experiments'][-1]['transfer_status'] == 'awaiting_approval'
    assert len(author.submissions) == 4 and len(renderer.calls) == 1
    from test_picture_learning_service import request_transfer
    completed = finish(service, request_transfer(service, completed), limit=80)
    assert len(author.submissions) == 5  # Only the legitimate changed-content author is new.
    assert len(renderer.calls) == 2 and len(completed['candidates']) == 2
    assert service.store.read('experiments', 'developer', prior['id']) == prior
    assert all(service.store.read('operations', 'developer', identity) == row
        for identity, row in original_rows.items())


@pytest.mark.parametrize('state,stop', [('running', False), ('completed', False), ('failed', True)])
def test_retry_rechecks_original_job_stop_before_accepting_or_rendering(tmp_path, state, stop):
    service, author, reviewer, renderer, project, prior, operation = failed_composition(tmp_path)
    retried = retry(service, project)
    author._job(operation['job_id']).update(state=state, stop_proven=stop)
    for _ in range(2):
        service = restart(service, author, reviewer, renderer)
        service.advance('developer', retried['latest_experiment_id'])
    current = service.store.read('experiments', 'developer', retried['latest_experiment_id'])
    assert current['state'] == 'submission_unknown' and not current['candidates']
    assert not current['completed_author_retry']['consumed']
    assert len(author.submissions) == 4 and not renderer.calls
    assert service.store.read('experiments', 'developer', prior['id']) == prior


@pytest.mark.parametrize('field', ['input', 'result', 'receipt', 'job', 'dependency'])
def test_changed_checkpoint_or_dependency_is_rejected_without_paid_work(tmp_path, field):
    service, author, _, renderer, project, prior, operation = failed_composition(tmp_path)
    retried = retry(service, project)
    if field == 'input':
        operation['input']['feedback'] += 'Changed frozen input'
    elif field == 'result':
        operation['result']['method']['technique'] += 'Changed result'
    elif field == 'receipt':
        operation['observation_receipt']['images'] = []
    elif field == 'job':
        operation['job_id'] = 'a-different-paid-job'
    else:
        component = prior['learning_strategy']['components'][0]['operation']['id']
        operation = service.store.read('operations', 'developer', component)
        operation['result']['method']['technique'] += 'Changed dependency'
    service.store.put('operations', operation)
    service.advance('developer', retried['latest_experiment_id'])
    current = service.store.read('experiments', 'developer', retried['latest_experiment_id'])
    assert current['state'] == 'failed' and not current['candidates']
    assert not current['completed_author_retry']['consumed']
    assert len(author.submissions) == 4 and not renderer.calls


def test_current_source_rejection_can_be_retried_without_reauthoring(tmp_path):
    service, author, _, renderer, project, prior, operation = failed_composition(tmp_path)
    retried = retry(service, project)
    validate = service._validate_learning_source
    checked = []

    def reject(run, source, **kwargs):
        checked.append(source['source_sha256'])
        raise ServiceError('test_still_not_allowed', 422)

    service._validate_learning_source = reject
    service.advance('developer', retried['latest_experiment_id'])
    current = service.store.read('experiments', 'developer', retried['latest_experiment_id'])
    assert current['state'] == 'failed' and not current['completed_author_retry']['consumed']
    assert checked == [operation['result']['source']['source_sha256']]
    assert len(author.submissions) == 4 and not renderer.calls
    service._validate_learning_source = validate
    retried = retry(service, project)
    service.advance('developer', retried['latest_experiment_id'])
    current = service.store.read('experiments', 'developer', retried['latest_experiment_id'])
    assert current['stage'] == 'render' and len(current['candidates']) == 1
    assert len(author.submissions) == 4 and not renderer.calls
    assert service.store.read('experiments', 'developer', prior['id']) == prior


def test_current_result_contract_is_revalidated_against_original_input(tmp_path, monkeypatch):
    service, author, _, renderer, project, _, operation = failed_composition(tmp_path)
    retried = retry(service, project)
    validated = []

    def reject(result, sha, document):
        validated.append((canonical_bytes(result), sha, canonical_bytes(document)))
        raise ServiceError('test_current_contract_rejects', 422)

    monkeypatch.setattr(service.protocol, 'validate_result', reject)
    service.advance('developer', retried['latest_experiment_id'])
    current = service.store.read('experiments', 'developer', retried['latest_experiment_id'])
    assert current['state'] == 'failed' and current['error']['code'] == 'test_current_contract_rejects'
    assert validated == [(canonical_bytes(operation['result']), operation['input_sha256'],
        canonical_bytes(operation['input']))]
    assert len(author.submissions) == 4 and not renderer.calls


def test_restart_before_acceptance_save_repeats_only_reads(tmp_path, monkeypatch):
    service, author, reviewer, renderer, project, _, _ = failed_composition(tmp_path)
    retried = retry(service, project)
    eid = retried['latest_experiment_id']
    save = service._save

    def crash_before_save(run):
        if run.get('completed_author_retry', {}).get('consumed'):
            raise KeyboardInterrupt('Process exited before candidate transaction')
        return save(run)

    monkeypatch.setattr(service, '_save', crash_before_save)
    with pytest.raises(KeyboardInterrupt):
        service.advance('developer', eid)
    persisted = service.store.read('experiments', 'developer', eid)
    assert not persisted['candidates'] and not persisted['completed_author_retry']['consumed']
    service = restart(service, author, reviewer, renderer)
    service.advance('developer', eid)
    persisted = service.store.read('experiments', 'developer', eid)
    assert persisted['stage'] == 'render' and len(persisted['candidates']) == 1
    assert len(author.submissions) == 4 and not renderer.calls


def test_matching_checkpoint_hash_does_not_replace_required_frame_transport(tmp_path):
    service, author, _, renderer, project, _, operation = failed_composition(tmp_path)
    # Pin this now-incomplete receipt at retry creation: hash equality alone
    # must never substitute for covering the original reference frame set.
    operation['observation_receipt']['images'] = []
    service.store.put('operations', operation)
    retried = retry(service, project)
    service.advance('developer', retried['latest_experiment_id'])
    current = service.store.read('experiments', 'developer', retried['latest_experiment_id'])
    assert current['state'] == 'failed'
    assert current['error']['code'] == 'learning_strategy_retry_frame_transport_incomplete'
    assert not current['candidates'] and len(author.submissions) == 4 and not renderer.calls


@pytest.mark.parametrize('role', ['author_operation', 'review_operation'])
def test_completed_probe_bindings_still_guard_source_and_review_evidence(tmp_path, role):
    service, author, _, renderer, project, prior, _ = failed_composition(tmp_path, probes=True)
    retried = retry(service, project)
    binding = prior['learning_strategy']['probes'][0][role]
    operation = service.store.read('operations', 'developer', binding['id'])
    if role == 'author_operation':
        operation['result']['method']['technique'] += 'Changed probe source'
    else:
        operation['result']['review']['summary'] += 'Changed probe observation'
    service.store.put('operations', operation)
    service.advance('developer', retried['latest_experiment_id'])
    current = service.store.read('experiments', 'developer', retried['latest_experiment_id'])
    assert current['state'] == 'failed' and not current['candidates']
    assert len(author.submissions) == 5 and len(renderer.calls) == 1


def test_post_compose_later_revision_never_reuses_the_initial_composition(tmp_path):
    service, _, _, _, _, prior, _ = failed_composition(tmp_path)
    later = deepcopy(prior)
    later.update(round=2)
    retry_run = service._new_experiment('developer', prior['project_id'], uuid4().hex,
        uuid4().hex, 'retry', deepcopy(prior['frozen']), prior['budget'], service.clock())
    service._learning_restore_retry(later, retry_run)
    assert 'completed_author_retry' not in retry_run
