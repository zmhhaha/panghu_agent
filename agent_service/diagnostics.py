"""Fixed-vocabulary execution diagnostics; native text is never logged here."""
from __future__ import annotations

import json
import logging
import re


_COMPONENTS = frozenset({'pi_runtime', 'pi_driver', 'model_proxy'})
_STAGES = frozenset({'check_settings', 'validate_request', 'prepare_workspace',
    'prepare_profile_workspace', 'verify_resources', 'write_agent_config', 'probe_versions',
    'build_command', 'build_prompt', 'write_prompt', 'prepare_isolation', 'persist_journal',
    'launch', 'execute_native', 'validate_completion', 'stop_processes', 'write_provenance',
    'check_request', 'wire_builder', 'validate_native_request', 'authorize_upstream',
    'open_upstream', 'validate_upstream', 'receive_upstream', 'persist_response'})
_CODES = frozenset({'cancelled', 'timed_out', 'runtime_not_ready', 'runtime_busy',
    'runtime_exit', 'runtime_protocol_error', 'runtime_idle_timeout', 'runtime_output_limit',
    'runtime_raw_output_limit', 'runtime_output_line_limit', 'runtime_event_limit',
    'process_safety_unverified', 'model_error', 'model_http_transient', 'model_mismatch',
    'runtime_error', 'unsafe_workspace', 'configuration_missing', 'invalid_timestamp',
    'invalid_native_request', 'invalid_native_output', 'invalid_profile_provenance',
    'event_sink_error', 'event_sink_failed', 'engine_unavailable', 'native_deadline_mismatch',
    'native_protocol_error', 'input_staging_not_ready', 'runtime_busy',
    'read_only_mounts_unavailable', 'input_mount_layout_invalid', 'unsafe_input_staging',
    'read_only_mount_failed', 'read_only_mount_unverified', 'input_unmount_unverified',
    'invalid_ppt_task', 'ppt_input_snapshot_mismatch', 'ppt_project_not_fresh',
    'ppt_prepared_checkpoint_required', 'model_proxy_grant_invalid',
    'model_grant_invalid', 'model_grant_revoked', 'model_grant_scope_mismatch',
    'model_upstream_rejected', 'model_upstream_protocol_invalid', 'model_response_too_large',
    'candidate_scope_mismatch', 'candidate_scope_consumed', 'candidate_grant_expired',
    'candidate_request_deadline_mismatch'})
_NAMED_CATEGORIES = {'RuntimeExecutionError': 'runtime_error', 'ProfileError': 'profile_error',
                     'ServiceError': 'service_error', 'ContractError': 'contract_error',
                     '_DriverError': 'driver_error',
                     'ConnectTimeout': 'http_connect_timeout', 'ReadTimeout': 'http_read_timeout',
                     'WriteTimeout': 'http_write_timeout', 'PoolTimeout': 'http_pool_timeout',
                     'ConnectError': 'http_connect_error', 'ReadError': 'http_read_error',
                     'WriteError': 'http_write_error', 'CloseError': 'http_close_error',
                     'RemoteProtocolError': 'http_remote_protocol_error',
                     'LocalProtocolError': 'http_local_protocol_error',
                     'UnsupportedProtocol': 'http_unsupported_protocol', 'ProxyError': 'http_proxy_error',
                     'DecodingError': 'http_decoding_error', 'TooManyRedirects': 'http_redirect_error',
                     'TimeoutException': 'http_timeout', 'NetworkError': 'http_network_error',
                     'TransportError': 'http_transport_error', 'ProtocolError': 'http_protocol_error',
                     'RequestError': 'http_request_error', 'HTTPStatusError': 'http_status_error'}


def log_pi_model_failure(message: dict) -> None:
    """Describe the pinned SDK's error shape without logging its native text.

    Pi serializes Error.message rather than its JS exception type. These labels
    are diagnostic hints from reviewed, anchored message templates, never proof
    of an upstream status or a reason to retry. Property names, response values,
    paths, and arbitrary provider error codes must not become log labels.
    """
    try:
        reason = message.get('stopReason')
        reason = reason if type(reason) is str and reason in {'error', 'aborted'} else 'unknown'
        raw = message.get('errorMessage')
        category, status = 'native_unknown', None
        if type(raw) is not str:
            category = 'native_error_missing'
        elif len(raw) > 16384:
            category = 'native_error_oversized'
        else:
            http = re.match(r'\A([45][0-9]{2}) (?=\S)', raw)
            if reason == 'error' and http:
                category, status = 'http_status_message', int(http.group(1))
            elif re.fullmatch(r"Cannot read properties of (?:undefined|null) \(reading '[^'\r\n]{1,128}'\)", raw):
                category = 'js_nullish_property_read'
            elif re.fullmatch(r"Cannot set properties of (?:undefined|null) \(setting '[^'\r\n]{1,128}'\)", raw):
                category = 'js_nullish_property_write'
            elif re.fullmatch(r'[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*){0,5} is not a function', raw):
                category = 'js_not_function'
            elif re.fullmatch(r'[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*){0,5} is not iterable', raw):
                category = 'js_not_iterable'
            elif re.fullmatch(r'Unhandled stop reason: [^\r\n]{1,128}', raw):
                category = 'responses_stop_reason_unknown'
            elif re.fullmatch(r'Error Code [A-Za-z0-9_-]{1,128}: [^\r\n]*', raw):
                category = 'responses_stream_error'
            elif raw == 'Unknown error (no error details in response)':
                category = 'responses_failed_without_details'
            elif raw == 'An unknown error occurred':
                category = 'responses_unsuccessful_status'
            elif raw == 'Request was aborted':
                category = 'request_aborted'
        record = dict(event='pi_model_failure', component='pi_runtime', stage='execute_native',
                      category=category, stop_reason=reason, native_http_status=status)
        logging.getLogger(__name__).warning('%s', json.dumps(record, sort_keys=True))
    except Exception:
        # Operator logging must never change the original error/retry semantics.
        pass


def log_execution_failure(component: str, stage: str, error: BaseException, *,
                          upstream_status=None, upstream_media=None) -> None:
    """Emit only reviewed labels and a bounded OS errno, never exception strings.

    This is operator logging, separate from public events/results. Unknown codes,
    exception type names, paths, command arguments and native output are omitted.
    A logging failure must not change execution or stop semantics.
    """
    try:
        category = _NAMED_CATEGORIES.get(type(error).__name__)
        if category is None:
            category = ('os_error' if isinstance(error, OSError) else
                        'value_error' if isinstance(error, ValueError) else
                        'type_error' if isinstance(error, TypeError) else 'unexpected_error')
        code = getattr(error, 'code', None)
        code = code if type(code) is str and code in _CODES else 'unclassified_error'
        number = error.errno if isinstance(error, OSError) else None
        record = dict(event='execution_failure',
            component=component if component in _COMPONENTS else 'unknown',
            stage=stage if stage in _STAGES else 'unknown', category=category, code=code,
            errno=number if type(number) is int and 1 <= number <= 4095 else None)
        if component == 'model_proxy':
            record['upstream_status'] = (upstream_status if type(upstream_status) is int
                                         and 100 <= upstream_status <= 599 else None)
            record['upstream_media'] = ('json' if upstream_media == 'application/json' else
                'event_stream' if upstream_media == 'text/event-stream' else
                'other' if type(upstream_media) is str and upstream_media else 'unknown')
        logging.getLogger(__name__).warning('%s', json.dumps(record, sort_keys=True))
    except Exception:
        pass
