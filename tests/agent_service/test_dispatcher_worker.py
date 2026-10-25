"""No-model HTTP handoff tests. NativeDriver below is explicitly synthetic."""
import json
from pathlib import Path
import sqlite3
import threading
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest

from agent_service.api import ApiSettings, create_app
from agent_service.bindings import SQLiteNativeBindings
from agent_service.contracts import (ExecutionEnvelope, JobRequest, Principal, ProfilePolicy, ProfileRegistry,
    ServiceError, WorkerResult, validate_workspace_task, verify_receipt)
from agent_service.dispatcher import Dispatcher
from agent_service.engines import NativeOutcome
from agent_service.store import Store
from agent_service.transport import RemoteWorkerAdapter, WorkerEndpoint
from agent_service.worker_api import WorkerHost, create_worker_app


TOKEN = 'test-worker-authentication-token-0000'
CALLER = 'test-caller-authentication-token-0000'
KEY = b'test-receipt-signing-key-000000000'
PINS = 'sha256:' + '4' * 64


class SyntheticDriver:
    ready = True

    def __init__(self, engine, wait=False, stopped=True):
        self.engine, self.wait, self.stopped = engine, wait, stopped
        self.calls = 0

    def execute(self, request, workspace, emit, cancel):
        self.calls += 1
        workspace.mkdir(mode=0o700)
        emit({'type': 'engine_progress', 'private_tool_data': 'must not cross the API'})
        if self.wait:
            assert cancel.wait(5), 'Test did not send cancellation'
        raw = json.dumps({'schema_version': '1', 'text': request.payload['prompt']}).encode()
        (workspace / 'output.json').write_bytes(raw)
        return NativeOutcome('completed', usage=None, stopped=self.stopped,
                             resource_ids=('synthetic-capsule',) if self.stopped else ())


@pytest.fixture
def setup_chain(tmp_path):
    opened = []

    def make(engine='pi', *, wait=False, stopped=True, clock=time.time):
        root = tmp_path / str(uuid4())
        root.mkdir()
        policy = ProfilePolicy('workspace-task.v1', 'r1', engine, 'runtime-r1', PINS,
                               enabled=True, task_validator=validate_workspace_task)
        driver = SyntheticDriver(engine, wait=wait, stopped=stopped)
        host = WorkerHost(policy, driver, root / 'control', root / 'workspace', KEY, clock=clock)
        worker = TestClient(create_worker_app(host, TOKEN))
        worker.__enter__()
        opened.append(worker)
        def transport(request):
            response = worker.request(request.method, request.url.path, content=request.content,
                                      headers=dict(request.headers))
            return httpx.Response(response.status_code, content=response.content, headers=dict(response.headers))
        adapter = RemoteWorkerAdapter(WorkerEndpoint('http://capsule', TOKEN, KEY, engine),
                                      client=httpx.Client(transport=httpx.MockTransport(transport)))
        store = Store('sqlite:///' + str(root / 'queue.sqlite'), allow_sqlite=True, clock=clock)
        store.initialize()
        app = TestClient(create_app(ApiSettings({CALLER: Principal('t', 'ppt', frozenset({policy.name}))}),
                                    store, ProfileRegistry([policy]), {engine: adapter}))
        opened.append(app)
        return policy, driver, host, worker, adapter, store, app

    yield make
    for client in reversed(opened):
        client.__exit__(None, None, None) if hasattr(client, 'exit_stack') else client.close()


def submit(app, key='one', timeout=600):
    response = app.post('/v1/jobs', headers={'Authorization': 'Bearer ' + CALLER, 'Idempotency-Key': key},
                        json=dict(profile='workspace-task.v1', profile_revision='r1', business_ref='ppt-step',
                                  task={'prompt': 'A technical explanation'}, timeout_seconds=timeout))
    assert response.status_code == 202, response.text
    return response.json()


