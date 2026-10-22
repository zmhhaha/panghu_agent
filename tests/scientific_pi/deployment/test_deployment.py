import importlib.util
import os
from pathlib import Path
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

    def test_api_patch_retains_crewai_and_bypasses_old_entrypoint_overlay(self):
        patch_value = renderer.api_patch('scientific-agent', IMAGE)
        container = patch_value['spec']['template']['spec']['containers'][0]
        self.assertIn('app.api.scientific_agent:app', container['command'])
        values = {item['name']: item.get('value') for item in container['env']}
        self.assertEqual(values['SCIENTIFIC_DEFAULT_ENGINE'], 'crewai')
        self.assertNotIn('SCIENTIFIC_PI_MODEL_API_KEY', values)
        self.assertTrue(values['SCIENTIFIC_PI_WORKER_URL'].startswith('http://scientific-pi-worker.'))

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


if __name__ == '__main__':
    unittest.main()
