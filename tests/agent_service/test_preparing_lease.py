"""Preparing ownership may span cold ticks; it never proves native execution."""
from uuid import uuid4

import pytest
from sqlalchemy import insert, select, update

from agent_service.contracts import ServiceError
from agent_service.dispatcher import Dispatcher
from agent_service.store import Store, attempts, inputs, worker_allocations


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def cold(tmp_path):
    clock = Clock()
    store = Store('sqlite:///' + str(tmp_path / 'queue.db'), allow_sqlite=True, clock=clock)
    store.initialize()
    request = dict(api_version='1', profile='workspace-task.v1', profile_revision='r1',
        business_ref='cold-once', task={'prompt': 'offline lease fixture'}, inputs=[], timeout_seconds=600)
    pins = dict(profile='workspace-task.v1', profile_revision='r1', engine='pi',
        runtime_revision='fixture-r1', image_digest='sha256:' + 'a' * 64)
    submitted, _ = store.submit('tenant', 'caller', 'once', request, pins)
    yield store, clock, submitted
    store.close()


def claim(cold, *, provisioning='kubernetes_per_attempt'):
    store, _, _ = cold
    return store.claim(lease_seconds=60, provisioning={'pi': provisioning})


def renew(store, job, seconds=60):
    return store.renew_preparing(job['job_id'], job['attempt_id'], job['fence'], lease_seconds=seconds)


def allocation(store, job):
    envelope = Dispatcher(store, {})._envelope(job)
    record = store.reserve_worker(envelope, pool_id='cold-fixture', namespace='agent-workers',
        pod_name='agent-' + envelope.attempt_id, intent_sha256='b' * 64)
    store.claim_worker_create(envelope.attempt_id, envelope.request_sha256)
    store.bind_worker(envelope.attempt_id, envelope.request_sha256, str(uuid4()))
    return envelope, record


def test_repeated_preparing_ticks_keep_same_attempt_and_absolute_deadline(cold):
    store, clock, original = cold
    job = claim(cold)
    envelope, _ = allocation(store, job)
    expected_hash = envelope.request_sha256
    for _ in range(5):
        clock.now += 45
        refreshed = renew(store, job)
        assert refreshed is not None
        assert refreshed['lease_expires_at'] == clock() + 60
        assert refreshed['deadline_at'] == original['deadline_at']
        assert refreshed['attempt_id'] == job['attempt_id'] and refreshed['fence'] == job['fence']
        assert Dispatcher(store, {})._envelope(refreshed).request_sha256 == expected_hash
    events = store.events('tenant', 'caller', job['job_id'])['items']
    assert sum(row['type'] == 'job.starting' for row in events) == 1
    assert not any(row['type'] == 'job.running' for row in events)


def test_preparing_lease_can_cover_input_construction_before_allocation_exists(cold):
    store, clock, _ = cold
    job = claim(cold)
    clock.now += 50
    result = renew(store, job)
    assert result is not None and result['lease_expires_at'] == 1110
    with store._tx() as conn:
        assert conn.execute(select(worker_allocations)).first() is None


@pytest.mark.parametrize('advance', [60, 61, 600])
def test_preparing_never_revives_an_expired_lease(cold, advance):
    store, clock, _ = cold
    job = claim(cold)
    clock.now += advance
    assert renew(store, job) is None
    assert store.get_job('tenant', 'caller', job['job_id'])['lease_expires_at'] == 1060
    store.reconcile_expired()
    assert renew(store, job) is None


def test_cancel_stops_preparing_renewal_without_claiming_native_authority(cold):
    store, clock, _ = cold
    job = claim(cold)
    _, record = allocation(store, job)
    clock.now += 45
    store.cancel('tenant', 'caller', job['job_id'])
    assert renew(store, job) is None
    current = store.get_worker_allocation(record['attempt_id'])
    assert current['worker_start_claimed'] is False


def test_deadline_bounds_preparing_even_if_stop_collection_lease_remains_live(cold):
    store, clock, _ = cold
    job = claim(cold)
    # Reach the deadline through valid short renewals, then hold the extra
    # stop-only lease interval. It cannot authorize more preparation or start.
    for _ in range(13):
        clock.now += 45
        assert renew(store, job) is not None
    assert clock.now == 1585
    clock.now = 1600
    assert renew(store, job) is None
    assert store.get_job('tenant', 'caller', job['job_id'])['deadline_at'] == 1600


