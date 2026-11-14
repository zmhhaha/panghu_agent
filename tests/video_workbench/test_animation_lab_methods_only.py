"""Methods-only startup and real HTTP proxy boundaries, without media execution."""
from contextlib import contextmanager
import http.client
import json
import re
import threading
from urllib.parse import urljoin

import pytest

from video_workbench import animation_lab as module
from video_workbench.animation_lab import AnimationLab, LabError, LabHTTPServer


ORIGIN = 'https://video-service.panghuer.top'
HOST = 'video-service.panghuer.top'
PREFIX = '/dev/animations'
DEVELOPER = 'casdoor:developer'


class MethodClient:
    def __init__(self):
        self.calls = []

    def catalogue(self, owner):
        self.calls.append(('catalogue', owner))
        return dict(effects=[dict(id='sample', media_url='/api/method-lab/commissions/c/candidates/v/media')],
                    commissions=[], availability=dict(enabled=True))

    def get(self, owner, commission_id):
        self.calls.append(('get', owner, commission_id))
        return dict(id=commission_id, candidates=[])

    def start(self, owner, body):
        self.calls.append(('start', owner, body))
        return dict(id='new-commission', request_id=body['request_id'], candidates=[])

    def action(self, owner, commission_id, action, body):
        self.calls.append(('action', owner, commission_id, action, body))
        return dict(id=commission_id, candidates=[])

    def artifact(self, owner, commission_id, candidate_id, kind):
        self.calls.append(('artifact', owner, commission_id, candidate_id, kind))
        return (b'{"fixture":true}', 'application/json') if kind == 'source' else (b'fixture-media-bytes', 'video/mp4')


@contextmanager
def serving(lab):
    with LabHTTPServer(lab, 0, proxy_mode=True, public_origin=ORIGIN,
            base_path=PREFIX, developer_users=[DEVELOPER]) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=5)


def request(server, method, path, body=None, *, omit=(), extra=None):
    headers = {'Host': HOST, 'X-Forwarded-User': DEVELOPER}
    raw = None
    if method == 'POST':
        raw = json.dumps(body).encode()
        headers.update({'Origin': ORIGIN, 'Content-Type': 'application/json',
                        'X-Animation-Lab-Token': server.lab.csrf_token})
    for key in omit:
        headers.pop(key, None)
    headers.update(extra or {})
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
    try:
        connection.request(method, path, body=raw, headers=headers)
        response = connection.getresponse()
        return response.status, response.read(), dict(response.getheaders())
    finally:
        connection.close()


def forbid_legacy(*args, **kwargs):
    raise AssertionError('Methods-only startup must not initialize legacy storage, cases or assets')


def test_methods_only_has_no_legacy_storage_compilation_assets_or_evaluation_dependency(tmp_path, monkeypatch):
    for name in ('catalog', 'compile_sample', 'AnnotationStore', 'TriageStore', 'IterationStore', 'ExperimentStore'):
        monkeypatch.setattr(module, name, forbid_legacy)
    root = tmp_path / 'should-not-exist'
    lab = AnimationLab(root, None, tmp_path / 'missing-readonly-evaluations',
                       method_client=MethodClient(), methods_only=True)
    assert not root.exists()
    assert lab.methods_only and lab.cases == lab.bundles == {}
    assert not hasattr(lab, 'store') and not hasattr(lab, 'experiments')
    assert not hasattr(lab, 'iteration_store') and not hasattr(lab, 'player_script')
    assert isinstance(lab.csrf_token, str) and len(lab.csrf_token) >= 32
    with pytest.raises(LabError, match='animation_method_configuration_required'):
        AnimationLab(root, {}, methods_only=True)


def test_methods_only_routes_preserve_identity_csrf_prefix_download_and_media_range(tmp_path):
    client = MethodClient()
    lab = AnimationLab(tmp_path / 'unused', {}, method_client=client, methods_only=True)
    with serving(lab) as server:
        assert request(server, 'GET', '/healthz', omit=['X-Forwarded-User'])[0] == 200
        status, html, _ = request(server, 'GET', PREFIX + '/')
        assert status == 200 and b'id="method-gallery-app"' in html
        for asset in ('method-gallery.mjs', 'method-gallery.css', 'knowledge.html', 'knowledge.mjs', 'knowledge.css'):
            assert request(server, 'GET', PREFIX + '/' + asset)[0] == 200
        assert request(server, 'GET', PREFIX + '/knowledge.html', omit=['X-Forwarded-User'])[0] == 403
        path = PREFIX + '/api/method-lab/catalog'
        status, raw, _ = request(server, 'GET', path)
        assert status == 200
        value = json.loads(raw)
        assert value['csrf_token'] == lab.csrf_token
        assert value['effects'][0]['media_url'] == PREFIX + '/api/method-lab/commissions/c/candidates/v/media'
        assert client.calls == [('catalogue', DEVELOPER)]
        assert request(server, 'GET', path, omit=['X-Forwarded-User'])[0] == 403
        assert request(server, 'GET', path, extra={'Host': 'evil.example'})[0] == 403
        assert request(server, 'GET', '/api/method-lab/catalog')[0] == 404
        assert request(server, 'GET', path + '?other=1')[0] == 422
        post = PREFIX + '/api/method-lab/commissions'
        assert request(server, 'POST', post, {'request_id': 'fixture'}, omit=['X-Animation-Lab-Token'])[0] == 403
        assert request(server, 'POST', post, {'request_id': 'fixture'}, extra={'Origin': 'https://evil.example'})[0] == 403
        assert not any(row[0] == 'start' for row in client.calls)
        status, raw, _ = request(server, 'POST', post, {'request_id': 'fixture'})
        assert status == 202 and json.loads(raw)['id'] == 'new-commission'
        assert client.calls[-1] == ('start', DEVELOPER, {'request_id': 'fixture'})
        assert request(server, 'POST', post + '/c/cancel', {})[0] == 200
        assert client.calls[-1] == ('action', DEVELOPER, 'c', 'cancel', {})
        media = PREFIX + '/api/method-lab/commissions/c/candidates/v/media'
        status, raw, headers = request(server, 'GET', media, extra={'Range': 'bytes=2-5'})
        assert status == 206 and raw == b'xtur'
        assert headers['Content-Range'] == 'bytes 2-5/19'
        status, raw, headers = request(server, 'GET', media.removesuffix('media') + 'source')
        assert status == 200 and json.loads(raw)['fixture'] is True
        assert 'attachment;' in headers['Content-Disposition']
        assert not (tmp_path / 'unused').exists()


