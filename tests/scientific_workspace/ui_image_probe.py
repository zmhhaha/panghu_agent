"""Exercise the shipped UI/gateway as its non-root user without external network."""
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

import uvicorn
from scientific_agent.workbench_server import app


def main():
    assert os.getuid() == 10000
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=17860,
        proxy_headers=False, access_log=False, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(.05)
    assert server.started
    def request(path, *, method='GET', headers=None, payload=None):
        value = urllib.request.Request('http://127.0.0.1:17860' + path,
            data=payload, headers=headers or {}, method=method)
        try:
            response = urllib.request.urlopen(value, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, {key.lower(): value for key, value in response.headers.items()}, response.read()
    try:
        status, headers, raw = request('/healthz')
        assert status == 200 and json.loads(raw)['frontend_ready'] is True
        status, headers, raw = request('/')
        assert status == 200 and b'<html' in raw and b'lang="zh-CN"' in raw
        assert "frame-ancestors 'none'" in headers['content-security-policy']
        scripts = re.findall(rb'src="(/assets/[^\"]+\.js)"', raw)
        assert scripts
        assets = {}
        for path in scripts:
            status, _, content = request(path.decode('ascii'))
            assert status == 200 and len(content) > 1000
            assets[path.decode('ascii')] = hashlib.sha256(content).hexdigest()
        assert request('/api/v1/research/projects')[0] == 401
        assert request('/scientific-reports')[0] == 401
        status, headers, raw = request('/api/v1/research/me', headers={'X-Forwarded-User': 'isolated-image-probe'})
        assert status == 200 and json.loads(raw)['user_id'] == 'isolated-image-probe'
        cookie = headers['set-cookie']
        assert cookie.startswith('__Host-research_csrf=') and 'Secure' in cookie and 'HttpOnly' in cookie
        assert 'Domain=' not in cookie
        write_headers = {'X-Forwarded-User': 'isolated-image-probe',
            'Origin': 'https://other.invalid', 'Cookie': cookie.split(';')[0],
            'X-CSRF-Token': json.loads(raw)['csrf_token'], 'Content-Type': 'application/json'}
        assert request('/api/v1/research/projects', method='POST', headers=write_headers, payload=b'{}')[0] == 403
        assert request('/api/unknown')[0] == 404
        print(json.dumps({'status': 'passed', 'uid': os.getuid(), 'static_assets': assets,
            'anonymous_data_denied': True, 'csrf_rejected_before_upstream': True,
            'secure_host_cookie': True, 'real_model_executed': False,
            'scope': 'actual image static/gateway startup; no SSO or business/model acceptance'}))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    assert not thread.is_alive()


if __name__ == '__main__':
    main()
