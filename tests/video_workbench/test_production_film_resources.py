"""Normal run shared-resource graph routing; stopped synthetic peers, no model.

These checks establish orchestration and immutable custody, not a real render,
producer/consumer admission or aesthetic acceptance.
"""
from copy import deepcopy
import json

import pytest

from agent_service.contracts import ServiceError, digest
from video_workbench.manim_film_admission import CONTEXT_SCHEMA, helpers
from video_workbench.manim_method_resources import PRODUCER_REQUIREMENT
from video_workbench.production_resource_stage import audio_node_id
from video_workbench.hyperframes_film_snapshot import decode_film_execution_snapshot
from test_film_authoring_resources import ResourceClient, RESOURCE_IMAGE
from test_production_film_runs import env, audio_phase, finish_module, RUNTIME
from test_production_runs import OWNER, children


class PreparedResourceClient(ResourceClient):
    def get(self, owner, operation_id):
        self.calls.append(('get', operation_id))
        if operation_id not in self.requests:
            raise ServiceError('not_found', 404)
        value = self.requests[operation_id]
        return dict(operation_id=operation_id, project_id=value['project_id'], node_id=value['node_id'],
            request_sha256=digest(value), status=self.state, stop_proven=self.proven,
            descriptor=self.descriptors[operation_id], resource_image=RESOURCE_IMAGE)

    def cancel_reservation(self, owner, request):
        self.calls.append(('cancel-reservation', request['operation_id']))
        self.requests[request['operation_id']] = deepcopy(request)
        return dict(operation_id=request['operation_id'], project_id=request['project_id'],
            node_id=request['node_id'], request_sha256=digest(request), status='cancelled', stop_proven=True)


@pytest.fixture
def resources(env):
    context = dict(schema=CONTEXT_SCHEMA, producer=PRODUCER_REQUIREMENT, resource_image=RESOURCE_IMAGE,
        helpers=helpers(), admission_sha256='e' * 64, formal_release=dict(image=env.creation.image,
            runtime_fingerprint=env.creation.runtime, trusted_host_sha256=env.creation.hosts))
    env.creation.configuration['resource_authoring'] = context
    env.creation.configuration['sha256'] = digest({key: value
        for key, value in env.creation.configuration.items() if key != 'sha256'})
    env.facade.identity = env.creation.configuration_sha256
    env.client = PreparedResourceClient(context)
    env.facade.resource_client = env.client
    env.resource_preflights = []
    def preflight(owner):
        assert owner == OWNER
        env.resource_preflights.append(owner)
        return True
    env.facade.preflight_resources = preflight
    return env


def plan(env, *, index=1, frames=12, start=6):
    return dict(nodes=[dict(id='geometry', kind='animation', producer='manim',
        inputs=dict(resource_authoring=dict(kind='manim', width=320, height=180,
            frame_count=frames, global_start_frame=0, seed=7)), dependencies=[],
        objective='Explain original geometry with a transparent authored resource.',
        required_capabilities=['manim'], key_effects=['area-conservation'],
        local_timing=dict(fps=24, frame_count=frames),
        placement=dict(scene_id=env.project['scenes'][index]['id'], start_frame=start))],
        budget=dict(max_attempts=1, max_attempts_per_node=1))


def request(env, selected):
    project = env.store.project(OWNER, env.project['id'])
    return dict(expected_revision=project['revision'], joint_plan_version_id='adopted-plan-1',
        resource_plan=deepcopy(selected))


def no_children(env):
    assert not children(env, 'audio_operations')
    assert not children(env, 'film_module_operations')
    assert not children(env, 'film_render_operations')


def starts(env):
    return [call for call in env.client.calls if call[0] == 'start']