def test_methods_only_closes_old_routes_without_initializing_their_stores(tmp_path):
    client = MethodClient()
    lab = AnimationLab(tmp_path / 'unused', {}, method_client=client, methods_only=True)
    with serving(lab) as server:
        for path in ('/api/catalog', '/api/experiments', '/api/creation-runs', '/api/annotations',
                     '/api/requirements', '/api/iterations', '/api/export', '/api/evaluations/old',
                     '/app.mjs', '/model.mjs', '/style.css', '/lab-player.mjs', '/experiments.mjs',
                     '/assets/hash/gsap.min.js', '/preview/old/candidate/', '/composition/old/candidate/clip/index.html'):
            assert request(server, 'GET', PREFIX + path)[0] == 404, path
        for path in ('/api/annotations', '/api/iterations', '/api/creation-runs', '/api/experiments/old/feedback'):
            assert request(server, 'POST', PREFIX + path, {})[0] == 404, path
        assert request(server, 'GET', PREFIX + '/api/catalog', omit=['X-Forwarded-User'])[0] == 403
        assert client.calls == []
    assert not (tmp_path / 'unused').exists()


@pytest.mark.parametrize('page', ['', 'knowledge.html'])
def test_methods_only_serves_the_complete_page_module_import_graph(tmp_path, page):
    client = MethodClient()
    lab = AnimationLab(tmp_path / 'unused', {}, method_client=client, methods_only=True)
    with serving(lab) as server:
        status, html, _ = request(server, 'GET', PREFIX + '/' + page)
        assert status == 200
        entrypoints = re.findall(rb'<script\b[^>]*\btype="module"[^>]*\bsrc="([^"]+)"', html)
        assert entrypoints
        pending = [urljoin(PREFIX + '/' + page, entry.decode()) for entry in entrypoints]
        visited = set()
        while pending:
            path = pending.pop()
            if path in visited:
                continue
            visited.add(path)
            assert path.startswith(PREFIX + '/') and path.endswith('.mjs')
            status, raw, headers = request(server, 'GET', path)
            assert status == 200, path
            assert headers['Content-Type'].startswith(('text/javascript', 'application/javascript')), path
            assert request(server, 'GET', path, omit=['X-Forwarded-User'])[0] == 403
            imports = re.findall(rb"(?m)^\s*(?:import|export)\b[^;]*?\bfrom\s*['\"](\./[^'\"]+)['\"]", raw)
            pending.extend(urljoin(path, name.decode()) for name in imports)
        assert not client.calls  # Loading the UI must not submit work or query another owner.


class CLIProbeServer:
    seen = []
    def __init__(self, lab, *args, **kwargs):
        self.seen.append(lab)
        self.server_port, self.base_path = 0, ''
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def serve_forever(self, **kwargs):
        raise KeyboardInterrupt


def test_cli_auto_mode_with_executor_skips_legacy_assets_and_old_store(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(module, 'LabHTTPServer', CLIProbeServer)
    monkeypatch.setattr(module, 'load_assets', forbid_legacy)
    monkeypatch.setattr(module, 'catalog', forbid_legacy)
    token = tmp_path / 'executor-token'
    token.write_text('private-fixture-token-' + 'x' * 32, encoding='ascii')
    data = tmp_path / 'unused'
    module.main(['--data-dir', str(data), '--creation-url', 'http://private-executor',
        '--creation-token-file', str(token), '--evaluations-dir', str(tmp_path / 'missing')])
    lab = CLIProbeServer.seen[-1]
    assert lab.methods_only and lab.method_client is not None and lab.creation_client is None
    assert not data.exists()
    output = capsys.readouterr().out
    assert 'Methods-only proxy' in output and 'private-fixture-token' not in output


def test_cli_legacy_mode_remains_explicit_and_requires_assets(tmp_path, monkeypatch):
    monkeypatch.setattr(module, 'LabHTTPServer', CLIProbeServer)
    loaded = []
    monkeypatch.setattr(module, 'load_assets', lambda path: loaded.append(path) or {})
    seen = []
    monkeypatch.setattr(module, 'AnimationLab', lambda *args, **kwargs: seen.append(kwargs) or kwargs)
    module.main(['--data-dir', str(tmp_path / 'legacy'), '--assets-dir', str(tmp_path / 'assets'), '--mode', 'legacy'])
    assert loaded == [tmp_path / 'assets'] and seen[-1]['methods_only'] is False
    with pytest.raises(LabError, match='animation_lab_assets_required'):
        module.main(['--data-dir', str(tmp_path / 'legacy'), '--mode', 'legacy'])
    with pytest.raises(LabError, match='animation_method_configuration_required'):
        module.main(['--data-dir', str(tmp_path / 'missing'), '--mode', 'methods'])
