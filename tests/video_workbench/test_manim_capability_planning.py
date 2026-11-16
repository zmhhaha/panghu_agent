"""Planning controls paid resources; synthetic peers do not assert visual quality."""
from copy import deepcopy
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, digest
from agent_service.animation_skills_contract import build_skill_package
from video_workbench.animation_method_service import AnimationMethodService
from video_workbench.manim_method_resources import PRODUCER_REQUIREMENT
from test_animation_method_service import Gateway, Renderer, finish, start, runtime_capabilities
from test_animation_skill_method_service import SkillGateway
from test_manim_method_integration import setup
from test_manim_method_rounds import advance_until
from test_animation_creation_selection import MANIM_CARRIER


class ChoiceGateway(Gateway):
    engine = 'canvas'
    use_manim = False
    empty_plan = False

    def collect(self, *args):
        result = super().collect(*args)
        for method in result['methods']:
            method['runtime_requirements']['engine'] = self.engine
            if self.use_manim:
                method['runtime_requirements']['resource_authoring'] = deepcopy(PRODUCER_REQUIREMENT)
        if self.empty_plan and result['mode'] == 'plan':
            result['methods'] = []
        if result['source'] is not None:
            result['source']['engine'] = self.engine
            if self.use_manim:
                result['source']['files'] = {'scene.html': '<html><script type="module" src="scene.js"></script></html>',
                                           'scene.js': MANIM_CARRIER}
        return result


def work(tmp_path, *, author=None):
    coordinator, recipe, executor, _ = setup(tmp_path)
    author = author or ChoiceGateway()
    reviewer = SkillGateway(True) if isinstance(author, SkillGateway) else Gateway(True)
    renderer = Renderer(tmp_path / 'renders')
    renderer.capabilities = lambda: runtime_capabilities(['html-svg', 'canvas'])
    original_render = renderer.render

    def render(operation_id, source, document, **options):
        outcome = original_render(operation_id, source, document, **options)
        outcome['runtime']['engine'] = source['engine']
        descriptors = document['constraints'].get('generated_resources', [])
        if descriptors:
            descriptor = descriptors[0]
            outcome['runtime'].update(generated_resources=[descriptor], generated_resource_observation=dict(
                schema='video.manim-method-resource-observation.v1',
                scope='loaded_resources_not_visual_acceptance', helpers_loaded=True,
                resources_sha256=[descriptor['sha256']], loaded_png_paths=sorted('/assets/' + name
                    for name in descriptor['assets_sha256'] if name.endswith('.png'))))
        renderer.results[operation_id] = deepcopy(outcome)
        return outcome

    renderer.render = render
    service = AnimationMethodService(tmp_path / 'lab', author_gateway=author, review_gateway=reviewer,
        renderer=renderer, manim_coordinator=coordinator)
    return service, coordinator, recipe, executor, author


def mixed(service, **options):
    return start(service, capability_selection={'creation': ['canvas', 'manim'], 'motion': []},
        resource_authoring=dict(kind='manim', frame_count=3), **options)


