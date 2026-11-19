"""Stopped render failures reach the creator once, within the original budget."""
from copy import deepcopy
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.video_learning_service import VideoLearningService
from test_video_learning_service import (
    Gateway, Renderer, real_mp4, start, finish,
)


class DiagnosedRenderer(Renderer):
    def __init__(self, root, raw):
        super().__init__(root, raw)
        self.failures = 1
        self.diagnose = True
        self.stopped = True
        self.inputs = {}

    def render(self, identity, source, document, *, deadline_at=None):
        self.fail = self.failures > 0
        self.failures -= 1
        result = super().render(identity, source, document, deadline_at=deadline_at)
        self.inputs[identity] = deepcopy((source, document))
        if self.fail:
            result['stop_proven'] = self.stopped
            if self.diagnose:
                result.update(evidence_sha256='e' * 64, diagnostic=dict(
                    schema='video.animation-render-failure.v1', category='frame_capture_timeout',
                    source_repair_recommended=True, receipt_bound=True,
                    operation_id=identity, source_sha256=source['source_sha256'],
                    input_sha256=digest(document), evidence_sha256='e' * 64,
                    summary='The host reported a frame capture timeout.', guidance=[]))
        self.results[identity] = result
        return deepcopy(result)


@pytest.fixture
def system(tmp_path, real_mp4):
    author, reviewer = Gateway(), Gateway()
    renderer = DiagnosedRenderer(tmp_path / 'renders', real_mp4)
    service = VideoLearningService(tmp_path / 'videos', author_gateway=author,
        review_gateway=reviewer, renderer=renderer)
    return service, author, reviewer, renderer


def at_failed_render(system, raw, **options):
    service, author, reviewer, renderer = system
    project, _ = start(service, raw, **options)
    for _ in range(10):
        service.advance('developer', project['latest_experiment_id'])
        if renderer.calls:
            return project, service.store.read('experiments', 'developer', project['latest_experiment_id'])
    raise AssertionError('render was never reached')


def test_render_failure_reaches_creator_with_frozen_goal_reference_and_tool_constraints(system, real_mp4):
    service, author, reviewer, renderer = system
    project, run = at_failed_render(system, real_mp4, max_rounds=2,
        capability_selection=dict(creation=['canvas'], motion=[]))
    assert run['state'] == 'running' and run['stage'] == 'author' and run['round'] == 2
    assert run['candidates'][0]['status'] == 'render_failed'
    repaired = finish(service, project)
    assert repaired['state'] == 'completed', repaired
    docs = [doc for doc, _ in author.uploads if doc['work_kind'] == 'reproduction']
    assert len(docs) == 2
    first, repair = docs
    assert repair['goal'] == first['goal'] and repair['criteria'] == first['criteria']
    assert repair['knowledge_binding'] == first['knowledge_binding']
    assert repair['knowledge_snapshot'] == first['knowledge_snapshot']
    assert repair['observation']['frames'] == first['observation']['frames']
    assert repair['constraints']['allowed_engines'] == ['canvas']
    assert repair['constraints']['capability_selection'] == first['constraints']['capability_selection']
    failure = repair['constraints']['render_repair']
    assert failure['diagnostic']['category'] == 'frame_capture_timeout'
    assert failure['failed_source']['source_sha256'] == run['candidates'][0]['source_sha256']
    assert repair['base_source'] == failure['failed_source']
    assert repair['constraints']['execution_guidance']['environment']['browser_renderer']['gpu_acceleration'] is False
    assert len(repaired['experiments'][0]['render_repairs']) == 1
    candidates = repaired['candidates']
    assert candidates[1]['repair_of_candidate_id'] == candidates[0]['id']
    assert candidates[0].get('media_url') is None and candidates[1]['media_url']
    assert repaired['adopted_candidate_id'] is None and repaired['published'] is False


def test_repair_has_one_creator_attempt_even_when_more_rounds_are_available(system, real_mp4):
    service, author, reviewer, renderer = system
    renderer.failures = 50
    project, _ = start(service, real_mp4, max_rounds=8)
    result = finish(service, project)
    assert result['state'] == 'failed' and result['experiments'][0]['stop_reason'] == 'render_failed'
    assert len(author.submissions) == 2 and len(renderer.calls) == 2
    assert not reviewer.submissions


