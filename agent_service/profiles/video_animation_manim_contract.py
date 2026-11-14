"""Worker-owned Manim recipe author contract; no render execution."""
from copy import deepcopy
from ..contracts import canonical_bytes, digest
from ..manim_resource_contract import REQUEST_SCHEMA, validate_request
from ..animation_skills_contract import validate_skill_catalog
from .video_animation_production_contract import require, checksum, text
from .video_animation_skill_production_contract import validate_skill_decisions

INPUT_SCHEMA = 'video.animation-manim-recipe-input.v1'
RESULT_SCHEMA = 'video.animation-manim-recipe-result.v1'
CORE_FIELDS = frozenset({'effect_id', 'goal', 'guidance', 'resource_constraints'})
INPUT_MAX_BYTES = 4 * 1024 * 1024
OUTPUT_MAX_BYTES = 1024 * 1024
MAX_TOOL_EVIDENCE_BYTES = 2 * 1024 * 1024
RAW_RESULT_FIELDS = frozenset({'schema', 'input_sha256', 'request', 'strategy', 'skill_decisions'})


def validate_core_task(value):
    require(type(value) is dict and set(value) == CORE_FIELDS, 'manim_recipe_core_invalid')
    text(value['effect_id'], 100)
    text(value['goal'], 16000)
    text(value['guidance'], 16000, empty=True)
    bounds = value['resource_constraints']
    request_fields = {'resource_id', 'width', 'height', 'fps', 'frame_count',
        'global_start_frame', 'seed', 'transparent', 'manim_version'}
    require(type(bounds) is dict and set(bounds) == request_fields, 'manim_recipe_bounds_invalid')
    validate_request(dict(schema=REQUEST_SCHEMA, source='# bounds validation only', scene_class='Probe', **bounds))
    # The unchanged source-only module v1 admits at most 64 used assets. Keep
    # room for the manifest, consumer adapter and two existing runtime assets.
    require(bounds['frame_count'] <= 60, 'manim_recipe_film_asset_slots_exceeded', 413)
    require(len(canonical_bytes(value)) <= INPUT_MAX_BYTES, 'manim_recipe_input_too_large', 413)
    return deepcopy(value)


def validate_input(value):
    require(type(value) is dict and set(value) == CORE_FIELDS | {'schema', 'skill_catalog'}
        and value['schema'] == INPUT_SCHEMA, 'manim_recipe_input_invalid')
    core = validate_core_task({key: value[key] for key in CORE_FIELDS})
    catalog = validate_skill_catalog(value['skill_catalog'])
    require(catalog['task_sha256'] == digest(core), 'manim_recipe_catalog_binding_mismatch', 409)
    require(len(canonical_bytes(value)) <= INPUT_MAX_BYTES, 'manim_recipe_input_too_large', 413)
    return deepcopy(value)


def normalize_agent_result(value, context, *, tool_evidence_sha256):
    require(type(value) is dict and set(value) == RAW_RESULT_FIELDS, 'manim_recipe_result_invalid')
    result = deepcopy(value)
    result['skill_tool_evidence_sha256'] = tool_evidence_sha256
    return result


def validate_result(value, input_sha256, input_document, *, read_package_sha256s, tool_evidence_sha256):
    context = validate_input(input_document)
    checksum(input_sha256)
    checksum(tool_evidence_sha256)
    require(type(value) is dict and set(value) == RAW_RESULT_FIELDS | {'skill_tool_evidence_sha256'}
        and value['schema'] == RESULT_SCHEMA and value['input_sha256'] == input_sha256
        and digest(context) == input_sha256 and value['skill_tool_evidence_sha256'] == tool_evidence_sha256,
        'manim_recipe_result_binding_mismatch', 409)
    request = validate_request(value['request'])
    require(all(request[key] == item for key, item in context['resource_constraints'].items()),
        'manim_recipe_frozen_bounds_changed', 409)
    strategy = value['strategy']
    require(type(strategy) is dict and set(strategy) == {'description', 'tradeoffs'}, 'manim_recipe_strategy_invalid')
    text(strategy['description'], 8000)
    text(strategy['tradeoffs'], 8000, empty=True)
    validate_skill_decisions(value['skill_decisions'], context['skill_catalog'],
        {'files': {'scene.py': request['source']}}, read_package_sha256s)
    require(len(canonical_bytes(value)) <= OUTPUT_MAX_BYTES, 'manim_recipe_output_too_large', 413)
    return deepcopy(value)

