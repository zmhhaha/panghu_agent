"""Real HTTP prefix/identity boundaries; synthetic assets do not execute a browser."""
from copy import deepcopy
from email.message import Message
import gzip
import http.client
import json
import socket
import threading
from types import SimpleNamespace

import pytest

from video_workbench.animation_lab import AnimationLab, LabHTTPServer, LabHandler, MAX_BODY
from video_workbench import animation_lab as lab_module
from video_workbench.animation_experiments import SCHEMA, definitions
from video_workbench.motion import sha256


ORIGIN = 'https://video-service.panghuer.top'
HOST = 'video-service.panghuer.top'
PREFIX = '/dev/animations'
DEVELOPER = 'casdoor:developer-sub'
ASSETS = {'gsap.min.js': b'synthetic API GSAP bytes', 'NotoSansCJK-Regular.ttc': b'ttcf-synthetic'}


class ProxyClient:
    def __init__(self, lab):
        self.lab = lab
        self.server = LabHTTPServer(lab, 0, proxy_mode=True, public_origin=ORIGIN,
            base_path=PREFIX+'/', developer_users=[DEVELOPER])
        self.thread = threading.Thread(target=self.server.serve_forever,
            kwargs={'poll_interval': .02}, daemon=True)
        self.thread.start()

    def request(self, method, path, body=None, *, headers=(), omit=(), defaults=True, decode_json=True):
        payload = None if body is None else json.dumps(body).encode()
        values = [('Host', HOST), ('X-Forwarded-User', DEVELOPER)] if defaults else []
        if method == 'POST' and defaults:
            values += [('Origin', ORIGIN), ('X-Animation-Lab-Token', self.lab.csrf_token),
                       ('Content-Type', 'application/json')]
        if payload is not None:
            values.append(('Content-Length', str(len(payload))))
        values = [(key,value) for key,value in values if key.lower() not in {name.lower() for name in omit}]
        values += list(headers)
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            connection.putrequest(method, path, skip_host=True)
            for key,value in values:
                connection.putheader(key, value)
            connection.endheaders(payload)
            response = connection.getresponse()
            raw, response_headers = response.read(), dict(response.getheaders())
            data = json.loads(raw) if decode_json and response_headers.get('Content-Type', '').startswith('application/json') else raw
            return response.status, data, response_headers
        finally:
            connection.close()

    def issue(self):
        case = self.lab.cases['interface-library-search']
        return dict(case_id=case['id'], case_identity=case['identity'], variant_id='candidate',
            kind='issue', start_seconds=11.5, end_seconds=11.5, tags=['motion'],
            severity='note', comment='Synthetic proxy API acceptance, not an aesthetic preference.')

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture(scope='module')
def proxy(tmp_path_factory):
    # One server preserves the real snapshot store without duplicating all 39
    # frozen media bundles for every read/header rejection parameter.
    client = ProxyClient(AnimationLab(tmp_path_factory.mktemp('proxy')/'data', ASSETS))
    yield client
    client.close()


