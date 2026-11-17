"""Per-round resource custody in the actual lab service, with fake paid peers."""
from copy import deepcopy
import json

import pytest

from agent_service.contracts import ServiceError
from video_workbench.animation_method_service import AnimationMethodService
from video_workbench.manim_method_resources import PRODUCER_REQUIREMENT
from test_animation_method_service import Gateway, Renderer, CRITERIA, start
from test_manim_method_integration import setup


class ProducerGateway(Gateway):
    def collect(self, *args):
        result = super().collect(*args)
        for method in result['methods']:
            method['runtime_requirements']['resource_authoring'] = deepcopy(PRODUCER_REQUIREMENT)
        if result['source'] is not None:
            result['source']['strategy']['description'] += ' attempt ' + str(len(self.submissions))
        return result


class IterationReviewer(Gateway):
    def __init__(self):
        super().__init__(True)
        self.outcome = 'improved'

    def collect(self, *args):
        result = super().collect(*args)
        result['review']['outcome'] = ('inconclusive' if self.outcome == 'improved'
            and not args[2]['comparison'].get('baseline_candidate_id') else self.outcome)
        if result['review']['outcome'] in {'inconclusive', 'equal', 'regressed'}:
            baseline = args[2]['comparison'].get('baseline_candidate_id')
            if baseline:
                result['review']['preferred_candidate_id'] = baseline
        result['review']['next_guidance'] = 'Change the actual shear geometry and preserve correspondence.'
        return result


def make_service(tmp_path, *, review='improved'):
    coordinator, recipe, executor, _ = setup(tmp_path)
    author, reviewer = ProducerGateway(), IterationReviewer()
    reviewer.outcome = review
    service = AnimationMethodService(tmp_path / 'lab', author_gateway=author, review_gateway=reviewer,
        renderer=Renderer(tmp_path / 'renders'), manim_coordinator=coordinator)
    coordinator.skill_adapter = service.library_adapter
    return service, coordinator, recipe, executor, author, reviewer


def advance_until(service, run_id, predicate, *, steps=120):
    for _ in range(steps):
        run = service.commissions.get('developer', run_id)
        if predicate(run):
            return run
        assert run['state'] not in {'failed', 'cancelled'}, run
        service.advance('developer', run_id)
    pytest.fail('service did not reach expected state')


def resources(service, run):
    return [op['input']['constraints']['generated_resources'][0] for op in run['operations']
            if op['stage'] == 'creator' and op['input'].get('mode') == 'create']


def test_each_exploration_round_gets_new_recipe_and_review_keeps_its_own_resource(tmp_path):
    service, coordinator, recipe, executor, author, reviewer = make_service(tmp_path)
    row = start(service, budget=dict(max_rounds=2, max_seconds=300), criteria=deepcopy(CRITERIA),
        resource_authoring=dict(kind='manim', frame_count=3, global_start_frame=24))
    assert row['resource_authoring']['frame_count'] == 3
    assert row['resource_authoring']['global_start_frame'] == 24
    run = advance_until(service, row['id'], lambda run: run['state'] == 'completed')
    assert run['stop_reason'] == 'budget_exhausted'
    assert recipe.submissions == executor.calls == 2
    assert len(author.submissions) == 3 and len(reviewer.submissions) == 2
    first, second = resources(service, run)
    assert first['scope'] != second['scope']
    assert first['manifest']['resource_id'] != second['manifest']['resource_id']
    assert service.resource_stage.context('developer', row['id'], resource_round=0) == first
    assert service.resource_stage.context('developer', row['id'], resource_round=1) == second
    reviews = [op for op in run['operations'] if op['stage'] == 'review']
    assert reviews[0]['input']['constraints']['generated_resources'] == [first]
    assert reviews[1]['input']['constraints']['generated_resources'] == [second]
    second_recipe = coordinator._get('developer', second['scope']['experiment_id'])
    guidance = json.loads(second_recipe['input']['guidance'].split('\n', 1)[1])
    assert guidance['baseline']['request']['source'] == coordinator.source_context('developer', first)['request']['source']
    assert guidance['criteria'] == CRITERIA
    assert guidance['review']['next_guidance'] == reviews[0]['result']['review']['next_guidance']
    assert guidance['methods']
    # Reading final detail and repeating advance do not create a third resource.
    for _ in range(2):
        assert service.get('developer', row['id'])['resource_authoring']['status'] == 'completed'
        service.advance('developer', row['id'])
    assert recipe.submissions == executor.calls == 2


