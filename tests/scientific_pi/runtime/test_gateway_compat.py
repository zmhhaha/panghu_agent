"""Trusted scientific gateway wire profile; no real model, keys or external network."""
import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from pi_worker.runtime import PiRuntime, RuntimeSettings, RuntimeExecutionError
from scientific_agent.pi_contracts import input_digest
from scientific_agent.pi_runtime_config import (
    SCIENTIFIC_GATEWAY_COMPLETIONS_COMPAT, runtime_from_env,
)

ROOT = Path(__file__).resolve().parents[3]


def factory(directory, **environment):
    values = {'SCIENTIFIC_PI_MODEL_BASE_URL': 'http://127.0.0.1:1/v1',
              'SCIENTIFIC_PI_MODEL': 'fixture-model',
              'SCIENTIFIC_PI_MODEL_API_KEY': 'local-dummy-only', **environment}
    with patch.dict(os.environ, values, clear=True):
        return runtime_from_env(Path(directory))


class GatewayCompatTests(unittest.TestCase):
    def test_factory_completions_defaults_and_preserves_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = factory(directory)
            self.assertEqual(runtime.settings.model_compat, SCIENTIFIC_GATEWAY_COMPLETIONS_COMPAT)
            self.assertEqual(runtime.settings.model_max_tokens, 8192)
            self.assertFalse(runtime.settings.fixture_mode)
            self.assertFalse(runtime.settings.capability_verified)
            self.assertFalse(runtime.settings.model_verified)
            self.assertFalse(runtime.settings.isolation_verified)
            self.assertEqual(runtime._custom_model_definition(), {
                'id': 'fixture-model', 'maxTokens': 8192,
                'compat': SCIENTIFIC_GATEWAY_COMPLETIONS_COMPAT})

    def test_responses_has_token_cap_without_completions_compat(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = factory(directory, SCIENTIFIC_PI_MODEL_API='openai-responses')
            self.assertEqual(runtime._custom_model_definition(), {'id': 'fixture-model', 'maxTokens': 8192})
            PiRuntime(replace(runtime.settings, fixture_mode=True))._check_settings()

    def test_direct_provider_can_clear_only_the_fixed_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = factory(directory, SCIENTIFIC_PI_MODEL_COMPAT_PROFILE='none')
            self.assertEqual(runtime.settings.model_compat, {})
            self.assertEqual(runtime.settings.model_max_tokens, 8192)
            for profile in ('arbitrary-json', '{"supportsStore":true}'):
                with self.assertRaises(ValueError):
                    factory(directory, SCIENTIFIC_PI_MODEL_COMPAT_PROFILE=profile)
            with self.assertRaises(ValueError):
                factory(directory, SCIENTIFIC_PI_MODEL_API='openai-responses',
                        SCIENTIFIC_PI_MODEL_COMPAT_PROFILE='scientific-gateway')

    def test_compatibility_and_token_limit_fail_closed_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            base = replace(factory(directory).settings, fixture_mode=True)
            bad = [dict(model_compat=None), dict(model_compat={'headers': {}}),
                   dict(model_compat={'supportsStore': 'false'}),
                   dict(model_compat={'supportsUsageInStreaming': 0}),
                   dict(model_compat={'supportsReasoningEffort': None}),
                   dict(model_compat={'maxTokensField': 'maxTokens'}),
                   dict(model_compat={'maxTokensField': []}),
                   dict(model_api='openai-responses'), dict(model_base_url=None)]
            bad += [dict(model_max_tokens=value) for value in (True, False, 0, -1, 8193, 1.5, '8192')]
            for override in bad:
                with self.subTest(override=override):
                    with self.assertRaises(RuntimeExecutionError):
                        PiRuntime(replace(base, **override))._check_settings()
            for limit in (1, 8192):
                PiRuntime(replace(base, model_max_tokens=limit))._check_settings()
            responses = replace(base, model_api='openai-responses', model_compat={})
            PiRuntime(responses)._check_settings()

    def test_actual_pinned_cli_strict_gateway_schema_two_tool_rounds(self):
        """Validate both real SDK requests with the actual gateway ChatRequest class."""
        helper_path = ROOT / 'tests/scientific_pi/integration/test_real_pi_smoke.py'
        spec = importlib.util.spec_from_file_location('strict_wire_smoke_helper', helper_path)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        node = shutil.which('node')
        if not node or not helper.CLI.is_file():
            self.skipTest('Reviewed local Pi dependency and Node required; no install performed')
        import pydantic
        from typing import Any
        source = ROOT.parent / 'llm-service/app.py'
        parsed = ast.parse(source.read_text(encoding='utf-8'))
        definition = next(item for item in parsed.body
                          if isinstance(item, ast.ClassDef) and item.name == 'ChatRequest')
        namespace = dict(BaseModel=pydantic.BaseModel, Field=pydantic.Field,
                         ConfigDict=pydantic.ConfigDict, Any=Any)
        exec(compile(ast.Module(body=[definition], type_ignores=[]), str(source), 'exec'), namespace)
        schema = namespace['ChatRequest']
        rejected = []
        validated = []
        with tempfile.TemporaryDirectory() as directory, helper.LoopbackModel() as model:
            original = model.server.RequestHandlerClass
            class StrictHandler(original):
                def do_POST(self):
                    body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                    try:
                        request = json.loads(body)
                        schema.model_validate(request)
                        assert type(request.get('max_tokens')) is int
                        assert 1 <= request['max_tokens'] <= 8192
                        assert not {'store', 'stream_options', 'max_completion_tokens', 'reasoning_effort'} & request.keys()
                        validated.append(set(request))
                    except Exception:
                        rejected.append(True)
                        self.send_response(422)
                        self.send_header('Content-Length', '0')
                        self.send_header('Connection', 'close')
                        self.end_headers()
                        self.close_connection = True
                        return
                    saved = self.rfile
                    self.rfile = io.BytesIO(body)
                    try:
                        super().do_POST()
                    finally:
                        self.rfile = saved
            model.server.RequestHandlerClass = StrictHandler
            settings = replace(factory(directory).settings,
                command=(node, str(helper.CLI)), node_command=(node,),
                provider='fixture', fixture_mode=True, model_base_url=model.base_url,
                model_env={'SCIENTIFIC_MODEL_API_KEY': helper.DUMMY_API_KEY},
                idle_timeout_seconds=15, term_grace_seconds=0.2, kill_grace_seconds=2)
            now = datetime.now(timezone.utc)
            job = dict(schema_version='1', task_id=str(uuid.uuid4()),
                       topic='Strict loopback tool protocol test only', user_id='local-strict-wire',
                       engine='pi', mode='brief', timeout_seconds=60,
                       created_at=now.isoformat(), execution_deadline_at=(now + timedelta(seconds=35)).isoformat(),
                       skill_bundle_revision=settings.skill_bundle_revision,
                       runtime_revision=settings.runtime_revision)
            job['input_digest'] = input_digest(job)
            workspace = Path(directory) / 'attempt'
            PiRuntime(settings).execute(job, workspace, lambda event: None, threading.Event())
            provenance = json.loads((workspace / 'provenance.json').read_text(encoding='utf-8'))
            accounting = provenance['output_accounting']
            self.assertGreater(accounting['raw_lines'], 0)
            self.assertGreater(accounting['message_updates'], 0)
            self.assertGreater(accounting['raw_bytes'], accounting['output_bytes'])
            self.assertGreater(accounting['physical_output_bytes'], 0)
            self.assertEqual(rejected, [])
            self.assertEqual(len(validated), 2)
            self.assertEqual(model.errors, [])
            self.assertIn(helper.MARKER, json.dumps(model.requests[1]['messages']))
            configured = json.loads((workspace / '.home/.pi/agent/models.json').read_text())
            entry = configured['providers']['fixture']['models'][0]
            self.assertEqual(entry['compat'], SCIENTIFIC_GATEWAY_COMPLETIONS_COMPAT)
            self.assertEqual(entry['maxTokens'], 8192)


if __name__ == '__main__':
    unittest.main()
