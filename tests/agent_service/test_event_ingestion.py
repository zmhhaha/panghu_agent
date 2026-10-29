"""Worker metadata replay is durable; native prose never becomes public events."""
from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest

from agent_service.contracts import ExecutionEnvelope, ProfilePolicy, ServiceError
from agent_service.engines import NativeOutcome
from agent_service.event_ingestion import sync_worker_events
from agent_service.store import Store
from agent_service.transport import RemoteWorkerAdapter, WorkerEndpoint
from agent_service.worker_api import WorkerHost, create_worker_app


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def submit_claim(store, key='one'):
    pins = {'profile': 'workspace-task.v1', 'profile_revision': 'v1', 'engine': 'pi',
            'runtime_revision': 'r1', 'image_digest': 'sha256:' + 'a' * 64}
    request = {'api_version': '1', 'profile': pins['profile'], 'profile_revision': pins['profile_revision'],
               'business_ref': 'business-stage', 'task': {'prompt': 'private task'},
               'timeout_seconds': 600, 'inputs': []}
    store.submit('tenant', 'ppt', key, request, pins)
    job = store.claim()
    envelope = ExecutionEnvelope(job_id=job['job_id'], attempt_id=job['attempt_id'], fence=job['fence'],
        **pins, deadline_at=job['deadline_at'], lease_expires_at=job['lease_expires_at'], task=request['task'])
    return job, envelope


@pytest.fixture
def setup(tmp_path):
    clock = Clock()
    url = 'sqlite:///' + str(tmp_path / 'events.db')
    store = Store(url, allow_sqlite=True, clock=clock)
    store.initialize()
    job, envelope = submit_claim(store)
    yield store, job, envelope, clock, url
    store.close()


def event(seq, kind='engine_progress'):
    return {'seq': seq, 'type': 'engine.progress', 'payload': {'event': kind}}


def page(envelope, items, *, has_more=False, cursor=None):
    return {'attempt_id': envelope.attempt_id, 'request_sha256': envelope.request_sha256,
            'items': items, 'next_cursor': items[-1]['seq'] if cursor is None and items else cursor or 0,
            'has_more': has_more}


class ReplayAdapter:
    def __init__(self, items):
        self.items, self.calls = items, []

    def events(self, envelope, after=0, limit=100):
        self.calls.append((after, limit))
        values = deepcopy(self.items[after:after + limit])
        return page(envelope, values, cursor=after + len(values), has_more=after + len(values) < len(self.items))


def public_progress(store, job):
    cursor, found = 0, []
    while True:
        result = store.events('tenant', 'ppt', job['job_id'], cursor)
        found += [x for x in result['items'] if x['type'] == 'engine.progress']
        cursor = result['next_cursor']
        if not result['has_more']:
            return found


def test_reconnect_resumes_persisted_cursor_without_repeating_events(setup):
    store, job, envelope, clock, url = setup
    adapter = ReplayAdapter([event(x) for x in range(1, 6)])
    first = sync_worker_events(store, adapter, envelope, page_limit=2, max_pages=1)
    assert first == {'cursor': 2, 'has_more': True, 'pages': 1}
    # A fresh engine/pool represents a restarted control service, not RAM state.
    peer = Store(url, allow_sqlite=True, clock=clock)
    try:
        assert sync_worker_events(peer, adapter, envelope, page_limit=2) == {'cursor': 5, 'has_more': False, 'pages': 2}
        assert sync_worker_events(peer, adapter, envelope)['cursor'] == 5
        assert adapter.calls == [(0, 2), (2, 2), (4, 2), (5, 100)]
        assert len(public_progress(peer, job)) == 5
    finally:
        peer.close()


def test_repeated_committed_page_is_idempotent_and_cursor_is_attempt_scoped(setup):
    store, job, envelope, clock, url = setup
    args = (job['job_id'], envelope.attempt_id, envelope.fence, 0, [event(1), event(2)], 2)
    assert store.ingest_worker_events(*args) == 2
    assert store.ingest_worker_events(*args) == 2
    assert len(public_progress(store, job)) == 2
    with pytest.raises(ServiceError):
        store.worker_event_cursor(job['job_id'], str(uuid4()), envelope.fence)
    with pytest.raises(ServiceError):
        store.ingest_worker_events(job['job_id'], envelope.attempt_id, envelope.fence + 1, 0, [event(1)], 1)
    assert len(public_progress(store, job)) == 2


def test_event_saturation_advances_worker_cursor_and_preserves_terminal_capacity(tmp_path):
    clock = Clock()
    store = Store('sqlite:///' + str(tmp_path / 'bounded.db'), allow_sqlite=True, clock=clock, max_events=32)
    store.initialize()
    try:
        job, envelope = submit_claim(store)
        adapter = ReplayAdapter([event(x) for x in range(1, 501)])
        result = sync_worker_events(store, adapter, envelope)
        assert result == {'cursor': 500, 'has_more': False, 'pages': 5}
        assert len(public_progress(store, job)) == 14  # 16 slots reserved for lifecycle/terminal.
        assert sync_worker_events(store, adapter, envelope)['cursor'] == 500
        assert adapter.calls[-1] == (500, 100)
        final = store.finish(job['job_id'], envelope.attempt_id, envelope.fence,
                             {'state': 'completed', 'usage': None}, [], stopped=True)
        assert final['state'] == 'completed'
        assert store.events('tenant', 'ppt', job['job_id'])['items'][-1]['type'] == 'job.completed'
    finally:
        store.close()


