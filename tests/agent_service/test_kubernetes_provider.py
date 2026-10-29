"""Real queue + fake Kubernetes + bounded Worker HTTP, with no model calls.

The fake API observes Pod lifecycle and deletion preconditions. The HTTP Worker
is an explicit protocol fixture; it neither claims native engine acceptance nor
supplies the outer Pod stop observation used to release the queue slot.
"""
from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest

from agent_service.api import ApiSettings, create_app
from agent_service.contracts import (ExecutionEnvelope, InlineFile, ProfilePolicy, ServiceError,
    Principal, ProfileRegistry, WorkerResult, canonical_bytes, sign_receipt, validate_workspace_task)
from agent_service.dispatcher import Dispatcher
from agent_service.kubernetes_provider import FINALIZER, PREFIX, KubernetesPool, KubernetesWorkerAdapter
from agent_service.store import Store
from agent_service.transport import RemoteWorkerAdapter


MASTER_TOKEN = 'offline-pool-master-token-00000000000000'
MASTER_RECEIPT = b'offline-pool-master-receipt-key-00000000'


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeKubernetes:
    def __init__(self, harness, *, cold=False, auto_terminate=True):
        self.h, self.cold, self.auto_terminate = harness, cold, auto_terminate
        self.objects = {'pods': {}, 'secrets': {}, 'configmaps': {}}
        self.calls, self.trace = [], []
        self.rv, self.pod_number = 0, 0
        self.lose_create_response = False
        self.lose_cleanup_response = None
        self.never_accept_create = False

    def timestamp(self):
        return datetime.fromtimestamp(self.h.clock(), timezone.utc).isoformat().replace('+00:00', 'Z')

    def version(self, value):
        self.rv += 1
        value['metadata']['resourceVersion'] = str(self.rv)

    def host_config(self):
        policy = self.h.pool.policy
        data = {'schema_version': 1, 'engine': policy.engine, 'profile': {
            'name': policy.name, 'revision': policy.revision, 'runtime_revision': policy.runtime_revision,
            'image_digest': policy.image_digest, 'enabled': policy.enabled,
            'max_timeout_seconds': policy.max_timeout_seconds}, 'model': None}
        result = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'immutable': True,
            'metadata': {'name': self.h.pool.host_config_map, 'namespace': self.h.pool.namespace,
                         'uid': str(uuid4()), 'resourceVersion': '1'},
            'data': {'config.json': json.dumps(data)}}
        self.objects['configmaps'][self.h.pool.host_config_map] = result

    def ready_pod(self, name):
        pod = self.objects['pods'][name]
        if 'podIP' not in pod.get('status', {}):
            self.pod_number += 1
            pod['status'] = {'phase': 'Running', 'podIP': '10.244.1.' + str(self.pod_number),
                'containerStatuses': [{'name': 'worker', 'restartCount': 0,
                    'state': {'running': {'startedAt': self.timestamp()}}}]}
        self.version(pod)
        return pod

    def terminate_pod(self, name):
        pod = self.objects['pods'][name]
        status = pod.setdefault('status', {})
        status['phase'] = 'Succeeded'
        status['containerStatuses'] = [{'name': 'worker', 'restartCount': 0, 'state': {'terminated': {
            'exitCode': 0, 'reason': 'Completed', 'finishedAt': self.timestamp(),
            'containerID': 'containerd://' + pod['metadata']['uid'].replace('-', '') * 2}}}]
        self.version(pod)
        return pod

    def request(self, method, path, body=None, *, content_type='application/json'):
        parsed = urlsplit(path)
        segments = parsed.path.strip('/').split('/')
        assert segments[:4] == ['api', 'v1', 'namespaces', self.h.pool.namespace]
        kind, name = segments[4], segments[5] if len(segments) == 6 else None
        self.calls.append((method, kind, name))
        if method == 'GET':
            if name is None:
                return {'items': deepcopy(list(self.objects[kind].values()))}
            if name not in self.objects[kind]:
                raise ServiceError('kubernetes_not_found', 404)
            return deepcopy(self.objects[kind][name])
        if method == 'POST':
            assert name is None
            name = body['metadata']['name']
            if name in self.objects[kind]:
                raise ServiceError('kubernetes_conflict', 409)
            if kind == 'pods' and self.never_accept_create:
                raise ServiceError('kubernetes_unavailable', 503)
            value = deepcopy(body)
            value['metadata']['uid'] = str(uuid4())
            self.version(value)
            self.objects[kind][name] = value
            if kind == 'pods':
                # API-server defaults are harmless; user command/security and
                # mounted resources must still be exactly the reviewed plan.
                value['spec'].update(nodeName='fixture-node', dnsPolicy='ClusterFirst',
                                     schedulerName='default-scheduler')
                container = value['spec']['containers'][0]
                container.update(terminationMessagePath='/dev/termination-log', terminationMessagePolicy='File')
                for item in container['env']:
                    if 'valueFrom' in item:
                        item['valueFrom']['fieldRef']['apiVersion'] = 'v1'
                value['status'] = {'phase': 'Pending'}
                if not self.cold:
                    self.ready_pod(name)
                if self.lose_create_response:
                    self.lose_create_response = False
                    raise ServiceError('kubernetes_unavailable', 503)
            return deepcopy(value)
        if method == 'DELETE':
            value = self.objects[kind].get(name)
            if value is None:
                raise ServiceError('kubernetes_not_found', 404)
            assert body['preconditions']['uid'] == value['metadata']['uid']
            if kind == 'pods':
                attempt = value['metadata']['labels'][PREFIX + 'attempt']
                allocation = self.h.store.get_worker_allocation(attempt)
                self.trace.append({'operation': 'delete-pod', 'attempt': attempt,
                    'candidate_persisted': allocation['candidate_result'] is not None,
                    'uid': allocation['pod_uid']})
                value['metadata']['deletionTimestamp'] = self.timestamp()
                self.version(value)
                if self.auto_terminate:
                    self.terminate_pod(name)
                if not value['metadata'].get('finalizers'):
                    del self.objects[kind][name]
            else:
                del self.objects[kind][name]
                if self.lose_cleanup_response == 'secret-delete':
                    self.lose_cleanup_response = None
                    raise ServiceError('kubernetes_unavailable', 503)
            return {'kind': 'Status', 'status': 'Success'}
        if method == 'PATCH':
            assert kind == 'pods' and content_type == 'application/json-patch+json'
            value = self.objects[kind][name]
            assert body[0] == {'op': 'test', 'path': '/metadata/uid', 'value': value['metadata']['uid']}
            assert body[1] == {'op': 'test', 'path': '/metadata/resourceVersion',
                               'value': value['metadata']['resourceVersion']}
            assert body[2]['op'] == 'replace' and body[2]['path'] == '/metadata/finalizers'
            allocation = self.h.store.get_worker_allocation(value['metadata']['labels'][PREFIX + 'attempt'])
            assert allocation['stop_observed_at'] is not None
            value['metadata']['finalizers'] = deepcopy(body[2]['value'])
            self.version(value)
            reply = deepcopy(value)
            if value['metadata'].get('deletionTimestamp') and not value['metadata']['finalizers']:
                del self.objects[kind][name]
            if self.lose_cleanup_response == 'patch':
                self.lose_cleanup_response = None
                raise ServiceError('kubernetes_unavailable', 503)
            return reply
        raise AssertionError('Unexpected fixture API operation')

    def close(self):
        pass


