"""Real HTTP prefix/identity boundaries; synthetic assets do not execute a browser."""
from copy import deepcopy
import http.client
import json
import threading

import pytest

from video_workbench.animation_lab import AnimationLab, LabHTTPServer
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
        case = self.lab.cases['svg-morph']
        return dict(case_id=case['id'], case_identity=case['identity'], variant_id='candidate',
            kind='issue', start_seconds=11.5, end_seconds=11.5, tags=['motion'],
            severity='note', comment='Synthetic proxy API acceptance, not an aesthetic preference.')

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture(scope='module')
def proxy(tmp_path_factory):
    # One server preserves the real snapshot store without duplicating all 15
    # frozen media bundles for every read/header rejection parameter.
    client = ProxyClient(AnimationLab(tmp_path_factory.mktemp('proxy')/'data', ASSETS))
    yield client
    client.close()


def test_prefix_catalog_preview_assets_and_export_preserve_frozen_identity_and_bytes(proxy):
    before = deepcopy(proxy.lab.cases)
    status, catalog, _ = proxy.request('GET', PREFIX+'/api/catalog')
    assert status == 200 and len(catalog['cases']) == 15
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
    assert status == 200 and b'href="./style.css"' in index and b'src="./app.mjs"' in index
    assert b'href="./api/export?format=jsonl"' in index
    for path in ('/app.mjs', '/model.mjs', '/style.css', '/lab-player.mjs'):
        assert proxy.request('GET', PREFIX+path)[0] == 200
    status, wrapper, _ = proxy.request('GET', PREFIX+'/preview/svg-morph/candidate/')
    assert status == 200 and (PREFIX+'/composition/svg-morph/candidate/scene-1/index.html').encode() in wrapper
    assert ('src="'+PREFIX+'/lab-player.mjs"').encode() in wrapper
    for case_id in ('diagram-connections', 'svg-morph', 'canvas-queue'):
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
    for path in ('/app.mjs', '/api/catalog', '/composition/svg-morph/candidate/scene-1/index.html'):
        assert proxy.request('GET', PREFIX+path)[2]['Cache-Control'] == 'no-store'


def test_prefixed_annotation_persists_trusted_principal_without_accepting_client_identity(proxy):
    status, result, _ = proxy.request('POST', PREFIX+'/api/annotations', proxy.issue())
    assert status == 201 and result['annotation']['client']['forwarded_user'] == DEVELOPER
    assert result['annotation']['reviewer'] == 'local-developer'  # Existing store contract stays unchanged.
    status, records, _ = proxy.request('GET', PREFIX+'/api/annotations?case_id=svg-morph')
    assert status == 200 and records['annotations'][0]['client']['forwarded_user'] == DEVELOPER
    annotation_id = result['annotation']['id']
    assert proxy.request('POST', PREFIX+'/api/annotations/'+annotation_id+'/retract', {'reason':'technical test'})[0] == 200
    assert proxy.request('GET', PREFIX+'/api/annotations?case_id=svg-morph')[1]['annotations'][0]['retracted']
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
    for path in ('/', '/api/catalog', '/api/export?format=jsonl', '/app.mjs', '/preview/svg-morph/candidate/'):
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
