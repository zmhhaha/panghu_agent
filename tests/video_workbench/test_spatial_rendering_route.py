from copy import deepcopy
import json

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.scene_space_renderer import render_capability
from video_workbench.spatial_rendering_route import preferred_route, admit_route, validate_scene_intent_plan
from test_scene_space_production import scene_env, legacy_env, OWNER, begin, finish


def intent(**changes):
    value=dict(schema='video.spatial-rendering-intent.v1',space_mode='layered',engine='canvas',
        camera_mode='animated',required_features=['form','scale','coverage'],
        optional_features=['lighting','texture','atmosphere'],
        quality=dict(structural_errors='block',optional_degradation='declared'))
    value.update(changes)
    return value


def configuration():
    return dict(available=True,renderer_mode='scene-space',configuration_sha256='a'*64,
        audio_modes=['silent'],render_capability=render_capability())


def test_same_audio_does_not_choose_engine_and_optional_orbit_does_not_force_it():
    assert preferred_route(intent())=='scene-space'
    assert preferred_route(intent(space_mode='planar'))=='hyperframes-film'
    assert preferred_route(intent(engine='three',space_mode='geometric',
        required_features=['form','viewpoint_change']))=='hyperframes-film'
    candidate=intent(optional_features=['viewpoint_change'])
    resolution=admit_route(candidate,configuration(),audio_mode='silent')
    assert resolution['route']=='scene-space'
    assert resolution['quality']['degraded_features']==[
        dict(feature='viewpoint_change',reason='capability_unavailable')]


def test_new_helpers_are_available_from_exact_declared_runtime_and_never_grant_dof():
    value=admit_route(intent(),configuration(),audio_mode='silent')
    assert value['quality']['status']=='satisfied' and value['visual_acceptance'] is False
    assert value['intent_sha256']==digest(intent())
    with pytest.raises(ServiceError) as error:
        admit_route(intent(required_features=['coverage','depth_of_field']),configuration(),audio_mode='silent')
    assert error.value.code=='spatial_rendering_required_features_unavailable'
    changed=configuration();changed['render_capability']['appearance']['surface_runtime_sha256']='b'*64
    degraded=admit_route(intent(),changed,audio_mode='silent')
    assert {r['feature'] for r in degraded['quality']['degraded_features']}=={'lighting','texture','atmosphere'}


def test_unadmitted_engine_audio_and_core_geometry_are_rejected():
    with pytest.raises(ServiceError,match='spatial_rendering_audio_mode_not_admitted'):
        admit_route(intent(),configuration(),audio_mode='narration')
    old=configuration();old['render_capability'].pop('artwork_api_revision')
    with pytest.raises(ServiceError,match='spatial_rendering_required_features_unavailable'):
        admit_route(intent(),old,audio_mode='silent')
    with pytest.raises(ServiceError,match='spatial_rendering_engine_unavailable'):
        admit_route(intent(space_mode='geometric',engine='three'),dict(available=True,
            renderer_mode='hyperframes-film',configuration_sha256='c'*64,
            runtime_capabilities=dict(allowed_engines=['canvas'])),audio_mode='silent')


def test_admitted_module_can_fulfil_layered_canvas_when_scene_audio_is_unavailable():
    requested=intent(required_features=['scale','coverage'],optional_features=['lighting'])
    resolved=admit_route(requested,dict(available=True,renderer_mode='hyperframes-film',
        configuration_sha256='e'*64,runtime_capabilities=dict(allowed_engines=['canvas'])),
        audio_mode='narration')
    assert resolved['route']=='hyperframes-film' and resolved['preferred_route']=='scene-space'
    assert resolved['audio_mode']=='narration'
    assert resolved['quality']['degraded_features']==[dict(feature='lighting',reason='capability_unavailable')]


def test_static_camera_and_physical_rotating_cache_are_not_false_promises():
    plan=dict(shots=[dict(camera=dict(keyframes=[dict(x=1,y=2,zoom=1),dict(x=2,y=2,zoom=1)]),
        local_animation=[])])
    with pytest.raises(ServiceError,match='spatial_rendering_camera_intent_changed'):
        validate_scene_intent_plan(intent(camera_mode='static'),plan)
    plan['shots'][0]['local_animation']=[dict(property='rotation',keyframes=[dict(value=0),dict(value=90)])]
    physical=intent(required_features=['coverage','lighting'],optional_features=[],
        light=dict(space='world',direction=[1,1,1],response='physical'))
    with pytest.raises(ServiceError,match='spatial_rendering_cached_light_motion_not_admitted'):
        validate_scene_intent_plan(physical,plan)
    physical['light']['response']='artistic'
    assert validate_scene_intent_plan(physical,plan)==plan


def test_production_freezes_plan_driven_route_and_replay_does_not_reselect(scene_env):
    env=scene_env
    env.renderer.capabilities=lambda owner:dict(render_capability(),authoring_constraints={})
    with env.store.transaction() as db:
        project=env.store._project(db,OWNER,env.project['id'])
        project['joint_plan']['rendering_intent']=intent()
        env.store._save(db,project)
    started=begin(env,key='foundation-route-first')
    assert started['rendering_route']['reason']=='explicit_space_engine_and_camera_requirements'
    assert started['rendering_route']['quality']['status']=='satisfied'
    frozen=deepcopy(started['rendering_route'])
    env.adapter.enabled=False
    replay=begin(env,key='foundation-route-first')
    assert replay['id']==started['id'] and replay['rendering_route']==frozen
    with pytest.raises(ServiceError,match='production_run_silent_requires_film'):
        begin(env,key='foundation-route-different')
    assert env.author.submissions==[] if hasattr(env.author,'submissions') else not env.author.jobs


def test_planar_plan_does_not_silently_enter_scene_route_when_it_is_the_only_adapter(scene_env):
    env=scene_env
    with env.store.transaction() as db:
        project=env.store._project(db,OWNER,env.project['id'])
        project['joint_plan']['rendering_intent']=intent(space_mode='planar',required_features=['scale','coverage'],optional_features=[])
        env.store._save(db,project)
    with pytest.raises(ServiceError,match='spatial_rendering_route_unavailable'):
        begin(env,key='foundation-planar-not-scene')
    with env.store.transaction() as db:
        assert db.execute('SELECT COUNT(*) FROM production_runs').fetchone()[0]==0
