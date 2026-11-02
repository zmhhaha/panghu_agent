"""Shared slot control concurrency, using a real queue and synthetic Workers."""
from concurrent.futures import ThreadPoolExecutor
import threading
from types import SimpleNamespace

import pytest

from agent_service import __main__ as cli
from agent_service.dispatcher import Dispatcher
from agent_service.store import Store


class SyntheticPerAttemptAdapter:
    provisioning = 'kubernetes_per_attempt'

    def __init__(self, *, coordinate_starts=True):
        self.lock = threading.Lock()
        self.started = []
        self.inspected = []
        self.cancelled = []
        self.renewed = []
        self.coordinate_starts = coordinate_starts
        self.all_started = threading.Event()
        self.first_start_saw_other_slots = False
        self.blocked_attempt_id = None
        self.inspect_entered = threading.Event()
        self.release_inspect = threading.Event()

    def start(self, envelope):
        with self.lock:
            self.started.append(envelope.attempt_id)
            if len(self.started) == 5:
                self.all_started.set()
        if self.coordinate_starts and envelope.task['prompt'] == 'job-0':
            # A serial dispatcher cannot start the remaining four Workers while
            # this call is pending. Record the outcome because start failures
            # are intentionally reconciled rather than propagated by Dispatcher.
            self.first_start_saw_other_slots = self.all_started.wait(10)
        return self.observed(envelope)

    @staticmethod
    def observed(envelope):
        return {'state': 'running', 'attempt_id': envelope.attempt_id,
                'request_sha256': envelope.request_sha256}

    def inspect(self, envelope):
        with self.lock:
            self.inspected.append(envelope.attempt_id)
        if envelope.attempt_id == self.blocked_attempt_id:
            self.inspect_entered.set()
            assert self.release_inspect.wait(10), 'Test did not release the blocked Worker inspection'
        return self.observed(envelope)

    def cancel(self, envelope):
        with self.lock:
            self.cancelled.append(envelope.attempt_id)

    def renew(self, envelope, lease_expires_at):
        with self.lock:
            self.renewed.append((envelope.attempt_id, lease_expires_at))


@pytest.fixture
def shared_store(tmp_path):
    store = Store('sqlite:///' + str(tmp_path / 'queue.sqlite'), allow_sqlite=True,
                  execution_slots=5)
    store.initialize()
    yield store
    store.close()


def submit_five(store):
    owners = ('ppt-service', 'scientific-service', 'video-workbench')
    submitted = []
    for index in range(5):
        submitted.append(store.submit('internal', owners[index % len(owners)], 'job-' + str(index), {
            'api_version': '1', 'profile': 'workspace-task.v1', 'profile_revision': 'r1',
            'business_ref': 'business-' + str(index), 'task': {'prompt': 'job-' + str(index)},
            'inputs': [], 'timeout_seconds': 600}, {
            'profile': 'workspace-task.v1', 'profile_revision': 'r1', 'engine': 'pi',
            'runtime_revision': 'reviewed-r1', 'image_digest': 'sha256:' + 'a' * 64})[0])
    return submitted


def dispatch_once(store, adapter):
    cli._dispatch(SimpleNamespace(execution_slots=5, lease_seconds=60, poll_seconds=0.01),
                  store, {'pi': adapter}, once=True)


def test_five_shared_slots_launch_while_first_worker_start_is_blocked(shared_store, capsys):
    submitted = submit_five(shared_store)
    adapter = SyntheticPerAttemptAdapter()
    dispatch_once(shared_store, adapter)
    assert adapter.first_start_saw_other_slots is True
    active = shared_store.active_jobs()
    assert {job['id'] for job in active} == {job['id'] for job in submitted}
    assert all(job['state'] == 'running' for job in active)
    assert len({job['fence'] for job in active}) == 5
    assert len(set(adapter.started)) == 5
    assert 'dispatch_tick_complete' in capsys.readouterr().out


def test_control_restart_reconciles_every_occupied_slot_without_starting_again(shared_store):
    submit_five(shared_store)
    adapter = SyntheticPerAttemptAdapter()
    dispatch_once(shared_store, adapter)
    before = shared_store.active_jobs()
    reopened = Store(str(shared_store.engine.url), allow_sqlite=True, execution_slots=5)
    try:
        reopened.initialize()
        recovered = [Dispatcher(reopened, {'pi': adapter}, slot_id=slot_id).reconcile()
                     for slot_id in range(1, 6)]
        assert {job['attempt_id'] for job in recovered} == {job['attempt_id'] for job in before}
        assert all(job['state'] == 'running' for job in recovered)
        assert len(adapter.started) == 5
        assert set(adapter.inspected) == {job['attempt_id'] for job in before}
        assert {attempt for attempt, _ in adapter.renewed} == {job['attempt_id'] for job in before}
    finally:
        reopened.close()


def test_blocked_inspection_does_not_delay_another_slots_cancel_or_renew(shared_store):
    submit_five(shared_store)
    adapter = SyntheticPerAttemptAdapter()
    dispatch_once(shared_store, adapter)
    blocked = shared_store.active(slot_id=1)
    cancelling = shared_store.active(slot_id=2)
    renewing = shared_store.active(slot_id=3)
    adapter.blocked_attempt_id = blocked['attempt_id']
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(Dispatcher(shared_store, {'pi': adapter}, slot_id=1).reconcile)
        try:
            assert adapter.inspect_entered.wait(5)
            shared_store.cancel(cancelling['tenant'], cancelling['caller'], cancelling['id'])
            cancelled = Dispatcher(shared_store, {'pi': adapter}, slot_id=2).reconcile()
            renewed = Dispatcher(shared_store, {'pi': adapter}, slot_id=3).reconcile()
            assert cancelled['state'] == 'cancelling'
            assert cancelling['attempt_id'] in adapter.cancelled
            assert renewed['lease_expires_at'] > renewing['lease_expires_at']
            assert renewing['attempt_id'] in {attempt for attempt, _ in adapter.renewed}
            assert not future.done()
        finally:
            adapter.release_inspect.set()
        assert future.result(timeout=5)['state'] == 'running'


def test_multislot_dispatcher_rejects_shared_static_capsule(shared_store):
    with pytest.raises(ValueError, match='per-attempt Workers'):
        Dispatcher(shared_store, {'pi': SimpleNamespace(provisioning='external_static')})
