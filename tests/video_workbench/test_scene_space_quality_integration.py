from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.scene_space_renderer import prepare_scene_render, render_capability
from test_scene_space_renderer import plan
from test_spatial_rendering_route import intent
from test_scene_space_production import scene_env, legacy_env, OWNER, begin, finish


def budget(**changes):
    value=dict(intent=intent(),configuration_sha256='b'*64,usage_lineage='verified-host-task',
        usage_sha256='c'*64,estimated_material_samples=20000,material_noise_limit=20000,usage_known=True)
    value.update(changes)
    return value


def test_budget_is_bound_before_painting_without_changing_geometry_clock_or_cache(plan):
    before=prepare_scene_render(plan,source_sha256='a'*64)
    after=prepare_scene_render(plan,source_sha256='a'*64,appearance_budget=budget())
    policy=after.pop('appearance_policy')
    assert policy['actual_parameters']['material_quality']=='coarse'
    assert policy['source_sha256']=='a'*64
    assert policy['plan_sha256']==before['frozen']['plan_sha256']
    assert policy['expected_loss']
    before.pop('sha256');after.pop('sha256')
    assert before==after


def test_first_page_host_estimate_defaults_to_real_cache_pixels_and_unknown_usage_stops(plan):
    offered=budget(material_noise_limit=64*1024*1024);offered.pop('estimated_material_samples')
    result=prepare_scene_render(plan,source_sha256='a'*64,appearance_budget=offered)
    assert result['appearance_policy']['estimated_material_samples']==result['total_cached_pixels']
    assert result['appearance_policy']['actual_parameters']['material_quality']=='full'
    with pytest.raises(ServiceError,match='scene_appearance_usage_unknown'):
        prepare_scene_render(plan,source_sha256='a'*64,appearance_budget=budget(usage_known=False))
    with pytest.raises(ServiceError,match='scene_appearance_policy_invalid'):
        prepare_scene_render(plan,source_sha256='a'*64,appearance_budget=budget(extra_author_budget=100))


def test_formal_preview_requests_freeze_actual_usage_lineage_and_host_budget(scene_env):
    env=scene_env
    env.renderer.capabilities=lambda owner:dict(render_capability(),authoring_constraints={})
    with env.store.transaction() as db:
        project=env.store._project(db,OWNER,env.project['id'])
        project['joint_plan']['rendering_intent']=intent()
        env.store._save(db,project)
    started=begin(env,key='formal-material-budget')
    finish(env)
    assert env.runs.get(OWNER,started['id'])['status']=='completed'
    assert env.renderer.submissions
    for request in env.renderer.submissions:
        frozen=request['document']['constraints']['scene_space_quality']
        assert frozen['usage_known'] is True
        assert frozen['usage_lineage']==env.spaces.get(OWNER,started['id'])['usage']['lineage_id']
        assert len(frozen['usage_sha256'])==64 and frozen['material_noise_limit']==64*1024*1024
    assert env.spaces.get(OWNER,started['id'])['usage_status']['used']['model_calls']==2