class WorkerHTTPFixture:
    def __init__(self, harness):
        self.h, self.executions, self.calls = harness, {}, []
        self.ready = True
        self.lose_start_response = False

    def factory(self, endpoint):
        return RemoteWorkerAdapter(endpoint, client=httpx.Client(
            transport=httpx.MockTransport(lambda request: self.request(endpoint, request)), trust_env=False))

    def request(self, endpoint, request):
        pod = next(value for value in self.h.kube.objects['pods'].values()
                   if value.get('status', {}).get('podIP') == request.url.host)
        assert pod['metadata'].get('deletionTimestamp') is None
        secret_name = pod['metadata']['name'] + '-auth'
        secret = json.loads(base64.b64decode(self.h.kube.objects['secrets'][secret_name]['data']['worker.json']))
        assert request.headers['authorization'] == 'Bearer ' + secret['worker_token']
        assert endpoint.receipt_key.hex() == secret['receipt_key_hex']
        path = request.url.path.strip('/').split('/')
        self.calls.append((request.method, request.url.path))
        if path == ['v1', 'capabilities']:
            return httpx.Response(200, json={'api_version': '1', **self.h.pool.policy.pins(), 'ready': self.ready,
                                           'supports_inputs': False})
        attempt = path[2]
        if request.method == 'PUT':
            envelope = ExecutionEnvelope.model_validate_json(request.content)
            assert envelope.attempt_id == attempt
            assert self.h.store.get_worker_allocation(attempt)['worker_start_claimed'] is True
            assert attempt not in self.executions, 'A second native PUT would repeat a paid attempt'
            self.executions[attempt] = {'envelope': envelope, 'state': 'running', 'key': endpoint.receipt_key,
                'result': None, 'events': [
                    {'seq': 1, 'type': 'engine.progress', 'payload': {'event': 'engine_started'}},
                    {'seq': 2, 'type': 'engine.progress', 'payload': {'event': 'engine_progress'}}]}
            if self.lose_start_response:
                self.lose_start_response = False
                raise httpx.ReadError('Fixture admission response lost')
        value = self.executions.get(attempt)
        if value is None:
            return httpx.Response(404, json={'detail': {'code': 'execution_missing'}})
        status = {'attempt_id': attempt, 'request_sha256': value['envelope'].request_sha256, 'state': value['state']}
        if len(path) == 3:
            return httpx.Response(200, json=status)
        if path[3] == 'events':
            query = parse_qs(request.url.query.decode())
            after, limit = int(query.get('after', ['0'])[0]), int(query.get('limit', ['100'])[0])
            items = value['events'][after:after + limit]
            return httpx.Response(200, json={'attempt_id': attempt, 'request_sha256': status['request_sha256'],
                'items': items, 'next_cursor': after + len(items), 'has_more': after + len(items) < len(value['events'])})
        if path[3] == 'lease':
            return httpx.Response(200, json=status)
        if path[3] == 'result':
            if value['result'] is None:
                return httpx.Response(409, json={'detail': {'code': 'result_not_ready'}})
            return httpx.Response(200, json=value['result'].model_dump())
        raise AssertionError('Unexpected fixture Worker operation')

    def complete(self, attempt):
        value = self.executions[attempt]
        envelope = value['envelope']
        result = WorkerResult(state='completed', usage=None, artifacts=[InlineFile.from_bytes(
            'response.json', canonical_bytes({'schema_version': '1', 'text': 'offline candidate'}))])
        receipt = sign_receipt(envelope, result, value['key'], ('offline-native:' + attempt,), self.h.clock())
        value['result'], value['state'] = result.model_copy(update={'stop_receipt': receipt}), 'completed'

    def puts(self):
        return [path for method, path in self.calls if method == 'PUT']


