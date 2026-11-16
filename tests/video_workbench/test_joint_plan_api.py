"""Joint authoring can restructure a project without silently approving media."""
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agent_service.contracts import ServiceError, digest
from video_workbench.app import create_app
from video_workbench.explanation_store import visual_generation_description
from video_workbench.animation_method_production import MethodProductionAdapter


def test_dedicated_joint_client_is_separate_and_closes_without_exposing_credential(tmp_path, monkeypatch):
    import importlib
    module = importlib.import_module('video_workbench.app')
    clients = []
    class Client:
        def __init__(self, url, token):
            self.url, self.token, self.closed = url, token, False
            clients.append(self)
        def close(self): self.closed = True
    monkeypatch.setattr(module, 'AgentClient', Client)
    monkeypatch.setenv('VIDEO_JOINT_PLAN_AGENT_URL', 'http://joint-agent:8000')
    monkeypatch.setenv('VIDEO_JOINT_PLAN_AGENT_TOKEN', 'j' * 32)
    monkeypatch.delenv('VIDEO_JOINT_PLAN_AGENT_TOKEN_FILE', raising=False)
    app = create_app(db_path=tmp_path / 'dedicated.db', gateway=object(), background=False,
        local_mode=True, joint_plan_enabled=True, trusted_hosts={'testserver'})
    with TestClient(app) as client:
        session = client.get('/api/session')
        assert session.json()['joint_plan_configured'] is True and 'j' * 32 not in session.text
        assert app.state.joint_plan_workflow.gateway.client is clients[0]
    assert clients[0].url == 'http://joint-agent:8000' and clients[0].closed


def proposal(document, checksum):
    script = document['project']['script']
    halves = [part + '。' for part in script.split('。') if part]
    return dict(schema_version='video-joint-plan-result.v1', input_sha256=checksum,
        summary='用请求的旅程说明排队和执行。', understanding_goal='理解任务如何得到处理。',
        visual_style='统一的蓝色任务卡片和清晰的空间关系。',
        continuity='同一张任务卡片从入口经过队列，再抵达执行器。',
        scenes=[dict(segment_id=f'segment-{i}', source_scene_ids=[s['scene_id'] for s in document['scenes']],
            title=f'任务旅程 {i}', narration=text, visual_description='任务卡片进入队列并交给执行器。',
            duration_seconds=8, pronunciation={}, key_points=['先排队', '再处理'],
            expression='跟随同一张卡片在空间中的移动，而不是切换标题页。',
            visual_steps=['卡片从入口进入', '卡片抵达队列'], delivery_note='讲到等待时稍停顿。',
            source_quotes=[text]) for i, text in enumerate(halves)],
        method_decision=(dict(delivery_sha256=document['method_delivery']['sha256'], decisions=[])
                         if document['method_delivery'] is not None else None), warnings=[])


class Gateway:
    def __init__(self):
        self.submits = 0
        self.uploads = 0
        self.jobs = {}
        self.lose_response = False

    def upload(self, document):
        self.uploads += 1
        return dict(input_id=str(uuid4()), sha256=digest(document),
                    path='joint-plan-input.json', role='joint_plan_input')

    def submit(self, operation_id, document, deadline_at, input_ref=None):
        self.submits += 1
        assert input_ref['sha256'] == digest(document)
        job = dict(job_id=str(uuid4()), state='running')
        self.jobs[operation_id] = job
        if self.lose_response:
            raise ServiceError('service_timeout', 504)
        return job

    def lookup(self, operation_id):
        return self.jobs[operation_id]

    def poll(self, job_id):
        return dict(job_id=job_id, state='completed')

    def collect(self, job_id, input_sha256, input_document):
        return proposal(input_document, input_sha256)

    def cancel(self, job_id):
        return dict(job_id=job_id, state='cancelling')


@pytest.fixture
def env(tmp_path):
    gateway = Gateway()
    app = create_app(db_path=tmp_path / 'db', local_mode=True, background=False,
                     joint_plan_gateway=gateway, joint_plan_enabled=True, trusted_hosts={'testserver'})
    with TestClient(app) as client:
        client.headers['X-CSRF-Token'] = client.get('/api/session').json()['csrf_token']
        project = client.post('/api/projects', json=dict(title='任务旅程',
            script='任务先进入队列。执行器再处理任务。', style='清晰图解')).json()
        yield client, app, gateway, project