def test_shared_graph_finishes_every_resource_before_first_module_and_uses_actual_recording(resources, monkeypatch):
    env = resources
    with env.store.transaction() as db:
        project = env.store._project(db, OWNER, env.project['id'])
        project['scenes'][1]['speech_settings'] = dict(pronunciation={'原文': '讲解'})
        db.execute('UPDATE joint_plan_heads SET applied_binding_sha256=? WHERE owner=? AND project_id=?',
            (env.joint._binding(project), OWNER, project['id']))
        env.store._save(db, project)
    env.project = env.store.project(OWNER, env.project['id'])
    first_scene, second_scene = env.project['scenes']
    selected = plan(env)
    body = request(env, selected)
    run = env.runs.start(OWNER, env.project['id'], body, 'shared-resource-normal-route')
    body['resource_plan']['nodes'][0]['inputs']['resource_authoring']['frame_count'] = 60
    audio_phase(env)
    state = env.runs.get(OWNER, run['id'])
    assert state['phase'] == 'resources' and not children(env, 'film_module_operations')
    view = env.runs.resource_stage.ledger.get(OWNER, run['id'])
    assert view['usage'] == {'attempts': 1} and len(starts(env)) == 1
    node = view['nodes']['geometry']
    task = node['task']
    assert task['inputs']['resource_authoring']['frame_count'] == 12
    assert 'placement' not in task
    recording = task['dependency_outputs'][audio_node_id(second_scene['id'])]
    assert recording['producer'] == 'verified-recording'
    assert recording['content']['narration'] == second_scene['narration']
    assert recording['content']['spoken_text'] == second_scene['narration'].replace('原文', '讲解')
    assert recording['content']['text_scope'] == 'bound_narration_input_not_asr_transcript'
    assert recording['local_timing']['duration_seconds'] == 2
    assert all(view['nodes'][audio_node_id(scene['id'])]['status'] == 'completed'
        for scene in env.project['scenes'])
    remote = next(iter(env.client.requests.values()))
    assert 'film_sha256' not in remote and remote['resource_authoring']['global_start_frame'] == 0
    assert recording['content']['audio_sha256'] in remote['guidance']
    assert second_scene['narration'] in remote['guidance']
    assert 'not_word_level_alignment' in remote['guidance']

    env.client.state, env.client.proven = 'completed', True
    env.runs.tick()
    ready = env.runs.resource_stage.ledger.get(OWNER, run['id'])['nodes']['geometry']['output']
    assert ready['content']['expression_acceptance'] == 'not_assessed'
    first = children(env, 'film_module_operations')[0]
    assert first['scene_id'] == first_scene['id'] and not first['resource_references']
    finish_module(env, first['id'])
    env.runs.tick()
    second = children(env, 'film_module_operations')[1]
    assert second['scene_id'] == second_scene['id']
    reference = second['request']['resource_references'][0]
    assert reference['resource_version_id'] == ready['resource_version_id']
    assert reference['placement']['global_start_frame'] == 48  # 42 real first-window frames + 6
    assert 'placementStartFrame:48' in second['document']['core_task']['scene']['instruction']
    assert not second.get('resource_authoring') and 'resource_authoring' not in second['request']
    assert len(starts(env)) == 1 and len(children(env, 'audio_operations')) == 2
    assert not children(env, 'film_render_operations')
    import test_production_film_runs as fixtures
    original = fixtures.raw_result
    def authored(document):
        result = original(document)
        text = document['scene']['instruction']
        reference = json.loads(text.split('APP-FROZEN-MANIM-FILM-RESOURCE\n', 1)[1]
            .split('\nEND-APP-FROZEN-MANIM-FILM-RESOURCE', 1)[0])
        result['module']['assets'] = [name for name in reference['assets']
            if not name.endswith('manim_method_frames.mjs')]
        # This inert source declaration proves package custody only. The
        # independent browser admission separately verifies actual frame use.
        result['module']['files'][result['module']['entry']] += '\n// createManimFrameLayer fixture\n'
        return result
    monkeypatch.setattr(fixtures, 'raw_result', authored)
    finish_module(env, second['id'])
    env.runs.tick()
    render = children(env, 'film_render_operations')[0]
    snapshot = env.creation.render_snapshot(render['id'])
    files = decode_film_execution_snapshot(snapshot, expected_snapshot_sha256=snapshot['sha256'],
        runtime_fingerprint=RUNTIME, trusted_host_sha256=env.creation.hosts, image=env.creation.image)
    descriptor = ready['content']['descriptor']
    assert 'project/' + descriptor['manifest_path'] in files
    assert 'project/assets/manim_frame_layer.mjs' in files
    assert len(snapshot['module_sha256']) == 2 and len(snapshot['recording_sha256']) == 2
    assert not any(name.endswith('.mp4') for name in files)
    assert len(starts(env)) == 1


def test_resource_preflight_failure_precedes_paid_children_and_run_reservation(resources):
    env = resources
    def unavailable(owner):
        no_children(env)
        raise ServiceError('film_manim_producer_not_admitted', 503)
    env.facade.preflight_resources = unavailable
    with pytest.raises(ServiceError, match='film_manim_producer_not_admitted'):
        env.runs.start(OWNER, env.project['id'], request(env, plan(env)), 'shared-resource-not-ready')
    no_children(env)
    with env.store.transaction() as db:
        assert db.execute('SELECT count(*) FROM production_runs').fetchone()[0] == 0


def test_resource_clock_checked_against_real_recording_before_any_producer_or_module(resources):
    env = resources
    run = env.runs.start(OWNER, env.project['id'], request(env, plan(env, index=0, frames=48)),
        'shared-resource-real-clock')
    audio_phase(env)
    state = env.runs.get(OWNER, run['id'])
    assert state['status'] == 'failed' and state['error'] == 'production_resource_clock_outside_scene'
    assert not env.client.requests and not children(env, 'film_module_operations')
    assert len(children(env, 'audio_operations')) == 2