class Harness:
    def __init__(self, root, *, engine='pi', cold=False, auto_terminate=True):
        self.clock, self.url = Clock(), 'sqlite:///' + str(root / 'queue.db')
        policy = ProfilePolicy('workspace-task.v1', 'v1', engine, 'r1', 'sha256:' + 'a' * 64,
                               enabled=True, task_validator=validate_workspace_task)
        self.pool = KubernetesPool('workspace-' + engine, policy, 'agent-workers',
            'registry.internal/agent-worker@' + policy.image_digest, 'agent-host', ('10.244.0.0/16',), enabled=True,
            uid_start=1000000, uid_count=65536)
        self.store = Store(self.url, allow_sqlite=True, clock=self.clock)
        self.store.initialize()
        self.kube = FakeKubernetes(self, cold=cold, auto_terminate=auto_terminate)
        self.kube.host_config()
        self.worker = WorkerHTTPFixture(self)
        self.rebuild_control()

    def rebuild_control(self, *, reopen=False):
        if reopen:
            self.store.close()
            self.store = Store(self.url, allow_sqlite=True, clock=self.clock)
        self.adapter = KubernetesWorkerAdapter(self.store, self.pool, self.kube, MASTER_TOKEN, MASTER_RECEIPT,
                                              client_factory=self.worker.factory, clock=self.clock)
        self.dispatcher = Dispatcher(self.store, {self.pool.policy.engine: self.adapter}, clock=self.clock)

    def submit(self, key='one', *, timeout_seconds=600):
        policy = self.pool.policy
        request = {'api_version': '1', 'profile': policy.name, 'profile_revision': policy.revision,
            'business_ref': 'business-' + key, 'task': {'prompt': 'offline protocol fixture'},
            'inputs': [], 'timeout_seconds': timeout_seconds}
        return self.store.submit('tenant', 'ppt', key, request, policy.pins())[0]

    def current(self, job):
        return self.store.get_job('tenant', 'ppt', job['job_id'])

    def allocation(self, job):
        return self.store.get_worker_allocation(self.current(job)['attempt_id'])

    def pod(self, job):
        return self.kube.objects['pods'][self.allocation(job)['pod_name']]

    def progress(self, job):
        return [value for value in self.store.events('tenant', 'ppt', job['job_id'])['items']
                if value['type'] == 'engine.progress']


@pytest.fixture
def make_harness(tmp_path):
    opened = []
    def make(**options):
        root = tmp_path / str(uuid4())
        root.mkdir()
        value = Harness(root, **options)
        opened.append(value)
        return value
    yield make
    for value in opened:
        value.store.close()