def test_continuation_freezes_actual_baseline_python_and_feedback_before_recipe_admission(tmp_path):
    service, coordinator, recipe, executor, _, _ = make_service(tmp_path, review='goal_met')
    first = start(service, resource_authoring=dict(kind='manim', frame_count=3))
    completed = advance_until(service, first['id'], lambda run: run['state'] == 'completed')
    descriptor = resources(service, completed)[0]
    next_run = start(service, base_commission_id=first['id'], feedback='The height should stay visibly fixed.')
    frozen = service.commissions.get('developer', next_run['id'])['frozen']
    assert frozen['manim_iteration']['baseline_descriptor'] == descriptor
    assert frozen['manim_iteration']['methods']
    assert frozen['manim_iteration']['criteria'] == CRITERIA
    assert next_run['resource_authoring']['frame_count'] == 3
    assert recipe.submissions == executor.calls == 1
    again = advance_until(service, next_run['id'], lambda run: run['state'] == 'completed')
    revised = resources(service, again)[0]
    assert revised['scope'] != descriptor['scope']
    record = coordinator._get('developer', revised['scope']['experiment_id'])
    guidance = json.loads(record['input']['guidance'].split('\n', 1)[1])
    assert guidance['baseline'] == coordinator.source_context('developer', descriptor)
    assert guidance['feedback'] == frozen['feedback']
    assert recipe.submissions == executor.calls == 2


def test_iteration_projection_is_stable_when_later_live_feedback_changes(tmp_path, monkeypatch):
    service, _, _, _, _, _ = make_service(tmp_path)
    row = start(service, budget=dict(max_rounds=2, max_seconds=300),
                resource_authoring=dict(kind='manim', frame_count=3))
    run = advance_until(service, row['id'], lambda run: len(run['reviews']) == 1)
    first = service._manim_projection('developer', run)
    assert first['resource_round'] == 1
    monkeypatch.setattr(service, '_feedback', lambda *args: [{'comment': 'A later unrelated opinion.'}])
    assert service._manim_projection('developer', run) == first
    assert first['frozen']['feedback'] == 'Change the actual shear geometry and preserve correspondence.'
    monkeypatch.setattr(service, 'clock', lambda: run['deadline_at'] - 10)
    assert service._manim_round(run) == 0  # no paid new recipe with no render window left


def test_second_recipe_reserved_before_create_is_cancelled_by_original_round_after_restart(tmp_path):
    service, coordinator, recipe, executor, author, reviewer = make_service(tmp_path)
    row = start(service, budget=dict(max_rounds=2, max_seconds=300),
                resource_authoring=dict(kind='manim', frame_count=3))
    run = advance_until(service, row['id'], lambda run: len(run['reviews']) == 1)
    first = resources(service, run)[0]
    # Reserve and submit the next resource while main create-1 does not exist.
    for _ in range(12):
        service.advance('developer', row['id'])
        if recipe.submissions == 2:
            break
    assert recipe.submissions == 2 and executor.calls == 1
    before = service.commissions.get('developer', row['id'])
    assert len(resources(service, before)) == 1
    service.commissions.cancel('developer', row['id'])
    resumed = AnimationMethodService(service.root, author_gateway=author, review_gateway=reviewer,
        renderer=service.renderer, manim_coordinator=coordinator)
    # The fake recipe may already have completed; cancellation still prevents
    # Cairo and waits for the same original resource task to become stopped.
    for _ in range(20):
        resumed.advance('developer', row['id'])
        if resumed.get('developer', row['id'])['state'] == 'cancelled':
            break
    final = resumed.get('developer', row['id'])
    assert final['state'] == 'cancelled'
    assert final['resource_authoring']['resource_plan_operation_id'] == resumed.resource_stage.operation_id(row['id'], 1)
    assert final['resource_authoring']['status'] == 'cancelled'
    assert recipe.submissions == 2 and executor.calls == 1
    assert resumed.resource_stage.context('developer', row['id'], resource_round=0) == first


