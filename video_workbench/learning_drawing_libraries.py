"""Fixed drawing helpers for reference learning, separate from artistic Skills.

Only image-owned code may supply these bytes. A loaded module establishes
transport evidence, never a function call, successful method or visual quality.
"""
from copy import deepcopy

from agent_service.contracts import ServiceError


OBSERVATION_SCHEMA = 'video.learning-drawing-library-observation.v1'
OBSERVATION_SCOPE = 'loaded_scripts_not_api_calls_or_visual_application'


def drawing_libraries(value, assets):
    if value is None:
        return {}
    from .ink_landscape_library import validate_descriptor
    if type(value) is not dict or set(value) != {'ink-landscape'}:
        raise ServiceError('learning_drawing_library_invalid', 422)
    validate_descriptor(value['ink-landscape'], assets)
    return deepcopy(value)


def validate_observation(observation, libraries):
    if (type(observation) is not dict or set(observation) != {
            'schema', 'scope', 'drawing_libraries', 'loaded_scripts', 'loaded_libraries', 'api_execution'}
            or observation['schema'] != OBSERVATION_SCHEMA
            or observation['scope'] != OBSERVATION_SCOPE
            or observation['drawing_libraries'] != libraries):
        raise ServiceError('learning_drawing_library_evidence_invalid', 409)
    scripts = observation['loaded_scripts']
    if (type(scripts) is not list or any(type(name) is not str for name in scripts)
            or scripts != sorted(set(scripts))
            or observation['loaded_libraries'] != (
                ['ink-landscape'] if '/assets/ink-landscape.mjs' in scripts else [])):
        raise ServiceError('learning_drawing_library_evidence_invalid', 409)
    execution = observation['api_execution']
    if (type(execution) is not dict or set(execution) != {'schema', 'scope', 'module_sha256', 'functions'}
            or execution['schema'] != 'video.learning-drawing-api-execution.v1'
            or execution['scope'] != 'v8_executed_functions_not_visual_application'
            or execution['module_sha256'] != libraries['ink-landscape']['assets']['ink-landscape.mjs']
            or type(execution['functions']) is not list or len(execution['functions']) > 1024):
        raise ServiceError('learning_drawing_library_evidence_invalid', 409)
    previous = (-1, -1)
    for function in execution['functions']:
        if (type(function) is not dict or set(function) != {
                'function_name', 'start_offset', 'end_offset', 'call_count'}
                or type(function['function_name']) is not str or not 1 <= len(function['function_name']) <= 200
                or any(type(function[key]) is not int for key in ('start_offset', 'end_offset', 'call_count'))
                or not 0 <= function['start_offset'] < function['end_offset'] <= 2 * 1024 * 1024
                or not 1 <= function['call_count'] <= 2 ** 53 - 1
                or (function['start_offset'], function['end_offset']) <= previous):
            raise ServiceError('learning_drawing_library_evidence_invalid', 409)
        previous = (function['start_offset'], function['end_offset'])
    if execution['functions'] and 'ink-landscape' not in observation['loaded_libraries']:
        raise ServiceError('learning_drawing_library_evidence_invalid', 409)
    return deepcopy(observation)