@pytest.mark.parametrize('selector', [
    {'kubernetes.io/hostname': 'worker-a'},
    [('kubernetes.io/hostname', 'worker-a')],
    (['kubernetes.io/hostname', 'worker-a'],),
    (('kubernetes.io/hostname', 'worker-a'), ('kubernetes.io/hostname', 'worker-b')),
    (('kubernetes.io/hostname', 1),),
    (('Invalid_Prefix/host', 'worker-a'),),
    (('kubernetes.io/hostname', 'worker-a\n'),),
])
def test_pool_constructor_rejects_mutable_or_invalid_node_selectors(selector):
    policy = ProfilePolicy('workspace-task.v1', 'v1', 'pi', 'r1', 'sha256:' + 'a' * 64)
    with pytest.raises(ValueError, match='Invalid fixed Kubernetes node selector'):
        KubernetesPool('pi-pool', policy, 'agent-workers',
            'registry/worker@' + policy.image_digest, 'agent-host', ('10.244.0.0/16',),
            node_selector=selector)


def test_explicit_node_selector_survives_real_dispatch_and_measured_pod_roundtrip(make_harness):
    h = make_harness()
    selector = (('kubernetes.io/hostname', 'fixture-node'),
                ('agent-service.panghuer.top/apparmor', 'loaded'))
    h.pool = replace(h.pool, node_selector=selector)
    h.rebuild_control()
    job = h.submit()
    running = h.dispatcher.dispatch_once()
    assert running['state'] == 'running'
    assert h.pod(job)['spec']['nodeSelector'] == dict(selector)
    h.worker.complete(running['attempt_id'])
    assert h.dispatcher.poll(running)['state'] == 'completed'
    assert len(h.worker.puts()) == 1
    assert h.store.active() is None


@pytest.mark.parametrize('engine', ['pi', 'hermes'])
def test_two_sequential_jobs_have_distinct_pods_and_single_native_admissions(make_harness, engine):
    h = make_harness(engine=engine)
    identities = []
    runtime_uids = []
    for key in ('one', 'two'):
        job = h.submit(key)
        running = h.dispatcher.dispatch_once()
        assert running['state'] == 'running'
        allocation = h.allocation(job)
        identities.append((allocation['pod_name'], allocation['pod_uid']))
        expected_uid = h.pool.uid_start + 2 * (running['fence'] - 1)
        env = {item['name']: item.get('value') for item in h.pod(job)['spec']['containers'][0]['env']}
        assert env['AGENT_RUNTIME_UID'] == str(expected_uid)
        assert allocation['agent_uid'] == expected_uid
        assert env['AGENT_PREVIEW_UID'] == str(expected_uid + 1)
        assert allocation['aux_uid'] == expected_uid + 1
        runtime_uids.append(expected_uid)
        h.worker.complete(running['attempt_id'])
        completed = h.dispatcher.poll(running)
        assert completed['state'] == 'completed'
        assert h.store.active() is None
        assert len(h.store.get_artifacts('tenant', 'ppt', job['job_id'])) == 1
        assert len(h.progress(job)) == 2
        assert h.allocation(job)['retired'] is True
    assert len(set(identities)) == 2
    assert len(set(runtime_uids)) == 2
    assert len([call for call in h.kube.calls if call[:2] == ('POST', 'pods')]) == 2
    assert len(h.worker.puts()) == 2
    assert h.kube.objects['pods'] == h.kube.objects['secrets'] == {}
    assert all(value['candidate_persisted'] for value in h.kube.trace)


def test_exhausted_uid_range_rejects_queued_job_without_allocating_or_holding_slot(make_harness):
    h = make_harness()
    h.pool = replace(h.pool, uid_count=2)
    h.rebuild_control()
    assert h.adapter.ready(h.pool.policy) is True
    first = h.submit('first')
    h.clock.now += 0.1
    second = h.submit('already-queued')
    running = h.dispatcher.dispatch_once()
    assert running['job_id'] == first['job_id'] and running['state'] == 'running'
    h.worker.complete(running['attempt_id'])
    assert h.dispatcher.poll(running)['state'] == 'completed'
    assert h.store.active() is None and h.current(second)['state'] == 'queued'
    assert h.adapter.ready(h.pool.policy) is False
    create_calls = [call for call in h.kube.calls if call[0] == 'POST']
    native_calls = list(h.worker.calls)
    failed = h.dispatcher.dispatch_once()
    assert failed['job_id'] == second['job_id'] and failed['state'] == 'failed'
    assert failed['outcome']['error_code'] == 'worker_uid_range_exhausted'
    with pytest.raises(ServiceError, match='worker_allocation_missing'):
        h.allocation(second)
    assert h.store.active() is None
    assert h.kube.objects['pods'] == h.kube.objects['secrets'] == {}
    assert [call for call in h.kube.calls if call[0] == 'POST'] == create_calls
    assert h.worker.calls == native_calls and len(h.worker.puts()) == 1
    h.rebuild_control(reopen=True)
    assert h.adapter.ready(h.pool.policy) is False
    assert h.dispatcher.reconcile() is None
    assert h.current(second)['outcome']['error_code'] == 'worker_uid_range_exhausted'