@pytest.mark.parametrize('provisioning', ['external_static', 'legacy'])
def test_external_or_historical_intent_cannot_gain_preparing_authority(cold, provisioning):
    store, clock, _ = cold
    job = claim(cold, provisioning='external_static' if provisioning == 'external_static' else 'kubernetes_per_attempt')
    if provisioning == 'legacy':
        with store._tx() as conn:
            conn.execute(update(attempts).where(attempts.c.id == job['attempt_id'])
                .values(dispatch_intent={'job_id': job['job_id']}))
    clock.now += 45
    assert renew(store, job) is None
    assert store.get_job('tenant', 'caller', job['job_id'])['lease_expires_at'] == 1060


@pytest.mark.parametrize('damage', ['missing-identity', 'tenant', 'fence', 'request_hash'])
def test_preparing_requires_complete_matching_frozen_dispatch_intent(cold, damage):
    store, clock, _ = cold
    job = claim(cold)
    with store._tx() as conn:
        intent = dict(conn.execute(select(attempts.c.dispatch_intent).where(attempts.c.id == job['attempt_id'])).scalar_one())
        if damage == 'missing-identity':
            intent = {'provisioning': 'kubernetes_per_attempt'}
        else:
            intent[damage] = {'tenant': 'another-owner', 'fence': job['fence'] + 1, 'request_hash': 'f' * 64}[damage]
        conn.execute(update(attempts).where(attempts.c.id == job['attempt_id']).values(dispatch_intent=intent))
    clock.now += 45
    assert renew(store, job) is None
    assert store.get_job('tenant', 'caller', job['job_id'])['lease_expires_at'] == 1060


def test_wrong_attempt_or_fence_cannot_renew_current_preparation(cold):
    store, clock, _ = cold
    job = claim(cold)
    clock.now += 45
    assert renew(store, {**job, 'attempt_id': str(uuid4())}) is None
    assert renew(store, {**job, 'fence': job['fence'] + 1}) is None
    assert store.get_job('tenant', 'caller', job['job_id'])['lease_expires_at'] == 1060


@pytest.mark.parametrize('field,value', [('retired', True), ('candidate_result', {'state': 'failed'})])
def test_retired_or_result_allocation_cannot_extend_preparation(cold, field, value):
    store, clock, _ = cold
    job = claim(cold)
    _, record = allocation(store, job)
    with store._tx() as conn:
        conn.execute(update(worker_allocations).where(worker_allocations.c.attempt_id == record['attempt_id'])
            .values(**{field: value}))
    clock.now += 45
    assert renew(store, job) is None


def test_native_claim_gets_fresh_bound_lease_and_disables_preparing_heartbeat(cold):
    store, clock, _ = cold
    job = claim(cold)
    envelope, _ = allocation(store, job)
    clock.now += 50
    assert store.claim_worker_start(envelope.attempt_id, envelope.request_sha256, lease_seconds=60) is True
    current = store.get_job('tenant', 'caller', job['job_id'])
    assert current['lease_expires_at'] == 1110
    assert Dispatcher(store, {})._envelope(current).request_sha256 == envelope.request_sha256
    clock.now += 10
    assert renew(store, job) is None
    assert store.claim_worker_start(envelope.attempt_id, envelope.request_sha256, lease_seconds=60) is False
    assert store.get_job('tenant', 'caller', job['job_id'])['lease_expires_at'] == 1110


def test_expired_native_claim_never_starts_or_refreshes_lease(cold):
    store, clock, _ = cold
    job = claim(cold)
    envelope, _ = allocation(store, job)
    clock.now = 1060
    with pytest.raises(ServiceError):
        store.claim_worker_start(envelope.attempt_id, envelope.request_sha256, lease_seconds=60)
    assert store.get_worker_allocation(envelope.attempt_id)['worker_start_claimed'] is False
    assert store.get_job('tenant', 'caller', job['job_id'])['lease_expires_at'] == 1060


