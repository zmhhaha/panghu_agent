"""Fixed-vocabulary execution diagnostics; native text is never logged here."""
from __future__ import annotations

import json
import logging


_COMPONENTS = frozenset({'pi_runtime', 'pi_driver'})
_STAGES = frozenset({'check_settings', 'validate_request', 'prepare_workspace',
    'prepare_profile_workspace', 'verify_resources', 'write_agent_config', 'probe_versions',
    'build_command', 'build_prompt', 'write_prompt', 'prepare_isolation', 'persist_journal',
    'launch', 'execute_native', 'validate_completion', 'stop_processes', 'write_provenance',
    'check_request', 'wire_builder', 'validate_native_request'})
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
    'ppt_prepared_checkpoint_required', 'model_proxy_grant_invalid'})
_NAMED_CATEGORIES = {'RuntimeExecutionError': 'runtime_error', 'ProfileError': 'profile_error',
                     'ServiceError': 'service_error', 'ContractError': 'contract_error',
                     '_DriverError': 'driver_error'}


def log_execution_failure(component: str, stage: str, error: BaseException) -> None:
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
        logging.getLogger(__name__).warning('%s', json.dumps(record, sort_keys=True))
    except Exception:
        pass
