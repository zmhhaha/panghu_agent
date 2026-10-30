"""Configuration security/operability contracts, without contacting a cluster.

Requires PyYAML, already available in the development workspace. These are
declarative gates; they do not claim actual CNI, CSI, OIDC or image acceptance.
"""
from __future__ import annotations

import ipaddress
from pathlib import Path
import re
import tomllib
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
COMMON = ROOT.parents[3]


class UniqueLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f'duplicate configuration key: {key}')
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def load_target(target):
    directory = ROOT / target
    config = yaml.load((directory / 'kustomization.yaml').read_text(encoding='utf-8'), Loader=UniqueLoader)
    documents = []
    for name in config['resources']:
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file():
            raise ValueError('target resources must be plain local files')
        documents.extend(d for d in yaml.load_all(path.read_text(encoding='utf-8'), Loader=UniqueLoader) if d)
    return config, documents


def pod_spec(document):
    return document['spec']['template']['spec']


def env_key(container, name):
    return next(item for item in container.get('env', []) if item['name'] == name)['valueFrom']['secretKeyRef']


class ManifestContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base_config, cls.base = load_target('base')
        cls.migration_config, cls.migration = load_target('migrate-job')
        cls.shared = list(yaml.load_all((COMMON / 'oauth/k8s/ppt-proxy-configmap.yaml').read_text(encoding='utf-8'), Loader=UniqueLoader))
        cls.all = cls.base + cls.migration + cls.shared
        cls.index = {(d['kind'], d['metadata']['name']): d for d in cls.all}
        cls.site = cls.index['Deployment', 'ppt-site']
        cls.job = cls.index['Job', 'ppt-migrate-schema4']
        cls.site_container, cls.proxy = pod_spec(cls.site)['containers']
        cls.migrator = pod_spec(cls.job)['containers'][0]
        cls.config = cls.index['ConfigMap', 'ppt-site']['data']
        cls.auth = cls.index['ConfigMap', 'ppt-auth']['data']
        cls.oauth_data = cls.index['ConfigMap', 'ppt-oidc']['data']
        cls.oauth = tomllib.loads(cls.oauth_data['oauth2-proxy.cfg'])
        cls.policies = {d['metadata']['name']: d['spec'] for d in cls.base if d['kind'] == 'NetworkPolicy'}

    def test_targets_are_isolated_and_migration_is_separate(self):
        self.assertEqual(self.base_config['namespace'], 'ppt-service')
        self.assertEqual(self.migration_config['namespace'], 'ppt-service')
        self.assertEqual(len(self.index), len(self.all), 'duplicate Kubernetes object identity')
        self.assertNotIn('Job', {d['kind'] for d in self.base})
        self.assertEqual([d['kind'] for d in self.migration], ['Job'])
        self.assertEqual(self.index['Namespace', 'ppt-service']['metadata']['labels']['pod-security.kubernetes.io/enforce'], 'restricted')

    def test_deployment_starts_only_the_site_with_execution_disabled(self):
        self.assertEqual(self.site['spec']['replicas'], 1)
        self.assertNotIn('suspend', self.job['spec'])
        self.assertNotIn('NOT-DEPLOY-READY', yaml.safe_dump(self.all))
        self.assertEqual(self.config['PPT_EXECUTION_ENABLED'], 'false')
        self.assertNotIn('PPT_LOCAL_TEST_ONLY', self.config)
        self.assertNotIn('INVITATION_SUBJECTS', self.auth)
        self.assertNotIn('allowed-emails', self.oauth_data)
        self.assertNotIn(('ConfigMap', 'ppt-owner'), self.index)
        self.assertNotIn('INVITATION_EMAILS_FILE', self.auth)
        self.assertIn('REQUIRED_', self.auth['OIDC_AUDIENCE'])
        self.assertEqual(self.auth['ALLOWED_ORIGINS'], 'https://ppt-service.panghuer.top')
        text = yaml.safe_dump(self.all).lower()
        self.assertNotIn('control.dev_site', text)
        self.assertNotIn('llm-service', text)
        self.assertNotIn('openai_api_key', text)

    def test_retired_standalone_database_is_not_recreated(self):
        self.assertNotIn('postgres.yaml', self.base_config['resources'])
        self.assertNotIn('StatefulSet', {d['kind'] for d in self.base})
        for kind, name in (
                ('Service', 'ppt-postgres'),
                ('ServiceAccount', 'ppt-postgres'),
                ('PersistentVolumeClaim', 'ppt-postgres-data'),
                ('NetworkPolicy', 'postgres-ingress')):
            self.assertNotIn((kind, name), self.index)
        self.assertNotIn('ppt-postgres-bootstrap', yaml.safe_dump(self.all))

    def test_site_port_is_loopback_and_not_exposed_by_service(self):
        args = self.site_container['args']
        self.assertEqual(args[args.index('--host') + 1], '127.0.0.1')
        self.assertEqual(args[args.index('--port') + 1], '8000')
        self.assertIn('--no-proxy-headers', args)
        self.assertIn('ppt_service.server.site:create_site', args)
        self.assertNotIn('ports', self.site_container)
        service = self.index['Service', 'ppt-site']['spec']
        self.assertEqual(service['type'], 'ClusterIP')
        self.assertEqual([(p['port'], p['targetPort']) for p in service['ports']], [(4180, 'oauth')])
        self.assertEqual(self.oauth['upstreams'], ['http://127.0.0.1:8000/'])
        self.assertEqual(self.config['TRUSTED_PROXY_CIDRS'], '127.0.0.1/32,::1/128')

    def test_proxy_and_site_admit_authenticated_users_without_membership_files(self):
        self.assertEqual(self.oauth['email_domains'], ['*'])
        self.assertNotIn('authenticated_emails_file', self.oauth)
        self.assertNotIn('INVITATION_EMAILS_FILE', self.auth)
        self.assertNotIn('skip_auth_routes', self.oauth)
        self.assertNotIn('owner', {volume['name'] for volume in pod_spec(self.site)['volumes']})
        for container in (self.site_container, self.proxy):
            self.assertFalse(any(mount['name'] == 'owner' or mount['mountPath'] == '/owner'
                                 for mount in container['volumeMounts']))

    def test_packaged_runtime_paths_are_explicit(self):
        self.assertEqual(self.site_container['command'], ['/usr/local/bin/python'])
        self.assertEqual(self.site_container['workingDir'], '/opt/ppt-service')
        self.assertEqual(self.config['PPT_WEB_DIST'], '/opt/ppt-service/web/dist')
        self.assertEqual(self.config['DATA_ROOT'], '/var/lib/ppt-service/state')

    def test_migration_is_bounded_and_uses_own_credential(self):
        self.assertEqual(self.job['spec']['backoffLimit'], 0)
        self.assertLessEqual(self.job['spec']['activeDeadlineSeconds'], 300)
        self.assertEqual(pod_spec(self.job)['restartPolicy'], 'Never')
        self.assertEqual(self.migrator['command'], ['/usr/local/bin/python', '-m', 'ppt_service.server.migrate'])
        self.assertEqual(env_key(self.migrator, 'DATABASE_URL'), {'name': 'ppt-database-migrator', 'key': 'DATABASE_URL'})
        self.assertEqual(env_key(self.site_container, 'DATABASE_URL'), {'name': 'ppt-database-runtime', 'key': 'DATABASE_URL'})
        self.assertNotIn('initContainers', pod_spec(self.site))
        for volume in pod_spec(self.job)['volumes']:
            self.assertNotIn('persistentVolumeClaim', volume)

    def test_secrets_are_references_and_separated_by_consumer(self):
        self.assertNotIn('Secret', {d['kind'] for d in self.all})
        self.assertEqual(env_key(self.proxy, 'OAUTH2_PROXY_CLIENT_SECRET'), {'name': 'ppt-oidc', 'key': 'client-secret'})
        self.assertEqual(env_key(self.proxy, 'OAUTH2_PROXY_COOKIE_SECRET'), {'name': 'ppt-oidc', 'key': 'cookie-secret'})
        self.assertNotIn('DATABASE_URL', self.config)
        self.assertNotIn('database', {m['name'] for m in self.proxy['volumeMounts']})
        self.assertNotIn('data', {m['name'] for m in self.proxy['volumeMounts']})
        self.assertNotIn('envFrom', self.proxy)

    def test_no_kubernetes_api_privilege_or_serviceaccount_token(self):
        forbidden = {'Role', 'RoleBinding', 'ClusterRole', 'ClusterRoleBinding'}
        self.assertFalse(forbidden & {d['kind'] for d in self.all})
        for document in self.all:
            if document['kind'] == 'ServiceAccount':
                self.assertIs(document['automountServiceAccountToken'], False)
        for document in (self.site, self.job):
            spec = pod_spec(document)
            self.assertIs(spec['automountServiceAccountToken'], False)
            self.assertIn(('ServiceAccount', spec['serviceAccountName']), self.index)

    def test_security_context_and_resource_bounds(self):
        for document in (self.site, self.job):
            spec = pod_spec(document)
            security = spec['securityContext']
            self.assertEqual(spec['nodeSelector'], {'kubernetes.io/arch': 'arm64'})
            self.assertIs(security['runAsNonRoot'], True)
            self.assertGreater(security['runAsUser'], 0)
            self.assertEqual(security['seccompProfile']['type'], 'RuntimeDefault')
            for field in ('hostNetwork', 'hostPID', 'hostIPC', 'shareProcessNamespace'):
                self.assertFalse(spec.get(field, False))
            self.assertNotIn('initContainers', spec)
            for container in spec['containers']:
                with self.subTest(container=container['name']):
                    context = container['securityContext']
                    self.assertIs(context['allowPrivilegeEscalation'], False)
                    self.assertIs(context['readOnlyRootFilesystem'], True)
                    self.assertEqual(context['capabilities']['drop'], ['ALL'])
                    self.assertFalse(context.get('privileged', False))
                    for bound in ('requests', 'limits'):
                        self.assertEqual(set(container['resources'][bound]), {'cpu', 'memory'})
            for volume in spec['volumes']:
                self.assertNotIn('hostPath', volume)
                if 'emptyDir' in volume:
                    self.assertIn('sizeLimit', volume['emptyDir'])

    def test_site_mount_cannot_rewrite_frozen_data_permissions(self):
        security = pod_spec(self.site)['securityContext']
        self.assertEqual((security['runAsUser'], security['runAsGroup']), (10000, 10000))
        self.assertNotIn('fsGroup', security)
        self.assertNotIn('fsGroupChangePolicy', security)
        self.assertNotIn('supplementalGroups', security)
        mount = next(mount for mount in self.site_container['volumeMounts'] if mount['name'] == 'data')
        self.assertEqual(mount['mountPath'], '/var/lib/ppt-service')
        self.assertEqual(self.config['DATA_ROOT'], mount['mountPath'] + '/state')

    def test_images_are_manifest_pins_or_explicit_unresolved_placeholders(self):
        for document in (self.site, self.job):
            for container in pod_spec(document)['containers']:
                image = container['image']
                self.assertTrue(re.fullmatch(r'REQUIRED_[A-Z0-9_]+:unresolved', image) or re.fullmatch(r'[^\s@]+@sha256:[0-9a-f]{64}', image), image)
        self.assertEqual(self.site_container['image'], self.migrator['image'])

    def test_probes_do_not_bypass_auth_or_confuse_liveness_with_schema(self):
        startup = self.site_container['startupProbe']['exec']['command'][-1]
        live = self.site_container['livenessProbe']['exec']['command'][-1]
        ready = self.site_container['readinessProbe']['exec']['command'][-1]
        self.assertIn('/healthz', startup)
        self.assertIn('/healthz', live)
        self.assertNotIn('api_schema_revision', live)
        self.assertIn('api_schema_revision', ready)
        self.assertIn('from ppt_service.server.migrate import REVISION', ready)
        self.assertIn('== REVISION', ready)
        self.assertIn("os.environ['DATABASE_URL']", ready)
        self.assertNotIn('Authorization', ready)
        self.assertEqual(self.proxy['readinessProbe']['httpGet']['path'], '/ready')
        self.assertEqual(self.proxy['livenessProbe']['httpGet']['path'], '/ping')

    def test_only_the_existing_ppt_data_claim_is_managed(self):
        data = self.index['PersistentVolumeClaim', 'ppt-data']['spec']
        self.assertEqual((data['storageClassName'], data['accessModes']), ('ceph-cephfs', ['ReadWriteMany']))
        self.assertEqual(data['resources']['requests']['storage'], '20Gi')
        self.assertEqual([d['metadata']['name'] for d in self.base if d['kind'] == 'PersistentVolumeClaim'], ['ppt-data'])
        for document in (self.site, self.job):
            for volume in pod_spec(document)['volumes']:
                if 'persistentVolumeClaim' in volume:
                    self.assertIn(('PersistentVolumeClaim', volume['persistentVolumeClaim']['claimName']), self.index)

    def test_proxy_security_contract_preserves_token_and_cookie_requirements(self):
        self.assertIs(self.oauth['pass_authorization_header'], True)
        self.assertIs(self.oauth['pass_access_token'], False)
        self.assertEqual(self.oauth['api_routes'], ['^/api/'])
        self.assertEqual(self.oauth['client_id'], self.auth['OIDC_AUDIENCE'])
        self.assertEqual(self.oauth['redirect_url'], self.auth['ALLOWED_ORIGINS'] + '/oauth2/callback')
        self.assertEqual(self.oauth['cookie_name'], '__Host-ppt')
        self.assertEqual(self.oauth['cookie_path'], '/')
        for key in ('cookie_secure', 'cookie_httponly', 'cookie_csrf_per_request'):
            self.assertIs(self.oauth[key], True)
        for key in ('cookie_domains', 'cookie_refresh', 'ssl_insecure_skip_verify', 'insecure_oidc_skip_issuer_verification'):
            self.assertNotIn(key, self.oauth)

    def test_shared_configuration_is_referenced_without_a_second_copy(self):
        self.assertEqual(self.site_container['envFrom'], [
            {'configMapRef': {'name': 'ppt-site'}},
            {'configMapRef': {'name': 'ppt-auth'}},
        ])
        self.assertTrue(set(self.config).isdisjoint(self.auth))
        self.assertNotIn('ppt-oidc', {d['metadata']['name'] for d in self.base if d['kind'] == 'ConfigMap'})
        for document in self.shared:
            self.assertEqual(document['metadata']['namespace'], 'ppt-service')

    def test_oidc_egress_only_allows_the_internal_tls_controller(self):
        document = yaml.load((COMMON / 'oauth/k8s/ppt-oidc-egress.yaml').read_text(encoding='utf-8'), Loader=UniqueLoader)
        self.assertEqual(document['metadata']['namespace'], 'ppt-service')
        spec = document['spec']
        self.assertEqual(spec['podSelector'], {'matchLabels': {'app.kubernetes.io/name': 'ppt-site'}})
        self.assertEqual(spec['policyTypes'], ['Egress'])
        self.assertEqual(spec['egress'], [{'to': [{
            'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'ingress-nginx'}},
            'podSelector': {'matchLabels': {
                'app.kubernetes.io/name': 'ingress-nginx',
                'app.kubernetes.io/instance': 'ingress-nginx',
                'app.kubernetes.io/component': 'controller',
            }},
        }], 'ports': [{'protocol': 'TCP', 'port': 443}]}])

    def test_default_deny_and_no_public_exposure(self):
        default = self.policies['default-deny']
        self.assertEqual(default['podSelector'], {})
        self.assertEqual(set(default['policyTypes']), {'Ingress', 'Egress'})
        self.assertFalse(default.get('ingress', []))
        self.assertFalse(default.get('egress', []))
        for document in self.base:
            self.assertNotIn(document['kind'], {'Ingress', 'Gateway', 'HTTPRoute'})
            if document['kind'] == 'Service':
                self.assertEqual(document['spec']['type'], 'ClusterIP')
                self.assertNotIn('externalIPs', document['spec'])
                for port in document['spec']['ports']:
                    self.assertNotIn('nodePort', port)

    def test_dns_requires_namespace_and_pod_match(self):
        dns = self.policies['dns-egress']
        self.assertEqual(dns['podSelector'], {'matchExpressions': [{
            'key': 'app.kubernetes.io/name', 'operator': 'In',
            'values': ['ppt-site', 'ppt-migrator', 'ppt-hermes-controller'],
        }]})
        rule = dns['egress'][0]
        self.assertEqual(rule['to'], [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'kube-system'}}, 'podSelector': {'matchLabels': {'k8s-app': 'kube-dns'}}}])
        self.assertEqual({(p['protocol'], p['port']) for p in rule['ports']}, {('UDP', 53), ('TCP', 53)})

    def test_only_exact_frpc_peers_reach_website(self):
        policy = self.policies['frpc-ingress']
        self.assertEqual(policy['podSelector']['matchLabels'], {'app.kubernetes.io/name': 'ppt-site'})
        rule = policy['ingress'][0]
        self.assertEqual(rule['from'], [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'frpc'}}, 'podSelector': {'matchLabels': {'app': 'frpc'}}}])
        self.assertEqual(rule['ports'], [{'protocol': 'TCP', 'port': 4180}])

    def test_database_egress_requires_shared_namespace_and_postgres_pod(self):
        for name, source in (('site-postgres-egress', 'ppt-site'), ('migrator-postgres-egress', 'ppt-migrator')):
            policy = self.policies[name]
            self.assertEqual(policy['podSelector']['matchLabels'], {'app.kubernetes.io/name': source})
            self.assertEqual(policy['policyTypes'], ['Egress'])
            self.assertEqual(policy['egress'], [{'to': [{
                'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'data'}},
                'podSelector': {'matchLabels': {'app': 'postgres'}},
            }], 'ports': [{'protocol': 'TCP', 'port': 5432}]}])
        database_policies = {name for name, policy in self.policies.items()
                             if any(port['port'] == 5432 for rule in policy.get('egress', [])
                                    for port in rule.get('ports', []))}
        self.assertEqual(database_policies, {'site-postgres-egress', 'migrator-postgres-egress'})
        self.assertNotIn('postgres-ingress', self.policies)
        self.assertFalse(any(port['port'] == 5432 for policy in self.policies.values()
                             for rule in policy.get('ingress', []) for port in rule.get('ports', [])))

    def test_no_unreviewed_https_or_unbounded_peer(self):
        # Every peer needs a real Pod selector (with optional namespace) or an
        # exact host CIDR. This baseline has no external HTTPS allowance.
        for policy in self.policies.values():
            for direction, peers in (('ingress', 'from'), ('egress', 'to')):
                for rule in policy.get(direction, []):
                    self.assertTrue(rule.get(peers))
                    self.assertTrue(rule.get('ports'))
                    self.assertNotIn(443, [port['port'] for port in rule['ports']])
                    for peer in rule[peers]:
                        if 'ipBlock' in peer:
                            network = ipaddress.ip_network(peer['ipBlock']['cidr'])
                            self.assertEqual(network.prefixlen, network.max_prefixlen)
                        else:
                            self.assertTrue(peer.get('podSelector', {}).get('matchLabels'))
                            if 'namespaceSelector' in peer:
                                self.assertTrue(peer['namespaceSelector'].get('matchLabels'))


if __name__ == '__main__':
    unittest.main()
