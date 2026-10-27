"""Offline startup failures expose only fixed operator diagnostic metadata."""
from datetime import datetime, timedelta, timezone
import errno
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from agent_service.contracts import ServiceError
from agent_service.diagnostics import log_execution_failure
from agent_service.engines import EngineRequest, PiDriver, StopEvidence
from agent_service.runtime import PiRuntime, RuntimeExecutionError, RuntimeSettings
from agent_service.runtime.profiles import CommonExecution, ExecutionProfile, ProfileError


SECRET = 'PRIVATE_TOKEN_INPUT_PATH_AND_NATIVE_OUTPUT'
LOGGER = 'agent_service.diagnostics'


class Profile(ExecutionProfile):
    def __init__(self, *, validation_error=None, preparation_error=None):
        self.validation_error, self.preparation_error = validation_error, preparation_error

    def validate(self, wire):
        if self.validation_error is not None:
            raise self.validation_error
        return CommonExecution('fixture-task', 'fixture-runtime', 'fixture-skills',
                               wire['timeout_seconds'], wire['execution_deadline_at'])

    def prepare_workspace(self, wire, workspace):
        if self.preparation_error is not None:
            raise self.preparation_error

    def build_prompt(self, wire):
        return SECRET

    def execution_provenance(self, started, finished):
        return {'engine': 'pi', 'profile': 'offline-diagnostic-fixture'}


