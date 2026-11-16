"""Formal resource orchestration with synthetic stopped output, no model/media."""
from copy import deepcopy
import json

import pytest

from agent_service.contracts import ServiceError, digest
from agent_service.engines import StopEvidence
from video_workbench.film_authoring_resources import expected_scope
from video_workbench.film_creation_workflow import FilmCreationWorkflow
from video_workbench.manim_film_admission import CONTEXT_SCHEMA, helpers
from video_workbench.manim_generated_assets import freeze_generated_assets
from video_workbench.manim_method_resources import method_resource, PRODUCER_REQUIREMENT
from test_manim_resource import compiled, request, IMAGE
from test_film_creation_workflow import env, module_request, render_request, OWNER

RESOURCE_IMAGE = 'registry.internal/manim-resource:latest@' + IMAGE


class ResourceClient:
    def __init__(self, context):
        self.context, self.calls, self.requests = context, [], {}
        self.state, self.proven, self.lose_submit = 'queued', False, False
        self.descriptors, self.envelopes = {}, {}

    def capabilities(self, owner):
        return dict(schema='video.manim-film-resource-capabilities.v1', enabled=True,
            producer=PRODUCER_REQUIREMENT, helpers=self.context['helpers'], resource_image=RESOURCE_IMAGE,
            admission_sha256=self.context['admission_sha256'])

    def start(self, owner, value):
        self.calls.append(('start', value['operation_id']))
        self.requests[value['operation_id']] = deepcopy(value)
        scope = expected_scope(owner, value['operation_id'])
        options = value['resource_authoring']
        recipe = request(resource_id='resource_' + value['operation_id'].replace('-', '')[:24],
            **{key: item for key, item in options.items() if key != 'kind'})
        recipe, files, _ = compiled(recipe)
        envelope = freeze_generated_assets(files, recipe, **scope, runtime_image_digest=IMAGE,
            stop_evidence=StopEvidence(execution_id=scope['operation_id'],
                all_writers_stopped=True, resource_ids=('synthetic-stopped-cairo',)))
        descriptor, _ = method_resource(envelope, expected_sha256=envelope['sha256'],
            expected_scope=scope, image_digest=IMAGE)
        self.envelopes[value['operation_id']], self.descriptors[value['operation_id']] = envelope, descriptor
        if self.lose_submit:
            raise ServiceError('fixture_lost_resource_receipt', 503)
        return self.get(owner, value['operation_id'])

    def get(self, owner, operation_id):
        self.calls.append(('get', operation_id))
        if operation_id not in self.requests:
            raise ServiceError('not_found', 404)
        value = self.requests[operation_id]
        return dict(operation_id=operation_id, project_id=value['project_id'], scene_id=value['scene_id'],
            film_sha256=value['film_sha256'], request_sha256=digest(value), status=self.state,
            stop_proven=self.proven, descriptor=self.descriptors[operation_id], resource_image=RESOURCE_IMAGE)

    def envelope(self, owner, operation_id):
        self.calls.append(('envelope', operation_id))
        return self.envelopes[operation_id]

    def cancel(self, owner, operation_id):
        self.calls.append(('cancel', operation_id))

    def cancel_reservation(self, owner, request):
        self.calls.append(('cancel-reservation', request['operation_id']))
        self.requests[request['operation_id']] = deepcopy(request)
        return dict(operation_id=request['operation_id'], project_id=request['project_id'],
            scene_id=request['scene_id'], film_sha256=request['film_sha256'],
            request_sha256=digest(request), status='cancelled', stop_proven=True)


@pytest.fixture
def resources(env):
    context = dict(schema=CONTEXT_SCHEMA, producer=PRODUCER_REQUIREMENT, resource_image=RESOURCE_IMAGE,
        helpers=helpers(), admission_sha256='e' * 64, formal_release=dict(image=env.creation.image,
            runtime_fingerprint=env.creation.runtime, trusted_host_sha256=env.creation.hosts))
    env.creation.configuration['resource_authoring'] = context
    env.creation.configuration['sha256'] = digest({key: item for key, item in env.creation.configuration.items() if key != 'sha256'})
    env.client = ResourceClient(context)
    env.workflow.resource_client = env.client
    return env


def start(env, index=0, predecessor=None):
    body = module_request(env, index, predecessor)
    body['resource_authoring'] = dict(kind='manim', width=320, height=180, frame_count=3, seed=7)
    return env.workflow.start_module(OWNER, env.project['id'], env.project['scenes'][index]['id'],
        body, 'formal-manim-module-' + str(index))