def start(client, project, key='joint-plan-intent-1', **fields):
    return client.post(f'/api/projects/{project["id"]}/joint-plan',
        json=dict(expected_revision=project['revision'], **fields), headers={'Idempotency-Key': key})


def adopt(client, app, project, candidate):
    current = app.state.store.project('local-developer', project['id'])
    return client.post(f'/api/projects/{project["id"]}/joint-plan/adopt', json=dict(
        expected_revision=current['revision'], version_id=candidate['id'], expected_head_version_id=None))


def test_empty_project_can_plan_before_any_storyboard_then_explicitly_adopt(env):
    client, app, gateway, project = env
    assert client.get('/api/session').json()['joint_plan_configured'] is True
    response = start(client, project, preserve_script=True)
    assert response.status_code == 202
    app.state.joint_plan_workflow.tick()
    state = client.get(f'/api/projects/{project["id"]}/joint-plan').json()
    assert state['operation']['status'] == 'completed'
    assert state['preserve_script'] is True
    candidate = state['versions'][0]
    assert candidate['adoptable'] and not candidate['stale']
    assert app.state.store.project('local-developer', project['id'])['scenes'] == []
    result = adopt(client, app, project, candidate)
    assert result.status_code == 200
    adopted = result.json()
    assert len(adopted['scenes']) == 2
    assert ''.join(s['narration'] for s in adopted['scenes']) == project['script']
    assert all(s['head_version_id'] is None and s['head_audio_version_id'] is None for s in adopted['scenes'])
    assert all(s['joint_plan']['expression'] in visual_generation_description(s) for s in adopted['scenes'])
    assert all('跨段衔接意图' in visual_generation_description(s) for s in adopted['scenes'])
    assert not app.state.joint_plan_store.project('local-developer', project['id'])['head_stale']
    assert gateway.submits == 1
    assert app.state.store.active_operations() == []
    assert app.state.store.active_audio_operations() == []


def test_restructure_keeps_historical_media_and_archives_original_scenes(env):
    client, app, _, project = env
    store = app.state.store
    project = store.save_scene('local-developer', project['id'], dict(title='旧结构',
        narration=project['script'], visual_description='旧卡片', duration_seconds=15, format='svg'))
    old_id = project['scenes'][0]['id']
    op = store.start('local-developer', project['id'], 'scene', {'expected_revision': project['revision']},
                     'historical-static', scene_id=old_id)
    source = '<svg xmlns="http://www.w3.org/2000/svg"><text>历史画面</text></svg>'
    store.complete(op['id'], dict(format='svg', source=source, summary='旧画面'))
    project = store.project('local-developer', project['id'])
    media_id = project['scenes'][0]['versions'][0]['id']
    assert start(client, project).status_code == 202
    app.state.joint_plan_workflow.tick()
    candidate = app.state.joint_plan_store.project('local-developer', project['id'])['versions'][0]
    result = adopt(client, app, project, candidate)
    assert result.status_code == 200
    assert old_id not in [s['id'] for s in result.json()['scenes']]
    assert store.version('local-developer', media_id)['source'] == source
    with store.transaction() as db:
        archived = json.loads(db.execute('SELECT document FROM joint_plan_adoptions').fetchone()[0])
        assert archived['scenes'][0]['id'] == old_id
        assert archived['scenes'][0]['versions'][0]['id'] == media_id
    with pytest.raises(ServiceError) as error:
        store.version('other-owner', media_id)
    assert error.value.status == 404


def test_input_change_blocks_old_candidate_and_keeps_scene_direction_binding(env):
    client, app, _, project = env
    assert start(client, project).status_code == 202
    app.state.joint_plan_workflow.tick()
    candidate = app.state.joint_plan_store.project('local-developer', project['id'])['versions'][0]
    with app.state.store.transaction() as db:
        changed = app.state.store._project(db, 'local-developer', project['id'])
        changed['script'] += '这是后来增加的内容。'
        app.state.store._save(db, changed)
    response = adopt(client, app, project, candidate)
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'joint_plan_stale'
    assert app.state.joint_plan_store.project('local-developer', project['id'])['versions'][0]['stale']


