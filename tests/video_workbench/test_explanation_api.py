"""Whole-project coordination through real HTTP, SQLite and workflow boundaries.

The only replacement is the remote Agent, so no model or speech cost is spent.
"""
import io
import wave
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agent_service.contracts import digest
from video_workbench.app import create_app
from video_workbench.explanation_store import scene_visual_binding
from video_workbench.motion_store import MotionStore, semantic_binding


def explanation_result(document, input_sha256):
    return dict(schema_version='video-explanation-result.v1', input_sha256=input_sha256,
        summary='先解释任务如何排队，再说明工作节点怎样接手。', warnings=[], scenes=[
            dict(scene_id=s['scene_id'], title=s['title'], narration='我们先来看：' + s['narration'],
                 visual_description='用任务卡片的位置变化说明这一过程。',
                 duration_seconds=s['duration_seconds'], pronunciation={},
                 key_points=['任务排队', '按序接手'], visual_strategy='flow',
                 visual_steps=['先展示等待处理的任务', '再展示任务被接手'],
                 delivery_note='前一句提出问题，后一句说明原因；这是讲述建议。',
                 source_quotes=[s['narration']]) for s in document['scenes']])


class FakeExplanationGateway:
    def __init__(self):
        self.jobs = {}
        self.submits = 0

    def upload(self, document):
        return dict(input_id=str(uuid4()), sha256=digest(document),
                    path='explanation-input.json', role='explanation_input')

    def submit(self, operation_id, document, deadline_at, input_ref=None):
        self.submits += 1
        assert input_ref['sha256'] == digest(document)
        job = dict(job_id=str(uuid4()), state='running', document=document)
        self.jobs[operation_id] = job
        return job

    def lookup(self, operation_id):
        return self.jobs[operation_id]

    def poll(self, job_id):
        job = next(j for j in self.jobs.values() if j['job_id'] == job_id)
        return dict(job, state='completed')

    def collect(self, job_id, input_sha256, input_document):
        return explanation_result(input_document, input_sha256)

    def cancel(self, job_id):
        return dict(job_id=job_id, state='cancelling')


@pytest.fixture
def env(tmp_path):
    gateway = FakeExplanationGateway()
    app = create_app(db_path=tmp_path / 'db', local_mode=True, background=False,
                     explanation_gateway=gateway, explanation_enabled=True,
                     trusted_hosts={'testserver'})
    with TestClient(app) as client:
        client.headers['X-CSRF-Token'] = client.get('/api/session').json()['csrf_token']
        p = client.post('/api/projects', json=dict(title='任务流转', style='蓝色图解',
            script='队列把任务排好。工作节点依次处理任务。')).json()
        for narration in ['队列把任务排好。', '工作节点依次处理任务。']:
            p = client.post(f'/api/projects/{p["id"]}/scenes', json=dict(title='任务',
                narration=narration, visual_description='展示任务卡片。',
                duration_seconds=8, format='svg')).json()
        yield client, app, gateway, p


def generate(client, p, key='explanation-intent-1'):
    body = dict(expected_revision=p['revision'], brief=dict(audience='非专业观众',
                goal='解释任务是怎样流转的', tone='conversational'), instruction='用问题引出解释')
    return client.post(f'/api/projects/{p["id"]}/explanation', json=body,
                       headers={'Idempotency-Key': key})


def test_review_then_atomic_adoption_reaches_visual_and_audio_inputs(env):
    client, app, gateway, p = env
    store = app.state.store
    owner = 'local-developer'
    scene_id = p['scenes'][0]['id']
    # Seed actual, immutable graphic and audio versions before planning.
    op = store.start(owner, p['id'], 'scene', {'expected_revision': p['revision']},
                     'original-visual', scene_id=scene_id)
    source = '<svg xmlns="http://www.w3.org/2000/svg"><text>旧画面</text></svg>'
    store.complete(op['id'], dict(format='svg', source=source, summary='旧画面'))
    p = store.project(owner, p['id'])
    visual_id = p['scenes'][0]['versions'][0]['id']
    p = store.adopt(owner, scene_id, visual_id, None)
    raw = io.BytesIO()
    with wave.open(raw, 'wb') as audio:
        audio.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\x10\x01' * 8000)
    p = store.upload_audio(owner, scene_id, {'expected_revision': p['revision']},
                           'original-audio', raw.getvalue())
    audio_id = p['scenes'][0]['audio_versions'][0]['id']
    p = store.adopt_audio(owner, scene_id, audio_id, None)
    original = p
    response = generate(client, p)
    assert response.status_code == 202, response.text
    # Replayed intent is the same saved operation, even after its result arrives.
    assert generate(client, p).json()['id'] == response.json()['id']
    app.state.explanation_workflow.tick()
    envelope = client.get(f'/api/projects/{p["id"]}/explanation').json()
    assert envelope['operation']['status'] == 'completed', envelope
    candidate = envelope['versions'][0]
    before = client.get(f'/api/projects/{p["id"]}').json()
    assert before['scenes'][0]['narration'] == original['scenes'][0]['narration']
    assert before['scenes'][0]['head_version_id'] == visual_id
    assert envelope['head_version_id'] is None and candidate['adoptable']
    adopted = client.post(f'/api/projects/{p["id"]}/explanation/adopt', json=dict(
        version_id=candidate['id'], expected_revision=before['revision'], expected_head_version_id=None))
    assert adopted.status_code == 200, adopted.text
    new = adopted.json()
    assert new['script'] == original['script']
    assert [s['id'] for s in new['scenes']] == [s['id'] for s in original['scenes']]
    assert new['speech_settings'] == original['speech_settings']
    assert new['scenes'][0]['head_version_id'] is None
    assert new['scenes'][0]['head_audio_version_id'] == audio_id
    assert new['scenes'][0]['audio_stale'] is True
    assert store.version(owner, visual_id)['source'] == source
    assert store.audio_version(owner, audio_id)['data'] == raw.getvalue()
    next_op = store.start(owner, p['id'], 'scene', {'expected_revision': new['revision']},
                         'new-visual-request', scene_id=scene_id)
    document = store.operation(next_op['id'])['document']
    assert document['narration'] == candidate['result']['scenes'][0]['narration']
    assert '任务排队' in document['visual_description']
    assert gateway.submits == 1 and store.active_audio_operations() == []


