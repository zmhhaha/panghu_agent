"""The integration guide shares the laboratory's existing identity boundary."""
from contextlib import contextmanager
import http.client
import json
import threading
from types import SimpleNamespace

import pytest

from video_workbench.animation_lab import LabHTTPServer, WEB
from video_workbench.deploy.build_animation_lab import SOURCE_FILES


@contextmanager
def api_server():
    lab = SimpleNamespace(methods_only=True, csrf_token='route-test-csrf', method_client=None)
    server = LabHTTPServer(lab, 0, proxy_mode=True,
        public_origin='https://video.example.test', base_path='/dev/animations',
        developer_users=['developer-a'])
    thread = threading.Thread(target=server.serve_forever,
        kwargs={'poll_interval': .02}, daemon=True)
    thread.start()

    def get(path, *, user='developer-a', extra=None):
        headers = {'Host': 'video.example.test'}
        if user is not None:
            headers['X-Forwarded-User'] = user
        headers.update(extra or {})
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        try:
            connection.request('GET', path, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    try:
        yield get
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(('route', 'relative', 'mime'), [
    ('agent-api.html', 'animation_lab_web/agent-api.html', 'text/html'),
    ('agent-api.openapi.json', 'animation_lab_web/agent-api.openapi.json', 'application/json'),
    ('agent-api-client.py', 'animation_lab_client.py', 'text/x-python'),
    ('prompt-inspection.mjs', 'animation_lab_web/prompt-inspection.mjs', 'text/javascript'),
])
def test_agent_integration_files_use_existing_guard_and_exact_build_inputs(route, relative, mime):
    with api_server() as get:
        path = '/dev/animations/' + route
        status, headers, raw = get(path)
        assert status == 200
        assert headers['Content-Type'].startswith(mime)
        assert headers['Cache-Control'] == 'no-store'
        assert raw == (WEB.parent / relative).read_bytes()
        assert 'video_workbench/' + relative in SOURCE_FILES
        if route.endswith('.py'):
            assert headers['Content-Disposition'] == 'attachment; filename="animation_lab_client.py"'
        for user in (None, 'ordinary-user'):
            status, _, raw = get(path, user=user)
            assert status == 403
            assert json.loads(raw)['error'] == 'animation_lab_developer_required'
        assert get(path, extra={'Origin': 'https://other.example.test'})[0] == 403
        assert get('/dev/animations/../animation_lab_client.py')[0] == 404


def test_openapi_describes_existing_session_and_async_submission_without_service_secrets():
    document = json.loads((WEB / 'agent-api.openapi.json').read_text(encoding='utf-8'))
    assert document['openapi'].startswith('3.1.')
    assert document['servers'][0]['url'].endswith('/dev/animations/api/method-lab')
    assert '/catalog' in document['paths']
    assert '/commissions' in document['paths']
    assert '/commissions/{commission_id}' in document['paths']
    assert '202' in document['paths']['/commissions']['post']['responses']
    schemes = document['components']['securitySchemes'].values()
    assert any(scheme.get('in') == 'cookie' and scheme.get('name') == '__Host-video_workbench'
               for scheme in schemes)
    assert not any(scheme.get('scheme') == 'bearer' for scheme in schemes)
    assert 'X-Animation-Creation-Owner' not in json.dumps(document)
