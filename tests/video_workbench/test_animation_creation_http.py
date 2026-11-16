"""Two real HTTP hops, developer authentication, read-only GET and media seeking."""
from contextlib import contextmanager
import http.client
import json
from types import SimpleNamespace
import threading
from uuid import uuid4

import pytest

from video_workbench.animation_creation_http import (
    CreationAPIClient, CreationHTTPServer, CreationTransportError, LIMITS)
from video_workbench.animation_lab import LabHTTPServer


TOKEN = 'private-creation-test-token-' + 'a' * 32
HOST = 'video-service.panghuer.top'
USER = 'casdoor:creation-developer'
BASE = '/dev/animations'


class TransportService:
    """Only a transport fixture; no model, render or production-readiness claim."""
    def __init__(self, root):
        self.root, self.rows, self.started, self.advanced = root, {}, 0, 0
        (root / 'media').write_bytes(bytes(range(256)) * 4)
        (root / 'source').write_text(json.dumps({'files': {'scene.html': '<script>unsafe()</script>'}}))

    def start(self, owner, body):
        key = (owner, body['request_id'])
        if key not in self.rows:
            self.started += 1
            self.rows[key] = dict(id=str(uuid4()), request_id=body['request_id'], input_role='development',
                state='queued', stage='initial_creation', versions=[dict(id='original',
                    media_url='/api/creation-runs/media-id/versions/original/media',
                    source_url='/api/creation-runs/media-id/versions/original/source')])
        return self.rows[key]

    def list(self, owner, experiment_id=None):
        return [row for (actor, _), row in self.rows.items() if actor == owner]

    def get(self, owner, run_id):
        row = next((r for r in self.list(owner) if r['id'] == run_id), None)
        if row is None:
            raise CreationTransportError('animation_creation_not_found', 404)
        return row

    def artifact(self, owner, run_id, version, kind):
        self.get(owner, run_id)
        return self.root / kind

    def cancel(self, owner, run_id):
        row = self.get(owner, run_id)
        row['state'] = 'cancelled'
        return row

    def advance(self, *args):
        self.advanced += 1
        raise AssertionError('GET and transport-only tests must not advance')


@contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def peers(tmp_path):
    service = TransportService(tmp_path)
    with serving(CreationHTTPServer(service, TOKEN, pump=False)) as backend:
        client = CreationAPIClient('http://127.0.0.1:' + str(backend.server_port), TOKEN)
        lab = SimpleNamespace(iteration_store=SimpleNamespace(), csrf_token='csrf-for-creation',
                              creation_client=client)
        with serving(LabHTTPServer(lab, 0, proxy_mode=True, public_origin='https://' + HOST,
                     base_path=BASE, developer_users=[USER])) as frontend:
            yield service, client, frontend


def request(server, method, path, body=None, headers=None):
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
    supplied = {'Host': HOST, 'X-Forwarded-User': USER}
    if method == 'POST':
        supplied.update({'Origin': 'https://' + HOST, 'X-Animation-Lab-Token': 'csrf-for-creation',
                         'Content-Type': 'application/json'})
    for key, value in (headers or {}).items():
        if value is None:
            supplied.pop(key, None)
        else:
            supplied[key] = value
    try:
        connection.request(method, BASE + path, None if body is None else json.dumps(body), supplied)
        response = connection.getresponse()
        raw, status, metadata = response.read(), response.status, dict(response.getheaders())
        return status, (json.loads(raw) if metadata.get('Content-Type', '').startswith('application/json') else raw), metadata
    finally:
        connection.close()