@pytest.mark.parametrize('engine', ['pi', 'hermes'])
def test_same_business_api_for_both_worker_engines(setup_chain, engine):
    policy, driver, host, worker, adapter, store, app = setup_chain(engine)
    job = submit(app)
    dispatcher = Dispatcher(store, {engine: adapter})
    running = dispatcher.dispatch_once()
    host._thread.join(5)
    final = dispatcher.poll(running)
    assert final['state'] == 'completed'
    assert final['outcome']['usage'] is None
    assert driver.calls == 1
    # Same key returns original accepted identity even after the capsule is spent.
    replay = app.post('/v1/jobs', headers={'Authorization': 'Bearer ' + CALLER, 'Idempotency-Key': 'one'},
                      json=dict(profile=policy.name, profile_revision='r1', business_ref='ppt-step',
                                task={'prompt': 'A technical explanation'}, timeout_seconds=600))
    assert replay.status_code == 200 and replay.json()['job_id'] == job['job_id']
    assert replay.json()['publication_authorized'] is False
    listed = app.get(f"/v1/jobs/{job['job_id']}/artifacts", headers={'Authorization': 'Bearer ' + CALLER})
    artifact = listed.json()['items'][0]
    downloaded = app.get(f"/v1/artifacts/{artifact['artifact_id']}/content", headers={'Authorization': 'Bearer ' + CALLER})
    assert json.loads(downloaded.content)['text'] == 'A technical explanation'
    assert b'private_tool_data' not in app.get(f"/v1/jobs/{job['job_id']}/events", headers={'Authorization': 'Bearer ' + CALLER}).content
    assert store.active() is None


def test_cancel_waits_for_stop_and_opens_no_artifacts(setup_chain):
    _, driver, host, _, adapter, store, app = setup_chain(wait=True)
    job = submit(app)
    dispatcher = Dispatcher(store, {'pi': adapter})
    running = dispatcher.dispatch_once()
    cancelling = store.cancel('t', 'ppt', job['job_id'])
    assert cancelling['state'] == 'cancelling'
    dispatcher.poll(running)
    host._thread.join(5)
    final = dispatcher.poll(running)
    assert final['state'] == 'cancelled'
    assert store.get_artifacts('t', 'ppt', job['job_id']) == []
    assert driver.calls == 1


def test_missing_stop_proof_quarantines_and_blocks_queue(setup_chain):
    _, _, host, _, adapter, store, app = setup_chain(stopped=False)
    submit(app)
    dispatcher = Dispatcher(store, {'pi': adapter})
    running = dispatcher.dispatch_once()
    host._thread.join(5)
    final = dispatcher.poll(running)
    assert final['state'] == 'quarantined'
    assert store.get_artifacts('t', 'ppt', final['job_id']) == []
    assert store.claim() is None
    assert store.active()['job_id'] == final['job_id']


@pytest.mark.parametrize('output', [None, b'{bad json', b'{"schema_version":"1","text":"a","text":"b"}',
                                  b'{"schema_version":"1","text":{},"published":true}'])
def test_bad_output_after_proven_stop_fails_and_releases_slot(setup_chain, output):
    _, driver, host, _, adapter, store, app = setup_chain()
    def execute(request, workspace, emit, cancel):
        workspace.mkdir(mode=0o700)
        if output is not None:
            (workspace/'output.json').write_bytes(output)
        return NativeOutcome('completed', stopped=True, resource_ids=('stopped-synthetic-capsule',))
    driver.execute = execute
    job = submit(app)
    dispatcher = Dispatcher(store, {'pi': adapter})
    running = dispatcher.dispatch_once()
    host._thread.join(5)
    final = dispatcher.poll(running)
    assert final['state'] == 'failed'
    assert final['outcome']['error_code'] == 'worker_output_invalid'
    assert store.get_artifacts('t', 'ppt', job['job_id']) == []
    assert store.active() is None


def test_control_restart_reconciles_without_put_again(setup_chain):
    _, driver, host, _, adapter, store, app = setup_chain()
    submit(app)
    first = Dispatcher(store, {'pi': adapter})
    running = first.dispatch_once()
    host._thread.join(5)
    def forbidden(envelope):
        pytest.fail('Restart submitted a second execution')
    adapter.start = forbidden
    second = Dispatcher(store, {'pi': adapter})
    assert second.reconcile()['state'] == 'completed'
    assert driver.calls == 1


