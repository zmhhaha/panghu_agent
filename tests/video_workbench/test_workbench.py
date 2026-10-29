import hashlib
import io
import json
import time
import zipfile
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agent_service.contracts import ServiceError, digest
from video_workbench.app import create_app
from video_workbench.service import Workflow
from video_workbench.store import Store


SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><rect width="1280" height="720" fill="#101a2c"/><text x="80" y="160" fill="white" font-size="60">测试画面</text></svg>'
SCENE = dict(title='原理', narration='这里介绍核心原理。', visual_description='蓝色背景，展示原理。', duration_seconds=8, format='svg')


class FakeGateway:
    """Deterministic remote contract boundary, never a model or paid request."""
    def __init__(self):
        self.jobs = {}
        self.submits = 0
        self.lookups = 0
        self.cancelled = []
        self.ambiguous = False
        self.ready = False
        self.bad_hash = False
        self.unsafe = False

    def upload(self, document):
        return dict(input_id=str(uuid4()), sha256=digest(document), path='video-input.json', role='video_input')

    def submit(self, operation_id, kind, document, deadline_at, input_ref=None):
        self.submits += 1
        assert input_ref['sha256'] == digest(document)
        job = dict(job_id=str(uuid4()), state='running', document=document, kind=kind)
        self.jobs[operation_id] = job
        if self.ambiguous:
            raise ServiceError('service_unavailable', 503)
        return job

    def lookup(self, operation_id):
        self.lookups += 1
        if operation_id not in self.jobs:
            raise ServiceError('not_found', 404)
        return self.jobs[operation_id]

    def poll(self, job_id):
        job = next(j for j in self.jobs.values() if j['job_id'] == job_id)
        return dict(job, state='cancelled' if job_id in self.cancelled else 'completed' if self.ready else 'running')

    def cancel(self, job_id):
        self.cancelled.append(job_id)
        return dict(job_id=job_id, state='cancelling')

    def collect(self, job_id, kind, input_sha256):
        output = dict(schema_version='1', scenes=[SCENE]) if kind == 'storyboard' else dict(
            schema_version='1', format='svg', source='<svg><script>alert(1)</script></svg>' if self.unsafe else SVG, summary='候选画面')
        return dict(schema_version='1', kind=kind, input_sha256='0' * 64 if self.bad_hash else input_sha256, output=output)


@pytest.fixture
def env(tmp_path):
    gateway = FakeGateway()
    app = create_app(db_path=tmp_path / 'state.db', gateway=gateway, background=False, trusted_hosts={'testserver'})
    with TestClient(app) as client:
        client.headers['X-Forwarded-User'] = 'alice'
        client.headers['X-CSRF-Token'] = client.get('/api/session').json()['csrf_token']
        yield client, app.state.store, app.state.workflow, gateway


def project(client, with_scene=True):
    response = client.post('/api/projects', json=dict(title='视频', script='输入文稿', style='深蓝色信息图'))
    assert response.status_code == 200, response.text
    result = response.json()
    if with_scene:
        result = client.post(f'/api/projects/{result["id"]}/scenes', json=SCENE).json()
    return result


def generate(client, p, key='generate-1', **fields):
    return client.post(f'/api/scenes/{p["scenes"][0]["id"]}/generate',
                       headers={'Idempotency-Key': key}, json=dict(expected_revision=p['revision'], **fields))


def test_generate_review_revise_adopt_and_export(env):
    client, store, workflow, gateway = env
    p = project(client)
    response = generate(client, p)
    assert response.status_code == 202, response.text
    gateway.ready = True
    workflow.tick()
    p = client.get('/api/projects/' + p['id']).json()
    scene = p['scenes'][0]
    first = scene['versions'][0]['id']
    assert scene['head_version_id'] is None  # Candidate requires explicit adoption.
    assert client.get(f'/api/projects/{p["id"]}/export').status_code == 409
    adopted = client.post(f'/api/scenes/{scene["id"]}/adopt', json=dict(version_id=first, expected_head_version_id=None))
    assert adopted.status_code == 200
    p = adopted.json()
    revision = client.post(f'/api/scenes/{scene["id"]}/revise', headers={'Idempotency-Key': 'revision-1'},
                          json=dict(expected_revision=p['revision'], base_version_id=first, instruction='把标题放大'))
    assert revision.status_code == 202, revision.text
    workflow.tick()
    p = client.get('/api/projects/' + p['id']).json()
    assert p['scenes'][0]['head_version_id'] == first
    assert len(p['scenes'][0]['versions']) == 2
    last_job = list(gateway.jobs.values())[-1]
    assert last_job['document']['base_source'] == SVG
    package = client.get(f'/api/projects/{p["id"]}/export')
    assert package.status_code == 200
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['scenes'][0]['version_id'] == first
        assert archive.read('scenes/001.svg').decode() == SVG
        assert 'sandbox=""' in archive.read('index.html').decode()
    preview = client.get(f'/api/versions/{first}/preview')
    assert preview.status_code == 200
    assert "connect-src 'none'" in preview.headers['content-security-policy']
    assert 'allow-same-origin' not in preview.headers['content-security-policy']
    source = client.get(f'/api/versions/{first}/source')
    assert source.headers['content-disposition'].startswith('attachment;')
    assert source.text == SVG


