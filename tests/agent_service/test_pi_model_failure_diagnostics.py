"""Native SDK failures remain private and never gain new retry semantics."""
import json
import unittest
from unittest.mock import patch

from agent_service.diagnostics import log_pi_model_failure
from agent_service.runtime import PiRuntime


SECRET = 'PRIVATE_PROPERTY_TOKEN_PATH_AND_RESPONSE'
LOGGER = 'agent_service.diagnostics'


class PiModelFailureDiagnosticsTests(unittest.TestCase):
    def classify(self, raw, *, reason='error', expected='native_unknown', status=None):
        with self.assertLogs(LOGGER, level='WARNING') as capture:
            error = PiRuntime._model_request_error({'stopReason': reason, 'errorMessage': raw})
        self.assertEqual(len(capture.records), 1)
        self.assertNotIn(SECRET, '\n'.join(capture.output))
        self.assertTrue(all(record.exc_info is None and not record.stack_info for record in capture.records))
        self.assertEqual(json.loads(capture.records[0].getMessage()), dict(
            event='pi_model_failure', component='pi_runtime', stage='execute_native',
            category=expected, stop_reason=reason, native_http_status=status))
        retryable = reason == 'error' and status in {429, 500, 502, 503, 504}
        self.assertEqual(error.code, 'model_http_transient' if retryable else 'model_error')
        self.assertEqual(error.retryable, retryable)
        self.assertNotIn(SECRET, str(error))

    def test_http_prefix_is_anchored_and_logs_no_provider_body(self):
        for status in (400, 401, 403, 404, 408, 409, 413, 422, 429, 500, 502, 503, 504, 599):
            with self.subTest(status=status):
                self.classify(f'{status} {SECRET}', expected='http_status_message', status=status)
        for raw in (f'{SECRET} 503 body', f' 503 {SECRET}', f'503\n{SECRET}',
                    f'5030 {SECRET}', f'503 ', f'200 {SECRET}', f'Error Code 503: {SECRET}'):
            with self.subTest(raw=raw):
                self.classify(raw, expected=('responses_stream_error' if raw.startswith('Error Code ') else 'native_unknown'))
        self.classify(f'503 {SECRET}', reason='aborted')

    def test_reviewed_js_shapes_discard_property_names(self):
        cases = {
            f"Cannot read properties of undefined (reading '{SECRET}')": 'js_nullish_property_read',
            f"Cannot read properties of null (reading '{SECRET}')": 'js_nullish_property_read',
            f"Cannot set properties of null (setting '{SECRET}')": 'js_nullish_property_write',
            f'event.{SECRET}.map is not a function': 'js_not_function',
            f'event.{SECRET} is not iterable': 'js_not_iterable',
        }
        for raw, category in cases.items():
            with self.subTest(category=category, raw=raw):
                self.classify(raw, expected=category)
                self.classify(f'{SECRET}\n{raw}')
                self.classify(f'{raw}\n{SECRET}')

    def test_responses_error_templates_discard_response_values(self):
        cases = {
            f'Unhandled stop reason: {SECRET}': 'responses_stop_reason_unknown',
            f'Error Code {SECRET}: {SECRET}': 'responses_stream_error',
            'Unknown error (no error details in response)': 'responses_failed_without_details',
            'An unknown error occurred': 'responses_unsuccessful_status',
            'Request was aborted': 'request_aborted',
        }
        for raw, category in cases.items():
            with self.subTest(category=category):
                self.classify(raw, expected=category)
        # response.failed may contain arbitrary provider code/text: no dynamic label.
        self.classify(f'{SECRET}: {SECRET}')

    def test_missing_oversized_and_unreviewed_messages_are_bounded(self):
        for raw in (None, {}, 503, [SECRET]):
            with self.subTest(raw=raw):
                self.classify(raw, expected='native_error_missing')
        self.classify(SECRET * 1024, expected='native_error_oversized')
        self.classify(SECRET)
        with self.assertLogs(LOGGER, level='WARNING') as capture:
            log_pi_model_failure({'stopReason': SECRET, 'errorMessage': SECRET})
        self.assertNotIn(SECRET, '\n'.join(capture.output))
        self.assertEqual(json.loads(capture.records[0].getMessage())['stop_reason'], 'unknown')

    def test_logging_failure_preserves_public_code_and_existing_retry_rule(self):
        with patch('agent_service.diagnostics.logging.getLogger', side_effect=ValueError(SECRET)):
            error = PiRuntime._model_request_error({'stopReason': 'error', 'errorMessage': f'503 {SECRET}'})
        self.assertEqual((error.code, error.retryable), ('model_http_transient', True))


if __name__ == '__main__':
    unittest.main()