def test_capability_definition_and_feedback_keep_proxy_authentication_and_prefix(proxy):
    status, catalogue, _ = proxy.request('GET', PREFIX+'/api/experiments')
    expected = {item['id']: item for item in definitions()}
    assert status == 200 and catalogue['schema'] == SCHEMA
    assert catalogue['csrf_token'] == proxy.lab.csrf_token
    assert {item['id'] for item in catalogue['experiments']} == set(expected)
    assert len(catalogue['experiments']) == len(expected)  # Reject duplicate catalogue rows.
    assert proxy.request('GET', PREFIX+'/api/experiments', omit=['X-Forwarded-User'])[0] == 403
    assert proxy.request('GET', '/api/experiments')[0] == 404
    for experiment in catalogue['experiments']:
        definition = expected[experiment['id']]
        # The HTTP prefix must not change the frozen goal, role constraints or
        # definition identity as the registry gains additional capabilities.
        assert {key: experiment[key] for key in definition} == definition
        assert experiment['experiment_identity'] == definition['identity']
        assert experiment['versions'] == experiment['feedback'] == experiment['comparisons'] == []
        assert experiment['status'] == 'unexecuted'
        path = '/api/experiments/' + experiment['id']
        status, detail, _ = proxy.request('GET', PREFIX+path)
        assert status == 200 and detail['csrf_token'] == proxy.lab.csrf_token
        assert {key: value for key, value in detail.items() if key != 'csrf_token'} == experiment
        for protected in (path, path+'/runs/missing-development-v1/output.mp4'):
            assert proxy.request('GET', PREFIX+protected, omit=['X-Forwarded-User'])[0] == 403
        body = dict(experiment_identity=experiment['identity'], kind='issue', version_ids=['missing'],
                    version_identities={'missing': '0'*64}, comment='No actual media to annotate',
                    start_seconds=0, end_seconds=1, preference=None)
        target = PREFIX+path+'/feedback'
        assert proxy.request('POST', target, body, omit=['X-Animation-Lab-Token'])[0] == 403
        assert proxy.request('POST', target, dict(body, experiment_identity='0'*64))[0] == 409
        assert proxy.request('POST', target, body)[0] == 422
        assert proxy.request('GET', PREFIX+path)[1]['feedback'] == []
    assert proxy.request('GET', PREFIX+'/api/experiments/unknown')[0] == 404


def test_prefix_catalog_preview_assets_and_export_preserve_frozen_identity_and_bytes(proxy):
    before = deepcopy(proxy.lab.cases)
    status, catalog, _ = proxy.request('GET', PREFIX+'/api/catalog')
    assert status == 200 and len(catalog['cases']) == 39
    for case in catalog['cases']:
        assert case['identity'] == before[case['id']]['identity']
        for variant in case['variants']:
            if variant['available']:
                assert variant['preview_url'] == PREFIX+before[case['id']]['variants'][0 if variant['id']=='baseline' else 1]['preview_url']
                assert {row['name'] for row in variant['preload_assets']} == set(ASSETS)
                for asset in variant['preload_assets']:
                    assert asset == dict(name=asset['name'], sha256=sha256(ASSETS[asset['name']]),
                        bytes=len(ASSETS[asset['name']]),
                        asset_url=PREFIX+f"/assets/{sha256(ASSETS[asset['name']])}/{asset['name']}")
    assert proxy.lab.cases == before
    status, index, _ = proxy.request('GET', PREFIX+'/')
    assert status == 200 and b'href="./method-gallery.css"' in index and b'src="./method-gallery.mjs"' in index
    assert b'id="method-gallery-app"' in index
    for path in ('/app.mjs', '/model.mjs', '/style.css', '/lab-player.mjs', '/experiments.mjs', '/experiment-model.mjs'):
        assert proxy.request('GET', PREFIX+path)[0] == 200
    status, wrapper, _ = proxy.request('GET', PREFIX+'/preview/interface-library-search/candidate/')
    assert status == 200 and (PREFIX+'/composition/interface-library-search/candidate/scene-1/index.html').encode() in wrapper
    assert ('src="'+PREFIX+'/lab-player.mjs"').encode() in wrapper
    for case_id in ('domain-diagram-water', 'interface-library-search', 'domain-canvas-clinic'):
        clip = proxy.lab.bundles[case_id, 'candidate']['clips'][0]
        base = PREFIX+f'/composition/{case_id}/candidate/{clip["id"]}/'
        for name, expected in clip['files'].items():
            status, raw, _ = proxy.request('GET', base+name, decode_json=False)
            assert status == 200
            assert raw == expected and sha256(raw) == sha256(expected)
        for name, expected in ASSETS.items():
            status, raw, headers = proxy.request('GET', base+'assets/'+name)
            assert status == 307 and raw == b'' and headers['Cache-Control'] == 'no-store'
            assert headers['Location'] == PREFIX+f'/assets/{sha256(expected)}/{name}'
            status, raw, headers = proxy.request('GET', headers['Location'])
            assert status == 200 and raw == expected
            assert headers['Cache-Control'] == 'private, max-age=31536000, immutable'
    status, exported, headers = proxy.request('GET', PREFIX+'/api/export?format=jsonl')
    assert status == 200 and 'attachment;' in headers['Content-Disposition']
    snapshots = [row['snapshot'] for row in map(json.loads,exported.splitlines()) if row['record_type']=='case_snapshot']
    assert {row['identity'] for row in snapshots} == {row['identity'] for row in before.values()}
    for snapshot in snapshots:
        assert PREFIX not in json.dumps(snapshot)
        assert 'preload_assets' not in json.dumps(snapshot)