@pytest.mark.parametrize('classified,stopped,rounds', [(False, True, 3), (True, False, 3), (True, True, 1)])
def test_unknown_unstopped_or_exhausted_failure_does_not_author_another_source(system, real_mp4,
        classified, stopped, rounds):
    service, author, reviewer, renderer = system
    renderer.diagnose, renderer.stopped = classified, stopped
    project, run = at_failed_render(system, real_mp4, max_rounds=rounds)
    for _ in range(4):
        service.advance('developer', run['id'])
    current = service.store.read('experiments', 'developer', run['id'])
    assert len(author.submissions) == 1 and len(renderer.calls) == 1
    assert current['state'] == ('submission_unknown' if not stopped else 'failed')
    assert not current.get('render_repairs')


def test_technical_retry_can_reclassify_a_stopped_historical_failure_without_rerendering_it(system, real_mp4):
    service, author, reviewer, renderer = system
    renderer.diagnose = False
    project, run = at_failed_render(system, real_mp4, max_rounds=2)
    assert run['state'] == 'failed'
    identity = renderer.calls[0]
    source, document = renderer.inputs[identity]
    renderer.results[identity].update(evidence_sha256='e' * 64, diagnostic=dict(
        source_repair_recommended=True, receipt_bound=True, operation_id=identity,
        category='frame_capture_timeout', source_sha256=source['source_sha256'],
        input_sha256=digest(document), evidence_sha256='e' * 64))
    renderer.diagnose = True
    request = dict(request_id=uuid4().hex, mode='retry')
    retry = service.next_experiment('developer', project['id'], request)
    pending = service.store.read('experiments', 'developer', retry['latest_experiment_id'])
    assert pending['stage'] == 'author' and pending['round'] == 2
    assert len(renderer.calls) == 1 and len(author.submissions) == 1
    assert pending['technical_retry_context']['consumed'] is True
    assert service.next_experiment('developer', project['id'], request)['latest_experiment_id'] == pending['id']
    assert finish(service, retry)['state'] == 'completed'
    assert len([doc for doc, _ in author.uploads if doc['work_kind'] == 'reproduction']) == 2
    assert len(renderer.calls) == 3  # one original, one repaired, one transfer


def test_stop_arriving_during_render_prevents_repair_submission(system, real_mp4):
    service, author, reviewer, renderer = system
    original = renderer.render
    project, _ = start(service, real_mp4, max_rounds=2)
    def stopped_render(*args, **kwargs):
        result = original(*args, **kwargs)
        service.store.stop('developer', project['latest_experiment_id'])
        return result
    renderer.render = stopped_render
    result = finish(service, project)
    assert len(author.submissions) == 1 and len(renderer.calls) == 1
    assert not result['experiments'][0].get('render_repairs')


def test_less_than_ninety_seconds_remaining_does_not_start_repair(system, real_mp4):
    service, author, reviewer, renderer = system
    original = renderer.render
    project, _ = start(service, real_mp4, max_rounds=2)
    def near_deadline(*args, **kwargs):
        result = original(*args, **kwargs)
        service.clock = lambda: kwargs['deadline_at'] - 60
        return result
    renderer.render = near_deadline
    result = finish(service, project)
    assert result['state'] == 'failed'
    assert len(author.submissions) == 1 and not result['experiments'][0].get('render_repairs')


@pytest.mark.parametrize('field', ['resource_plan', 'learning_strategy'])
def test_repair_cannot_start_a_new_resource_or_collaboration_plan(system, field):
    service, author, reviewer, renderer = system
    operation = dict(input=dict(constraints=dict(render_repair={})))
    with pytest.raises(ServiceError, match='video_learning_repair_nested_plan_forbidden'):
        service._adopt_resource_proposal({}, operation, dict(method={field: {}}))
    assert not author.submissions and not reviewer.submissions and not renderer.calls