def test_restart_after_claim_before_reservation_creates_original_attempt_only_once(make_harness):
    h = make_harness()
    job = h.submit()
    claimed = h.store.claim(lease_seconds=60,
        provisioning={h.pool.policy.engine: 'kubernetes_per_attempt'})
    assert claimed['state'] == 'starting' and h.kube.calls == [] and h.worker.calls == []
    with pytest.raises(ServiceError, match='worker_allocation_missing'):
        h.allocation(job)
    h.rebuild_control(reopen=True)
    running = h.dispatcher.reconcile()
    assert running['state'] == 'running'
    assert (running['job_id'], running['attempt_id'], running['fence']) == (
        claimed['job_id'], claimed['attempt_id'], claimed['fence'])
    original = h.allocation(job)
    h.rebuild_control(reopen=True)
    assert h.dispatcher.reconcile()['state'] == 'running'
    assert h.allocation(job)['pod_uid'] == original['pod_uid']
    assert len([call for call in h.kube.calls if call[:2] == ('POST', 'pods')]) == 1
    assert len(h.worker.puts()) == 1
    h.worker.complete(running['attempt_id'])
    assert h.dispatcher.poll(h.current(job))['state'] == 'completed'
    assert h.store.active() is None


@pytest.mark.parametrize(('interruption', 'expected_state'), [
    ('cancel', 'cancelled'), ('deadline', 'timed_out'), ('lease', 'failed'),
])
def test_restart_after_claim_without_reservation_safely_finishes_interrupted_job(
        make_harness, interruption, expected_state):
    h = make_harness()
    job = h.submit(timeout_seconds=15 if interruption == 'deadline' else 600)
    claimed = h.store.claim(lease_seconds=60,
        provisioning={h.pool.policy.engine: 'kubernetes_per_attempt'})
    if interruption == 'cancel':
        assert h.store.cancel('tenant', 'ppt', job['job_id'])['state'] == 'cancelling'
    elif interruption == 'deadline':
        h.clock.now = claimed['deadline_at'] + 1
        assert h.clock() < claimed['lease_expires_at']
    else:
        h.clock.now = claimed['lease_expires_at'] + 1
        assert h.clock() < claimed['deadline_at']
    h.rebuild_control(reopen=True)
    resolved = h.dispatcher.reconcile()
    assert resolved['state'] == expected_state
    assert resolved['outcome']['stop_receipt'] is None
    assert (resolved['attempt_id'], resolved['fence']) == (claimed['attempt_id'], claimed['fence'])
    assert h.store.active() is None
    with pytest.raises(ServiceError, match='worker_allocation_missing'):
        h.allocation(job)
    assert h.kube.objects['pods'] == h.kube.objects['secrets'] == {}
    assert not any(method in {'POST', 'PATCH', 'DELETE'} for method, _, _ in h.kube.calls)
    assert h.worker.calls == []
    assert h.store.get_artifacts('tenant', 'ppt', job['job_id']) == []
    h.rebuild_control(reopen=True)
    assert h.dispatcher.reconcile() is None
    assert h.current(job)['state'] == expected_state


def test_frozen_static_attempt_cannot_be_taken_over_or_released_by_kubernetes_pool(make_harness):
    h = make_harness()
    job = h.submit()
    # A controller crash after static admission can leave no Kubernetes row.
    # Changing deployment configuration must not reinterpret that absence as
    # evidence the original static Worker was never launched.
    claimed = h.store.claim(lease_seconds=60)
    h.rebuild_control(reopen=True)
    envelope = h.dispatcher._envelope(h.current(job))
    with pytest.raises(ServiceError):
        h.adapter.inspect(envelope)
    assert h.adapter.resolve_uncreated(envelope) is None
    h.clock.now = claimed['lease_expires_at'] + 1
    assert h.dispatcher.reconcile()['state'] == 'quarantined'
    assert h.store.active()['job_id'] == job['job_id']
    with pytest.raises(ServiceError, match='worker_allocation_missing'):
        h.allocation(job)
    assert h.kube.objects['pods'] == h.kube.objects['secrets'] == {}
    assert not any(method in {'POST', 'PATCH', 'DELETE'} for method, _, _ in h.kube.calls)
    assert h.worker.calls == []
    assert h.store.get_artifacts('tenant', 'ppt', job['job_id']) == []