def test_regression_export_excludes_capability_experiment_feedback(proxy, monkeypatch):
    for store, marker in ((proxy.lab.store, 'regression'), (proxy.lab.triage_store, 'triage'),
                          (proxy.lab.iteration_store, 'iteration'), (proxy.lab.experiments, 'experiment')):
        monkeypatch.setattr(store, 'export_jsonl', lambda marker=marker: (json.dumps({'type': marker})+'\n').encode())
    status, raw, _ = proxy.request('GET', PREFIX+'/api/export?format=jsonl&scope=regressions')
    assert status == 200
    assert [json.loads(line)['type'] for line in raw.splitlines()] == ['regression', 'triage', 'iteration']
    status, raw, _ = proxy.request('GET', PREFIX+'/api/export?format=jsonl')
    assert status == 200
    assert [json.loads(line)['type'] for line in raw.splitlines()] == ['regression', 'triage', 'iteration', 'experiment']
    for query in ('scope=unknown', 'scope=', 'scope=all&scope=regressions', 'scope=regressions&extra=1'):
        assert proxy.request('GET', PREFIX+'/api/export?'+query)[0] == 422


def test_shared_asset_routes_require_exact_current_hash_name_and_authenticated_developer(proxy):
    name = 'NotoSansCJK-Regular.ttc'
    path = PREFIX+f'/assets/{sha256(ASSETS[name])}/{name}'
    for suffix in (f"/assets/{'0'*64}/{name}", f'/assets/{sha256(ASSETS[name])}/gsap.min.js',
                   f'/assets/{sha256(ASSETS[name])}/animation-lab.db', '/assets/../animation-lab.db'):
        status, _, headers = proxy.request('GET', PREFIX+suffix)
        assert status == 404 and headers['Cache-Control'] == 'no-store'
    for headers in [(), (('X-Forwarded-User', 'another-user'),)]:
        status, _, values = proxy.request('GET', path, omit=('X-Forwarded-User',), headers=headers)
        assert status == 403 and values['Cache-Control'] == 'no-store'
    # Shared cached responses are opt-in only; scripts, evidence, catalog, and
    # composition bytes continue to have their original no-store policy.
    for path in ('/app.mjs', '/api/catalog', '/composition/interface-library-search/candidate/scene-1/index.html'):
        assert proxy.request('GET', PREFIX+path)[2]['Cache-Control'] == 'no-store'


@pytest.mark.parametrize('retired', ['diagram-connections', 'diagram-long-label', 'process-retry',
    'continuity-retain', 'continuity-copy', 'canvas-queue', 'three-layers',
    'condition-branch', 'parallel-wait', 'architecture-expand', 'focus-shift',
    'data-compare', 'code-execution', 'local-replace', 'svg-morph'])
