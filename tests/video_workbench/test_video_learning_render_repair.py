"""Source faults get one repair; capture limits preserve the original artwork."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from agent_service.profiles import video_reference_learning as protocol
from video_workbench import animation_method_renderer as shared
from video_workbench.video_learning_renderer import VideoLearningRenderer
from video_workbench.video_learning_service import VideoLearningService
from test_animation_method_renderer import IMAGE, SOURCE, SyntheticExecution
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
        self.failure_code = 'method_scene_prepare_failed'
        self.failure_category = 'scene_interface_unavailable'

    def render(self, identity, source, document, *, deadline_at=None):
        self.fail = self.failures > 0
        self.failures -= 1
        result = super().render(identity, source, document, deadline_at=deadline_at)
        self.inputs[identity] = deepcopy((source, document))
        if self.fail:
            result['stop_proven'] = self.stopped
            if self.diagnose:
                result.update(evidence_sha256='e' * 64, diagnostic=dict(
                    schema='video.animation-render-failure.v1', code=self.failure_code,
                    category=self.failure_category,
                    source_repair_recommended=True, receipt_bound=True,
                    operation_id=identity, source_sha256=source['source_sha256'],
                    input_sha256=digest(document), evidence_sha256='e' * 64,
                    summary='The host reported a classified failure.', guidance=[]))
        self.results[identity] = result
        return deepcopy(result)


@pytest.fixture
def system(tmp_path, real_mp4):
    author, reviewer = Gateway(), Gateway()
    renderer = DiagnosedRenderer(tmp_path / 'renders', real_mp4)
    service = VideoLearningService(tmp_path / 'videos', author_gateway=author,
        review_gateway=reviewer, renderer=renderer)
    return service, author, reviewer, renderer


@pytest.fixture
def timeout_system(tmp_path, monkeypatch):
    """Exercise the actual adapter and strict host-report parser, without Docker."""
    runtime = shared.MethodAnimationRenderer(tmp_path / 'runtime', IMAGE,
        enabled=True, hyperframes=True, legacy_bridge=True)
    renderer = VideoLearningRenderer(tmp_path / 'renders', runtime_renderer=runtime)
    execute = SyntheticExecution('known')

    def capture_timeout(directory):
        outcome = execute(directory)
        root = Path(directory)
        binding = json.loads((root / 'binding.json').read_bytes())
        request = binding['request']
        outcome['error'] = 'animation_method_render_container_failed'
        (root / 'render-outcome.json').write_bytes(canonical_bytes(outcome))
        (root / 'work').mkdir()
        receipt = dict(schema=request['schema'].replace('-request.', '-receipt.'), status='failed',
            request_identity=request['identity'], request_sha256=binding['request_sha256'],
            image_digest=binding['image_digest'], host_sha256=request['host_sha256'],
            **{key: request[key] for key in ('engine', 'engine_runtime_sha256', 'project_runtime_sha256')
               if key in request},
            error='TimeoutError: page.screenshot: Timeout 10000ms exceeded.\n'
                  'Call log:\n  - taking page screenshot\n')
        (root / 'work/render-receipt.json').write_bytes(canonical_bytes(receipt))
        return outcome

    def html_source():
        return dict(deepcopy(SOURCE), source_sha256=protocol.source_sha256(SOURCE))

    monkeypatch.setattr(shared, 'execute_method_render', capture_timeout)
    monkeypatch.setattr('test_video_learning_service.source', html_source)
    author, reviewer = Gateway(), Gateway()
    service = VideoLearningService(tmp_path / 'videos', author_gateway=author,
        review_gateway=reviewer, renderer=renderer)
    return service, author, reviewer, renderer, execute


def at_failed_render(system, raw, **options):
    service, author, reviewer, renderer = system
    project, _ = start(service, raw, **options)
    for _ in range(10):
        service.advance('developer', project['latest_experiment_id'])
        if renderer.calls:
            return project, service.store.read('experiments', 'developer', project['latest_experiment_id'])
    raise AssertionError('render was never reached')


@pytest.mark.parametrize('code,category', [
    ('method_scene_prepare_failed', 'scene_interface_unavailable'),
    ('method_absolute_seek_mismatch', 'non_deterministic_seek'),
    ('method_reverse_seek_mismatch', 'non_deterministic_seek'),
])
def test_render_failure_reaches_creator_with_frozen_goal_reference_and_tool_constraints(
        system, real_mp4, code, category):
    service, author, reviewer, renderer = system
    renderer.failure_code, renderer.failure_category = code, category
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
    assert failure['diagnostic']['category'] == category
    assert failure['diagnostic']['code'] == code
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
        code='method_scene_prepare_failed', category='scene_interface_unavailable',
        source_sha256=source['source_sha256'],
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


def test_bound_host_capture_timeout_preserves_source_and_does_not_author_again(timeout_system, real_mp4):
    service, author, reviewer, renderer, execute = timeout_system
    project, _ = start(service, real_mp4, max_rounds=3)
    result = finish(service, project)
    run = service.store.read('experiments', 'developer', project['latest_experiment_id'])
    assert result['state'] == 'failed' and run['stop_reason'] == 'render_failed'
    assert run['round'] == 1 and run['stage'] == 'render' and not run.get('render_repairs')
    assert len(author.submissions) == execute.calls == 1 and not reviewer.submissions
    candidate = run['candidates'][0]
    assert candidate['status'] == 'render_failed'
    assert candidate['source'] == dict(SOURCE, source_sha256=protocol.source_sha256(SOURCE))
    diagnostic = candidate['render_failure']
    assert diagnostic['code'] == 'method_frame_capture_timeout'
    assert diagnostic['category'] == 'frame_capture_timeout'
    assert diagnostic['receipt_bound'] is True and diagnostic['source_repair_recommended'] is False
    assert 'increase finite render resources or time' in ' '.join(diagnostic['guidance'])
    operation = service.store.read('operations', 'developer', candidate['render_operation_id'])
    assert operation['input_sha256'] == digest(operation['input']) == diagnostic['input_sha256']
    original = deepcopy((run, operation))
    root = renderer._directory(operation['id'])
    before = {name: (root / name).read_bytes() for name in (
        'binding.json', 'render-outcome.json', 'work/render-receipt.json', 'input/scene/scene.html')}
    service.get('developer', project['id'])
    assert renderer.lookup(operation['id'])['diagnostic'] == diagnostic
    assert (service.store.read('experiments', 'developer', run['id']),
        service.store.read('operations', 'developer', operation['id'])) == original
    assert {name: (root / name).read_bytes() for name in before} == before
    assert len(author.submissions) == execute.calls == 1


def test_historical_timeout_recommendation_cannot_turn_retry_into_author(system, real_mp4):
    service, author, reviewer, renderer = system
    renderer.failure_code = 'method_frame_capture_timeout'
    renderer.failure_category = 'frame_capture_timeout'
    project, run = at_failed_render(system, real_mp4, max_rounds=3)
    assert run['state'] == 'failed' and run['round'] == 1
    candidate = deepcopy(run['candidates'][0])
    assert candidate['render_failure']['source_repair_recommended'] is True  # historical advice
    operation = service.store.read('operations', 'developer', candidate['render_operation_id'])
    prior = deepcopy((run, operation))
    request = dict(request_id=uuid4().hex, mode='retry')
    retry = service.next_experiment('developer', project['id'], request)
    pending = service.store.read('experiments', 'developer', retry['latest_experiment_id'])
    assert pending['stage'] == 'render' and pending['round'] == 1
    assert not pending.get('render_repairs') and 'render_repair_pending' not in pending
    assert pending['technical_retry_context']['consumed'] is False
    assert pending['candidates'][0]['source'] == candidate['source']
    assert pending['candidates'][0]['render_failure'] == candidate['render_failure']
    assert pending['frozen'] == run['frozen'] and pending['budget'] == run['budget']
    assert len(author.submissions) == len(renderer.calls) == 1 and not reviewer.submissions
    assert service.next_experiment('developer', project['id'], request)['latest_experiment_id'] == pending['id']
    service.advance('developer', pending['id'])
    assert len(author.submissions) == 1 and len(renderer.calls) == 2 and not reviewer.submissions
    retry_source, retry_document = renderer.inputs[renderer.calls[-1]]
    assert retry_source == candidate['source']
    expected = deepcopy(operation['input'])
    expected['experiment_id'] = pending['id']
    assert retry_document == expected
    assert (service.store.read('experiments', 'developer', run['id']),
        service.store.read('operations', 'developer', operation['id'])) == prior


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


def test_new_guidance_is_frozen_and_does_not_change_historical_document_reconstruction(system, real_mp4):
    service, author, reviewer, renderer = system
    project, _ = start(service, real_mp4)
    run = service.store.read('experiments', 'developer', project['latest_experiment_id'])
    current = service._document(run, 'create')
    assert current['constraints']['execution_guidance'] == run['frozen']['runtime_capabilities']['execution_guidance']
    historical = deepcopy(run)
    historical['frozen']['runtime_capabilities'].pop('execution_guidance')
    original = deepcopy(historical)
    assert 'execution_guidance' not in service._document(historical, 'create')['constraints']
    assert historical == original
    assert not author.submissions and not renderer.calls


def test_repair_input_budget_snapshot_is_stable_across_reconstruction(system, real_mp4):
    service, author, reviewer, renderer = system
    project, run = at_failed_render(system, real_mp4, max_rounds=2)
    first = service._document(run, 'create')
    now = service.clock()
    service.clock = lambda: now + 15
    assert service._document(run, 'create') == first
    # A resumed budget cannot rewrite the old repair's own frozen facts.
    run['deadline_at'] += 100
    assert service._document(run, 'create') == first
