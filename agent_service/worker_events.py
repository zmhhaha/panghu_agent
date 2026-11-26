"""Exact metadata vocabulary admitted from a Worker; no native text fields."""

NATIVE_EVENT_KINDS = frozenset({'pi_event', 'runtime_finished', 'engine_started', 'engine_progress'})
OUTPUT_FAILURE_STAGES = frozenset({'stage_inputs', 'execute_native', 'collect_output', 'release_context',
    'freeze_artifacts', 'freeze_manifest', 'sign_result', 'unknown'})
OUTPUT_FAILURE_CLASSES = frozenset({'invalid_json', 'invalid_output_contract', 'output_io_failure',
    'invalid_javascript', 'handoff_failure'})
JSON_FAILURE_REASONS = frozenset({'strict_json', 'json_syntax', 'duplicate_key', 'non_finite_number',
    'invalid_utf8'})
JSON_LOCATION_CODES = frozenset({'json_line', 'json_column', 'json_offset'})
JAVASCRIPT_LOCATION_CODES = frozenset({'javascript_line', 'javascript_column'})
JAVASCRIPT_OUTPUT_FILE_CODES = {'/scene.html': 'scene_html', '/scene.js': 'scene_js'}


def valid_worker_payload(value):
    """Accept only fixed labels plus bounded numeric JSON source locations."""
    if type(value) is not dict or type(value.get('event')) is not str:
        return False
    kind = value['event']
    if kind in NATIVE_EVENT_KINDS:
        return set(value) == {'event'}
    if (type(value.get('phase')) is not str or value['phase'] not in OUTPUT_FAILURE_STAGES
            or type(value.get('code')) is not str):
        return False
    if kind == 'output_validation_failed':
        return set(value) == {'event', 'phase', 'code'} and value['code'] in OUTPUT_FAILURE_CLASSES
    if kind == 'output_validation_reason':
        return set(value) == {'event', 'phase', 'code'} and value['code'] in JSON_FAILURE_REASONS
    if kind == 'output_validation_file':
        return set(value) == {'event', 'phase', 'code'} and value['code'] in JAVASCRIPT_OUTPUT_FILE_CODES.values()
    if kind == 'output_validation_location':
        return (set(value) == {'event', 'phase', 'code', 'count'}
            and value['code'] in JSON_LOCATION_CODES | JAVASCRIPT_LOCATION_CODES
            and type(value.get('count')) is int and 0 <= value['count'] <= 2**31 - 1)
    return False