def test_retired_examples_have_no_public_preview_composition_or_annotation_route(proxy, retired):
    assert retired not in proxy.lab.cases
    assert (retired, 'candidate') not in proxy.lab.bundles
    assert proxy.request('GET', PREFIX+f'/preview/{retired}/candidate/')[0] == 404
    assert proxy.request('GET', PREFIX+f'/composition/{retired}/candidate/scene-1/index.html')[0] == 404
    assert proxy.request('GET', PREFIX+'/api/annotations?case_id='+retired)[0] == 404
    status, result, _ = proxy.request('POST', PREFIX+'/api/annotations', dict(proxy.issue(), case_id=retired))
    assert status == 422 and result['error'] == 'animation_lab_unknown_case'


@pytest.mark.parametrize('accept_encoding,compressed', [
    (None, False), ('', False), ('identity', False), ('br, deflate', False),
    ('gzip', True), ('br, gzip, deflate', True), ('GZip ; q=0.500', True),
    ('gzip;q=0.001', True), ('*;q=0.7', True), ('gzip;q=1, *;q=0', True),
    ('gzip;q=0', False), ('gzip;q=0.000', False), ('gzip;q=0, *;q=1', False),
    ('gzip;q=0.5, gzip;q=0', False), ('gzip;q=1.01', False), ('gzip;q=invalid', False),
    ('gzip;q=.5', False), ('gzip;q=0.5;other=1', False), ('*;q=0', False),
])
def test_shared_asset_transport_negotiation_preserves_raw_hashes_and_case_identities(proxy, accept_encoding, compressed):
    identities = {key: row['identity'] for key, row in proxy.lab.cases.items()}
    player = proxy.lab.player_script
    header = () if accept_encoding is None else (('Accept-Encoding', accept_encoding),)
    for name, expected in ASSETS.items():
        path = PREFIX+f'/assets/{sha256(expected)}/{name}'
        status, raw, headers = proxy.request('GET', path, headers=header)
        assert status == 200 and headers['Vary'] == 'Accept-Encoding'
        assert int(headers['Content-Length']) == len(raw)
        assert headers['Cache-Control'] == 'private, max-age=31536000, immutable'
        if compressed:
            assert headers['Content-Encoding'] == 'gzip'
            assert raw == proxy.lab.gzip_assets[name]
            decoded = gzip.decompress(raw)
        else:
            assert 'Content-Encoding' not in headers
            decoded = raw
        assert decoded == expected and sha256(decoded) == sha256(expected)
    assert {key: row['identity'] for key, row in proxy.lab.cases.items()} == identities
    assert proxy.lab.player_script == player
    # gzip is restricted to shared immutable resources: no changes to player,
    # generated HTML, API JSON or redirect responses even when accepted.
    for path in ('/lab-player.mjs', '/api/catalog',
                 '/composition/interface-library-search/candidate/scene-1/index.html',
                 '/composition/interface-library-search/candidate/scene-1/assets/gsap.min.js'):
        _, _, headers = proxy.request('GET', PREFIX+path, headers=header)
        assert 'Content-Encoding' not in headers and 'Vary' not in headers
        assert headers['Cache-Control'] == 'no-store'


def test_repeated_accept_encoding_cannot_override_an_explicit_gzip_refusal(proxy):
    name, expected = next(iter(ASSETS.items()))
    path = PREFIX+f'/assets/{sha256(expected)}/{name}'
    _, raw, headers = proxy.request('GET', path, headers=(
        ('Accept-Encoding', 'gzip;q=1'), ('Accept-Encoding', 'gzip;q=0, *;q=1')))
    assert raw == expected and 'Content-Encoding' not in headers


def test_prefixed_annotation_persists_trusted_principal_without_accepting_client_identity(proxy):
    status, result, _ = proxy.request('POST', PREFIX+'/api/annotations', proxy.issue())
    assert status == 201 and result['annotation']['client']['forwarded_user'] == DEVELOPER
    assert result['annotation']['reviewer'] == 'local-developer'  # Existing store contract stays unchanged.
    status, records, _ = proxy.request('GET', PREFIX+'/api/annotations?case_id=interface-library-search')
    assert status == 200 and records['annotations'][0]['client']['forwarded_user'] == DEVELOPER
    annotation_id = result['annotation']['id']
    assert proxy.request('POST', PREFIX+'/api/annotations/'+annotation_id+'/retract', {'reason':'technical test'})[0] == 200
    assert proxy.request('GET', PREFIX+'/api/annotations?case_id=interface-library-search')[1]['annotations'][0]['retracted']
    for key in ('client', 'forwarded_user', 'reviewer'):
        assert proxy.request('POST', PREFIX+'/api/annotations', dict(proxy.issue(), **{key:'forged'}))[0] == 422


