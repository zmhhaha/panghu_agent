"""Adaptive studies go through the durable service, not a simulated scheduler."""
from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench import learning_strategy as strategy
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


def test_new_creator_inputs_freeze_complete_source_package_guidance(tmp_path):
    service, author, reviewer, _ = system(tmp_path)
    project, _ = start(service, max_rounds=1)
    completed = finish(service, project, limit=80)
    assert completed['state'] == 'completed'
    inspected = service.inspect('developer', project['id'], project['latest_experiment_id'])
    create_operations = [row for row in inspected['operations']
                         if row['stage'] == 'author' and row['input']['constraints'].get('learning_strategy_task')]
    assert [row['input']['constraints']['learning_strategy_task']['phase'] for row in create_operations] == [
        'plan', 'probe_author', 'component', 'component', 'compose']
    for operation in create_operations:
        instructions = operation['input']['constraints']['learning_strategy_task']['instructions']
        assert all(line in instructions for line in strategy.source_package_guidance())
        assert 'non-null source' in ' '.join(instructions)
        assert 'scene.html' in ' '.join(instructions) and 'scene.js' in ' '.join(instructions)
        assert 'scene.css' in ' '.join(instructions) and 'do not authorize additional filenames' in ' '.join(instructions)
        assert operation['input_sha256'] == digest(operation['input'])
        uploaded = next(files for document, files in author.uploads
                        if canonical_bytes(document) == canonical_bytes(operation['input']))
        assert uploaded[service.protocol.INPUT_NAME] == canonical_bytes(operation['input'])
    # Reviewers receive evidence and review contracts, never a new request to author source.
    assert all(not any(line in document['constraints'].get('learning_strategy_task', {}).get('instructions', [])
                       for line in strategy.source_package_guidance())
               for document, _ in reviewer.uploads)


def test_new_guidance_does_not_rewrite_failed_component_retry_input(tmp_path, monkeypatch):
    from uuid import uuid4
    current_guidance = strategy.source_package_guidance
    # Emulate an already frozen operation created before this prospective guide.
    monkeypatch.setattr(strategy, 'source_package_guidance', lambda: [])
    service, author, _, renderer = system(tmp_path)
    author.plan = plan(probes=False)
    collect = author.collect
    fail_once = True

    def invalid_extra_filename(job_id, sha, document):
        nonlocal fail_once
        result = collect(job_id, sha, document)
        task = document['constraints'].get('learning_strategy_task', {})
        if fail_once and task.get('phase') == 'component' and task.get('task_id') == 'space':
            fail_once = False
            result['source']['files']['node-layer.js'] = result['source']['files'].pop('scene.js')
        return result

    author.collect = invalid_extra_filename
    project, _ = start(service, max_rounds=1)
    failed = finish(service, project)
    assert failed['state'] == 'failed'
    prior = service.store.read('experiments', 'developer', project['latest_experiment_id'])
    assert prior['error']['code'] == 'animation_experiment_files_invalid' and prior['stop_proven']
    assert len(prior['learning_strategy']['components']) == 1 and not renderer.calls
    operation = service.store.read('operations', 'developer', prior['operation_ids'][-1])
    original_input = canonical_bytes(operation['input'])
    original_hash = operation['input_sha256']
    author_calls = len(author.submissions)
    monkeypatch.setattr(strategy, 'source_package_guidance', current_guidance)
    retried = service.next_experiment('developer', project['id'], dict(request_id=uuid4().hex, mode='retry'))
    retry_id = retried['latest_experiment_id']
    retry = service.store.read('experiments', 'developer', retry_id)
    assert retry['learning_strategy'] == prior['learning_strategy']
    assert retry['frozen'] == prior['frozen'] and retry['budget'] == prior['budget']
    service.advance('developer', retry_id)
    retry = service.store.read('experiments', 'developer', retry_id)
    resumed = service.store.read('operations', 'developer', retry['operation_ids'][0])
    assert canonical_bytes(resumed['input']) == canonical_bytes(dict(operation['input'], experiment_id=retry_id))
    assert not any(line in resumed['input']['constraints']['learning_strategy_task']['instructions']
                   for line in current_guidance())
    assert len(author.submissions) == author_calls + 1
    assert canonical_bytes(service.store.read('operations', 'developer', operation['id'])['input']) == original_input
    assert service.store.read('operations', 'developer', operation['id'])['input_sha256'] == original_hash
    completed = finish(service, retried, limit=80)
    assert completed['state'] == 'completed'
    phases = [(document['constraints'].get('learning_strategy_task', {}).get('phase'),
               document['constraints'].get('learning_strategy_task', {}).get('task_id'))
              for document, _ in author.uploads]
    assert phases == [('plan', None), ('component', 'material'), ('component', 'space'),
                      ('component', 'space'), ('compose', None), (None, None)]
    composition = author.uploads[-2][0]['constraints']['learning_strategy_task']
    assert all(line in composition['instructions'] for line in current_guidance())
    assert service.store.read('experiments', 'developer', prior['id']) == prior