def test_manim_source_context_overflow_is_rejected_before_new_resource_is_reserved(tmp_path):
    service, _, recipe, executor, _, _ = make_service(tmp_path, review='goal_met')
    row = start(service, resource_authoring=dict(kind='manim', frame_count=3))
    advance_until(service, row['id'], lambda run: run['state'] == 'completed')
    before = len(service.commissions.list_all('developer'))
    with pytest.raises(ServiceError, match='iteration_context_too_large'):
        start(service, base_commission_id=row['id'], feedback='x' * 15000)
    assert len(service.commissions.list_all('developer')) == before
    assert recipe.submissions == executor.calls == 1


def test_historic_commission_without_iteration_marker_keeps_original_resource_identity(tmp_path):
    service, _, _, _, _, _ = make_service(tmp_path)
    run = dict(id='historic-id', frozen=dict(resource_authoring={'kind': 'manim'}),
               operations=[], candidates=[], reviews=[])
    assert service._manim_projection('developer', run) is run
    assert service.resource_stage.operation_id(run['id']) == 'historic-id'


def test_two_distinct_unimproved_attempts_stop_without_a_third_resource(tmp_path):
    service, _, recipe, executor, _, _ = make_service(tmp_path, review='inconclusive')
    row = start(service, budget=dict(max_rounds=4, max_seconds=300),
                resource_authoring=dict(kind='manim', frame_count=3))
    run = advance_until(service, row['id'], lambda run: run['state'] == 'completed')
    assert run['stop_reason'] == 'no_progress'
    assert len(resources(service, run)) == recipe.submissions == executor.calls == 2


def test_actual_service_accepts_a_minimal_manim_native_clock_carrier(tmp_path):
    from test_animation_creation_selection import MANIM_CARRIER
    service, _, recipe, executor, author, _ = make_service(tmp_path, review='goal_met')
    collect = author.collect

    def with_carrier(*args):
        result = collect(*args)
        if result['source'] is not None:
            result['source']['files'] = {'scene.html': '<html><script type="module" src="scene.js"></script></html>',
                'scene.js': MANIM_CARRIER}
        return result

    author.collect = with_carrier
    render = service.renderer.render

    def observed(operation_id, source, document, **options):
        outcome = render(operation_id, source, document, **options)
        descriptor = document['constraints']['generated_resources'][0]
        outcome['runtime'].update(generated_resources=[descriptor], generated_resource_observation=dict(
            schema='video.manim-method-resource-observation.v1',
            scope='loaded_resources_not_visual_acceptance', helpers_loaded=True,
            resources_sha256=[descriptor['sha256']], loaded_png_paths=sorted('/assets/' + name
                for name in descriptor['assets_sha256'] if name.endswith('.png'))))
        service.renderer.results[operation_id] = deepcopy(outcome)
        return outcome

    service.renderer.render = observed
    row = start(service, capability_selection={'creation': ['manim'], 'motion': []},
        resource_authoring=dict(kind='manim', frame_count=3))
    run = advance_until(service, row['id'], lambda run: run['state'] == 'completed')
    assert run['stop_reason'] == 'goal_met' and len(run['candidates']) == 1
    assert recipe.submissions == executor.calls == 1
    detail = service.get('developer', row['id'])
    assert detail['resource_authoring']['frame_count'] == 3
    # Explicitly returning to automatic does not secretly prepare the old
    # Manim resource. Its producer-dependent baseline method is incompatible,
    # so the request fails honestly before another paid child can be reserved.
    with pytest.raises(ServiceError):
        start(service, base_commission_id=row['id'],
            capability_selection={'creation': [], 'motion': []})
    assert recipe.submissions == executor.calls == 1