@pytest.mark.parametrize('omit,headers,error', [
    (('X-Forwarded-User',), (), 'animation_lab_developer_required'),
    (('X-Forwarded-User',), (('X-Forwarded-User','some-other-user'),), 'animation_lab_developer_required'),
    (('X-Forwarded-User',), (('X-Forwarded-User', DEVELOPER+',other'),), 'animation_lab_developer_required'),
    ((), (('X-Forwarded-User', DEVELOPER),), 'animation_lab_developer_required'),
    (('X-Forwarded-User',), (('X-Forwarded-User', 'other'),('X-Auth-Request-User', DEVELOPER)), 'animation_lab_developer_required'),
    (('Host',), (('Host','attacker.invalid'),('X-Forwarded-Host',HOST)), 'animation_lab_host_rejected'),
    ((), (('Host', HOST),), 'animation_lab_host_rejected'),
    ((), (('Origin', 'https://attacker.invalid'),), 'animation_lab_origin_rejected'),
    ((), (('Origin', ORIGIN),('Origin', ORIGIN)), 'animation_lab_origin_rejected'),
])
def test_auth_and_fixed_origin_reject_ambiguous_or_spoofed_headers_on_reads(proxy, omit, headers, error):
    for path in ('/', '/api/catalog', '/api/export?format=jsonl', '/app.mjs', '/preview/interface-library-search/candidate/'):
        status, result, _ = proxy.request('GET', PREFIX+path, omit=omit, headers=headers)
        assert (status,result['error']) == (403,error)


@pytest.mark.parametrize('omit,headers,error', [
    (('Origin',), (), 'animation_lab_origin_required'),
    (('Origin',), (('Origin','http://'+HOST),), 'animation_lab_origin_rejected'),
    (('Origin',), (('Origin','https://attacker.invalid'),('X-Forwarded-Proto','https')), 'animation_lab_origin_rejected'),
    (('X-Animation-Lab-Token',), (), 'animation_lab_token_required'),
    (('X-Animation-Lab-Token',), (('X-Animation-Lab-Token','invalid'),), 'animation_lab_token_required'),
    ((), (('X-Animation-Lab-Token','invalid'),), 'animation_lab_token_required'),
    ((), (('Content-Length','9'),), 'animation_lab_body_size'),
    ((), (('Transfer-Encoding','chunked'),), 'animation_lab_body_size'),
    ((), (('Transfer-Encoding',''),), 'animation_lab_body_size'),
])
def test_https_write_origin_csrf_and_body_boundaries_remain_strict(proxy, omit, headers, error):
    before = proxy.lab.store.list()
    status,result,_ = proxy.request('POST', PREFIX+'/api/annotations', proxy.issue(), omit=omit, headers=headers)
    assert status in {403,413} and result['error'] == error
    assert proxy.lab.store.list() == before


@pytest.mark.parametrize('omit,headers', [
    (('Content-Type',), ()),
    ((), (('Content-Type','application/json'),)),
    (('Content-Type',), (('Content-Type','text/plain'),)),
])
def test_missing_or_ambiguous_json_content_type_is_rejected(proxy, omit, headers):
    before = proxy.lab.store.list()
    status, result, _ = proxy.request('POST', PREFIX+'/api/annotations', proxy.issue(), omit=omit, headers=headers)
    assert (status, result['error']) == (415, 'animation_lab_json_required')
    assert proxy.lab.store.list() == before