def test_registered_missing_ledger_never_recreates_or_submits_another_resource(resources):
    env = resources
    run = env.runs.start(OWNER, env.project['id'], request(env, plan(env)), 'shared-resource-lost-ledger')
    audio_phase(env)
    ledger = env.runs.resource_stage.ledger
    with ledger._connect() as db:
        db.execute('DELETE FROM resource_plans WHERE owner=? AND operation_id=?', (OWNER, run['id']))
    env.runs.tick()
    env.runs.tick()
    state = env.runs.get(OWNER, run['id'])
    assert state['blocked_reason'] == 'production_resource_ledger_missing'
    assert len(starts(env)) == 1 and not children(env, 'film_module_operations')
    with pytest.raises(ServiceError, match='resource_plan_not_found'):
        ledger.get(OWNER, run['id'])


def test_normal_run_uses_adopted_resource_sidecar_without_manual_api_field(resources):
    env = resources
    selected = plan(env)
    original = deepcopy(selected)
    original['nodes'][0]['placement'] = {'segment_id': 'segment-b', 'start_frame': 6}
    with env.store.transaction() as db:
        project = env.store._project(db, OWNER, env.project['id'])
        project['joint_plan']['resource_plan'] = selected
        # This fixture is a previously confirmed plan. The original result is
        # deliberately unchanged, as the real sidecar must preserve it.
        version = dict(id='adopted-plan-1', result={}, resource_plan=original)
        db.execute('INSERT INTO joint_plan_versions VALUES(?,?,?,?,?)',
            ('adopted-plan-1', OWNER, project['id'], 'fixture-plan-op', json.dumps(version)))
        db.execute('UPDATE projects SET document=? WHERE id=?', (json.dumps(project), project['id']))
    body = request(env, selected)
    body.pop('resource_plan')
    run = env.runs.start(OWNER, env.project['id'], body, 'shared-resource-confirmed-sidecar')
    audio_phase(env)
    state = env.runs.get(OWNER, run['id'])
    assert state['phase'] == 'resources' and state['current']
    assert len(starts(env)) == 1 and not children(env, 'film_module_operations')
    with env.store.transaction() as db:
        project = env.store._project(db, OWNER, env.project['id'])
        project['joint_plan']['resource_plan']['nodes'][0]['placement']['start_frame'] = 7
        db.execute('UPDATE projects SET document=? WHERE id=?', (json.dumps(project), project['id']))
    assert not env.runs.get(OWNER, run['id'])['current']


@pytest.mark.parametrize('invalid', ['scene', 'decoded', 'module_slots', 'local_clock'])
def test_invalid_resource_plan_is_rejected_before_paid_children(resources, invalid):
    env = resources
    selected = plan(env, frames=48 if invalid in {'decoded', 'module_slots'} else 12)
    if invalid == 'scene':
        selected['nodes'][0]['placement']['scene_id'] = 'unknown-scene'
        code = 'production_resource_placement_invalid'
    elif invalid == 'local_clock':
        selected['nodes'][0]['local_timing']['frame_count'] = 13
        code = 'production_resource_local_clock_required'
    else:
        second = deepcopy(selected['nodes'][0])
        second['id'] = 'another-geometry'
        if invalid == 'decoded':
            second['placement']['scene_id'] = env.project['scenes'][0]['id']
            selected['nodes'][0]['inputs']['resource_authoring'].update(width=960, height=540)
            second['inputs']['resource_authoring'].update(width=960, height=540)
            code = 'production_run_resource_budget_exceeded'
        else:
            code = 'production_run_resource_module_budget_exceeded'
        selected['nodes'].append(second)
        selected['budget']['max_attempts'] = 2
    with pytest.raises(ServiceError, match=code):
        env.runs.start(OWNER, env.project['id'], request(env, selected), 'shared-resource-invalid-' + invalid)
    no_children(env)
    assert not starts(env)


def test_cancel_drains_original_resource_without_starting_any_module(resources):
    env = resources
    run = env.runs.start(OWNER, env.project['id'], request(env, plan(env)), 'shared-resource-cancel')
    audio_phase(env)
    original = next(iter(env.client.requests))
    env.runs.cancel(OWNER, run['id'])
    env.runs.tick()
    state = env.runs.get(OWNER, run['id'])
    assert state['status'] == 'cancelled' and not state['draining']
    assert ('cancel-reservation', original) in env.client.calls
    assert len(starts(env)) == 1 and not children(env, 'film_module_operations')


def test_unknown_resource_submission_is_looked_up_once_without_new_paid_attempt(resources):
    env = resources
    env.client.lose_submit = True
    run = env.runs.start(OWNER, env.project['id'], request(env, plan(env)), 'shared-resource-unknown-submit')
    audio_phase(env)
    assert len(starts(env)) == 1 and not children(env, 'film_module_operations')
    env.client.state, env.client.proven = 'completed', True
    env.runs.tick()
    state = env.runs.get(OWNER, run['id'])
    assert state['phase'] == 'module' and len(starts(env)) == 1
    assert state['resources']['usage'] == {'attempts': 1}
