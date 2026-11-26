"""Real HTTP planning/adoption/run admission with stopped local Agent peers.

Only external peers/readiness are fixtures. The resource-planning contract,
adopted plan mapping, SQLite custody, CSRF and route selection are real.
"""
from copy import deepcopy
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agent_service.contracts import digest
from video_workbench.app import create_app
from video_workbench.scene_space_renderer import render_capability
from test_joint_plan_api import Gateway, proposal
from test_resource_planning import ResourceGateway, planning_result, producers
from test_spatial_rendering_route import intent
from test_creative_film_module_contract import RUNTIME


OWNER = 'local-developer'
EMPTY = dict(nodes=[], budget=dict(max_attempts=1, max_attempts_per_node=1))


class JointPeer(Gateway):
    def collect(self, job_id, input_sha256, document):
        result = proposal(document, input_sha256)
        if 'rendering_foundation' in document:
            result['rendering_intent'] = intent(required_features=['scale', 'coverage'], optional_features=[])
        return result


class ResourcePeer(ResourceGateway):
    def collect(self, job_id, checksum, document):
        result = planning_result(document, with_node=self.with_node)
        for node in result['resource_plan']['nodes']:
            node['placement']['segment_id'] = document['joint_result']['scenes'][0]['segment_id']
        return result


class SceneReadiness:
    enabled = True

    def __init__(self):
        self.calls = []

    def freeze_configuration(self, owner, project_id):
        self.calls.append((owner, project_id))
        value = dict(available=True, renderer_mode='scene-space', fps=24, max_seconds=300,
                     audio_modes=['silent'], render_capability=render_capability())
        value['configuration_sha256'] = digest(value)
        return value


class FilmReadiness:
    enabled = True
    resource_client = object()
    configuration = dict(runtime_capabilities=dict(allowed_engines=['canvas'], assets={}))

    def __init__(self):
        self.calls = []

    def start(self):
        pass

    def stop(self):
        pass

    def freeze_configuration(self, owner, project_id):
        self.calls.append((owner, project_id))
        return dict(available=True, renderer_mode='hyperframes-film', fps=24, max_seconds=300,
                    configuration_sha256=digest(self.configuration))

    def preflight_resources(self, owner):
        return True


@pytest.fixture(scope='module')
def http(tmp_path_factory):
    # Keep live endpoints/credentials out of all local admission tests.
    with pytest.MonkeyPatch.context() as patch:
        for name in list(os.environ):
            if name.startswith(('VIDEO_', 'SERVICE_AUTH_')):
                patch.delenv(name)
        scene, film = SceneReadiness(), FilmReadiness()
        planner, resources = JointPeer(), ResourcePeer()
        capabilities = dict(schema='video.animation-runtime-capabilities.v1', allowed_engines=['canvas'], assets={},
            runtime_identity=dict(image_digest=RUNTIME['image_digest'], host_sha256='a' * 64,
                engine_runtime_sha256=RUNTIME['engine_runtime_sha256'], admission_id='synthetic-routing-fixture'))
        app = create_app(db_path=tmp_path_factory.mktemp('empty-resource') / 'workbench.db',
            local_mode=True, background=False, trusted_hosts={'testserver'}, gateway=object(),
            joint_plan_gateway=planner, joint_plan_enabled=True, resource_planning_gateway=resources,
            resource_producer_catalog=lambda owner: producers(), rendering_foundation_enabled=True,
            creative_capabilities=capabilities,
            scene_space_adapter=scene, film_creation=film, production_runs_enabled=True,
            motion_enabled=True, creative_enabled=False, process_enabled=False, refinement_enabled=False)
        with TestClient(app, base_url='https://testserver') as client:
            client.headers['Origin'] = 'https://testserver'
            client.headers['X-CSRF-Token'] = client.get('/api/session').json()['csrf_token']
            yield SimpleNamespace(client=client, app=app, scene=scene, film=film, resources=resources)


def adopted(http, *, nodes=False, foundation=True):
    http.resources.with_node = nodes
    http.app.state.joint_plan_store.rendering_foundation = foundation
    project = http.client.post('/api/projects', json=dict(title='Same scene routing',
        script='请求先等待。', style='One persistent scene.', production_mode='motion')).json()
    response = http.client.post(f'/api/projects/{project["id"]}/joint-plan',
        json=dict(expected_revision=project['revision']), headers={'Idempotency-Key': 'plan-' + uuid4().hex})
    assert response.status_code == 202, response.text
    for _ in range(3):
        http.app.state.joint_plan_workflow.tick()
    versions = http.app.state.joint_plan_store.project(OWNER, project['id'])['versions']
    assert versions, http.app.state.joint_plan_store.project(OWNER, project['id'])
    candidate = versions[0]
    assert candidate['resource_planning']['status'] == 'completed' and candidate['adoptable']
    assert bool(candidate['resource_plan']['nodes']) is nodes
    with http.app.state.store.transaction() as db:
        raw = db.execute('SELECT document FROM joint_plan_versions WHERE id=?', (candidate['id'],)).fetchone()[0]
    current = http.app.state.store.project(OWNER, project['id'])
    response = http.client.post(f'/api/projects/{project["id"]}/joint-plan/adopt', json=dict(
        expected_revision=current['revision'], version_id=candidate['id'], expected_head_version_id=None))
    assert response.status_code == 200, response.text
    project = response.json()
    return project, candidate, raw


