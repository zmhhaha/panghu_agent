"""Offline binding handoff checks; no native engine, model or approval claim."""
from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient
import httpx
import pytest

from agent_service.api import ApiSettings, create_app, public_job
from agent_service.contracts import (ExecutionBindingPins, Principal, ProfilePolicy,
                                     ProfileRegistry, ServiceError)
from agent_service.dispatcher import Dispatcher
from agent_service.store import Store
from agent_service.transport import RemoteWorkerAdapter, WorkerEndpoint


CALLER_TOKEN = 'offline-caller-token-for-binding-tests'
WORKER_TOKEN = 'offline-worker-token-for-binding-tests'
RECEIPT_KEY = b'offline-receipt-key-for-binding-tests'


def binding(**changes):
    identity = dict(binding_id='workspace-pi', binding_revision='r1',
        profile='workspace-task.v1', profile_revision='r1', contract_sha256='1' * 64,
        bundle_name='workspace-core', bundle_revision='r1', bundle_sha256='2' * 64,
        tool_environment_name='workspace-tools', tool_environment_revision='r1',
        tool_environment_sha256='3' * 64, engine='pi', runtime_revision='runtime-r1',
        image_digest='sha256:' + '4' * 64, adapter_revision='adapter-r1',
        model_policy_revision='model-r1', model_policy_sha256='5' * 64,
        acceptance_sha256='6' * 64)
    identity.update(changes)
    return ExecutionBindingPins.create(**identity)


def policy(value=None):
    value = value or binding()
    return ProfilePolicy(value.profile, value.profile_revision, value.engine,
                         value.runtime_revision, value.image_digest, enabled=True,
                         execution_binding=value)


def request():
    return dict(api_version='1', profile='workspace-task.v1', profile_revision='r1',
                business_ref='offline-business-stage', task={'prompt': 'Read input'},
                inputs=[], timeout_seconds=600)


def remote(capabilities):
    def handle(req):
        assert req.method == 'GET' and req.url.path == '/v1/capabilities'
        assert req.headers['authorization'] == 'Bearer ' + WORKER_TOKEN
        return httpx.Response(200, json=capabilities)
    return RemoteWorkerAdapter(WorkerEndpoint('http://offline-worker', WORKER_TOKEN,
                                             RECEIPT_KEY, capabilities['engine']),
                               client=httpx.Client(transport=httpx.MockTransport(handle)))


def app(store, configured):
    adapter = remote(dict(configured.pins(), ready=True, supports_inputs=True))
    principal = Principal('tenant', 'ppt', frozenset({configured.name}))
    return create_app(ApiSettings({CALLER_TOKEN: principal}), store,
                      ProfileRegistry([configured]), {configured.engine: adapter}), adapter


def headers(key='first'):
    return {'Authorization': 'Bearer ' + CALLER_TOKEN, 'Idempotency-Key': key}


class RecordingWorker:
    """Only captures the accepted envelope; it never launches an engine."""
    def __init__(self):
        self.envelopes = []

    def start(self, envelope):
        self.envelopes.append(envelope)
        return dict(attempt_id=envelope.attempt_id, request_sha256=envelope.request_sha256,
                    state='running')


def test_saved_binding_survives_registry_route_change_and_control_restart(tmp_path):
    url = 'sqlite:///' + str(tmp_path / 'queue.db')
    store = Store(url, allow_sqlite=True, clock=lambda: 1000.0)
    store.initialize()
    first = policy()
    first_app, first_remote = app(store, first)
    try:
        with TestClient(first_app) as client:
            response = client.post('/v1/jobs', json=request(), headers=headers())
            assert response.status_code == 202
            accepted = response.json()
            assert accepted['pins'] == first.pins()
            assert accepted['publication_authorized'] is False
        store.close()

        # Same public profile is now routed to a different engine/package and
        # acceptance record; the historical execution remains on its old pins.
        second = policy(binding(engine='hermes', binding_id='workspace-hermes',
                                bundle_revision='r2', bundle_sha256='7' * 64,
                                acceptance_sha256='8' * 64))
        store = Store(url, allow_sqlite=True, clock=lambda: 1001.0)
        changed_app, changed_remote = app(store, second)
        try:
            with TestClient(changed_app) as client:
                replay = client.post('/v1/jobs', json=request(), headers=headers())
                assert replay.status_code == 200
                assert replay.json() == accepted
                detail = client.get('/v1/jobs/' + accepted['job_id'], headers=headers())
                assert detail.status_code == 200 and detail.json()['pins'] == first.pins()
                submitted = client.post('/v1/jobs', json=request(), headers=headers('second'))
                assert submitted.status_code == 202 and submitted.json()['pins'] == second.pins()
                capabilities = client.get('/v1/capabilities', headers=headers()).json()['profiles']
                assert capabilities[0]['execution_binding'] == second.pins()['execution_binding']

            worker = RecordingWorker()
            dispatcher = Dispatcher(store, {'pi': worker}, clock=lambda: 1001.0)
            running = dispatcher.dispatch_once()
            envelope = worker.envelopes[0]
            assert running['job_id'] == accepted['job_id']
            assert envelope.execution_binding == first.execution_binding
            assert first.matches(envelope) and not second.matches(envelope)
            # Public allocation validation rebuilds identity from persisted
            # state; it must agree with the dispatcher, including the binding.
            allocation = store.reserve_worker(envelope, 'offline-pool', 'agent-workers',
                                              'offline-binding-worker', '9' * 64)
            assert allocation['request_sha256'] == envelope.request_sha256
            forged = envelope.model_copy(update={'execution_binding': second.execution_binding})
            with pytest.raises(ServiceError, match='worker_allocation_conflict'):
                store.reserve_worker(forged, 'offline-pool', 'agent-workers',
                                     'offline-binding-worker', '9' * 64)
        finally:
            changed_remote.close()
    finally:
        first_remote.close()
        store.close()


