"""Real private/public HTTP boundaries; no models, Docker or animation recipes.

The first fixtures exercise actual durable method commission storage. Media
transport fixtures are inert bytes and make no rendering or viewing claims.
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
from video_workbench.animation_method_http import MethodLabClient
from video_workbench.animation_method_service import AnimationMethodService
from video_workbench.animation_lab import LabHTTPServer
from video_workbench import animation_method_renderer as renderer
from agent_service.contracts import canonical_bytes


TOKEN = 'private-method-lab-test-token-' + 'a' * 32
HOST = 'video-service.panghuer.top'
USER = 'casdoor:method-developer'
SECOND = 'casdoor:second-developer'
BASE = '/dev/animations'
PREFIX = '/api/method-lab'
CSRF = 'test-method-csrf'


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


def raw_request(server, method, path, *, body=None, headers=()):
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
    raw = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    try:
        connection.putrequest(method, path, skip_host=True)
        for key, value in headers:
            connection.putheader(key, value)
        if raw is not None:
            connection.putheader('Content-Length', str(len(raw)))
        connection.endheaders(raw)
        response = connection.getresponse()
        data, metadata = response.read(), dict(response.getheaders())
        value = json.loads(data) if metadata.get('Content-Type', '').startswith('application/json') else data
        return response.status, value, metadata
    finally:
        connection.close()


def public_request(server, method, path, body=None, *, headers=None, absolute=False):
    supplied = {'Host': HOST, 'X-Forwarded-User': USER}
    if method == 'POST':
        supplied.update({'Origin': 'https://' + HOST, 'X-Animation-Lab-Token': CSRF,
                         'Content-Type': 'application/json'})
    for key, value in (headers or {}).items():
        if value is None:
            supplied.pop(key, None)
        else:
            supplied[key] = value
    return raw_request(server, method, path if absolute else BASE + path,
                       body=body, headers=list(supplied.items()))


class NoPaidGateway:
    def __init__(self):
        self.preflights = []

    def preflight(self, deadline):
        self.preflights.append(deadline)
        return {}

    def __getattr__(self, name):
        pytest.fail('HTTP reads/start must never call paid Agent stage: ' + name)


class NoRenderer:
    # Capabilities are a local read, distinct from attempting a paid/render
    # stage. This fixture advertises no provider and still forbids execution.
    capabilities = None

    def __getattr__(self, name):
        pytest.fail('HTTP reads/start must never call renderer stage: ' + name)


@pytest.fixture
def durable(tmp_path):
    author, review = NoPaidGateway(), NoPaidGateway()
    service = AnimationMethodService(tmp_path / 'methods', author_gateway=author,
                                    review_gateway=review, renderer=NoRenderer())
    with serving(CreationHTTPServer(SimpleNamespace(), TOKEN, pump=False, method_service=service)) as backend:
        client = MethodLabClient('http://127.0.0.1:' + str(backend.server_port), TOKEN)
        yield service, author, review, backend, client


def start_body():
    return dict(request_id=str(uuid4()), goal='Explain a distribution becoming a selected cohort',
                budget=dict(max_rounds=1, max_seconds=300), duration_seconds=18)


def test_private_caller_and_exact_owner_headers_are_required(durable):
    service, author, review, backend, client = durable
    for headers in (
        [('Host', '127.0.0.1')],
        [('Authorization', 'Bearer ' + 'wrong-' + 'z' * 32), ('X-Animation-Creation-Owner', USER)],
        [('Authorization', 'Bearer ' + TOKEN)],
        [('Authorization', 'Bearer ' + TOKEN), ('X-Animation-Creation-Owner', USER),
         ('X-Animation-Creation-Owner', SECOND)],
        [('Authorization', 'Bearer ' + TOKEN), ('Authorization', 'Bearer ' + TOKEN),
         ('X-Animation-Creation-Owner', USER)],
    ):
        assert raw_request(backend, 'GET', PREFIX + '/catalog', headers=headers)[0] == 403
    assert client.catalogue(USER)['commissions'] == []
    assert service.pending() == [] and author.preflights == review.preflights == []


def test_real_storage_start_is_idempotent_and_gets_never_submit_paid_work(durable):
    service, author, review, _, client = durable
    body = start_body()
    first = client.start(USER, body)
    assert first['state'] == 'queued' and first['execution'] == []
    assert client.start(USER, body)['id'] == first['id']
    before = (len(author.preflights), len(review.preflights))
    for _ in range(3):
        assert client.get(USER, first['id'])['execution'] == []
        assert len(client.catalogue(USER)['commissions']) == 1
        assert client.catalogue(SECOND)['commissions'] == []
    assert before == (len(author.preflights), len(review.preflights))
    assert len(service.pending()) == 1
    with pytest.raises(CreationTransportError) as denied:
        client.get(SECOND, first['id'])
    assert denied.value.status == 404
    assert client.action(USER, first['id'], 'cancel', {})['state'] == 'cancelled'


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('diagnostic,expected', [
    ('Error: method_absolute_seek_mismatch', 'method_absolute_seek_mismatch'),
    ('Error: method_absolute_seek_mismatch SOURCE_SECRET', 'animation_method_render_container_failed'),
])
def test_real_failed_renderer_reason_reaches_private_and_public_http_safely(
        durable, tmp_path, monkeypatch, legacy, diagnostic, expected):
    service, author, review, _, client = durable
    row = client.start(USER, dict(start_body(), criteria=[dict(id='causal', description='The cause is visible.')]))
    image = 'registry.example/renderer:latest@sha256:' + 'a' * 64
    calls = []

    def failed_execution(directory):
        calls.append(str(directory))
        binding = json.loads((directory / 'binding.json').read_bytes())
        request = binding['request']
        (directory / 'work').mkdir()
        receipt = dict(schema=request['schema'].replace('-request.', '-receipt.'), status='failed',
            request_identity=request['identity'], request_sha256=binding['request_sha256'],
            image_digest=binding['image_digest'], host_sha256=request['host_sha256'], error=diagnostic)
        (directory / 'work/render-receipt.json').write_bytes(canonical_bytes(receipt))
        outcome = dict(schema='video.animation-method-render-outcome.v1',
            request_identity=request['identity'], request_sha256=binding['request_sha256'], image=image,
            status='failed', error='animation_method_render_container_failed', automatic_retry=False,
            cleanup=dict(status='removed_and_confirmed_absent'))
        (directory / 'render-outcome.json').write_bytes(canonical_bytes(outcome))
        return outcome

    monkeypatch.setattr(renderer, 'execute_method_render', failed_execution)
    service.renderer = renderer.MethodAnimationRenderer(tmp_path / 'failed-renders', image, enabled=True)
    run = service.commissions.get(USER, row['id'])
    document = service._document(USER, run, 'create')
    source = dict(engine='html-svg', files={'scene.html': '<html><body>inert failed test</body></html>'},
                  strategy=dict(description='HTTP failure test', tradeoffs='No actual media'))
    operation = service.commissions.reserve(USER, row['id'], 'render',
        dict(source=source, document=document, sound_events=[]), 'failed-render')
    service.advance(USER, row['id'])
    assert service.commissions.operation(USER, operation['id'])['result']['error'] == expected
    if legacy:
        service.commissions.stop(USER, row['id'], 'failed',
            '画面运行检查未通过；保留失败证据，不自动重试')
    else:
        service.advance(USER, row['id'])
        assert service.commissions.get(USER, row['id'])['summary']['code'] == expected
    original = service.commissions.get(USER, row['id'])
    original_events = service.commissions.events(USER, row['id'])
    before = (len(author.preflights), len(review.preflights))
    lab = SimpleNamespace(iteration_store=SimpleNamespace(), csrf_token=CSRF, method_client=client)
    with serving(LabHTTPServer(lab, 0, proxy_mode=True, public_origin='https://' + HOST,
                 base_path=BASE, developer_users=[USER])) as frontend:
        for _ in range(2):
            private = client.get(USER, row['id'])
            status, public, headers = public_request(frontend, 'GET', PREFIX + '/commissions/' + row['id'])
            assert status == 200 and headers['Cache-Control'] == 'no-store'
            for detail in (private, public):
                assert detail['state'] == 'failed' and detail['error'] == expected
                assert detail['candidates'] == [] and detail['can_retry'] is True
                assert 'SOURCE_SECRET' not in json.dumps(detail)
    assert len(calls) == 1 and (len(author.preflights), len(review.preflights)) == before
    assert service.commissions.get(USER, row['id']) == original
    assert service.commissions.events(USER, row['id']) == original_events


@pytest.mark.parametrize('summary', [
    'Error: SOURCE_SECRET from historical exception',
    {'message': 'SOURCE_SECRET', 'code': 'unknown_SOURCE_SECRET'},
])
def test_old_failed_summary_without_known_code_has_fixed_http_fallback(durable, summary):
    service, _, _, _, client = durable
    row = client.start(USER, start_body())
    service.commissions.stop(USER, row['id'], 'failed', summary)
    original = service.commissions.get(USER, row['id'])
    failed = client.get(USER, row['id'])
    assert failed['error'] == 'animation_method_execution_failed'
    assert 'SOURCE_SECRET' not in json.dumps(failed)
    assert service.commissions.get(USER, row['id']) == original


def test_method_goals_and_standards_can_cross_the_old_small_annotation_body_limit(durable):
    service, _, _, _, client = durable
    body = dict(start_body(), goal='解释对象去向与统计结果的因果关系。' * 500)
    assert len(json.dumps(body).encode()) > 32768
    row = client.start(USER, body)
    assert row['goal'] == body['goal'] and row['execution'] == []
    assert client.published_catalog(USER) == {'methods': []}
    assert len(service.pending()) == 1


def test_browser_cannot_forge_technical_or_review_evidence(durable):
    service, _, _, backend, client = durable
    body = dict(start_body(), technical={'status': 'passed'}, runtime={'admission': {'status': 'admitted'}})
    with pytest.raises(CreationTransportError) as invalid:
        client.start(USER, body)
    assert invalid.value.status == 422 and service.pending() == []
    headers = [('Authorization', 'Bearer ' + TOKEN), ('X-Animation-Creation-Owner', USER),
               ('Content-Type', 'application/json')]
    duplicate = b'{"request_id":"one","request_id":"two"}'
    assert raw_request(backend, 'POST', PREFIX + '/commissions', body=duplicate, headers=headers)[0] == 400
    assert service.pending() == []


class TransportMethods:
    """Inert remote endpoint fixture with explicit principal checks."""
    def __init__(self):
        self.rows, self.started, self.calls, self.advanced = {}, 0, [], 0
        self.media = bytes(range(256)) * 4

    def start(self, owner, body):
        self.calls.append(('start', owner))
        key = (owner, body['request_id'])
        if key not in self.rows:
            self.started += 1
            identity = str(uuid4())
            base = PREFIX + '/commissions/' + identity + '/candidates/inert-candidate/'
            self.rows[key] = dict(id=identity, request_id=body['request_id'], state='queued',
                candidates=[dict(id='inert-candidate', media_url=base + 'media', source_url=base + 'source')],
                best_candidate_id='inert-candidate', methods=[])
        return deepcopy(self.rows[key])

    def catalogue(self, owner):
        self.calls.append(('catalogue', owner))
        rows = [deepcopy(row) for (actor, _), row in self.rows.items() if actor == owner]
        return dict(schema='video.animation-method-lab.v1', effects=rows, commissions=rows,
                    availability=dict(enabled=True, reason=None))

    def get(self, owner, commission_id):
        self.calls.append(('get', owner))
        found = next((row for (actor, _), row in self.rows.items()
                      if actor == owner and row['id'] == commission_id), None)
        if found is None:
            raise CreationTransportError('animation_method_commission_not_found', 404)
        return deepcopy(found)

    def artifact(self, owner, commission_id, candidate_id, kind):
        self.get(owner, commission_id)
        assert candidate_id == 'inert-candidate'
        return (self.media, 'video/mp4') if kind == 'media' else (
            json.dumps({'files': {'scene.html': '<script>inert_fixture()</script>'}}).encode(), 'application/json')

    def cancel(self, owner, commission_id, body):
        assert body == {}
        result = self.get(owner, commission_id)
        for row in self.rows.values():
            if row['id'] == result['id']:
                row['state'] = 'cancelled'
        result['state'] = 'cancelled'
        return result

    def advance(self, *_args):
        self.advanced += 1
        pytest.fail('HTTP transport tests must never advance paid work')


@pytest.fixture
def peers():
    methods = TransportMethods()
    with serving(CreationHTTPServer(SimpleNamespace(), TOKEN, pump=False, method_service=methods)) as backend:
        client = MethodLabClient('http://127.0.0.1:' + str(backend.server_port), TOKEN)
        lab = SimpleNamespace(iteration_store=SimpleNamespace(), csrf_token=CSRF, method_client=client)
        with serving(LabHTTPServer(lab, 0, proxy_mode=True, public_origin='https://' + HOST,
                     base_path=BASE, developer_users=[USER, SECOND])) as frontend:
            yield methods, client, frontend


def test_two_hops_forward_trusted_owner_and_prefix_candidate_media_once(peers):
    service, client, frontend = peers
    body = start_body()
    status, row, headers = public_request(frontend, 'POST', PREFIX + '/commissions', body)
    assert status == 202 and headers['Cache-Control'] == 'no-store'
    candidate = row['candidates'][0]
    assert candidate['media_url'].startswith(BASE + PREFIX + '/')
    assert BASE + BASE not in candidate['media_url']
    assert public_request(frontend, 'POST', PREFIX + '/commissions', body)[1] == row
    for _ in range(3):
        assert public_request(frontend, 'GET', PREFIX + '/commissions/' + row['id'])[1] == row
        status, catalogue, _ = public_request(frontend, 'GET', PREFIX + '/catalog')
        assert status == 200 and catalogue['csrf_token'] == CSRF
        assert catalogue['commissions'][0]['candidates'][0] == candidate
    assert public_request(frontend, 'GET', PREFIX + '/catalog', headers={'X-Forwarded-User': SECOND})[1]['commissions'] == []
    assert public_request(frontend, 'GET', PREFIX + '/commissions/' + row['id'],
                          headers={'X-Forwarded-User': SECOND})[0] == 404
    assert client.catalogue(SECOND)['commissions'] == []
    assert service.started == 1 and service.advanced == 0
    assert all(owner in {USER, SECOND} for _, owner in service.calls)
    assert TOKEN not in json.dumps(catalogue)


@pytest.mark.parametrize('bad', [
    {'X-Forwarded-User': None}, {'X-Forwarded-User': 'unknown'}, {'Host': 'evil.invalid'},
    {'Origin': None}, {'Origin': 'https://evil.invalid'}, {'X-Animation-Lab-Token': None},
    {'X-Animation-Lab-Token': 'wrong'}, {'Content-Type': 'text/plain'},
])
def test_public_csrf_and_developer_guard_precedes_remote_start(peers, bad):
    service, _, frontend = peers
    status, _, _ = public_request(frontend, 'POST', PREFIX + '/commissions', start_body(), headers=bad)
    assert status in {403, 415} and service.started == 0 and service.calls == []


def test_prefixed_media_range_head_source_download_and_owner_guard(peers):
    service, _, frontend = peers
    row = public_request(frontend, 'POST', PREFIX + '/commissions', start_body())[1]
    candidate = row['candidates'][0]
    status, raw, headers = public_request(frontend, 'GET', candidate['media_url'],
        headers={'Range': 'bytes=20-39'}, absolute=True)
    assert status == 206 and raw == service.media[20:40]
    assert headers['Content-Range'] == 'bytes 20-39/1024'
    status, raw, headers = public_request(frontend, 'HEAD', candidate['media_url'], absolute=True)
    assert status == 200 and raw == b'' and headers['Content-Length'] == '1024'
    status, source, headers = public_request(frontend, 'GET', candidate['source_url'], absolute=True)
    assert status == 200 and source['files']['scene.html'] == '<script>inert_fixture()</script>'
    assert headers['Content-Disposition'].startswith('attachment;')
    assert public_request(frontend, 'GET', candidate['media_url'], headers={'X-Forwarded-User': SECOND},
                          absolute=True)[0] == 404
    assert public_request(frontend, 'GET', candidate['media_url'], headers={'X-Forwarded-User': None},
                          absolute=True)[0] == 403
    assert service.advanced == 0


def test_gallery_is_new_default_and_queries_and_internal_reads_are_not_browser_writes(peers):
    service, _, frontend = peers
    status, html, _ = public_request(frontend, 'GET', '/')
    assert status == 200 and b'method-gallery.mjs' in html
    for name in ('method-gallery.html', 'method-gallery.mjs', 'method-gallery.css'):
        assert public_request(frontend, 'GET', '/' + name)[0] == 200
    assert public_request(frontend, 'GET', PREFIX + '/catalog?refresh=1')[0] == 422
    assert public_request(frontend, 'POST', PREFIX + '/methods/read', {})[0] == 404
    assert public_request(frontend, 'GET', PREFIX + '/catalog', absolute=True)[0] == 404
    assert service.calls == [] and service.started == 0


def test_without_executor_catalogue_is_readonly_disabled_and_start_is_rejected():
    lab = SimpleNamespace(iteration_store=SimpleNamespace(), csrf_token=CSRF)
    with serving(LabHTTPServer(lab, 0, proxy_mode=True, public_origin='https://' + HOST,
                 base_path=BASE, developer_users=[USER])) as frontend:
        status, catalogue, _ = public_request(frontend, 'GET', PREFIX + '/catalog')
        assert status == 200 and catalogue['effects'] == catalogue['commissions'] == []
        assert catalogue['availability']['enabled'] is False
        assert public_request(frontend, 'POST', PREFIX + '/commissions', start_body())[0] == 503