def start(http, project, candidate, *, extra=None, key=None):
    body = dict(expected_revision=project['revision'], joint_plan_version_id=candidate['id'], audio_mode='silent')
    body.update(extra or {})
    key = key or 'production-' + uuid4().hex
    return http.client.post(f'/api/projects/{project["id"]}/production-runs', json=body,
                            headers={'Idempotency-Key': key}), body, key


def unchanged(http, project, candidate, raw):
    current = http.app.state.store.project(OWNER, project['id'])
    assert current['joint_plan'] == project['joint_plan']
    with http.app.state.store.transaction() as db:
        assert db.execute('SELECT document FROM joint_plan_versions WHERE id=?', (candidate['id'],)).fetchone()[0] == raw
    assert candidate['resource_planning']['raw_result']['resource_plan'] == candidate['resource_plan']
    source = current['joint_plan']['resource_plan_source']
    assert source['candidate_resource_plan_sha256'] == digest(candidate['resource_plan'])
    assert source['requirements_sha256'] == digest(current['joint_plan']['resource_plan'])


def test_valid_empty_planner_graph_uses_scene_with_and_without_spatial_intent_and_replays(http):
    for foundation in (True, False):
        project, candidate, raw = adopted(http, foundation=foundation)
        assert project['joint_plan']['resource_plan'] == EMPTY
        scene_calls, film_calls = len(http.scene.calls), len(http.film.calls)
        response, body, key = start(http, project, candidate)
        assert response.status_code == 202, response.text
        run = response.json()
        assert run['renderer_mode'] == 'scene-space'
        assert len(http.scene.calls) == scene_calls + 1 and len(http.film.calls) == film_calls
        with http.app.state.store.transaction() as db:
            journal = json.loads(db.execute('SELECT document FROM production_runs WHERE id=?', (run['id'],)).fetchone()[0])
        assert journal['frozen']['joint_plan']['resource_plan'] == EMPTY
        assert 'resource_plan_registration' not in journal
        unchanged(http, project, candidate, raw)
        http.scene.enabled = False
        replay = http.client.post(f'/api/projects/{project["id"]}/production-runs', json=body,
            headers={'Idempotency-Key': key})
        assert replay.status_code == 202 and replay.json()['id'] == run['id']
        assert len(http.scene.calls) == scene_calls + 1
        http.scene.enabled = True


def test_nonempty_and_explicit_resource_graphs_keep_the_film_route(http):
    for nodes, extra in ((True, None), (False, dict(resource_plan=deepcopy(EMPTY)))):
        project, candidate, raw = adopted(http, nodes=nodes)
        scene_calls, film_calls = len(http.scene.calls), len(http.film.calls)
        response, _, _ = start(http, project, candidate, extra=extra)
        assert response.status_code == 202, response.text
        run = response.json()
        assert run['renderer_mode'] == 'hyperframes-film'
        assert len(http.scene.calls) == scene_calls and len(http.film.calls) == film_calls + 1
        if nodes:
            assert run['resource_plan_registration']['status'] == 'pending'
        unchanged(http, project, candidate, raw)


def test_malformed_empty_graphs_cannot_bypass_resource_validation_or_admit_work(http):
    project, candidate, raw = adopted(http)
    malformed = [dict(nodes=[]), dict(nodes=[], budget=None),
        dict(nodes=[], budget=dict(max_attempts=True, max_attempts_per_node=1)),
        dict(nodes=[], budget=dict(max_attempts=1, max_attempts_per_node=2)),
        dict(nodes=[], budget=dict(max_attempts=33, max_attempts_per_node=1)),
        dict(EMPTY, unknown_resource='not permitted')]
    for value in malformed:
        with http.app.state.store.transaction() as db:
            damaged = http.app.state.store._project(db, OWNER, project['id'])
            damaged['joint_plan']['resource_plan'] = deepcopy(value)
            http.app.state.store._save(db, damaged)
            db.execute('UPDATE joint_plan_heads SET applied_binding_sha256=? WHERE owner=? AND project_id=?',
                (http.app.state.joint_plan_store._binding(damaged), OWNER, project['id']))
        scene_calls, film_calls = len(http.scene.calls), len(http.film.calls)
        response, _, _ = start(http, project, candidate)
        assert response.status_code == 422, response.text
        assert response.json()['detail']['code'] == 'production_resource_plan_invalid'
        assert len(http.scene.calls) == scene_calls and len(http.film.calls) == film_calls
        with http.app.state.store.transaction() as db:
            assert db.execute('SELECT COUNT(*) FROM production_runs WHERE project_id=?', (project['id'],)).fetchone()[0] == 0
            assert db.execute('SELECT document FROM joint_plan_versions WHERE id=?', (candidate['id'],)).fetchone()[0] == raw
