"""Real formal-film stores retain original module/audio/Skill identities.

The existing fixture executes frozen contracts with synthetic peers/media;
it does not constitute animation, continuous playback or speech evaluation.
"""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from agent_service.contracts import ServiceError, digest
from agent_service.profiles.video_animation_review import REVIEW_PACKED_PROFILE
from video_workbench.production_refinement_driver import ProductionRefinementDriver
from test_film_creation_workflow import OWNER, env, completed_modules, render_request


def baseline(env):
    modules = completed_modules(env)
    operation = env.workflow.start_render(OWNER, env.project['id'], render_request(env, modules), 'refinement-baseline')
    env.workflow.tick()
    operation = env.workflow.get_render(OWNER, operation['id'])
    assert operation['status'] == 'completed'
    return dict(run_id='production-whole-film', version_id=operation['id'], sha256=operation['sha256'])


def driver(env, tmp_path):
    return ProductionRefinementDriver(env.store, env.workflow,
        SimpleNamespace(profile=REVIEW_PACKED_PROFILE, preflight=lambda deadline: dict(ready=True)), tmp_path / 'evidence')


def state(context, index=0):
    return dict(id='ad6634f3-b322-400f-8e56-b33213ef111e', kind='user_requested',
        children=[dict(stage='module', index=index, key='refine-module-' + str(index), status='reserved')],
        module_version_ids=deepcopy(context['module_version_ids']),
        request=dict(instruction='让对象更易辨认。', items=None),
        review=dict(issues=[], limitations=['仅观察了抽帧，未听取音频。']))


def test_real_source_media_snapshot_and_module_context_are_frozen(env, tmp_path):
    source = baseline(env)
    context = driver(env, tmp_path).freeze(OWNER, env.project['id'], source)
    assert context['source'] == source and context['film'] == env.film
    assert context['film_sha256'] == digest(env.film)
    assert context['module_targets'] == [render_request(env, [])['module_targets'][i] for i in range(2)]
    assert len(context['modules']) == 2
    assert context['configuration_sha256'] == env.creation.configuration_sha256
    assert context['review_profile'] == REVIEW_PACKED_PROFILE
    assert len(env.renderer.calls) == 1


def test_revision_submits_original_exact_module_with_same_target_audio_and_predecessor(env, tmp_path):
    source = baseline(env)
    real = driver(env, tmp_path)
    context = real.freeze(OWNER, env.project['id'], source)
    current = state(context)
    checksum, submit = real.prepare('module', 'formal-refine-module-0', context, current)
    admitted = submit()
    durable = env.creation.operation('module', admitted['id'])
    request = durable['request']
    assert digest(request) == checksum
    assert request['base_module_version_id'] == context['module_version_ids'][0]
    assert request['target'] == context['module_targets'][0]
    assert request['film'] == env.film and request['film_sha256'] == digest(env.film)
    assert durable['document']['core_task']['base_module'] == env.workflow.module_package(
        OWNER, context['module_version_ids'][0])['result']['module']
    env.workflow.tick()
    receipt = real.lookup('module', 'formal-refine-module-0', context, current)
    assert receipt['state'] == 'completed' and receipt['stop_proven'] is True
    current['module_version_ids'][0] = receipt['output']['version_id']
    current['children'] = [dict(stage='module', index=1, status='reserved')]
    _, submit = real.prepare('module', 'formal-refine-module-1', context, current)
    second = submit()
    assert env.creation.operation('module', second['id'])['request']['predecessor_version_id'] == current['module_version_ids'][0]


def test_final_render_uses_original_run_lineage_and_checked_real_bytes(env, tmp_path):
    source = baseline(env)
    real = driver(env, tmp_path)
    context = real.freeze(OWNER, env.project['id'], source)
    current = state(context)
    _, submit = real.prepare('render', 'formal-refine-render-key', context, current)
    admitted = submit()
    assert env.creation.operation('render', admitted['id'])['request']['production_run_id'] == source['run_id']
    env.workflow.tick()
    receipt = real.lookup('render', 'formal-refine-render-key', context, current)
    assert receipt['state'] == 'completed' and receipt['stop_proven'] is True
    assert receipt['output']['source']['run_id'] == source['run_id']
    assert receipt['output']['source']['version_id'] == admitted['id']
    assert receipt['output']['source']['sha256'] == source['sha256']


def test_source_sha_owner_and_project_cannot_be_swapped(env, tmp_path):
    source = baseline(env)
    real = driver(env, tmp_path)
    with pytest.raises(ServiceError, match='production_refinement_source_changed'):
        real.freeze(OWNER, env.project['id'], dict(source, sha256='f' * 64))
    with pytest.raises(ServiceError, match='not_found'):
        real.freeze('different-owner', env.project['id'], source)
    with pytest.raises(ServiceError, match='production_refinement_source_not_completed'):
        real.freeze(OWNER, env.project['id'], dict(source, run_id='invented-origin'))


def test_source_current_content_change_rejected_before_new_child(env, tmp_path):
    source = baseline(env)
    real = driver(env, tmp_path)
    scene = env.project['scenes'][0]
    env.store.save_scene(OWNER, env.project['id'], dict(title='Different understanding goal',
        narration=scene['narration'], visual_description=scene['visual_description'], duration_seconds=1,
        format='html'), scene_id=scene['id'], expected_revision=env.project['revision'])
    with pytest.raises(ServiceError, match='film_creation_project_binding_mismatch'):
        real.freeze(OWNER, env.project['id'], source)


def test_no_implicit_review_profile_or_unbounded_instruction(env, tmp_path):
    with pytest.raises(ServiceError, match='production_refinement_review_profile_required'):
        ProductionRefinementDriver(env.store, env.workflow, SimpleNamespace(profile='anything'), tmp_path)
    source = baseline(env)
    real = driver(env, tmp_path)
    context = real.freeze(OWNER, env.project['id'], source)
    current = state(context)
    current['request']['instruction'] = 'x' * 16000
    with pytest.raises(ServiceError, match='production_refinement_instruction_too_large'):
        real.prepare('module', 'formal-refine-oversized', context, current)


@pytest.mark.parametrize('kind', ['automatic', 'requirements'])
def test_point_issues_reach_author_at_start_next_scene_boundary_and_film_end(kind):
    context = dict(film=dict(scenes=[dict(start_seconds=0, end_seconds=3),
        dict(start_seconds=3, end_seconds=6)]))
    issues = [dict(issue_id='point-' + str(point), start_seconds=point, end_seconds=point,
        problem='Visible overlap.', change_target='Separate objects.') for point in (0, 3, 6)]
    request = dict(instruction=None, items=[dict(scope=dict(kind='time_range', start_seconds=3, end_seconds=6),
        problem='Boundary overlap.', expected='Clear objects.')] if kind == 'requirements' else None)
    current = dict(kind=kind, request=request, review=dict(issues=issues, limitations=[]))
    first = json.loads(ProductionRefinementDriver._instruction(context, current, 0))['observed_issues']
    second = json.loads(ProductionRefinementDriver._instruction(context, current, 1))['observed_issues']
    assert [row['issue_id'] for row in first] == ([] if kind == 'requirements' else ['point-0'])
    assert [row['issue_id'] for row in second] == ['point-3', 'point-6']