class Stopper:
    def stop(self, execution_id):
        pass

    def prove_stopped(self, execution_id):
        return StopEvidence(execution_id, True, ('offline-fixture-writer',))


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        skill = self.root / 'SKILL.md'
        skill.write_text('# Fixed offline diagnostic fixture\n', encoding='utf-8')
        fake = Path(__file__).with_name('fixtures') / 'fake_pi.py'
        self.settings = RuntimeSettings(command=(sys.executable, str(fake), 'success'),
            node_command=(sys.executable, str(fake), 'node'), provider='fixture', model='fixture-model',
            runtime_revision='fixture-runtime', skill_bundle_revision='fixture-skills', fixture_mode=True,
            skill_paths=(skill,), skill_hashes=(hashlib.sha256(skill.read_bytes()).hexdigest(),),
            model_base_url='http://127.0.0.1:1/v1', model_api='openai-responses',
            model_env={'AGENT_MODEL_API_KEY': SECRET}, term_grace_seconds=.1, kill_grace_seconds=1)
        self.request = EngineRequest('fixture-task', 'workspace-task.v1', {'prompt': SECRET}, 30,
                                    datetime.now(timezone.utc) + timedelta(seconds=30))
        self.wire = {'timeout_seconds': self.request.timeout_seconds,
                     'execution_deadline_at': self.request.deadline_at.isoformat()}

    def tearDown(self):
        self.directory.cleanup()

    def records(self, capture):
        self.assertNotIn(SECRET, '\n'.join(capture.output))
        self.assertNotIn(str(self.root), '\n'.join(capture.output))
        self.assertTrue(all(record.exc_info is None and not record.stack_info for record in capture.records))
        return [json.loads(record.getMessage()) for record in capture.records]

    def test_oserror_during_profile_preparation_preserves_errno_without_paths_or_text(self):
        runtime = PiRuntime(self.settings, profile=Profile(
            preparation_error=PermissionError(errno.EACCES, SECRET, str(self.root / SECRET))))
        with self.assertLogs(LOGGER, level='WARNING') as capture:
            with self.assertRaises(RuntimeExecutionError) as raised:
                runtime.execute(self.wire, self.root / 'attempt', lambda _: None, threading.Event())
        self.assertEqual(raised.exception.code, 'runtime_error')
        self.assertEqual(self.records(capture), [dict(event='execution_failure', component='pi_runtime',
            stage='prepare_profile_workspace', category='os_error', code='unclassified_error', errno=errno.EACCES)])

    def test_profile_validation_logs_reviewed_code_and_keeps_public_error(self):
        runtime = PiRuntime(self.settings, profile=Profile(
            validation_error=ProfileError('invalid_native_request', SECRET)))
        with self.assertLogs(LOGGER, level='WARNING') as capture:
            with self.assertRaises(RuntimeExecutionError) as raised:
                runtime.execute(self.wire, self.root / 'attempt', lambda _: None, threading.Event())
        self.assertEqual(raised.exception.code, 'invalid_native_request')
        self.assertEqual(self.records(capture)[0], dict(event='execution_failure', component='pi_runtime',
            stage='validate_request', category='profile_error', code='invalid_native_request', errno=None))

    def test_service_error_during_workspace_setup_remains_original_exception(self):
        failure = ServiceError('input_staging_not_ready', 503)
        runtime = PiRuntime(self.settings, profile=Profile(preparation_error=failure))
        with self.assertLogs(LOGGER, level='WARNING') as capture:
            with self.assertRaises(ServiceError) as raised:
                runtime.execute(self.wire, self.root / 'attempt', lambda _: None, threading.Event())
        self.assertIs(raised.exception, failure)
        self.assertEqual(self.records(capture)[0]['category'], 'service_error')
        self.assertEqual(self.records(capture)[0]['code'], 'input_staging_not_ready')

    def test_driver_wire_builder_failure_logs_only_operator_metadata(self):
        def bad_wire(request):
            raise ServiceError('model_proxy_grant_invalid', 503)
        driver = PiDriver(object(), Stopper(), wire_builder=bad_wire, enabled=True)
        events = []
        with self.assertLogs(LOGGER, level='WARNING') as capture:
            result = driver.execute(self.request, self.root / SECRET, events.append, threading.Event())
        self.assertEqual((result.status, result.error_code, result.stopped), ('failed', 'engine_error', True))
        self.assertEqual(events, [])
        self.assertEqual(self.records(capture)[0], dict(event='execution_failure', component='pi_driver',
            stage='wire_builder', category='service_error', code='model_proxy_grant_invalid', errno=None))

    def test_driver_runtime_failure_still_discards_exception_text_and_unknown_code(self):
        class Runtime:
            def execute(self, *args):
                raise RuntimeExecutionError(SECRET, SECRET)
        driver = PiDriver(Runtime(), Stopper(), wire_builder=lambda _: self.wire, enabled=True)
        with self.assertLogs(LOGGER, level='WARNING') as capture:
            result = driver.execute(self.request, self.root, lambda _: None, threading.Event())
        self.assertEqual(result.error_code, 'engine_error')
        self.assertEqual(self.records(capture)[0]['stage'], 'execute_native')
        self.assertEqual(self.records(capture)[0]['code'], 'unclassified_error')

    def test_real_offline_subprocess_success_emits_no_failure_warning(self):
        runtime = PiRuntime(self.settings, profile=Profile())
        driver = PiDriver(runtime, Stopper(), wire_builder=lambda _: self.wire, enabled=True)
        events = []
        with self.assertNoLogs(LOGGER, level='WARNING'):
            result = driver.execute(self.request, self.root / 'attempt', events.append, threading.Event())
        self.assertEqual((result.status, result.stopped), ('completed', True))
        self.assertTrue((self.root / 'attempt' / 'provenance.json').is_file())
        self.assertNotIn(SECRET, json.dumps(events))

    def test_unreviewed_exception_names_codes_and_labels_never_enter_logs(self):
        custom_type = type(SECRET, (RuntimeError,), {'code': SECRET})
        with self.assertLogs(LOGGER, level='WARNING') as capture:
            log_execution_failure(SECRET, SECRET, custom_type(SECRET))
        self.assertEqual(self.records(capture), [dict(event='execution_failure', component='unknown',
            stage='unknown', category='unexpected_error', code='unclassified_error', errno=None)])

    def test_logging_failure_cannot_change_execution_error_semantics(self):
        with patch('agent_service.diagnostics.logging.getLogger', side_effect=ValueError(SECRET)):
            log_execution_failure('pi_runtime', 'validate_request', ValueError(SECRET))


if __name__ == '__main__':
    unittest.main()