def declare_resource_in_synthetic_author(monkeypatch):
    import test_film_creation_workflow as fixture
    original = fixture.raw_result
    def authored(document):
        result = original(document)
        text = document['scene']['instruction']
        if 'APP-FROZEN-MANIM-FILM-RESOURCE\n' in text:
            reference = json.loads(text.split('APP-FROZEN-MANIM-FILM-RESOURCE\n', 1)[1]
                .split('\nEND-APP-FROZEN-MANIM-FILM-RESOURCE', 1)[0])
            result['module']['assets'] = [name for name in reference['assets'] if not name.endswith('manim_method_frames.mjs')]
            # This is deliberately inert synthetic source, not a visual render.
            result['module']['files'][result['module']['entry']] += '\n// createManimFrameLayer resource-fixture\n'
        return result
    monkeypatch.setattr(fixture, 'raw_result', authored)


def test_producer_completes_before_source_only_author_and_custody_is_frozen(resources, monkeypatch):
    env = resources
    declare_resource_in_synthetic_author(monkeypatch)
    operation = start(env)
    env.workflow.tick()
    assert [call[0] for call in env.client.calls].count('start') == 1
    assert not env.gateway.calls and not env.catalog_calls
    env.client.state, env.client.proven = 'completed', True
    env.workflow.tick()
    result = env.workflow.get_module(OWNER, operation['id'])
    assert result['status'] == 'completed' and result['version_id']
    package = env.creation.module_package(OWNER, result['version_id'])
    descriptor = env.client.descriptors[operation['id']]
    assert descriptor['manifest_path'] in package['input_document']['assets']
    assert 'assets/manim_frame_layer.mjs' in package['result']['module']['assets']
    assert descriptor['sha256'] in package['input_document']['scene']['instruction']
    assert len([call for call in env.gateway.calls if call[0] == 'submit']) == 1
    with env.store.transaction() as db:
        assert db.execute('SELECT count(*) FROM film_module_resources').fetchone()[0] == 1


def test_unknown_resource_submit_recovers_same_operation_once_after_restart(resources, monkeypatch):
    env = resources
    declare_resource_in_synthetic_author(monkeypatch)
    env.client.lose_submit = True
    operation = start(env)
    env.workflow.tick()
    assert env.workflow.get_module(OWNER, operation['id'])['status'] == 'queued'
    env.client.state, env.client.proven = 'completed', True
    resumed = FilmCreationWorkflow(env.creation, env.gateway, env.adapter, env.renderer,
        enabled=True, render_readiness=lambda: True, resource_client=env.client)
    resumed.tick()
    assert resumed.get_module(OWNER, operation['id'])['status'] == 'completed'
    assert len([call for call in env.client.calls if call[0] == 'start']) == 1


def test_cancellation_drains_original_resource_before_marking_stopped(resources):
    env = resources
    operation = start(env)
    env.workflow.tick()
    env.workflow.cancel_module(OWNER, operation['id'])
    env.workflow.tick()
    current = env.workflow.get_module(OWNER, operation['id'])
    assert current['status'] == 'cancelling' and current['stop_proven'] is False
    assert ('cancel', operation['id']) in env.client.calls and not env.gateway.calls
    env.client.state, env.client.proven = 'cancelled', True
    env.workflow.tick()
    current = env.workflow.get_module(OWNER, operation['id'])
    assert current['status'] == 'cancelled' and current['stop_proven'] is True
    assert not env.gateway.calls


def test_false_terminal_producer_state_does_not_release_cancel_or_submit_module(resources):
    env = resources
    operation = start(env)
    env.workflow.tick()
    env.client.state = 'failed'
    env.workflow.tick()
    current = env.workflow.get_module(OWNER, operation['id'])
    assert current['status'] == 'running' and current['stop_proven'] is False
    assert current['error'] == 'film_resource_stop_unproven' and not env.gateway.calls


def test_ignoring_explicit_generated_resource_rejects_author_result(resources):
    env = resources
    operation = start(env)
    env.workflow.tick()
    env.client.state, env.client.proven = 'completed', True
    env.workflow.tick()
    current = env.workflow.get_module(OWNER, operation['id'])
    assert current['status'] == 'failed' and current['error'] == 'film_resource_not_declared_by_module'