def test_start_read_and_refresh_use_trusted_principal_without_extra_creation(peers):
    service, client, frontend = peers
    body = dict(request_id=str(uuid4()), experiment_id='cohort-statistics', definition_identity='a'*64,
                input_role='development')
    status, first, _ = request(frontend, 'POST', '/api/creation-runs', body)
    assert status == 202
    status, listing, _ = request(frontend, 'GET', '/api/creation-runs?experiment_id=cohort-statistics')
    assert status == 200 and listing['enabled'] and listing['limits'] == LIMITS
    assert listing['runs'][0]['request_id'] == body['request_id']
    assert listing['runs'][0]['versions'][0]['source_url'].startswith(BASE + '/api/')
    assert client.list('another-developer')['runs'] == []
    assert request(frontend, 'GET', '/api/creation-runs/' + first['id'])[1] == first
    assert request(frontend, 'POST', '/api/creation-runs', body)[1] == first
    assert service.started == 1 and service.advanced == 0
    with pytest.raises(CreationTransportError, match='animation_creation_not_found'):
        client.get('another-developer', first['id'])


@pytest.mark.parametrize('bad', [
    {'X-Forwarded-User': None}, {'X-Forwarded-User': 'other-developer'},
    {'Origin': 'https://evil.invalid'}, {'X-Animation-Lab-Token': None},
])
def test_developer_and_csrf_checks_precede_private_executor_start(peers, bad):
    service, _, frontend = peers
    status, _, _ = request(frontend, 'POST', '/api/creation-runs', {'request_id':str(uuid4())}, bad)
    assert status == 403 and service.started == 0


def test_default_lab_stays_read_only_and_reports_disabled():
    lab = SimpleNamespace(iteration_store=SimpleNamespace(), csrf_token='csrf-for-creation')
    with serving(LabHTTPServer(lab, 0, proxy_mode=True, public_origin='https://' + HOST,
                 base_path=BASE, developer_users=[USER])) as frontend:
        status, listing, _ = request(frontend, 'GET', '/api/creation-runs')
        assert status == 200 and not listing['enabled'] and listing['runs'] == []
        assert request(frontend, 'POST', '/api/creation-runs', {})[0] == 503


def test_media_ranges_and_source_download_use_same_owner_guard(peers):
    service, _, frontend = peers
    run = request(frontend, 'POST', '/api/creation-runs', {'request_id':str(uuid4())})[1]
    path = '/api/creation-runs/' + run['id'] + '/versions/original/'
    status, raw, headers = request(frontend, 'GET', path + 'media', headers={'Range':'bytes=23-42'})
    assert status == 206 and raw == bytes(range(256))[23:43]
    assert headers['Content-Range'] == 'bytes 23-42/1024'
    status, source, headers = request(frontend, 'GET', path + 'source')
    assert status == 200 and source['files']['scene.html'] == '<script>unsafe()</script>'
    assert headers['Content-Disposition'].startswith('attachment;')
    assert request(frontend, 'GET', path + 'media', headers={'X-Forwarded-User':None})[0] == 403
    assert service.advanced == 0


def test_cancel_is_explicit_and_does_not_create_another_run(peers):
    service, _, frontend = peers
    run = request(frontend, 'POST', '/api/creation-runs', {'request_id':str(uuid4())})[1]
    assert request(frontend, 'POST', '/api/creation-runs/' + run['id'] + '/cancel', {})[1]['state'] == 'cancelled'
    assert service.started == 1


def test_private_executor_needs_both_token_and_owner(peers):
    _, client, _ = peers
    with pytest.raises(CreationTransportError) as wrong:
        CreationAPIClient(client.base_url, 'incorrect-token-' + 'z'*32).list(USER)
    assert wrong.value.status == 403
    with pytest.raises(CreationTransportError, match='animation_creation_owner_invalid'):
        client.list('invalid\r\nowner')


@pytest.mark.parametrize('path', ['/api/creation-runs?unknown=x',
    '/api/creation-runs?experiment_id=a&experiment_id=b'])
def test_unexpected_query_never_reaches_executor(peers, path):
    service, _, frontend = peers
    assert request(frontend, 'GET', path)[0] == 422
    assert service.started == 0 and service.advanced == 0