def test_storyboard_and_project_edit_lock(env):
    client, store, workflow, gateway = env
    p = project(client, False)
    r = client.post(f'/api/projects/{p["id"]}/storyboard', json=dict(expected_revision=p['revision']), headers={'Idempotency-Key': 'storyboard-1'})
    assert r.status_code == 202
    assert client.post(f'/api/projects/{p["id"]}/scenes', json=SCENE).status_code == 409
    gateway.ready = True
    workflow.tick()
    p = client.get('/api/projects/' + p['id']).json()
    assert len(p['scenes']) == 1
    assert p['storyboard_operation']['status'] == 'completed'
    assert client.post(f'/api/projects/{p["id"]}/storyboard', json=dict(expected_revision=p['revision']), headers={'Idempotency-Key': 'storyboard-2'}).status_code == 409


def test_idempotency_replay_before_revision_check(env):
    client, store, workflow, gateway = env
    p = project(client)
    a, b = generate(client, p), generate(client, p)
    assert a.status_code == b.status_code == 202
    assert a.json()['id'] == b.json()['id']
    assert generate(client, p, instruction='different').status_code == 409
    assert generate(client, p, key='generate-2').json()['detail']['code'] == 'revision_conflict'
    workflow.tick()
    assert gateway.submits == 1


def test_ambiguous_submit_survives_restart_without_second_post(env):
    client, store, workflow, gateway = env
    p = project(client)
    gateway.ambiguous = True
    op = generate(client, p).json()
    workflow.tick()
    persisted = Store(store.path).operation(op['id'])
    assert persisted['submit_attempted'] is True and persisted['input_ref'] is not None
    assert persisted['job_id'] is None
    recovered = Workflow(Store(store.path), gateway)
    gateway.ready = True
    recovered.tick()
    recovered.tick()
    assert gateway.submits == 1 and gateway.lookups == 1
    assert store.operation(op['id'])['status'] == 'completed'
    assert len(client.get('/api/projects/' + p['id']).json()['scenes'][0]['versions']) == 1


def test_cancel_ambiguous_late_admission_is_confirmed_by_poll(env):
    client, store, workflow, gateway = env
    p = project(client)
    gateway.ambiguous = True
    op = generate(client, p).json()
    workflow.tick()
    assert client.post('/api/operations/' + op['id'] + '/cancel').json()['status'] == 'cancelling'
    workflow.tick()
    assert gateway.cancelled
    assert store.operation(op['id'])['status'] == 'cancelling'
    workflow.tick()
    assert store.operation(op['id'])['status'] == 'cancelled'
    assert not client.get('/api/projects/' + p['id']).json()['scenes'][0]['versions']


def test_cancel_before_submission_and_deadline(env):
    client, store, workflow, gateway = env
    p = project(client)
    op = generate(client, p).json()
    client.post('/api/operations/' + op['id'] + '/cancel')
    workflow.tick()
    assert store.operation(op['id'])['status'] == 'cancelled'
    assert gateway.submits == 0


@pytest.mark.parametrize('failure', ['bad_hash', 'unsafe'])
def test_invalid_result_never_becomes_version(env, failure):
    client, store, workflow, gateway = env
    p = project(client)
    op = generate(client, p).json()
    gateway.ready = True
    setattr(gateway, failure, True)
    workflow.tick()
    assert store.operation(op['id'])['status'] == 'failed'
    assert client.get('/api/projects/' + p['id']).json()['scenes'][0]['versions'] == []


