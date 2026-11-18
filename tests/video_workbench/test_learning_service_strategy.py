"""Adaptive studies go through the durable service, not a simulated scheduler."""
from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError
from video_workbench.picture_learning_service import PictureLearningService
from test_picture_learning_service import Gateway, Renderer, start, finish
from test_learning_strategy import plan


class PlanningGateway(Gateway):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.plan = plan()

    def collect(self, job_id, sha, document):
        result = super().collect(job_id, sha, document)
        task = document['constraints'].get('learning_strategy_task', {})
        if document['mode'] == 'create' and task.get('phase') == 'plan':
            result['method']['learning_strategy'] = deepcopy(self.plan)
        return result


def system(tmp_path):
    author, reviewer, renderer = PlanningGateway(), Gateway(scores=[85]), Renderer(tmp_path / 'renders')
    service = PictureLearningService(tmp_path / 'library', author_gateway=author,
        review_gateway=reviewer, renderer=renderer)
    return service, author, reviewer, renderer


def advance_to(service, project, *, stage=None, phase=None):
    for _ in range(40):
        run = service.store.read('experiments', 'developer', project['latest_experiment_id'])
        if (stage is None or run['stage'] == stage) and (
                phase is None or (run.get('learning_strategy') or {}).get('phase') == phase):
            return run
        assert run['state'] not in {'failed', 'completed', 'cancelled'}, run.get('error')
        service.advance('developer', run['id'])
    raise AssertionError('stage was not reached')


def test_actual_service_runs_probe_review_dependencies_and_complete_transfer(tmp_path):
    service, author, reviewer, renderer = system(tmp_path)
    project, _ = start(service, max_rounds=1)
    frozen = service.inspect('developer', project['id'], project['latest_experiment_id'])['frozen']
    result = finish(service, project, limit=80)
    assert result['state'] == 'completed' and result['experiments'][-1]['transfer_status'] == 'reviewed'
    assert len(result['candidates']) == 2  # Internal study is not a library work.
    public = result['experiments'][-1]
    assert public['learning_progress']['complete']
    assert len(public['internal_attempts']) == 1
    study = public['internal_attempts'][0]
    assert study['status'] == 'reviewed' and study['review_operation_id']
    assert len(renderer.calls) == 3 and len(reviewer.submissions) == 3
    phases = [doc['constraints'].get('learning_strategy_task', {}).get('phase') for doc, _ in author.uploads]
    assert phases == ['plan', 'probe_author', 'component', 'component', 'compose', None]
    composition = author.uploads[4][0]
    task = composition['constraints']['learning_strategy_task']
    assert len(task['dependencies']) == 2 and task['probes'][0]['review']
    assert any(frame['frame_id'].startswith('probe-') for frame in composition['observation']['frames'])
    assert all(doc['knowledge_snapshot'] is None for doc, _ in author.uploads)
    assert service.inspect('developer', project['id'], project['latest_experiment_id'])['frozen'] == frozen
    path, mime = service.artifact('developer', project['id'], study['artifacts']['picture']['id'])
    assert path.is_file() and mime == 'image/png'
    before = (len(author.submissions), len(renderer.calls), len(reviewer.submissions))
    service.get('developer', project['id']); service.catalogue('developer')
    assert (len(author.submissions), len(renderer.calls), len(reviewer.submissions)) == before


@pytest.mark.parametrize('flag,code', [('allow_collaboration', 'collaboration_disabled'),
                                      ('allow_probes', 'probes_disabled')])
def test_disabled_strategy_stops_after_original_plan_without_child_calls(tmp_path, flag, code):
    service, author, reviewer, renderer = system(tmp_path)
    project, _ = start(service, **{flag: False})
    result = finish(service, project)
    assert result['state'] == 'failed' and code in result['experiments'][-1]['error']['code']
    assert len(author.submissions) == 1 and not renderer.calls and not reviewer.submissions


