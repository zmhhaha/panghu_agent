"""Durable developer opinions over real HTTP; no Agent or rendering calls."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import http.client
import json
import sqlite3
from threading import Barrier, Thread
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_service.contracts import ServiceError, canonical_bytes, digest
from video_workbench.animation_creation_http import CreationHTTPServer
from video_workbench.animation_lab import LabHTTPServer
from video_workbench.animation_method_http import MethodLabClient
from video_workbench.animation_method_service import AnimationMethodService


OWNER = 'casdoor:feedback-developer'
SECOND = 'casdoor:other-developer'
TOKEN = 'method-feedback-test-' + 't' * 40
HOST = 'video-service.panghuer.top'
BASE = '/dev/animations'
PREFIX = '/api/method-lab'


class ForbiddenWork:
    def __getattr__(self, name):
        pytest.fail('Opinions must not call Agent/renderer: ' + name)


def reopen(root, clock):
    return AnimationMethodService(root, author_gateway=ForbiddenWork(),
        review_gateway=ForbiddenWork(), renderer=ForbiddenWork(), clock=clock)


def seed(service, owner=OWNER):
    """Trusted inert ledger fixture, not rendering or model evidence."""
    run = service.commissions.create(owner, dict(goal='Review two complete passages',
        duration_seconds=18, budget=dict(creator=4, review=4, render=4, seconds=300)), str(uuid4()))
    candidates = []
    for index, duration in enumerate((13.5, 11)):
        media = dict(sha256=str(index + 1) * 64, duration_seconds=duration)
        op = service.commissions.reserve(owner, run['id'], 'render', {'inert_fixture': index}, str(uuid4()))
        assert service.commissions.claim(owner, op['id'])['submission_claimed']
        service.commissions.complete(owner, op['id'], {'media': media})
        candidates.append(service.commissions.add_candidate(owner, run['id'], dict(
            operation_id=op['id'], media=media, strategy='INERT UNIT TEST',
            source={'engine': 'html-svg', 'files': {'scene.html': '<div>fixture</div>'}}), str(uuid4())))
    return run['id'], candidates


@pytest.fixture
def work(tmp_path):
    now = [1000.0]
    service = reopen(tmp_path / 'lab', lambda: now[0])
    commission_id, candidates = seed(service)
    return service, commission_id, candidates, now


def comment(candidate, **changes):
    return dict(request_id=str(uuid4()), candidate_id=candidate['id'], comment='The relation is unclear.', **changes)


def preference(candidates, **changes):
    body = dict(request_id=str(uuid4()), kind='preference', left_candidate_id=candidates[0]['id'],
                right_candidate_id=candidates[1]['id'], preference='right')
    body.update(changes)
    return body


def count(service, table):
    assert table in {'feedback', 'feedback_withdrawals', 'feedback_withdrawal_requests'}
    with sqlite3.connect(service.metadata_path) as db:
        return db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]


def test_legacy_and_located_comments_use_actual_media_and_survive_reopen(work):
    service, identity, candidates, now = work
    body = comment(candidates[0], start_seconds=0, end_seconds=13.5,
                   region=dict(x=.25, y=.5, width=.75, height=.5))
    row = service.feedback(OWNER, identity, body)
    assert row['kind'] == 'comment' and row['withdrawn'] is False and row['withdrawal'] is None
    assert row['source'] == 'explicit_developer_feedback' and row['created_at'] == 1000
    assert all(row[key] == value for key, value in body.items())
    now[0] += 10
    restored = reopen(service.root, lambda: now[0])
    assert restored.feedback(OWNER, identity, body) == row
    assert restored.get(OWNER, identity)['feedback'] == [row]
    explicit = restored.feedback(OWNER, identity, comment(candidates[1], kind='comment'))
    assert explicit['kind'] == 'comment' and count(restored, 'feedback') == 2


@pytest.mark.parametrize('bounds', [
    {'start_seconds': 0}, {'end_seconds': 1},
    {'start_seconds': -1, 'end_seconds': 1}, {'start_seconds': 2, 'end_seconds': 1},
    {'start_seconds': 0, 'end_seconds': 13.51},
    {'start_seconds': False, 'end_seconds': 1}, {'start_seconds': 0, 'end_seconds': True},
    {'start_seconds': '0', 'end_seconds': 1}, {'start_seconds': None, 'end_seconds': 1},
    {'start_seconds': float('nan'), 'end_seconds': 1},
    {'start_seconds': 0, 'end_seconds': float('inf')},
    {'start_seconds': -float('inf'), 'end_seconds': 1},
    {'start_seconds': 0, 'end_seconds': 10 ** 500},
])
def test_time_annotations_reject_partial_nonfinite_and_out_of_actual_media_bounds(work, bounds):
    service, identity, candidates, _ = work
    with pytest.raises(ServiceError, match='animation_method_feedback_range_invalid'):
        service.feedback(OWNER, identity, comment(candidates[0], **bounds))
    assert count(service, 'feedback') == 0


@pytest.mark.parametrize('region', [
    None, [], {}, dict(x=0, y=0, width=1), dict(x=0, y=0, width=1, height=1, unit='px'),
    dict(x=-.1, y=0, width=1, height=1), dict(x=0, y=0, width=1.1, height=1),
    dict(x=.8, y=0, width=.3, height=1), dict(x=0, y=.8, width=1, height=.3),
    dict(x=False, y=0, width=1, height=1), dict(x=0, y=0, width=float('nan'), height=1),
    dict(x=0, y=0, width=1, height=float('inf')), dict(x=10 ** 500, y=0, width=0, height=0),
])
def test_region_is_exact_complete_finite_normalized_rectangle(work, region):
    service, identity, candidates, _ = work
    with pytest.raises(ServiceError, match='animation_method_feedback_region_invalid'):
        service.feedback(OWNER, identity, comment(candidates[0], region=region))
    assert count(service, 'feedback') == 0


@pytest.mark.parametrize('choice', ['left', 'right', 'equal', 'neither'])
def test_comparisons_save_explicit_pair_without_selecting_accepting_or_publishing(work, choice):
    service, identity, candidates, _ = work
    before = service.commissions.get(OWNER, identity)
    events = service.commissions.events(OWNER, identity)
    body = preference(candidates, preference=choice)
    row = service.feedback(OWNER, identity, body)
    assert all(row[key] == value for key, value in body.items())
    assert service.feedback(OWNER, identity, body) == row
    assert service.commissions.get(OWNER, identity) == before
    assert service.commissions.events(OWNER, identity) == events
    assert service.methods.list_methods(OWNER) == []
    assert service.get(OWNER, identity)['best_candidate_id'] == candidates[0]['id']


@pytest.mark.parametrize('changes', [
    {'preference': 'tie'}, {'preference': []}, {'comment': None},
    {'candidate_id': 'ambiguous'}, {'start_seconds': 0, 'end_seconds': 1},
    {'region': dict(x=0, y=0, width=1, height=1)}, {'accepted': True},
])
def test_comparison_cannot_smuggle_annotations_or_approval(work, changes):
    service, identity, candidates, _ = work
    with pytest.raises(ServiceError) as error:
        service.feedback(OWNER, identity, preference(candidates, **changes))
    assert error.value.status == 422 and count(service, 'feedback') == 0


def test_comparison_candidates_must_be_distinct_and_in_this_owned_commission(work):
    service, identity, candidates, _ = work
    other_id, same_owner = seed(service)
    _, other_owner = seed(service, SECOND)
    for candidate, status in ((candidates[0], 422), (same_owner[0], 404), (other_owner[0], 404)):
        with pytest.raises(ServiceError) as error:
            service.feedback(OWNER, identity, preference(candidates, right_candidate_id=candidate['id']))
        assert error.value.status == status
    with pytest.raises(ServiceError) as error:
        service.feedback(SECOND, identity, preference(candidates))
    assert error.value.status == 404
    assert service.get(OWNER, other_id)['feedback'] == [] and count(service, 'feedback') == 0


def test_withdrawal_preserves_original_and_first_metadata_with_replay_and_restart(work):
    service, identity, candidates, now = work
    body = preference(candidates, comment='Right is clearer.')
    original = service.feedback(OWNER, identity, body)
    with sqlite3.connect(service.metadata_path) as db:
        saved = db.execute('SELECT document FROM feedback').fetchone()[0]
    now[0] = 1200
    request = dict(request_id='withdraw-one', feedback_id=original['id'])
    withdrawn = service.withdraw_feedback(OWNER, identity, request)
    metadata = withdrawn['withdrawal']
    assert withdrawn['withdrawn'] and metadata['created_at'] == 1200
    assert metadata['source'] == 'explicit_developer_feedback_withdrawal'
    assert metadata['request_id'] == request['request_id'] and metadata['feedback_id'] == original['id']
    assert metadata['commission_id'] == identity
    assert all(withdrawn[key] == value for key, value in original.items() if key not in {'withdrawn', 'withdrawal'})
    now[0] = 1500
    restored = reopen(service.root, lambda: now[0])
    assert restored.withdraw_feedback(OWNER, identity, request) == withdrawn
    assert restored.feedback(OWNER, identity, body) == withdrawn
    assert restored.withdraw_feedback(OWNER, identity, dict(request, request_id='withdraw-repeat')) == withdrawn
    assert restored.get(OWNER, identity)['feedback'] == [withdrawn]
    with sqlite3.connect(service.metadata_path) as db:
        assert db.execute('SELECT document FROM feedback').fetchone()[0] == saved
    assert count(restored, 'feedback') == count(restored, 'feedback_withdrawals') == 1
    assert count(restored, 'feedback_withdrawal_requests') == 2
    another = restored.feedback(OWNER, identity, comment(candidates[1]))
    for request_id in ('withdraw-one', 'withdraw-repeat'):
        with pytest.raises(ServiceError) as error:
            restored.withdraw_feedback(OWNER, identity, dict(request_id=request_id, feedback_id=another['id']))
        assert error.value.status == 409


def test_feedback_and_withdrawal_are_owner_and_commission_scoped(work):
    service, identity, candidates, _ = work
    body = comment(candidates[0])
    row = service.feedback(OWNER, identity, body)
    second_id, _ = seed(service)
    foreign_id, _ = seed(service, SECOND)
    for owner, commission_id in ((SECOND, identity), (SECOND, foreign_id), (OWNER, second_id)):
        with pytest.raises(ServiceError) as error:
            service.withdraw_feedback(owner, commission_id, dict(request_id='withdraw', feedback_id=row['id']))
        assert error.value.status == 404
    with pytest.raises(ServiceError) as error:
        service.feedback(OWNER, second_id, dict(body, candidate_id=service.get(OWNER, second_id)['candidates'][0]['id']))
    assert error.value.status == 409
    assert count(service, 'feedback_withdrawals') == count(service, 'feedback_withdrawal_requests') == 0
    assert service.get(SECOND, foreign_id)['feedback'] == []


def test_concurrent_save_and_withdraw_are_atomic_single_records(work):
    service, identity, candidates, _ = work
    body = comment(candidates[0])
    gate = Barrier(4)
    def save(_):
        gate.wait()
        return service.feedback(OWNER, identity, body)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(save, range(4)))
    assert rows == [rows[0]] * 4 and count(service, 'feedback') == 1
    gate = Barrier(4)
    def withdraw(_):
        gate.wait()
        return service.withdraw_feedback(OWNER, identity, dict(request_id='one-withdraw', feedback_id=rows[0]['id']))
    with ThreadPoolExecutor(max_workers=4) as pool:
        withdrawn = list(pool.map(withdraw, range(4)))
    assert withdrawn == [withdrawn[0]] * 4
    assert count(service, 'feedback_withdrawals') == count(service, 'feedback_withdrawal_requests') == 1


def test_existing_raw_feedback_documents_are_projected_without_rewriting(work):
    service, identity, candidates, _ = work
    body = comment(candidates[0])
    raw = dict(body, id=str(uuid4()), commission_id=identity, source='explicit_developer_feedback', created_at=900)
    document = canonical_bytes(raw).decode()
    with sqlite3.connect(service.metadata_path) as db:
        db.execute('INSERT INTO feedback VALUES (?,?,?,?,?)',
            (OWNER, body['request_id'], identity, digest(dict(commission_id=identity, body=body)), document))
    projected = service.feedback(OWNER, identity, body)
    assert projected == dict(raw, kind='comment', withdrawn=False, withdrawal=None)
    service.withdraw_feedback(OWNER, identity, dict(request_id='withdraw-legacy', feedback_id=raw['id']))
    with sqlite3.connect(service.metadata_path) as db:
        assert db.execute('SELECT document FROM feedback').fetchone()[0] == document


@contextmanager
def serving(server):
    thread = Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


class PumpSpy:
    def __init__(self):
        self.notifications = 0
    def notify(self):
        self.notifications += 1
    def close(self):
        pass


def public(server, method, suffix, body=None, **headers):
    supplied = {'Host': HOST, 'X-Forwarded-User': OWNER}
    if method == 'POST':
        supplied.update({'Origin': 'https://' + HOST, 'X-Animation-Lab-Token': 'feedback-csrf',
                         'Content-Type': 'application/json'})
    supplied.update(headers)
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
    try:
        connection.request(method, BASE + PREFIX + suffix, body=None if body is None else json.dumps(body),
                           headers=supplied)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_real_two_hop_feedback_and_withdrawal_never_wake_pump_or_submit_work(work):
    service, identity, candidates, _ = work
    before = service.commissions.get(OWNER, identity)
    events = service.commissions.events(OWNER, identity)
    with serving(CreationHTTPServer(SimpleNamespace(), TOKEN, pump=False, method_service=service)) as backend:
        spy = backend.method_pump = PumpSpy()
        client = MethodLabClient('http://127.0.0.1:' + str(backend.server_port), TOKEN)
        lab = SimpleNamespace(iteration_store=SimpleNamespace(), csrf_token='feedback-csrf',
                              method_client=client, methods_only=True)
        with serving(LabHTTPServer(lab, 0, proxy_mode=True, public_origin='https://' + HOST,
                     base_path=BASE, developer_users=[OWNER, SECOND])) as frontend:
            connection = http.client.HTTPConnection('127.0.0.1', frontend.server_port, timeout=5)
            try:
                connection.request('GET', BASE + '/method-comparison.mjs',
                                   headers={'Host': HOST, 'X-Forwarded-User': OWNER})
                response = connection.getresponse()
                assert response.status == 200
                assert response.getheader('Content-Type').startswith('text/javascript')
                assert response.read()
            finally:
                connection.close()
            suffix = '/commissions/' + identity
            status, saved = public(frontend, 'POST', suffix + '/feedback', comment(candidates[0],
                start_seconds=1, end_seconds=3, region=dict(x=0, y=0, width=1, height=1)))
            assert status == 200 and saved['kind'] == 'comment'
            status, compared = public(frontend, 'POST', suffix + '/feedback', preference(candidates))
            assert status == 200 and compared['preference'] == 'right'
            request = dict(request_id='http-withdraw', feedback_id=saved['id'])
            status, withdrawn = public(frontend, 'POST', suffix + '/withdraw_feedback', request)
            assert status == 200 and withdrawn['withdrawn']
            assert public(frontend, 'POST', suffix + '/withdraw_feedback', request) == (200, withdrawn)
            status, detail = public(frontend, 'GET', suffix)
            assert status == 200 and detail['feedback'] == [withdrawn, compared]
            assert detail['best_candidate_id'] == candidates[0]['id']
            assert public(frontend, 'POST', suffix + '/feedback', comment(candidates[0], end_seconds=1))[0] == 422
            assert public(frontend, 'POST', suffix + '/withdraw_feedback?retry=1', request)[0] == 422
            assert public(frontend, 'POST', suffix + '/withdraw_feedback', request,
                          **{'X-Forwarded-User': SECOND})[0] == 404
            assert public(frontend, 'POST', suffix + '/withdraw_feedback', request,
                          **{'X-Animation-Lab-Token': 'wrong'})[0] == 403
            assert public(frontend, 'POST', suffix + '/approve', {})[0] == 404
            for _ in range(3):
                assert public(frontend, 'GET', suffix)[0] == 200
            assert spy.notifications == 0
    assert service.commissions.get(OWNER, identity) == before
    assert service.commissions.events(OWNER, identity) == events
    assert service.methods.list_methods(OWNER) == []