def test_owner_isolation_and_csrf(env):
    client, store, workflow, gateway = env
    p = project(client)
    assert client.post('/api/projects', headers={'X-CSRF-Token': 'bad'}, json=dict(title='a', script='b')).status_code == 403
    assert client.post('/api/projects', headers={'Origin': 'https://evil.test'}, json=dict(title='a', script='b')).status_code == 403
    client.headers['X-Forwarded-User'] = 'bob'
    assert client.get('/api/projects/' + p['id']).status_code == 404
    assert client.get('/api/projects').json()['projects'] == []
    token = client.get('/api/session').json()['csrf_token']
    client.headers['X-CSRF-Token'] = token
    assert client.patch('/api/scenes/' + p['scenes'][0]['id'], json=dict(SCENE, expected_revision=p['revision'])).status_code == 404
    assert client.get(f'/api/projects/{p["id"]}/export').status_code == 404


def test_stale_edit_and_adoption_and_unconfigured(env, tmp_path):
    client, store, workflow, gateway = env
    p = project(client)
    old_revision = p['revision']
    op = generate(client, p)
    gateway.ready = True
    workflow.tick()
    assert client.patch('/api/scenes/' + p['scenes'][0]['id'], json=dict(SCENE, expected_revision=old_revision)).status_code == 409
    p = client.get('/api/projects/' + p['id']).json()
    scene = p['scenes'][0]
    body = dict(version_id=scene['versions'][0]['id'], expected_head_version_id=None)
    assert client.post('/api/scenes/' + scene['id'] + '/adopt', json=body).status_code == 200
    assert client.post('/api/scenes/' + scene['id'] + '/adopt', json=body).status_code == 409
    standalone = create_app(db_path=tmp_path / 'off.db', background=False, local_mode=True, trusted_hosts={'testserver'})
    with TestClient(standalone) as c:
        c.headers['X-CSRF-Token'] = c.get('/api/session').json()['csrf_token']
        p = project(c)
        assert generate(c, p).json()['detail']['code'] == 'agent_not_configured'


def test_expired_unknown_submission_stops_lookup_without_resubmitting(env):
    client, store, workflow, gateway = env
    p = project(client)
    op = generate(client, p).json()
    store.update_operation(op['id'], submit_attempted=True, deadline_at=time.time() - 1)
    workflow.tick()
    assert store.operation(op['id'])['status'] == 'timed_out'
    assert gateway.submits == 0


def test_authentication_and_host_required(tmp_path):
    app = create_app(db_path=tmp_path / 'auth.db', background=False, trusted_hosts={'testserver'})
    with TestClient(app) as client:
        assert client.get('/api/session').status_code == 401
        assert client.get('/api/session', headers={'Host': 'evil.test'}).status_code == 400


def test_cancel_after_input_upload_with_stale_snapshot_never_submits(env):
    client, store, workflow, gateway = env
    p = project(client)
    op = generate(client, p).json()
    current = store.operation(op['id'])
    snapshot = store.update_operation(op['id'], input_ref=gateway.upload(current['document']))
    store.cancel('alice', op['id'])
    workflow._step(snapshot)
    assert gateway.submits == 0
    assert store.operation(op['id'])['status'] == 'cancelled'


def test_disabled_route_is_definitive_failure(env):
    client, store, workflow, gateway = env
    p = project(client)
    op = generate(client, p).json()
    def disabled(*args, **kwargs):
        raise ServiceError('video_profile_unavailable', 503)
    gateway.submit = disabled
    workflow.tick()
    assert store.operation(op['id'])['status'] == 'failed'
    assert store.operation(op['id'])['error'] == 'video_profile_unavailable'


def test_completed_operation_cannot_be_reopened_by_cancellation(env):
    client, store, workflow, gateway = env
    p = project(client)
    op = generate(client, p).json()
    gateway.ready = True
    workflow.tick()
    assert client.post('/api/operations/' + op['id'] + '/cancel').json()['status'] == 'completed'
    workflow.tick()
    assert len(client.get('/api/projects/' + p['id']).json()['scenes'][0]['versions']) == 1


def test_malformed_csrf_and_origin_rejected(env):
    client, store, workflow, gateway = env
    assert client.post('/api/projects', headers={'Origin': 'http://['}, json=dict(title='a', script='b')).status_code == 403