@pytest.mark.parametrize('missing_csrf,expected_status,expected_error', [
    (False, 415, 'animation_lab_json_required'),
    (True, 403, 'animation_lab_token_required'),
])
def test_early_rejection_drains_delayed_raw_body_before_sending_response(
        proxy, monkeypatch, missing_csrf, expected_status, expected_error):
    before = proxy.lab.store.list()
    payload = json.dumps(proxy.issue()).encode()
    rejection_entered, body_sent = threading.Event(), threading.Event()
    consumed = []
    drain = LabHandler._drain_rejected_post_body

    def observed_drain(handler):
        # Synchronize after the rejection decision and before draining. The
        # client sends its body later, without relying on scheduler sleeps.
        rejection_entered.set()
        assert body_sent.wait(3)
        drain(handler)
        consumed.append(handler._post_body_consumed)

    monkeypatch.setattr(LabHandler, '_drain_rejected_post_body', observed_drain)
    headers = [('Host', HOST), ('X-Forwarded-User', DEVELOPER), ('Origin', ORIGIN),
               ('Content-Type', 'application/json'), ('Content-Length', str(len(payload)))]
    if not missing_csrf:
        headers.extend([('X-Animation-Lab-Token', proxy.lab.csrf_token),
                        ('Content-Type', 'application/json')])
    head = ('POST '+PREFIX+'/api/annotations HTTP/1.0\r\n'
            + ''.join(name+': '+value+'\r\n' for name, value in headers) + '\r\n').encode()
    with socket.create_connection(('127.0.0.1', proxy.server.server_port), timeout=5) as stream:
        try:
            stream.sendall(head)
            assert rejection_entered.wait(3)
            stream.sendall(payload)
            body_sent.set()
            response = http.client.HTTPResponse(stream)
            response.begin()
            assert response.status == expected_status
            assert json.loads(response.read()) == {'error': expected_error}
            assert consumed == [len(payload)]
        finally:
            body_sent.set()
    assert proxy.lab.store.list() == before


def test_error_after_json_parsing_does_not_consume_the_body_twice(proxy, monkeypatch):
    before = proxy.lab.store.list()
    reads = []
    read = LabHandler._read_post_body

    def counted(handler, length, **kwargs):
        reads.append((length, handler._post_body_consumed, kwargs.get('deadline')))
        return read(handler, length, **kwargs)

    monkeypatch.setattr(LabHandler, '_read_post_body', counted)
    status, result, _ = proxy.request('POST', PREFIX+'/api/not-a-route', {})
    assert status == 404 and result == {'error': 'animation_lab_not_found'}
    assert reads == [(len(json.dumps({}).encode()), 0, None)]
    assert proxy.lab.store.list() == before


@pytest.mark.parametrize('headers', [
    [], [('Content-Length', '2'), ('Content-Length', '2')],
    [('Content-Length', '-1')], [('Content-Length', '2, 2')],
    [('Content-Length', str(MAX_BODY + 1))],
    [('Content-Length', '2'), ('Transfer-Encoding', 'chunked')],
    [('Content-Length', '2'), ('Transfer-Encoding', '')],
])
def test_rejection_does_not_read_missing_ambiguous_or_unbounded_framing(headers):
    handler = object.__new__(LabHandler)
    handler.headers = Message()
    for key, value in headers:
        handler.headers[key] = value
    handler._post_body_consumed = 0

    def forbidden(*_args, **_kwargs):
        raise AssertionError('Ambiguous or unbounded framing must not trigger body reads')

    handler._read_post_body = forbidden
    handler._drain_rejected_post_body()
    assert handler.close_connection is True


