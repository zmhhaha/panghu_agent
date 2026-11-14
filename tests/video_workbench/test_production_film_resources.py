"""Normal run resource routing with synthetic local admission; no paid work.

These tests verify business orchestration only. They do not establish a real
producer/consumer admission, a rendered image, or developer acceptance.
"""
from copy import deepcopy

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.manim_film_admission import CONTEXT_SCHEMA, helpers
from video_workbench.manim_method_resources import PRODUCER_REQUIREMENT
from test_production_film_runs import env, audio_phase, finish_module
from test_production_runs import OWNER, children, seed


@pytest.fixture
def resources(env):
    context = dict(schema=CONTEXT_SCHEMA, producer=PRODUCER_REQUIREMENT,
        resource_image='registry.internal/manim-resource:latest@sha256:' + 'a' * 64,
        helpers=helpers(), admission_sha256='e' * 64,
        formal_release=dict(image=env.creation.image, runtime_fingerprint=env.creation.runtime,
            trusted_host_sha256=env.creation.hosts))
    env.creation.configuration['resource_authoring'] = context
    env.creation.configuration['sha256'] = digest({key: value
        for key, value in env.creation.configuration.items() if key != 'sha256'})
    env.facade.identity = env.creation.configuration_sha256
    env.resource_preflights = []
    def preflight(owner):
        assert owner == OWNER
        assert not children(env, 'audio_operations')
        assert not children(env, 'film_module_operations')
        env.resource_preflights.append(owner)
        return True
    env.facade.preflight_resources = preflight
    return env


def request(env, selected):
    project = env.store.project(OWNER, env.project['id'])
    return dict(expected_revision=project['revision'], joint_plan_version_id='adopted-plan-1',
        resource_authoring=deepcopy(selected))


def no_children(env):
    assert not children(env, 'audio_operations')
    assert not children(env, 'film_module_operations')
    assert not children(env, 'film_render_operations')


def test_resource_preflight_failure_precedes_paid_children_and_run_reservation(resources):
    env = resources
    def unavailable(owner):
        no_children(env)
        raise ServiceError('film_manim_producer_not_admitted', 503)
    env.facade.preflight_resources = unavailable
    with pytest.raises(ServiceError, match='film_manim_producer_not_admitted'):
        env.runs.start(OWNER, env.project['id'], request(env,
            {env.project['scenes'][0]['id']: dict(kind='manim')}), 'resource-preflight-failed')
    no_children(env)
    with env.store.transaction() as db:
        assert db.execute('SELECT count(*) FROM production_runs').fetchone()[0] == 0


@pytest.mark.parametrize('invalid', ['scene', 'decoded', 'film_asset_slots'])
def test_invalid_resource_scene_or_aggregate_budget_is_rejected_before_children(resources, invalid):
    env = resources
    if invalid == 'film_asset_slots':
        env.project = seed(env.store, env.joint, count=3)
    selected = {scene['id']: dict(kind='manim', width=320, height=180, frame_count=48)
        for scene in env.project['scenes']}
    code = 'production_run_resource_budget_exceeded'
    if invalid == 'scene':
        selected = {'unknown-scene': dict(kind='manim')}
        code = 'production_run_resource_scene_invalid'
    elif invalid == 'decoded':
        for value in selected.values():
            # Each 94.9 MiB resource is valid alone; the pair exceeds 128 MiB.
            value.update(width=960, height=540)
    with pytest.raises(ServiceError, match=code):
        env.runs.start(OWNER, env.project['id'], request(env, selected), 'invalid-resource-' + invalid)
    assert env.resource_preflights == [OWNER]
    no_children(env)


def test_frozen_resource_mapping_routes_only_selected_module_after_original_audio(resources):
    env = resources
    first_scene, second_scene = env.project['scenes']
    selected = {second_scene['id']: dict(kind='manim', width=320, height=180, frame_count=12)}
    body = request(env, selected)
    run = env.runs.start(OWNER, env.project['id'], body, 'resource-mapping-route')
    body['resource_authoring'][second_scene['id']]['frame_count'] = 60
    assert run['resource_authoring'] == selected
    assert run['resource_work_budget'] == dict(recipe_jobs=1, resource_executions=1)
    audio_phase(env)
    first = children(env, 'film_module_operations')[0]
    assert first['scene_id'] == first_scene['id']
    assert 'resource_authoring' not in first['request']
    assert 'resource_authoring' not in first
    finish_module(env, first['id'])
    env.runs.tick()
    second = children(env, 'film_module_operations')[1]
    assert second['scene_id'] == second_scene['id']
    assert second['request']['resource_authoring'] == selected[second_scene['id']]
    assert second['resource_authoring']['global_start_frame'] == 42
    assert second['resource_authoring']['frame_count'] == 12
    assert second['resource_submit_attempted'] is False
    state = env.runs.get(OWNER, run['id'])
    assert state['resource_authoring'] == selected
    assert len(children(env, 'audio_operations')) == 2
    assert not children(env, 'film_render_operations')
    assert not children(env, 'motion_operations')


def test_resource_clock_rechecked_against_measured_recording_before_module_creation(resources):
    env = resources
    # The planned scene is 8s, but the unchanged real recording plus pauses is
    # 42 frames. A 48-frame resource cannot be shifted into the next scene.
    body = request(env, {env.project['scenes'][0]['id']:
        dict(kind='manim', width=320, height=180, frame_count=48)})
    run = env.runs.start(OWNER, env.project['id'], body, 'resource-actual-clock-check')
    audio_phase(env)
    state = env.runs.get(OWNER, run['id'])
    assert state['status'] == 'failed'
    assert state['error'] == 'film_resource_clock_outside_scene'
    assert len(children(env, 'audio_operations')) == 2
    assert not children(env, 'film_module_operations')
    assert not children(env, 'film_render_operations')