def test_one_paid_submit_survives_lost_response_and_idempotent_replay(env):
    client, app, gateway, project = env
    gateway.lose_response = True
    first = start(client, project)
    assert first.status_code == 202
    replay = start(client, project)
    assert replay.json()['id'] == first.json()['id']
    app.state.joint_plan_workflow.tick()
    assert app.state.joint_plan_store.operation(first.json()['id'])['error'] == 'submission_uncertain'
    app.state.joint_plan_workflow.tick()
    assert gateway.submits == gateway.uploads == 1
    assert app.state.joint_plan_store.operation(first.json()['id'])['status'] == 'completed'


def test_cancel_before_submit_never_calls_agent(env):
    client, app, gateway, project = env
    operation = start(client, project).json()
    assert client.post(f'/api/joint-plan-operations/{operation["id"]}/cancel').status_code == 200
    app.state.joint_plan_workflow.tick()
    assert gateway.submits == gateway.uploads == 0
    state = app.state.joint_plan_store.project('local-developer', project['id'])
    assert state['operation']['status'] == 'cancelled' and not state['versions']


def test_adoption_blocks_pending_project_render_without_partial_scene_write(env):
    client, app, _, project = env
    start(client, project)
    app.state.joint_plan_workflow.tick()
    candidate = app.state.joint_plan_store.project('local-developer', project['id'])['versions'][0]
    with app.state.store.transaction() as db:
        db.execute('INSERT INTO video_renders VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                   ('render-busy', 'local-developer', project['id'], 'busy', 'hash',
                    json.dumps({'status': 'queued'}), '{}', None))
    response = adopt(client, app, project, candidate)
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'render_in_progress'
    assert app.state.store.project('local-developer', project['id'])['scenes'] == []


def test_published_method_delivery_is_durable_and_bound_before_agent_upload(env, tmp_path):
    client, app, gateway, project = env
    catalogs = []
    def catalog(owner):
        catalogs.append(owner)
        return []
    adapter = MethodProductionAdapter(catalog, lambda *args: pytest.fail('empty shortlist must not fetch'),
                                      ledger_path=tmp_path / 'freezes.db')
    app.state.joint_plan_workflow.method_adapter = adapter
    operation = start(client, project).json()
    app.state.joint_plan_workflow.tick()
    durable = app.state.joint_plan_store.operation(operation['id'])
    delivery = durable['document']['method_delivery']
    assert durable['method_prepared'] is True
    assert delivery['consumer_id'] == 'video-joint-plan:' + operation['id']
    assert delivery['allowed_engines'] == ['html-svg']
    assert durable['input_ref']['sha256'] == durable['input_sha256'] == digest(durable['document'])
    candidate = app.state.joint_plan_store.project('local-developer', project['id'])['versions'][0]
    assert candidate['result']['method_decision'] == dict(delivery_sha256=delivery['sha256'], decisions=[])
    assert catalogs == ['local-developer'] and gateway.submits == 1


def test_csrf_owner_and_disabled_admission_do_not_create_hidden_work(tmp_path):
    gateway = Gateway()
    app = create_app(db_path=tmp_path / 'db', background=False, joint_plan_gateway=gateway,
                     joint_plan_enabled=True, trusted_hosts={'testserver'})
    with TestClient(app) as client:
        client.headers['X-Forwarded-User'] = 'alice'
        client.headers['X-CSRF-Token'] = client.get('/api/session').json()['csrf_token']
        project = client.post('/api/projects', json=dict(title='队列', script='任务先排队。')).json()
        csrf = client.headers.pop('X-CSRF-Token')
        assert start(client, project).status_code == 403
        client.headers['X-CSRF-Token'] = csrf
        operation = start(client, project).json()
        client.headers['X-Forwarded-User'] = 'bob'
        client.headers['X-CSRF-Token'] = client.get('/api/session').json()['csrf_token']
        assert client.get(f'/api/projects/{project["id"]}/joint-plan').status_code == 404
        assert client.post(f'/api/joint-plan-operations/{operation["id"]}/cancel').status_code == 404
        app.state.joint_plan_workflow.enabled = False
        assert start(client, project, key='disabled-new-plan').status_code == 503
        assert gateway.submits == 0
