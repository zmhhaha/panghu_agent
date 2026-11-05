import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scientific_agent.pi_runtime_config import runtime_from_env

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('scientific_pi_renderer', ROOT / 'k8s/scientific-pi/render.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
IMAGE = 'example.invalid/scientific-pi@sha256:' + '0' * 64


class DeploymentTests(unittest.TestCase):
    def resources(self):
        return renderer.render('scientific-agent', IMAGE, 'http://llm-service.llm.svc.cluster.local/v1', 'llm-trusted')

    def test_images_require_immutable_digests(self):
        for image in ('image:latest', 'image:v1', 'image@sha256:nope', ''):
            with self.subTest(image=image), self.assertRaises(ValueError):
                renderer.pinned_image(image)
        renderer.pinned_image(IMAGE)

    def test_default_closed_no_human_resources_or_api_network_isolation(self):
        objects, api_policy = self.resources()
        self.assertEqual({item['kind'] for item in objects['items']}, {'PersistentVolumeClaim', 'Deployment', 'Service', 'NetworkPolicy'})
        self.assertTrue(all(item['metadata']['namespace'] == 'scientific-agent' for item in objects['items']))
        self.assertNotIn(api_policy, objects['items'])
        deployment = next(item for item in objects['items'] if item['kind'] == 'Deployment')
        self.assertEqual(deployment['spec']['template']['metadata']['labels']['llm-client'], 'true')
        self.assertEqual(deployment['spec']['selector']['matchLabels'], {'app': 'scientific-pi-worker'})
        pod = deployment['spec']['template']['spec']
        self.assertIs(pod['automountServiceAccountToken'], False)
        self.assertEqual(deployment['spec']['strategy']['type'], 'Recreate')
        container = pod['containers'][0]
        self.assertTrue(all(item['value'] == '0' for item in container['env'] if item['name'].endswith('_VERIFIED')))
        self.assertTrue(container['securityContext']['readOnlyRootFilesystem'])
        self.assertEqual(container['securityContext']['capabilities']['drop'], ['ALL'])
        self.assertEqual(set(container['securityContext']['capabilities']['add']), {'CHOWN','DAC_OVERRIDE','SETUID','SETGID','KILL'})
        self.assertTrue(all('hostPath' not in volume for volume in pod['volumes']))
        self.assertTrue(all('secretKeyRef' in item['valueFrom'] for item in container['env'] if 'valueFrom' in item))

    def test_model_path_and_namespace_are_trusted_configuration(self):
        external, _ = renderer.render('scientific-agent', IMAGE, 'https://example.invalid/v1', 'model')
        external_deployment = next(item for item in external['items'] if item['kind'] == 'Deployment')
        self.assertNotIn('llm-client', external_deployment['spec']['template']['metadata']['labels'])
        for url in ('http://external.example/v1', 'https://user:pass@example.invalid/v1', 'https://example.invalid/v1?token=secret'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                renderer.render('scientific-agent', IMAGE, url, 'model')
        with self.assertRaises(ValueError):
            renderer.render('bad namespace', IMAGE, 'https://example.invalid/v1', 'model')

    def test_pi_api_removes_legacy_execution_credentials_and_source_overlays(self):
        patch_value = renderer.api_patch('scientific-agent', IMAGE, execution_backend='pi')
        template = patch_value['spec']['template']
        pod = template['spec']
        container = pod['containers'][0]
        self.assertIn('app.api.scientific_agent:app', container['command'])
        values = {item['name']: item.get('value') for item in container['env']}
        self.assertEqual(values['SCIENTIFIC_DEFAULT_ENGINE'], 'pi')
        self.assertEqual(values['SCIENTIFIC_EXECUTION_BACKEND'], 'pi')
        self.assertNotIn('SCIENTIFIC_PI_MODEL_API_KEY', values)
        self.assertTrue(values['SCIENTIFIC_PI_WORKER_URL'].startswith('http://scientific-pi-worker.'))
        removed_env = {item['name'] for item in container['env'] if item.get('$patch') == 'delete'}
        self.assertTrue({'LLM_BASE_URL', 'LLM_MODEL', 'LLM_SERVICE_TOKEN'} <= removed_env)
        self.assertEqual({item['mountPath'] for item in container['volumeMounts'] if item.get('$patch') == 'delete'},
                         {'/app/scientific_agent', '/app/app/api/agent.py', renderer.AGENT_TOKEN_PATH})
        self.assertEqual({item['name'] for item in pod['volumes'] if item.get('$patch') == 'delete'},
                         {'source', 'scientific-workflow', renderer.AGENT_TOKEN_VOLUME})
        self.assertTrue({'SCIENTIFIC_AGENT_SERVICE_URL', 'SCIENTIFIC_AGENT_SERVICE_TOKEN_FILE',
                         'SCIENTIFIC_AGENT_GENERATION_REVISION', 'SCIENTIFIC_AGENT_REVISION_REVISION',
                         'SCIENTIFIC_AGENT_SERVICE_TOKEN'} <= removed_env)
        self.assertEqual(pod['initContainers'], [{'name': 'rag-sync', '$patch': 'delete'}])
        self.assertIsNone(template['metadata']['labels']['llm-client'])
        # API migration never replaces the verified worker spec or evidence flags.
        self.assertNotIn('SCIENTIFIC_PI_MODEL_VERIFIED', values)

    def test_agent_api_uses_business_token_file_and_removes_retired_worker_dependency(self):
        patch_value = renderer.api_patch('scientific-agent', IMAGE, execution_backend='agent-service')
        self.assertEqual(set(patch_value), {'spec'})
        pod = patch_value['spec']['template']['spec']
        container = pod['containers'][0]
        values = {item['name']: item.get('value') for item in container['env']}
        self.assertEqual(values['SCIENTIFIC_EXECUTION_BACKEND'], 'agent-service')
        self.assertEqual(values['SCIENTIFIC_AGENT_SERVICE_URL'],
                         'http://agent-service.agent-service.svc.cluster.local:8080')
        self.assertEqual(values['SCIENTIFIC_AGENT_SERVICE_TOKEN_FILE'], renderer.AGENT_TOKEN_PATH)
        self.assertEqual(values['SCIENTIFIC_AGENT_GENERATION_REVISION'], 'v1')
        self.assertEqual(values['SCIENTIFIC_AGENT_REVISION_REVISION'], 'v1')
        self.assertEqual([entry for entry in pod['volumes'] if entry.get('$patch') != 'delete'], [{
            'name': renderer.AGENT_TOKEN_VOLUME, 'secret': {'secretName': 'scientific-agent-service',
            'defaultMode': 0o444, 'items': [{'key': 'token', 'path': 'token'}]}}])
        mount = next(entry for entry in container['volumeMounts'] if entry.get('$patch') != 'delete')
        self.assertEqual(mount, {'name': renderer.AGENT_TOKEN_VOLUME, 'mountPath': renderer.AGENT_TOKEN_PATH,
                                'subPath': 'token', 'readOnly': True})
        credentials = [entry for entry in container['env'] if 'valueFrom' in entry]
        self.assertEqual(credentials, [])
        for name in ('SCIENTIFIC_PI_WORKER_URL', 'SCIENTIFIC_PI_WORKER_TOKEN',
                     'SCIENTIFIC_PI_RUNTIME_REVISION', 'SCIENTIFIC_PI_SKILL_BUNDLE_REVISION'):
            self.assertEqual(next(entry for entry in container['env'] if entry['name'] == name),
                             {'name': name, '$patch': 'delete'})
        self.assertNotIn('scientific-pi-worker', json.dumps(patch_value))
        self.assertFalse(any('MODEL_API_KEY' in name or name.endswith('_VERIFIED') for name in values))
        self.assertNotIn('envFrom', container)
        self.assertNotIn('items', patch_value)

    def test_agent_api_customizes_only_operator_owned_endpoint_and_contracts(self):
        result = renderer.api_patch('science', IMAGE, execution_backend='agent-service',
            agent_namespace='agents', agent_caller_secret='science.agent-token', agent_caller_secret_key='caller',
            agent_generation_revision='generate-v2', agent_revision_revision='revise-v3')
        pod = result['spec']['template']['spec']
        values = {item['name']: item.get('value') for item in pod['containers'][0]['env']}
        self.assertEqual(values['SCIENTIFIC_AGENT_SERVICE_URL'], 'http://agent-service.agents.svc.cluster.local:8080')
        self.assertEqual(values['SCIENTIFIC_AGENT_GENERATION_REVISION'], 'generate-v2')
        self.assertEqual(values['SCIENTIFIC_AGENT_REVISION_REVISION'], 'revise-v3')
        secret = next(item['secret'] for item in pod['volumes'] if 'secret' in item)
        self.assertEqual(secret['secretName'], 'science.agent-token')
        self.assertEqual(secret['items'], [{'key': 'caller', 'path': 'token'}])

    def test_agent_network_review_selects_both_namespace_and_exact_api_pods(self):
        policy = renderer.api_egress_policy('scientific-agent', execution_backend='agent-service')
        self.assertEqual(policy['metadata'], {'name': 'scientific-api-to-agent-service', 'namespace': 'scientific-agent'})
        self.assertEqual(policy['spec'], {
            'podSelector': {'matchLabels': {'app': 'research-api'}}, 'policyTypes': ['Egress'],
            'egress': [{'to': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': 'agent-service'}},
                'podSelector': {'matchLabels': {'app.kubernetes.io/name': 'agent-execution-service', 'component': 'api'}}}],
                'ports': [{'protocol': 'TCP', 'port': 8080}]}]})
        _, old_policy = self.resources()
        self.assertEqual(old_policy, renderer.api_egress_policy('scientific-agent', execution_backend='pi'))
        self.assertEqual(policy, renderer.api_egress_policy('scientific-agent'))
        self.assertNotIn('ipBlock', json.dumps(policy))

    def test_agent_deployment_configuration_rejects_unusable_names_and_revisions(self):
        for overrides in ({'agent_namespace': '../agents'}, {'agent_namespace': 'a' * 64},
                          {'agent_caller_secret': '../token'}, {'agent_caller_secret': 'a' * 64},
                          {'agent_caller_secret': 'a..b'}, {'agent_caller_secret_key': '/token'},
                          {'agent_caller_secret_key': ''}, {'agent_generation_revision': '../v1'},
                          {'agent_revision_revision': ''}, {'agent_generation_revision': 'a' * 101}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                renderer.api_patch('scientific-agent', IMAGE, execution_backend='agent-service', **overrides)
        for method in (renderer.api_patch, renderer.api_egress_policy):
            args = ('bad namespace', IMAGE) if method == renderer.api_patch else ('bad namespace',)
            with self.assertRaises(ValueError):
                method(*args, execution_backend='agent-service')
            args = ('scientific-agent', IMAGE) if method == renderer.api_patch else ('scientific-agent',)
            with self.assertRaises(ValueError):
                method(*args, execution_backend='unreviewed')

    def test_agent_cli_renders_api_patch_and_network_review_without_worker_image_or_cluster(self):
        for arguments in (['--api-patch', '--api-image', IMAGE], ['--api-egress-policy']):
            with self.subTest(arguments=arguments):
                result = subprocess.run([sys.executable, str(ROOT / 'k8s/scientific-pi/render.py'),
                    '--execution-backend', 'agent-service', *arguments], cwd=ROOT,
                    capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                parsed = json.loads(result.stdout)
                self.assertNotIn('items', parsed)
                self.assertNotIn('scientific-pi-worker', result.stdout if '--api-egress-policy' in arguments else '')
                if '--api-patch' in arguments:
                    self.assertEqual(parsed['spec']['template']['spec']['containers'][0]['image'], IMAGE)
                else:
                    self.assertEqual(parsed['kind'], 'NetworkPolicy')

    def test_agent_cli_rejects_worker_deployment_and_dedicated_worker_verification_flags(self):
        for arguments in ([], ['--worker-image', IMAGE], ['--api-patch'],
                          ['--api-egress-policy', '--assert-verified-runtime'],
                          ['--api-patch', '--api-egress-policy', '--api-image', IMAGE]):
            with self.subTest(arguments=arguments):
                result = subprocess.run([sys.executable, str(ROOT / 'k8s/scientific-pi/render.py'),
                    '--execution-backend', 'agent-service', *arguments], cwd=ROOT,
                    capture_output=True, text=True, timeout=20)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')

    def test_cli_defaults_to_agent_service_and_cannot_recreate_dedicated_worker(self):
        result = subprocess.run([sys.executable, str(ROOT / 'k8s/scientific-pi/render.py'),
            '--api-patch', '--api-image', IMAGE], cwd=ROOT,
            capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        container = json.loads(result.stdout)['spec']['template']['spec']['containers'][0]
        values = {entry['name']: entry.get('value') for entry in container['env']}
        self.assertEqual(values['SCIENTIFIC_EXECUTION_BACKEND'], 'agent-service')
        self.assertNotIn('scientific-pi-worker', result.stdout)
        self.assertEqual(json.loads(result.stdout), renderer.api_patch('scientific-agent', IMAGE))
        for arguments in ([], ['--worker-image', IMAGE], ['--execution-backend', 'pi'],
                          ['--execution-backend', 'pi', '--api-patch', '--api-image', IMAGE]):
            with self.subTest(arguments=arguments):
                result = subprocess.run([sys.executable, str(ROOT / 'k8s/scientific-pi/render.py'),
                    *arguments], cwd=ROOT, capture_output=True, text=True, timeout=20)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('scientific_agent/deploy.sh', result.stderr)
                self.assertEqual(result.stdout, '')

    def test_factory_cannot_enable_fixture_or_inherit_control_secret(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            'SCIENTIFIC_PI_FIXTURE_MODE': '1', 'SCIENTIFIC_PI_WORKER_TOKEN': 'control-secret',
            'SCIENTIFIC_PI_MODEL_API_KEY': 'model-only-secret', 'SMTP_PASSWORD': 'mail-secret',
        }, clear=True):
            runtime = runtime_from_env(Path(directory))
            self.assertFalse(runtime.settings.fixture_mode)
            self.assertFalse(runtime.health()['ready'])
            self.assertEqual(dict(runtime.settings.model_env), {'SCIENTIFIC_MODEL_API_KEY': 'model-only-secret'})
            self.assertNotIn('model-only-secret', repr(runtime.settings))
            self.assertTrue((Path(directory)/'control'/'isolation-sentinel').is_file())

    def test_invalid_evidence_flag_does_not_silently_enable(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'SCIENTIFIC_PI_MODEL_VERIFIED': 'yes'}, clear=True):
            with self.assertRaises(ValueError):
                runtime_from_env(Path(directory))


class DeploymentScriptTests(unittest.TestCase):
    """Exercise actual shell control flow with a local kubectl stand-in."""
    def setUp(self):
        if os.name == 'nt':
            self.bash = Path('C:/Program Files/Git/bin/bash.exe')
        else:
            self.bash = Path(shutil.which('bash') or '/nonexistent/bash')
        if not self.bash.is_file():
            self.skipTest('bash is required for deployment script tests')
        # Git Bash and native Python must share the same sandbox-writable path.
        self.directory = tempfile.TemporaryDirectory(prefix='.scientific-deploy-test-', dir=ROOT)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.log = self.root / 'kubectl.log'
        self.out = self.root / 'review'
        kubectl = self.root / 'kubectl'
        kubectl.write_text('''#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$SCIENTIFIC_DEPLOY_TEST_LOG"
case " $* " in
  *" exec "*) printf '%s\\n' "${SCIENTIFIC_DEPLOY_TEST_ACTIVE:-0}" ;;
  *" get secret llm-token "*) printf '%s\\n' 'synthetic-ready-marker' ;;
  *" get deployment/api -o json "*) printf '%s\\n' '{"spec":{"template":{"spec":{"containers":[{"name":"api","image":"old-image"}]}}}}' ;;
esac
''', encoding='utf-8', newline='\n')
        kubectl.chmod(0o755)
        sleep = self.root / 'sleep'
        sleep.write_text('#!/usr/bin/env bash\nexit 0\n', encoding='utf-8', newline='\n')
        sleep.chmod(0o755)
        self.env = dict(os.environ, SCIENTIFIC_PI_WORKER_IMAGE=IMAGE, SCIENTIFIC_PI_API_IMAGE=IMAGE,
                        SCIENTIFIC_DEPLOY_TEST_LOG=self.log.as_posix(),
                        PATH=str(self.root) + os.pathsep + os.environ.get('PATH', ''))

    def run_script(self, mode, active='0'):
        self.env['SCIENTIFIC_DEPLOY_TEST_ACTIVE'] = active
        return subprocess.run([str(self.bash), (ROOT / 'scripts/deploy-scientific-pi.sh').as_posix(), mode, self.out.as_posix()],
                              env=self.env, cwd=ROOT, text=True, encoding='utf-8', capture_output=True, timeout=20)

    def test_retired_worker_entrypoint_cannot_render_or_apply_resources(self):
        for mode in ('--dry-run', '--render', '--apply', '--apply-api'):
            with self.subTest(mode=mode):
                result = self.run_script(mode)
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertIn('scientific_agent/deploy.sh', result.stderr)
                self.assertFalse(self.log.exists())
                self.assertFalse(self.out.exists())

    def test_generic_entrypoints_refuse_to_overwrite_current_scientific_services(self):
        for script in ('deploy-api.sh', 'deploy-ui.sh'):
            for name in ('scientific_agent', 'scientific-agent'):
                with self.subTest(script=script, name=name):
                    result = subprocess.run([str(self.bash), (ROOT / 'scripts' / script).as_posix(), name],
                        env=self.env, cwd=ROOT, text=True, encoding='utf-8', capture_output=True, timeout=20)
                    self.assertEqual(result.returncode, 64, result.stderr)
                    self.assertIn('scientific_agent/deploy.sh', result.stderr)
                    self.assertFalse(self.log.exists())

    def test_token_sync_defaults_exclude_retired_scientific_caller(self):
        result = subprocess.run([str(self.bash), (ROOT / 'scripts/sync-llm-token.sh').as_posix()],
            env=self.env, cwd=ROOT, text=True, encoding='utf-8', capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.log.read_text().splitlines()
        expected = {'research-agent', 'daofaziran-agent', 'fofawubian-agent', 'yimaneili-agent',
                    'zhenzhuzhida-agent', 'zhongkuifumo-agent', 'zhougongjiemeng-agent',
                    'xiaotanrenjian-agent', 'bingbichunqiu-agent', 'game-review-agent',
                    'literature-downloader'}
        # Inspect actual command execution, including reads, so the removed
        # caller cannot survive in a second sync/restart list.
        self.assertEqual(len(calls), len(expected) * 2)
        self.assertTrue(all('scientific' not in call for call in calls))
        for namespace in expected:
            self.assertEqual(sum('-n ' + namespace + ' ' in call + ' ' for call in calls), 2)
        self.assertTrue(all(' rollout ' not in ' ' + call + ' ' for call in calls))

    def test_token_sync_rejects_explicit_retired_caller_before_any_cluster_call(self):
        for name in ('scientific-agent', 'scientific_agent'):
            with self.subTest(name=name):
                result = subprocess.run([str(self.bash), (ROOT / 'scripts/sync-llm-token.sh').as_posix(),
                    'research-agent', '--restart', name], env=self.env, cwd=ROOT,
                    text=True, encoding='utf-8', capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertIn('retired', result.stderr)
                self.assertIn('agent-service', result.stderr)
                self.assertFalse(self.log.exists())

    def test_token_sync_preserves_other_callers_and_restart_behavior(self):
        result = subprocess.run([str(self.bash), (ROOT / 'scripts/sync-llm-token.sh').as_posix(),
            'research-agent', '--restart'], env=self.env, cwd=ROOT,
            text=True, encoding='utf-8', capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.log.read_text().splitlines()
        self.assertEqual(len(calls), 4)
        self.assertTrue(all('-n research-agent ' in call + ' ' for call in calls))
        self.assertEqual(sum(' rollout restart ' in ' ' + call + ' ' for call in calls), 1)
        self.assertEqual(sum(' rollout status ' in ' ' + call + ' ' for call in calls), 1)


if __name__ == '__main__':
    unittest.main()