def test_cancel_reserved_plan_before_create_retires_uid_and_prevents_late_pod_launch(make_harness):
    h = make_harness()
    job = h.submit()
    claimed = h.store.claim(lease_seconds=60,
        provisioning={h.pool.policy.engine: 'kubernetes_per_attempt'})
    envelope = h.dispatcher._envelope(claimed)
    allocation, _, secret = h.adapter._allocation(envelope, reserve=True)
    assert allocation['create_claimed'] is False and allocation['retired'] is False
    assert allocation['pod_uid'] is None
    # Simulate the real crash window after credential creation but before the
    # durable CAS authorizes the only Pod-create request.
    h.adapter._ensure_secret(secret)
    assert h.kube.objects['secrets'] and h.kube.objects['pods'] == {}
    h.store.cancel('tenant', 'ppt', job['job_id'])
    h.rebuild_control(reopen=True)
    resolved = h.dispatcher.reconcile()
    assert resolved['state'] == 'cancelled' and h.store.active() is None
    retired = h.allocation(job)
    assert retired['retired'] is True and retired['create_claimed'] is False
    assert retired['pod_uid'] is None and retired['agent_uid'] == allocation['agent_uid']
    assert retired['stop_observed_at'] is None and resolved['outcome']['stop_receipt'] is None
    with pytest.raises(ServiceError):
        h.store.claim_worker_create(envelope.attempt_id, envelope.request_sha256)
    assert not any(method == 'POST' and kind == 'pods' for method, kind, _ in h.kube.calls)
    assert h.worker.calls == [] and h.kube.objects['pods'] == {}
    assert h.kube.objects['secrets'] == {}
    assert h.store.get_artifacts('tenant', 'ppt', job['job_id']) == []
    h.rebuild_control(reopen=True)
    assert h.dispatcher.reconcile() is None and h.current(job)['state'] == 'cancelled'


def test_cold_capsule_stays_starting_until_native_ready_then_admits_once(make_harness):
    h = make_harness(cold=True)
    job = h.submit()
    cold = h.dispatcher.dispatch_once()
    assert cold['state'] == 'starting' and h.worker.puts() == []
    assert h.dispatcher.poll(cold)['state'] == 'starting'
    assert not any(item['type'] == 'job.running' for item in h.store.events('tenant', 'ppt', job['job_id'])['items'])
    h.kube.ready_pod(h.allocation(job)['pod_name'])
    running = h.dispatcher.poll(cold)
    assert running['state'] == 'running' and len(h.worker.puts()) == 1
    h.dispatcher.poll(running)
    assert len(h.worker.puts()) == 1


@pytest.mark.parametrize('lost', ['pod-create', 'native-start'])
def test_lost_admission_response_inspects_original_resource_without_repeating_work(make_harness, lost):
    h = make_harness()
    h.kube.lose_create_response = lost == 'pod-create'
    h.worker.lose_start_response = lost == 'native-start'
    job = h.submit()
    running = h.dispatcher.dispatch_once()
    assert running['state'] == 'running'
    original = h.allocation(job)
    h.rebuild_control(reopen=True)
    assert h.dispatcher.reconcile()['state'] == 'running'
    assert h.allocation(job)['pod_uid'] == original['pod_uid']
    assert len([call for call in h.kube.calls if call[:2] == ('POST', 'pods')]) == 1
    assert len(h.worker.puts()) == 1


def test_claimed_create_without_observable_pod_never_recreates_after_restart(make_harness):
    h = make_harness()
    h.kube.never_accept_create = True
    job = h.submit()
    assert h.dispatcher.dispatch_once()['state'] == 'quarantined'
    h.kube.never_accept_create = False
    h.rebuild_control(reopen=True)
    assert h.dispatcher.tick()['state'] == 'quarantined'
    assert len([call for call in h.kube.calls if call[:2] == ('POST', 'pods')]) == 1
    assert h.worker.puts() == [] and h.store.active()['job_id'] == job['job_id']
    allocation = h.allocation(job)
    assert allocation['create_claimed'] is True and allocation['pod_uid'] is None
    h.clock.now = h.current(job)['lease_expires_at'] + 1
    h.rebuild_control(reopen=True)
    assert h.dispatcher.reconcile()['state'] == 'quarantined'
    assert h.store.active()['job_id'] == job['job_id']
    assert h.store.resolve_uncreated_attempt(h.dispatcher._envelope(h.current(job))) is None
    assert h.store.active()['job_id'] == job['job_id']
    assert h.allocation(job)['retired'] is False
    assert h.allocation(job)['stop_observed_at'] is None
    assert len([call for call in h.kube.calls if call[:2] == ('POST', 'pods')]) == 1
    assert h.worker.calls == []