def test_complete_historical_intent_without_preparation_marker_keeps_old_lease(cold):
    store, clock, _ = cold
    job = claim(cold)
    envelope, _ = allocation(store, job)
    with store._tx() as conn:
        intent = dict(conn.execute(select(attempts.c.dispatch_intent).where(
            attempts.c.id == job['attempt_id'])).scalar_one())
        intent.pop('preparation_lease_seconds')
        conn.execute(update(attempts).where(attempts.c.id == job['attempt_id']).values(dispatch_intent=intent))
    clock.now += 45
    assert renew(store, job) is None
    assert store.claim_worker_start(envelope.attempt_id, envelope.request_sha256) is True
    assert store.get_job('tenant', 'caller', job['job_id'])['lease_expires_at'] == 1060


def test_native_fresh_lease_does_not_expand_a_short_configured_dispatcher_interval(cold):
    store, clock, _ = cold
    job = store.claim(lease_seconds=15, provisioning={'pi': 'kubernetes_per_attempt'})
    envelope, _ = allocation(store, job)
    clock.now += 10
    assert renew(store, job, seconds=60) is None
    assert store.claim_worker_start(envelope.attempt_id, envelope.request_sha256) is True
    assert store.get_job('tenant', 'caller', job['job_id'])['lease_expires_at'] == 1025


@pytest.mark.parametrize('legacy', [False, True])
def test_static_or_legacy_native_claim_keeps_its_original_lease(cold, legacy):
    store, clock, _ = cold
    job = claim(cold, provisioning='kubernetes_per_attempt' if legacy else 'external_static')
    envelope, _ = allocation(store, job)
    if legacy:
        with store._tx() as conn:
            conn.execute(update(attempts).where(attempts.c.id == job['attempt_id'])
                .values(dispatch_intent={'provisioning': 'kubernetes_per_attempt'}))
    clock.now += 50
    assert store.claim_worker_start(envelope.attempt_id, envelope.request_sha256, lease_seconds=60) is True
    assert store.get_job('tenant', 'caller', job['job_id'])['lease_expires_at'] == 1060


def test_input_batch_crosses_query_boundary_and_preserves_request_order_and_bytes(cold):
    store, _, _ = cold
    originals = []
    for index in range(501):
        content = ('original frozen record ' + str(index)).encode()
        saved = store.put_input('tenant', 'caller', f'original-{index}.json', content)
        originals.append((saved, content))
    references = [dict(input_id=saved['input_id'], sha256=saved['sha256'], path=f'records/{index:06d}.json')
        for index, (saved, _) in enumerate(reversed(originals))]
    with store._tx() as conn:
        files = store._input_files(conn, 'tenant', 'caller', references)
    assert len(files) == 501
    assert [file.name for file in files] == [row['path'] for row in references]
    assert [file.content() for file in files] == [content for _, content in reversed(originals)]


@pytest.mark.parametrize('damage', ['tenant', 'caller', 'hash', 'bytes', 'deleted', 'duplicate-id', 'duplicate-path'])
def test_batch_input_reader_still_rejects_forged_or_unavailable_originals(cold, damage):
    store, clock, _ = cold
    left = store.put_input('tenant', 'caller', 'left.json', b'left immutable bytes')
    right = store.put_input('tenant', 'caller', 'right.json', b'right immutable bytes')
    references = [dict(input_id=row['input_id'], sha256=row['sha256'], path=row['name']) for row in (left, right)]
    with store._tx() as conn:
        if damage in {'tenant', 'caller'}:
            conn.execute(update(inputs).where(inputs.c.id == left['input_id']).values(**{damage: 'another-owner'}))
        elif damage == 'hash':
            references[0]['sha256'] = 'f' * 64
        elif damage == 'bytes':
            conn.execute(update(inputs).where(inputs.c.id == left['input_id']).values(content=b'mutated bytes'))
        elif damage == 'deleted':
            conn.execute(insert(store.file_tables['tombstones']).values(kind='input', id=left['input_id'], deleted_at=clock()))
        elif damage == 'duplicate-id':
            references[1]['input_id'], references[1]['sha256'] = left['input_id'], left['sha256']
        elif damage == 'duplicate-path':
            references[1]['path'] = references[0]['path']
    with store._tx() as conn:
        with pytest.raises(ServiceError):
            store._input_files(conn, 'tenant', 'caller', references)