def test_resource_assets_follow_exact_predecessor_and_survive_single_film_assembly(resources, monkeypatch):
    env = resources
    declare_resource_in_synthetic_author(monkeypatch)
    first = start(env)
    env.workflow.tick()
    env.client.state, env.client.proven = 'completed', True
    env.workflow.tick()
    version = env.workflow.get_module(OWNER, first['id'])['version_id']
    body = module_request(env, 1, version)
    second = env.workflow.start_module(OWNER, env.project['id'], env.project['scenes'][1]['id'], body,
        'formal-resource-second-native')
    env.workflow.tick()
    current = env.creation.operation('module', second['id'])
    assert current['status'] == 'completed'
    assert env.client.descriptors[first['id']]['manifest_path'] in current['document']['assets']
    assert 'APP-FROZEN-PREDECESSOR-APPEARANCE' in current['document']['scene']['instruction']
    assert len([call for call in env.client.calls if call[0] == 'start']) == 1
    modules = [env.workflow.get_module(OWNER, value['id']) for value in (first, second)]
    render = env.workflow.start_render(OWNER, env.project['id'], render_request(env, modules),
        'formal-resource-single-film')
    snapshot = env.creation.render_snapshot(render['id'])
    from video_workbench.hyperframes_film_snapshot import decode_film_execution_snapshot
    files = decode_film_execution_snapshot(snapshot, expected_snapshot_sha256=snapshot['sha256'],
        runtime_fingerprint=env.creation.runtime, trusted_host_sha256=env.creation.hosts, image=env.creation.image)
    assert len(snapshot['module_sha256']) == 2 and len(snapshot['recording_sha256']) == 2
    assert 'project/' + env.client.descriptors[first['id']]['manifest_path'] in files
    assert sum(name.endswith('manim_frame_layer.mjs') for name in files) == 1
    assert not any(name.endswith('.mp4') for name in files)


@pytest.mark.parametrize('cancelled', [False, True])
def test_crash_before_remote_reservation_reuses_original_unpaid_intent(resources, cancelled):
    env = resources
    operation = start(env)
    env.creation.claim_resource(operation['id'])  # crash before any remote POST
    if cancelled:
        env.workflow.cancel_module(OWNER, operation['id'])
    env.workflow.tick()
    calls = [name for name, _ in env.client.calls]
    if cancelled:
        assert 'cancel-reservation' in calls and 'start' not in calls
        assert env.workflow.get_module(OWNER, operation['id'])['status'] == 'cancelled'
    else:
        assert calls.count('start') == 1
    assert not env.gateway.calls


def test_confirmed_remote_record_is_never_recreated_if_it_disappears(resources):
    env = resources
    operation = start(env)
    env.workflow.tick()
    env.client.requests.clear()
    env.workflow.tick()
    assert len([call for call in env.client.calls if call[0] == 'start']) == 1
    assert env.workflow.get_module(OWNER, operation['id'])['status'] == 'running'


def test_oversized_frozen_resource_request_is_rejected_before_local_claim(resources):
    env = resources
    body = module_request(env)
    body.update(instruction='x' * 15990,
        resource_authoring=dict(kind='manim', width=320, height=180, frame_count=3))
    operation = env.workflow.start_module(OWNER, env.project['id'], env.project['scenes'][0]['id'],
        body, 'formal-resource-too-long')
    env.workflow.tick()
    current = env.creation.operation('module', operation['id'])
    assert current['status'] == 'failed' and not current.get('resource_submit_attempted')
    assert not env.client.requests and not env.gateway.calls


def test_normal_production_http_contract_preserves_optional_resource_intent():
    from video_workbench.app import ProductionRunRequest
    request = dict(expected_revision=3, joint_plan_version_id='plan-1',
        resource_authoring={'scene-1': dict(kind='manim', frame_count=48)})
    assert ProductionRunRequest.model_validate(request).model_dump(exclude_none=True) == request
    assert ProductionRunRequest(expected_revision=3,
        joint_plan_version_id='plan-1').model_dump(exclude_none=True) == {
            'expected_revision': 3, 'joint_plan_version_id': 'plan-1'}


@pytest.mark.parametrize('mutation', ['owner', 'scene', 'source', 'helper'])
def test_changed_resource_scope_recipe_or_helper_never_enters_module_input(resources, mutation):
    env = resources
    operation = start(env)
    env.workflow.tick()
    env.client.state, env.client.proven = 'completed', True
    envelope = env.client.envelopes[operation['id']]
    descriptor = env.client.descriptors[operation['id']]
    if mutation == 'owner':
        envelope['scope']['owner'] = 'another-owner'
    elif mutation == 'scene':
        env.client.requests[operation['id']]['scene_id'] = 'another-scene'
    elif mutation == 'source':
        envelope['request']['source'] += '# changed\n'
    else:
        descriptor['helpers']['manim_frame_layer.mjs'] = 'b' * 64
    env.workflow.tick()
    current = env.workflow.get_module(OWNER, operation['id'])
    assert current['status'] != 'completed' and not env.gateway.calls