@pytest.mark.parametrize('changes', [
    {'bundle_revision': 'r2'}, {'bundle_sha256': '7' * 64},
    {'acceptance_sha256': '8' * 64}, {'contract_sha256': '9' * 64},
    {'tool_environment_revision': 'r2'}, {'tool_environment_sha256': 'a' * 64},
    {'adapter_revision': 'adapter-r2'}, {'model_policy_sha256': 'b' * 64},
    {'binding_revision': 'r2'},
])
def test_readiness_rejects_different_binding_with_identical_base_pins(changes):
    expected = policy()
    advertised = policy(binding(**changes))
    assert all(expected.pins()[key] == advertised.pins()[key] for key in
               ('profile', 'profile_revision', 'engine', 'runtime_revision', 'image_digest'))
    adapter = remote(dict(advertised.pins(), ready=True))
    try:
        assert adapter.ready(advertised) is True
        assert adapter.ready(expected) is False
    finally:
        adapter.close()


@pytest.mark.parametrize('bound_policy,bound_worker', [(True, False), (False, True)])
def test_readiness_rejects_missing_or_extra_binding(bound_policy, bound_worker):
    configured = policy()
    plain = replace(configured, execution_binding=None)
    advertised = configured if bound_worker else plain
    adapter = remote(dict(advertised.pins(), ready=True))
    try:
        assert adapter.ready(configured if bound_policy else plain) is False
    finally:
        adapter.close()


@pytest.mark.parametrize('bound', [False, True])
def test_binding_readiness_keeps_input_staging_requirement(bound):
    configured = policy() if bound else replace(policy(), execution_binding=None)
    adapter = remote(dict(configured.pins(), ready=True, supports_inputs=False))
    try:
        assert adapter.ready(configured) is True
        assert adapter.ready(replace(configured, supports_inputs=True)) is False
    finally:
        adapter.close()


@pytest.mark.parametrize('field,value', [
    ('engine', 'hermes'), ('runtime_revision', 'runtime-other'),
    ('image_digest', 'sha256:' + 'b' * 64), ('profile', 'other-task.v1'),
    ('profile_revision', 'r2'),
])
def test_store_rejects_binding_inconsistent_with_outer_pins(tmp_path, field, value):
    store = Store('sqlite:///' + str(tmp_path / 'queue.db'), allow_sqlite=True)
    store.initialize()
    try:
        pins = policy().pins()
        pins[field] = value
        with pytest.raises(ServiceError, match='invalid_execution_pins'):
            store.submit('tenant', 'ppt', 'one', request(), pins)
    finally:
        store.close()


@pytest.mark.parametrize('change', [
    {'sha256': 'f' * 64}, {'combination_sha256': 'e' * 64},
    {'api_key': 'must-never-be-exposed'}, {'acceptance_sha256': 'not-a-hash'},
])
def test_store_and_public_api_reject_unvalidated_binding_metadata(tmp_path, change):
    store = Store('sqlite:///' + str(tmp_path / 'queue.db'), allow_sqlite=True)
    store.initialize()
    try:
        pins = policy().pins()
        pins['execution_binding'].update(change)
        with pytest.raises(ServiceError, match='invalid_execution_pins'):
            store.submit('tenant', 'ppt', 'one', request(), pins)
        with pytest.raises(ServiceError, match='invalid_execution_pins'):
            public_job(dict(pins=pins, request=request(), state='queued'))
    finally:
        store.close()


def test_store_rejects_binding_profile_different_from_request(tmp_path):
    store = Store('sqlite:///' + str(tmp_path / 'queue.db'), allow_sqlite=True)
    store.initialize()
    try:
        changed_request = dict(request(), profile='other-task.v1')
        with pytest.raises(ServiceError, match='invalid_execution_pins'):
            store.submit('tenant', 'ppt', 'one', changed_request, policy().pins())
    finally:
        store.close()
