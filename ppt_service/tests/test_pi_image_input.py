"""Synthetic local PNG transport only; no visual-quality or production evidence."""
import base64
import dataclasses
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from pi_worker.runtime import PiRuntime, RuntimeSettings, RuntimeExecutionError
from ppt_service.tests import test_pi_responses_proxy as fixture
from ppt_service.tests.test_pi_profile import ppt_job, FAKE
from ppt_service.worker.pi_profile import PptProfile
from ppt_service.worker.proxy_usage import UsageLedger

import struct
import zlib


def png_chunk(kind, data):
    return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff)


PNG = (b'\x89PNG\r\n\x1a\n' + png_chunk(b'IHDR', struct.pack('!2I5B', 1, 1, 8, 2, 0, 0, 0))
       + png_chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00')) + png_chunk(b'IEND', b''))


class PiImageInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.skill = self.root / 'SKILL.md'
        self.skill.write_text('---\nname: image-protocol-fixture\ndescription: Synthetic image protocol only.\n---\nNo visual quality evidence.\n', encoding='utf-8')
        self.job = ppt_job()
        self.profile = PptProfile(runtime_revision=self.job['runtime_revision'], skill_bundle_revision=self.job['skill_bundle_revision'])
        self.settings = RuntimeSettings(command=(sys.executable, str(FAKE), 'success'),
            node_command=(sys.executable, str(FAKE), 'node'), provider='fixture', model=fixture.proxy.DEFAULT_MODEL,
            fixture_mode=True, model_api='openai-responses', model_base_url='http://127.0.0.1:1/v1',
            model_env={'SCIENTIFIC_MODEL_API_KEY': fixture.WORKER}, skill_paths=(self.skill,),
            skill_hashes=(hashlib.sha256(self.skill.read_bytes()).hexdigest(),),
            runtime_revision=self.job['runtime_revision'], skill_bundle_revision=self.job['skill_bundle_revision'],
            idle_timeout_seconds=12, term_grace_seconds=.2, kill_grace_seconds=2)

    def runtime(self, **changes):
        return PiRuntime(dataclasses.replace(self.settings, **changes), profile=self.profile)

    def test_default_definition_exact_and_explicit_modes(self):
        self.assertIsNone(RuntimeSettings().model_input)
        self.assertEqual(self.runtime()._custom_model_definition(), {'id': fixture.proxy.DEFAULT_MODEL})
        for mode in (('text',), ('text', 'image')):
            with self.subTest(mode=mode):
                runtime = self.runtime(model_input=mode)
                runtime._check_settings()
                self.assertEqual(runtime._custom_model_definition(), {'id': fixture.proxy.DEFAULT_MODEL, 'input': list(mode)})
                workspace = self.root / ('-'.join(mode))
                runtime.execute(self.job, workspace, lambda e: None, threading.Event())
                models = json.loads((workspace / '.home/.pi/agent/models.json').read_text())
                self.assertEqual(models['providers']['fixture']['models'], [runtime._custom_model_definition()])
        self.assertEqual(self.runtime(model_api='openai-completions', model_max_tokens=4096,
            model_compat={'supportsStore': False})._custom_model_definition(),
            {'id': fixture.proxy.DEFAULT_MODEL, 'maxTokens': 4096, 'compat': {'supportsStore': False}})

    def test_invalid_modes_rejected_before_probe_or_spawn(self):
        modes = [(), [], ['text'], ['text', 'image'], 'text', {'text': True}, True, 1,
                 ('image',), ('image', 'text'), ('text', 'text'), ('text', 'image', 'image'),
                 ('text', 'audio'), ('text', None), ('text', True), ('TEXT',)]
        for mode in modes:
            with self.subTest(mode=mode), patch.object(PiRuntime, '_probe') as probe, patch('pi_worker.runtime.adapter.subprocess.Popen') as spawn:
                runtime = self.runtime(model_input=mode)
                self.assertFalse(runtime.health()['ready'])
                with self.assertRaises(RuntimeExecutionError) as caught:
                    runtime.execute(self.job, self.root / 'unused', lambda e: None, threading.Event())
                self.assertEqual(caught.exception.code, 'configuration_missing')
                probe.assert_not_called()
                spawn.assert_not_called()
        self.assertFalse((self.root / 'unused').exists())

    def test_explicit_input_requires_custom_endpoint(self):
        for mode in (('text',), ('text', 'image')):
            with self.subTest(mode=mode), patch.object(PiRuntime, '_probe') as probe, patch('pi_worker.runtime.adapter.subprocess.Popen') as spawn:
                runtime = self.runtime(model_input=mode, model_api=None, model_base_url=None)
                with self.assertRaises(RuntimeExecutionError) as caught:
                    runtime.execute(self.job, self.root / 'unused', lambda e: None, threading.Event())
                self.assertEqual(caught.exception.code, 'configuration_missing')
                probe.assert_not_called()
                spawn.assert_not_called()

    def test_input_never_authorizes_ppt_production_or_job_capabilities(self):
        self.assertFalse(self.profile.production_ready)
        with patch('pi_worker.runtime.adapter.subprocess.Popen') as spawn:
            runtime = self.runtime(model_input=('text', 'image'), fixture_mode=False,
                capability_verified=True, model_verified=True, isolation_verified=True, image_digest='sha256:'+'a'*64)
            with self.assertRaises(RuntimeExecutionError) as caught:
                runtime.execute(self.job, self.root / 'unused', lambda e: None, threading.Event())
            self.assertEqual(caught.exception.code, 'runtime_not_ready')
            for key in ('model_input', 'input'):
                with self.subTest(key=key), self.assertRaises(RuntimeExecutionError):
                    self.runtime().execute(dict(self.job, **{key: ['text', 'image']}), self.root / 'unused', lambda e: None, threading.Event())
            spawn.assert_not_called()

    def run_image_transport(self, mode):
        node = shutil.which('node')
        if node is None or not fixture.CLI.is_file():
            self.skipTest('Installed Node or pinned Pi absent; no automatic install')
        image = self.root / 'local-only.png'
        image.write_bytes(PNG)
        args = {'path': str(image)}
        original_sse = fixture.sse
        def image_sse(n):
            raw = original_sse(n)
            if n != 1:
                return raw
            events = []
            for block in raw.decode().strip().split('\n\n'):
                event = json.loads(block.split('\ndata: ', 1)[1])
                def rewrite(value):
                    if isinstance(value, dict):
                        if value.get('type') == 'function_call':
                            value['name'] = 'read'
                            if value.get('arguments'):
                                value['arguments'] = json.dumps(args)
                        if value.get('type') == 'response.function_call_arguments.delta':
                            value['delta'] = json.dumps(args)
                        if value.get('type') == 'response.function_call_arguments.done':
                            value['arguments'] = json.dumps(args)
                        for child in value.values():
                            rewrite(child)
                    elif isinstance(value, list):
                        for child in value:
                            rewrite(child)
                rewrite(event)
                events.append('event: '+event['type']+'\ndata: '+json.dumps(event)+'\n\n')
            return ''.join(events).encode()
        with patch.object(fixture, 'sse', image_sse), fixture.LocalProxy(UsageLedger()) as local:
            runtime = self.runtime(command=(str(Path(node).resolve()), str(fixture.CLI)),
                node_command=(str(Path(node).resolve()),), model_input=mode, compact_events=True,
                model_base_url=f'http://127.0.0.1:{local.proxy.server_port}/v1')
            events = []
            runtime.execute(self.job, self.root / 'worker', events.append, threading.Event())
            self.assertEqual(local.errors, [])
            self.assertEqual(len(local.requests), 2)
            self.assertEqual(local.observed, [('/v1/responses', fixture.proxy.DEFAULT_MODEL, True, False)]*2)
            self.assertEqual(len(local.connections), 2)
            first, second = local.requests
            self.assertIn('read', [tool.get('name') for tool in first['tools']])
            calls = [i for i in second['input'] if i.get('type') == 'function_call']
            outputs = [i for i in second['input'] if i.get('type') == 'function_call_output']
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]['call_id'], fixture.CALL)
            self.assertEqual(calls[0]['name'], 'read')
            self.assertEqual(json.loads(calls[0]['arguments']), args)
            self.assertEqual(len(outputs), 1)
            self.assertEqual(outputs[0]['call_id'], fixture.CALL)
            self.assertEqual(events[-1]['type'], 'runtime_finished')
            self.assertEqual(self.job['task']['assets'], [])
            self.assertFalse(list((self.root / 'worker/.home').glob('runtime-*.json')))
            return second

    def test_actual_pi_default_text_only_downgrades_image(self):
        second = self.run_image_transport(None)
        serialized = json.dumps(second['input'])
        self.assertNotIn('input_image', serialized)
        self.assertNotIn('data:image/png;base64,', serialized)
        self.assertIn('(tool image omitted: model does not support images)', serialized)

    def test_actual_pi_explicit_image_preserves_png_bytes(self):
        second = self.run_image_transport(('text', 'image'))
        images = []
        def collect(value):
            if isinstance(value, dict):
                if value.get('type') == 'input_image':
                    images.append(value)
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)
        collect(second['input'])
        self.assertEqual(len(images), 1)
        prefix, encoded = images[0]['image_url'].split(',', 1)
        self.assertEqual(prefix, 'data:image/png;base64')
        decoded = base64.b64decode(encoded, validate=True)
        self.assertEqual(decoded, PNG)
        self.assertEqual(hashlib.sha256(decoded).hexdigest(), hashlib.sha256(PNG).hexdigest())


if __name__ == '__main__':
    unittest.main()