def test_failed_probe_render_retry_keeps_paid_plan_and_source(tmp_path):
    service, author, reviewer, renderer = system(tmp_path)
    author.plan = plan(components=False)
    project, _ = start(service, max_rounds=1)
    failed_run = advance_to(service, project, stage='render', phase='probe_review')
    renderer.fail = True
    service.advance('developer', failed_run['id'])
    prior = service.store.read('experiments', 'developer', failed_run['id'])
    assert prior['state'] == 'failed' and prior['stop_proven']
    paid_before = len(author.submissions)
    from uuid import uuid4
    retried = service.next_experiment('developer', project['id'], dict(request_id=uuid4().hex, mode='retry'))
    renderer.fail = False
    current = advance_to(service, retried, stage='review', phase='probe_review')
    assert len(author.submissions) == paid_before
    assert current['learning_strategy']['plan_operation'] == prior['learning_strategy']['plan_operation']
    assert current['internal_attempts'][0]['author_operation_id'] == prior['internal_attempts'][0]['author_operation_id']
    finished = finish(service, retried)
    assert finished['state'] == 'completed'
    original = service.store.read('experiments', 'developer', failed_run['id'])
    assert original == prior  # Recovery does not rewrite failed history.


def test_stop_during_specialist_does_not_submit_other_parts_or_render(tmp_path):
    service, author, reviewer, renderer = system(tmp_path)
    author.plan = plan(probes=False)
    project, _ = start(service)
    advance_to(service, project, phase='component')
    author.active = True
    service.advance('developer', project['latest_experiment_id'])
    service.stop('developer', project['id'])
    result = finish(service, project)
    assert result['state'] == 'cancelled'
    assert len(author.submissions) == 2 and len(author.cancelled) == 1
    assert not renderer.calls and not reviewer.submissions


def test_stopped_between_components_retry_creates_the_next_task_without_replaying_completed_input(tmp_path):
    from uuid import uuid4
    service, author, _, renderer = system(tmp_path)
    author.plan = plan(probes=False)
    project, _ = start(service, max_rounds=1)
    for _ in range(20):
        run = service.store.read('experiments', 'developer', project['latest_experiment_id'])
        if (run.get('learning_strategy') or {}).get('component_id') == 'space':
            break
        service.advance('developer', run['id'])
    assert run['learning_strategy']['component_id'] == 'space'
    assert run['active_operation_id'] is None and len(author.submissions) == 2
    assert not renderer.calls
    service.stop('developer', project['id'])
    stopped = finish(service, project)
    assert stopped['state'] == 'cancelled'
    retried = service.next_experiment('developer', project['id'], dict(request_id=uuid4().hex, mode='retry'))
    run = service.store.read('experiments', 'developer', retried['latest_experiment_id'])
    assert 'technical_retry_context' not in run
    assert run['learning_strategy']['component_id'] == 'space'
    result = finish(service, retried, limit=80)
    assert result['state'] == 'completed', result['experiments'][-1].get('error')
    tasks = [doc['constraints'].get('learning_strategy_task', {}) for doc, _ in author.uploads]
    assert [(task.get('phase'), task.get('task_id')) for task in tasks] == [
        ('plan', None), ('component', 'material'), ('component', 'space'), ('compose', None), (None, None)]
    original = service.store.read('experiments', 'developer', project['latest_experiment_id'])
    assert original['state'] == 'cancelled' and len(original['learning_strategy']['components']) == 1


def test_stopped_after_plan_retry_runs_the_pending_probe_without_new_planning(tmp_path):
    from uuid import uuid4
    service, author, _, _ = system(tmp_path)
    author.plan = plan(components=False)
    project, _ = start(service, max_rounds=1)
    run = advance_to(service, project, stage='author', phase='probe_author')
    assert run['active_operation_id'] is None and len(author.submissions) == 1
    service.stop('developer', project['id'])
    assert finish(service, project)['state'] == 'cancelled'
    retried = service.next_experiment('developer', project['id'], dict(request_id=uuid4().hex, mode='retry'))
    result = finish(service, retried, limit=80)
    assert result['state'] == 'completed', result['experiments'][-1].get('error')
    phases = [doc['constraints'].get('learning_strategy_task', {}).get('phase') for doc, _ in author.uploads]
    assert phases == ['plan', 'probe_author', 'compose', None]