def test_tampered_artifact_is_rejected(setup_chain):
    _, _, host, _, adapter, store, app = setup_chain()
    submit(app)
    dispatcher = Dispatcher(store, {'pi': adapter})
    running = dispatcher.dispatch_once()
    host._thread.join(5)
    real_result = adapter.result
    def tamper(envelope):
        result = real_result(envelope)
        return result.model_copy(update={'usage': {'input_tokens': 999}})
    adapter.result = tamper
    assert dispatcher.poll(running)['state'] == 'quarantined'
    assert store.get_artifacts('t', 'ppt', running['job_id']) == []


def test_worker_transport_loss_does_not_renew_lease_forever(setup_chain):
    now = [1000.0]
    _, _, host, _, adapter, store, app = setup_chain(wait=True, clock=lambda: now[0])
    submit(app)
    dispatcher = Dispatcher(store, {'pi': adapter}, clock=lambda: now[0])
    running = dispatcher.dispatch_once()
    original = running['lease_expires_at']
    def unavailable(envelope):
        raise ServiceError('worker_transport_unavailable', 503)
    adapter.inspect = unavailable
    now[0] += 10
    dispatcher.poll(running)
    assert store.active()['lease_expires_at'] == original
    now[0] = original + 1
    assert dispatcher.tick()['state'] == 'quarantined'
    host._cancel.set()
    host._thread.join(5)


def test_deadline_stop_grace_does_not_extend_execution(setup_chain):
    now = [1000.0]
    _, _, host, _, adapter, store, app = setup_chain(wait=True, clock=lambda: now[0])
    submit(app, timeout=2)
    dispatcher = Dispatcher(store, {'pi': adapter}, clock=lambda: now[0])
    running = dispatcher.dispatch_once()
    now[0] = 1003.0
    dispatcher.poll(running)
    host._thread.join(5)
    assert dispatcher.poll(running)['state'] == 'timed_out'
    assert store.active() is None


def test_worker_auth_and_unknown_fields_are_rejected(setup_chain):
    _, _, host, worker, adapter, store, app = setup_chain()
    assert worker.get('/v1/capabilities').status_code == 401
    submit(app)
    claimed = store.claim()
    e = Dispatcher(store, {'pi': adapter})._envelope(claimed)
    response = worker.put('/v1/executions/' + e.attempt_id,
        headers={'Authorization': 'Bearer ' + TOKEN}, json={**e.model_dump(), 'executable': '/bin/sh'})
    assert response.status_code == 422
    assert host._state is None


def test_worker_restart_quarantines_unresolved_record(tmp_path):
    policy = ProfilePolicy('workspace-task.v1', 'r1', 'pi', 'r1', PINS)
    first = WorkerHost(policy, SyntheticDriver('pi'), tmp_path/'control', tmp_path/'work', KEY)
    first.initialize()
    first._state = {'envelope': {}, 'state': 'running', 'request_sha256': '0'*64, 'events': [], 'result': None}
    first._save()
    first.close()
    second = WorkerHost(policy, SyntheticDriver('pi'), tmp_path/'control', tmp_path/'work', KEY)
    second.initialize()
    try:
        assert second._state['state'] == 'quarantined'
        assert second._state['result']['stop_receipt'] is None
        assert not second.capabilities()['ready']
    finally:
        second.close()
    # No GC-dependent SQLite handle may keep the capsule private DB open.
    database = tmp_path/'control'/'execution.sqlite'
    database.rename(database.with_suffix('.closed'))


def test_native_admission_intent_survives_reopen_and_has_one_cas_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path = tmp_path/'native.sqlite'
    bindings = SQLiteNativeBindings(path)
    bindings.initialize()
    bindings.prepare('execution', 'session', 'admit-key')
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert sum(pool.map(lambda _: SQLiteNativeBindings(path).mark_admission_started('execution'), range(6))) == 1
    reopened = SQLiteNativeBindings(path)
    assert reopened.load('execution').admission_started is True
    assert reopened.load('execution').run_id is None
    reopened.bind_run('execution', 'run')
    assert reopened.load('execution').run_id == 'run'
    with pytest.raises(RuntimeError):
        reopened.bind_run('execution', 'different')


