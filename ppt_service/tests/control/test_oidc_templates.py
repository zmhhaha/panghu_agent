"""Check the shared OAuth contract used by the real-auth PPT deployment."""
from pathlib import Path
import tomllib
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMMON = ROOT.parents[1]


class OidcTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        objects = list(yaml.safe_load_all((COMMON / 'oauth/k8s/ppt-proxy-configmap.yaml').read_text(encoding='utf-8')))
        objects.extend(yaml.safe_load_all((COMMON / 'oauth/k8s/ppt-owner-configmap.yaml').read_text(encoding='utf-8')))
        cls.namespaces = {obj['metadata']['namespace'] for obj in objects}
        cls.maps = {obj['metadata']['name']: obj['data'] for obj in objects}
        cls.proxy = tomllib.loads(cls.maps['ppt-oidc']['oauth2-proxy.cfg'])
        cls.auth = cls.maps['ppt-auth']

    def test_cookie_is_secure_host_only_without_refresh(self):
        self.assertEqual(self.proxy['cookie_name'], '__Host-ppt')
        self.assertEqual(self.proxy['cookie_path'], '/')
        self.assertEqual(self.proxy['cookie_samesite'], 'lax')
        for key in ('cookie_secure', 'cookie_httponly', 'cookie_csrf_per_request'):
            self.assertIs(self.proxy[key], True)
        for key in ('cookie_domains', 'cookie_refresh'):
            self.assertNotIn(key, self.proxy)

    def test_forwarded_id_token_and_api_401_contract(self):
        self.assertIs(self.proxy['pass_authorization_header'], True)
        self.assertIs(self.proxy['pass_access_token'], False)
        self.assertEqual(self.proxy['upstreams'], ['http://127.0.0.1:8000/'])
        self.assertEqual(self.proxy['api_routes'], ['^/api/'])
        self.assertNotIn('skip_auth_routes', self.proxy)
        self.assertNotIn('email_domains', self.proxy)

    def test_dedicated_client_and_independent_owner_list(self):
        self.assertEqual(self.namespaces, {'ppt-service'})
        self.assertEqual(self.proxy['client_id'], 'REQUIRED_DEDICATED_PPT_CLIENT_ID')
        self.assertEqual(self.proxy['client_id'], self.auth['OIDC_AUDIENCE'])
        self.assertNotIn('INVITATION_SUBJECTS', self.auth)
        self.assertNotIn('allowed-emails', self.maps['ppt-oidc'])
        self.assertEqual(set(self.maps['ppt-owner']), {'emails'})
        self.assertIsInstance(self.maps['ppt-owner']['emails'], str)
        self.assertEqual(self.auth['ALLOWED_ORIGINS'], 'https://ppt.panghuer.top')
        self.assertEqual(self.proxy['redirect_url'], self.auth['ALLOWED_ORIGINS'] + '/oauth2/callback')
        self.assertEqual(self.proxy['authenticated_emails_file'], '/owner/emails')
        self.assertEqual(self.auth['INVITATION_EMAILS_FILE'], self.proxy['authenticated_emails_file'])

    def test_public_settings_have_no_credentials_or_validation_bypass(self):
        self.assertEqual(self.auth['OIDC_ISSUER'], 'https://auth.panghuer.top')
        self.assertEqual(self.auth['OIDC_JWKS_URL'], 'https://auth.panghuer.top/.well-known/jwks')
        self.assertEqual(self.proxy['oidc_issuer_url'], self.auth['OIDC_ISSUER'])
        for key in ('client_secret', 'cookie_secret', 'ssl_insecure_skip_verify',
                    'insecure_oidc_skip_issuer_verification', 'insecure_oidc_allow_unverified_email'):
            self.assertNotIn(key, self.proxy)
        for key in ('DATABASE_URL', 'PPT_LOCAL_TEST_ONLY'):
            self.assertNotIn(key, self.auth)
        self.assertIs(self.proxy['request_logging'], False)

    def test_legacy_config_copies_are_removed(self):
        for name in ('oauth2-proxy.cfg.example', 'api.env.example'):
            self.assertFalse((ROOT / 'deploy/oidc' / name).exists())
        base = list(yaml.safe_load_all((ROOT / 'deploy/k8s/base/config.yaml').read_text(encoding='utf-8')))
        self.assertEqual([obj['metadata']['name'] for obj in base], ['ppt-site'])
        self.assertTrue(set(base[0]['data']).isdisjoint(self.auth))


if __name__ == '__main__':
    unittest.main()
