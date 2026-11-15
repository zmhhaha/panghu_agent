"""Actual authenticated HTTP hops for recycle-bin metadata, without paid work.

Fixtures use ordinary store creation/stopping in isolation. They have no Agent
operations, samples or media, and make no animation-quality claims.
"""
from contextlib import contextmanager
from copy import deepcopy
import http.client
import json
from threading import Thread
from types import SimpleNamespace
from uuid import uuid4

import pytest

from video_workbench.animation_creation_http import CreationHTTPServer, CreationTransportError
from video_workbench.animation_lab import LabHTTPServer
from video_workbench.animation_method_http import MethodLabClient
from video_workbench.animation_method_service import AnimationMethodService


HOST = 'video-service.panghuer.top'
BASE = '/dev/animations'
API = '/api/method-lab'
OWNER = 'casdoor:trash-http-owner'
OTHER = 'casdoor:trash-http-other'
TOKEN = 'trash-http-private-token-' + 't' * 32
CSRF = 'trash-http-test-csrf'


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


def request(server, method, path, *, body=None, headers=()):
    conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
    raw = None if body is None else json.dumps(body).encode()
    try:
        conn.putrequest(method, path, skip_host=True)
        for key, value in headers:
            conn.putheader(key, value)
        if raw is not None:
            conn.putheader('Content-Length', str(len(raw)))
        conn.endheaders(raw)
        response = conn.getresponse()
        value, metadata = response.read(), dict(response.getheaders())
        return response.status, json.loads(value), metadata
    finally:
        conn.close()


def public(peers, method, path, body=None, *, change=None):
    headers = {'Host': HOST, 'X-Forwarded-User': OWNER}
    if method == 'POST':
        headers.update({'Origin': 'https://' + HOST, 'X-Animation-Lab-Token': CSRF,
                        'Content-Type': 'application/json'})
    for key, value in (change or {}).items():
        if value is None:
            headers.pop(key, None)
        else:
            headers[key] = value
    return request(peers.frontend, method, BASE + API + path, body=body, headers=headers.items())


class NoWorkGateway:
    capabilities = None

    def __getattr__(self, name):
        pytest.fail('Recycle-bin HTTP must not call an execution stage: ' + name)


class PumpSpy:
    def __init__(self):
        self.notifications = 0

    def notify(self):
        self.notifications += 1

    def close(self):
        pass


@pytest.fixture
def peers(tmp_path):
    no_work = NoWorkGateway()
    service = AnimationMethodService(tmp_path / 'lab', author_gateway=no_work,
                                    review_gateway=no_work, renderer=no_work)
    body = dict(title='HTTP terminal metadata fixture', goal='Verify recoverable visibility only',
                duration_seconds=3, budget=dict(creator=1, review=0, render=0, seconds=1))
    original = service.commissions.create(OWNER, body, str(uuid4()))
    service.commissions.stop(OWNER, original['id'], 'failed', {'reason': 'fixture_not_executed'})
    related = service.commissions.create(OWNER, dict(body, retry_of_commission_id=original['id']), str(uuid4()))
    service.commissions.stop(OWNER, related['id'], 'failed', {'reason': 'fixture_not_executed'})
    frozen = {row['id']: deepcopy(service.commissions.get(OWNER, row['id'])) for row in (original, related)}
    events = {identity: service.commissions.events(OWNER, identity) for identity in frozen}
    assert service.pending() == []
    pump = PumpSpy()
    backend = CreationHTTPServer(SimpleNamespace(), TOKEN, pump=False, method_service=service)
    backend.method_pump = pump
    with serving(backend):
        client = MethodLabClient('http://127.0.0.1:' + str(backend.server_port), TOKEN)
        lab = SimpleNamespace(methods_only=True, csrf_token=CSRF, method_client=client)
        with serving(LabHTTPServer(lab, 0, proxy_mode=True, public_origin='https://' + HOST,
                     base_path=BASE, developer_users=[OWNER, OTHER])) as frontend:
            yield SimpleNamespace(service=service, client=client, backend=backend, frontend=frontend,
                                  original=original['id'], related=related['id'], pump=pump)
    assert pump.notifications == 0
    assert service.pending() == []
    assert {identity: service.commissions.get(OWNER, identity) for identity in frozen} == frozen
    assert {identity: service.commissions.events(OWNER, identity) for identity in frozen} == events


def delete_body(snapshot):
    return dict(request_id=str(uuid4()), expected_snapshot_sha256=snapshot)