def test_restart_uses_durable_candidate_before_stopping_and_never_re_admits(make_harness):
    h = make_harness(auto_terminate=False)
    job = h.submit()
    running = h.dispatcher.dispatch_once()
    h.worker.complete(running['attempt_id'])
    assert h.dispatcher.poll(running)['state'] == 'running'
    allocation = h.allocation(job)
    assert allocation['candidate_result'] is not None and allocation['stop_observed_at'] is None
    assert h.store.get_artifacts('tenant', 'ppt', job['job_id']) == []
    assert h.kube.trace[0]['candidate_persisted'] is True
    # Original allocation/pins remain authoritative during drain even if the
    # deployment source ConfigMap has subsequently been removed.
    h.kube.objects['configmaps'].clear()
    h.rebuild_control(reopen=True)
    h.kube.terminate_pod(allocation['pod_name'])
    completed = h.dispatcher.reconcile()
    assert completed['state'] == 'completed'
    assert len(h.worker.puts()) == 1
    assert len([call for call in h.kube.calls if call[:2] == ('POST', 'pods')]) == 1
    assert h.allocation(job)['retired'] is True and h.store.active() is None


@pytest.mark.parametrize('lost', ['patch', 'secret-delete'])
def test_lost_cleanup_response_recovers_persisted_stop_without_worker_access(make_harness, lost):
    h = make_harness()
    job = h.submit()
    running = h.dispatcher.dispatch_once()
    h.worker.complete(running['attempt_id'])
    h.kube.lose_cleanup_response = lost
    pending = h.dispatcher.poll(running)
    assert pending['state'] == 'running'
    allocation = h.allocation(job)
    assert allocation['stop_observed_at'] is not None and allocation['retired'] is False
    worker_calls = len(h.worker.calls)
    h.rebuild_control(reopen=True)
    assert h.dispatcher.reconcile()['state'] == 'completed'
    assert len(h.worker.calls) == worker_calls
    assert h.allocation(job)['retired'] is True


def test_replaced_pod_uid_or_404_cannot_prove_stop_or_start_replacement(make_harness):
    h = make_harness()
    job = h.submit()
    running = h.dispatcher.dispatch_once()
    allocation = h.allocation(job)
    pod = h.pod(job)
    original_uid = pod['metadata']['uid']
    pod['metadata']['uid'] = str(uuid4())
    with pytest.raises(ServiceError):
        h.adapter.inspect(h.dispatcher._envelope(running))
    assert h.allocation(job)['pod_uid'] == original_uid
    # A vanished resource might have run; its name cannot be used to create a
    # new physical attempt and absence is not affirmative stop observation.
    del h.kube.objects['pods'][allocation['pod_name']]
    assert h.dispatcher.poll(running)['state'] == 'running'
    h.clock.now = h.current(job)['lease_expires_at'] + 1
    assert h.dispatcher.tick()['state'] == 'quarantined'
    assert h.store.active()['job_id'] == job['job_id']
    assert h.store.get_artifacts('tenant', 'ppt', job['job_id']) == []
    assert len(h.worker.puts()) == 1
    assert len([call for call in h.kube.calls if call[:2] == ('POST', 'pods')]) == 1


def test_missing_finalizer_keeps_result_private_and_quarantines(make_harness):
    h = make_harness()
    job = h.submit()
    running = h.dispatcher.dispatch_once()
    h.worker.complete(running['attempt_id'])
    h.pod(job)['metadata']['finalizers'] = []
    assert h.dispatcher.poll(running)['state'] == 'quarantined'
    assert h.store.active()['job_id'] == job['job_id']
    assert h.store.get_artifacts('tenant', 'ppt', job['job_id']) == []


@pytest.mark.parametrize('mutate', [
    lambda pod: pod['status']['containerStatuses'][0]['state']['terminated'].update(reason='ContainerStatusUnknown'),
    lambda pod: pod['status']['containerStatuses'][0]['state']['terminated'].update(finishedAt='1970-01-01T00:00:00Z'),
    lambda pod: pod['status']['containerStatuses'][0]['state']['terminated'].update(finishedAt='1970-01-01T00:16:40'),
    lambda pod: pod['status']['containerStatuses'][0]['state']['terminated'].update(finishedAt='2999-01-01T00:00:00Z'),
    lambda pod: pod['status']['containerStatuses'][0]['state']['terminated'].update(containerID=''),
    lambda pod: pod['status']['containerStatuses'][0]['state']['terminated'].update(containerID=42),
    lambda pod: pod['status'].update(containerStatuses=[]),
    lambda pod: pod['status']['containerStatuses'].append({'name': 'unexpected-helper', 'restartCount': 0,
                                                         'state': {'running': {}}}),
])
def test_incomplete_or_unknown_container_stop_never_exports_candidate(make_harness, mutate):
    h = make_harness(auto_terminate=False)
    job = h.submit()
    running = h.dispatcher.dispatch_once()
    h.worker.complete(running['attempt_id'])
    assert h.dispatcher.poll(running)['state'] == 'running'  # candidate durable, Pod deletion pending.
    pod = h.kube.terminate_pod(h.allocation(job)['pod_name'])
    mutate(pod)
    pending = h.dispatcher.poll(h.current(job))
    assert pending['state'] == 'running'
    assert h.allocation(job)['stop_observed_at'] is None
    assert h.store.get_artifacts('tenant', 'ppt', job['job_id']) == []
    assert h.store.active()['job_id'] == job['job_id']