def test_start_persistence_failure_never_launches(setup_chain, monkeypatch):
    _, driver, host, _, adapter, store, app = setup_chain()
    submit(app)
    claimed = store.claim()
    envelope = Dispatcher(store, {'pi': adapter})._envelope(claimed)
    def fail(state):
        raise sqlite3.OperationalError('synthetic disk failure')
    monkeypatch.setattr(host, '_write', fail)
    with pytest.raises(ServiceError):
        host.start(envelope)
    assert driver.calls == 0 and host._state is None
    assert host.capabilities()['ready'] is False


def test_final_persistence_failure_exposes_no_success(setup_chain, monkeypatch):
    _, _, host, _, adapter, store, app = setup_chain(wait=True)
    job = submit(app)
    running = Dispatcher(store, {'pi': adapter}).dispatch_once()
    original = host._write
    def fail_final(state):
        if state['result'] is not None:
            raise sqlite3.OperationalError('synthetic disk failure')
        original(state)
    monkeypatch.setattr(host, '_write', fail_final)
    host.cancel(running['attempt_id'])
    host._thread.join(5)
    assert host.status(running['attempt_id'])['state'] == 'quarantined'
    assert host.result(running['attempt_id'])['stop_receipt'] is None
    assert host.result(running['attempt_id'])['state'] == 'quarantined'


def test_lost_worker_lease_is_failure_not_user_cancellation(setup_chain):
    now = [1000.0]
    _, _, host, _, adapter, store, app = setup_chain(wait=True, clock=lambda: now[0])
    job = submit(app)
    dispatcher = Dispatcher(store, {'pi': adapter}, clock=lambda: now[0])
    running = dispatcher.dispatch_once()
    now[0] = 1050.0
    assert store.renew(job['job_id'], running['attempt_id'], running['fence'], 60)
    # The control-side commit succeeded, but its Worker HTTP renewal was lost.
    now[0] = 1061.0
    host._thread.join(5)
    result = host.result(running['attempt_id'])
    assert result['state'] == 'failed'
    assert result['error_code'] == 'worker_lease_expired'
    assert dispatcher.poll(running)['state'] == 'failed'


def test_worker_directory_aliases_cannot_overlap(tmp_path):
    with pytest.raises(ValueError):
        WorkerHost(ProfilePolicy('workspace-task.v1', 'r1', 'pi', 'r1', PINS),
                   SyntheticDriver('pi'), tmp_path/'state', tmp_path/'state'/'child'/'..', KEY)


def test_symlink_control_database_is_rejected(tmp_path):
    control = tmp_path/'control'
    control.mkdir(mode=0o700)
    target = tmp_path/'unrelated'
    target.write_bytes(b'unchanged')
    try:
        (control/'execution.sqlite').symlink_to(target)
    except OSError:
        pytest.skip('OS permission does not allow symlinks')
    host = WorkerHost(ProfilePolicy('workspace-task.v1', 'r1', 'pi', 'r1', PINS),
                      SyntheticDriver('pi'), control, tmp_path/'work', KEY)
    with pytest.raises(RuntimeError):
        host.initialize()
    assert target.read_bytes() == b'unchanged'


@pytest.mark.parametrize('field', ['deadline_at', 'lease_expires_at'])
@pytest.mark.parametrize('value', [float('nan'), float('inf'), -1.0])
def test_execution_clock_rejects_nonfinite(field, value):
    data = dict(job_id=str(uuid4()), attempt_id=str(uuid4()), fence=1, engine='pi', profile='workspace-task.v1',
                profile_revision='r1', runtime_revision='r1', image_digest=PINS,
                deadline_at=1000.0, lease_expires_at=1010.0, task={'prompt': 'text'})
    data[field] = value
    with pytest.raises(ValueError):
        ExecutionEnvelope(**data)
