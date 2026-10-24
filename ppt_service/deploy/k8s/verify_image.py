"""Exercise installed image and exact bundled assets; only synthetic JWT/SQLite."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
import jwt
from sqlalchemy import create_engine, select, func
from sqlalchemy.pool import StaticPool
from ppt_service.server.auth import Settings
from ppt_service.server.migrate import migrate
from ppt_service.server.models import Job
from ppt_service.server.site import create_site
import ppt_service.server.site as site_module


def main():
    root = Path('/opt/ppt-service')
    assert str(site_module.__file__) == str(root / 'ppt_service/server/site.py')
    assert 'PPT_LOCAL_TEST_ONLY' not in os.environ
    assert os.environ['PPT_EXECUTION_ENABLED'] == 'false'
    assert os.getuid() == 10000 and os.getgid() == 10000
    assert not (root / 'ppt_service/control').exists()
    inputs = json.loads((root / 'build-info/site-inputs.json').read_text())
    for key, expected in inputs.items():
        path = root / key.removeprefix('code/')
        data = path.read_bytes()
        assert len(data) == expected['bytes']
        assert hashlib.sha256(data).hexdigest() == expected['sha256']
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    migrate(engine)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode({'iss': 'https://p1.test', 'sub': 'p1-owner', 'aud': 'p1',
                        'email': 'p1-owner@ppt.test',
                        'iat': datetime.now(timezone.utc), 'exp': datetime.now(timezone.utc) + timedelta(minutes=5)}, key, algorithm='RS256')
    with tempfile.TemporaryDirectory() as scratch:
        emails = Path(scratch) / 'emails'
        emails.write_text('p1-owner@ppt.test\n', encoding='utf-8')
        settings = Settings('sqlite://', scratch, oidc_issuer='https://p1.test', oidc_audience='p1',
                            trusted_proxy_cidrs=('127.0.0.1/32',), allowed_origins=('https://ppt.test',),
                            invitation_emails_file=str(emails))
        site = create_site(settings, engine, str(root / 'web/dist'))
        site.state.jwks = SimpleNamespace(get_signing_key_from_jwt=lambda unused: SimpleNamespace(key=key.public_key()))
        client = TestClient(site, base_url='https://ppt.test', client=('127.0.0.1', 1234))
        assert client.get('/').status_code == 401
        headers = {'Authorization': 'Bearer ' + token}
        asset_count = 0
        for name in inputs:
            if not name.startswith('web/dist/'):
                continue
            path = '/' if name.endswith('/index.html') else '/' + name.removeprefix('web/dist/')
            response = client.get(path, headers=headers)
            assert response.status_code == 200 and response.content == (root / name).read_bytes()
            assert client.get(path).status_code == 401
            asset_count += 1
        me = client.get('/api/v1/me', headers=headers)
        assert me.status_code == 200
        headers |= {'Origin': 'https://ppt.test', 'X-CSRF-Token': me.json()['csrf_token']}
        assert client.post('/api/v1/projects', headers=headers, json={'title': 'P1 image-only fixture'}).status_code == 201
        assert client.post('/api/v1/projects/synthetic/plans', headers=headers, json={}).status_code == 503
        with site.state.sessions() as session:
            assert session.scalar(select(func.count(Job.id))) == 0
        assert client.get('/healthz').json() == {'status': 'ok'}
        assert client.get('/oauth2/callback').status_code == 404
        assert client.get('/assets/private.js.map', headers=headers).status_code == 404
    engine.dispose()
    print(json.dumps({'installed_image': True, 'runtime_inputs_verified': len(inputs),
                      'bundled_http_assets': asset_count, 'real_signed_jwt': True,
                      'execution_disabled': True, 'model_requests': 0, 'production_ready': False}))


if __name__ == '__main__':
    main()