@pytest.mark.parametrize('mutation', [
    lambda p: p.update(attempt_id=str(uuid4())),
    lambda p: p.update(request_sha256='0' * 64),
    lambda p: p.update(next_cursor=True),
    lambda p: p.update(next_cursor=1.0),
    lambda p: p.update(next_cursor=2),
    lambda p: p.update(has_more='false'),
    lambda p: p.update(items=[], next_cursor=0, has_more=True),
    lambda p: p.update(items=[event(2)], next_cursor=2),
    lambda p: p.update(items=[event(1), event(1)], next_cursor=2),
    lambda p: p['items'][0].update(seq=True),
    lambda p: p['items'][0].update(type='job.completed'),
    lambda p: p['items'][0].update(private_tool_data='private-secret'),
    lambda p: p['items'][0]['payload'].update(private_tool_data='private-secret'),
    lambda p: p['items'][0]['payload'].update(event='private-native-text'),
    lambda p: p['items'][0]['payload'].update(event=['engine_progress']),
    lambda p: p.update(private_tool_data='private-secret'),
])
def test_malformed_or_misbound_page_has_no_partial_commit(setup, mutation):
    store, job, envelope, _, _ = setup
    response = page(envelope, [event(1)])
    mutation(response)
    class Invalid:
        def events(self, *_args, **_kwargs):
            return response
    with pytest.raises(ServiceError) as error:
        sync_worker_events(store, Invalid(), envelope)
    assert error.value.code == 'worker_events_invalid'
    assert 'private-secret' not in str(error.value)
    assert store.worker_event_cursor(job['job_id'], envelope.attempt_id, envelope.fence) == 0
    assert public_progress(store, job) == []


def test_cross_attempt_page_cannot_enter_a_new_job_stream(setup):
    store, first, first_envelope, _, _ = setup
    response = page(first_envelope, [event(1)])
    store.finish(first['job_id'], first_envelope.attempt_id, first_envelope.fence,
                 {'state': 'completed', 'usage': None}, [], stopped=True)
    second, second_envelope = submit_claim(store, 'two')
    class WrongAttempt:
        def events(self, *_args, **_kwargs):
            return response
    with pytest.raises(ServiceError):
        sync_worker_events(store, WrongAttempt(), second_envelope)
    assert public_progress(store, second) == []


def test_stale_or_expired_authority_does_not_fetch_worker(setup):
    store, job, envelope, clock, _ = setup
    adapter = ReplayAdapter([event(1)])
    with pytest.raises(ServiceError):
        sync_worker_events(store, adapter, envelope.model_copy(update={'fence': envelope.fence + 1}))
    clock.now = job['lease_expires_at'] + 1
    with pytest.raises(ServiceError):
        sync_worker_events(store, adapter, envelope)
    assert adapter.calls == []


def test_page_budget_is_bounded_and_legacy_adapter_has_no_stream(setup):
    store, _, envelope, _, _ = setup
    assert sync_worker_events(store, object(), envelope) == {'cursor': 0, 'has_more': False, 'pages': 0}
    for values in ({'page_limit': 0}, {'page_limit': 101}, {'page_limit': True},
                   {'max_pages': 0}, {'max_pages': 6}, {'max_pages': True}):
        with pytest.raises(ValueError):
            sync_worker_events(store, object(), envelope, **values)


def test_worker_http_page_is_bound_bounded_and_contains_only_sanitized_metadata(tmp_path):
    import time
    key, token = b'receipt-fixture-key-00000000000000', 'worker-fixture-token-0000000000000'
    policy = ProfilePolicy('workspace-task.v1', 'v1', 'pi', 'r1', 'sha256:' + 'a' * 64, enabled=True)
    class Driver:
        ready = True
        engine = 'pi'
        def execute(self, request, workspace, emit, cancel):
            for _ in range(3):
                emit({'type': 'engine_progress', 'private_tool_data': 'must-stay-private'})
            return NativeOutcome('completed', output='fixture', stopped=True, resource_ids=('offline-fixture',))
    host = WorkerHost(policy, Driver(), tmp_path / 'control', tmp_path / 'workspace', key)
    now = time.time()
    envelope = ExecutionEnvelope(job_id=str(uuid4()), attempt_id=str(uuid4()), fence=1, **policy.pins(),
        deadline_at=now + 300, lease_expires_at=now + 60, task={'prompt': 'fixture'})
    with TestClient(create_worker_app(host, token)) as client:
        host.start(envelope)
        host._thread.join(5)
        def transfer(request):
            path = request.url.path + ('?' + request.url.query.decode() if request.url.query else '')
            response = client.request(request.method, path, content=request.content, headers=dict(request.headers))
            return httpx.Response(response.status_code, content=response.content)
        with httpx.Client(transport=httpx.MockTransport(transfer)) as http:
            adapter = RemoteWorkerAdapter(WorkerEndpoint('http://capsule', token, key), client=http)
            first = adapter.events(envelope, 0, 2)
            assert first == page(envelope, [event(1), event(2)], has_more=True)
            assert adapter.events(envelope, 2, 2) == page(envelope, [event(3)])
            assert adapter.events(envelope, 3, 2) == page(envelope, [], cursor=3)
            assert 'must-stay-private' not in str(first)
            with pytest.raises(ServiceError):
                adapter.events(envelope, 4)
        headers = {'Authorization': 'Bearer ' + token}
        for query in ('after=-1', 'limit=0', 'limit=101', 'after=1.5'):
            assert client.get(f'/v1/executions/{envelope.attempt_id}/events?{query}', headers=headers).status_code == 422
        assert client.get(f'/v1/executions/{envelope.attempt_id}/events?after=4', headers=headers).status_code == 409
