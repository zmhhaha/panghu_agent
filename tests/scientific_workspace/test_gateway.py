"""Verify the public boundary without an identity-provider or model call."""
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from scientific_agent.workbench_server import create_app, COOKIE


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        Path(self.root.name, 'index.html').write_text('<html>Workbench</html>')
        self.calls = []

        def transport(*args):
            self.calls.append(args)
            return 200, {'Content-Type': 'application/json'}, b'{"projects":[]}'

        self.app = create_app(static_root=self.root.name, public_origin='https://research.test', transport=transport)
        self.client = TestClient(self.app, base_url='https://research.test')
        self.identity = {'X-Forwarded-User': 'alice'}

    def tearDown(self):
        self.client.close()
        self.root.cleanup()

    def mutation(self, **changes):
        token = self.client.get('/api/v1/research/me', headers=self.identity).json()['csrf_token']
        headers = dict(self.identity, Origin='https://research.test', **{'X-CSRF-Token': token})
        headers.update(changes)
        return self.client.post('/api/v1/research/projects', headers=headers, json={'requirements': {'question': 'bounded'}})

    def test_no_anonymous_data_or_writes(self):
        self.assertEqual(self.client.get('/api/v1/research/projects').status_code, 401)
        self.assertEqual(self.client.get('/api/v1/research/me').status_code, 401)
        self.assertEqual(self.client.post('/api/v1/research/projects', headers=self.identity, json={}).status_code, 403)
        self.assertEqual(self.calls, [])

    def test_csrf_origin_and_header_both_required(self):
        self.assertEqual(self.mutation(Origin='https://other.test').status_code, 403)
        self.assertEqual(self.mutation(**{'X-CSRF-Token': 'wrong'}).status_code, 403)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.mutation().status_code, 200)

    def test_secure_cookie_and_minimal_forwarding(self):
        response = self.client.get('/api/v1/research/me', headers=self.identity)
        cookie = response.headers['set-cookie']
        self.assertIn(COOKIE, cookie)
        self.assertIn('Secure', cookie)
        self.assertIn('HttpOnly', cookie)
        self.assertNotIn('Domain=', cookie)
        self.assertEqual(self.mutation(**{'Authorization': 'secret', 'X-Auth-Request-User': 'mallory',
            'X-Forwarded-For': '127.0.0.1', 'Idempotency-Key': 'operation-one'}).status_code, 200)
        forwarded = self.calls[-1][3]
        self.assertEqual(forwarded['X-Forwarded-User'], 'alice')
        self.assertEqual(forwarded['Idempotency-Key'], 'operation-one')
        self.assertNotIn('Authorization', forwarded)
        self.assertNotIn('Cookie', forwarded)
        self.assertNotIn('X-Forwarded-For', forwarded)
        self.assertNotIn('X-Auth-Request-User', forwarded)

    def test_legacy_is_readonly_and_unknown_apis_do_not_become_html(self):
        self.assertEqual(self.client.get('/scientific-reports', headers=self.identity).status_code, 200)
        self.assertEqual(self.client.post('/scientific-reports', headers=self.identity).status_code, 405)
        self.assertEqual(self.client.get('/api/not-real', headers=self.identity).status_code, 404)
        self.assertEqual(self.client.get('/assets/missing.js').status_code, 404)
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_upload_limit_and_security_headers(self):
        response = self.client.post('/api/v1/research/projects', headers=dict(self.identity,
            Origin='https://research.test', **{'X-CSRF-Token': 'x' * 43, 'Content-Length': str(13 * 1024 * 1024)}))
        # CSRF is checked before reading an oversized unauthenticated body.
        self.assertEqual(response.status_code, 403)
        self.assertIn("frame-ancestors 'none'", response.headers['content-security-policy'])
        self.assertEqual(response.headers['cache-control'], 'no-store')


if __name__ == '__main__':
    unittest.main()