def test_real_two_hop_single_and_effect_deletion_restore_keep_siblings_and_owner(peers):
    status, empty, headers = public(peers, 'GET', '/trash')
    assert status == 200 and empty['items'] == [] and empty['csrf_token'] == CSRF
    assert headers['Cache-Control'] == 'no-store'
    detail = peers.client.get(OWNER, peers.original)
    body = delete_body(detail['deletion']['snapshot_sha256'])
    status, deleted, _ = public(peers, 'POST', '/commissions/' + peers.original + '/trash', body)
    assert status == 200 and deleted['action'] == 'trash'
    assert deleted['entry']['scope'] == 'commission' and deleted['entry']['member_ids'] == [peers.original]
    assert public(peers, 'POST', '/commissions/' + peers.original + '/trash', body)[1] == deleted
    assert public(peers, 'GET', '/commissions/' + peers.original)[0] == 404
    with pytest.raises(CreationTransportError) as missing:
        peers.client.get(OWNER, peers.original)
    assert missing.value.status == 404
    catalogue = public(peers, 'GET', '/catalog')[1]
    assert len(catalogue['effects']) == 1 and catalogue['effects'][0]['id'] == peers.original
    assert catalogue['effects'][0]['open_commission_id'] == peers.related
    assert [row['id'] for row in catalogue['commissions']] == [peers.related]
    assert public(peers, 'GET', '/trash', change={'X-Forwarded-User': OTHER})[1]['items'] == []
    denied = public(peers, 'POST', '/trash/' + deleted['entry']['id'] + '/restore',
                    {'request_id': str(uuid4())}, change={'X-Forwarded-User': OTHER})
    assert denied[0] == 404

    effect = catalogue['effects'][0]
    status, whole, _ = public(peers, 'POST', '/effects/' + effect['id'] + '/trash',
                             delete_body(effect['deletion']['snapshot_sha256']))
    assert status == 200 and whole['entry']['scope'] == 'effect'
    assert public(peers, 'GET', '/catalog')[1]['effects'] == []
    restored = public(peers, 'POST', '/trash/' + whole['entry']['id'] + '/restore', {'request_id': str(uuid4())})
    assert restored[0] == 200 and restored[1]['action'] == 'restore'
    assert peers.client.get(OWNER, peers.related)['id'] == peers.related
    assert public(peers, 'GET', '/commissions/' + peers.original)[0] == 404
    assert public(peers, 'GET', '/trash')[1]['items'] == [deleted['entry']]
    restore_body = {'request_id': str(uuid4())}
    entry_id = deleted['entry']['id']
    restored = peers.client.restore_trash(OWNER, entry_id, restore_body)
    assert restored['entry']['status'] == 'restored'
    assert peers.client.restore_trash(OWNER, entry_id, restore_body) == restored
    assert peers.client.get(OWNER, peers.original)['id'] == peers.original
    assert public(peers, 'GET', '/trash')[1]['items'] == []


@pytest.mark.parametrize('scope,path', [
    ('commission', '/commissions/{id}/trash'), ('effect', '/effects/{id}/trash'),
])
def test_server_conflict_code_and_stale_snapshot_survive_both_http_hops(peers, scope, path):
    target = path.format(id=peers.original)
    status, error, _ = public(peers, 'POST', target, delete_body('0' * 64))
    assert status == 409 and error == {'error': 'animation_method_trash_snapshot_changed'}
    call = peers.client.trash_commission if scope == 'commission' else peers.client.trash_effect
    with pytest.raises(CreationTransportError) as rejected:
        call(OWNER, peers.original, delete_body('0' * 64))
    assert rejected.value.status == 409 and rejected.value.code == error['error']
    assert public(peers, 'GET', '/trash')[1]['items'] == []
    assert len(peers.client.catalogue(OWNER)['commissions']) == 2


@pytest.mark.parametrize('path', ['/trash', '/commissions/{id}/trash', '/effects/{id}/trash', '/trash/entry-id/restore'])
def test_private_recycle_routes_require_exact_token_and_owner_before_read_or_write(peers, path):
    path = API + path.format(id=peers.original)
    method = 'GET' if path == API + '/trash' else 'POST'
    for headers in (
        [('Host', '127.0.0.1')],
        [('Authorization', 'Bearer wrong-' + 'w' * 32), ('X-Animation-Creation-Owner', OWNER)],
        [('Authorization', 'Bearer ' + TOKEN)],
        [('Authorization', 'Bearer ' + TOKEN), ('X-Animation-Creation-Owner', OWNER),
         ('X-Animation-Creation-Owner', OTHER)],
    ):
        assert request(peers.backend, method, path, body={} if method == 'POST' else None,
                       headers=headers)[0] == 403
    assert peers.client.trash_catalogue(OWNER)['items'] == []


@pytest.mark.parametrize('path', ['/commissions/{id}/trash', '/effects/{id}/trash', '/trash/entry-id/restore'])
@pytest.mark.parametrize('change', [
    {'X-Forwarded-User': None}, {'X-Forwarded-User': 'not-a-developer'}, {'Host': 'evil.invalid'},
    {'Origin': None}, {'Origin': 'https://evil.invalid'}, {'X-Animation-Lab-Token': None},
    {'X-Animation-Lab-Token': 'wrong'}, {'Content-Type': 'text/plain'},
])
def test_each_public_recycle_write_checks_auth_origin_and_csrf_before_private_call(peers, path, change):
    original_call = peers.client._request
    calls = []
    peers.client._request = lambda *args, **kwargs: calls.append((args, kwargs))
    try:
        status, _, _ = public(peers, 'POST', path.format(id=peers.original), {'request_id': str(uuid4())}, change=change)
    finally:
        peers.client._request = original_call
    assert status in {403, 415} and calls == []


@pytest.mark.parametrize('change', [{'X-Forwarded-User': None}, {'X-Forwarded-User': 'not-a-developer'}, {'Host': 'evil.invalid'}])
def test_public_recycle_read_is_authenticated_even_without_csrf(peers, change):
    assert public(peers, 'GET', '/trash', change=change)[0] == 403


def test_recycle_routes_reject_queries_and_owner_body_forgery_without_creating_work(peers):
    detail = peers.client.get(OWNER, peers.original)
    body = delete_body(detail['deletion']['snapshot_sha256'])
    assert public(peers, 'GET', '/trash?refresh=1')[0] == 422
    assert public(peers, 'POST', '/commissions/' + peers.original + '/trash?force=1', body)[0] == 422
    assert public(peers, 'POST', '/commissions/' + peers.original + '/trash', dict(body, owner=OTHER))[0] == 422
    assert public(peers, 'GET', '/trash')[1]['items'] == []
    assert peers.client.trash_catalogue(OTHER)['items'] == []