def test_unused_manim_never_preflights_reserves_or_attaches_producer_and_restart_replays(tmp_path, monkeypatch):
    service, coordinator, recipe, executor, author = work(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail('unadopted candidate must not touch the resource runtime')
    monkeypatch.setattr(coordinator, 'preflight', forbidden)
    monkeypatch.setattr(service.resource_stage, 'advance', forbidden)
    monkeypatch.setattr(service.resource_stage, 'allocated_round', forbidden)
    body = dict(request_id=str(uuid4()), goal='Canvas only is sufficient', duration_seconds=12,
        budget=dict(max_rounds=1, max_seconds=300),
        capability_selection={'creation': ['canvas', 'manim'], 'motion': []})
    row = service.start('developer', body)
    assert row['adopted_capabilities'] is None
    assert row['resource_authoring']['status'] == 'awaiting_plan'
    result = finish(service, row['id'])
    decision = result['adopted_capabilities']
    assert decision['creation'] == ['canvas'] and decision['uses_manim'] is False
    assert result['resource_authoring']['status'] == 'not_adopted'
    assert result['resource_authoring']['usage'] == {'attempts': 0}
    run = service.commissions.get('developer', row['id'])
    plan = next(op for op in run['operations'] if op['input'].get('mode') == 'plan')
    assert decision['plan_result_sha256'] == plan['result_sha256'] == digest(plan['result'])
    assert run['adopted_capabilities_sha256'] == digest(decision)
    create = next(op for op in run['operations'] if op['input'].get('mode') == 'create')
    assert create['input']['constraints']['allowed_engines'] == ['canvas']
    assert 'generated_resources' not in create['input']['constraints']
    assert not create['result']['methods'][0]['runtime_requirements'].get('resource_authoring')
    assert recipe.submissions == executor.calls == 0
    restarted = AnimationMethodService(service.root, author_gateway=author, review_gateway=service.reviewer,
        renderer=service.renderer, manim_coordinator=coordinator)
    assert restarted.start('developer', body)['id'] == row['id']
    assert restarted.commissions.get('developer', row['id'])['adopted_capabilities'] == decision


def test_canvas_continuation_can_add_unused_manim_without_requiring_baseline_python(tmp_path, monkeypatch):
    service, coordinator, recipe, executor, _ = work(tmp_path)
    first = finish(service, start(service, capability_selection={'creation': ['canvas']})['id'])
    monkeypatch.setattr(coordinator, 'preflight', lambda *args: pytest.fail('unused resource'))
    next_run = mixed(service, base_commission_id=first['id'])
    frozen = service.commissions.get('developer', next_run['id'])['frozen']
    assert 'baseline_descriptor' not in frozen['manim_iteration']
    completed = finish(service, next_run['id'])
    assert completed['state'] == 'completed', completed
    assert not completed['adopted_capabilities']['uses_manim']
    assert recipe.submissions == executor.calls == 0


def test_mixed_plan_can_choose_manim_carrier_without_browser_decoration(tmp_path):
    service, _, recipe, executor, author = work(tmp_path)
    author.engine, author.use_manim = 'html-svg', True
    row = start(service, capability_selection={'creation': ['canvas', 'manim'], 'motion': ['native-js']},
                resource_authoring=dict(kind='manim', frame_count=3))
    run = advance_until(service, row['id'], lambda run: bool(run.get('adopted_capabilities')))
    plan = next(op for op in run['operations'] if op['input'].get('mode') == 'plan')
    assert 'generated_resources' not in plan['input']['constraints']
    assert recipe.submissions == executor.calls == 0
    assert run['adopted_capabilities']['creation'] == ['manim']
    assert run['adopted_capabilities']['motion'] == []
    completed = finish(service, row['id'], steps=100)
    assert completed['state'] == 'completed'
    assert completed['capability_selection']['motion'] == ['native-js']
    assert recipe.submissions == executor.calls == 1


@pytest.mark.parametrize('bad', ['empty', 'outside', 'missing-producer'])
def test_invalid_plan_stops_before_any_resource_task(tmp_path, bad):
    service, _, recipe, executor, author = work(tmp_path)
    if bad == 'empty':
        author.empty_plan = True
    elif bad == 'outside':
        author.engine = 'html-svg'  # Carrier is legal only when Manim is adopted.
    else:
        author.engine = 'html-svg'
    selection = {'creation': ['manim']} if bad == 'missing-producer' else {'creation': ['canvas', 'manim']}
    row = start(service, capability_selection=selection)
    final = finish(service, row['id'])
    assert final['state'] == 'failed'
    assert final['resource_authoring']['status'] == 'not_started'
    assert recipe.submissions == executor.calls == 0


def test_cancel_during_plan_never_reserves_resource(tmp_path, monkeypatch):
    service, coordinator, recipe, executor, author = work(tmp_path)
    author.active = True
    monkeypatch.setattr(coordinator, 'preflight', lambda *args: pytest.fail('plan is cancelled'))
    monkeypatch.setattr(service.resource_stage, 'advance', lambda *args, **kwargs: pytest.fail('resource reserved'))
    row = mixed(service)
    advance_until(service, row['id'], lambda run: bool(author.submissions))
    service.commissions.cancel('developer', row['id'])
    final = finish(service, row['id'])
    assert final['state'] == 'cancelled' and final['adopted_capabilities'] is None
    assert recipe.submissions == executor.calls == 0


def test_allocated_recipe_reconciles_and_waits_for_stop_proof_after_admission_changes(tmp_path, monkeypatch):
    service, coordinator, recipe, executor, author = work(tmp_path)
    author.engine, author.use_manim = 'html-svg', True
    row = mixed(service)
    advance_until(service, row['id'], lambda run: recipe.submissions == 1)
    recipe.job.update(state='running', stop_proven=False)
    original_round = service.resource_stage.allocated_round('developer', row['id'])
    assert original_round == 0

    def no_longer_admitted(*args, **kwargs):
        raise ServiceError('changed_runtime_admission', 409)

    monkeypatch.setattr(coordinator, 'preflight', no_longer_admitted)
    monkeypatch.setattr(coordinator, 'recipe_guidance', no_longer_admitted)
    # A new admission check must not mark the parent failed while its original
    # paid recipe is still live. Existing work continues through reconciliation.
    service.advance('developer', row['id'])
    assert service.get('developer', row['id'])['state'] not in {'failed', 'completed', 'cancelled'}
    service.commissions.cancel('developer', row['id'])
    service.advance('developer', row['id'])
    assert recipe.cancelled is True
    assert service.get('developer', row['id'])['state'] == 'cancelling'
    assert recipe.submissions == 1 and executor.calls == 0
    recipe.job.update(state='cancelled', stop_proven=True)
    final = finish(service, row['id'])
    assert final['state'] == 'cancelled'
    assert service.resource_stage.allocated_round('developer', row['id']) == original_round
    assert final['resource_authoring']['status'] == 'cancelled'
    assert recipe.submissions == 1 and executor.calls == 0


def test_new_manim_only_motion_is_rejected_but_normalization_accepts_historical_shape(tmp_path):
    from video_workbench.animation_creation_selection import normalize_selection
    service, _, recipe, executor, _ = work(tmp_path)
    old = {'creation': ['manim'], 'motion': ['native-js']}
    assert normalize_selection(old) == old
    with pytest.raises(ServiceError, match='manim_motion_conflict'):
        start(service, capability_selection=old)
    for key in ('adopted_capabilities', 'resource_planning'):
        with pytest.raises(ServiceError):
            start(service, **{key: {}})
    assert recipe.submissions == executor.calls == 0


def test_historical_manim_motion_same_key_replay_keeps_frozen_input(tmp_path):
    from video_workbench.animation_creation_selection import freeze_constraints
    from video_workbench.manim_method_coordinator import authoring_request
    service, _, recipe, executor, _ = work(tmp_path)
    request_id = str(uuid4())
    selection = {'creation': ['manim'], 'motion': ['native-js']}
    body = dict(request_id=request_id, goal='A historic native carrier', duration_seconds=12,
                budget=dict(max_rounds=1, max_seconds=300), capability_selection=selection,
                resource_authoring=dict(kind='manim', frame_count=3))
    capabilities = service._capabilities()
    frozen = dict(goal=body['goal'], duration_seconds=12, capability_selection=deepcopy(selection),
        budget=dict(creator=2, review=1, render=1, seconds=300),
        resource_authoring=authoring_request(body['resource_authoring'], 12),
        constraints=freeze_constraints(selection, dict(allowed_engines=capabilities['allowed_engines'],
            runtime_identity=capabilities['runtime_identity']), capabilities['assets']),
        assets=capabilities['assets'], manim_iteration=dict(criteria=[], methods=[]))
    old = service.commissions.create('developer', frozen, request_id)
    assert service.start('developer', body)['id'] == old['id']
    replayed = service.commissions.get('developer', old['id'])
    assert replayed['frozen'] == old['frozen']
    assert 'resource_planning' not in replayed['frozen']
    assert recipe.submissions == executor.calls == 0


def test_deadline_during_plan_does_not_make_or_preflight_resources(tmp_path, monkeypatch):
    service, coordinator, recipe, executor, author = work(tmp_path)
    author.active = True
    row = mixed(service)
    run = advance_until(service, row['id'], lambda run: bool(author.submissions))
    monkeypatch.setattr(service, 'clock', lambda: run['deadline_at'] + 1)
    monkeypatch.setattr(coordinator, 'preflight', lambda *args: pytest.fail('deadline before resource adoption'))
    monkeypatch.setattr(service.resource_stage, 'advance', lambda *args, **kwargs: pytest.fail('resource reserved'))
    final = finish(service, row['id'])
    assert final['state'] in {'failed', 'completed'}
    assert final['adopted_capabilities'] is None
    assert final['resource_authoring']['status'] == 'not_started'
    assert recipe.submissions == executor.calls == 0


class ProducerSkillGateway(SkillGateway):
    def collect(self, *args):
        result = super().collect(*args)
        for method in result['methods']:
            method['runtime_requirements']['resource_authoring'] = deepcopy(PRODUCER_REQUIREMENT)
            package = method['skill_package']
            method['skill_package'] = build_skill_package(skill_id=package['skill_id'], version=package['version'],
                kind=package['kind'], files=package['files'], runtime_requirements=dict(
                    package['runtime_requirements'], resource_authoring=deepcopy(PRODUCER_REQUIREMENT)))
        if result['source'] is not None:
            result['source']['files'] = {'scene.html': '<html><script type="module" src="scene.js"></script></html>',
                                       'scene.js': MANIM_CARRIER}
        return result


def test_v2_transfer_derives_manim_from_frozen_input_methods_with_empty_plan_output(tmp_path):
    service, _, recipe, executor, _ = work(tmp_path, author=ProducerSkillGateway())
    first = finish(service, start(service, capability_selection={'creation': ['manim']},
        resource_authoring=dict(kind='manim', frame_count=3))['id'], steps=100)
    assert first['state'] == 'completed'
    body = dict(request_id=str(uuid4()), goal='Apply shear to a new geometry',
        structural_changes={'relations': {'before': 'one shape', 'after': 'two related shapes'}},
        content_changes=dict(before=first['goal'], after='Apply shear to a new geometry', reason='Changed shape relation'),
        budget=dict(max_rounds=1, max_seconds=300))
    transferred = service.transfer('developer', first['id'], body)
    run = advance_until(service, transferred['id'], lambda run: bool(run.get('adopted_capabilities')))
    plan = next(op for op in run['operations'] if op['input'].get('mode') == 'plan')
    assert plan['result']['methods'] == [] and plan['input']['methods']
    assert run['adopted_capabilities']['uses_manim'] is True
    assert recipe.submissions == executor.calls == 1
    completed = finish(service, transferred['id'], steps=100)
    assert completed['state'] == 'completed'
    assert recipe.submissions == executor.calls == 2