def test_late_result_after_edit_is_readable_but_cannot_apply(env):
    client, app, gateway, p = env
    assert generate(client, p).status_code == 202
    app.state.explanation_workflow.tick()
    current = client.get(f'/api/projects/{p["id"]}').json()
    scene = current['scenes'][1]
    edited = client.patch(f'/api/scenes/{scene["id"]}', json=dict(
        expected_revision=current['revision'], title=scene['title'], narration='用户自己的解释。',
        visual_description=scene['visual_description'], duration_seconds=8, format='svg')).json()
    state = client.get(f'/api/projects/{p["id"]}/explanation').json()
    candidate = state['versions'][0]
    assert candidate['stale'] and not candidate['adoptable']
    response = client.post(f'/api/projects/{p["id"]}/explanation/adopt', json=dict(
        expected_revision=edited['revision'], version_id=candidate['id'], expected_head_version_id=None))
    assert response.status_code == 409
    after = client.get(f'/api/projects/{p["id"]}').json()
    assert after['scenes'] == edited['scenes']
    assert gateway.submits == 1


def test_adopted_explanation_guides_motion_and_invalidates_old_semantics(env):
    client, app, gateway, p = env
    assert generate(client, p).status_code == 202
    app.state.explanation_workflow.tick()
    candidate = app.state.explanation_store.project('local-developer', p['id'])['versions'][0]
    p = app.state.store.project('local-developer', p['id'])
    p = app.state.explanation_store.adopt('local-developer', p['id'], dict(
        version_id=candidate['id'], expected_revision=p['revision'], expected_head_version_id=None))
    scene = p['scenes'][0]
    motion = MotionStore(app.state.store)
    op = motion.start('local-developer', scene['id'], {'expected_revision': p['revision']}, 'planned-motion')
    document = motion.operation(op['id'])['document']
    assert '任务排队' in document['visual_description']
    assert '再展示任务被接手' in document['visual_description']
    before = semantic_binding(p, scene)
    # Direction is part of motion identity even when the prose stays identical.
    scene['explanation']['key_points'] = ['新的强调重点']
    assert semantic_binding(p, scene) != before
    # A manual content edit stops consuming directions bound to the old scene.
    scene['narration'] = '用户新的旁白。'
    assert scene['explanation']['visual_binding_sha256'] != scene_visual_binding(scene)


def test_disabled_or_unsigned_requests_never_admit_work(tmp_path):
    app = create_app(db_path=tmp_path / 'db', local_mode=True, background=False,
                     explanation_gateway=FakeExplanationGateway(), explanation_enabled=False,
                     trusted_hosts={'testserver'})
    with TestClient(app) as client:
        session = client.get('/api/session').json()
        assert session['explanation_configured'] is False
        route = '/api/projects/nonexistent/explanation'
        assert client.post(route, json={'expected_revision': 1}).status_code == 403
        client.headers['X-CSRF-Token'] = session['csrf_token']
        assert client.post(route, json={'expected_revision': 1},
                           headers={'Idempotency-Key': 'disabled-plan'}).status_code == 503
        assert app.state.explanation_store.active_operations() == []


def test_optional_brief_uses_saved_content_and_empty_project_has_clear_error(env):
    client, app, gateway, p = env
    empty = client.post('/api/projects', json={'title': '空项目', 'script': '还没有镜头。'}).json()
    response = client.post(f'/api/projects/{empty["id"]}/explanation',
        json={'expected_revision': empty['revision']}, headers={'Idempotency-Key': 'empty-project-plan'})
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'explanation_requires_scenes'
    response = client.post(f'/api/projects/{p["id"]}/explanation',
        json={'expected_revision': p['revision']}, headers={'Idempotency-Key': 'optional-brief-plan'})
    assert response.status_code == 202, response.text
    app.state.explanation_workflow.tick()
    assert gateway.submits == 1
    state = client.get(f'/api/projects/{p["id"]}/explanation').json()
    assert state['brief'] == dict(audience='', goal='', tone='conversational')
    assert state['operation']['status'] == 'completed'


def test_explanation_candidate_and_cancel_are_owner_scoped(tmp_path):
    gateway = FakeExplanationGateway()
    app = create_app(db_path=tmp_path / 'db', background=False,
                     explanation_gateway=gateway, explanation_enabled=True, trusted_hosts={'testserver'})
    with TestClient(app) as client:
        client.headers['X-Forwarded-User'] = 'alice'
        client.headers['X-CSRF-Token'] = client.get('/api/session').json()['csrf_token']
        p = client.post('/api/projects', json={'title': '标题', 'script': '队列把任务排好。'}).json()
        p = client.post(f'/api/projects/{p["id"]}/scenes', json=dict(title='镜头',
            narration='队列把任务排好。', visual_description='排队。')).json()
        op = generate(client, p).json()
        client.headers['X-Forwarded-User'] = 'bob'
        client.headers['X-CSRF-Token'] = client.get('/api/session').json()['csrf_token']
        assert client.get(f'/api/projects/{p["id"]}/explanation').status_code == 404
        assert client.post(f'/api/explanation-operations/{op["id"]}/cancel').status_code == 404