def test_cancel_while_cold_stops_capsule_without_any_native_admission(make_harness):
    h = make_harness(cold=True)
    job = h.submit()
    cold = h.dispatcher.dispatch_once()
    assert cold['state'] == 'starting'
    h.store.cancel('tenant', 'ppt', job['job_id'])
    assert h.dispatcher.poll(cold)['state'] == 'cancelled'
    assert h.worker.puts() == [] and h.store.active() is None
    assert h.store.get_artifacts('tenant', 'ppt', job['job_id']) == []
    assert h.allocation(job)['retired'] is True


def test_quarantine_releases_slot_only_after_original_pod_stop_and_discards_candidate(make_harness):
    h = make_harness(auto_terminate=False)
    job = h.submit()
    running = h.dispatcher.dispatch_once()
    h.worker.complete(running['attempt_id'])
    assert h.dispatcher.poll(running)['state'] == 'running'
    assert h.allocation(job)['candidate_result']['artifacts']
    h.clock.now = h.current(job)['lease_expires_at'] + 1
    assert h.dispatcher.tick()['state'] == 'quarantined'
    assert h.store.active() is not None
    h.kube.terminate_pod(h.allocation(job)['pod_name'])
    recovered = h.dispatcher.tick()
    assert recovered['state'] == 'failed' and recovered['outcome']['error_code'] == 'quarantine_stopped'
    assert h.store.active() is None and h.allocation(job)['retired'] is True
    assert h.store.get_artifacts('tenant', 'ppt', job['job_id']) == []
    assert len(h.worker.puts()) == 1


def test_maximum_native_resource_proof_is_preserved_without_overflowing_outer_proof(make_harness):
    h = make_harness()
    job = h.submit()
    running = h.dispatcher.dispatch_once()
    allocation = h.allocation(job)
    h.worker.complete(running['attempt_id'])
    native = h.worker.executions[running['attempt_id']]
    resources = tuple('offline-native-writer-' + str(value) for value in range(64))
    result = native['result']
    proof = sign_receipt(native['envelope'], result, native['key'], resources, h.clock())
    native['result'] = result.model_copy(update={'stop_receipt': proof})
    completed = h.dispatcher.poll(running)
    assert completed['state'] == 'completed'
    persisted = h.allocation(job)
    assert persisted['candidate_result']['stop_receipt']['resource_ids'] == list(resources)
    assert completed['outcome']['stop_receipt']['resource_ids'] == [
        'k8s:' + h.pool.namespace + ':' + allocation['pod_uid']]
    assert len(h.store.get_artifacts('tenant', 'ppt', job['job_id'])) == 1
    assert h.store.active() is None and persisted['retired'] is True


def test_api_capabilities_declares_pool_admission_without_starting_native_worker(make_harness):
    from fastapi.testclient import TestClient
    h = make_harness(cold=True)
    h.worker.ready = False
    token = 'offline-business-authentication-token-000000'
    settings = ApiSettings({token: Principal('tenant', 'ppt', frozenset({h.pool.policy.name}))})
    app = create_app(settings, h.store, ProfileRegistry([h.pool.policy]), {h.pool.policy.engine: h.adapter})
    with TestClient(app) as client:
        response = client.get('/v1/capabilities', headers={'Authorization': 'Bearer ' + token})
    assert response.status_code == 200
    profile = response.json()['profiles'][0]
    assert profile['ready'] is True
    assert profile['provisioning'] == 'kubernetes_per_attempt'
    assert profile['readiness_basis'] == 'pool_admission'
    assert profile['requires_native_readiness_check'] is True
    assert h.store.active() is None
    assert h.kube.objects['pods'] == h.kube.objects['secrets'] == {}
    assert h.worker.calls == []
    assert h.kube.calls and all(method == 'GET' for method, _, _ in h.kube.calls)