def test_rejection_drain_uses_one_wall_clock_budget_instead_of_resetting_for_each_chunk(monkeypatch):
    handler = object.__new__(LabHandler)
    handler.headers = Message()
    handler.headers['Content-Length'] = '3'
    handler._post_body_consumed = 0
    timeouts, reads = [], []
    handler.connection = SimpleNamespace(gettimeout=lambda: 7, settimeout=timeouts.append)
    handler.rfile = SimpleNamespace(read1=lambda size: reads.append(size) or b'x')
    clock = iter([0, .1, .2, .3])
    monkeypatch.setattr(lab_module, 'time', SimpleNamespace(monotonic=lambda: next(clock)))
    handler._drain_rejected_post_body()
    assert handler._post_body_consumed == 2 and reads == [3, 2]
    assert timeouts[:2] == pytest.approx([.15, .05]) and timeouts[-1] == 7


def test_unprefixed_and_traversal_routes_cannot_escape_to_another_service_or_data(proxy):
    for path in ('/api/catalog', '/', '/app.mjs', '/animation-lab.db',
                 PREFIX+'-other/api/catalog', PREFIX+'/%2e%2e/animation-lab.db',
                 PREFIX+'/data/animation-lab.db'):
        assert proxy.request('GET', path)[0] == 404
    status, _, headers = proxy.request('GET', PREFIX)
    assert status == 308 and headers['Location'] == PREFIX+'/'


def test_health_probe_has_no_sample_data_or_csrf_even_without_proxy_identity(proxy):
    status, result, _ = proxy.request('GET', '/healthz', defaults=False)
    assert status == 200 and result == {'status':'ok'}
    assert proxy.request('GET', PREFIX+'/api/catalog', defaults=False)[0] == 403


def test_evaluation_resource_urls_are_prefixed_at_http_boundary_only(proxy, monkeypatch):
    original = dict(id='saved-run', task_url='/api/evaluations/saved-run/revision-task?report_identity=abc',
        findings=[dict(frames=[dict(image_url='/api/evaluations/saved-run/frames/one?report_identity=abc')])])
    monkeypatch.setattr(proxy.lab, 'evaluation_detail', lambda _: original)
    status, value, _ = proxy.request('GET', PREFIX+'/api/evaluations/saved-run')
    assert status == 200 and value['task_url'] == PREFIX+original['task_url']
    assert value['findings'][0]['frames'][0]['image_url'] == PREFIX+original['findings'][0]['frames'][0]['image_url']
    assert original['task_url'].startswith('/api/')
    assert proxy.server.public_urls(dict(report_url='/evaluations/saved-run/report.html'))['report_url'].startswith(PREFIX+'/evaluations/')


@pytest.mark.parametrize('options', [
    dict(listen='0.0.0.0'), dict(base_path=PREFIX), dict(public_origin=ORIGIN),
    dict(proxy_mode=True), dict(proxy_mode=True, public_origin=ORIGIN),
    dict(proxy_mode=True, public_origin='http://video-service.panghuer.top',developer_users=[DEVELOPER]),
    dict(proxy_mode=True, public_origin=ORIGIN+'/',developer_users=[DEVELOPER]),
    dict(proxy_mode=True, public_origin=ORIGIN+'/path',developer_users=[DEVELOPER]),
    dict(proxy_mode=True, public_origin='https://a..invalid',developer_users=[DEVELOPER]),
    dict(proxy_mode=True, public_origin=ORIGIN+':99999',developer_users=[DEVELOPER]),
    dict(proxy_mode=True, public_origin=ORIGIN,developer_users=['a,b']),
    dict(proxy_mode=True, public_origin=ORIGIN,developer_users=DEVELOPER),
    dict(proxy_mode=True, public_origin=ORIGIN,developer_users=[DEVELOPER],base_path='/dev/../api'),
    dict(proxy_mode=True, public_origin=ORIGIN,developer_users=[DEVELOPER],base_path='//dev'),
])
def test_server_startup_cannot_silently_relax_localhost_or_infer_proxy_configuration(options):
    with pytest.raises(ValueError):
        LabHTTPServer(None, 0, **options)
